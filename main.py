from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import subprocess
import re
import shutil
from pathlib import Path
from datetime import datetime
import hashlib
import logging
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import atexit

# Setup logging
# Move log file outside project directory to avoid triggering reloads
import sys
LOG_FILE = Path(__file__).parent.parent / "movie_searcher.log" if Path(__file__).parent.parent.exists() else Path(__file__).parent / "movie_searcher.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# Database setup
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, Text, Index, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session, aliased
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
    images = Column(Text, nullable=True)  # JSON array as string
    screenshots = Column(Text, nullable=True)  # JSON array as string
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

class MovieFrame(Base):
    __tablename__ = "movie_frames"
    
    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    movie_id = Column(Integer, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, index=True)
    path = Column(String, nullable=False)  # Path to the extracted frame image
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
CURRENT_SCHEMA_VERSION = 2

# Indexes are defined on columns directly (name and path already have indexes)
# Additional indexes can be added via migration if needed

# FastAPI app will be created after lifespan function is defined
# (temporary placeholder - will be replaced)
app = None

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()
DB_FILE = SCRIPT_DIR / "movie_searcher.db"

# Database engine and session
# Enable foreign key support for SQLite
engine = create_engine(
    f"sqlite:///{DB_FILE}", 
    echo=False,
    connect_args={"check_same_thread": False}  # Required for SQLite with FastAPI
)
# Enable foreign keys for SQLite
from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_schema_version():
    """Get current database schema version"""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    if "schema_version" not in existing_tables:
        return None
    
    with engine.connect() as conn:
        result = conn.execute(text("SELECT MAX(version) FROM schema_version"))
        row = result.fetchone()
        return row[0] if row and row[0] is not None else None

def set_schema_version(version, description=None):
    """Record that a schema version has been applied"""
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO schema_version (version, description, applied_at)
            VALUES (:version, :description, CURRENT_TIMESTAMP)
        """), {"version": version, "description": description})
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error setting schema version: {e}")
    finally:
        db.close()

def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    
    # Set initial schema version if database is new
    version = get_schema_version()
    if version is None:
        set_schema_version(CURRENT_SCHEMA_VERSION, "Initial schema version")
        logger.info(f"Database initialized with schema version {CURRENT_SCHEMA_VERSION}")
    else:
        logger.info(f"Database initialized (current schema version: {version})")

def migrate_db_schema():
    """
    Migrate database schema to match current models.
    
    Uses schema version tracking to avoid unnecessary checks on every startup.
    """
    from sqlalchemy import inspect, text
    
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    if "movies" not in existing_tables:
        # No existing database, schema will be created by init_db
        return
    
    current_version = get_schema_version()
    
    # If schema_version table doesn't exist or version is None, we need to check for old schema
    if current_version is None:
        # Check if this is old schema (path as PK) or new schema missing version tracking
        existing_columns = {col['name']: col for col in inspector.get_columns("movies")}
        
        if 'id' in existing_columns:
            # New schema but missing version tracking - just add year if needed and set version
            if "year" not in existing_columns:
                logger.info("Adding missing 'year' column to movies table...")
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE movies ADD COLUMN year INTEGER"))
                logger.info("Migration complete: added 'year' column")
            set_schema_version(CURRENT_SCHEMA_VERSION, "Added version tracking to existing database")
            return
        else:
            # Old schema - needs full migration (will set version after migration)
            pass  # Continue to full migration below
    
    # Ensure all tables have required created/updated columns (regardless of version)
    # This fixes cases where tables were created without these columns
    tables_requiring_timestamps = ["config", "indexed_paths", "search_history"]
    for table_name in tables_requiring_timestamps:
        if table_name in existing_tables:
            table_columns = {col['name']: col for col in inspector.get_columns(table_name)}
            needs_fix = False
            with engine.begin() as conn:
                if "created" not in table_columns:
                    logger.info(f"Adding missing 'created' column to {table_name} table...")
                    # SQLite limitation: cannot add column with CURRENT_TIMESTAMP default to existing table
                    # Add as nullable without default, update existing rows, SQLAlchemy model default handles new rows
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN created DATETIME"))
                    # Set current timestamp for all existing rows
                    conn.execute(text(f"UPDATE {table_name} SET created = CURRENT_TIMESTAMP"))
                    needs_fix = True
                if "updated" not in table_columns:
                    logger.info(f"Adding missing 'updated' column to {table_name} table...")
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN updated DATETIME"))
                    # Set current timestamp for all existing rows
                    conn.execute(text(f"UPDATE {table_name} SET updated = CURRENT_TIMESTAMP"))
                    needs_fix = True
                if needs_fix:
                    logger.info(f"Fixed {table_name} table: added missing timestamp columns")
    
    # If already at current version, no migration needed
    if current_version == CURRENT_SCHEMA_VERSION:
        return
    
    # Check if this is the old schema (no id column in movies)
    existing_columns = {col['name']: col for col in inspector.get_columns("movies")}
    
    if 'id' not in existing_columns:
        # Old schema - needs full migration (will set version after migration completes)
        logger.info("Migrating from old schema (path PK) to new schema (id PK)...")
        # Continue to full migration below
    else:
        # New schema but version is outdated - handle incremental upgrades
        logger.info(f"Upgrading schema from version {current_version} to {CURRENT_SCHEMA_VERSION}...")
        
        if current_version < 2:
            logger.info("Migrating to schema version 2: ensure config table has surrogate key and timestamps.")
            config_columns = {}
            if "config" in existing_tables:
                config_columns = {col['name']: col for col in inspector.get_columns("config")}
            needs_config_migration = "config" in existing_tables and "id" not in config_columns
            
            if needs_config_migration:
                with engine.begin() as conn:
                    conn.execute(text("DROP TABLE IF EXISTS config_new"))
                    conn.execute(text("""
                        CREATE TABLE config_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                            "key" VARCHAR NOT NULL UNIQUE,
                            value TEXT,
                            created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                            updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                        )
                    """))
                    logger.info("Rebuilding config table to include autoincrement id column...")
                    conn.execute(text("""
                        INSERT INTO config_new ("key", value, created, updated)
                        SELECT 
                            "key",
                            value,
                            CASE 
                                WHEN created IS NULL OR created = '' THEN CURRENT_TIMESTAMP
                                ELSE datetime(created)
                            END,
                            CASE 
                                WHEN updated IS NULL OR updated = '' THEN CURRENT_TIMESTAMP
                                ELSE datetime(updated)
                            END
                        FROM config
                    """))
                    conn.execute(text("DROP TABLE config"))
                    conn.execute(text("ALTER TABLE config_new RENAME TO config"))
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_config_key ON config (key)"))
            else:
                # Table either already conforms or was never created; ensure it exists and has an index.
                Base.metadata.tables["config"].create(bind=engine, checkfirst=True)
                with engine.begin() as conn:
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_config_key ON config (key)"))
            
            set_schema_version(2, "Added autoincrement id column to config table")
            current_version = 2
        
        # If we get here without incrementing current_version, the migration wasn't implemented
        if current_version < CURRENT_SCHEMA_VERSION:
            logger.error(f"Schema version {CURRENT_SCHEMA_VERSION} migration not implemented! "
                        f"Database is at version {current_version} but code expects {CURRENT_SCHEMA_VERSION}.")
            raise RuntimeError(f"Migration from version {current_version} to {CURRENT_SCHEMA_VERSION} not implemented")
        return
    
    # Need full migration from old schema to new schema
    logger.info("Starting database schema migration...")

    config_columns = {}
    if "config" in existing_tables:
        config_columns = {col['name']: col for col in inspector.get_columns("config")}
    
    frames_columns = {}
    needs_movie_frames_migration = False
    if "movie_frames" in existing_tables:
        frames_columns = {col['name']: col for col in inspector.get_columns("movie_frames")}
        if 'movie_id' in frames_columns:
            movie_id_col = frames_columns.get('movie_id', {})
            col_type = str(movie_id_col.get('type', '')).upper()
            if 'VARCHAR' in col_type or 'TEXT' in col_type:
                needs_movie_frames_migration = True
        else:
            # Old schema might have had different column name
            needs_movie_frames_migration = True
    
    with engine.begin() as conn:
        # Step 0: Clean up any partial migration tables from previous failed attempts
        logger.info("Cleaning up any partial migration tables...")
        # Drop tables first (this automatically drops their indexes in SQLite)
        # But we also try to drop indexes explicitly in case they exist independently
        tables_to_drop = [
            "movies_new", "ratings_new", "watch_history_new", 
            "search_history_new", "launch_history_new", 
            "indexed_paths_new", "config_new", "movie_frames_new"
        ]
        indexes_to_drop = [
            "ix_movies_path", "ix_movies_name", "ix_movies_hash",
            "ix_ratings_movie_id", "ix_watch_history_movie_id",
            "ix_search_history_query", "ix_launch_history_movie_id",
            "ix_indexed_paths_path", "ix_config_key", "ix_movie_frames_movie_id"
        ]
        
        # Drop tables first (this automatically drops their indexes in SQLite)
        for table_name in tables_to_drop:
            try:
                conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            except Exception as e:
                logger.debug(f"Error dropping table {table_name}: {e}")
        
        # Try to drop any orphaned indexes (in case they exist independently)
        # Note: In SQLite, indexes are usually auto-dropped with tables, but we check anyway
        for index_name in indexes_to_drop:
            try:
                # Try dropping with table qualification
                conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
            except Exception:
                pass  # Index may not exist
        
        # Step 1: Add year column if it doesn't exist
        if "year" not in existing_columns:
            logger.info("Adding 'year' column to movies table...")
            conn.execute(text("ALTER TABLE movies ADD COLUMN year INTEGER"))
        
        # Step 2: Create new tables with correct schema
        logger.info("Creating new tables with updated schema...")
        
        # Create new movies table
        conn.execute(text("""
            CREATE TABLE movies_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                path VARCHAR NOT NULL UNIQUE,
                name VARCHAR NOT NULL,
                year INTEGER,
                length FLOAT,
                size INTEGER,
                hash VARCHAR,
                images TEXT,
                screenshots TEXT,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX ix_movies_path ON movies_new (path)"))
        conn.execute(text("CREATE INDEX ix_movies_name ON movies_new (name)"))
        conn.execute(text("CREATE INDEX ix_movies_hash ON movies_new (hash)"))
        
        # Migrate movies data
        logger.info("Migrating movies data...")
        # Handle created field - convert from string ISO format to datetime
        conn.execute(text("""
            INSERT INTO movies_new (path, name, year, length, size, hash, images, screenshots, created, updated)
            SELECT 
                path,
                name,
                year,
                length,
                size,
                hash,
                images,
                screenshots,
                CASE 
                    WHEN created IS NULL OR created = '' THEN CURRENT_TIMESTAMP
                    ELSE datetime(created)
                END,
                CURRENT_TIMESTAMP
            FROM movies
        """))
        
        # Create new ratings table
        conn.execute(text("""
            CREATE TABLE ratings_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                movie_id INTEGER NOT NULL UNIQUE,
                rating FLOAT NOT NULL,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(movie_id) REFERENCES movies_new (id) ON DELETE CASCADE
            )
        """))
        conn.execute(text("CREATE INDEX ix_ratings_movie_id ON ratings_new (movie_id)"))
        
        # Migrate ratings data (need to map path to id)
        logger.info("Migrating ratings data...")
        conn.execute(text("""
            INSERT INTO ratings_new (movie_id, rating, created, updated)
            SELECT 
                m.id,
                r.rating,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM ratings r
            JOIN movies_new m ON m.path = r.movie_id
        """))
        
        # Create new watch_history table
        conn.execute(text("""
            CREATE TABLE watch_history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                movie_id INTEGER NOT NULL,
                watch_status BOOLEAN,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(movie_id) REFERENCES movies_new (id) ON DELETE CASCADE
            )
        """))
        conn.execute(text("CREATE INDEX ix_watch_history_movie_id ON watch_history_new (movie_id)"))
        
        # Migrate watch_history data
        logger.info("Migrating watch_history data...")
        # Convert old string status to boolean
        conn.execute(text("""
            INSERT INTO watch_history_new (movie_id, watch_status, created, updated)
            SELECT 
                m.id,
                CASE 
                    WHEN wh.watch_status = 'watched' THEN 1
                    WHEN wh.watch_status = 'not watched' OR wh.watch_status = 'unwatched' THEN 0
                    ELSE NULL
                END,
                COALESCE(wh.timestamp, CURRENT_TIMESTAMP),
                COALESCE(wh.timestamp, CURRENT_TIMESTAMP)
            FROM watch_history wh
            JOIN movies_new m ON m.path = wh.movie_id
        """))
        
        # Create new search_history table
        conn.execute(text("""
            CREATE TABLE search_history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                query VARCHAR NOT NULL,
                results_count INTEGER,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX ix_search_history_query ON search_history_new (query)"))
        
        # Migrate search_history data
        logger.info("Migrating search_history data...")
        conn.execute(text("""
            INSERT INTO search_history_new (query, results_count, created, updated)
            SELECT 
                query,
                results_count,
                COALESCE(timestamp, CURRENT_TIMESTAMP),
                COALESCE(timestamp, CURRENT_TIMESTAMP)
            FROM search_history
        """))
        
        # Create new launch_history table
        conn.execute(text("""
            CREATE TABLE launch_history_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                movie_id INTEGER NOT NULL,
                subtitle VARCHAR,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(movie_id) REFERENCES movies_new (id) ON DELETE CASCADE
            )
        """))
        conn.execute(text("CREATE INDEX ix_launch_history_movie_id ON launch_history_new (movie_id)"))
        
        # Migrate launch_history data
        logger.info("Migrating launch_history data...")
        conn.execute(text("""
            INSERT INTO launch_history_new (movie_id, subtitle, created, updated)
            SELECT 
                m.id,
                lh.subtitle,
                COALESCE(lh.timestamp, CURRENT_TIMESTAMP),
                COALESCE(lh.timestamp, CURRENT_TIMESTAMP)
            FROM launch_history lh
            JOIN movies_new m ON m.path = lh.path
        """))
        
        # Create new indexed_paths table
        conn.execute(text("""
            CREATE TABLE indexed_paths_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                path VARCHAR NOT NULL UNIQUE,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX ix_indexed_paths_path ON indexed_paths_new (path)"))
        
        # Migrate indexed_paths data
        logger.info("Migrating indexed_paths data...")
        conn.execute(text("""
            INSERT INTO indexed_paths_new (path, created, updated)
            SELECT 
                path,
                COALESCE(indexed_at, CURRENT_TIMESTAMP),
                COALESCE(indexed_at, CURRENT_TIMESTAMP)
            FROM indexed_paths
        """))
        
        # Create new config table
        if "config" in existing_tables and 'id' not in config_columns:
            conn.execute(text("""
                CREATE TABLE config_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    key VARCHAR NOT NULL UNIQUE,
                    value TEXT,
                    created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
            """))
            conn.execute(text("CREATE INDEX ix_config_key ON config_new (key)"))
            
            logger.info("Migrating config data...")
            conn.execute(text("""
                INSERT INTO config_new (key, value, created, updated)
                SELECT 
                    key,
                    value,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                FROM config
            """))
        
        # Create new movie_frames table
        migrated_movie_frames = False
        if needs_movie_frames_migration:
            migrated_movie_frames = True
            conn.execute(text("""
                CREATE TABLE movie_frames_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    movie_id INTEGER NOT NULL,
                    path VARCHAR NOT NULL,
                    created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    FOREIGN KEY(movie_id) REFERENCES movies_new (id) ON DELETE CASCADE
                )
            """))
            conn.execute(text("CREATE INDEX ix_movie_frames_movie_id ON movie_frames_new (movie_id)"))
            
            logger.info("Migrating movie_frames data...")
            conn.execute(text("""
                INSERT INTO movie_frames_new (movie_id, path, created, updated)
                SELECT 
                    m.id,
                    mf.path,
                    COALESCE(mf.created_at, CURRENT_TIMESTAMP),
                    COALESCE(mf.created_at, CURRENT_TIMESTAMP)
                FROM movie_frames mf
                JOIN movies_new m ON m.path = mf.movie_id
            """))
        
        # Step 3: Drop old tables
        logger.info("Dropping old tables...")
        conn.execute(text("DROP TABLE IF EXISTS ratings"))
        conn.execute(text("DROP TABLE IF EXISTS watch_history"))
        conn.execute(text("DROP TABLE IF EXISTS search_history"))
        conn.execute(text("DROP TABLE IF EXISTS launch_history"))
        conn.execute(text("DROP TABLE IF EXISTS indexed_paths"))
        if "config" in existing_tables and 'id' not in config_columns:
            conn.execute(text("DROP TABLE IF EXISTS config"))
        if migrated_movie_frames:
            conn.execute(text("DROP TABLE IF EXISTS movie_frames"))
        conn.execute(text("DROP TABLE IF EXISTS movies"))
        
        # Step 4: Rename new tables
        logger.info("Renaming new tables...")
        conn.execute(text("ALTER TABLE movies_new RENAME TO movies"))
        conn.execute(text("ALTER TABLE ratings_new RENAME TO ratings"))
        conn.execute(text("ALTER TABLE watch_history_new RENAME TO watch_history"))
        conn.execute(text("ALTER TABLE search_history_new RENAME TO search_history"))
        conn.execute(text("ALTER TABLE launch_history_new RENAME TO launch_history"))
        conn.execute(text("ALTER TABLE indexed_paths_new RENAME TO indexed_paths"))
        if "config" in existing_tables and 'id' not in config_columns:
            conn.execute(text("ALTER TABLE config_new RENAME TO config"))
        if migrated_movie_frames:
            conn.execute(text("ALTER TABLE movie_frames_new RENAME TO movie_frames"))
    
    # Record migration completion
    set_schema_version(CURRENT_SCHEMA_VERSION, "Migrated from old schema (path PK) to new schema (id PK)")
    logger.info("Database schema migration completed successfully!")
    
    # Handle version upgrades (future schema changes)
    # Add version-specific migrations here when CURRENT_SCHEMA_VERSION increases
    # Example:
    # if current_version < 2:
    #     # Migration code for version 2
    #     pass
    # if current_version < 3:
    #     # Migration code for version 3
    #     pass

def remove_sample_files():
    """Remove all movies with 'sample' in their name from the database"""
    db = SessionLocal()
    try:
        # Find all movies with 'sample' in name (case-insensitive)
        # SQLite LIKE is case-insensitive for ASCII, but we'll use func.lower for compatibility
        sample_movies = db.query(Movie).filter(
            func.lower(Movie.name).like('%sample%')
        ).all()
        
        if not sample_movies:
            logger.info("No sample files found in database")
            return 0
        
        count = len(sample_movies)
        logger.info(f"Found {count} sample file(s) to remove")
        
        # Remove related records for each sample movie
        for movie in sample_movies:
            # Delete MovieFrame records
            db.query(MovieFrame).filter(MovieFrame.movie_id == movie.id).delete()
            
            # Delete Rating records
            db.query(Rating).filter(Rating.movie_id == movie.id).delete()
            
            # Delete WatchHistory records
            db.query(WatchHistory).filter(WatchHistory.movie_id == movie.id).delete()
            
            # Delete LaunchHistory records
            db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).delete()
            
            # Delete the movie itself
            db.delete(movie)
            
            logger.info(f"Removed sample file: {movie.name}")
        
        db.commit()
        logger.info(f"Successfully removed {count} sample file(s) from database")
        return count
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing sample files: {e}")
        return 0
    finally:
        db.close()

