#!/usr/bin/env python3
"""
IMDb Dataset Import Tool for Movie Searcher

Downloads and imports IMDb datasets into the local database for offline metadata.
Only imports movies with significant ratings (numVotes > 1000) to keep the database reasonable size.

Datasets used:
- title.basics.tsv.gz: Basic movie info (titles, years, genres)
- title.principals.tsv.gz: Cast/crew credits (directors, actors)
- name.basics.tsv.gz: Person details (names, birth/death years)

Usage:
    python imdb_import.py [options]

Options:
    --download-only    Only download the datasets, don't import
    --import-only      Only import previously downloaded datasets
    --force            Force re-download even if files exist
    --sample           Import only first 1000 movies (for testing)
"""

import os
import gzip
import csv
import logging
import argparse
import requests
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import sys

# Add project root to path so we can import database models
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from database import SessionLocal
from models import ExternalMovie, Person, MovieCredit, CURRENT_SCHEMA_VERSION

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('imdb_import.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# IMDb dataset URLs
IMDB_BASE_URL = "https://datasets.imdbws.com/"
DATASETS = {
    'title.basics': 'title.basics.tsv.gz',
    'title.principals': 'title.principals.tsv.gz',
    'name.basics': 'name.basics.tsv.gz'
}

# Download directory
DATA_DIR = Path("imdb_data")
DATA_DIR.mkdir(exist_ok=True)

class IMDbImporter:
    def __init__(self, sample_mode: bool = False):
        self.sample_mode = sample_mode
        self.session = SessionLocal()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()

    def download_dataset(self, dataset_name: str, force: bool = False) -> Path:
        """Download a single IMDb dataset"""
        filename = DATASETS[dataset_name]
        filepath = DATA_DIR / filename

        if filepath.exists() and not force:
            logger.info(f"{filename} already exists, skipping download")
            return filepath

        url = IMDB_BASE_URL + filename
        logger.info(f"Downloading {filename} from {url}...")

        try:
            response = requests.get(url, stream=True, timeout=300)  # 5 minute timeout
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))

            with open(filepath, 'wb') as f:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            logger.info(".1f")

            logger.info(f"Downloaded {filename} ({downloaded} bytes)")
            return filepath

        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            raise

    def download_all(self, force: bool = False):
        """Download all required IMDb datasets"""
        logger.info("Starting IMDb dataset downloads...")
        for dataset_name in DATASETS.keys():
            self.download_dataset(dataset_name, force)
        logger.info("All datasets downloaded successfully")

    def parse_tsv_gz(self, filepath: Path) -> csv.DictReader:
        """Parse a gzipped TSV file"""
        return csv.DictReader(
            gzip.open(filepath, 'rt', encoding='utf-8'),
            delimiter='\t'
        )

    def import_people(self, filepath: Path):
        """Import people (cast/crew) from name.basics.tsv.gz"""
        logger.info("Importing people data...")

        reader = self.parse_tsv_gz(filepath)
        count = 0
        batch_size = 1000

        for row in reader:
            try:
                # Only import people who have worked on movies we'll be importing
                # We'll filter this during the movie credits import instead
                person = Person(
                    imdb_id=row['nconst'],
                    primary_name=row['primaryName'],
                    birth_year=int(row['birthYear']) if row['birthYear'] != '\\N' else None,
                    death_year=int(row['deathYear']) if row['deathYear'] != '\\N' else None
                )

                self.session.merge(person)  # Use merge to handle duplicates

                count += 1
                if count % batch_size == 0:
                    self.session.commit()
                    logger.info(f"Imported {count} people...")

                if self.sample_mode and count >= 5000:  # Import more people for sample mode
                    break

            except Exception as e:
                logger.warning(f"Error importing person {row.get('nconst', 'unknown')}: {e}")
                continue

        self.session.commit()
        logger.info(f"Imported {count} people total")

    def import_movies(self, filepath: Path):
        """Import movie data from title.basics.tsv.gz"""
        logger.info("Importing movie data...")

        reader = self.parse_tsv_gz(filepath)
        count = 0
        imported = 0
        batch_size = 1000

        for row in reader:
            try:
                # Only import movies
                if row['titleType'] != 'movie':
                    continue

                # Only import movies with significant ratings (numVotes > 1000)
                # This filters out obscure movies and keeps the DB manageable
                num_votes = int(row['numVotes']) if row['numVotes'] != '\\N' else 0
                if num_votes <= 1000:
                    continue

                # Parse year
                year = int(row['startYear']) if row['startYear'] != '\\N' else None

                # Parse runtime
                runtime = int(row['runtimeMinutes']) if row['runtimeMinutes'] != '\\N' else None

                # Parse genres (comma-separated string)
                genres = row['genres'] if row['genres'] != '\\N' else None

                # Parse rating
                rating = float(row['averageRating']) if row['averageRating'] != '\\N' else None

                movie = ExternalMovie(
                    imdb_id=row['tconst'],
                    primary_title=row['primaryTitle'],
                    original_title=row['originalTitle'] if row['originalTitle'] != '\\N' else None,
                    year=year,
                    runtime_minutes=runtime,
                    genres=genres,
                    rating=rating,
                    votes=num_votes
                )

                self.session.add(movie)
                imported += 1

                count += 1
                if count % batch_size == 0:
                    self.session.commit()
                    logger.info(f"Processed {count} rows, imported {imported} movies...")

                if self.sample_mode and imported >= 1000:
                    break

            except Exception as e:
                logger.warning(f"Error importing movie {row.get('tconst', 'unknown')}: {e}")
                continue

        self.session.commit()
        logger.info(f"Imported {imported} movies from {count} total rows")

    def import_credits(self, filepath: Path):
        """Import movie credits from title.principals.tsv.gz"""
        logger.info("Importing movie credits...")

        # First, get all movie IDs we imported
        movie_ids = {row[0] for row in self.session.query(ExternalMovie.imdb_id).all()}
        logger.info(f"Found {len(movie_ids)} movies to process credits for")

        reader = self.parse_tsv_gz(filepath)
        count = 0
        imported = 0
        batch_size = 1000

        for row in reader:
            try:
                # Only process credits for movies we imported
                if row['tconst'] not in movie_ids:
                    continue

                # Only import key roles
                category = row['category']
                if category not in ['director', 'actor', 'actress', 'writer']:
                    continue

                # Get the movie and person IDs from our database
                movie = self.session.query(ExternalMovie).filter(ExternalMovie.imdb_id == row['tconst']).first()
                person = self.session.query(Person).filter(Person.imdb_id == row['nconst']).first()

                if not movie or not person:
                    continue

                # Parse characters (JSON array)
                characters = None
                if row['characters'] != '\\N':
                    # Remove brackets and quotes, split by comma
                    chars_str = row['characters'].strip('[]')
                    if chars_str:
                        characters = [c.strip('"\'' ) for c in chars_str.split(',')]

                credit = MovieCredit(
                    movie_id=movie.id,
                    person_id=person.id,
                    category=category,
                    characters=characters
                )

                self.session.add(credit)
                imported += 1

                count += 1
                if count % batch_size == 0:
                    self.session.commit()
                    logger.info(f"Processed {count} rows, imported {imported} credits...")

                if self.sample_mode and imported >= 5000:
                    break

            except Exception as e:
                logger.warning(f"Error importing credit for {row.get('tconst', 'unknown')}/{row.get('nconst', 'unknown')}: {e}")
                continue

        self.session.commit()
        logger.info(f"Imported {imported} credits from {count} total rows")

    def auto_link_movies(self):
        """Automatically link local movies to IMDb movies using fuzzy matching"""
        logger.info("Starting auto-linking of local movies to IMDb data...")

        from scanning import clean_movie_name
        from fuzzywuzzy import fuzz
        from fuzzywuzzy.process import extractOne

        # Get local movies without year info
        local_movies = self.session.query(Movie).filter(
            Movie.hidden == False,
            Movie.year.is_(None)  # Only movies missing year info
        ).all()

        logger.info(f"Found {len(local_movies)} local movies to potentially link")

        # Get all IMDb movies for fuzzy matching
        imdb_movies = {}
        for movie in self.session.query(ExternalMovie).all():
            # Create searchable key: "Movie Title (Year)"
            key = movie.primary_title
            if movie.year:
                key += f" ({movie.year})"
            imdb_movies[key.lower()] = movie

        linked_count = 0

        for local_movie in local_movies:
            try:
                # Create search key from local movie name
                search_key = local_movie.name.lower()

                # Try exact match first
                if search_key in imdb_movies:
                    imdb_movie = imdb_movies[search_key]
                    match_score = 100
                else:
                    # Try fuzzy match
                    best_match, match_score = extractOne(
                        search_key,
                        imdb_movies.keys(),
                        scorer=fuzz.token_sort_ratio
                    )

                    if match_score >= 85:  # High confidence threshold
                        imdb_movie = imdb_movies[best_match]
                    else:
                        continue

                # Update local movie with IMDb data
                if not local_movie.year and imdb_movie.year:
                    local_movie.year = imdb_movie.year
                    logger.info(f"Linked '{local_movie.name}' -> '{imdb_movie.primary_title} ({imdb_movie.year})' (score: {match_score})")
                    linked_count += 1

            except Exception as e:
                logger.warning(f"Error linking movie {local_movie.id} ({local_movie.name}): {e}")
                continue

        self.session.commit()
        logger.info(f"Auto-linked {linked_count} movies with IMDb data")

    def run_import(self):
        """Run the full import process"""
        logger.info("Starting IMDb import process...")

        # Verify we have the data files
        for dataset_name, filename in DATASETS.items():
            filepath = DATA_DIR / filename
            if not filepath.exists():
                raise FileNotFoundError(f"Dataset file not found: {filepath}. Run with --download first.")

        # Import in dependency order
        self.import_people(DATA_DIR / DATASETS['name.basics'])
        self.import_movies(DATA_DIR / DATASETS['title.basics'])
        self.import_credits(DATA_DIR / DATASETS['title.principals'])

        # Auto-link local movies
        try:
            self.auto_link_movies()
        except ImportError:
            logger.warning("fuzzywuzzy not installed, skipping auto-linking. Install with: pip install fuzzywuzzy")

        logger.info("IMDb import completed successfully!")

def main():
    parser = argparse.ArgumentParser(description="IMDb Dataset Import Tool")
    parser.add_argument('--download-only', action='store_true', help='Only download datasets')
    parser.add_argument('--import-only', action='store_true', help='Only import datasets (skip download)')
    parser.add_argument('--force', action='store_true', help='Force re-download even if files exist')
    parser.add_argument('--sample', action='store_true', help='Import only sample data for testing')

    args = parser.parse_args()

    if args.download_only and args.import_only:
        parser.error("Cannot use both --download-only and --import-only")

    with IMDbImporter(sample_mode=args.sample) as importer:
        try:
            if not args.import_only:
                importer.download_all(force=args.force)

            if not args.download_only:
                importer.run_import()

        except Exception as e:
            logger.error(f"Import failed: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
