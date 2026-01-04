"""
Configuration management for Movie Searcher.
Handles loading and saving configuration from/to settings.json file.

Architecture note: This project intentionally separates machine config from library data.
- settings.json (this module): Machine-specific settings like folder paths, UI preferences,
  VLC options, API keys. Gitignored so each installation can differ. Users edit via Settings UI.
- SQLite database: Library data—movies, ratings, watch history, playlists, screenshots.
  Portable content that could theoretically move between machines.

This split is clean for a single-user local app. If multi-user support were ever needed,
user preferences would move to the database with user accounts.
"""
import json
import logging
import os
from pathlib import Path

# File locking imports (platform-specific)
if os.name == 'nt':  # Windows
    import msvcrt
else:  # Unix/Linux/Mac
    import fcntl

logger = logging.getLogger(__name__)

# Path to settings.json file (in project root)
SETTINGS_FILE = Path(__file__).parent / "settings.json"

# Track if we've migrated from database yet
_migration_done = False


def _migrate_from_database():
    """One-time migration: copy config from database to settings.json if database has config"""
    global _migration_done
    if _migration_done:
        return

    _migration_done = True

    # Only migrate if settings.json doesn't exist yet
    if SETTINGS_FILE.exists():
        return

    try:
        from database import Config, SessionLocal
        db = SessionLocal()
        try:
            config_rows = db.query(Config).all()
            if not config_rows:
                return  # No config in database, nothing to migrate

            logger.info(f"Migrating {len(config_rows)} config entries from database to settings.json...")

            # Build config dict from database
            config = {}
            for row in config_rows:
                try:
                    config[row.key] = json.loads(row.value)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Invalid JSON in config key '{row.key}': {e}. Skipping.")
                    continue

            # Save to settings.json
            if config:
                _write_config_file(config)
                logger.info("Successfully migrated config to settings.json")
        finally:
            db.close()
    except Exception as e:
        # Database not available or migration failed - that's okay, we'll start fresh
        logger.debug(f"Could not migrate from database: {e}")


def _read_config_file():
    """Read configuration from settings.json file"""
    if not SETTINGS_FILE.exists():
        return {}

    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            # Try to acquire lock (non-blocking read)
            try:
                if os.name == 'nt':  # Windows
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                else:  # Unix
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            except (OSError, AttributeError):
                # Lock failed, but we can still read
                pass

            try:
                content = f.read()
                if not content.strip():
                    return {}
                return json.loads(content)
            finally:
                try:
                    if os.name == 'nt':
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (OSError, AttributeError):
                    pass
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in settings.json: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error reading settings.json: {e}")
        return {}


def _write_config_file(config):
    """Write configuration to settings.json file with file locking"""
    # Ensure directory exists
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Write atomically: write to temp file, then rename
    temp_file = SETTINGS_FILE.with_suffix('.json.tmp')

    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            # Try to acquire exclusive lock
            try:
                if os.name == 'nt':  # Windows
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                else:  # Unix
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except (OSError, AttributeError):
                # Lock failed, but we can still write
                pass

            try:
                json.dump(config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())  # Ensure data is written to disk
            finally:
                try:
                    if os.name == 'nt':
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (OSError, AttributeError):
                    pass

        # Atomic rename
        temp_file.replace(SETTINGS_FILE)
    except Exception as e:
        logger.error(f"Error writing settings.json: {e}")
        # Clean up temp file if it exists
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        raise


def load_config():
    """Load configuration from settings.json file"""
    # Migrate from database if needed (one-time)
    _migrate_from_database()

    return _read_config_file()


def save_config(config):
    """Save configuration to settings.json file"""
    # Merge with existing config (don't overwrite keys not in the update)
    existing_config = _read_config_file()
    existing_config.update(config)

    _write_config_file(existing_config)


def get_movies_folder():
    """Get the movies folder path from config only - no defaults, no guessing"""
    config = load_config()
    path = config.get("movies_folder")
    if path:
        return path
    return None


def get_local_target_folder():
    """Get the local target folder path from config only - no defaults, no guessing"""
    config = load_config()
    path = config.get("local_target_folder")
    if path:
        return path
    return None
