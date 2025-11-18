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
import sys
# Ensure UTF-8 output for console to avoid UnicodeEncodeError on Windows consoles
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    # Safe to ignore; file handler below still uses UTF-8
    pass

# Log file in root directory
LOG_FILE = Path(__file__).parent / "movie_searcher.log"

# Custom filter to suppress verbose video_processing logs from console
class ConsoleLogFilter(logging.Filter):
    """Filter to suppress verbose logs from video_processing module on console"""
    def filter(self, record):
        # Allow all logs that are WARNING or above
        if record.levelno >= logging.WARNING:
            return True
        # Suppress INFO/DEBUG logs from video_processing module
        if record.name.startswith('video_processing'):
            return False
        # Allow all other logs
        return True

# Global shutdown flag for filter (set during shutdown)
_app_shutting_down = False

# Custom filter to suppress asyncio shutdown errors (known Windows issue)
class SuppressShutdownErrorsFilter(logging.Filter):
    """Filter to suppress known harmless errors during shutdown"""
    def filter(self, record):
        # Suppress AssertionError from asyncio during shutdown (Windows ProactorEventLoop issue)
        if record.levelno == logging.ERROR:
            if 'asyncio' in record.name:
                msg = record.getMessage() if hasattr(record, 'getMessage') else str(record.msg)
                if 'AssertionError' in msg or '_attach' in msg:
                    return False
            # Suppress ffmpeg errors during shutdown (processes being killed is expected)
            if 'video_processing' in record.name:
                msg = record.getMessage() if hasattr(record, 'getMessage') else str(record.msg)
                if '_ffmpeg_job failed' in msg and _app_shutting_down:
                    return False
        return True

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)  # Capture everything

# File handler: logs everything (DEBUG and above) with shutdown error suppression
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
file_handler.addFilter(SuppressShutdownErrorsFilter())
root_logger.addHandler(file_handler)

# Console handler: only logs INFO and above, with filtering for video_processing
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.addFilter(ConsoleLogFilter())
console_handler.addFilter(SuppressShutdownErrorsFilter())
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

# Database setup - import from database module
from database import (
    Base, SessionLocal, get_db,
    Movie, Rating, MovieStatus, SearchHistory, LaunchHistory, IndexedPath, Config, Screenshot, Image, SchemaVersion,
    init_db, migrate_db_schema, remove_sample_files,
    get_movie_id_by_path, get_indexed_paths_set, get_movie_screenshot_path
)
from models import MovieStatusEnum
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import func

# Import video processing and subprocess management
from video_processing import (
    initialize_video_processing,
    shutdown_flag, kill_all_active_subprocesses,
    run_interruptible_subprocess,
    get_video_length as get_video_length_vp, validate_ffmpeg_path, find_ffmpeg as find_ffmpeg_core,
    generate_screenshot_filename,
    extract_screenshots as extract_screenshots_core,
    frame_extraction_queue, process_frame_queue as process_frame_queue_core,
    SCREENSHOT_DIR
)

# Import VLC integration
from vlc_integration import (
    launch_movie_in_vlc, get_currently_playing_movies,
    has_been_launched, find_subtitle_file
)

# Import scanning module
from scanning import (
    scan_progress, run_scan_async, scan_directory, index_movie,
    add_scan_log, is_sample_file, get_file_hash, find_images_in_folder,
    clean_movie_name, filter_yts_images,
    load_cleaning_patterns, extract_screenshots, extract_movie_screenshot,
    process_frame_queue, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS,
    set_callbacks as set_scanning_callbacks
)

# FastAPI app will be created after lifespan function is defined
# (temporary placeholder - will be replaced)
app = None

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()

# Prevent duplicate scan starts (race between concurrent requests)
scan_start_lock = threading.Lock()

# Initialize video processing will be run during app startup (lifespan)

SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.vtt', '.ass', '.ssa'}

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

def get_image_url_path(image_path):
    """Convert absolute image path to relative URL path for static serving"""
    if not image_path:
        return None
    
    # Check if it's a screenshot (in screenshots directory)
    from video_processing import SCREENSHOT_DIR
    if SCREENSHOT_DIR:
        try:
            image_path_obj = Path(image_path).resolve()
            screenshot_dir_obj = Path(SCREENSHOT_DIR).resolve()
            try:
                relative_path = image_path_obj.relative_to(screenshot_dir_obj)
                # Screenshots are served via /screenshots/ - return None to use screenshot path directly
                return None  # Screenshots handled separately via filename extraction
            except ValueError:
                pass  # Not in screenshots directory
        except Exception:
            pass
    
    # Check if it's in movies folder
    movies_folder = get_movies_folder()
    if not movies_folder:
        return None
    try:
        # Convert to Path objects and resolve
        image_path_obj = Path(image_path).resolve()
        movies_folder_obj = Path(movies_folder).resolve()
        
        # Check if image is within movies folder
        try:
            relative_path = image_path_obj.relative_to(movies_folder_obj)
            # Convert to forward slashes for URL
            return str(relative_path).replace('\\', '/')
        except ValueError:
            # Image is not within movies folder
            return None
    except Exception:
        return None

# Auto-detect and save ffmpeg if not configured (called during startup)
def auto_detect_ffmpeg():
    """Auto-detect ffmpeg and save to config if found"""
    config = load_config()
    
    # If already configured and valid, skip
    if config.get("ffmpeg_path"):
        is_valid, _ = validate_ffmpeg_path(config["ffmpeg_path"])
        if is_valid:
            return
    
    # Try to find ffmpeg in PATH
    import shutil
    ffmpeg_exe = shutil.which("ffmpeg")
    if ffmpeg_exe:
        is_valid, _ = validate_ffmpeg_path(ffmpeg_exe)
        if is_valid:
            config["ffmpeg_path"] = ffmpeg_exe
            save_config(config)
            logger.info(f"Auto-detected and saved ffmpeg: {ffmpeg_exe}")
            return
    
    # Try common Windows locations
    if os.name == 'nt':
        common_paths = [
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/Program Files (x86)/ffmpeg/bin/ffmpeg.exe"),
        ]
        for ffmpeg_path in common_paths:
            if ffmpeg_path.exists():
                is_valid, _ = validate_ffmpeg_path(str(ffmpeg_path))
                if is_valid:
                    config["ffmpeg_path"] = str(ffmpeg_path)
                    save_config(config)
                    logger.info(f"Auto-detected and saved ffmpeg: {ffmpeg_path}")
                    return

# Define lifespan function after all dependencies are available
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    """Lifespan context manager for startup and shutdown"""
    # Startup
    logger.info("Startup: initializing database...")
    # Note: init_db creates missing tables but doesn't modify existing ones.
    # migrate_db_schema handles one-time migration from old schema.
    init_db()
    migrate_db_schema()
    logger.info("Startup: database ready.")

    # Initialize video processing and ffmpeg after DB is ready
    logger.info("Startup: initializing video processing...")
    initialize_video_processing(SCRIPT_DIR)
    auto_detect_ffmpeg()
    logger.info("Startup: video processing ready.")
    
    # Mount static files directory (favicon, etc.)
    static_dir = SCRIPT_DIR / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=False), name="static")
        logger.info(f"Startup: mounted static directory at /")
    
    # Mount screenshots directory as static files (after SCREENSHOT_DIR is initialized)
    from video_processing import SCREENSHOT_DIR
    if SCREENSHOT_DIR and SCREENSHOT_DIR.exists():
        app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOT_DIR)), name="screenshots")
        logger.info(f"Startup: mounted screenshots directory at /screenshots")
    
    # Mount movies folder as static files for image serving
    movies_folder = get_movies_folder()
    if movies_folder and os.path.exists(movies_folder):
        try:
            app.mount("/movies", StaticFiles(directory=movies_folder), name="movies")
            logger.info(f"Startup: mounted movies directory at /movies")
        except Exception as e:
            logger.warning(f"Failed to mount movies directory: {e}")

    removed_count = remove_sample_files()
    if removed_count > 0:
        print(f"Removed {removed_count} sample file(s) from database")
    
    # Set up callbacks for scanning module to avoid circular imports
    set_scanning_callbacks(load_config, get_movies_folder)
    
    yield
    
    # Shutdown
    logger.info("Shutdown event triggered, cleaning up...")
    global _app_shutting_down
    _app_shutting_down = True
    shutdown_flag.set()
    kill_all_active_subprocesses()