def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp'}
SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.vtt', '.ass', '.ssa'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

def is_sample_file(file_path):
    """Check if a file should be excluded (contains 'sample' in name, case-insensitive)"""
    if isinstance(file_path, Path):
        name = file_path.stem.lower()
    else:
        name = Path(file_path).stem.lower()
    return 'sample' in name
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"
FRAMES_DIR = SCRIPT_DIR / "frames"

def load_config():
    """Load configuration from database"""
    db = SessionLocal()
    try:
        config = {}
        try:
            config_rows = db.query(Config).all()
            for row in config_rows:
                # Try to parse as JSON, fallback to string
                try:
                    config[row.key] = json.loads(row.value)
                except:
                    config[row.key] = row.value
        except Exception as e:
            # Database tables not initialized yet
            logger.debug(f"Database not initialized yet: {e}")
            pass
        
        return config
    finally:
        db.close()

def save_config(config):
    """Save configuration to database"""
    db = SessionLocal()
    try:
        for key, value in config.items():
            value_str = json.dumps(value) if not isinstance(value, str) else value
            db.merge(Config(key=key, value=value_str))
        db.commit()
    finally:
        db.close()

def get_movies_folder():
    """Get the movies folder path, checking config, env, then default"""
    config = load_config()
    logger.info(f"get_movies_folder called. Config: {config}")
    
    # Check config file first
    if config.get("movies_folder"):
        path = config["movies_folder"]
        logger.info(f"Found config path: '{path}'")
        path_obj = Path(path)
        if path_obj.exists() and path_obj.is_dir():
            logger.info(f"Config path exists and is directory: {path}")
            return path
        else:
            logger.warning(f"Config path does not exist or is not a directory: {path}")
    
    # Check environment variable
    env_path = os.environ.get("MOVIE_ROOT_PATH", "")
    if env_path:
        logger.info(f"Found env path: '{env_path}'")
        path_obj = Path(env_path)
        if path_obj.exists() and path_obj.is_dir():
            logger.info(f"Env path exists and is directory: {env_path}")
            return env_path
        else:
            logger.warning(f"Env path does not exist or is not a directory: {env_path}")
    
    # Default: look for "movies" folder in same directory as script
    movies_folder = SCRIPT_DIR / "movies"
    logger.info(f"Checking default folder: {movies_folder}")
    if movies_folder.exists() and movies_folder.is_dir():
        logger.info(f"Default folder exists: {movies_folder}")
        return str(movies_folder)
    else:
        logger.info(f"Default folder does not exist: {movies_folder}")
    
    logger.info("No movies folder found")
    return None

# Initialize database and run migrations before any database operations
# This must happen at module level before Config model is used
init_db()
migrate_db_schema()

# Get initial movies folder path
ROOT_MOVIE_PATH = get_movies_folder()

# If no movies folder found in config, set default to D:\movies
if not ROOT_MOVIE_PATH:
    default_path = Path("D:/movies")
    if default_path.exists() and default_path.is_dir():
        logger.info(f"Using default movies folder: {default_path}")
        # Save to config
        config = load_config()
        config["movies_folder"] = str(default_path)
        save_config(config)
        ROOT_MOVIE_PATH = str(default_path)

# Scan progress tracking (in-memory)
scan_progress = {
    "is_scanning": False,
    "current": 0,
    "total": 0,
    "current_file": "",
    "status": "idle",
    "logs": [],  # List of log entries: {"timestamp": str, "level": str, "message": str}
    "frame_queue_size": 0,
    "frames_processed": 0,
    "frames_total": 0
}

# Frame extraction queue and executor
frame_extraction_queue = Queue()
frame_executor = None
frame_processing_active = False

# Shutdown and process tracking
shutdown_flag = threading.Event()
active_subprocesses = []  # List of active subprocess.Popen objects
active_subprocesses_lock = threading.Lock()

def register_subprocess(proc: subprocess.Popen):
    """Register a subprocess so it can be killed on shutdown"""
    with active_subprocesses_lock:
        active_subprocesses.append(proc)

def unregister_subprocess(proc: subprocess.Popen):
    """Unregister a subprocess when it completes"""
    with active_subprocesses_lock:
        if proc in active_subprocesses:
            active_subprocesses.remove(proc)

def kill_all_ffmpeg_processes():
    """Kill all ffmpeg processes on the system"""
    try:
        import platform
        if platform.system() == "Windows":
            # Windows: use taskkill
            subprocess.run(["taskkill", "/F", "/IM", "ffmpeg.exe"], 
                         capture_output=True, timeout=5)
        else:
            # Unix: use pkill
            subprocess.run(["pkill", "-9", "ffmpeg"], 
                         capture_output=True, timeout=5)
        logger.info("Killed all ffmpeg processes")
    except Exception as e:
        logger.warning(f"Error killing ffmpeg processes: {e}")

def kill_all_active_subprocesses():
    """Kill all registered subprocesses"""
    with active_subprocesses_lock:
        for proc in active_subprocesses[:]:  # Copy list to avoid modification during iteration
            try:
                if proc.poll() is None:  # Process still running
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                unregister_subprocess(proc)
            except Exception as e:
                logger.warning(f"Error killing subprocess: {e}")
        active_subprocesses.clear()
    kill_all_ffmpeg_processes()

# Define lifespan function after all dependencies are available
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    """Lifespan context manager for startup and shutdown"""
    # Startup
    # Note: init_db creates missing tables but doesn't modify existing ones.
    # migrate_db_schema handles one-time migration from old schema.
    # For production, consider making migrations manual (like Django).
    init_db()
    migrate_db_schema()
    removed_count = remove_sample_files()
    if removed_count > 0:
        print(f"Removed {removed_count} sample file(s) from database")
    
    yield
    
    # Shutdown
    logger.info("Shutdown event triggered, cleaning up...")
    shutdown_flag.set()
    kill_all_active_subprocesses()

# Create FastAPI app with lifespan
app = FastAPI(title="Movie Searcher", lifespan=lifespan)

def run_interruptible_subprocess(cmd, timeout=30, capture_output=True):
    """Run a subprocess that can be interrupted by shutdown flag"""
    if shutdown_flag.is_set():
        return None
    
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        register_subprocess(proc)
        
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(
                cmd, proc.returncode, stdout, stderr
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
    except KeyboardInterrupt:
        if proc:
            proc.kill()
            proc.wait()
        raise
    finally:
        if proc:
            unregister_subprocess(proc)

def add_scan_log(level: str, message: str):
    """Add a log entry to scan progress"""
    global scan_progress
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "level": level,  # "info", "success", "warning", "error"
        "message": message
    }
    scan_progress["logs"].append(log_entry)
    # Keep only last 1000 log entries to prevent memory issues
    if len(scan_progress["logs"]) > 1000:
        scan_progress["logs"] = scan_progress["logs"][-1000:]

class MovieInfo(BaseModel):
    path: str
    name: str
    length: Optional[float] = None
    created: Optional[str] = None
    size: Optional[int] = None

class SearchRequest(BaseModel):
    query: str

class LaunchRequest(BaseModel):
    path: str
    subtitle_path: Optional[str] = None
    close_existing_vlc: bool = True

class WatchedRequest(BaseModel):
    path: str
    watched: bool
    rating: Optional[float] = None

class ConfigRequest(BaseModel):
    movies_folder: Optional[str] = None
    settings: Optional[dict] = None

# Database state functions
def get_movie_id_by_path(db: Session, path: str) -> Optional[int]:
    """Get movie ID from path. Returns None if movie doesn't exist."""
    movie = db.query(Movie).filter(Movie.path == path).first()
    return movie.id if movie else None

def get_movies_dict(db: Session):
    """Get all movies as a dictionary (for backward compatibility)"""
    movies = {}
    for movie in db.query(Movie).all():
        movies[movie.path] = {
            "name": movie.name,
            "length": movie.length,
            "created": movie.created,
            "size": movie.size,
            "hash": movie.hash,
            "images": json.loads(movie.images) if movie.images else [],
            "screenshots": json.loads(movie.screenshots) if movie.screenshots else []
        }
    return movies

def get_indexed_paths_set(db: Session):
    """Get all indexed paths as a set"""
    paths = set()
    for indexed_path in db.query(IndexedPath).all():
        paths.add(indexed_path.path)
    return paths

def load_state():
    """Load state from database (returns dict for backward compatibility)"""
    db = SessionLocal()
    try:
        return {
            "movies": get_movies_dict(db),
            "indexed_paths": get_indexed_paths_set(db)
        }
    finally:
        db.close()

def save_state(state):
    """Save state to database"""
    db = SessionLocal()
    try:
        # Update movies
        for path, info in state.get("movies", {}).items():
            # Filter out YTS images before storing
            images = filter_yts_images(info.get("images", []))
            movie = Movie(
                path=path,
                name=info.get("name", ""),
                length=info.get("length"),
                created=info.get("created"),
                size=info.get("size"),
                hash=info.get("hash"),
                images=json.dumps(images),
                screenshots=json.dumps(info.get("screenshots", []))
            )
            db.merge(movie)
        
        # Update indexed paths
        indexed_paths = state.get("indexed_paths", set())
        for path in indexed_paths:
            db.merge(IndexedPath(path=path))
        
        db.commit()
    finally:
        db.close()

