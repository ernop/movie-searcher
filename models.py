"""
SQLAlchemy database models (table definitions) for Movie Searcher.
"""
from sqlalchemy import Column, String, Float, Integer, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

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
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class Rating(Base):
    __tablename__ = "ratings"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    rating = Column(Float, nullable=False)
    created = Column(DateTime, default=func.now(), nullable=False)
    updated = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

class WatchHistory(Base):
    __tablename__ = "watch_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, index=True)
    watch_status = Column(Boolean, nullable=True)  # NULL = unknown, True = watched, False = not watched
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

class Image(Base):
    """Images that came with the movie (posters, covers, thumbnails, etc.) - existing files found in movie folder"""
    __tablename__ = "images"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, index=True)
    image_path = Column(String, nullable=False)  # Path to the image file
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

class SchemaVersion(Base):
    """Tracks database schema version to avoid unnecessary migration checks"""
    __tablename__ = "schema_version"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    version = Column(Integer, nullable=False, unique=True)
    description = Column(String, nullable=True)
    applied_at = Column(DateTime, default=func.now(), nullable=False)

# Current schema version - increment when schema changes
CURRENT_SCHEMA_VERSION = 6