# Create FastAPI app with lifespan
app = FastAPI(title="Movie Searcher", lifespan=lifespan)


class MovieInfo(BaseModel):
    path: str
    name: str
    length: Optional[float] = None
    created: Optional[str] = None
    size: Optional[int] = None

class SearchRequest(BaseModel):
    query: str

class LaunchRequest(BaseModel):
    movie_id: int
    subtitle_path: Optional[str] = None
    close_existing_vlc: bool = True
    start_time: Optional[float] = None  # Start time in seconds

class ChangeStatusRequest(BaseModel):
    movie_id: int
    movieStatus: Optional[str] = None  # None = unset, "watched", "unwatched", "want_to_watch"

class RatingRequest(BaseModel):
    movie_id: int
    rating: int  # 1-5 only

class ConfigRequest(BaseModel):
    movies_folder: Optional[str] = None
    settings: Optional[dict] = None

class CleanNameTestRequest(BaseModel):
    text: str

class ScreenshotsIntervalRequest(BaseModel):
    movie_id: int
    every_minutes: float = 3
    subtitle_path: Optional[str] = None  # Path to subtitle file to burn in (if any)

@app.post("/api/frames/start")
async def start_frame_worker():
    """Force-start the background screenshot extraction worker."""
    try:
        process_frame_queue()
        return {
            "status": "started",
            "frame_queue_size": frame_extraction_queue.qsize()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/frames/status")
async def get_frame_worker_status():
    """Report frame extraction queue status."""
    try:
        from video_processing import (
            frame_processing_active,
            screenshot_completion_times, screenshot_completion_lock
        )
        import time
        
        # Count screenshots processed in the last minute
        one_minute_ago = time.time() - 60
        recent_count = 0
        with screenshot_completion_lock:
            recent_count = sum(1 for ts in screenshot_completion_times if ts >= one_minute_ago)
        
        return {
            "is_running": frame_processing_active,
            "queue_size": frame_extraction_queue.qsize(),
            "processed_last_minute": recent_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_video_length(file_path):
    """Extract video length using ffprobe from configured ffmpeg bundle"""
    return get_video_length_vp(file_path)

def find_ffmpeg():
    """Find ffmpeg executable - requires configured path, no fallbacks"""
    return find_ffmpeg_core(load_config)

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
            
            # Use normalized year from DB
            year = movie.year
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

@app.get("/", response_class=HTMLResponse)
async def read_root():
    html_path = SCRIPT_DIR / "index.html"
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

# Serve SPA for clean movie detail URLs
@app.get("/movie/{movie_id}", response_class=HTMLResponse)
@app.get("/movie/{movie_id}/{slug}", response_class=HTMLResponse)
async def serve_movie_detail_spa(movie_id: int, slug: str = ""):
    html_path = SCRIPT_DIR / "index.html"
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()



@app.post("/api/index")
async def index_movies(root_path: str = Query(None)):
    """One-time deep index scan (runs in background)"""
    global scan_progress, scan_start_lock
    
    logger.info(f"index_movies called with root_path: {root_path}")
    
    if not root_path:
        root_path = get_movies_folder()
        logger.info(f"Got root_path from get_movies_folder: {root_path}")
    
    if not root_path:
        error_msg = "Movies folder not configured. Please use 'Change Movies Folder' in settings to select a folder."
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    
    path_obj = Path(root_path)
    logger.info(f"Checking path: {root_path}")
    
    if not path_obj.exists() and not os.path.exists(root_path):
        error_msg = f"Path not found: {root_path}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    
    logger.info(f"Starting scan of: {root_path}")
    
    # Run scan in background with a lock to prevent duplicates
    with scan_start_lock:
        if scan_progress["is_scanning"]:
            raise HTTPException(status_code=400, detail="Scan already in progress")
        # Mark as scanning before launching the thread to close the race window
        scan_progress["is_scanning"] = True
        scan_progress["status"] = "starting"
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
    global scan_progress, scan_start_lock
    
    logger.info(f"admin_reindex called with root_path: {root_path}")
    
    if not root_path:
        root_path = get_movies_folder()
        logger.info(f"Got root_path from get_movies_folder: {root_path}")
    
    if not root_path:
        error_msg = "Movies folder not configured. Please use 'Change Movies Folder' in settings to select a folder."
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    
    path_obj = Path(root_path)
    logger.info(f"Checking path: {root_path}")
    
    if not path_obj.exists() and not os.path.exists(root_path):
        error_msg = f"Path not found: {root_path}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    
    logger.info(f"Starting scan of: {root_path}")
    
    # Run scan in background (same as frontend) with race-free start
    with scan_start_lock:
        if scan_progress["is_scanning"]:
            raise HTTPException(status_code=400, detail="Scan already in progress")
        scan_progress["is_scanning"] = True
        scan_progress["status"] = "starting"
        import threading
        thread = threading.Thread(target=run_scan_async, args=(root_path,))
        thread.daemon = True
        thread.start()
    
    return {"status": "started", "message": "Reindex started in background"}

@app.get("/api/search")
async def search_movies(
    q: str,
    filter_type: str = Query("all", pattern="^(all|watched|unwatched)$"),
    language: Optional[str] = Query("all"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200)
):
    """Search movies with pagination. Returns total for infinite scrolling."""
    if not q or len(q) < 2:
        return {"results": [], "total": 0}
    
    db = SessionLocal()
    try:
        query_lower = q.lower()

        from sqlalchemy import or_, and_, case
        # Build base query
        movie_query = db.query(Movie).filter(
            func.lower(Movie.name).contains(query_lower),
            or_(Movie.length == None, Movie.length >= 60)
        )

        # Get watched movie IDs efficiently
        watched_movie_ids = set()
        if filter_type in ("watched", "unwatched", "all"):
            watched_entries = db.query(MovieStatus.movie_id).filter(
                MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value
            ).all()
            watched_movie_ids = {row[0] for row in watched_entries}

        # Apply watched/unwatched filter
        if filter_type == "watched":
            if not watched_movie_ids:
                # No watched movies, return empty
                return {"results": [], "total": 0}
            movie_query = movie_query.filter(Movie.id.in_(watched_movie_ids))
        elif filter_type == "unwatched":
            if watched_movie_ids:
                movie_query = movie_query.filter(~Movie.id.in_(watched_movie_ids))
        
        # Apply audio language filter using movie_audio table
        if language and language != "all":
            from models import MovieAudio
            # Filter to movies that have at least one audio track matching the selected language
            movie_query = movie_query.filter(
                Movie.id.in_(
                    db.query(MovieAudio.movie_id).filter(
                        func.lower(func.trim(MovieAudio.audio_type)) == func.lower(func.trim(language))
                    ).subquery()
                )
            )

        # Load all matching movies for scoring and total count
        movies = movie_query.all()
        total_count = len(movies)
        
        # Score and sort: prioritize names starting with query
        scored_movies = []
        for movie in movies:
            name_lower = movie.name.lower()
            score = 100 if name_lower.startswith(query_lower) else 50
            scored_movies.append((score, movie))
        
        # Sort by score (desc), then name (asc)
        scored_movies.sort(key=lambda x: (-x[0], x[1].name.lower()))
        # Apply pagination window
        if offset >= len(scored_movies):
            page_slice = []
        else:
            page_slice = scored_movies[offset:offset + limit]
        
        # Extract result IDs for batch loading
        result_ids = [movie.id for _, movie in page_slice]
        results = []

        # Preload watch status info (latest watch_status for each movie)
        watch_status_dict = {}
        if result_ids:
            # Get movie status for each movie (one-to-one relationship)
            movie_statuses = db.query(MovieStatus).filter(
                MovieStatus.movie_id.in_(result_ids)
            ).all()
            for movie_status in movie_statuses:
                watch_status_dict[movie_status.movie_id] = {
                    "watch_status": movie_status.movieStatus,
                    "watched_date": movie_status.updated.isoformat() if movie_status.updated else None,
                }


        # Build results
        for score, movie in page_slice:
            watch_info = watch_status_dict.get(movie.id, {})
            watch_status = watch_info.get("watch_status")
            is_watched = watch_status == MovieStatusEnum.WATCHED.value  # For backward compatibility
            
            image_objs = db.query(Image).filter(Image.movie_id == movie.id).all()
            screenshot_objs = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()
            
            # Return IDs and paths
            images = [{"id": img.id, "path": img.image_path, "url_path": get_image_url_path(img.image_path)} for img in image_objs if "www.YTS.AM" not in img.image_path]
            screenshots = [{"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds} for s in screenshot_objs]
            
            # For get_largest_image (needs paths)
            image_paths = [img.image_path for img in image_objs if "www.YTS.AM" not in img.image_path]
            screenshot_paths = [s.shot_path for s in screenshot_objs]
            screenshot_obj = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).first()
            screenshot_path = screenshot_obj.shot_path if screenshot_obj else None
            info = {
                "images": image_paths,
                "screenshots": screenshot_paths,
                "frame": screenshot_path
            }
            largest_image_path = get_largest_image(info)
            largest_image_id = None
            if largest_image_path:
                for img in image_objs:
                    if img.image_path == largest_image_path and "www.YTS.AM" not in img.image_path:
                        largest_image_id = img.id
                        break
                if not largest_image_id:
                    for s in screenshot_objs:
                        if s.shot_path == largest_image_path:
                            largest_image_id = s.id
                            break
            
            has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
            
            # Get rating
            rating_entry = db.query(Rating).filter(Rating.movie_id == movie.id).first()
            rating = int(rating_entry.rating) if rating_entry else None

            results.append({
                "id": movie.id,
                "path": movie.path,
                "name": movie.name,
                "length": movie.length,
                "created": movie.created,
                "size": movie.size,
                "watch_status": watch_status,
                "watched": is_watched,  # Keep for backward compatibility
                "watched_date": watch_info.get("watched_date"),
                "score": score,
                "images": images,
                "screenshots": screenshots,
                "screenshot_id": screenshot_obj.id if screenshot_obj else None,
                "screenshot_path": screenshot_path,
                "image_id": largest_image_id,
                "image_path": largest_image_path,
                "image_path_url": get_image_url_path(largest_image_path) if largest_image_path else None,
                "year": movie.year,
                "has_launched": has_launched,
                "rating": rating
            })

        # Save to history (count what we actually return)
        # Only add if the last entry is different (prevent duplicate consecutive entries)
        last_search = db.query(SearchHistory).order_by(SearchHistory.created.desc()).first()
        if not last_search or last_search.query != q:
            search_entry = SearchHistory(
                query=q,
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

        return {"results": results, "total": total_count, "offset": offset, "limit": limit}
    finally:
        db.close()

@app.get("/api/movie/{movie_id}")
async def get_movie_details_by_id(movie_id: int):
    """Get detailed information about a specific movie by its database ID"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        # Get watch status (None or enum value)
        movie_status = db.query(MovieStatus).filter(MovieStatus.movie_id == movie.id).first()
        watch_status = movie_status.movieStatus if movie_status else None

        # Get images and screenshots from tables
        image_objs = db.query(Image).filter(Image.movie_id == movie.id).all()
        screenshot_objs = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).order_by(Screenshot.timestamp_seconds.asc().nullslast()).all()
        
        # Filter out YTS images and return IDs and paths (convert absolute paths to relative URL paths)
        images = [{"id": img.id, "path": img.image_path, "url_path": get_image_url_path(img.image_path)} for img in image_objs if "www.YTS.AM" not in img.image_path]
        screenshots = [{"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds} for s in screenshot_objs]

        # Get screenshot ID and path (for frame)
        screenshot_obj = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).first()
        screenshot_id = screenshot_obj.id if screenshot_obj else None
        screenshot_path = screenshot_obj.shot_path if screenshot_obj else None

        # Build info dict for get_largest_image (needs paths to check file sizes)
        image_paths = [img.image_path for img in image_objs if "www.YTS.AM" not in img.image_path]
        screenshot_paths = [s.shot_path for s in screenshot_objs]
        info = {
            "images": image_paths,
            "screenshots": screenshot_paths,
            "frame": screenshot_path
        }

        # Get largest image ID and path
        largest_image_path = get_largest_image(info)
        largest_image_id = None
        if largest_image_path:
            # Find the ID for the largest image path
            for img in image_objs:
                if img.image_path == largest_image_path and "www.YTS.AM" not in img.image_path:
                    largest_image_id = img.id
                    break
            if not largest_image_id:
                # Check screenshots
                for s in screenshot_objs:
                    if s.shot_path == largest_image_path:
                        largest_image_id = s.id
                        break

        year = movie.year
        has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
        
        # Get rating
        rating_entry = db.query(Rating).filter(Rating.movie_id == movie.id).first()
        rating = int(rating_entry.rating) if rating_entry and rating_entry.rating is not None else None
        
        return {
            "id": movie.id,
            "path": movie.path,
            "name": movie.name,
            "length": movie.length,
            "created": movie.created,
            "size": movie.size,
            "watch_status": watch_status,
            "watched": watch_status == MovieStatusEnum.WATCHED.value,  # Keep for backward compatibility
            "watched_date": movie_status.updated.isoformat() if movie_status and movie_status.updated else None,
            "images": images,
            "screenshots": screenshots,
            "screenshot_id": screenshot_id,
            "screenshot_path": screenshot_path,
            "image_id": largest_image_id,
            "image_path": largest_image_path,
            "image_path_url": get_image_url_path(largest_image_path) if largest_image_path else None,
            "year": year,
            "has_launched": has_launched,
            "rating": rating
        }
    finally:
        db.close()

@app.get("/api/movie/{movie_id}/screenshots")
async def get_movie_screenshots(movie_id: int):
    """Get screenshots for a movie (lightweight endpoint for polling)"""
    db = SessionLocal()
    try:
        screenshots = db.query(Screenshot).filter(Screenshot.movie_id == movie_id).order_by(Screenshot.timestamp_seconds.asc().nullslast()).all()
        return {
            "screenshots": [{"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds} for s in screenshots]
        }
    finally:
        db.close()

# REMOVED: Insecure path-based image endpoint
# Use /api/image/{image_id} instead - serves images by database ID (secure)

@app.post("/api/movie/screenshots/interval")
async def create_interval_screenshots(request: ScreenshotsIntervalRequest):
    """Queue screenshots every N minutes across the movie duration (default 3 minutes)."""
    logger.info(f"POST /api/movie/screenshots/interval - Request data: movie_id={request.movie_id}, every_minutes={request.every_minutes}, subtitle_path={request.subtitle_path}")
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {request.movie_id}")

        # Determine duration
        length_seconds = movie.length if movie.length else get_video_length(movie.path)
        if not length_seconds or length_seconds <= 0:
            raise HTTPException(status_code=400, detail="Unable to determine video length")

        # Compute timestamps (0 to end, step every_minutes)
        step_seconds = max(0.1, request.every_minutes * 60)
        timestamps = []
        current = step_seconds
        while current < length_seconds:
            timestamps.append(int(current))
            current += step_seconds
        if len(timestamps) == 0:
            # If no timestamps after filtering, use first step instead of 0
            timestamps = [int(step_seconds)] if step_seconds < length_seconds else []

        # FIRST: Delete orphaned screenshot files (files on disk not in database) BEFORE deleting from DB
        # This prevents race conditions where orphaned files get synced back to DB
        orphaned_files = []
        orphaned_deleted = 0
        try:
            from video_processing import generate_screenshot_filename, SCREENSHOT_DIR
            video_path_obj = Path(movie.path)
            movie_name = video_path_obj.stem
            sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', movie_name).strip('. ')[:100]
            
            # Get all screenshot paths currently in database for this movie
            existing_db_paths = {s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()}
            
            # Check for screenshots with and without subtitle suffix
            for suffix in ["", "_subs"]:
                pattern = f"{sanitized_name}_screenshot*s{suffix}.jpg"
                for screenshot_file in SCREENSHOT_DIR.glob(pattern):
                    file_path_str = str(screenshot_file)
                    # If file exists but NOT in database, it's orphaned - delete it
                    if screenshot_file.exists() and file_path_str not in existing_db_paths:
                        orphaned_files.append(file_path_str)
                        try:
                            os.remove(screenshot_file)
                            orphaned_deleted += 1
                            logger.info(f"Deleted orphaned screenshot file: {screenshot_file.name}")
                        except Exception as del_err:
                            logger.warning(f"Failed to delete orphaned screenshot file {screenshot_file.name}: {del_err}")
        except Exception as e:
            logger.warning(f"Error checking/deleting orphaned screenshot files: {e}", exc_info=True)
        
        # SECOND: Delete all existing screenshots for this movie from database and disk
        existing_screenshots = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()
        deleted_count = 0
        for screenshot in existing_screenshots:
            # Delete file from disk if it exists
            if screenshot.shot_path and os.path.exists(screenshot.shot_path):
                try:
                    os.remove(screenshot.shot_path)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete screenshot file {screenshot.shot_path}: {e}")
            # Delete from database
            db.delete(screenshot)
        db.commit()
        
        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} existing screenshots from database and disk for movie_id={request.movie_id} before generating new set")
        
        if orphaned_deleted > 0:
            logger.info(f"Deleted {orphaned_deleted} orphaned screenshot file(s) on disk not in database for movie_id={request.movie_id}")
        
        if orphaned_files and orphaned_deleted == 0:
            logger.warning(f"Found {len(orphaned_files)} orphaned screenshot file(s) on disk but failed to delete them. Files: {orphaned_files}")
        
        # Use provided subtitle_path if any
        subtitle_path = request.subtitle_path
        if subtitle_path and not os.path.exists(subtitle_path):
            logger.warning(f"subtitle_path provided but file not found: {subtitle_path}")
            subtitle_path = None
        
        # Queue extractions (async by default)
        queued = 0
        skipped_existing = 0
        errors = 0
        logger.info(f"Generating screenshots for movie_id={request.movie_id}, path={movie.path}, length={length_seconds}s, timestamps={len(timestamps)} timestamps, subtitle_path={subtitle_path}")
        logger.info(f"Timestamp range: {timestamps[0] if timestamps else 'none'}s to {timestamps[-1] if timestamps else 'none'}s")
        
        for ts in timestamps:
            try:
                # User-triggered work gets higher priority over backlog
                # Pass movie_id to avoid path lookup issues in database save
                result = extract_movie_screenshot(movie.path, timestamp_seconds=ts, priority="user_high", subtitle_path=subtitle_path, movie_id=movie.id)
                if result is None:
                    # None means it was queued successfully
                    queued += 1
                    if queued <= 5 or queued % 10 == 0:  # Log first 5 and every 10th
                        logger.info(f"Queued screenshot at {ts}s (total queued: {queued})")
                elif isinstance(result, str):
                    # String means screenshot already exists
                    skipped_existing += 1
                    logger.info(f"Screenshot already exists at {ts}s: {result}")
                else:
                    errors += 1
                    logger.warning(f"Unexpected return value from extract_movie_screenshot at {ts}s: {result}")
            except Exception as e:
                errors += 1
                logger.error(f"Failed to queue screenshot at {ts}s for movie_id={request.movie_id}, path={movie.path}: {e}", exc_info=True)
                continue
        
        logger.info(f"Screenshot queuing complete: queued={queued}, skipped_existing={skipped_existing}, errors={errors}")
        
        if skipped_existing > 0:
            logger.warning(f"WARNING: {skipped_existing} screenshots were skipped because files already exist. This may indicate orphaned files weren't properly deleted.")
        
        # Check queue size before starting worker
        from video_processing import frame_extraction_queue
        queue_size_before = frame_extraction_queue.qsize()
        logger.info(f"Queue size before starting worker: {queue_size_before}")
        
        if queue_size_before == 0 and queued == 0 and skipped_existing > 0:
            logger.warning(f"WARNING: No screenshots queued but {skipped_existing} were skipped. This suggests orphaned files exist that should have been deleted.")
        
        # Ensure background worker is running to process the queue now
        try:
            process_frame_queue(max_workers=3)
            queue_size_after = frame_extraction_queue.qsize()
            logger.info(f"Queue size after starting worker: {queue_size_after}")
        except Exception as e:
            logger.error(f"Failed to start frame queue processor: {e}", exc_info=True)

        # Return current known screenshots (new ones will appear as the worker processes them)
        current_shots = [{"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds} for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).order_by(Screenshot.timestamp_seconds.asc().nullslast()).all()]
        response = {
            "status": "queued",
            "queued": queued,
            "every_minutes": request.every_minutes,
            "timestamps": timestamps,
            "screenshots": current_shots
        }
        if orphaned_files:
            response["orphaned_files"] = orphaned_files
            response["warning"] = f"Found {len(orphaned_files)} orphaned screenshot file(s) on disk not in database. These may prevent new screenshots from being generated. Please manually delete them if needed."
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queuing interval screenshots: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/image/{image_id}")
async def get_image_by_id(image_id: int):
    """Serve an image file by its database ID"""
    from fastapi.responses import FileResponse
    db = SessionLocal()
    try:
        img = db.query(Image).filter(Image.id == image_id).first()
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        path_obj = Path(img.image_path)
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail="Image file missing")
        return FileResponse(str(path_obj))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving image id={image_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/screenshot/{screenshot_id}")
async def get_screenshot_by_id(screenshot_id: int):
    """Serve a screenshot image by its database ID"""
    from fastapi.responses import FileResponse
    db = SessionLocal()
    try:
        shot = db.query(Screenshot).filter(Screenshot.id == screenshot_id).first()
        if not shot:
            raise HTTPException(status_code=404, detail="Screenshot not found")
        path_obj = Path(shot.shot_path)
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail="Screenshot file missing")
        return FileResponse(str(path_obj))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving screenshot id={screenshot_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/launch")
async def launch_movie(request: LaunchRequest):
    """Launch movie in VLC with optional subtitle"""
    logger.info(f"POST /api/launch - Request data: movie_id={request.movie_id}, subtitle_path={request.subtitle_path}, close_existing_vlc={request.close_existing_vlc}, start_time={request.start_time}")
    # Validate movie exists in index before launching
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {request.movie_id}")
    finally:
        db.close()
    
    # Delegate to VLC integration module
    try:
        result = launch_movie_in_vlc(
            movie_path=movie.path,
            subtitle_path=request.subtitle_path,
            close_existing=request.close_existing_vlc,
            start_time=request.start_time
        )
        return result
    except HTTPException:
        raise
    except FileNotFoundError as e:
        error_msg = str(e)
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "detail": error_msg,
                "steps": [],
                "results": []
            }
        )
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "detail": error_msg,
                "steps": [],
                "results": []
            }
        )

@app.get("/api/history")
async def get_history():
    """Get search and launch history"""
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

@app.get("/api/launch-history")
async def get_launch_history():
    """Get launch history with movie information"""
    db = SessionLocal()
    try:
        # Single query with JOINs to get all data at once
        results = db.query(
            LaunchHistory,
            Movie,
            MovieStatus,
            Screenshot
        ).join(
            Movie, LaunchHistory.movie_id == Movie.id
        ).outerjoin(
            MovieStatus, Movie.id == MovieStatus.movie_id
        ).outerjoin(
            Screenshot, Movie.id == Screenshot.movie_id
        ).order_by(
            LaunchHistory.created.desc()
        ).limit(100).all()
        
        launches_with_info = []
        for launch, movie, movie_status, screenshot in results:
            if not movie:
                continue
            
            # Get images and screenshots from tables
            image_objs = db.query(Image).filter(Image.movie_id == movie.id).all()
            screenshot_objs = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()
            
            # Return IDs and paths
            images = [{"id": img.id, "path": img.image_path, "url_path": get_image_url_path(img.image_path)} for img in image_objs if "www.YTS.AM" not in img.image_path]
            screenshots = [{"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds} for s in screenshot_objs]
            
            # For get_largest_image (needs paths)
            image_paths = [img.image_path for img in image_objs if "www.YTS.AM" not in img.image_path]
            screenshot_paths = [s.shot_path for s in screenshot_objs]
            screenshot_path = screenshot.shot_path if screenshot and os.path.exists(screenshot.shot_path) else None
            info = {
                "images": image_paths,
                "screenshots": screenshot_paths,
                "frame": screenshot_path
            }
            largest_image_path = get_largest_image(info)
            largest_image_id = None
            if largest_image_path:
                for img in image_objs:
                    if img.image_path == largest_image_path and "www.YTS.AM" not in img.image_path:
                        largest_image_id = img.id
                        break
                if not largest_image_id:
                    for s in screenshot_objs:
                        if s.shot_path == largest_image_path:
                            largest_image_id = s.id
                            break
            
            screenshot_path = screenshot.shot_path if screenshot and os.path.exists(screenshot.shot_path) else None
            movie_info = {
                "id": movie.id,
                "path": movie.path,
                "name": movie.name,
                "length": movie.length,
                "created": movie.created,
                "size": movie.size,
                "watched": movie_status is not None and movie_status.movieStatus == MovieStatusEnum.WATCHED.value,
                "watched_date": movie_status.updated.isoformat() if movie_status and movie_status.updated else None,
                "images": images,
                "screenshots": screenshots,
                "screenshot_id": screenshot.id if screenshot else None,
                "screenshot_path": screenshot_path,
                "image_id": largest_image_id,
                "image_path": largest_image_path,
                "image_path_url": get_image_url_path(largest_image_path) if largest_image_path else None,
                "year": movie.year
            }
            
            launches_with_info.append({
                "movie": movie_info,
                "timestamp": launch.created.isoformat(),
                "subtitle": launch.subtitle
            })
        
        return {"launches": launches_with_info}
    finally:
        db.close()

@app.post("/api/change-status")
async def change_status(request: ChangeStatusRequest):
    """Change movie status (watched, unwatched, want_to_watch, or null to unset)"""
    logger.info(f"POST /api/change-status - Request data: movie_id={request.movie_id}, movieStatus={request.movieStatus}")
    db = SessionLocal()
    try:
        
        # Validate movieStatus value if provided
        if request.movieStatus is not None:
            valid_statuses = {MovieStatusEnum.WATCHED.value, MovieStatusEnum.UNWATCHED.value, MovieStatusEnum.WANT_TO_WATCH.value}
            if request.movieStatus not in valid_statuses:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid movieStatus: {request.movieStatus}. Must be one of: {', '.join(valid_statuses)}"
                )
        
        # Look up movie by ID
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {request.movie_id}")
        
        # Update or create movie status (one-to-one relationship)
        if request.movieStatus is not None:
            movie_status = db.query(MovieStatus).filter(MovieStatus.movie_id == movie.id).first()
            if movie_status:
                movie_status.movieStatus = request.movieStatus
                # updated field will be automatically set by SQLAlchemy onupdate
            else:
                movie_status = MovieStatus(
                    movie_id=movie.id,
                    movieStatus=request.movieStatus
                )
                db.add(movie_status)
        else:
            # If movieStatus is None, remove the status entry (unset)
            movie_status = db.query(MovieStatus).filter(MovieStatus.movie_id == movie.id).first()
            if movie_status:
                db.delete(movie_status)
        
        db.commit()
        
        # Return the new movieStatus
        return {"status": "updated", "movieStatus": request.movieStatus}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in change_status endpoint: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/watched")
async def get_watched():
    """Get list of watched movies"""
    db = SessionLocal()
    try:
        watched_movies_list = []
        
        # Get all movies with "watched" status (one-to-one relationship, no need for deduplication)
        movie_statuses = db.query(MovieStatus).filter(
            MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value
        ).order_by(MovieStatus.updated.desc()).all()
        
        for movie_status in movie_statuses:
            movie = db.query(Movie).filter(Movie.id == movie_status.movie_id).first()
            if movie:
                try:
                    # Get images and screenshots from tables
                    image_objs = db.query(Image).filter(Image.movie_id == movie.id).all()
                    screenshot_objs = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()
                    
                    # Return IDs and paths
                    images = [{"id": img.id, "path": img.image_path} for img in image_objs if "www.YTS.AM" not in img.image_path]
                    screenshots = [{"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds} for s in screenshot_objs]
                    
                    # Get screenshot ID and path
                    screenshot_obj = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).first()
                    screenshot_id = screenshot_obj.id if screenshot_obj else None
                    screenshot_path = screenshot_obj.shot_path if screenshot_obj else None
                    
                    # For get_largest_image (needs paths)
                    image_paths = [img.image_path for img in image_objs if "www.YTS.AM" not in img.image_path]
                    screenshot_paths = [s.shot_path for s in screenshot_objs]
                    info = {
                        "images": image_paths,
                        "screenshots": screenshot_paths,
                        "frame": screenshot_path
                    }
                    
                    # Safely get largest image ID and path with error handling
                    largest_image_id = None
                    largest_image_path = None
                    try:
                        largest_image_path = get_largest_image(info)
                        if largest_image_path:
                            for img in image_objs:
                                if img.image_path == largest_image_path and "www.YTS.AM" not in img.image_path:
                                    largest_image_id = img.id
                                    break
                            if not largest_image_id:
                                for s in screenshot_objs:
                                    if s.shot_path == largest_image_path:
                                        largest_image_id = s.id
                                        break
                    except Exception as e:
                        logger.warning(f"Error getting largest image for movie {movie.id}: {e}")
                    
                    movie_info = {
                        "path": movie.path,
                        "name": movie.name,
                        "length": movie.length,
                        "created": movie.created,
                        "size": movie.size,
                        "watched_date": movie_status.updated.isoformat() if movie_status.updated else None,
                        "images": images,
                        "screenshots": screenshots,
                        "screenshot_id": screenshot_id,
                        "screenshot_path": screenshot_path,
                        "image_id": largest_image_id,
                        "image_path": largest_image_path,
                        "image_path_url": get_image_url_path(largest_image_path) if largest_image_path else None,
                        "year": movie.year,
                        "has_launched": db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
                    }
                    watched_movies_list.append(movie_info)
                except Exception as e:
                    logger.error(f"Error processing movie {movie_status.movie_id} in watched list: {e}", exc_info=True)
                    continue
        
        # Sort by watched date (most recent first)
        # Handle None values by converting them to empty string for sorting
        watched_movies_list.sort(key=lambda x: x.get("watched_date") or "", reverse=True)
        
        return {"watched": watched_movies_list}
    except Exception as e:
        logger.error(f"Error in get_watched endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/subtitles")
async def get_subtitles(movie_id: int = Query(None), video_path: str = Query(None)):
    """Find available subtitle files for a video
    
    Searches in:
    1. Current folder (same directory as video)
    2. "subs" folder (case insensitive) if it exists in the same directory
    """
    # Get video path from movie_id if provided
    if movie_id:
        db = SessionLocal()
        try:
            movie = db.query(Movie).filter(Movie.id == movie_id).first()
            if not movie:
                raise HTTPException(status_code=404, detail=f"Movie not found: {movie_id}")
            video_path = movie.path
        finally:
            db.close()
    elif not video_path:
        raise HTTPException(status_code=400, detail="Either movie_id or video_path must be provided")
    
    video_path_obj = Path(video_path)
    video_dir = video_path_obj.parent
    
    subtitles = []
    subtitle_paths_found = set()
    
    # Search directories: current folder and "subs" folder (case insensitive)
    search_dirs = [("current", video_dir)]
    
    # Check if "subs" folder exists (case insensitive)
    try:
        for item in video_dir.iterdir():
            if item.is_dir() and item.name.lower() == "subs":
                search_dirs.append(("subs", item))
                break
    except Exception as e:
        logger.warning(f"Error checking for subs folder: {e}")
    
    # Search in each directory for any file with a subtitle extension
    for location, search_dir in search_dirs:
        try:
            for subtitle_file in search_dir.iterdir():
                if subtitle_file.is_file() and subtitle_file.suffix.lower() in SUBTITLE_EXTENSIONS:
                    path_str = str(subtitle_file)
                    if path_str not in subtitle_paths_found:
                        subtitles.append({
                            "path": path_str,
                            "name": subtitle_file.name,
                            "type": subtitle_file.suffix[1:].upper(),
                            "location": location
                        })
                        subtitle_paths_found.add(path_str)
        except (PermissionError, OSError) as e:
            logger.warning(f"Error scanning {location} directory for subtitles: {e}")
    
    return {"subtitles": subtitles}

@app.get("/api/rating/{movie_id}")
async def get_rating(movie_id: int):
    """Get rating for a specific movie by ID"""
    db = SessionLocal()
    try:
        rating_entry = db.query(Rating).filter(Rating.movie_id == movie_id).first()
        if rating_entry:
            return {"rating": int(rating_entry.rating), "movie_id": movie_id}
        return {"rating": None, "movie_id": movie_id}
    finally:
        db.close()

@app.post("/api/rating")
async def set_rating(request: RatingRequest):
    """Set rating for a movie (1-5 only)"""
    logger.info(f"POST /api/rating - Request data: movie_id={request.movie_id}, rating={request.rating}")
    db = SessionLocal()
    try:
        # Validate rating is 1-5
        if request.rating < 1 or request.rating > 5:
            raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
        
        # Verify movie exists
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {request.movie_id}")
        
        # Get or create rating entry
        rating_entry = db.query(Rating).filter(Rating.movie_id == request.movie_id).first()
        if rating_entry:
            rating_entry.rating = float(request.rating)
        else:
            rating_entry = Rating(
                movie_id=request.movie_id,
                rating=float(request.rating)
            )
            db.add(rating_entry)
        
        db.commit()
        return {"status": "updated", "rating": request.rating, "movie_id": request.movie_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in set_rating endpoint: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.delete("/api/rating/{movie_id}")
async def delete_rating(movie_id: int):
    """Delete rating for a movie"""
    db = SessionLocal()
    try:
        rating_entry = db.query(Rating).filter(Rating.movie_id == movie_id).first()
        if rating_entry:
            db.delete(rating_entry)
            db.commit()
            return {"status": "deleted", "movie_id": movie_id}
        return {"status": "not_found", "movie_id": movie_id}
    except Exception as e:
        logger.error(f"Error in delete_rating endpoint: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/config")
async def get_config():
    """Get current configuration"""
    db = SessionLocal()
    try:
        config = load_config()
        movies_folder = get_movies_folder()
        
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
            "ffmpeg": ffmpeg_status,
            "settings": config  # Return all settings
        }
    finally:
        db.close()

@app.post("/api/config")
async def set_config(request: ConfigRequest):
    """Set movies folder path and/or user settings"""
    global ROOT_MOVIE_PATH
    logger.info(f"POST /api/config - Request data: movies_folder={request.movies_folder}, settings={request.settings}")
    
    config = load_config()
    
    # Update movies folder if provided
    if request.movies_folder is not None:
        if not request.movies_folder:
            # Remove movies folder from config
            config.pop("movies_folder", None)
            save_config(config)
            ROOT_MOVIE_PATH = None
            logger.info("Removed movies folder from config")
            return {"status": "removed", "movies_folder": ""}
        
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
        # Count movies with "watched" status
        watched_count = db.query(MovieStatus).filter(
            MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value
        ).count()
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

@app.get("/api/language-counts")
async def get_language_counts():
    """Get counts of movies by audio language (from movie_audio)"""
    db = SessionLocal()
    try:
        # Count distinct movies per audio language code from movie_audio
        from sqlalchemy import or_, distinct
        from models import MovieAudio

        # Only consider valid movies (length >= 60 or null) by joining to movies
        counts_rows = (
            db.query(
                func.lower(func.trim(MovieAudio.audio_type)).label("lang"),
                func.count(distinct(MovieAudio.movie_id)).label("count")
            )
            .join(Movie, Movie.id == MovieAudio.movie_id)
            .filter(
                MovieAudio.audio_type.isnot(None),
                func.trim(MovieAudio.audio_type) != '',
                or_(Movie.length == None, Movie.length >= 60)
            )
            .group_by(func.lower(func.trim(MovieAudio.audio_type)))
            .order_by(func.count(distinct(MovieAudio.movie_id)).desc())
            .all()
        )

        counts_dict = {lang: count for lang, count in counts_rows if lang}
        
        # Also get count for "all" (total movies)
        total_count = db.query(Movie).filter(
            or_(Movie.length == None, Movie.length >= 60)
        ).count()
        counts_dict['all'] = total_count
        
        return {"counts": counts_dict}
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
    logger.info(f"POST /api/cleaning-patterns - Request data: {json.dumps(data, indent=2)}")
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

@app.post("/api/admin/reclean-names")
async def reclean_all_names():
    """Re-clean all movie names from their existing paths without full re-scan"""
    db = SessionLocal()
    try:
        movies = db.query(Movie).all()
        total = len(movies)
        updated = 0
        
        for movie in movies:
            try:
                # Re-clean the name using full path (to handle TV series with season/episode)
                cleaned_name, year = clean_movie_name(movie.path)
                
                # Update if changed
                if movie.name != cleaned_name or movie.year != year:
                    movie.name = cleaned_name
                    movie.year = year
                    movie.updated = datetime.now()
                    updated += 1
            except Exception as e:
                logger.warning(f"Error re-cleaning name for movie {movie.id} ({movie.path}): {e}")
                continue
        
        db.commit()
        return {
            "status": "complete",
            "total": total,
            "updated": updated,
            "message": f"Re-cleaned {updated} of {total} movie names"
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error re-cleaning names: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/clean-name/test")
async def test_clean_name(request: CleanNameTestRequest):
    """Test existing clean_movie_name function without modifying any data"""
    logger.info(f"POST /api/clean-name/test - Request data: text={request.text}")
    try:
        patterns = load_cleaning_patterns()
        cleaned, year = clean_movie_name(request.text, patterns)
        return {
            "input": request.text,
            "cleaned_name": cleaned,
            "year": year
        }
    except Exception as e:
        logger.error(f"Error testing clean name: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/currently-playing")
async def get_currently_playing():
    """Get currently playing movies from VLC instances"""
    playing = get_currently_playing_movies()
    return {"playing": playing}

def get_largest_image(movie_info):
    """Get the largest image file from movie's images or screenshots.
    Prefers qualified images from movie folder over screenshots."""
    folder_images = []
    
    # First, check folder images (filter out YTS spam images)
    if movie_info.get("images"):
        filtered_images = filter_yts_images(movie_info["images"])
        for img_path in filtered_images:
            try:
                if os.path.exists(img_path):
                    size = os.path.getsize(img_path)
                    folder_images.append((img_path, size))
            except:
                pass
    
    # If we have qualified folder images, return the largest one
    if folder_images:
        largest = max(folder_images, key=lambda x: x[1])
        return largest[0]
    
    # Fallback to screenshots only if no qualified folder images exist
    screenshot_images = []
    if movie_info.get("screenshots"):
        for screenshot_path in movie_info["screenshots"]:
            try:
                if os.path.exists(screenshot_path):
                    size = os.path.getsize(screenshot_path)
                    screenshot_images.append((screenshot_path, size))
            except:
                pass
    
    # Add frame if available (also a screenshot)
    if movie_info.get("frame"):
        try:
            if os.path.exists(movie_info["frame"]):
                size = os.path.getsize(movie_info["frame"])
                screenshot_images.append((movie_info["frame"], size))
        except:
            pass
    
    if not screenshot_images:
        return None
    
    # Return the largest screenshot
    largest = max(screenshot_images, key=lambda x: x[1])
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
    per_page: int = Query(15, ge=1, le=100),
    filter_type: str = Query("all", pattern="^(all|watched|unwatched)$"),
    letter: Optional[str] = Query(None, pattern="^[A-Z#]$"),
    year: Optional[int] = Query(None, ge=1900, le=2035),
    decade: Optional[int] = Query(None, ge=1900, le=2030),
    language: Optional[str] = Query("all"),
    no_year: Optional[bool] = Query(None)
):
    """Get all movies for exploration view with pagination and filters"""
    # Normalize letter to uppercase if provided
    if letter is not None:
        letter = letter.upper()
    
    # Log the actual request URL and query params to debug letter filtering
    query_params = dict(request.query_params)
    
    db = SessionLocal()
    try:
        # Base query for movies
        from sqlalchemy import or_
        movie_q = db.query(Movie).filter(or_(Movie.length == None, Movie.length >= 60))

        # Apply watched filter using EXISTS subquery for performance
        if filter_type == "watched":
            exists_watch = db.query(MovieStatus.id).filter(
                (MovieStatus.movie_id == Movie.id) & (MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value)
            ).exists()
            movie_q = movie_q.filter(exists_watch)
        elif filter_type == "unwatched":
            exists_watch = db.query(MovieStatus.id).filter(
                (MovieStatus.movie_id == Movie.id) & (MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value)
            ).exists()
            movie_q = movie_q.filter(~exists_watch)

        # Letter filter (SQL-side for A-Z; '#' handled client-like is tricky)
        if letter and letter != "#" and len(letter) == 1 and letter.isalpha():
            # Simple prefix match
            prefix = f"{letter}%"
            movie_q = movie_q.filter(func.substr(Movie.name, 1, 1) == letter)

        # Audio language filter via movie_audio
        if language and language != "all":
            from models import MovieAudio
            # Map canonical codes to all possible variants in the database
            code_variants = {
                'en': ['en', 'eng'],
                'es': ['es', 'spa'],
                'fr': ['fr', 'fra', 'fre'],
                'de': ['de', 'ger', 'deu'],
                'it': ['it', 'ita'],
                'pt': ['pt', 'por'],
                'ru': ['ru', 'rus'],
                'ja': ['ja', 'jpn', 'jap'],
                'ko': ['ko', 'kor'],
                'zh': ['zh', 'zho', 'chi'],
                'hi': ['hi', 'hin'],
                'sv': ['sv', 'swe'],
                'da': ['da', 'dan'],
                'ar': ['ar', 'ara'],
                'pl': ['pl', 'pol'],
                'is': ['is', 'ice'],
                'cs': ['cs', 'cze'],
                'fi': ['fi', 'fin'],
                'und': ['und', 'unknown'],
                'unknown': ['und', 'unknown'],
                'zxx': ['zxx']
            }
            variants = code_variants.get(language.lower(), [language.lower()])
            movie_q = movie_q.filter(
                Movie.id.in_(
                    db.query(MovieAudio.movie_id).filter(
                        func.lower(func.trim(MovieAudio.audio_type)).in_(variants)
                    ).subquery()
                )
            )

        # Year filters are mutually exclusive: year > decade > no_year
        # If year is specified, use it (highest priority)
        if year is not None:
            movie_q = movie_q.filter(Movie.year == year)
        # If decade is specified (and year is not), use decade
        elif decade is not None:
            # Ensure decade is a multiple of 10
            decade_start = (decade // 10) * 10
            decade_end = decade_start + 9
            movie_q = movie_q.filter(Movie.year >= decade_start, Movie.year <= decade_end)
        # If no_year is specified (and neither year nor decade), use no_year
        elif no_year:
            movie_q = movie_q.filter(Movie.year == None)

        # Use normal pagination
        total = movie_q.count()
        rows = movie_q.order_by(Movie.name.asc()).offset((page - 1) * per_page).limit(per_page).all()
        
        movie_ids = [m.id for m in rows]

        # Batched fetch for watch status info (one-to-one relationship)
        watch_status_info = {}
        if movie_ids:
            movie_statuses = db.query(MovieStatus).filter(
                MovieStatus.movie_id.in_(movie_ids)
            ).all()
            for ms in movie_statuses:
                watch_status_info[ms.movie_id] = {
                    "watch_status": ms.movieStatus,
                    "watched": ms.movieStatus == MovieStatusEnum.WATCHED.value,  # Keep for backward compatibility
                    "watched_date": ms.updated.isoformat() if ms.updated else None
                }


        # One screenshot id per movie (prefer smallest id as representative)
        screenshot_map = {}
        if movie_ids:
            shots = db.query(Screenshot.movie_id, func.min(Screenshot.id)).filter(
                Screenshot.movie_id.in_(movie_ids)
            ).group_by(Screenshot.movie_id).all()
            screenshot_map = {movie_id: shot_id for movie_id, shot_id in shots}

        # Has launched flags for page ids
        launched_set = set()
        if movie_ids:
            launched_rows = db.query(LaunchHistory.movie_id).filter(
                LaunchHistory.movie_id.in_(movie_ids)
            ).distinct().all()
            launched_set = {r.movie_id for r in launched_rows}

        # Get ratings for page ids
        rating_map = {}
        if movie_ids:
            rating_rows = db.query(Rating.movie_id, Rating.rating).filter(
                Rating.movie_id.in_(movie_ids)
            ).all()
            rating_map = {movie_id: int(rating) for movie_id, rating in rating_rows}

        # Build response items without heavy filesystem ops
        result_movies = []
        for m in rows:
            info = watch_status_info.get(m.id, {})
            watch_status = info.get("watch_status")
            result_movies.append({
                "id": m.id,
                "path": m.path,
                "name": m.name,
                "length": m.length,
                "created": m.created,
                "size": m.size,
                "watch_status": watch_status,
                "watched": bool(info.get("watched", False)),  # Keep for backward compatibility
                "watched_date": info.get("watched_date"),
                "year": m.year,
                "has_launched": (m.id in launched_set),
                "screenshot_id": screenshot_map.get(m.id),  # frontend will call /api/screenshot/{id}
                "rating": rating_map.get(m.id)
            })

        # Letter counts across all movies respecting watched filter (but not letter/year/decade filters)
        # Fetch only needed columns for speed
        counts_q = db.query(Movie.id, Movie.name, Movie.year)
        if filter_type == "watched":
            exists_watch = db.query(MovieStatus.id).filter(
                (MovieStatus.movie_id == Movie.id) & (MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value)
            ).exists()
            counts_q = counts_q.filter(exists_watch)
        elif filter_type == "unwatched":
            exists_watch = db.query(MovieStatus.id).filter(
                (MovieStatus.movie_id == Movie.id) & (MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value)
            ).exists()
            counts_q = counts_q.filter(~exists_watch)
        letter_counts = {}
        year_counts = {}
        decade_counts = {}
        no_year_count = 0
        for _, nm, yr in counts_q.all():
            lt = get_first_letter(nm)
            letter_counts[lt] = letter_counts.get(lt, 0) + 1
            if yr is not None:
                year_counts[yr] = year_counts.get(yr, 0) + 1
                # Calculate decade (e.g., 1987 -> 1980s)
                decade_start = (yr // 10) * 10
                decade_counts[decade_start] = decade_counts.get(decade_start, 0) + 1
            else:
                no_year_count += 1

        return {
            "movies": result_movies,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page if total > 0 else 0
            },
            "letter_counts": letter_counts,
            "year_counts": year_counts,
            "decade_counts": decade_counts,
            "no_year_count": no_year_count
        }
    except Exception as e:
        logger.error(f"Error in explore endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/random-movie")
async def get_random_movie():
    """Get a random movie ID"""
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        # Query movies with length >= 60 or null length
        movie_q = db.query(Movie.id).filter(or_(Movie.length == None, Movie.length >= 60))
        total = movie_q.count()
        
        if total == 0:
            raise HTTPException(status_code=404, detail="No movies found")
        
        import random
        offset = random.randint(0, total - 1)
        random_movie = movie_q.offset(offset).limit(1).first()
        
        if not random_movie:
            raise HTTPException(status_code=404, detail="No movie found")
        
        return {"id": random_movie.id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in random-movie endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/random-movies")
async def get_random_movies(count: int = Query(10, ge=1, le=50)):
    """Get random movie cards with full metadata"""
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        # Query movies with length >= 60 or null length
        movie_q = db.query(Movie).filter(or_(Movie.length == None, Movie.length >= 60))
        total = movie_q.count()
        
        if total == 0:
            return {"results": []}
        
        # Get random movies by selecting random offsets
        import random
        actual_count = min(count, total)
        random_offsets = random.sample(range(total), actual_count)
        
        # Fetch movies at random offsets
        random_movies = []
        for offset in random_offsets:
            movie = movie_q.offset(offset).limit(1).first()
            if movie:
                random_movies.append(movie)
        
        if not random_movies:
            return {"results": []}
        
        movie_ids = [m.id for m in random_movies]
        
        # Batched fetch for watch status info (latest watch entry regardless of status)
        watch_status_info = {}
        if movie_ids:
            movie_statuses = db.query(MovieStatus).filter(
                MovieStatus.movie_id.in_(movie_ids)
            ).all()
            for ms in movie_statuses:
                watch_status_info[ms.movie_id] = {
                    "watch_status": ms.movieStatus,
                    "watched": ms.movieStatus == MovieStatusEnum.WATCHED.value,
                    "watched_date": ms.updated.isoformat() if ms.updated else None
                }
        
        # One screenshot id per movie (prefer smallest id as representative)
        screenshot_map = {}
        if movie_ids:
            shots = db.query(Screenshot.movie_id, func.min(Screenshot.id)).filter(
                Screenshot.movie_id.in_(movie_ids)
            ).group_by(Screenshot.movie_id).all()
            screenshot_map = {movie_id: shot_id for movie_id, shot_id in shots}
        
        # Has launched flags
        launched_set = set()
        if movie_ids:
            launched_rows = db.query(LaunchHistory.movie_id).filter(
                LaunchHistory.movie_id.in_(movie_ids)
            ).distinct().all()
            launched_set = {r.movie_id for r in launched_rows}
        
        # Get ratings
        rating_map = {}
        if movie_ids:
            rating_rows = db.query(Rating.movie_id, Rating.rating).filter(
                Rating.movie_id.in_(movie_ids)
            ).all()
            rating_map = {movie_id: int(rating) for movie_id, rating in rating_rows}
        
        # Build response items
        result_movies = []
        for m in random_movies:
            info = watch_status_info.get(m.id, {})
            watch_status = info.get("watch_status")
            result_movies.append({
                "id": m.id,
                "path": m.path,
                "name": m.name,
                "length": m.length,
                "created": m.created,
                "size": m.size,
                "watch_status": watch_status,
                "watched": bool(info.get("watched", False)),
                "watched_date": info.get("watched_date"),
                "year": m.year,
                "has_launched": (m.id in launched_set),
                "screenshot_id": screenshot_map.get(m.id),
                "rating": rating_map.get(m.id)
            })
        
        return {"results": result_movies}
    except Exception as e:
        logger.error(f"Error in random-movies endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


if __name__ == "__main__":
    from server import run_server
    run_server()