def load_watched():
    """Load watched movies from database (returns dict for backward compatibility)"""
    db = SessionLocal()
    try:
        watched = []
        watched_dates = {}
        ratings = {}
        
        # Get all movies with watched status in watch_history
        watch_entries = db.query(WatchHistory).filter(
            WatchHistory.watch_status == True
        ).order_by(WatchHistory.updated.desc()).all()
        
        # Get most recent watch entry per movie
        watched_paths_set = set()
        for entry in watch_entries:
            movie = db.query(Movie).filter(Movie.id == entry.movie_id).first()
            if movie and movie.path not in watched_paths_set:
                watched.append(movie.path)
                watched_dates[movie.path] = entry.updated.isoformat()
                watched_paths_set.add(movie.path)
        
        # Get ratings
        for rating in db.query(Rating).all():
            movie = db.query(Movie).filter(Movie.id == rating.movie_id).first()
            if movie:
                ratings[movie.path] = rating.rating
        
        return {
            "watched": watched,
            "watched_dates": watched_dates,
            "ratings": ratings
        }
    finally:
        db.close()

def save_watched(watched_data):
    """Save watched movies to database (for backward compatibility - not used in new structure)"""
    # This function is kept for backward compatibility but new code should use
    # the normalized Rating and WatchHistory tables directly
    db = SessionLocal()
    try:
        watched_paths = set(watched_data.get("watched", []))
        watched_dates = watched_data.get("watched_dates", {})
        ratings = watched_data.get("ratings", {})
        
        # Convert paths to movie IDs
        path_to_id = {}
        for path in watched_paths:
            movie_id = get_movie_id_by_path(db, path)
            if movie_id:
                path_to_id[path] = movie_id
        
        # Get current watched movies (movies with watched status)
        current_watched_ids = set()
        for entry in db.query(WatchHistory).filter(WatchHistory.watch_status == True).all():
            current_watched_ids.add(entry.movie_id)
        
        # Remove unwatched movies (delete watch history entries)
        watched_ids = set(path_to_id.values())
        for movie_id in current_watched_ids - watched_ids:
            db.query(WatchHistory).filter(
                WatchHistory.movie_id == movie_id,
                WatchHistory.watch_status == True
            ).delete()
            db.query(Rating).filter(Rating.movie_id == movie_id).delete()
        
        # Add/update watched movies
        for path, movie_id in path_to_id.items():
            # Create watch history entry
            watch_entry = WatchHistory(
                movie_id=movie_id,
                watch_status=True
            )
            db.add(watch_entry)
            
            # Update rating if provided
            if path in ratings and ratings[path] is not None:
                rating_entry = Rating(
                    movie_id=movie_id,
                    rating=ratings[path]
                )
                db.merge(rating_entry)
        
        db.commit()
    finally:
        db.close()

def load_history():
    """Load history from database (returns dict for backward compatibility)"""
    db = SessionLocal()
    try:
        searches = []
        for search in db.query(SearchHistory).order_by(SearchHistory.created.desc()).limit(100).all():
            searches.append({
                "query": search.query,
                "timestamp": search.created.isoformat(),
                "results_count": search.results_count
            })
        
        launches = []
        for launch in db.query(LaunchHistory).order_by(LaunchHistory.created.desc()).all():
            movie = db.query(Movie).filter(Movie.id == launch.movie_id).first()
            if movie:
                launches.append({
                    "path": movie.path,
                    "subtitle": launch.subtitle,
                    "timestamp": launch.created.isoformat()
                })
        
        return {
            "searches": searches,
            "launches": launches
        }
    finally:
        db.close()

def save_history(history):
    """Save history to database"""
    db = SessionLocal()
    try:
        # Save searches (keep last 100)
        searches = history.get("searches", [])
        for search in searches[-100:]:
            search_entry = SearchHistory(
                query=search.get("query", ""),
                timestamp=datetime.fromisoformat(search.get("timestamp", datetime.now().isoformat())),
                results_count=search.get("results_count")
            )
            db.add(search_entry)
        
        # Save launches
        launches = history.get("launches", [])
        for launch in launches:
            launch_path = launch.get("path", "")
            # Get movie ID from path
            movie = db.query(Movie).filter(Movie.path == launch_path).first()
            if movie:
                launch_entry = LaunchHistory(
                    movie_id=movie.id,
                    subtitle=launch.get("subtitle"),
                    timestamp=datetime.fromisoformat(launch.get("timestamp", datetime.now().isoformat()))
                )
                db.add(launch_entry)
        
        db.commit()
    finally:
        db.close()

