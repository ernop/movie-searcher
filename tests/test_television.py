"""Public series parsing, migration and ownership regressions."""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from models import Base, Movie
from television import TVEpisode, TVEpisodeFile, TVSeries, episode_numbers, initialize_schema, series_view


@pytest.mark.parametrize(('name', 'expected'), [
    ('Example.S01E02.mkv', (1, [2])), ('Example.S01E02E03.mkv', (1, [2, 3])),
    ('Example.S01E02-E04.mkv', (1, [2, 3, 4])), ('Example_2x03.mp4', (2, [3])),
    ('Film.1942.mkv', None),
])
def test_episode_identity(name, expected):
    assert episode_numbers(name) == expected


def test_additive_schema_migration_preserves_existing_list_items():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE movie_list_items (id INTEGER PRIMARY KEY, title TEXT)'))
        db.execute(text("INSERT INTO movie_list_items VALUES (1, 'Film')"))
    initialize_schema(engine)
    initialize_schema(engine)
    assert {'media_type', 'tv_series_id', 'season_number', 'episode_number'} <= {c['name'] for c in inspect(engine).get_columns('movie_list_items')}
    with engine.connect() as db:
        assert tuple(db.execute(text('SELECT title, media_type FROM movie_list_items')).one()) == ('Film', 'movie')
    engine.dispose()


def test_series_ownership_requires_present_visible_files_for_aired_episodes(tmp_path):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    path = tmp_path / 'Example.S01E01.mkv'
    path.write_bytes(b'video')
    with Session(engine) as db:
        series = TVSeries(title='Example', catalogued=True)
        movie = Movie(path=str(path), name='Example S01E01', hidden=False)
        db.add_all([series, movie])
        db.flush()
        aired = TVEpisode(series_id=series.id, season=1, number=1, aired=True)
        future = TVEpisode(series_id=series.id, season=1, number=2, aired=False)
        db.add_all([aired, future])
        db.flush()
        db.add(TVEpisodeFile(episode_id=aired.id, movie_id=movie.id))
        db.flush()
        assert series_view(db, series)['complete']
        movie.hidden = True
        db.flush()
        assert not series_view(db, series)['complete']
        movie.hidden = False
        db.flush()
        path.unlink()
        assert not series_view(db, series)['complete']
    engine.dispose()
