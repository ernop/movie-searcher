# IMDb Dataset Import Tool

This tool downloads and imports IMDb datasets into your Movie Searcher database for offline metadata access.

## What it does

1. **Downloads** official IMDb datasets (free for non-commercial use)
2. **Filters** to only movies with >1000 votes (keeps DB size manageable)
3. **Imports** movie details, cast/crew, and credits
4. **Auto-links** your local movies to IMDb data using fuzzy matching
5. **Provides** director pages, actor info, and enhanced movie details

## Datasets Imported

- `title.basics.tsv.gz`: Movie titles, years, genres, runtime
- `title.principals.tsv.gz`: Cast/crew credits (directors, actors, writers)
- `name.basics.tsv.gz`: Person details (names, birth/death years)

## Installation

```bash
pip install fuzzywuzzy python-levenshtein requests
# OR
pip install -r requirements.txt
```

## Usage

### First time setup (download + import)

```bash
python imdb_import.py
```

### Download only

```bash
python imdb_import.py --download-only
```

### Import only (after downloading)

```bash
python imdb_import.py --import-only
```

### Force re-download

```bash
python imdb_import.py --force
```

### Sample mode (for testing)

```bash
python imdb_import.py --sample
```

## What gets imported

- **Movies**: Only movies with >1000 IMDb votes (~20k-50k movies)
- **People**: Cast/crew from those movies
- **Credits**: Director, actor, writer roles

## Database impact

- **ExternalMovie table**: ~20k-50k rows
- **Person table**: ~50k-100k rows
- **MovieCredit table**: ~100k-200k rows

## Auto-linking

After import, local movies are automatically linked to IMDb data using fuzzy title matching. This fills in:

- Missing release years
- Director information
- Cast information
- Genres, ratings, runtime

## API Endpoints Added

- `GET /api/person/{id}`: Person details and filmography
- `GET /api/search-people`: Search people by name
- `GET /api/imdb-stats`: Statistics about imported data

## Movie details enhanced

The `GET /api/movie/{id}` endpoint now includes `imdb_data` with:

- Full cast/crew
- Director info
- IMDb rating
- Genres
- Runtime

## Legal Notes

- IMDb datasets are provided free for non-commercial use
- Data is stored locally only
- No API calls to IMDb after import
- Respect IMDb's terms of service