def has_been_launched(movie_path):
    """Check if a movie has ever been launched"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.path == movie_path).first()
        if not movie:
            return False
        count = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count()
        return count > 0
    finally:
        db.close()

def get_video_length(file_path):
    """Extract video length using mutagen if available, otherwise return None"""
    if not HAS_MUTAGEN:
        return None
    
    try:
        audio = MutagenFile(file_path)
        if audio is not None and hasattr(audio, 'info'):
            length = getattr(audio.info, 'length', None)
            return length
    except:
        pass
    return None

def get_file_hash(file_path):
    """Generate hash for file to detect changes"""
    stat = os.stat(file_path)
    return hashlib.md5(f"{file_path}:{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()

def find_images_in_folder(video_path):
    """Find image files in the same folder as the video"""
    video_path_obj = Path(video_path)
    video_dir = video_path_obj.parent
    base_name = video_path_obj.stem
    
    images = []
    for ext in IMAGE_EXTENSIONS:
        # Check for exact match
        img_path = video_dir / f"{base_name}{ext}"
        if img_path.exists() and "www.YTS.AM" not in img_path.name:
            images.append(str(img_path))
        
        # Check for common patterns (poster, cover, etc.)
        for pattern in [f"{base_name}_poster{ext}", f"{base_name}_cover{ext}", f"{base_name}_thumb{ext}",
                        f"poster{ext}", f"cover{ext}", f"folder{ext}", f"thumb{ext}"]:
            img_path = video_dir / pattern
            if img_path.exists() and str(img_path) not in images and "www.YTS.AM" not in img_path.name:
                images.append(str(img_path))
    
    # Also check for any images in the folder (limit to first 10)
    for img_file in video_dir.iterdir():
        if img_file.suffix.lower() in IMAGE_EXTENSIONS and str(img_file) not in images and "www.YTS.AM" not in img_file.name:
            images.append(str(img_file))
            if len(images) >= 10:
                break
    
    return images[:10]  # Limit to 10 images

def validate_ffmpeg_path(ffmpeg_path):
    """Validate that an ffmpeg path exists and is executable"""
    if not ffmpeg_path:
        return False, "Path is empty"
    
    path_obj = Path(ffmpeg_path)
    
    # Check if file exists
    if not path_obj.exists():
        return False, f"Path does not exist: {ffmpeg_path}"
    
    # Check if it's a file (not a directory)
    if not path_obj.is_file():
        return False, f"Path is not a file: {ffmpeg_path}"
    
    # Try to execute ffmpeg -version to verify it's actually ffmpeg
    try:
        result = subprocess.run([str(path_obj), "-version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            return True, "Valid"
        else:
            return False, f"ffmpeg -version returned non-zero exit code: {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg -version timed out"
    except Exception as e:
        return False, f"Error executing ffmpeg: {str(e)}"

def find_ffmpeg():
    """Find ffmpeg executable - requires configured path, no fallbacks"""
    config = load_config()
    configured_path = config.get("ffmpeg_path")
    
    if not configured_path:
        logger.error("ffmpeg_path not configured. Set ffmpeg_path in configuration to use frame extraction.")
        return None
    
    # Validate the configured path
    is_valid, error_msg = validate_ffmpeg_path(configured_path)
    if is_valid:
        logger.info(f"Using configured ffmpeg path: {configured_path}")
        return configured_path
    else:
        logger.error(f"Configured ffmpeg path is invalid: {configured_path} - {error_msg}")
        logger.error("Please fix the ffmpeg_path configuration. Frame extraction will not work until this is corrected.")
        return None

def generate_frame_filename(video_path, timestamp_seconds):
    """Generate a sensible frame filename based on movie name and timestamp"""
    video_path_obj = Path(video_path)
    movie_name = video_path_obj.stem  # Get filename without extension
    
    # Sanitize filename: remove invalid characters for Windows/Linux
    import re
    # Replace invalid filename characters with underscore
    sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', movie_name)
    # Remove leading/trailing dots and spaces
    sanitized_name = sanitized_name.strip('. ')
    # Limit length to avoid filesystem issues
    if len(sanitized_name) > 100:
        sanitized_name = sanitized_name[:100]
    
    # Format: movie_name_frame150s.jpg
    frame_filename = f"{sanitized_name}_frame{int(timestamp_seconds)}s.jpg"
    return FRAMES_DIR / frame_filename

def extract_movie_frame_sync(video_path, timestamp_seconds=150):
    """Extract a single frame from video synchronously (blocking)"""
    video_path_obj = Path(video_path)
    
    # Create frames directory if it doesn't exist
    FRAMES_DIR.mkdir(exist_ok=True)
    
    # Generate frame filename based on movie name and timestamp
    frame_path = generate_frame_filename(video_path, timestamp_seconds)
    
    # Check if frame already exists
    if frame_path.exists():
        return str(frame_path)
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        logger.warning(f"ffmpeg not found, skipping frame extraction for {video_path}")
        return None
    
    # Try to get video length to validate timestamp
    length = get_video_length(video_path)
    if length and timestamp_seconds > length:
        # If requested timestamp is beyond video length, use 30 seconds or 10% into the video, whichever is smaller
        timestamp_seconds = min(30, max(10, length * 0.1))
        logger.info(f"Timestamp exceeds video length {length}s, using {timestamp_seconds}s instead")
    
    # Extract frame
    try:
        cmd = [
            ffmpeg_exe,
            "-i", str(video_path),
            "-ss", str(timestamp_seconds),
            "-vframes", "1",
            "-q:v", "2",  # High quality
            "-y",  # Overwrite
            str(frame_path)
        ]
        
        result = run_interruptible_subprocess(cmd, timeout=30, capture_output=True)
        if result and result.returncode == 0 and frame_path.exists():
            logger.info(f"Extracted frame from {video_path} at {timestamp_seconds}s")
            return str(frame_path)
        elif result:
            error_msg = result.stderr.decode() if result.stderr else 'Unknown error'
            logger.warning(f"Failed to extract frame from {video_path}: {error_msg}")
            return None
        else:
            return None
    except subprocess.TimeoutExpired:
        logger.warning(f"Frame extraction timed out for {video_path}")
        return None
    except Exception as e:
        logger.error(f"Error extracting frame from {video_path}: {e}")
        return None

def extract_movie_frame(video_path, timestamp_seconds=150, async_mode=True):
    """Extract a single frame from video - can be synchronous or queued for async processing"""
    video_path_obj = Path(video_path)
    
    # Create frames directory if it doesn't exist
    FRAMES_DIR.mkdir(exist_ok=True)
    
    # Generate frame filename based on movie name and timestamp
    frame_path = generate_frame_filename(video_path, timestamp_seconds)
    
    # Check if frame already exists
    if frame_path.exists():
        add_scan_log("info", f"Frame already exists: {frame_path.name}")
        return str(frame_path)
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        add_scan_log("warning", f"ffmpeg not found, skipping frame extraction")
        logger.warning(f"ffmpeg not found, skipping frame extraction for {video_path}")
        return None
    
    # If async mode, queue it for background processing
    if async_mode:
        global frame_extraction_queue, scan_progress
        frame_extraction_queue.put({
            "video_path": video_path,
            "timestamp_seconds": timestamp_seconds,
            "ffmpeg_exe": ffmpeg_exe
        })
        scan_progress["frame_queue_size"] = frame_extraction_queue.qsize()
        scan_progress["frames_total"] = scan_progress.get("frames_total", 0) + 1
        add_scan_log("info", f"Queued frame extraction (queue: {frame_extraction_queue.qsize()})")
        return None  # Return None to indicate it's queued, will be processed later
    else:
        # Synchronous mode (for backwards compatibility)
        return extract_movie_frame_sync(video_path, timestamp_seconds)

def process_frame_extraction_worker(frame_info):
    """Worker function to extract a frame - runs in thread pool"""
    try:
        video_path = frame_info["video_path"]
        timestamp_seconds = frame_info["timestamp_seconds"]
        ffmpeg_exe = frame_info["ffmpeg_exe"]
        
        # Try to get video length to validate timestamp
        length = get_video_length(video_path)
        if length and timestamp_seconds > length:
            timestamp_seconds = min(30, max(10, length * 0.1))
        
        # Regenerate frame path with potentially adjusted timestamp
        frame_path = generate_frame_filename(video_path, timestamp_seconds)
        
        add_scan_log("info", f"Extracting frame: {Path(video_path).name} at {timestamp_seconds:.1f}s...")
        
        cmd = [
            ffmpeg_exe,
            "-i", str(video_path),
            "-ss", str(timestamp_seconds),
            "-vframes", "1",
            "-q:v", "2",
            "-y",
            str(frame_path)
        ]
        
        if shutdown_flag.is_set():
            return False
        
        result = run_interruptible_subprocess(cmd, timeout=30, capture_output=True)
        if result and result.returncode == 0 and Path(frame_path).exists():
            # Save to database
            db = SessionLocal()
            try:
                # Get movie ID from path
                movie = db.query(Movie).filter(Movie.path == video_path).first()
                if not movie:
                    logger.warning(f"Movie not found for frame extraction: {video_path}")
                    return
                
                # Check if entry already exists
                existing = db.query(MovieFrame).filter(MovieFrame.movie_id == movie.id).first()
                if not existing:
                    movie_frame = MovieFrame(
                        movie_id=movie.id,
                        path=frame_path
                    )
                    db.add(movie_frame)
                    db.commit()
                
                global scan_progress
                scan_progress["frames_processed"] = scan_progress.get("frames_processed", 0) + 1
                scan_progress["frame_queue_size"] = frame_extraction_queue.qsize()
                add_scan_log("success", f"Frame extracted: {Path(video_path).name}")
                logger.info(f"Extracted frame from {video_path}")
            finally:
                db.close()
            return True
        else:
            error_msg = result.stderr.decode() if result.stderr else 'Unknown error'
            add_scan_log("error", f"Frame extraction failed: {Path(video_path).name} - {error_msg[:80]}")
            logger.warning(f"Failed to extract frame from {video_path}: {error_msg}")
            return False
    except subprocess.TimeoutExpired:
        add_scan_log("error", f"Frame extraction timed out: {Path(video_path).name}")
        logger.warning(f"Frame extraction timed out for {video_path}")
        return False
    except Exception as e:
        add_scan_log("error", f"Frame extraction error: {Path(video_path).name} - {str(e)[:80]}")
        logger.error(f"Error extracting frame from {video_path}: {e}")
        return False

def process_frame_queue(max_workers=3):
    """Process queued frame extractions in background thread pool"""
    global frame_executor, frame_processing_active, frame_extraction_queue
    
    if frame_processing_active:
        return
    
    frame_processing_active = True
    add_scan_log("info", "Starting background frame extraction...")
    
    def worker():
        global frame_executor
        frame_executor = ThreadPoolExecutor(max_workers=max_workers)
        
        # Continue processing while queue has items or scan is still running
        processed_count = 0
        while not shutdown_flag.is_set():
            try:
                # Get frame info from queue (with timeout to periodically check scan status)
                try:
                    frame_info = frame_extraction_queue.get(timeout=2)
                except:
                    # Queue empty, check if scan is done and queue is truly empty
                    if shutdown_flag.is_set():
                        break
                    if not scan_progress.get("is_scanning", False) and frame_extraction_queue.empty():
                        break
                    continue
                
                # Submit to thread pool (non-blocking)
                future = frame_executor.submit(process_frame_extraction_worker, frame_info)
                processed_count += 1
                frame_extraction_queue.task_done()
                
                # Don't wait for result here - let it run in parallel
                # Just track that we submitted it
                
            except Exception as e:
                logger.error(f"Error in frame extraction worker: {e}")
        
        # Shutdown executor with timeout (interruptible)
        if frame_executor:
            frame_executor.shutdown(wait=False)  # Don't wait, allow interruption
            # Give a short time for tasks to finish, then kill subprocesses
            import time
            time.sleep(0.5)
            kill_all_active_subprocesses()
        
        global frame_processing_active
        frame_processing_active = False
        remaining = frame_extraction_queue.qsize()
        if remaining == 0:
            add_scan_log("success", f"All frame extractions completed ({processed_count} processed)")
        else:
            add_scan_log("warning", f"Frame extraction stopped with {remaining} items remaining")
    
    # Start worker thread
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

def extract_screenshots(video_path, num_screenshots=5):
    """Extract screenshots from video using ffmpeg"""
    video_path_obj = Path(video_path)
    
    # Create screenshots directory if it doesn't exist
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    # Generate screenshot filename based on video hash
    video_hash = hashlib.md5(str(video_path).encode()).hexdigest()[:8]
    screenshot_base = SCREENSHOT_DIR / f"{video_hash}"
    
    screenshots = []
    
    # Check if screenshots already exist
    existing_screenshots = []
    for i in range(num_screenshots):
        screenshot_path = screenshot_base.parent / f"{screenshot_base.name}_{i+1}.jpg"
        if screenshot_path.exists():
            existing_screenshots.append(str(screenshot_path))
    
    if len(existing_screenshots) == num_screenshots:
        return existing_screenshots
    
    # Try to get video length
    length = get_video_length(video_path)
    if not length or length < 1:
        return existing_screenshots if existing_screenshots else []
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        logger.warning(f"ffmpeg not found, skipping screenshot extraction for {video_path}")
        return existing_screenshots if existing_screenshots else []
    
    # Extract screenshots at evenly spaced intervals
    try:
        for i in range(num_screenshots):
            screenshot_path = screenshot_base.parent / f"{screenshot_base.name}_{i+1}.jpg"
            if screenshot_path.exists():
                screenshots.append(str(screenshot_path))
                continue
            
            # Calculate timestamp (distribute evenly across video)
            timestamp = (length / (num_screenshots + 1)) * (i + 1)
            
            # Extract frame
            cmd = [
                ffmpeg_exe,
                "-i", str(video_path),
                "-ss", str(timestamp),
                "-vframes", "1",
                "-q:v", "2",  # High quality
                "-y",  # Overwrite
                str(screenshot_path)
            ]
            
            if shutdown_flag.is_set():
                break
            
            result = run_interruptible_subprocess(cmd, timeout=30, capture_output=True)
            if result and result.returncode == 0 and screenshot_path.exists():
                screenshots.append(str(screenshot_path))
            elif result:
                logger.warning(f"Failed to extract screenshot {i+1} from {video_path}")
    except subprocess.TimeoutExpired:
        logger.warning(f"Screenshot extraction timed out for {video_path}")
    except Exception as e:
        logger.error(f"Error extracting screenshots from {video_path}: {e}")
    
    return screenshots

def load_cleaning_patterns():
    """Load approved cleaning patterns from database"""
    db = SessionLocal()
    try:
        config_row = db.query(Config).filter(Config.key == 'cleaning_patterns').first()
        if config_row:
            try:
                data = json.loads(config_row.value)
                return {
                    'exact_strings': set(data.get('exact_strings', [])),
                    'bracket_patterns': data.get('bracket_patterns', []),
                    'parentheses_patterns': data.get('parentheses_patterns', []),
                    'year_patterns': data.get('year_patterns', True),  # Default to True
                }
            except Exception as e:
                logger.error(f"Error parsing cleaning patterns from database: {e}")
    except Exception as e:
        logger.error(f"Error loading cleaning patterns: {e}")
    finally:
        db.close()
    
    # Return defaults if not found
    return {
        'exact_strings': set(),
        'bracket_patterns': [],
        'parentheses_patterns': [],
        'year_patterns': True,
    }

def save_cleaning_patterns(patterns):
    """Save approved cleaning patterns to database"""
    db = SessionLocal()
    try:
        data = {
            'exact_strings': list(patterns['exact_strings']),
            'bracket_patterns': patterns['bracket_patterns'],
            'parentheses_patterns': patterns['parentheses_patterns'],
            'year_patterns': patterns['year_patterns'],
        }
        value_str = json.dumps(data)
        config_entry = Config(key='cleaning_patterns', value=value_str)
        db.merge(config_entry)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving cleaning patterns: {e}")
        return False
    finally:
        db.close()

def extract_year_from_name(name):
    """Extract year from movie name (1900-2035)"""
    # Look for 4-digit years in the range 1900-2035
    year_pattern = r'\b(19\d{2}|20[0-2]\d|203[0-5])\b'
    matches = re.findall(year_pattern, name)
    if matches:
        # Return the first valid year found
        year = int(matches[0])
        if 1900 <= year <= 2035:
            return year
    return None

def clean_movie_name(name, patterns=None):
    """Clean movie name using approved patterns and extract year"""
    if patterns is None:
        patterns = load_cleaning_patterns()
    
    original_name = name
    year = None
    
    # Extract year first if enabled
    if patterns.get('year_patterns', True):
        year = extract_year_from_name(name)
        # Remove year from name
        if year:
            name = re.sub(rf'\b{year}\b', '', name)
    
    # Remove exact strings
    for exact_str in patterns.get('exact_strings', set()):
        name = name.replace(exact_str, ' ')
    
    # Remove bracket patterns [anything]
    for pattern in patterns.get('bracket_patterns', []):
        if pattern == '[anything]':
            name = re.sub(r'\[.*?\]', '', name)
        else:
            name = name.replace(pattern, ' ')
    
    # Remove parentheses patterns (anything)
    for pattern in patterns.get('parentheses_patterns', []):
        if pattern == '(anything)':
            # Remove parentheses content, but be smart about it
            # Don't remove if it's just a year or looks like part of title
            name = re.sub(r'\([^)]*\)', '', name)
        else:
            name = name.replace(pattern, ' ')
    
    # Clean up multiple spaces and trim
    name = re.sub(r'\s+', ' ', name).strip()
    
    # If name becomes empty, use original
    if not name:
        name = original_name
    
    return name, year

def analyze_movie_names():
    """Analyze all movie names to find suspicious patterns"""
    db = SessionLocal()
    try:
        movies = db.query(Movie).all()
        
        # Collect patterns
        bracket_contents = Counter()  # [RarBG], [AnimeXP], etc.
        parentheses_contents = Counter()  # (1956 - Stanley Kubrick), etc.
        exact_strings = Counter()
        years_found = Counter()
        
        for movie in movies:
            name = movie.name
            
            # Extract bracket contents [anything]
            bracket_matches = re.findall(r'\[([^\]]+)\]', name)
            for match in bracket_matches:
                bracket_contents[f'[{match}]'] += 1
            
            # Extract parentheses contents
            paren_matches = re.findall(r'\(([^)]+)\)', name)
            for match in paren_matches:
                # Check if it looks like a year or year-director pattern
                if re.match(r'^\d{4}', match) or re.match(r'^\d{4}\s*[-–]\s*', match):
                    parentheses_contents[f'({match})'] += 1
                elif len(match) > 3:  # Only count substantial parentheses content
                    parentheses_contents[f'({match})'] += 1
            
            # Extract years
            year = extract_year_from_name(name)
            if year:
                years_found[str(year)] += 1
            
            # Look for common clutter strings (resolution, codec, etc.)
            clutter_patterns = [
                r'\b\d{3,4}p\b',  # 1080p, 720p, etc.
                r'\b\d{3,4}x\d{3,4}\b',  # 1920x1080, etc.
                r'\b(BluRay|BRRip|DVDRip|WEBRip|HDTV|HDRip|BDRip)\b',
                r'\b(x264|x265|HEVC|AVC|H\.264|H\.265)\b',
                r'\b(AC3|DTS|AAC|MP3)\b',
                r'\b(REPACK|PROPER|RERIP)\b',
            ]
            
            for pattern in clutter_patterns:
                matches = re.findall(pattern, name, re.IGNORECASE)
                for match in matches:
                    exact_strings[match] += 1
        
        # Convert to lists with counts
        bracket_list = [{'pattern': p, 'count': c} for p, c in bracket_contents.most_common()]
        paren_list = [{'pattern': p, 'count': c} for p, c in parentheses_contents.most_common()]
        exact_list = [{'pattern': p, 'count': c} for p, c in exact_strings.most_common()]
        years_list = [{'pattern': p, 'count': c} for p, c in years_found.most_common()]
        
        return {
            'bracket_patterns': bracket_list,
            'parentheses_patterns': paren_list,
            'exact_strings': exact_list,
            'years': years_list,
            'total_movies': len(movies)
        }
    finally:
        db.close()

def index_movie(file_path, db: Session = None):
    """Index a single movie file"""
    # Normalize the path to ensure consistent storage
    # file_path can be either a Path object or a string
    if isinstance(file_path, Path):
        path_obj = file_path
    else:
        path_obj = Path(file_path)
    
    # Use resolve() to get absolute normalized path
    try:
        normalized_path_obj = path_obj.resolve()
    except (OSError, RuntimeError):
        # If resolve fails, use absolute()
        normalized_path_obj = path_obj.absolute()
    
    # Convert to string - Path objects on Windows already use backslashes
    normalized_path = str(normalized_path_obj)
    
    file_hash = get_file_hash(normalized_path)
    
    # Use provided session or create new one
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        # Check if already indexed and unchanged
        existing = db.query(Movie).filter(Movie.path == normalized_path).first()
        file_unchanged = existing and existing.hash == file_hash
        
        # Check if frame exists for this movie
        existing_frame = None
        if existing:
            existing_frame = db.query(MovieFrame).filter(MovieFrame.movie_id == existing.id).first()
        has_frame = existing_frame and os.path.exists(existing_frame.path) if existing_frame else False
        
        # If file unchanged and frame exists, no update needed
        if file_unchanged and has_frame:
            return False  # No update needed
        
        add_scan_log("info", f"  Getting file metadata...")
        
        stat = os.stat(normalized_path)
        created = datetime.fromtimestamp(stat.st_ctime).isoformat()
        size = stat.st_size
        
        # Try to get video length
        length = get_video_length(normalized_path)
        
        # Find images in folder
        add_scan_log("info", f"  Searching for images in folder...")
        images = find_images_in_folder(normalized_path)
        if images:
            add_scan_log("success", f"  Found {len(images)} image(s)")
        
        # Extract screenshots (only if no images found or screenshots missing)
        screenshots = []
        if existing and existing.screenshots:
            screenshots = json.loads(existing.screenshots) if existing.screenshots else []
            add_scan_log("info", f"  Using existing screenshots")
        elif len(images) == 0 or not (existing and existing.screenshots):
            add_scan_log("info", f"  Extracting screenshots...")
            screenshots = extract_screenshots(normalized_path, num_screenshots=5)
            if screenshots:
                add_scan_log("success", f"  Extracted {len(screenshots)} screenshot(s)")
        
        # Extract movie frame (at 2-3 minutes, default 2.5 minutes = 150 seconds)
        add_scan_log("info", f"  Checking frame...")
        frame_path = None
        if existing_frame:
            # Check if the frame file still exists
            if os.path.exists(existing_frame.path):
                frame_path = existing_frame.path
                add_scan_log("info", f"  Frame already exists")
            else:
                # Frame file was deleted, remove from DB and queue for re-extraction
                add_scan_log("warning", f"  Frame file missing, queuing re-extraction...")
                db.delete(existing_frame)
                extract_movie_frame(normalized_path, timestamp_seconds=150, async_mode=True)
        else:
            # No frame exists, queue for extraction (even if file unchanged)
            add_scan_log("info", f"  No frame found, queuing extraction...")
            extract_movie_frame(normalized_path, timestamp_seconds=150, async_mode=True)
        
        # Filter out YTS images before storing in database (defense in depth)
        images = filter_yts_images(images)
        
        # Clean movie name and extract year
        raw_name = normalized_path_obj.stem
        cleaned_name, year = clean_movie_name(raw_name)
        
        # Create or update movie record
        movie = Movie(
            path=normalized_path,
            name=cleaned_name,
            year=year,
            length=length,
            created=created,
            size=size,
            hash=file_hash,
            images=json.dumps(images),
            screenshots=json.dumps(screenshots)
        )
        db.merge(movie)
        db.commit()
        return True
    finally:
        if should_close:
            db.close()

def scan_directory(root_path, state=None, progress_callback=None):
    """Scan directory for video files with optional progress callback"""
    root = Path(root_path)
    if not root.exists():
        add_scan_log("error", f"Path does not exist: {root_path}")
        return {"indexed": 0, "updated": 0, "errors": []}
    
    add_scan_log("info", f"Starting scan of: {root_path}")
    db = SessionLocal()
    try:
        # First pass: count total files
        global scan_progress
        scan_progress["status"] = "counting"
        scan_progress["current_file"] = "Counting files..."
        add_scan_log("info", "Counting video files...")
        
        total_files = 0
        for ext in VIDEO_EXTENSIONS:
            files = [f for f in root.rglob(f"*{ext}") if not is_sample_file(f)]
            count = len(files)
            total_files += count
            if count > 0:
                add_scan_log("info", f"Found {count} {ext} files")
        
        scan_progress["total"] = total_files
        scan_progress["current"] = 0
        scan_progress["status"] = "scanning"
        add_scan_log("success", f"Total files to process: {total_files}")
        
        indexed = 0
        updated = 0
        errors = []
        
        # Second pass: actually scan
        add_scan_log("info", "Starting file processing...")
        for ext in VIDEO_EXTENSIONS:
            if shutdown_flag.is_set():
                add_scan_log("warning", "Scan interrupted by shutdown")
                break
            for file_path in root.rglob(f"*{ext}"):
                if shutdown_flag.is_set():
                    add_scan_log("warning", "Scan interrupted by shutdown")
                    break
                
                # Skip sample files
                if is_sample_file(file_path):
                    add_scan_log("info", f"Skipping sample file: {file_path.name}")
                    continue
                
                try:
                    scan_progress["current"] = indexed + 1
                    scan_progress["current_file"] = file_path.name
                    
                    add_scan_log("info", f"[{indexed + 1}/{total_files}] Processing: {file_path.name}")
                    
                    if index_movie(file_path, db):
                        updated += 1
                        add_scan_log("success", f"Indexed: {file_path.name}")
                    else:
                        add_scan_log("info", f"Skipped (unchanged): {file_path.name}")
                    indexed += 1
                    
                    if progress_callback:
                        progress_callback(indexed, total_files, file_path.name)
                except Exception as e:
                    errors.append(str(file_path))
                    error_msg = str(e)
                    add_scan_log("error", f"Error indexing {file_path.name}: {error_msg[:150]}")
                    logger.error(f"Error indexing {file_path}: {e}")
        
        # Mark path as indexed
        db.merge(IndexedPath(path=str(root_path)))
        db.commit()
        
        add_scan_log("success", f"Scan complete: {indexed} files processed, {updated} updated, {len(errors)} errors")
        
        return {"indexed": indexed, "updated": updated, "errors": errors}
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
async def read_root():
    html_path = SCRIPT_DIR / "index.html"
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/star-rating.js")
async def get_star_rating_js():
    """Serve the star rating JavaScript file"""
    js_path = SCRIPT_DIR / "star-rating.js"
    if js_path.exists():
        with open(js_path, "r", encoding="utf-8") as f:
            from fastapi.responses import Response
            return Response(content=f.read(), media_type="application/javascript")
    raise HTTPException(status_code=404, detail="star-rating.js not found")

def run_scan_async(root_path: str):
    """Run scan in background thread"""
    global scan_progress, frame_extraction_queue
    try:
        if shutdown_flag.is_set():
            return
        scan_progress["is_scanning"] = True
        scan_progress["current"] = 0
        scan_progress["total"] = 0
        scan_progress["current_file"] = ""
        scan_progress["status"] = "starting"
        scan_progress["logs"] = []  # Clear previous logs
        scan_progress["frames_processed"] = 0
        scan_progress["frames_total"] = 0
        
        # Clear frame queue
        while not frame_extraction_queue.empty():
            try:
                frame_extraction_queue.get_nowait()
            except:
                break
        
        add_scan_log("info", "=" * 60)
        add_scan_log("info", "Starting movie scan")
        add_scan_log("info", f"Root path: {root_path}")
        add_scan_log("info", "=" * 60)
        
        # Start frame extraction processing in parallel (if not already running)
        process_frame_queue(max_workers=3)
        
        result = scan_directory(root_path, progress_callback=None)
        
        add_scan_log("info", "=" * 60)
        add_scan_log("success", f"Scan completed successfully!")
        add_scan_log("info", f"  Files processed: {result['indexed']}")
        add_scan_log("info", f"  Files updated: {result['updated']}")
        if result['errors']:
            add_scan_log("warning", f"  Errors: {len(result['errors'])}")
        queue_size = frame_extraction_queue.qsize()
        if queue_size > 0:
            add_scan_log("info", f"  Frames queued: {queue_size} (processing in background)")
        add_scan_log("info", "=" * 60)
        
        scan_progress["status"] = "complete"
        scan_progress["is_scanning"] = False
        logger.info(f"Scan complete: {result}")
    except Exception as e:
        add_scan_log("error", f"Fatal scan error: {str(e)}")
        scan_progress["status"] = f"error: {str(e)}"
        scan_progress["is_scanning"] = False
        logger.error(f"Scan error: {e}")

@app.post("/api/index")
async def index_movies(root_path: str = Query(None)):
    """One-time deep index scan (runs in background)"""
    global scan_progress
    
    if scan_progress["is_scanning"]:
        raise HTTPException(status_code=400, detail="Scan already in progress")
    
    logger.info(f"index_movies called with root_path: {root_path}")
    
    if not root_path:
        root_path = get_movies_folder()
        logger.info(f"Got root_path from get_movies_folder: {root_path}")
    
    if not root_path:
        error_msg = "Movies folder not found. Please create a 'movies' folder in the same directory as this script, or use 'Change Movies Folder' to select one."
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    
    path_obj = Path(root_path)
    logger.info(f"Checking path: {root_path}")
    
    if not path_obj.exists() and not os.path.exists(root_path):
        error_msg = f"Path not found: {root_path}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    
    logger.info(f"Starting scan of: {root_path}")
    
    # Run scan in background
    import threading
    thread = threading.Thread(target=run_scan_async, args=(root_path,))
    thread.daemon = True
    thread.start()
    
    return {"status": "started", "message": "Scan started in background"}

@app.get("/api/scan-progress")
async def get_scan_progress():
    """Get current scan progress"""
    global scan_progress, frame_extraction_queue
    return {
        "is_scanning": scan_progress["is_scanning"],
        "current": scan_progress["current"],
        "total": scan_progress["total"],
        "current_file": scan_progress["current_file"],
        "status": scan_progress["status"],
        "progress_percent": (scan_progress["current"] / scan_progress["total"] * 100) if scan_progress["total"] > 0 else 0,
        "logs": scan_progress.get("logs", []),
        "frame_queue_size": frame_extraction_queue.qsize(),
        "frames_processed": scan_progress.get("frames_processed", 0),
        "frames_total": scan_progress.get("frames_total", 0)
    }

@app.post("/api/admin/reindex")
async def admin_reindex(root_path: str = Query(None)):
    """Admin endpoint to reindex - uses same code as frontend"""
    global scan_progress
    
    if scan_progress["is_scanning"]:
        raise HTTPException(status_code=400, detail="Scan already in progress")
    
    logger.info(f"admin_reindex called with root_path: {root_path}")
    
    if not root_path:
        root_path = get_movies_folder()
        logger.info(f"Got root_path from get_movies_folder: {root_path}")
    
    if not root_path:
        error_msg = "Movies folder not found. Please create a 'movies' folder in the same directory as this script, or use 'Change Movies Folder' to select one."
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    
    path_obj = Path(root_path)
    logger.info(f"Checking path: {root_path}")
    
    if not path_obj.exists() and not os.path.exists(root_path):
        error_msg = f"Path not found: {root_path}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    
    logger.info(f"Starting scan of: {root_path}")
    
    # Run scan in background (same as frontend)
    import threading
    thread = threading.Thread(target=run_scan_async, args=(root_path,))
    thread.daemon = True
    thread.start()
    
    return {"status": "started", "message": "Reindex started in background"}

@app.get("/api/search")
async def search_movies(q: str, filter_type: str = Query("all", pattern="^(all|watched|unwatched)$")):
    """Search movies with autocomplete"""
    if not q or len(q) < 1:
        return {"results": []}
    
    db = SessionLocal()
    try:
        query_lower = q.lower()
        
        # Build query with search filter
        from sqlalchemy import or_
        movie_query = db.query(Movie).filter(
            func.lower(Movie.name).contains(query_lower)
        )
        
        # Get watched paths (movies with "watched" status in watch_history)
        watched_paths = set()
        watched_dict = {}
        # Get most recent "watched" entry per movie
        watch_entries = db.query(WatchHistory).filter(
            WatchHistory.watch_status == True
        ).order_by(WatchHistory.updated.desc()).all()
        
        for entry in watch_entries:
            if entry.movie_id not in watched_paths:
                watched_paths.add(entry.movie_id)
                watched_dict[entry.movie_id] = {
                    "watched_date": entry.updated.isoformat() if entry.updated else None,
                    "rating": None
                }
        
        # Get ratings
        for rating in db.query(Rating).all():
            if rating.movie_id in watched_dict:
                watched_dict[rating.movie_id]["rating"] = rating.rating
        
        results = []
        for movie in movie_query.all():
            is_watched = movie.path in watched_paths
            
            # Apply watched/unwatched filter
            if filter_type == "watched" and not is_watched:
                continue
            if filter_type == "unwatched" and is_watched:
                continue
            
            name_lower = movie.name.lower()
            # Calculate match score (exact start = higher score)
            score = 100 if name_lower.startswith(query_lower) else 50
            
            # Parse images and screenshots
            images = json.loads(movie.images) if movie.images else []
            screenshots = json.loads(movie.screenshots) if movie.screenshots else []
            
            # Filter out YTS images
            images = filter_yts_images(images)
            
            # Get frame path
            frame_path = get_movie_frame_path(db, movie.id)
            
            # Build info dict for get_largest_image (include frame)
            info = {
                "images": images,
                "screenshots": screenshots,
                "frame": frame_path
            }
            
            # Get largest image
            largest_image = get_largest_image(info)
            
            # Extract year from name
            year = extract_year_from_name(movie.name)
            
            # Check if launched
            has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
            
            results.append({
                "path": movie.path,
                "name": movie.name,
                "length": movie.length,
                "created": movie.created,
                "size": movie.size,
                "watched": is_watched,
                "watched_date": watched_dict.get(movie.path, {}).get("watched_date") if is_watched else None,
                "rating": watched_dict.get(movie.path, {}).get("rating") if is_watched else None,
                "score": score,
                "images": images,
                "screenshots": screenshots,
                "frame": frame_path,
                "image": largest_image,
                "year": year,
                "has_launched": has_launched
            })
        
        # Sort by score, then name
        results.sort(key=lambda x: (-x["score"], x["name"].lower()))
        
        # Save to history
        search_entry = SearchHistory(
            query=q,
            timestamp=datetime.now(),
            results_count=len(results)
        )
        db.add(search_entry)
        
        # Keep last 100 searches
        search_count = db.query(SearchHistory).count()
        if search_count > 100:
            oldest = db.query(SearchHistory).order_by(SearchHistory.created.asc()).limit(search_count - 100).all()
            for old_search in oldest:
                db.delete(old_search)
        
        db.commit()
        
        return {"results": results[:50]}  # Limit to 50 results
    finally:
        db.close()

@app.get("/api/movie")
async def get_movie_details(path: str = Query(...)):
    """Get detailed information about a specific movie"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.path == path).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Check if watched (has "watched" status in watch_history)
        watch_entry = db.query(WatchHistory).filter(
            WatchHistory.movie_id == movie.id,
            WatchHistory.watch_status == True
        ).order_by(WatchHistory.updated.desc()).first()
        is_watched = watch_entry is not None
        
        # Get rating
        rating_entry = db.query(Rating).filter(Rating.movie_id == movie.id).first()
        
        images = json.loads(movie.images) if movie.images else []
        screenshots = json.loads(movie.screenshots) if movie.screenshots else []
        
        # Filter out YTS images
        images = filter_yts_images(images)
        
        # Get frame path
        frame_path = get_movie_frame_path(db, movie.id)
        
        info = {
            "images": images,
            "screenshots": screenshots,
            "frame": frame_path
        }
        
        # Get largest image
        largest_image = get_largest_image(info)
        
        # Extract year from name
        year = extract_year_from_name(movie.name)
        
        has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
        
        return {
            "path": movie.path,
            "name": movie.name,
            "length": movie.length,
            "created": movie.created,
            "size": movie.size,
            "watched": is_watched,
            "watched_date": watch_entry.updated.isoformat() if watch_entry and watch_entry.updated else None,
            "rating": rating_entry.rating if rating_entry else None,
            "images": images,
            "screenshots": screenshots,
            "frame": frame_path,
            "image": largest_image,
            "year": year,
            "has_launched": has_launched
        }
    finally:
        db.close()

