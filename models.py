"""
SQLAlchemy database models (table definitions) for Movie Searcher.
"""
from enum import Enum
from sqlalchemy import Column, String, Float, Integer, DateTime, Boolean, Text, ForeignKey, JSON
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class MovieStatusEnum(str, Enum):
    """Enum for movie status values"""
    WATCHED = "watched"
    UNWATCHED = "unwatched"
    WANT_TO_WATCH = "want_to_watch"

class Movie(Base):
    __tablename__ = "movies"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    path = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=True)
    length = Column(Float, nullable=True)
    size = Column(Integer, nullable=True)
    hash = Column(String, nullable=True, index=True)
    language = Column(String, nullable=True, index=True)  # Primary audio language code (e.g., 'en', 'es', 'fr')
    image_path = Column(String, nullable=True)  # Path to movie's image (poster/cover) or fallback screenshot
    hidden = Column(Boolean, default=False, nullable=False, index=True)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class Rating(Base):
    __tablename__ = "ratings"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    rating = Column(Float, nullable=False)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class MovieStatus(Base):
    __tablename__ = "movie_status"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    movieStatus = Column(String, nullable=True)  # NULL = unknown, "watched", "unwatched", "want_to_watch"
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class SearchHistory(Base):
    __tablename__ = "search_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    query = Column(String, nullable=False, index=True)
    results_count = Column(Integer, nullable=True)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class LaunchHistory(Base):
    __tablename__ = "launch_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, index=True)
    subtitle = Column(String, nullable=True)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class IndexedPath(Base):
    __tablename__ = "indexed_paths"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    path = Column(String, nullable=False, unique=True, index=True)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class Config(Base):
    __tablename__ = "config"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    key = Column(String, nullable=False, unique=True, index=True)
    value = Column(Text, nullable=True)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class Screenshot(Base):
    """Screenshots extracted from video files using ffmpeg"""
    __tablename__ = "screenshots"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, index=True)
    shot_path = Column(String, nullable=False)  # Path to the extracted screenshot image
    timestamp_seconds = Column(Float, nullable=True)  # Timestamp in seconds when screenshot was taken
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class MovieAudio(Base):
    """Audio streams/types available for a movie (e.g., language codes like eng, jpn, und)."""
    __tablename__ = "movie_audio"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, index=True)
    audio_type = Column(String, nullable=False, index=True)  # Stores language code or descriptor
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

# --- Playlist Models ---

class Playlist(Base):
    """User and system playlists"""
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)
    is_system = Column(Boolean, default=False, nullable=False)  # True for 'Favorites', 'Want to Watch'
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class PlaylistItem(Base):
    """Movies in playlists"""
    __tablename__ = "playlist_items"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    playlist_id = Column(Integer, ForeignKey('playlists.id', ondelete='CASCADE'), nullable=False, index=True)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, index=True)
    order = Column(Integer, default=0, nullable=False)
    added_at = Column(DateTime, default=func.now(), nullable=False)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

# --- Offline Metadata Models (IMDb subset) ---

class ExternalMovie(Base):
    """Subset of IMDb movie data (title.basics)"""
    __tablename__ = "external_movies"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    imdb_id = Column(String, unique=True, nullable=False, index=True)  # tt1234567
    primary_title = Column(String, nullable=False, index=True)
    original_title = Column(String, nullable=True)
    year = Column(Integer, nullable=True, index=True)
    runtime_minutes = Column(Integer, nullable=True)
    genres = Column(String, nullable=True)  # Comma-separated string
    rating = Column(Float, nullable=True)   # IMDb rating
    votes = Column(Integer, nullable=True)  # Number of votes
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class Person(Base):
    """Subset of IMDb person data (name.basics)"""
    __tablename__ = "people"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    imdb_id = Column(String, unique=True, nullable=False, index=True)  # nm1234567
    primary_name = Column(String, nullable=False, index=True)
    birth_year = Column(Integer, nullable=True)
    death_year = Column(Integer, nullable=True)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class MovieCredit(Base):
    """Joins ExternalMovie and Person (title.principals)"""
    __tablename__ = "movie_credits"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('external_movies.id', ondelete='CASCADE'), nullable=False, index=True)
    person_id = Column(Integer, ForeignKey('people.id', ondelete='CASCADE'), nullable=False, index=True)
    category = Column(String, nullable=False)  # director, actor, actress, writer
    characters = Column(JSON, nullable=True)   # JSON array of character names
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class SchemaVersion(Base):
    """Tracks database schema version to avoid unnecessary migration checks"""
    __tablename__ = "schema_version"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    version = Column(Integer, nullable=False, unique=True)
    description = Column(String, nullable=True)
    applied_at = Column(DateTime, default=func.now(), nullable=False)

# Current schema version - increment when schema changes
CURRENT_SCHEMA_VERSION = 12
