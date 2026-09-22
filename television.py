"""Series catalogue, episode/file identities, and library ownership."""
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, UniqueConstraint, inspect, or_, text

from media_identity import episode_numbers as episode_numbers
from media_identity import file_identity, is_extra, parsed_name, property_value
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


class TVSeriesFile(Base):
    """TV files whose content must not count as owning an aired episode."""
    __tablename__ = 'tv_series_files'
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), primary_key=True)
    series_id = Column(Integer, ForeignKey('tv_series.id', ondelete='CASCADE'), nullable=False)
    kind = Column(String, nullable=False)


def normalize(title):
    return re.sub(r'[^\w]+', '', title).casefold()


def initialize_schema(engine):
    TVSeries.__table__.create(engine, checkfirst=True)
    TVSeason.__table__.create(engine, checkfirst=True)
    TVEpisode.__table__.create(engine, checkfirst=True)
    TVEpisodeFile.__table__.create(engine, checkfirst=True)
    TVSeriesFile.__table__.create(engine, checkfirst=True)
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


def tv_movie_ids(db):
    return db.query(TVEpisodeFile.movie_id).union(db.query(TVSeriesFile.movie_id))


def match_series(candidates, title, year=None, country=None):
    if not isinstance(title, str):
        return None
    matches = []
    for candidate in candidates:
        guess = parsed_name(candidate.title)
        alias = property_value(guess, 'title')
        same_title = normalize(candidate.title) == normalize(title) or (isinstance(alias, str) and normalize(alias) == normalize(title))
        if (same_title and (not country or country == property_value(guess, 'country'))
                and (not year or not candidate.year or candidate.year == year)):
            matches.append(candidate)
    if year:
        matches = [s for s in matches if s.year == year] or matches
    return matches[0] if len(matches) == 1 else None


def register_file(db, movie, series_id=None, season=None, numbers=None, repair=False):
    identity = file_identity(movie.path)
    parsed = identity['episodes']
    if season is None and parsed:
        season, numbers = parsed
    linked = db.query(TVEpisode).join(TVEpisodeFile, TVEpisodeFile.episode_id == TVEpisode.id).filter(TVEpisodeFile.movie_id == movie.id).first()
    existing = db.get(TVSeries, linked.series_id) if linked else None
    # Keep established identities; repair only the old parser's numeric show names.
    if series_id is None and existing and (existing.catalogued or not existing.title.isdecimal() or not repair):
        series_id = existing.id
    title, year = identity['title'], identity['year']
    series = db.get(TVSeries, series_id) if series_id else None
    if not series:
        candidates = list(db.query(TVSeries))
        series = match_series(candidates, title, year, identity['country'])
        if not series:
            # Bonus filenames often omit the show title; use a recognized folder.
            for parent in Path(movie.path).parents:
                folder = parsed_name(parent.name)
                series = match_series(candidates, property_value(folder, 'title'),
                                      property_value(folder, 'year'), property_value(folder, 'country'))
                if series:
                    break
        if not series and (parsed or identity['contextual_tv']) and title and not title.isdecimal() and not is_extra(movie.path):
            series = TVSeries(title=title, year=year)
            db.add(series)
            db.flush()
    if not series:
        return None
    role = 'extra' if is_extra(movie.path) else 'episode' if season is not None and numbers else 'unassigned'
    # Mere proximity to a series is insufficient to reclassify a film.
    if role == 'unassigned' and not linked and not series_id:
        evidence = parsed_name(movie.path)
        if not evidence.get('episode') and not evidence.get('season'):
            return None
        if property_value(parsed_name(Path(movie.path).name), 'year'):
            return None
    if linked and existing.catalogued and role != 'extra' and not parsed and not numbers:
        return 'episode'
    if role != 'episode':
        db.query(TVEpisodeFile).filter_by(movie_id=movie.id).delete(synchronize_session=False)
        row = db.get(TVSeriesFile, movie.id)
        if row is None:
            row = TVSeriesFile(movie_id=movie.id)
            db.add(row)
        row.series_id, row.kind = series.id, role
        movie.name = Path(movie.path).stem
        db.flush()
        return role
    if repair or series_id:
        db.query(TVEpisodeFile).filter_by(movie_id=movie.id).delete(synchronize_session=False)
    db.query(TVSeriesFile).filter_by(movie_id=movie.id).delete(synchronize_session=False)
    movie.name = series.title + f' S{season:02d}' + ''.join(f'E{n:02d}' for n in numbers)
    if series.year:
        movie.year = series.year
    ensure_season(db, series.id, season)
    for number in numbers:
        episode = ensure_episode(db, series.id, season, number)
        if not db.get(TVEpisodeFile, (episode.id, movie.id)):
            db.add(TVEpisodeFile(episode_id=episode.id, movie_id=movie.id))
    db.flush()
    return 'episode'


def series_view(db, series):
    episodes = db.query(TVEpisode).filter_by(series_id=series.id).order_by(TVEpisode.season, TVEpisode.number).all()
    rows = db.query(TVEpisodeFile.episode_id, Movie).join(Movie, Movie.id == TVEpisodeFile.movie_id).join(TVEpisode, TVEpisode.id == TVEpisodeFile.episode_id).filter(TVEpisode.series_id == series.id, Movie.hidden.is_(False)).all()
    files = {}
    for episode_id, movie in rows:
        if Path(movie.path).is_file():
            files.setdefault(episode_id, []).append(movie.id)
    additional = db.query(Movie, TVSeriesFile.kind).join(TVSeriesFile, TVSeriesFile.movie_id == Movie.id).filter(TVSeriesFile.series_id == series.id, Movie.hidden.is_(False)).all()
    aired = [e for e in episodes if e.aired]
    owned = sum(e.id in files for e in aired)
    return {'id': series.id, 'media_type': 'series', 'name': series.title, 'title': series.title,
            'year': series.year, 'tvmaze_id': series.tvmaze_id, 'source_url': series.source_url,
            'catalogued': series.catalogued, 'status': series.status, 'aired_episodes': len(aired),
            'owned_episodes': owned, 'complete': bool(series.catalogued and aired and owned == len(aired)),
            'additional_files': [{'movie_id': m.id, 'title': Path(m.path).stem, 'kind': kind} for m, kind in additional if Path(m.path).is_file()],
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
        linked = db.query(TVEpisode.series_id).join(TVEpisodeFile, TVEpisodeFile.episode_id == TVEpisode.id)
        series = db.query(TVSeries).filter(or_(TVSeries.title.op('GLOB')('*[^0-9]*'), TVSeries.catalogued.is_(True), TVSeries.id.in_(linked), TVSeries.id.in_(db.query(TVSeriesFile.series_id))))
        return [series_view(db, s) for s in series.order_by(TVSeries.title) if normalize(q) in normalize(s.title)]


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
            link = db.get(TVSeriesFile, movie_id)
            if not link:
                return None
            return {'series': series_view(db, db.get(TVSeries, link.series_id)), 'episodes': [], 'next_episode': None}
        view = series_view(db, db.get(TVSeries, episode.series_id))
        linked = [e for e in view['episodes'] if movie_id in e['movie_ids']]
        last = max((e['season'], e['number']) for e in linked)
        following = next((e for e in view['episodes'] if (e['season'], e['number']) > last), None)
        return {'series': view, 'episodes': linked, 'next_episode': following}
