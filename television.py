"""Series catalogue, episode/file identities, and library ownership."""
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, UniqueConstraint, inspect, text

from models import Base, Movie, MovieList, MovieListItem


class TVSeries(Base):
    __tablename__ = 'tv_series'
    id = Column(Integer, primary_key=True)
    tvmaze_id = Column(Integer, unique=True, nullable=True)
    title = Column(String, nullable=False)
    year = Column(Integer)
    status = Column(String)
    source_url = Column(String)
    catalogued = Column(Boolean, default=False, nullable=False)


class TVSeason(Base):
    __tablename__ = 'tv_seasons'
    id = Column(Integer, primary_key=True)
    series_id = Column(Integer, ForeignKey('tv_series.id', ondelete='CASCADE'), nullable=False)
    number = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('series_id', 'number'),)


class TVEpisode(Base):
    __tablename__ = 'tv_episodes'
    id = Column(Integer, primary_key=True)
    series_id = Column(Integer, ForeignKey('tv_series.id', ondelete='CASCADE'), nullable=False)
    season = Column(Integer, nullable=False)
    number = Column(Integer, nullable=False)
    title = Column(String)
    airdate = Column(String)
    runtime = Column(Float)
    aired = Column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint('series_id', 'season', 'number'),)


class TVEpisodeFile(Base):
    __tablename__ = 'tv_episode_files'
    episode_id = Column(Integer, ForeignKey('tv_episodes.id', ondelete='CASCADE'), primary_key=True)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), primary_key=True)


def normalize(title):
    return re.sub(r'[^\w]+', '', title).casefold()


def initialize_schema(engine):
    TVSeries.__table__.create(engine, checkfirst=True)
    TVSeason.__table__.create(engine, checkfirst=True)
    TVEpisode.__table__.create(engine, checkfirst=True)
    TVEpisodeFile.__table__.create(engine, checkfirst=True)
    if 'movie_list_items' in inspect(engine).get_table_names():
        columns = {c['name'] for c in inspect(engine).get_columns('movie_list_items')}
        with engine.begin() as conn:
            for name, definition in {'media_type': "VARCHAR NOT NULL DEFAULT 'movie'", 'tv_series_id': 'INTEGER',
                                     'season_number': 'INTEGER', 'episode_number': 'INTEGER'}.items():
                if name not in columns:
                    conn.execute(text(f'ALTER TABLE movie_list_items ADD COLUMN {name} {definition}'))


def resolve_show(title, year=None, show_id=None):
    try:
        with requests.Session() as session:
            if show_id:
                response = session.get(f'https://api.tvmaze.com/shows/{int(show_id)}', timeout=30)
                response.raise_for_status()
                show = response.json()
            else:
                response = session.get('https://api.tvmaze.com/search/shows', params={'q': title}, timeout=30)
                response.raise_for_status()
                choices = [r['show'] for r in response.json() if normalize(r['show']['name']) == normalize(title)]
                exact = [s for s in choices if (s.get('premiered') or '').startswith(str(year))] if year else []
                choices = exact or choices
                if len(choices) != 1:
                    raise ValueError('Choose the exact series: ' + ', '.join(f"{s['name']} ({s.get('premiered') or 'year unknown'}, TVmaze {s['id']})" for s in choices) if choices else 'No exact series found in TVmaze.')
                show = choices[0]
            response = session.get(f"https://api.tvmaze.com/shows/{show['id']}/episodes", timeout=30)
            response.raise_for_status()
            return show, response.json()
    except requests.RequestException:
        raise ValueError('TV catalogue lookup failed; check connectivity and retry.') from None


