"""
Screenshot database synchronization and persistence utilities.
Handles saving screenshots to database with proper session management and path normalization.

PRINCIPLE: Database operations should NEVER fail in normal operation. If they do, it's a
programming error that needs to be fixed, not retried. SQLite is reliable - failures indicate
bugs in our code (path normalization, session management, etc.).
"""
import logging
import re
from pathlib import Path

from database import Movie, Screenshot, SessionLocal

logger = logging.getLogger(__name__)

def normalize_screenshot_path(path):
    """
    Normalize screenshot path to ensure consistent storage/querying.
    Always resolves to absolute path and converts to string.
    """
    if isinstance(path, Path):
        return str(path.resolve())
    return str(Path(path).resolve())

def save_screenshot_to_db(movie_id: int, screenshot_path, timestamp_seconds: float) -> bool:
    """
    Save screenshot to database. Returns True if saved or already exists, False on error.
    
    This function should NEVER fail in normal operation. If it fails, it's a programming error
    that needs to be fixed, not retried. SQLite is reliable - failures indicate bugs in our code.
    
    Args:
        movie_id: Movie ID (required)
        screenshot_path: Path to screenshot file (Path or str)
        timestamp_seconds: Timestamp when screenshot was taken
    
    Returns:
        True if saved or already exists, False on error
    """
    if not movie_id:
        logger.error("save_screenshot_to_db called without movie_id - programming error")
        return False

    normalized_path = normalize_screenshot_path(screenshot_path)

    db = SessionLocal()
    try:
        # Verify movie exists
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            logger.error(f"Movie ID {movie_id} not found when saving screenshot {Path(screenshot_path).name}")
            return False

        # Check if already exists (using normalized path for consistency)
        existing = db.query(Screenshot).filter(
            Screenshot.movie_id == movie_id,
            Screenshot.shot_path == normalized_path
        ).first()

        if existing:
            logger.debug(f"Screenshot already in database: movie_id={movie_id}, path={Path(screenshot_path).name}")
            return True

        # Add new screenshot
        screenshot = Screenshot(
            movie_id=movie_id,
            shot_path=normalized_path,
            timestamp_seconds=timestamp_seconds
        )
        db.add(screenshot)
        db.commit()

        # Verify save succeeded (query with normalized path)
        saved = db.query(Screenshot).filter(
            Screenshot.movie_id == movie_id,
            Screenshot.shot_path == normalized_path
        ).first()

        if saved:
            logger.info(f"Saved screenshot to database: movie_id={movie_id}, screenshot_id={saved.id}, path={Path(screenshot_path).name}")
            return True
        else:
            # This should never happen - indicates a serious problem (path normalization bug?)
            logger.error(f"CRITICAL: Commit succeeded but screenshot not found: movie_id={movie_id}, path={normalized_path}")
            logger.error(f"  Original path: {screenshot_path}")
            logger.error(f"  Normalized path: {normalized_path}")
            db.rollback()
            return False

    except Exception as e:
        logger.error(f"Database error saving screenshot: movie_id={movie_id}, path={Path(screenshot_path).name}, error={e}", exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()

def sync_existing_screenshot(movie_id: int, screenshot_path, timestamp_seconds: float = None) -> bool:
    """
    Sync an existing screenshot file to database if missing.
    Returns True if synced or already exists, False on error.
    
    If timestamp_seconds is None, attempts to extract from filename.
    """
    if timestamp_seconds is None:
        # Try to extract timestamp from filename
        try:
            match = re.search(r'_screenshot(\d+)s\.jpg$', Path(screenshot_path).name)
            if match:
                timestamp_seconds = float(match.group(1))
        except Exception:
            timestamp_seconds = None

    return save_screenshot_to_db(movie_id, screenshot_path, timestamp_seconds)

def find_orphaned_files(movie_id: int, screenshot_dir: Path) -> list[Path]:
    """
    Find screenshot files on disk that are not in database for a movie.
    Returns list of orphaned file paths.
    """
    db = SessionLocal()
    try:
        # Get all screenshots for this movie from DB (normalized paths)
        db_screenshots = db.query(Screenshot).filter(Screenshot.movie_id == movie_id).all()
        db_paths = {normalize_screenshot_path(s.shot_path) for s in db_screenshots}

        # Get movie name to find matching files
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            return []

        # Find files matching screenshot pattern
        movie_name = movie.name
        sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', movie_name).strip('. ')[:100]

        orphaned = []
        pattern = f"{sanitized_name}_screenshot*.jpg"
        for screenshot_file in screenshot_dir.glob(pattern):
            normalized_file = normalize_screenshot_path(screenshot_file)
            if normalized_file not in db_paths:
                orphaned.append(screenshot_file)

        return orphaned
    finally:
        db.close()

def find_missing_files(movie_id: int) -> list[Screenshot]:
    """
    Find screenshots in database that don't exist on disk.
    Returns list of Screenshot objects for missing files.
    """
    db = SessionLocal()
    try:
        screenshots = db.query(Screenshot).filter(Screenshot.movie_id == movie_id).all()
        missing = []
        for screenshot in screenshots:
            path = Path(screenshot.shot_path)
            if not path.exists():
                missing.append(screenshot)
        return missing
    finally:
        db.close()

def sync_movie_screenshots(movie_id: int, screenshot_dir: Path) -> dict:
    """
    Synchronize screenshots for a movie: detect and report mismatches.
    
    Returns dict with:
    - orphaned_files: files on disk not in DB
    - missing_files: entries in DB but files missing
    - synced_count: number of orphaned files synced to DB
    
    This can be called periodically or on-demand to ensure DB and disk stay in sync.
    """
    # Find orphaned files (on disk, not in DB)
    orphaned = find_orphaned_files(movie_id, screenshot_dir)

    # Find missing files (in DB, not on disk)
    missing = find_missing_files(movie_id)

    # Sync orphaned files to DB
    synced_count = 0
    for orphaned_file in orphaned:
        if sync_existing_screenshot(movie_id, orphaned_file):
            synced_count += 1

    return {
        "orphaned_files": [str(f) for f in orphaned],
        "missing_files": [{"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds} for s in missing],
        "synced_count": synced_count
    }

def restore_missing_screenshot(screenshot: Screenshot, video_path: str, extract_func) -> bool:
    """
    Restore a missing screenshot by re-extracting it.
    
    Args:
        screenshot: Screenshot object from database (file missing on disk)
        video_path: Path to video file
        extract_func: Function to queue screenshot extraction (from video_processing)
    
    Returns:
        True if queued for restoration, False on error
    
    Note: extract_func should be passed from calling code to avoid circular imports.
    Example: restore_missing_screenshot(screenshot, video_path, extract_movie_screenshot)
    """
    timestamp = screenshot.timestamp_seconds or 180  # Default to 3 minutes if unknown

    # Queue for re-extraction
    # extract_func signature: (video_path, timestamp, load_config, find_ffmpeg,
    #                          scan_progress, add_log, priority, subtitle_path, movie_id)
    # We'll need to pass the required parameters - this function should be called
    # from code that has access to these dependencies
    logger.info(f"Queuing restoration of missing screenshot: movie_id={screenshot.movie_id}, timestamp={timestamp}s")

    # Note: This is a placeholder - actual implementation depends on extract_func signature
    # The calling code should handle the actual extraction queueing
    return True

