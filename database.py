"""
Database setup, migrations, and utilities for Movie Searcher.
"""
from pathlib import Path
from typing import Optional
import os
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.sql import func
import logging

# Import all models and Base from models module
from models import (
    Base,
    Movie, Rating, WatchHistory, SearchHistory, LaunchHistory, 
    IndexedPath, Config, Screenshot, Image, SchemaVersion, MovieAudio,
    CURRENT_SCHEMA_VERSION
)

# Setup logging
logger = logging.getLogger(__name__)

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
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_schema_version():
    """Get current database schema version"""
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
        
        if current_version < 3:
            logger.info("Migrating to schema version 3: create screenshots and images tables, remove JSON columns from movies.")
            
            with engine.begin() as conn:
                # Drop old tables if they exist
                conn.execute(text("DROP TABLE IF EXISTS movie_frames"))
                
                # Create screenshots table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS screenshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        movie_id INTEGER NOT NULL,
                        shot_path VARCHAR NOT NULL,
                        created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        FOREIGN KEY(movie_id) REFERENCES movies (id) ON DELETE CASCADE
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_screenshots_movie_id ON screenshots (movie_id)"))
                
                # Create images table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS images (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        movie_id INTEGER NOT NULL,
                        image_path VARCHAR NOT NULL,
                        created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        FOREIGN KEY(movie_id) REFERENCES movies (id) ON DELETE CASCADE
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_images_movie_id ON images (movie_id)"))
                
                # Check if movies table has images/screenshots columns and remove them
                movies_columns = {col['name']: col for col in inspector.get_columns("movies")}
                has_images_column = "images" in movies_columns
                has_screenshots_column = "screenshots" in movies_columns
                
                if has_images_column or has_screenshots_column:
                    logger.info("Removing images and screenshots columns from movies table...")
                    # SQLite doesn't support DROP COLUMN, so recreate the table
                    conn.execute(text("""
                        CREATE TABLE movies_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                            path VARCHAR NOT NULL UNIQUE,
                            name VARCHAR NOT NULL,
                            year INTEGER,
                            length FLOAT,
                            size INTEGER,
                            hash VARCHAR,
                            created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                            updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                        )
                    """))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_path ON movies_new (path)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_name ON movies_new (name)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_hash ON movies_new (hash)"))
                    
                    # Copy data (excluding images and screenshots columns)
                    conn.execute(text("""
                        INSERT INTO movies_new (id, path, name, year, length, size, hash, created, updated)
                        SELECT id, path, name, year, length, size, hash, created, updated
                        FROM movies
                    """))
                    
                    # Drop old table and rename new one
                    conn.execute(text("DROP TABLE movies"))
                    conn.execute(text("ALTER TABLE movies_new RENAME TO movies"))
                    
                    # Recreate indexes
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_path ON movies (path)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_name ON movies (name)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_hash ON movies (hash)"))
            
            set_schema_version(3, "Created screenshots and images tables, removed JSON columns from movies")
            current_version = 3
        
        if current_version < 4:
            logger.info("Migrating to schema version 4: add language column to movies table.")
            
            existing_columns = {col['name']: col for col in inspector.get_columns("movies")}
            if "language" not in existing_columns:
                with engine.begin() as conn:
                    logger.info("Adding 'language' column to movies table...")
                    conn.execute(text("ALTER TABLE movies ADD COLUMN language VARCHAR"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_language ON movies (language)"))
                    logger.info("Migration complete: added 'language' column")
            
            set_schema_version(4, "Added language column to movies table")
            current_version = 4
        
        if current_version < 5:
            logger.info("Migrating to schema version 5: create movie_audio table.")
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS movie_audio (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        movie_id INTEGER NOT NULL,
                        audio_type VARCHAR NOT NULL,
                        created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        FOREIGN KEY(movie_id) REFERENCES movies (id) ON DELETE CASCADE
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_audio_movie_id ON movie_audio (movie_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_audio_audio_type ON movie_audio (audio_type)"))
            set_schema_version(5, "Created movie_audio table")
            current_version = 5
        
        if current_version < 6:
            logger.info("Migrating to schema version 6: add timestamp_seconds column to screenshots table.")
            existing_columns = {col['name']: col for col in inspector.get_columns("screenshots")}
            if "timestamp_seconds" not in existing_columns:
                with engine.begin() as conn:
                    logger.info("Adding 'timestamp_seconds' column to screenshots table...")
                    conn.execute(text("ALTER TABLE screenshots ADD COLUMN timestamp_seconds FLOAT"))
                    logger.info("Migration complete: added 'timestamp_seconds' column")
            
            set_schema_version(6, "Added timestamp_seconds column to screenshots table")
            current_version = 6
        
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
            "indexed_paths_new", "config_new", "screenshots_new", "images_new"
        ]
        indexes_to_drop = [
            "ix_movies_path", "ix_movies_name", "ix_movies_hash",
            "ix_ratings_movie_id", "ix_watch_history_movie_id",
            "ix_search_history_query", "ix_launch_history_movie_id",
            "ix_indexed_paths_path", "ix_config_key", "ix_screenshots_movie_id", "ix_images_movie_id"
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
        
        # Create new movies table (without images/screenshots columns)
        conn.execute(text("""
            CREATE TABLE movies_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                path VARCHAR NOT NULL UNIQUE,
                name VARCHAR NOT NULL,
                year INTEGER,
                length FLOAT,
                size INTEGER,
                hash VARCHAR,
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
        # Check if old table has images/screenshots columns
        old_movies_columns = {col['name']: col for col in inspector.get_columns("movies")}
        has_old_images = "images" in old_movies_columns
        has_old_screenshots = "screenshots" in old_movies_columns
        
        if has_old_images or has_old_screenshots:
            conn.execute(text("""
                INSERT INTO movies_new (path, name, year, length, size, hash, created, updated)
                SELECT 
                    path,
                    name,
                    year,
                    length,
                    size,
                    hash,
                    CASE 
                        WHEN created IS NULL OR created = '' THEN CURRENT_TIMESTAMP
                        ELSE datetime(created)
                    END,
                    CURRENT_TIMESTAMP
                FROM movies
            """))
        else:
            conn.execute(text("""
                INSERT INTO movies_new (path, name, year, length, size, hash, created, updated)
                SELECT 
                    path,
                    name,
                    year,
                    length,
                    size,
                    hash,
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
        
        # Create screenshots and images tables
        # Create screenshots table
        conn.execute(text("""
            CREATE TABLE screenshots_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                movie_id INTEGER NOT NULL,
                shot_path VARCHAR NOT NULL,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(movie_id) REFERENCES movies_new (id) ON DELETE CASCADE
            )
        """))
        conn.execute(text("CREATE INDEX ix_screenshots_movie_id ON screenshots_new (movie_id)"))
        
        # Create images table
        conn.execute(text("""
            CREATE TABLE images_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                movie_id INTEGER NOT NULL,
                image_path VARCHAR NOT NULL,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(movie_id) REFERENCES movies_new (id) ON DELETE CASCADE
            )
        """))
        conn.execute(text("CREATE INDEX ix_images_movie_id ON images_new (movie_id)"))
        
        # Step 3: Drop old tables
        logger.info("Dropping old tables...")
        conn.execute(text("DROP TABLE IF EXISTS ratings"))
        conn.execute(text("DROP TABLE IF EXISTS watch_history"))
        conn.execute(text("DROP TABLE IF EXISTS search_history"))
        conn.execute(text("DROP TABLE IF EXISTS launch_history"))
        conn.execute(text("DROP TABLE IF EXISTS indexed_paths"))
        if "config" in existing_tables and 'id' not in config_columns:
            conn.execute(text("DROP TABLE IF EXISTS config"))
        # Drop old movie_frames table if it exists (data will be re-processed)
        if "movie_frames" in existing_tables:
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
        conn.execute(text("ALTER TABLE screenshots_new RENAME TO screenshots"))
        conn.execute(text("ALTER TABLE images_new RENAME TO images"))
    
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
        from sqlalchemy.sql import func
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
            # Delete Screenshot records
            db.query(Screenshot).filter(Screenshot.movie_id == movie.id).delete()
            
            # Delete Image records
            db.query(Image).filter(Image.movie_id == movie.id).delete()
            
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

# Database utility functions
def get_movie_id_by_path(db: Session, path: str) -> Optional[int]:
    """Get movie ID from path. Returns None if movie doesn't exist."""
    movie = db.query(Movie).filter(Movie.path == path).first()
    return movie.id if movie else None

def get_indexed_paths_set(db: Session):
    """Get all indexed paths as a set"""
    paths = set()
    for indexed_path in db.query(IndexedPath).all():
        paths.add(indexed_path.path)
    return paths

def get_movie_screenshot_path(db: Session, movie_id: int):
    """Get a screenshot path for a movie from the database"""
    screenshot = db.query(Screenshot).filter(Screenshot.movie_id == movie_id).first()
    if screenshot and os.path.exists(screenshot.shot_path):
        return screenshot.shot_path
    return None