def save_catalog(db, show, episodes):
    series = db.query(TVSeries).filter(TVSeries.tvmaze_id == show['id']).first()
    if not series:
        candidates = db.query(TVSeries).filter(TVSeries.tvmaze_id.is_(None)).all()
        series = next((s for s in candidates if normalize(s.title) == normalize(show['name'])), None)
    if not series:
        series = TVSeries(title=show['name'])
        db.add(series)
        db.flush()
    series.tvmaze_id = show['id']
    series.title = show['name']
    series.year = int(show['premiered'][:4]) if show.get('premiered') else None
    series.source_url = show['url']
    series.status = show.get('status')
    series.catalogued = True
    now = datetime.now(timezone.utc)
    for data in episodes:
        season, number = data.get('season'), data.get('number')
        if season is None or number is None:
            continue
        ensure_season(db, series.id, season)
        episode = ensure_episode(db, series.id, season, number)
        episode.title = data.get('name')
        episode.airdate = data.get('airdate')
        episode.runtime = data.get('runtime') or show.get('averageRuntime') or show.get('runtime') or 45
        episode.aired = bool(data.get('airstamp') and datetime.fromisoformat(data['airstamp'].replace('Z', '+00:00')) <= now)
    db.flush()
    for item in db.query(MovieListItem).filter(MovieListItem.media_type == 'series'):
        if item.tv_series_id == series.id or (not item.tv_series_id and normalize(item.title) == normalize(series.title) and (not item.year or item.year == series.year)):
            item.tv_series_id = series.id
    for movie in db.query(Movie).join(TVEpisodeFile, TVEpisodeFile.movie_id == Movie.id).join(TVEpisode, TVEpisode.id == TVEpisodeFile.episode_id).filter(TVEpisode.series_id == series.id):
        register_file(db, movie, series_id=series.id)
    db.commit()
    return series


def ensure_season(db, series_id, number):
    row = db.query(TVSeason).filter_by(series_id=series_id, number=number).first()
    if not row:
        row = TVSeason(series_id=series_id, number=number)
        db.add(row)
        db.flush()
    return row


def ensure_episode(db, series_id, season, number):
    row = db.query(TVEpisode).filter_by(series_id=series_id, season=season, number=number).first()
    if not row:
        row = TVEpisode(series_id=series_id, season=season, number=number)
        db.add(row)
        db.flush()
    return row


def episode_numbers(name):
    name = name.replace('_', '.')
    match = re.search(r'(?i)\bS(\d{1,3})E(\d{1,4})((?:[ ._-]*E\d{1,4})*)', name)
    if match:
        numbers = [int(match[2]), *map(int, re.findall(r'(?i)E(\d+)', match[3]))]
        if '-' in match[3] and len(numbers) == 2 and 0 < numbers[-1] - numbers[0] < 100:
            numbers = list(range(numbers[0], numbers[-1] + 1))
        return int(match[1]), numbers
    match = re.search(r'(?i)\b(\d{1,2})x(\d{2,3})\b', name)
    return (int(match[1]), [int(match[2])]) if match else None


def register_file(db, movie, series_id=None, season=None, numbers=None):
    parsed = episode_numbers(Path(movie.path).stem)
    if parsed:
        season, numbers = parsed
    if season is None or not numbers:
        return
    if series_id is None:
        linked = db.query(TVEpisode).join(TVEpisodeFile, TVEpisodeFile.episode_id == TVEpisode.id).filter(TVEpisodeFile.movie_id == movie.id).first()
        if linked:
            series_id = linked.series_id
    if series_id is None:
        stem = Path(movie.path).stem.replace('_', '.')
        prefix = re.split(r'(?i)\bS\d+E\d+|\b\d+x\d+', stem)[0]
        title = re.sub(r'[._]+', ' ', prefix).strip(' -([')
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', title)
        year = int(year_match[1]) if year_match else None
        if year_match:
            title = title[:year_match.start()].strip(' ([.-')
        if not title:
            return
        series = next((s for s in db.query(TVSeries) if normalize(s.title) == normalize(title) and (not year or not s.year or s.year == year)), None)
        if not series:
            series = TVSeries(title=title, year=year)
            db.add(series)
            db.flush()
        series_id = series.id
    series = db.get(TVSeries, series_id)
    movie.name = series.title + f' S{season:02d}' + ''.join(f'E{n:02d}' for n in numbers)
    if series.year:
        movie.year = series.year
    ensure_season(db, series_id, season)
    for number in numbers:
        episode = ensure_episode(db, series_id, season, number)
        if not db.get(TVEpisodeFile, (episode.id, movie.id)):
            db.add(TVEpisodeFile(episode_id=episode.id, movie_id=movie.id))
    db.flush()