@app.get("/api/image")
async def get_image(image_path: str):
    """Serve image files"""
    from fastapi.responses import FileResponse
    
    try:
        # Normalize path - handle both forward and backslashes on Windows
        # URL decode first in case it was encoded
        import urllib.parse
        decoded_path = urllib.parse.unquote(image_path)
        # Convert forward slashes to backslashes on Windows for path operations
        if os.name == 'nt':  # Windows
            normalized_path = decoded_path.replace('/', '\\')
        else:
            normalized_path = decoded_path.replace('\\', '/')
        
        path_obj = Path(normalized_path)
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail="Image not found")
        
        # Security: ensure path is within allowed directories
        movies_folder = get_movies_folder()
        if movies_folder:
            movies_path = Path(movies_folder)
            try:
                path_obj.resolve().relative_to(movies_path.resolve())
            except ValueError:
                # Also allow screenshots and frames directories
                try:
                    path_obj.resolve().relative_to(SCREENSHOT_DIR.resolve())
                except ValueError:
                    try:
                        path_obj.resolve().relative_to(FRAMES_DIR.resolve())
                    except ValueError:
                        raise HTTPException(status_code=403, detail="Access denied")
        
        return FileResponse(str(path_obj))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving image {image_path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def find_subtitle_file(video_path):
    """Find subtitle file for a video"""
    video_path_obj = Path(video_path)
    base_name = video_path_obj.stem
    
    # Check same directory first
    video_dir = video_path_obj.parent
    for ext in SUBTITLE_EXTENSIONS:
        subtitle_path = video_dir / f"{base_name}{ext}"
        if subtitle_path.exists():
            return str(subtitle_path)
    
    # Check for common subtitle naming patterns
    for ext in SUBTITLE_EXTENSIONS:
        for pattern in [f"{base_name}.en{ext}", f"{base_name}.eng{ext}", f"{base_name}_en{ext}"]:
            subtitle_path = video_dir / pattern
            if subtitle_path.exists():
                return str(subtitle_path)
    
    return None

@app.post("/api/launch")
async def launch_movie(request: LaunchRequest):
    """Launch movie in VLC with optional subtitle"""
    steps = []
    results = []
    
    # The path should always be in the index - use it directly
    # Paths are stored correctly in the index during scanning
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.path == request.path).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found in index: {request.path}")
    finally:
        db.close()
    
    movie_path = request.path
    
    # Step 1: Verify file exists
    steps.append("Step 1: Verifying movie file exists")
    if not os.path.exists(movie_path):
        error_msg = f"File not found: {movie_path} (original: {request.path})"
        steps.append(f"  ERROR: {error_msg}")
        results.append({"step": 1, "status": "error", "message": error_msg})
        # Return error with steps included
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "detail": error_msg,
                "steps": steps,
                "results": results
            }
        )
    results.append({"step": 1, "status": "success", "message": f"File found: {movie_path}"})
    steps.append(f"  SUCCESS: File exists at {movie_path}")
    
    try:
        # Step 2: Find VLC executable
        steps.append("Step 2: Locating VLC executable")
        vlc_paths = [
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\VideoLAN\vlc.exe"),
            "vlc"  # If in PATH
        ]
        
        vlc_exe = None
        checked_paths = []
        for path in vlc_paths:
            checked_paths.append(path)
            if path == "vlc":
                # Check if vlc is in PATH
                try:
                    result = subprocess.run(["vlc", "--version"], capture_output=True, timeout=2)
                    if result.returncode == 0:
                        vlc_exe = path
                        steps.append(f"  Found VLC in PATH")
                        break
                except:
                    steps.append(f"  Checked PATH: not found")
            elif os.path.exists(path):
                vlc_exe = path
                steps.append(f"  Found VLC at: {path}")
                break
            else:
                steps.append(f"  Checked: {path} (not found)")
        
        if not vlc_exe:
            error_msg = "VLC not found. Please install VLC or set path."
            steps.append(f"  ERROR: {error_msg}")
            steps.append(f"  Checked paths: {', '.join(checked_paths)}")
            results.append({"step": 2, "status": "error", "message": error_msg, "checked_paths": checked_paths})
            # Return error with steps included
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "detail": error_msg,
                    "steps": steps,
                    "results": results,
                    "checked_paths": checked_paths
                }
            )
        results.append({"step": 2, "status": "success", "message": f"VLC found at: {vlc_exe}"})
        
        # Step 2.5: Close existing VLC windows if requested
        if request.close_existing_vlc:
            steps.append("Step 2.5: Closing existing VLC windows")
            try:
                if os.name == 'nt':  # Windows
                    # Find all VLC processes
                    result = subprocess.run(
                        ["tasklist", "/FI", "IMAGENAME eq vlc.exe", "/FO", "CSV", "/NH"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        # Count processes
                        lines = [line for line in result.stdout.strip().split('\n') if line.strip()]
                        process_count = len(lines)
                        steps.append(f"  Found {process_count} existing VLC process(es)")
                        
                        # Close them
                        kill_result = subprocess.run(
                            ["taskkill", "/F", "/IM", "vlc.exe"],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if kill_result.returncode == 0:
                            steps.append(f"  Successfully closed {process_count} VLC process(es)")
                            results.append({"step": 2.5, "status": "success", "message": f"Closed {process_count} existing VLC process(es)"})
                        else:
                            steps.append(f"  WARNING: Failed to close some VLC processes: {kill_result.stderr}")
                            results.append({"step": 2.5, "status": "warning", "message": "Some VLC processes may not have closed"})
                    else:
                        steps.append("  No existing VLC processes found")
                        results.append({"step": 2.5, "status": "info", "message": "No existing VLC processes to close"})
                else:
                    # Linux/Mac - use pkill or killall
                    try:
                        result = subprocess.run(
                            ["pkill", "-f", "vlc"],
                            capture_output=True,
                            timeout=5
                        )
                        if result.returncode == 0:
                            steps.append("  Closed existing VLC processes")
                            results.append({"step": 2.5, "status": "success", "message": "Closed existing VLC processes"})
                        else:
                            steps.append("  No existing VLC processes found")
                            results.append({"step": 2.5, "status": "info", "message": "No existing VLC processes to close"})
                    except FileNotFoundError:
                        # Try killall as fallback
                        try:
                            subprocess.run(["killall", "vlc"], capture_output=True, timeout=5)
                            steps.append("  Closed existing VLC processes (using killall)")
                            results.append({"step": 2.5, "status": "success", "message": "Closed existing VLC processes"})
                        except:
                            steps.append("  WARNING: Could not close existing VLC processes (pkill/killall not available)")
                            results.append({"step": 2.5, "status": "warning", "message": "Could not close existing VLC processes"})
            except Exception as e:
                steps.append(f"  WARNING: Error closing existing VLC processes: {str(e)}")
                results.append({"step": 2.5, "status": "warning", "message": f"Error closing existing VLC: {str(e)}"})
        else:
            steps.append("Step 2.5: Skipping close existing VLC (option disabled)")
            results.append({"step": 2.5, "status": "info", "message": "Close existing VLC option disabled"})
        
        # Step 3: Build VLC command
        steps.append("Step 3: Building VLC command")
        vlc_cmd = [vlc_exe, movie_path]
        steps.append(f"  Base command: {vlc_exe} {movie_path}")
        results.append({"step": 3, "status": "success", "message": f"Command prepared: {vlc_exe}"})
        
        # Step 4: Handle subtitles
        steps.append("Step 4: Checking for subtitles")
        subtitle_path = request.subtitle_path
        if not subtitle_path:
            steps.append("  No subtitle provided, attempting auto-detection")
            subtitle_path = find_subtitle_file(movie_path)
            if subtitle_path:
                steps.append(f"  Auto-detected subtitle: {subtitle_path}")
            else:
                steps.append("  No subtitle file found")
        else:
            steps.append(f"  Subtitle provided: {subtitle_path}")
        
        if subtitle_path and os.path.exists(subtitle_path):
            vlc_cmd.extend(["--sub-file", subtitle_path])
            steps.append(f"  Added subtitle to command: {subtitle_path}")
            results.append({"step": 4, "status": "success", "message": f"Subtitle loaded: {subtitle_path}"})
        else:
            if subtitle_path:
                steps.append(f"  WARNING: Subtitle file not found: {subtitle_path}")
                results.append({"step": 4, "status": "warning", "message": f"Subtitle file not found: {subtitle_path}"})
            else:
                steps.append("  No subtitle will be used")
                results.append({"step": 4, "status": "info", "message": "No subtitle file"})
        
        # Step 5: Launch VLC
        steps.append("Step 5: Launching VLC")
        steps.append(f"  Full command: {' '.join(vlc_cmd)}")
        try:
            process = subprocess.Popen(vlc_cmd, shell=False)
            steps.append(f"  VLC process started (PID: {process.pid})")
            results.append({"step": 5, "status": "success", "message": f"VLC launched successfully (PID: {process.pid})"})
        except Exception as e:
            error_msg = f"Failed to launch VLC: {str(e)}"
            steps.append(f"  ERROR: {error_msg}")
            results.append({"step": 5, "status": "error", "message": error_msg})
            raise
        
        # Step 6: Save to history
        steps.append("Step 6: Saving to history")
        db = SessionLocal()
        try:
            # Get movie ID from path
            movie = db.query(Movie).filter(Movie.path == movie_path).first()
            if not movie:
                raise HTTPException(status_code=404, detail=f"Movie not found in database: {movie_path}")
            
            launch_entry = LaunchHistory(
                movie_id=movie.id,
                subtitle=subtitle_path,
                timestamp=datetime.now()
            )
            db.add(launch_entry)
            
            # Create watch history entry for launch (watch session started)
            watch_entry = WatchHistory(
                movie_id=movie.id,
                watch_status=None  # NULL = unknown (started watching but not finished)
            )
            db.add(watch_entry)
            
            db.commit()
            steps.append("  History saved successfully")
            results.append({"step": 6, "status": "success", "message": "Launch saved to history"})
        finally:
            db.close()
        
        # Final summary
        steps.append("=" * 50)
        steps.append("LAUNCH COMPLETE")
        steps.append(f"Movie: {movie_path}")
        steps.append(f"VLC: {vlc_exe}")
        steps.append(f"Subtitle: {subtitle_path or 'None'}")
        steps.append(f"Process ID: {process.pid}")
        steps.append("=" * 50)
        
        return {
            "status": "launched",
            "subtitle": subtitle_path,
            "steps": steps,
            "results": results,
            "vlc_path": vlc_exe,
            "command": " ".join(vlc_cmd),
            "process_id": process.pid
        }
    except HTTPException as he:
        # Include steps in error response if possible
        error_detail = str(he.detail)
        steps.append(f"  HTTP ERROR: {error_detail}")
        results.append({"step": "error", "status": "error", "message": error_detail})
        # Try to return steps in error response
        try:
            return JSONResponse(
                status_code=he.status_code,
                content={
                    "status": "error",
                    "detail": error_detail,
                    "steps": steps,
                    "results": results
                }
            )
        except:
            raise he
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        steps.append(f"  FATAL ERROR: {error_msg}")
        results.append({"step": "error", "status": "error", "message": error_msg})
        # Try to return steps in error response
        try:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "detail": error_msg,
                    "steps": steps,
                    "results": results
                }
            )
        except:
            raise HTTPException(status_code=500, detail=error_msg)

@app.get("/api/history")
async def get_history():
    """Get search and launch history"""
    return load_history()

@app.get("/api/launch-history")
async def get_launch_history():
    """Get launch history with movie information"""
    db = SessionLocal()
    try:
        # Single query with JOINs to get all data at once
        # Subquery to get most recent watch entry per movie
        watch_subq = db.query(
            WatchHistory.movie_id,
            func.max(WatchHistory.updated).label('max_updated')
        ).filter(
            WatchHistory.watch_status == True
        ).group_by(WatchHistory.movie_id).subquery()
        
        watch_alias = aliased(WatchHistory)
        
        results = db.query(
            LaunchHistory,
            Movie,
            watch_alias,
            Rating,
            MovieFrame
        ).join(
            Movie, LaunchHistory.movie_id == Movie.id
        ).outerjoin(
            watch_subq, Movie.id == watch_subq.c.movie_id
        ).outerjoin(
            watch_alias, 
            (watch_alias.movie_id == watch_subq.c.movie_id) & 
            (watch_alias.updated == watch_subq.c.max_updated)
        ).outerjoin(
            Rating, Movie.id == Rating.movie_id
        ).outerjoin(
            MovieFrame, Movie.id == MovieFrame.movie_id
        ).order_by(
            LaunchHistory.created.desc()
        ).limit(100).all()
        
        launches_with_info = []
        for launch, movie, watch_entry, rating_entry, frame in results:
            if not movie:
                continue
            
            images = json.loads(movie.images) if movie.images else []
            screenshots = json.loads(movie.screenshots) if movie.screenshots else []
            
            # Filter out YTS images
            images = filter_yts_images(images)
            
            # Get frame path
            frame_path = None
            if frame and os.path.exists(frame.path):
                frame_path = frame.path
            
            info = {
                "images": images,
                "screenshots": screenshots,
                "frame": frame_path
            }
            
            movie_info = {
                "path": movie.path,
                "name": movie.name,
                "length": movie.length,
                "created": movie.created,
                "size": movie.size,
                "watched": watch_entry is not None,
                "watched_date": watch_entry.updated.isoformat() if watch_entry and watch_entry.updated else None,
                "rating": rating_entry.rating if rating_entry else None,
                "images": images,
                "screenshots": screenshots,
                "frame": frame_path,
                "image": get_largest_image(info),
                "year": extract_year_from_name(movie.name)
            }
            
            launches_with_info.append({
                "movie": movie_info,
                "timestamp": launch.created.isoformat(),
                "subtitle": launch.subtitle
            })
        
        return {"launches": launches_with_info}
    finally:
        db.close()

@app.post("/api/watched")
async def mark_watched(request: WatchedRequest):
    """Mark movie as watched or unwatched, optionally with rating"""
    db = SessionLocal()
    try:
        # Get movie ID from path
        movie = db.query(Movie).filter(Movie.path == request.path).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {request.path}")
        
        if request.watched:
            # Create watch history entry
            watch_entry = WatchHistory(
                movie_id=movie.id,
                watch_status=True
            )
            db.add(watch_entry)
            
            # Update rating if provided
            if request.rating is not None:
                rating_entry = Rating(
                    movie_id=movie.id,
                    rating=request.rating
                )
                db.merge(rating_entry)
        else:
            # Remove watch status (delete "watched" entries)
            db.query(WatchHistory).filter(
                WatchHistory.movie_id == movie.id,
                WatchHistory.watch_status == True
            ).delete()
            # Note: We keep the rating even when unwatched, but you can delete it if desired
            # db.query(Rating).filter(Rating.movie_id == movie.id).delete()
        
        db.commit()
        return {"status": "updated"}
    finally:
        db.close()

@app.get("/api/watched")
async def get_watched():
    """Get list of watched movies"""
    db = SessionLocal()
    try:
        watched_movies_list = []
        
        # Get all movies with "watched" status, get most recent entry per movie
        watch_entries = db.query(WatchHistory).filter(
            WatchHistory.watch_status == True
        ).order_by(WatchHistory.updated.desc()).all()
        
        watched_movie_ids = set()
        for watch_entry in watch_entries:
            if watch_entry.movie_id not in watched_movie_ids:
                watched_movie_ids.add(watch_entry.movie_id)
                
                movie = db.query(Movie).filter(Movie.id == watch_entry.movie_id).first()
                if movie:
                    # Get rating
                    rating_entry = db.query(Rating).filter(Rating.movie_id == movie.id).first()
                    
                    images = json.loads(movie.images) if movie.images else []
                    screenshots = json.loads(movie.screenshots) if movie.screenshots else []
                    
                    # Get frame path
                    frame_path = get_movie_frame_path(db, movie.id)
                    
                    info = {
                        "images": images,
                        "screenshots": screenshots,
                        "frame": frame_path
                    }
                    
                    movie_info = {
                        "path": movie.path,
                        "name": movie.name,
                        "length": movie.length,
                        "created": movie.created,
                        "size": movie.size,
                        "watched_date": watch_entry.updated.isoformat() if watch_entry.updated else None,
                        "rating": rating_entry.rating if rating_entry else None,
                        "images": images,
                        "screenshots": screenshots,
                        "frame": frame_path,
                        "image": get_largest_image(info),
                        "year": extract_year_from_name(movie.name),
                        "has_launched": db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
                    }
                    watched_movies_list.append(movie_info)
        
        # Sort by watched date (most recent first)
        watched_movies_list.sort(key=lambda x: x.get("watched_date", ""), reverse=True)
        
        return {"watched": watched_movies_list}
    finally:
        db.close()

@app.get("/api/subtitles")
async def get_subtitles(video_path: str):
    """Find available subtitle files for a video"""
    video_path_obj = Path(video_path)
    video_dir = video_path_obj.parent
    base_name = video_path_obj.stem
    
    subtitles = []
    for ext in SUBTITLE_EXTENSIONS:
        # Check exact match
        subtitle_path = video_dir / f"{base_name}{ext}"
        if subtitle_path.exists():
            subtitles.append({
                "path": str(subtitle_path),
                "name": subtitle_path.name,
                "type": ext[1:].upper()
            })
        
        # Check common patterns
        for pattern in [f"{base_name}.en{ext}", f"{base_name}.eng{ext}", f"{base_name}_en{ext}"]:
            subtitle_path = video_dir / pattern
            if subtitle_path.exists() and str(subtitle_path) not in [s["path"] for s in subtitles]:
                subtitles.append({
                    "path": str(subtitle_path),
                    "name": subtitle_path.name,
                    "type": ext[1:].upper()
                })
    
    return {"subtitles": subtitles}

@app.get("/api/watch-history")
async def get_watch_history(movie_id: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=1000)):
    """Get watch history for a specific movie or all movies. movie_id can be a path or integer ID."""
    db = SessionLocal()
    try:
        actual_movie_id = None
        if movie_id:
            # Try to parse as integer first
            try:
                actual_movie_id = int(movie_id)
            except ValueError:
                # If not an integer, treat as path and get movie ID
                movie = db.query(Movie).filter(Movie.path == movie_id).first()
                if movie:
                    actual_movie_id = movie.id
                else:
                    raise HTTPException(status_code=404, detail=f"Movie not found: {movie_id}")
        
        if actual_movie_id:
            watch_history = db.query(WatchHistory).filter(
                WatchHistory.movie_id == actual_movie_id
            ).order_by(WatchHistory.updated.desc()).limit(limit).all()
        else:
            watch_history = db.query(WatchHistory).order_by(
                WatchHistory.updated.desc()
            ).limit(limit).all()
        
        history_list = []
        for entry in watch_history:
            movie = db.query(Movie).filter(Movie.id == entry.movie_id).first()
            history_list.append({
                "id": entry.id,
                "movie_id": entry.movie_id,
                "movie_path": movie.path if movie else None,
                "name": movie.name if movie else f"Movie ID {entry.movie_id}",
                "watch_status": entry.watch_status,
                "timestamp": entry.updated.isoformat() if entry.updated else None
            })
        
        return {"history": history_list}
    finally:
        db.close()

