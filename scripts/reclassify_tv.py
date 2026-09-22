"""Audit on a database copy; optionally apply the same shared classifier after backup."""
import argparse
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Movie
from television import (
    TVEpisode,
    TVEpisodeFile,
    TVSeries,
    TVSeriesFile,
    initialize_schema,
    reconcile_series_lists,
    register_file,
)


def snapshot(db, movie):
    episodes = db.query(TVSeries.title, TVSeries.year, TVEpisode.season, TVEpisode.number).join(
        TVEpisode, TVEpisode.series_id == TVSeries.id).join(
        TVEpisodeFile, TVEpisodeFile.episode_id == TVEpisode.id).filter(TVEpisodeFile.movie_id == movie.id).all()
    additional = db.query(TVSeries.title, TVSeries.year, TVSeriesFile.kind).join(
        TVSeriesFile, TVSeriesFile.series_id == TVSeries.id).filter(TVSeriesFile.movie_id == movie.id).all()
    return {'episodes': sorted([list(row) for row in episodes], key=str),
            'additional': [list(row) for row in additional]}


def backup(source, destination):
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    Path(destination).chmod(0o600)


def run(database, report, apply=False, backup_path=None):
    changes = []
    with tempfile.TemporaryDirectory(prefix='tv-audit-') as directory:
        shadow = Path(directory) / 'library.db'
        backup(database, shadow)
        engine = create_engine(f'sqlite:///{shadow}')
        initialize_schema(engine)
        with Session(engine) as db:
            movies = db.query(Movie).order_by(Movie.id).all()
            for offset, movie in enumerate(movies):
                before = snapshot(db, movie)
                register_file(db, movie, repair=True)
                after = snapshot(db, movie)
                if before != after:
                    changes.append({'id': movie.id, 'path': movie.path, 'before': before, 'after': after})
                if offset % 1000 == 0:
                    print(f'Audited {offset}/{len(movies)} files', flush=True)
        engine.dispose()
    report.write_text(json.dumps({'applied': False, 'changes': changes}, indent=2))
    report.chmod(0o600)
    if apply:
        if not backup_path or backup_path.exists():
            raise ValueError('Provide a new backup path before applying changes.')
        backup(database, backup_path)
        engine = create_engine(f'sqlite:///{database}', connect_args={'timeout': 30})
        initialize_schema(engine)
        with Session(engine) as db:
            for change in changes:
                movie = db.get(Movie, change['id'])
                if not movie or movie.path != change['path'] or snapshot(db, movie) != change['before']:
                    raise ValueError(f"File {change['id']} changed during the audit; rerun it.")
                register_file(db, movie, repair=True)
                if snapshot(db, movie) != change['after']:
                    raise ValueError(f"Classification for {movie.id} changed during the audit; no changes committed.")
            reconcile_series_lists(db)
            db.commit()
        engine.dispose()
        report.write_text(json.dumps({'applied': True, 'changes': changes}, indent=2))
    print(json.dumps({'applied': apply, 'changed_files': len(changes),
                      'roles': dict(Counter('additional' if c['after']['additional'] else 'episode' for c in changes))}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[1] / 'movie_searcher.db')
    parser.add_argument('--report', type=Path, required=True, help='Private JSON audit output (not in Git).')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    run(args.database, args.report, args.apply, args.backup)
