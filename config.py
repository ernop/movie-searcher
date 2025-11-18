"""
Configuration management for Movie Searcher.
Handles loading and saving configuration from/to database.
"""
import json
import logging
from database import SessionLocal, Config

logger = logging.getLogger(__name__)

def load_config():
    """Load configuration from database"""
    db = SessionLocal()
    try:
        config = {}
        try:
            config_rows = db.query(Config).all()
            for row in config_rows:
                # Parse as JSON - if invalid, log error and skip
                try:
                    config[row.key] = json.loads(row.value)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Invalid JSON in config key '{row.key}': {e}. Skipping.")
                    continue
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
            # Always JSON-encode the value, even if it's a string
            # This ensures consistent storage format and proper parsing on load
            value_str = json.dumps(value)
            existing = db.query(Config).filter(Config.key == key).first()
            if existing:
                existing.value = value_str
            else:
                db.add(Config(key=key, value=value_str))
        db.commit()
    finally:
        db.close()

def get_movies_folder():
    """Get the movies folder path from config only - no defaults, no guessing"""
    config = load_config()
    path = config.get("movies_folder")
    if path:
        return path
    return None