@app.get("/api/config")
async def get_config():
    """Get current configuration"""
    db = SessionLocal()
    try:
        config = load_config()
        movies_folder = get_movies_folder()
        logger.info(f"get_config returning movies_folder: {movies_folder}")
        
        # Check ffmpeg status
        ffmpeg_path = find_ffmpeg()
        ffmpeg_status = {
            "found": ffmpeg_path is not None,
            "path": ffmpeg_path or "",
            "configured": config.get("ffmpeg_path") or None
        }
        
        # Return all config settings
        return {
            "movies_folder": movies_folder or "",
            "default_folder": str(SCRIPT_DIR / "movies"),
            "ffmpeg": ffmpeg_status,
            "settings": config  # Return all settings
        }
    finally:
        db.close()

@app.post("/api/config")
async def set_config(request: ConfigRequest):
    """Set movies folder path and/or user settings"""
    global ROOT_MOVIE_PATH
    logger.info(f"set_config called with: {request.movies_folder}, settings: {request.settings}")
    
    config = load_config()
    
    # Update movies folder if provided
    if request.movies_folder is not None:
        if not request.movies_folder:
            # Reset to default
            config.pop("movies_folder", None)
            save_config(config)
            ROOT_MOVIE_PATH = get_movies_folder()
            logger.info(f"Reset to default folder: {ROOT_MOVIE_PATH}")
            return {"status": "reset", "movies_folder": ROOT_MOVIE_PATH or ""}
        
        # Normalize path (handle both / and \)
        folder_path = request.movies_folder.strip()
        # Convert forward slashes to backslashes on Windows
        if os.name == 'nt':  # Windows
            folder_path = folder_path.replace('/', '\\')
            # Normalize double backslashes (but preserve UNC paths)
            if not folder_path.startswith('\\\\'):
                folder_path = folder_path.replace('\\\\', '\\')
            # Remove trailing backslash (unless it's a drive root like C:\)
            if folder_path.endswith('\\') and len(folder_path) > 3:
                folder_path = folder_path.rstrip('\\')
        
        logger.info(f"Normalized path: '{folder_path}'")
        logger.info(f"Path type: {type(folder_path)}")
        logger.info(f"Path length: {len(folder_path)}")
        logger.info(f"Path repr: {repr(folder_path)}")
        
        # Try Path object approach
        path_obj = Path(folder_path)
        logger.info(f"Path object: {path_obj}")
        logger.info(f"Path object absolute: {path_obj.absolute()}")
        logger.info(f"Path object exists (Path): {path_obj.exists()}")
        logger.info(f"Path object is_dir (Path): {path_obj.is_dir()}")
        
        # Try os.path approach
        logger.info(f"os.path.exists: {os.path.exists(folder_path)}")
        logger.info(f"os.path.isdir: {os.path.isdir(folder_path)}")
        logger.info(f"os.path.abspath: {os.path.abspath(folder_path)}")
        
        # Check if path exists using both methods
        exists_pathlib = path_obj.exists()
        exists_os = os.path.exists(folder_path)
        
        logger.info(f"Path exists check - pathlib: {exists_pathlib}, os.path: {exists_os}")
        
        if not exists_pathlib and not exists_os:
            error_msg = f"Path not found: '{folder_path}' (checked with both pathlib and os.path)"
            logger.error(error_msg)
            # Try to list parent directory to help debug
            parent = path_obj.parent
            if parent.exists():
                try:
                    contents = list(parent.iterdir())
                    logger.info(f"Parent directory exists. Contents: {[str(c) for c in contents[:10]]}")
                except Exception as e:
                    logger.error(f"Error listing parent directory: {e}")
            raise HTTPException(status_code=404, detail=error_msg)
        
        # Check if it's a directory
        is_dir_pathlib = path_obj.is_dir()
        is_dir_os = os.path.isdir(folder_path)
        
        logger.info(f"Is directory check - pathlib: {is_dir_pathlib}, os.path: {is_dir_os}")
        
        if not is_dir_pathlib and not is_dir_os:
            error_msg = f"Path is not a directory: '{folder_path}'"
            logger.error(error_msg)
            raise HTTPException(status_code=400, detail=error_msg)
        
        # Save movies folder to config
        config["movies_folder"] = folder_path
        save_config(config)
        logger.info(f"Saved to config: {folder_path}")
        
        # Update global
        ROOT_MOVIE_PATH = folder_path
        logger.info(f"Updated ROOT_MOVIE_PATH to: {ROOT_MOVIE_PATH}")
    
    # Update user settings if provided
    if request.settings:
        for key, value in request.settings.items():
            # Special validation for ffmpeg_path
            if key == "ffmpeg_path":
                if value:  # If setting a path, validate it
                    is_valid, error_msg = validate_ffmpeg_path(value)
                    if not is_valid:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid ffmpeg path: {error_msg}. Path: {value}"
                        )
                    logger.info(f"Validated ffmpeg path: {value}")
                else:
                    # Empty string means remove the setting (use auto-detection)
                    config.pop("ffmpeg_path", None)
                    logger.info("Removed ffmpeg_path setting, will use auto-detection")
                    continue
            
            config[key] = value
        save_config(config)
        logger.info(f"Updated user settings: {list(request.settings.keys())}")
    
    return {"status": "updated", "movies_folder": config.get("movies_folder", ""), "settings": config}

