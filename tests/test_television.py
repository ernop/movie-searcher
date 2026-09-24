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


@pytest.mark.parametrize(('name', 'expected'), [
    ('Example Season 7 Episode 11.avi', (7, [11])),
    ('Example - S01 - E01 - A Place.avi', (1, [1])),
    ('1918 - Charlie Chaplin - A Dogs Life.avi', None),
    ('Mindwalk_(1990)_704x480_4x3.mp4', None),
    ('Star Wars Episode VI 1983.mkv', None),
])
def test_standard_parser_handles_legacy_names_without_guessing_films(name, expected):
    assert episode_numbers(name) == expected


def test_repair_numeric_series_and_separate_extras(tmp_path):
    from television import TVSeriesFile, register_file, tv_movie_ids
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    folder = tmp_path / 'Example Show (1993)'
    folder.mkdir()
    paths = [folder / '001 S01E01 Pilot.mkv', folder / 'Deleted Scenes S01E02.mkv']
    for path in paths:
        path.write_bytes(b'video')
    with Session(engine) as db:
        bogus = TVSeries(title='001')
        movie = Movie(path=str(paths[0]), name='001 S01E01')
        extra = Movie(path=str(paths[1]), name='Deleted scenes')
        db.add_all([bogus, movie, extra])
        db.flush()
        old = TVEpisode(series_id=bogus.id, season=1, number=1)
        db.add(old)
        db.flush()
        db.add(TVEpisodeFile(episode_id=old.id, movie_id=movie.id))
        db.flush()
        assert register_file(db, movie, repair=True) == 'episode'
        assert register_file(db, extra, repair=True) == 'extra'
        series = db.query(TVSeries).filter_by(title='Example Show').one()
        assert db.query(TVEpisodeFile).count() == 1
        assert db.get(TVSeriesFile, extra.id).series_id == series.id
        assert {r[0] for r in tv_movie_ids(db)} == {movie.id, extra.id}
        assert len(series_view(db, series)['additional_files']) == 1
        register_file(db, movie, repair=True)
        register_file(db, extra, repair=True)
        assert db.query(TVEpisodeFile).count() == 1
        assert db.query(TVSeriesFile).count() == 1
    engine.dispose()


def test_preserve_regional_series_and_match_new_extras(tmp_path):
    from television import TVSeriesFile, register_file
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        uk = TVSeries(title='The Office UK')
        us = TVSeries(title='The Office (US)', year=2005)
        db.add_all([uk, us])
        db.flush()
        regular = Movie(path=str(tmp_path / 'The.Office.US.S01E01.mkv'), name='Pilot')
        extra = Movie(path=str(tmp_path / 'The Office US (2005)' / 'Deleted Scenes' / 'The.Office.US.S01E01.mkv'), name='Deleted scenes')
        db.add_all([regular, extra])
        db.flush()
        register_file(db, regular, series_id=us.id)
        register_file(db, regular, repair=True)
        register_file(db, extra, repair=True)
        assert db.get(TVSeriesFile, extra.id).series_id == us.id
        linked = db.query(TVEpisode).join(TVEpisodeFile).filter(TVEpisodeFile.movie_id == regular.id).one()
        assert linked.series_id == us.id
    engine.dispose()


def test_backfill_preview_apply_and_repeat_preserve_movie_ids(tmp_path):
    import json

    from scripts.reclassify_tv import run
    database = tmp_path / 'library.db'
    report = tmp_path / 'audit.json'
    backup = tmp_path / 'backup.db'
    engine = create_engine(f'sqlite:///{database}')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Movie(id=42, name='Legacy', path=str(tmp_path / 'Example Season 2 Episode 3.mkv')))
        db.commit()
    run(database, report)
    with Session(engine) as db:
        assert db.query(TVEpisodeFile).count() == 0
    run(database, report, apply=True, backup_path=backup)
    with Session(engine) as db:
        assert db.query(TVEpisodeFile).one().movie_id == 42
        assert db.get(Movie, 42).name == 'Example S02E03'
    assert backup.is_file()
    run(database, report)
    assert json.loads(report.read_text())['changes'] == []
    engine.dispose()


def test_season_folders_classify_tv_without_inventing_episode_numbers(tmp_path):
    from television import TVSeriesFile, register_file
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        movie = Movie(path=str(tmp_path / 'Example Season 2' / 'Example - 207.mkv'), name='Example 207')
        db.add(movie)
        db.flush()
        assert register_file(db, movie) == 'unassigned'
        assert db.get(TVSeriesFile, movie.id)
        assert db.query(TVEpisodeFile).count() == 0
    engine.dispose()


@pytest.mark.parametrize('filename', ['Example Episode 01 - Pilot.avi', 'Example (Author) Vol2-Episode3.avi'])
def test_episode_only_names_are_tv_without_invented_seasons(tmp_path, filename):
    from television import TVSeriesFile, register_file
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        movie = Movie(path=str(tmp_path / 'Example' / filename), name=filename)
        db.add(movie)
        db.flush()
        assert register_file(db, movie) == 'unassigned'
        assert db.get(TVSeriesFile, movie.id).kind == 'unassigned'
        assert db.query(TVEpisodeFile).count() == 0
        assert register_file(db, movie, repair=True) == 'unassigned'
    engine.dispose()


@pytest.mark.parametrize('filename', ['Star.Wars.Episode.6.Return.of.the.Jedi.1983.mp4', 'Star Wars Episode VI 1983.mkv', 'Film.1918.mkv'])
def test_film_episode_titles_and_years_do_not_create_tv(filename):
    from media_identity import file_identity
    identity = file_identity('/movies/' + filename)
    assert not identity['contextual_tv']
    assert identity['episodes'] is None
