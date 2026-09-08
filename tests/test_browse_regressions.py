"""Exercise browse endpoints against a temporary library, without starting media workers."""
import ast
import asyncio
import logging
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Query, Request
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, Movie, MovieAudio, MovieStatus, MovieStatusEnum
from television import TVEpisode, TVEpisodeFile, TVSeries
from scanning import clean_movie_name


def test_probe_access_failure_is_not_classified_as_broken_media():
    from scanning import media_kind_from_probe
    with pytest.raises(RuntimeError, match='Permission denied'):
        media_kind_from_probe('{}', 'Permission denied', 1)


def test_same_title_screenshot_is_not_reused_for_another_movie(tmp_path, monkeypatch):
    from video import screenshot
    monkeypatch.setattr(screenshot, '_get_shared_resources', lambda: {'SCREENSHOT_DIR': tmp_path})
    monkeypatch.setattr(screenshot, '_lookup_movie_name', lambda *args, **kwargs: 'Heat')
    legacy = tmp_path / 'Heat_screenshot300s.jpg'
    legacy.write_bytes(b'old screenshot')
    assert screenshot.find_existing_screenshot_file('/movies/Heat.mkv', 300, movie_id=42) is None
    own = tmp_path / 'Heat_42_screenshot300s.jpg'
    own.write_bytes(b'correct screenshot')
    assert screenshot.find_existing_screenshot_file('/movies/Heat.mkv', 300, movie_id=42) == own
    assert legacy.is_file()


@pytest.fixture
def api():
    # main.py starts media processing at import time. Load these unmodified functions
    # without its startup code so tests cannot touch the real library or VLC.
    names = {'LANGUAGE_ALIASES', 'movie_language_rows', 'count_movie_languages',
             'get_language_counts', 'explore_movies', 'get_largest_movie_ids_subquery',
             'get_subtitles', 'movie_display_name'}
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            nodes.append(node)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    namespace = dict(globals(), re=re, HTTPException=HTTPException, Query=Query, Request=Request,
                     func=func, MovieStatusEnum=MovieStatusEnum, clean_movie_name=clean_movie_name, SessionLocal=sessions, logger=logging.getLogger(__name__),
                     time=__import__('time'), SUBTITLE_EXTENSIONS={'.srt', '.sub', '.vtt', '.ass', '.ssa'},
                     build_movie_cards=lambda db, movies: {m.id: {'id': m.id, 'name': m.name, 'year': m.year} for m in movies})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'main.py', 'exec'), namespace)
    app = FastAPI()
    for route, name in [('/api/explore', 'explore_movies'), ('/api/language-counts', 'get_language_counts'), ('/api/subtitles', 'get_subtitles')]:
        app.get(route)(namespace[name])
    with sessions() as db:
        db.add_all([Movie(id=i, name=name, year=year, size=size, path=f'/movies/{i}.mkv', hidden=hidden, length=120)
                    for i, name, year, size, hidden in [
                        (1, 'Alpha', 1980, 20, False), (2, 'Alpha', 1980, 10, False),
                        (3, 'beta', 1988, 20, False), (4, '9 Lives', 1990, 20, False),
                        (5, 'Éclair', None, 20, False), (6, 'Hidden', 1980, 20, True),
                        (7, 'Unknown', 2000, 20, False), (8, 'Dutch', 1980, 20, False)]])
        db.add_all([MovieAudio(movie_id=i, audio_type=lang) for i, lang in [
            (1, 'ja'), (1, 'jpn'), (2, 'jpn'), (3, 'eng'), (4, 'und'),
            (4, 'unknown'), (6, 'ja'), (7, 'unknown'), (8, 'dut'), (8, 'nld')]])
        db.add(MovieStatus(movie_id=1, movieStatus='watched'))
        db.commit()
    class Client:
        def get(self, path, **kwargs):
            async def request():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    return await client.get(path, **kwargs)
            return asyncio.run(request())
    yield Client(), namespace
    engine.dispose()