@app.post("/api/open-folder")
async def open_folder(path: str = Query(...)):
    """Open file explorer at the folder containing the movie file"""
    try:
        path_obj = Path(path)
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        folder_path = path_obj.parent
        
        if os.name == 'nt':  # Windows
            subprocess.Popen(f'explorer.exe /select,"{path_obj}"', shell=True)
        elif os.name == 'posix':  # Linux/Mac
            if os.uname().sysname == 'Darwin':  # macOS
                subprocess.Popen(['open', '-R', str(path_obj)])
            else:  # Linux
                # Try various file managers
                for cmd in ['xdg-open', 'nautilus', 'dolphin', 'thunar']:
                    try:
                        subprocess.Popen([cmd, str(folder_path)])
                        break
                    except FileNotFoundError:
                        continue
                else:
                    raise HTTPException(status_code=500, detail="No file manager found")
        else:
            raise HTTPException(status_code=500, detail="Unsupported operating system")
        
        return {"status": "opened", "folder": str(folder_path)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error opening folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
async def get_stats():
    """Get indexing statistics"""
    db = SessionLocal()
    try:
        total_movies = db.query(Movie).count()
        # Count distinct movies with "watched" status
        watched_movie_ids = {entry.movie_id for entry in db.query(WatchHistory).filter(
            WatchHistory.watch_status == True
        ).all()}
        watched_count = len(watched_movie_ids)
        indexed_paths = [ip.path for ip in db.query(IndexedPath).all()]
        movies_folder = get_movies_folder()
        return {
            "total_movies": total_movies,
            "watched_count": watched_count,
            "indexed_paths": indexed_paths,
            "movies_folder": movies_folder or ""
        }
    finally:
        db.close()

@app.get("/api/cleaning-patterns")
async def get_cleaning_patterns():
    """Get all suspicious patterns found in movie names"""
    try:
        analysis = analyze_movie_names()
        current_patterns = load_cleaning_patterns()
        return {
            "analysis": analysis,
            "current_patterns": {
                "exact_strings": list(current_patterns['exact_strings']),
                "bracket_patterns": current_patterns['bracket_patterns'],
                "parentheses_patterns": current_patterns['parentheses_patterns'],
                "year_patterns": current_patterns['year_patterns'],
            }
        }
    except Exception as e:
        logger.error(f"Error getting cleaning patterns: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/cleaning-patterns")
async def save_cleaning_patterns_endpoint(data: dict):
    """Save approved cleaning patterns"""
    try:
        patterns = {
            'exact_strings': set(data.get('exact_strings', [])),
            'bracket_patterns': data.get('bracket_patterns', []),
            'parentheses_patterns': data.get('parentheses_patterns', []),
            'year_patterns': data.get('year_patterns', True),
        }
        if save_cleaning_patterns(patterns):
            return {"success": True}
        else:
            raise HTTPException(status_code=500, detail="Failed to save patterns")
    except Exception as e:
        logger.error(f"Error saving cleaning patterns: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def get_vlc_window_titles():
    """Get window titles from running VLC instances on Windows"""
    if os.name != 'nt':  # Windows only for now
        return []
    
    try:
        # Use PowerShell to get VLC window titles
        ps_command = """
        Get-Process | Where-Object {$_.ProcessName -eq 'vlc'} | ForEach-Object {
            $proc = $_
            Add-Type -TypeDefinition @"
                using System;
                using System.Runtime.InteropServices;
                public class Win32 {
                    [DllImport("user32.dll")]
                    public static extern IntPtr GetForegroundWindow();
                    [DllImport("user32.dll")]
                    public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
                }
"@
            $hwnd = $proc.MainWindowHandle
            if ($hwnd -ne [IntPtr]::Zero) {
                $title = New-Object System.Text.StringBuilder 256
                [Win32]::GetWindowText($hwnd, $title, $title.Capacity) | Out-Null
                $titleText = $title.ToString()
                if ($titleText) {
                    Write-Output "$titleText|$($proc.Id)"
                }
            }
        }
        """
        
        result = subprocess.run(
            ["powershell", "-Command", ps_command],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            titles = []
            for line in result.stdout.strip().split('\n'):
                if '|' in line:
                    title, pid = line.split('|', 1)
                    if title and title.strip():
                        titles.append({"title": title.strip(), "pid": pid.strip()})
            return titles
    except Exception as e:
        logger.warning(f"Error getting VLC window titles: {e}")
    
    return []

def get_vlc_command_lines():
    """Get command line arguments from running VLC processes"""
    if os.name != 'nt':  # Windows only
        return []
    
    try:
        import shlex
        import re
        
        # Use wmic to get command line arguments
        result = subprocess.run(
            ["wmic", "process", "where", "name='vlc.exe'", "get", "CommandLine,ProcessId", "/format:csv"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            command_lines = []
            lines = result.stdout.strip().split('\n')
            
            # Find header line to determine column positions
            header_line = None
            for line in lines:
                if 'CommandLine' in line and 'ProcessId' in line:
                    header_line = line
                    break
            
            if header_line:
                # Parse header to find column indices
                header_parts = [p.strip() for p in header_line.split(',')]
                try:
                    cmd_idx = header_parts.index('CommandLine')
                    pid_idx = header_parts.index('ProcessId')
                except ValueError:
                    # Fallback: assume standard order
                    cmd_idx = -2
                    pid_idx = -1
            
            for line in lines:
                if not line.strip() or 'CommandLine' in line or 'Node' in line:
                    continue
                
                parts = [p.strip() for p in line.split(',')]
                if len(parts) < 2:
                    continue
                
                if header_line:
                    cmd_line = parts[cmd_idx] if cmd_idx < len(parts) else ''
                    pid = parts[pid_idx] if pid_idx < len(parts) else ''
                else:
                    # Fallback parsing
                    cmd_line = parts[-2] if len(parts) >= 2 else ''
                    pid = parts[-1] if len(parts) >= 1 else ''
                
                if not cmd_line or 'vlc.exe' not in cmd_line.lower():
                    continue
                
                # Extract file path from command line
                # VLC command line format: "C:\path\to\vlc.exe" "C:\path\to\movie.mp4"
                try:
                    args = shlex.split(cmd_line)
                    # Find the first argument that's a file path (not vlc.exe itself)
                    for arg in args[1:]:  # Skip vlc.exe path
                        if os.path.exists(arg) and Path(arg).suffix.lower() in VIDEO_EXTENSIONS:
                            command_lines.append({"path": arg, "pid": pid})
                            break
                except:
                    # Fallback: try to extract path manually using regex
                    # Look for quoted paths or paths with video extensions
                    matches = re.findall(r'["\']([^"\']+\.(?:mp4|avi|mkv|mov|wmv|flv|webm|m4v|mpg|mpeg|3gp))["\']', cmd_line, re.IGNORECASE)
                    if matches:
                        for match in matches:
                            if os.path.exists(match):
                                command_lines.append({"path": match, "pid": pid})
                                break
                    else:
                        # Try unquoted paths
                        matches = re.findall(r'([A-Za-z]:[^"\']+\.(?:mp4|avi|mkv|mov|wmv|flv|webm|m4v|mpg|mpeg|3gp))', cmd_line, re.IGNORECASE)
                        for match in matches:
                            if os.path.exists(match):
                                command_lines.append({"path": match, "pid": pid})
                                break
            return command_lines
    except Exception as e:
        logger.warning(f"Error getting VLC command lines: {e}")
    
    return []

@app.get("/api/currently-playing")
async def get_currently_playing():
    """Get currently playing movies from VLC instances"""
    db = SessionLocal()
    try:
        playing = []
        
        # Try to get command line arguments first (more reliable)
        vlc_processes = get_vlc_command_lines()
        
        # If no command lines found, try window titles as fallback
        if not vlc_processes:
            titles = get_vlc_window_titles()
            # Try to match window titles to movie names
            for title_info in titles:
                title = title_info["title"]
                # VLC window title format is often: "movie_name - VLC media player"
                # Extract movie name
                if " - VLC" in title:
                    movie_name = title.split(" - VLC")[0].strip()
                    # Try to find matching movie in index
                    movie = db.query(Movie).filter(func.lower(Movie.name) == movie_name.lower()).first()
                    if movie:
                        playing.append({
                            "path": movie.path,
                            "name": movie.name,
                            "pid": title_info["pid"]
                        })
        
        # Process command line results
        for proc_info in vlc_processes:
            file_path = proc_info["path"]
            # Normalize path for comparison
            try:
                normalized_path = str(Path(file_path).resolve())
            except:
                normalized_path = file_path
            
            # Check if this path is in our index
            movie = db.query(Movie).filter(Movie.path == normalized_path).first()
            if movie:
                playing.append({
                    "path": normalized_path,
                    "name": movie.name,
                    "pid": proc_info["pid"]
                })
            else:
                # Try case-insensitive match
                movie = db.query(Movie).filter(func.lower(Movie.path) == normalized_path.lower()).first()
                if movie:
                    playing.append({
                        "path": movie.path,
                        "name": movie.name,
                        "pid": proc_info["pid"]
                    })
        
        return {"playing": playing}
    finally:
        db.close()

def extract_year_from_name(name):
    """Extract year from movie name (common patterns: (2023), 2023, -2023)"""
    import re
    # Try patterns: (2023), [2023], 2023, -2023
    patterns = [
        r'\((\d{4})\)',  # (2023)
        r'\[(\d{4})\]',  # [2023]
        r'\b(19\d{2}|20\d{2})\b',  # 2023 or 1999
    ]
    
    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            year = int(match.group(1))
            # Reasonable year range
            if 1900 <= year <= 2100:
                return year
    return None

def get_movie_frame_path(db: Session, movie_id: int):
    """Get the frame path for a movie from the database"""
    frame = db.query(MovieFrame).filter(MovieFrame.movie_id == movie_id).first()
    if frame and os.path.exists(frame.path):
        return frame.path
    return None

def filter_yts_images(image_paths):
    """Filter out images with 'www.YTS.AM' in filename"""
    if not image_paths:
        return []
    filtered = []
    for img_path in image_paths:
        # Check if filename contains www.YTS.AM
        img_name = Path(img_path).name
        if "www.YTS.AM" not in img_name:
            filtered.append(img_path)
    return filtered

def get_largest_image(movie_info):
    """Get the largest image file from movie's images or screenshots"""
    all_images = []
    
    # Add folder images (filter out YTS images)
    if movie_info.get("images"):
        filtered_images = filter_yts_images(movie_info["images"])
        for img_path in filtered_images:
            try:
                if os.path.exists(img_path):
                    size = os.path.getsize(img_path)
                    all_images.append((img_path, size))
            except:
                pass
    
    # Add screenshots
    if movie_info.get("screenshots"):
        for screenshot_path in movie_info["screenshots"]:
            try:
                if os.path.exists(screenshot_path):
                    size = os.path.getsize(screenshot_path)
                    all_images.append((screenshot_path, size))
            except:
                pass
    
    # Add frame if available
    if movie_info.get("frame"):
        try:
            if os.path.exists(movie_info["frame"]):
                size = os.path.getsize(movie_info["frame"])
                all_images.append((movie_info["frame"], size))
        except:
            pass
    
    if not all_images:
        return None
    
    # Return the path of the largest image
    largest = max(all_images, key=lambda x: x[1])
    return largest[0]

def get_first_letter(name):
    """Get the first letter of a movie name for alphabet navigation"""
    if not name:
        return "#"
    name_stripped = name.strip()
    if not name_stripped:
        return "#"
    first_char = name_stripped[0].upper()
    return first_char if first_char.isalpha() else "#"

@app.get("/api/explore")
async def explore_movies(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=100),
    filter_type: str = Query("all", pattern="^(all|watched|unwatched)$"),
    letter: Optional[str] = Query(None, pattern="^[A-Z#]$")
):
    """Get all movies for exploration view with pagination and filters"""
    # Normalize letter to uppercase if provided
    if letter is not None:
        letter = letter.upper()
    
    # Log the actual request URL and query params to debug letter filtering
    query_params = dict(request.query_params)
    logger.info(f"Explore endpoint called: URL={request.url}")
    logger.info(f"Query params: {query_params}")
    logger.info(f"Parsed letter parameter: {letter!r} (type: {type(letter)})")
    
    db = SessionLocal()
    try:
        # Get watched paths (movies with "watched" status)
        watched_paths = set()
        watched_dict = {}
        watch_entries = db.query(WatchHistory).filter(
            WatchHistory.watch_status == True
        ).order_by(WatchHistory.updated.desc()).all()
        
        for entry in watch_entries:
            if entry.movie_id not in watched_paths:
                watched_paths.add(entry.movie_id)
                watched_dict[entry.movie_id] = {
                    "watched_date": entry.updated.isoformat() if entry.updated else None,
                    "rating": None
                }
        
        # Get ratings
        for rating in db.query(Rating).all():
            if rating.movie_id in watched_dict:
                watched_dict[rating.movie_id]["rating"] = rating.rating
        
        # First pass: build all movies matching the filter (for letter counts)
        all_filtered_movies = []
        for movie in db.query(Movie).all():
            is_watched = movie.path in watched_paths
            
            # Apply watched/unwatched filter
            if filter_type == "watched" and not is_watched:
                continue
            if filter_type == "unwatched" and is_watched:
                continue
            
            first_letter = get_first_letter(movie.name)
            all_filtered_movies.append({
                "path": movie.path,
                "name": movie.name,
                "first_letter": first_letter,
                "is_watched": is_watched
            })
        
        # Calculate letter counts from all filtered movies (not affected by letter filter)
        letter_counts = {}
        for movie in all_filtered_movies:
            movie_letter = movie["first_letter"]
            letter_counts[movie_letter] = letter_counts.get(movie_letter, 0) + 1
        
        # Second pass: apply letter filter and build full movie list
        movies = []
        skipped_by_watched = 0
        skipped_by_letter = 0
        for movie in db.query(Movie).all():
            is_watched = movie.path in watched_paths
            
            # Apply watched/unwatched filter
            if filter_type == "watched" and not is_watched:
                skipped_by_watched += 1
                continue
            if filter_type == "unwatched" and is_watched:
                skipped_by_watched += 1
                continue
            
            first_letter = get_first_letter(movie.name)
            
            # Filter by letter if specified - only show movies that START with the letter
            if letter is not None and letter != "":
                if first_letter != letter:
                    skipped_by_letter += 1
                    continue
                # Debug: log first few matches
                if len(movies) < 3:
                    logger.info(f"Letter filter '{letter}' MATCH: '{movie.name}' -> first_letter='{first_letter}'")
            else:
                # Debug: log when no letter filter is applied
                if len(movies) < 3:
                    logger.debug(f"No letter filter: '{movie.name}' -> first_letter='{first_letter}'")
            
            images = json.loads(movie.images) if movie.images else []
            screenshots = json.loads(movie.screenshots) if movie.screenshots else []
            
            # Get frame path
            frame_path = get_movie_frame_path(db, movie.id)
            
            info = {
                "images": images,
                "screenshots": screenshots,
                "frame": frame_path
            }
            
            # Get largest image
            largest_image = get_largest_image(info)
            
            # Extract year from name
            year = extract_year_from_name(movie.name)
            
            has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
            
            movies.append({
                "path": movie.path,
                "name": movie.name,
                "length": movie.length,
                "created": movie.created,
                "size": movie.size,
                "watched": is_watched,
                "watched_date": watched_dict.get(movie.path, {}).get("watched_date") if is_watched else None,
                "rating": watched_dict.get(movie.path, {}).get("rating") if is_watched else None,
                "frame": frame_path,
                "image": largest_image,
                "first_letter": first_letter,
                "year": year,
                "has_launched": has_launched
            })
        
        # Sort by name (case-insensitive, ignoring leading dots/numbers)
        # Strip leading non-alphabetic characters for proper alphabetical sorting
        def sort_key(movie):
            name = movie["name"].strip()
            # Remove leading dots, numbers, and special chars for sorting
            name_clean = name.lstrip('.-_0123456789 ')
            if not name_clean:
                name_clean = name
            return name_clean.lower()
        
        movies.sort(key=sort_key)
        
        # Calculate pagination
        total = len(movies)
        start = (page - 1) * per_page
        end = start + per_page
        paginated_movies = movies[start:end]
        
        logger.info(f"Explore API: letter={letter}, filter_type={filter_type}, total_movies={total}, paginated={len(paginated_movies)}, skipped_by_watched={skipped_by_watched}, skipped_by_letter={skipped_by_letter}")
        if letter is not None:
            if paginated_movies:
                first_letters = [get_first_letter(m['name']) for m in paginated_movies[:10]]
                logger.info(f"After letter filter '{letter}': First 10 movie names: {[m['name'] for m in paginated_movies[:10]]}")
                logger.info(f"First letters of those movies: {first_letters}")
                # Verify filtering worked
                mismatches = [m['name'] for m in paginated_movies[:10] if get_first_letter(m['name']) != letter]
                if mismatches:
                    logger.error(f"FILTERING BUG: Found {len(mismatches)} movies that don't match letter '{letter}': {mismatches[:5]}")
            else:
                logger.warning(f"Letter filter '{letter}' applied but no movies returned! Total before filter: {len(movies)}")
        else:
            if paginated_movies:
                first_letters = [get_first_letter(m['name']) for m in paginated_movies[:10]]
                logger.info(f"No letter filter: First 10 movie names: {[m['name'] for m in paginated_movies[:10]]}")
                logger.info(f"First letters of those movies: {first_letters}")
        
        return {
            "movies": paginated_movies,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page if total > 0 else 0
            },
            "letter_counts": letter_counts
        }
    finally:
        db.close()


if __name__ == "__main__":
    # Register atexit handler for cleanup on exit
    atexit.register(lambda: (shutdown_flag.set(), kill_all_active_subprocesses()))
    
    import uvicorn
    import signal
    import sys
    
    def signal_handler(sig, frame):
        """Handle Ctrl+C gracefully"""
        logger.info("Received interrupt signal, shutting down...")
        shutdown_flag.set()
        kill_all_active_subprocesses()
        sys.exit(0)
    
    # Register signal handlers for clean shutdown
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    
    # Auto-reload enabled - server restarts when Python files change
    # Using uvicorn.run() for more reliable reload behavior
    # uvicorn handles signals, but we also register our own for extra safety
    
    # Configure uvicorn logging
    # According to uvicorn docs: use reload_includes=['*.py'] and reload_excludes=['*']
    # to only watch Python files and exclude everything else
    import logging.config
    uvicorn_log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(levelname)s - %(message)s",
            },
            "access": {
                "format": "%(asctime)s - %(levelname)s - %(message)s",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn.error": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }
    
    try:
        logger.info("=" * 60)
        logger.info("Starting Movie Searcher server")
        logger.info("Server URL: http://127.0.0.1:8002")
        logger.info("Auto-reload: ENABLED (server restarts on Python file changes)")
        logger.info("Note: 'X change detected' messages indicate file changes triggering reload")
        logger.info("=" * 60)
        uvicorn.run(
            "main:app",
            host="127.0.0.1",
            port=8002,
            reload=True,
            # Only watch Python files - exclude problematic files/dirs
            # Use reload_dirs to only watch the project directory (not parent dirs)
            reload_dirs=[str(Path(__file__).parent)],
            reload_includes=["*.py"],
            # Exclude files that change frequently but shouldn't trigger reloads
            reload_excludes=[
                "*.log",           # Log files
                "*.db", "*.db-*",  # Database files (including .db-wal, .db-shm)
                "*.json",          # JSON files
                "*.tmp",           # Temporary files
                "__pycache__/**",  # Python cache
                "venv/**",         # Virtual environment
                "frames/**",       # Extracted frames
                "screenshots/**",  # Screenshots
                "images/**",       # Images
            ],
            use_colors=False,
            log_config=uvicorn_log_config
        )
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        shutdown_flag.set()
        kill_all_active_subprocesses()
        sys.exit(0)

