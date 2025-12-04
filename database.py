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
    Movie, Rating, MovieStatus, SearchHistory, LaunchHistory, 
    IndexedPath, Config, Screenshot, SchemaVersion, MovieAudio,
    Playlist, PlaylistItem, ExternalMovie, Person, MovieCredit,
    MovieList, MovieListItem, Stat,
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
    connect_args={"check_same_thread": False, "timeout": 15}  # Required for SQLite with FastAPI
)

# Enable foreign keys for SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
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
        
        # Populate default playlists if this is a fresh install
        populate_default_playlists()
    else:
        logger.info(f"Database initialized (current schema version: {version})")

def populate_default_playlists():
    """Ensure default system playlists exist"""
    db = SessionLocal()
    try:
        # Check/Create 'Favorites'
        fav = db.query(Playlist).filter(Playlist.name == "Favorites").first()
        if not fav:
            fav = Playlist(name="Favorites", is_system=True)
            db.add(fav)
        
        # Check/Create 'Want to Watch'
        wtw = db.query(Playlist).filter(Playlist.name == "Want to Watch").first()
        if not wtw:
            wtw = Playlist(name="Want to Watch", is_system=True)
            db.add(wtw)
            
        db.commit()
    except Exception as e:
        logger.error(f"Error populating default playlists: {e}")
    finally:
        db.close()

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
    
    # If already at current version, no migration needed - skip all checks
    if current_version == CURRENT_SCHEMA_VERSION:
        return
    
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
    
    # Ensure all tables have required created/updated columns (only during migrations)
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
        
        if current_version < 7:
            logger.info("Migrating to schema version 7: Add movie_status table alongside watch_history")
            with engine.begin() as conn:
                # Create movie_status table if it doesn't exist (add new structure alongside old)
                if "movie_status" not in existing_tables:
                    logger.info("Creating movie_status table (new structure)...")
                    conn.execute(text("""
                        CREATE TABLE movie_status (
                            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                            movie_id INTEGER NOT NULL UNIQUE,
                            movieStatus VARCHAR,
                            created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                            updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                            FOREIGN KEY(movie_id) REFERENCES movies (id) ON DELETE CASCADE
                        )
                    """))
                    conn.execute(text("CREATE INDEX ix_movie_status_movie_id ON movie_status (movie_id)"))
                    
                    # If watch_history exists, migrate data from it to movie_status
                    if "watch_history" in existing_tables:
                        logger.info("Migrating data from watch_history to movie_status (taking most recent status per movie)...")
                        # Check if watch_history has boolean or string status
                        wh_columns = {col['name']: col for col in inspector.get_columns("watch_history")}
                        if "watch_status" in wh_columns:
                            # New schema with boolean
                            conn.execute(text("""
                                INSERT INTO movie_status (movie_id, movieStatus, created, updated)
                                SELECT 
                                    wh.movie_id,
                                    CASE 
                                        WHEN wh.watch_status = 1 THEN 'watched'
                                        WHEN wh.watch_status = 0 THEN 'unwatched'
                                        ELSE NULL
                                    END,
                                    MIN(wh.created) as created,
                                    MAX(wh.updated) as updated
                                FROM watch_history wh
                                INNER JOIN (
                                    SELECT movie_id, MAX(updated) as max_updated
                                    FROM watch_history
                                    GROUP BY movie_id
                                ) latest ON wh.movie_id = latest.movie_id AND wh.updated = latest.max_updated
                                GROUP BY wh.movie_id
                            """))
                        else:
                            # Old schema migration path - this shouldn't happen but handle it
                            conn.execute(text("""
                                INSERT INTO movie_status (movie_id, movieStatus, created, updated)
                                SELECT 
                                    wh.movie_id,
                                    CASE 
                                        WHEN wh.watch_status = 'watched' THEN 'watched'
                                        WHEN wh.watch_status = 'not watched' OR wh.watch_status = 'unwatched' THEN 'unwatched'
                                        ELSE NULL
                                    END,
                                    MIN(wh.created) as created,
                                    MAX(wh.updated) as updated
                                FROM watch_history wh
                                INNER JOIN (
                                    SELECT movie_id, MAX(updated) as max_updated
                                    FROM watch_history
                                    GROUP BY movie_id
                                ) latest ON wh.movie_id = latest.movie_id AND wh.updated = latest.max_updated
                                GROUP BY wh.movie_id
                            """))
                        
                        # VERIFY: Check that migration succeeded
                        migrated_count = conn.execute(text("SELECT COUNT(*) FROM movie_status")).scalar()
                        original_count = conn.execute(text("SELECT COUNT(DISTINCT movie_id) FROM watch_history")).scalar()
                        logger.info(f"Migration verification: {migrated_count} rows in movie_status, {original_count} distinct movies in watch_history")
                        
                        if migrated_count != original_count:
                            logger.warning(f"Migration data mismatch: {migrated_count} != {original_count}. watch_history will be kept for reference.")
                        else:
                            logger.info("Data migration successful. watch_history table is now deprecated but kept for reference.")
                            logger.info("watch_history will be removed in a future migration after confirming movie_status works correctly.")
                    else:
                        logger.info("No watch_history table found. movie_status table created empty.")
                else:
                    # movie_status already exists - this might be from a previous partial migration
                    existing_count = conn.execute(text("SELECT COUNT(*) FROM movie_status")).scalar()
                    logger.info(f"movie_status table already exists with {existing_count} rows. Skipping creation.")
                    
                    # If watch_history exists but movie_status is empty, try to migrate
                    if "watch_history" in existing_tables and existing_count == 0:
                        logger.info("movie_status exists but is empty. Migrating data from watch_history...")
                        wh_columns = {col['name']: col for col in inspector.get_columns("watch_history")}
                        if "watch_status" in wh_columns:
                            conn.execute(text("""
                                INSERT INTO movie_status (movie_id, movieStatus, created, updated)
                                SELECT 
                                    wh.movie_id,
                                    CASE 
                                        WHEN wh.watch_status = 1 THEN 'watched'
                                        WHEN wh.watch_status = 0 THEN 'unwatched'
                                        ELSE NULL
                                    END,
                                    MIN(wh.created) as created,
                                    MAX(wh.updated) as updated
                                FROM watch_history wh
                                INNER JOIN (
                                    SELECT movie_id, MAX(updated) as max_updated
                                    FROM watch_history
                                    GROUP BY movie_id
                                ) latest ON wh.movie_id = latest.movie_id AND wh.updated = latest.max_updated
                                GROUP BY wh.movie_id
                            """))
                        logger.info("Data migration completed.")
            
            set_schema_version(7, "Converted watch_history to movie_status (one-to-one relationship)")
            current_version = 7
        
        if current_version < 8:
            logger.info("Migrating to schema version 8: Convert status boolean to movieStatus enum string")
            with engine.begin() as conn:
                # Check if movie_status table exists and has status column
                if "movie_status" in existing_tables:
                    table_columns = {col['name']: col for col in inspector.get_columns("movie_status")}
                    if "status" in table_columns:
                        # Rename status column to movieStatus and convert boolean to string enum
                        logger.info("Converting status boolean to movieStatus enum string...")
                        # SQLite doesn't support ALTER COLUMN, so we need to recreate the table
                        conn.execute(text("""
                            CREATE TABLE movie_status_new (
                                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                                movie_id INTEGER NOT NULL UNIQUE,
                                movieStatus VARCHAR,
                                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                                FOREIGN KEY(movie_id) REFERENCES movies (id) ON DELETE CASCADE
                            )
                        """))
                        conn.execute(text("CREATE INDEX ix_movie_status_movie_id ON movie_status_new (movie_id)"))
                        
                        # Migrate data: convert boolean to enum string
                        conn.execute(text("""
                            INSERT INTO movie_status_new (movie_id, movieStatus, created, updated)
                            SELECT 
                                movie_id,
                                CASE 
                                    WHEN status = 1 THEN 'watched'
                                    WHEN status = 0 THEN 'unwatched'
                                    ELSE NULL
                                END,
                                created,
                                updated
                            FROM movie_status
                        """))
                        
                        # Drop old table and rename new one
                        conn.execute(text("DROP TABLE movie_status"))
                        conn.execute(text("ALTER TABLE movie_status_new RENAME TO movie_status"))
            
            set_schema_version(8, "Converted status boolean to movieStatus enum string")
            current_version = 8
        
        if current_version < 9:
            logger.info("Migrating to schema version 9: Add image_path column to movies table")
            existing_columns = {col['name']: col for col in inspector.get_columns("movies")}
            if "image_path" not in existing_columns:
                with engine.begin() as conn:
                    logger.info("Adding 'image_path' column to movies table...")
                    conn.execute(text("ALTER TABLE movies ADD COLUMN image_path VARCHAR"))
                    logger.info("Migration complete: added 'image_path' column")
            
            set_schema_version(9, "Added image_path column to movies table")
            current_version = 9
        
        if current_version < 10:
            logger.info("Migrating to schema version 10: Drop images table (replaced by movie.image_path)")
            with engine.begin() as conn:
                logger.info("Dropping 'images' table...")
                conn.execute(text("DROP TABLE IF EXISTS images"))
                logger.info("Migration complete: dropped 'images' table")
            
            set_schema_version(10, "Dropped images table (replaced by movie.image_path)")
            current_version = 10
        
        if current_version < 11:
            logger.info("Migrating to schema version 11: Add hidden column to movies table")
            existing_columns = {col['name']: col for col in inspector.get_columns("movies")}
            if "hidden" not in existing_columns:
                with engine.begin() as conn:
                    logger.info("Adding 'hidden' column to movies table...")
                    # SQLite uses 0/1 for boolean
                    conn.execute(text("ALTER TABLE movies ADD COLUMN hidden BOOLEAN DEFAULT 0 NOT NULL"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_hidden ON movies (hidden)"))
                    logger.info("Migration complete: added 'hidden' column")
            
            set_schema_version(11, "Added hidden column to movies table")
            current_version = 11

        if current_version < 12:
            logger.info("Migrating to schema version 12: Add playlists and offline metadata tables")
            
            # Define table creation SQL
            sql_commands = [
                # Playlists
                """
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    name VARCHAR NOT NULL,
                    is_system BOOLEAN DEFAULT 0 NOT NULL,
                    created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """,
                # Playlist Items
                """
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    playlist_id INTEGER NOT NULL,
                    movie_id INTEGER NOT NULL,
                    "order" INTEGER DEFAULT 0 NOT NULL,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    FOREIGN KEY(playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
                    FOREIGN KEY(movie_id) REFERENCES movies (id) ON DELETE CASCADE
                )
                """,
                "CREATE INDEX IF NOT EXISTS ix_playlist_items_playlist_id ON playlist_items (playlist_id)",
                "CREATE INDEX IF NOT EXISTS ix_playlist_items_movie_id ON playlist_items (movie_id)",
                
                # External Movies (IMDb)
                """
                CREATE TABLE IF NOT EXISTS external_movies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    imdb_id VARCHAR NOT NULL UNIQUE,
                    primary_title VARCHAR NOT NULL,
                    original_title VARCHAR,
                    year INTEGER,
                    runtime_minutes INTEGER,
                    genres VARCHAR,
                    rating FLOAT,
                    votes INTEGER,
                    created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """,
                "CREATE INDEX IF NOT EXISTS ix_external_movies_imdb_id ON external_movies (imdb_id)",
                "CREATE INDEX IF NOT EXISTS ix_external_movies_primary_title ON external_movies (primary_title)",
                "CREATE INDEX IF NOT EXISTS ix_external_movies_year ON external_movies (year)",

                # People
                """
                CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    imdb_id VARCHAR NOT NULL UNIQUE,
                    primary_name VARCHAR NOT NULL,
                    birth_year INTEGER,
                    death_year INTEGER,
                    created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                )
                """,
                "CREATE INDEX IF NOT EXISTS ix_people_imdb_id ON people (imdb_id)",
                "CREATE INDEX IF NOT EXISTS ix_people_primary_name ON people (primary_name)",

                # Movie Credits
                """
                CREATE TABLE IF NOT EXISTS movie_credits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    movie_id INTEGER NOT NULL,
                    person_id INTEGER NOT NULL,
                    category VARCHAR NOT NULL,
                    characters JSON,
                    created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    FOREIGN KEY(movie_id) REFERENCES external_movies (id) ON DELETE CASCADE,
                    FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE CASCADE
                )
                """,
                "CREATE INDEX IF NOT EXISTS ix_movie_credits_movie_id ON movie_credits (movie_id)",
                "CREATE INDEX IF NOT EXISTS ix_movie_credits_person_id ON movie_credits (person_id)"
            ]

            with engine.begin() as conn:
                for cmd in sql_commands:
                    conn.execute(text(cmd))
                
                # Create Default Playlists
                logger.info("Creating default playlists...")
                conn.execute(text("""
                    INSERT OR IGNORE INTO playlists (name, is_system, created, updated) 
                    VALUES ('Favorites', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """))
                conn.execute(text("""
                    INSERT OR IGNORE INTO playlists (name, is_system, created, updated) 
                    VALUES ('Want to Watch', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """))
                
                # Migrate 'want_to_watch' status to playlist
                logger.info("Migrating 'Want to Watch' status to playlist...")
                
                # Get 'Want to Watch' playlist ID
                wtw_id = conn.execute(text("SELECT id FROM playlists WHERE name = 'Want to Watch'")).scalar()
                
                if wtw_id:
                    conn.execute(text(f"""
                        INSERT INTO playlist_items (playlist_id, movie_id, "order", added_at, created, updated)
                        SELECT 
                            {wtw_id},
                            movie_id,
                            0,
                            updated,
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP
                        FROM movie_status
                        WHERE movieStatus = 'want_to_watch'
                        AND movie_id NOT IN (
                            SELECT movie_id FROM playlist_items WHERE playlist_id = {wtw_id}
                        )
                    """))
            
            set_schema_version(12, "Added playlists and offline metadata tables")
            current_version = 12
        
        if current_version < 13:
            # Create movie_lists and movie_list_items tables
            logger.info("Creating movie_lists and movie_list_items tables...")
            with engine.begin() as conn:
                # Create movie_lists table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS movie_lists (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        slug VARCHAR UNIQUE NOT NULL,
                        query VARCHAR NOT NULL,
                        title VARCHAR NOT NULL,
                        provider VARCHAR,
                        comment TEXT,
                        cost_usd FLOAT,
                        is_favorite BOOLEAN NOT NULL DEFAULT 0,
                        is_deleted BOOLEAN NOT NULL DEFAULT 0,
                        movies_count INTEGER NOT NULL DEFAULT 0,
                        in_library_count INTEGER NOT NULL DEFAULT 0,
                        created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_lists_slug ON movie_lists (slug)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_lists_query ON movie_lists (query)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_lists_title ON movie_lists (title)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_lists_is_favorite ON movie_lists (is_favorite)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_lists_is_deleted ON movie_lists (is_deleted)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_lists_created ON movie_lists (created)"))
                
                # Create movie_list_items table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS movie_list_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        movie_list_id INTEGER NOT NULL,
                        movie_id INTEGER,
                        title VARCHAR NOT NULL,
                        year INTEGER,
                        ai_comment TEXT,
                        is_in_library BOOLEAN NOT NULL DEFAULT 0,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        FOREIGN KEY (movie_list_id) REFERENCES movie_lists(id) ON DELETE CASCADE,
                        FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE SET NULL
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_list_items_movie_list_id ON movie_list_items (movie_list_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_list_items_movie_id ON movie_list_items (movie_id)"))
                logger.info("Migration complete: created movie_lists and movie_list_items tables")
            
            set_schema_version(13, "Added movie_lists and movie_list_items tables for AI search results")
            current_version = 13
        
        if current_version < 14:
            logger.info("Migrating to schema version 14: Add stats table for performance tracking")
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS stats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        stat_type VARCHAR NOT NULL,
                        value FLOAT NOT NULL,
                        movie_id INTEGER,
                        extra_data TEXT,
                        created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE SET NULL
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stats_stat_type ON stats (stat_type)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stats_movie_id ON stats (movie_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_stats_created ON stats (created)"))
                logger.info("Migration complete: created stats table")
            
            set_schema_version(14, "Added stats table for performance tracking")
            current_version = 14
        
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
            "movies_new", "ratings_new", "movie_status_new", 
            "search_history_new", "launch_history_new", 
            "indexed_paths_new", "config_new", "screenshots_new", "images_new"
        ]
        indexes_to_drop = [
            "ix_movies_path", "ix_movies_name", "ix_movies_hash",
            "ix_ratings_movie_id", "ix_movie_status_movie_id",
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
                language VARCHAR,
                image_path VARCHAR,
                hidden BOOLEAN DEFAULT 0 NOT NULL,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX ix_movies_path ON movies_new (path)"))
        conn.execute(text("CREATE INDEX ix_movies_name ON movies_new (name)"))
        conn.execute(text("CREATE INDEX ix_movies_hash ON movies_new (hash)"))
        conn.execute(text("CREATE INDEX ix_movies_hidden ON movies_new (hidden)"))
        
        # Migrate movies data
        logger.info("Migrating movies data...")
        # Handle created field - convert from string ISO format to datetime
        # Check if old table has images/screenshots columns
        old_movies_columns = {col['name']: col for col in inspector.get_columns("movies")}
        has_old_images = "images" in old_movies_columns
        has_old_screenshots = "screenshots" in old_movies_columns
        
        # Check for other columns that might exist in source but not in target schema definition above if we were strictly following v3
        # But here we are creating v11 schema, so we should try to copy if they exist, or default
        
        # Construct the SELECT part dynamically based on what exists
        select_cols = ["path", "name", "year", "length", "size", "hash"]
        
        # language
        if "language" in old_movies_columns:
            select_cols.append("language")
        else:
            select_cols.append("NULL as language")
            
        # image_path
        if "image_path" in old_movies_columns:
            select_cols.append("image_path")
        else:
            select_cols.append("NULL as image_path")
            
        # hidden
        if "hidden" in old_movies_columns:
            select_cols.append("hidden")
        else:
            select_cols.append("0 as hidden")

        select_clause = ", ".join(select_cols)

        conn.execute(text(f"""
            INSERT INTO movies_new (path, name, year, length, size, hash, language, image_path, hidden, created, updated)
            SELECT 
                {select_clause},
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
        
        # Create new movie_status table (one-to-one, taking most recent status per movie)
        conn.execute(text("""
            CREATE TABLE movie_status_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                movie_id INTEGER NOT NULL UNIQUE,
                movieStatus VARCHAR,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(movie_id) REFERENCES movies_new (id) ON DELETE CASCADE
            )
        """))
        conn.execute(text("CREATE INDEX ix_movie_status_movie_id ON movie_status_new (movie_id)"))
        
        # Migrate watch_history data - take most recent status per movie
        logger.info("Migrating watch_history data to movie_status (taking most recent status per movie)...")
        # Convert old string status to enum string and take most recent per movie
        conn.execute(text("""
            INSERT INTO movie_status_new (movie_id, movieStatus, created, updated)
            SELECT 
                m.id,
                CASE 
                    WHEN wh.watch_status = 'watched' OR wh.watch_status = 1 THEN 'watched'
                    WHEN wh.watch_status = 'not watched' OR wh.watch_status = 'unwatched' OR wh.watch_status = 0 THEN 'unwatched'
                    ELSE NULL
                END,
                MIN(COALESCE(wh.timestamp, CURRENT_TIMESTAMP)),
                MAX(COALESCE(wh.timestamp, CURRENT_TIMESTAMP))
            FROM watch_history wh
            JOIN movies_new m ON m.path = wh.movie_id
            INNER JOIN (
                SELECT movie_id, MAX(COALESCE(timestamp, '1970-01-01')) as max_timestamp
                FROM watch_history
                GROUP BY movie_id
            ) latest ON wh.movie_id = latest.movie_id AND COALESCE(wh.timestamp, '1970-01-01') = latest.max_timestamp
            GROUP BY m.id
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

        # Create Playlists tables (New in v12 but including in full migration)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                name VARCHAR NOT NULL,
                is_system BOOLEAN DEFAULT 0 NOT NULL,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                playlist_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                "order" INTEGER DEFAULT 0 NOT NULL,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(playlist_id) REFERENCES playlists (id) ON DELETE CASCADE,
                FOREIGN KEY(movie_id) REFERENCES movies_new (id) ON DELETE CASCADE
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_playlist_items_playlist_id ON playlist_items (playlist_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_playlist_items_movie_id ON playlist_items (movie_id)"))
        
        # Initialize default playlists
        conn.execute(text("INSERT OR IGNORE INTO playlists (name, is_system, created, updated) VALUES ('Favorites', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT OR IGNORE INTO playlists (name, is_system, created, updated) VALUES ('Want to Watch', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        
        # Create External Metadata tables
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS external_movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                imdb_id VARCHAR NOT NULL UNIQUE,
                primary_title VARCHAR NOT NULL,
                original_title VARCHAR,
                year INTEGER,
                runtime_minutes INTEGER,
                genres VARCHAR,
                rating FLOAT,
                votes INTEGER,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_external_movies_imdb_id ON external_movies (imdb_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_external_movies_primary_title ON external_movies (primary_title)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_external_movies_year ON external_movies (year)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                imdb_id VARCHAR NOT NULL UNIQUE,
                primary_name VARCHAR NOT NULL,
                birth_year INTEGER,
                death_year INTEGER,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_people_imdb_id ON people (imdb_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_people_primary_name ON people (primary_name)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS movie_credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                movie_id INTEGER NOT NULL,
                person_id INTEGER NOT NULL,
                category VARCHAR NOT NULL,
                characters JSON,
                created DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(movie_id) REFERENCES external_movies (id) ON DELETE CASCADE,
                FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE CASCADE
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_credits_movie_id ON movie_credits (movie_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movie_credits_person_id ON movie_credits (person_id)"))
        
        # Step 3: Drop old tables
        logger.info("Dropping old tables...")
        conn.execute(text("DROP TABLE IF EXISTS ratings"))
        conn.execute(text("DROP TABLE IF EXISTS watch_history"))
        conn.execute(text("DROP TABLE IF EXISTS movie_status"))
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
        conn.execute(text("ALTER TABLE movie_status_new RENAME TO movie_status"))
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
            
            # Delete Rating records
            db.query(Rating).filter(Rating.movie_id == movie.id).delete()
            
            # Delete MovieStatus record
            db.query(MovieStatus).filter(MovieStatus.movie_id == movie.id).delete()
            
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