def explore(api, **params):
    response = api[0].get('/api/explore', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_hash_and_case_insensitive_letter(api):
    assert {m['name'] for m in explore(api, letter='#')['movies']} == {'9 Lives', 'Éclair'}
    assert [m['name'] for m in explore(api, letter='B')['movies']] == ['beta']


def test_counts_use_same_visible_deduplicated_population(api):
    data = explore(api)
    assert data['pagination']['total'] == 6
    assert sum(data['letter_counts'].values()) == 6
    assert sum(data['decade_counts'].values()) + data['no_year_count'] == 6
    assert data['language_counts']['all'] == 6
    assert api[0].get('/api/language-counts').json()['counts']['all'] == 6


def test_facets_keep_other_filters_but_allow_switching_own_choice(api):
    data = explore(api, language='ja', decade=1980, letter='A')
    assert [m['id'] for m in data['movies']] == [1]
    assert data['letter_counts'] == {'A': 1}
    assert data['year_counts'] == {'1980': 1}
    assert data['language_counts']['ja'] == 1
    assert data['language_counts']['en'] == 0
    for language in ('ja', 'en', 'nl', 'unknown'):
        data = explore(api, language=language, decade=1980)
        assert data['language_counts'][language] == data['pagination']['total']
        assert sum(data['letter_counts'].values()) == data['pagination']['total']
    assert explore(api, filter_type='watched')['pagination']['total'] == 1
    assert explore(api, no_year='true')['pagination']['total'] == 1


def test_languages_are_distinct_and_unknown_includes_missing_tags(api):
    counts = api[0].get('/api/language-counts').json()['counts']
    assert counts['ja'] == counts['nl'] == 1
    assert counts['unknown'] == 3
    assert 'jpn' not in counts and 'und' not in counts and 'dut' not in counts
    for language in ('unknown', 'und'):
        assert {m['id'] for m in explore(api, language=language)['movies']} == {4, 5, 7}


def test_subtitle_matches_are_ordered_and_unrelated_files_remain_available(api, tmp_path):
    movie = tmp_path / 'Stalker.1979.1080p.mkv'
    movie.touch()
    for name in ('Stalker.1979.1080p.en.srt', 'Total.Recall.1990.srt', 'Stalker.2000.srt'):
        (tmp_path / name).touch()
    response = api[0].get('/api/subtitles', params={'video_path': str(movie)})
    assert response.status_code == 200, response.text
    subtitles = response.json()['subtitles']
    assert subtitles[0]['name'] == 'Stalker.1979.1080p.en.srt'
    assert subtitles[0]['matches_movie'] is True
    assert len(subtitles) == 3
    assert all(not sub['matches_movie'] for sub in subtitles[1:])


def test_old_path_titles_are_cleaned_without_changing_normal_names(api):
    display = api[1]['movie_display_name']
    assert display('/movies/Stalker.1979.1080p.mkv') == 'Stalker'
    assert display('Stalker') == 'Stalker'
    assert display('AC/DC') == 'AC/DC'
    assert display(r'C:\Movies\Stalker.1979.1080p.mkv') == 'Stalker'


def test_recently_acquired_is_paginated_by_added_date_without_100_item_cutoff(api):
    from datetime import datetime, timedelta
    with api[1]['SessionLocal']() as db:
        db.query(Movie).delete()
        for i in range(125):
            db.add(Movie(name=f'Film {i:03}', path=f'/movies/new-{i}.mkv', size=100,
                         length=120, hidden=False, year=1942,
                         created=datetime(2026, 1, 1) + timedelta(days=i)))
        db.commit()
    first = explore(api, filter_type='newest', page=1, per_page=100)
    second = explore(api, filter_type='newest', page=2, per_page=100)
    assert first['pagination']['total'] == 125
    assert first['movies'][0]['name'] == 'Film 124'
    assert len(first['movies']) == 100 and len(second['movies']) == 25
    assert second['movies'][-1]['name'] == 'Film 000'
    filtered = explore(api, filter_type='newest', year=1942)
    assert filtered['pagination']['total'] == 125


def test_tv_is_opt_in_including_short_episodes_and_counts(api):
    with api[1]['SessionLocal']() as db:
        series = TVSeries(title='Example')
        movie = Movie(name='TV Example', path='/movies/tv.mkv', length=22, hidden=False, size=100)
        db.add_all([series, movie])
        db.flush()
        episode = TVEpisode(series_id=series.id, season=1, number=1)
        db.add(episode)
        db.flush()
        db.add(TVEpisodeFile(episode_id=episode.id, movie_id=movie.id))
        db.add(MovieAudio(movie_id=movie.id, audio_type='eng'))
        db.commit()
    assert 'TV Example' not in {m['name'] for m in explore(api)['movies']}
    inclusive = explore(api, include_tv=True)
    assert 'TV Example' in {m['name'] for m in inclusive['movies']}
    assert inclusive['pagination']['total'] == explore(api)['pagination']['total'] + 1
    assert api[0].get('/api/language-counts', params={'include_tv': True}).json()['counts']['en'] == api[0].get('/api/language-counts').json()['counts']['en'] + 1


def test_artwork_discovery_accepts_uppercase_and_prefers_named_poster(tmp_path):
    from scanning import find_images_in_folder
    movie = tmp_path / 'Film.mkv'
    movie.touch()
    (tmp_path / 'screenshot.png').write_bytes(b'x' * 200)
    (tmp_path / 'POSTER.JPG').write_bytes(b'x' * 10)
    (tmp_path / 'cover.JFIF').write_bytes(b'x' * 5)
    (tmp_path / 'www.YTS.jpg').write_bytes(b'x' * 1000)
    images = find_images_in_folder(movie)
    assert [Path(p).name for p in images] == ['POSTER.JPG', 'cover.JFIF', 'screenshot.png']


def test_artwork_thumbnail_is_bounded_raster(tmp_path):
    from artwork import thumbnail_response
    from PIL import Image
    from io import BytesIO
    path = tmp_path / 'poster.png'
    Image.new('RGB', (600, 900)).save(path)
    response = thumbnail_response(path, 120)
    with Image.open(BytesIO(response.body)) as image:
        assert image.size == (120, 180)
        assert image.format == 'JPEG'