def series_view(db, series):
    episodes = db.query(TVEpisode).filter_by(series_id=series.id).order_by(TVEpisode.season, TVEpisode.number).all()
    rows = db.query(TVEpisodeFile.episode_id, Movie).join(Movie, Movie.id == TVEpisodeFile.movie_id).join(TVEpisode, TVEpisode.id == TVEpisodeFile.episode_id).filter(TVEpisode.series_id == series.id, Movie.hidden.is_(False)).all()
    files = {}
    for episode_id, movie in rows:
        if Path(movie.path).is_file():
            files.setdefault(episode_id, []).append(movie.id)
    aired = [e for e in episodes if e.aired]
    owned = sum(e.id in files for e in aired)
    return {'id': series.id, 'media_type': 'series', 'name': series.title, 'title': series.title,
            'year': series.year, 'tvmaze_id': series.tvmaze_id, 'source_url': series.source_url,
            'catalogued': series.catalogued, 'status': series.status, 'aired_episodes': len(aired),
            'owned_episodes': owned, 'complete': bool(series.catalogued and aired and owned == len(aired)),
            'episodes': [{'id': e.id, 'season': e.season, 'number': e.number, 'title': e.title,
                          'airdate': e.airdate, 'runtime': e.runtime, 'aired': e.aired, 'movie_ids': files.get(e.id, [])} for e in episodes]}


def list_series(db, item):
    series = db.get(TVSeries, item.tv_series_id) if item.tv_series_id else next((s for s in db.query(TVSeries) if normalize(s.title) == normalize(item.title) and (not item.year or not s.year or item.year == s.year)), None)
    view = series_view(db, series) if series else {'media_type': 'series', 'name': item.title, 'year': item.year, 'complete': False, 'owned_episodes': 0, 'aired_episodes': None}
    return {**view, 'ai_comment': item.ai_comment}


def reconcile_series_lists(db):
    for item in db.query(MovieListItem).filter(MovieListItem.media_type == 'series'):
        view = list_series(db, item)
        item.is_in_library = view['complete']
        item.movie_id = None
        if view.get('id'):
            item.tv_series_id = view['id']
    db.flush()
    for row in db.query(MovieList):
        row.in_library_count = db.query(MovieListItem).filter_by(movie_list_id=row.id, is_in_library=True).count()


router = APIRouter(prefix='/api/series')


@router.get('')
def get_series(q: str = ''):
    from database import SessionLocal
    with SessionLocal() as db:
        return [series_view(db, s) for s in db.query(TVSeries).order_by(TVSeries.title) if normalize(q) in normalize(s.title)]


@router.get('/{identifier}')
def get_series_detail(identifier: int):
    from database import SessionLocal
    with SessionLocal() as db:
        series = db.get(TVSeries, identifier)
        if not series:
            raise HTTPException(404, 'Series not found')
        return series_view(db, series)


from pydantic import BaseModel, Field


class CatalogueRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    year: int | None = None
    tvmaze_id: int | None = Field(default=None, gt=0)


@router.post('/catalogue')
def catalogue_series(body: CatalogueRequest):
    from database import SessionLocal
    try:
        show, episodes = resolve_show(body.title, body.year, body.tvmaze_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    with SessionLocal() as db:
        series = save_catalog(db, show, episodes)
        reconcile_series_lists(db)
        db.commit()
        return series_view(db, series)


@router.get('/file/{movie_id}')
def file_series(movie_id: int):
    from database import SessionLocal
    with SessionLocal() as db:
        episode = db.query(TVEpisode).join(TVEpisodeFile, TVEpisodeFile.episode_id == TVEpisode.id).filter(TVEpisodeFile.movie_id == movie_id).first()
        if not episode:
            return None
        view = series_view(db, db.get(TVSeries, episode.series_id))
        linked = [e for e in view['episodes'] if movie_id in e['movie_ids']]
        last = max((e['season'], e['number']) for e in linked)
        following = next((e for e in view['episodes'] if (e['season'], e['number']) > last), None)
        return {'series': view, 'episodes': linked, 'next_episode': following}
