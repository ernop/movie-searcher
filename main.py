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
LOG_FILE = Path(__file__).parent.parent / "movie_searcher.log" if Path(__file__).parent.parent.exists() else Path(__file__).parent / "movie_searcher.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# Database setup - import from database module
from database import (
    Base, SessionLocal, get_db,
    Movie, Rating, WatchHistory, SearchHistory, LaunchHistory, IndexedPath, Config, Screenshot, Image, SchemaVersion,
    init_db, migrate_db_schema, remove_sample_files,
    get_movie_id_by_path, get_indexed_paths_set, get_movie_screenshot_path
)
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import func

# Import video processing and subprocess management
from video_processing import (
    initialize_video_processing,
    shutdown_flag, kill_all_active_subprocesses,
    run_interruptible_subprocess,
    get_video_length as get_video_length_vp, validate_ffmpeg_path, find_ffmpeg as find_ffmpeg_core,
    extract_movie_screenshot_sync, generate_screenshot_filename,
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

    removed_count = remove_sample_files()
    if removed_count > 0:
        print(f"Removed {removed_count} sample file(s) from database")
    
    # Set up callbacks for scanning module to avoid circular imports
    set_scanning_callbacks(load_config, get_movies_folder)
    
    yield
    
    # Shutdown
    logger.info("Shutdown event triggered, cleaning up...")
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
    path: str
    subtitle_path: Optional[str] = None
    close_existing_vlc: bool = True

class WatchedRequest(BaseModel):
    path: str
    watch_status: Optional[bool] = None  # None = unset, True = watched, False = unwatched
    rating: Optional[float] = None

class ConfigRequest(BaseModel):
    movies_folder: Optional[str] = None
    settings: Optional[dict] = None

class CleanNameTestRequest(BaseModel):
    text: str

def get_video_length(file_path):
    """Extract video length using mutagen if available, otherwise return None"""
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

@app.get("/star-rating.js")
async def get_star_rating_js():
    """Serve the star rating JavaScript file"""
    js_path = SCRIPT_DIR / "star-rating.js"
    if js_path.exists():
        with open(js_path, "r", encoding="utf-8") as f:
            from fastapi.responses import Response
            return Response(content=f.read(), media_type="application/javascript")
    raise HTTPException(status_code=404, detail="star-rating.js not found")

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
    language: Optional[str] = Query("all")
):
    """Search movies. Always returns full fields (limited to 50)."""
    if not q or len(q) < 2:
        return {"results": []}
    
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
            watched_entries = db.query(WatchHistory.movie_id).filter(
                WatchHistory.watch_status == True
            ).distinct().all()
            watched_movie_ids = {row[0] for row in watched_entries}

        # Apply watched/unwatched filter
        if filter_type == "watched":
            if not watched_movie_ids:
                # No watched movies, return empty
                return {"results": []}
            movie_query = movie_query.filter(Movie.id.in_(watched_movie_ids))
        elif filter_type == "unwatched":
            if watched_movie_ids:
                movie_query = movie_query.filter(~Movie.id.in_(watched_movie_ids))
        
        # Apply language filter
        if language and language != "all":
            movie_query = movie_query.filter(Movie.language == language)

        # Get movies and calculate scores
        movies = movie_query.all()
        
        # Score and sort: prioritize names starting with query
        scored_movies = []
        for movie in movies:
            name_lower = movie.name.lower()
            score = 100 if name_lower.startswith(query_lower) else 50
            scored_movies.append((score, movie))
        
        # Sort by score (desc), then name (asc), limit to 50
        scored_movies.sort(key=lambda x: (-x[0], x[1].name.lower()))
        scored_movies = scored_movies[:50]
        
        # Extract result IDs for batch loading
        result_ids = [movie.id for _, movie in scored_movies]
        results = []

        # Preload watch status info (latest watch_status for each movie)
        watch_status_dict = {}
        if result_ids:
            # Get latest watch entry for each movie (regardless of status)
            watch_entries = db.query(WatchHistory).filter(
                WatchHistory.movie_id.in_(result_ids)
            ).order_by(WatchHistory.updated.desc()).all()
            for entry in watch_entries:
                if entry.movie_id not in watch_status_dict:
                    watch_status_dict[entry.movie_id] = {
                        "watch_status": entry.watch_status,
                        "watched_date": entry.updated.isoformat() if entry.updated else None,
                        "rating": None
                    }

            # Preload ratings
            for rating in db.query(Rating).filter(Rating.movie_id.in_(result_ids)).all():
                if rating.movie_id in watch_status_dict:
                    watch_status_dict[rating.movie_id]["rating"] = rating.rating

        # Build results
        for score, movie in scored_movies:
            watch_info = watch_status_dict.get(movie.id, {})
            watch_status = watch_info.get("watch_status")
            is_watched = watch_status is True  # For backward compatibility
            
            images = [img.image_path for img in db.query(Image).filter(Image.movie_id == movie.id).all()]
            screenshots = [s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()]
            images = filter_yts_images(images)
            screenshot_path = get_movie_screenshot_path(db, movie.id)
            info = {
                "images": images,
                "screenshots": screenshots,
                "frame": screenshot_path
            }
            largest_image = get_largest_image(info)
            has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0

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
                "rating": watch_info.get("rating"),
                "score": score,
                "images": images,
                "screenshots": screenshots,
                "frame": screenshot_path,
                "image": largest_image,
                "year": movie.year,
                "has_launched": has_launched
            })

        # Save to history (count what we actually return)
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

        return {"results": results}
    finally:
        db.close()

@app.get("/api/movie")
async def get_movie_details(path: str = Query(...)):
    """Get detailed information about a specific movie"""
    db = SessionLocal()
    try:
        from sqlalchemy.sql import func
        # Resolve by normalized path, tolerant to slash variants and case on Windows
        normalized_path = None
        try:
            normalized_path = os.path.normpath(path)
        except Exception:
            normalized_path = path

        movie = db.query(Movie).filter(Movie.path == normalized_path).first()
        if not movie and os.name == 'nt':
            movie = db.query(Movie).filter(func.lower(Movie.path) == normalized_path.lower()).first()
        if not movie:
            swapped = normalized_path.replace("\\", "/") if "\\" in normalized_path else normalized_path.replace("/", "\\")
            movie = db.query(Movie).filter(Movie.path == swapped).first()
            if not movie and os.name == 'nt':
                movie = db.query(Movie).filter(func.lower(Movie.path) == swapped.lower()).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Get latest watch status (None/True/False)
        watch_entry = db.query(WatchHistory).filter(
            WatchHistory.movie_id == movie.id
        ).order_by(WatchHistory.updated.desc()).first()
        watch_status = watch_entry.watch_status if watch_entry else None
        
        # Get rating
        rating_entry = db.query(Rating).filter(Rating.movie_id == movie.id).first()
        
        # Get images and screenshots from tables
        images = [img.image_path for img in db.query(Image).filter(Image.movie_id == movie.id).all()]
        screenshots = [s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()]
        
        # Filter out YTS images
        images = filter_yts_images(images)
        
        # Get screenshot path
        screenshot_path = get_movie_screenshot_path(db, movie.id)
        
        info = {
            "images": images,
            "screenshots": screenshots,
            "frame": screenshot_path
        }
        
        # Get largest image
        largest_image = get_largest_image(info)

        # If no image/screenshot is available, synchronously extract a thumbnail now
        if not largest_image:
            try:
                # Prefer a reasonable default timestamp; extract_movie_screenshot_sync will clamp if needed
                default_ts = 15
                shot_path = extract_movie_screenshot_sync(movie.path, default_ts, find_ffmpeg)
                if shot_path and os.path.exists(shot_path):
                    # Persist to DB if not already present
                    existing = db.query(Screenshot).filter(
                        Screenshot.movie_id == movie.id,
                        Screenshot.shot_path == shot_path
                    ).first()
                    if not existing:
                        db.add(Screenshot(movie_id=movie.id, shot_path=shot_path))
                        db.commit()
                    # Refresh media lists
                    screenshots = [s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()]
                    screenshot_path = get_movie_screenshot_path(db, movie.id)
                    info = {
                        "images": images,
                        "screenshots": screenshots,
                        "frame": screenshot_path
                    }
                    largest_image = get_largest_image(info)
            except HTTPException:
                # Propagate API errors as-is
                raise
            except Exception as e:
                # Non-fatal: If extraction fails, continue without image
                logger.warning(f"On-demand screenshot extraction failed for {movie.path}: {e}")
        
        # Year is normalized and stored during indexing/cleaning
        year = movie.year
        
        has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
        
        return {
            "id": movie.id,
            "path": movie.path,
            "name": movie.name,
            "length": movie.length,
            "created": movie.created,
            "size": movie.size,
            "watch_status": watch_status,
            "watched": watch_status is True,  # Keep for backward compatibility
            "watched_date": watch_entry.updated.isoformat() if watch_entry and watch_entry.updated else None,
            "rating": rating_entry.rating if rating_entry else None,
            "images": images,
            "screenshots": screenshots,
            "frame": screenshot_path,
            "image": largest_image,
            "year": year,
            "has_launched": has_launched
        }
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

        # Get latest watch status (None/True/False)
        watch_entry = db.query(WatchHistory).filter(
            WatchHistory.movie_id == movie.id
        ).order_by(WatchHistory.updated.desc()).first()
        watch_status = watch_entry.watch_status if watch_entry else None

        # Get rating
        rating_entry = db.query(Rating).filter(Rating.movie_id == movie.id).first()

        # Get images and screenshots from tables
        images = [img.image_path for img in db.query(Image).filter(Image.movie_id == movie.id).all()]
        screenshots = [s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()]

        # Filter out YTS images
        images = filter_yts_images(images)

        # Get screenshot path
        screenshot_path = get_movie_screenshot_path(db, movie.id)

        info = {
            "images": images,
            "screenshots": screenshots,
            "frame": screenshot_path
        }

        # Get largest image
        largest_image = get_largest_image(info)

        # If no image/screenshot is available, best-effort extract a thumbnail
        if not largest_image:
            try:
                default_ts = 15
                shot_path = extract_movie_screenshot_sync(movie.path, default_ts, find_ffmpeg)
                if shot_path and os.path.exists(shot_path):
                    existing = db.query(Screenshot).filter(
                        Screenshot.movie_id == movie.id,
                        Screenshot.shot_path == shot_path
                    ).first()
                    if not existing:
                        db.add(Screenshot(movie_id=movie.id, shot_path=shot_path))
                        db.commit()
                    screenshots = [s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()]
                    screenshot_path = get_movie_screenshot_path(db, movie.id)
                    info = {
                        "images": images,
                        "screenshots": screenshots,
                        "frame": screenshot_path
                    }
                    largest_image = get_largest_image(info)
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"On-demand screenshot extraction failed for id={movie.id}: {e}")

        year = movie.year
        has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0

        return {
            "id": movie.id,
            "path": movie.path,
            "name": movie.name,
            "length": movie.length,
            "created": movie.created,
            "size": movie.size,
            "watch_status": watch_status,
            "watched": watch_status is True,  # Keep for backward compatibility
            "watched_date": watch_entry.updated.isoformat() if watch_entry and watch_entry.updated else None,
            "rating": rating_entry.rating if rating_entry else None,
            "images": images,
            "screenshots": screenshots,
            "frame": screenshot_path,
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
        if not image_path:
            raise HTTPException(status_code=400, detail="image_path is required")
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
        screenshots_dir = (Path(__file__).parent / "screenshots")
        if movies_folder or screenshots_dir:
            movies_path = Path(movies_folder)
            try:
                path_obj.resolve().relative_to(movies_path.resolve())
            except ValueError:
                # Also allow screenshots directory (use local path to avoid None resolve())
                try:
                    path_obj.resolve().relative_to(screenshots_dir.resolve())
                except ValueError:
                    raise HTTPException(status_code=403, detail="Access denied")
        
        return FileResponse(str(path_obj))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving image {image_path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
        # Restrict to the app screenshots directory
        screenshots_dir = (Path(__file__).parent / "screenshots").resolve()
        try:
            path_obj.resolve().relative_to(screenshots_dir)
        except ValueError:
            # As a safeguard, also allow under movies folder (legacy paths)
            movies_folder = get_movies_folder()
            if not movies_folder:
                raise HTTPException(status_code=403, detail="Access denied")
            try:
                path_obj.resolve().relative_to(Path(movies_folder).resolve())
            except ValueError:
                raise HTTPException(status_code=403, detail="Access denied")
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
    # Validate movie exists in index before launching
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.path == request.path).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found in index: {request.path}")
    finally:
        db.close()
    
    # Delegate to VLC integration module
    try:
        result = launch_movie_in_vlc(
            movie_path=request.path,
            subtitle_path=request.subtitle_path,
            close_existing=request.close_existing_vlc
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
            Screenshot
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
            Screenshot, Movie.id == Screenshot.movie_id
        ).order_by(
            LaunchHistory.created.desc()
        ).limit(100).all()
        
        launches_with_info = []
        for launch, movie, watch_entry, rating_entry, screenshot in results:
            if not movie:
                continue
            
            # Get images and screenshots from tables
            images = [img.image_path for img in db.query(Image).filter(Image.movie_id == movie.id).all()]
            screenshots = [s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()]
            
            # Filter out YTS images
            images = filter_yts_images(images)
            
            # Get screenshot path
            screenshot_path = None
            if screenshot and os.path.exists(screenshot.shot_path):
                screenshot_path = screenshot.shot_path
            
            info = {
                "images": images,
                "screenshots": screenshots,
                "frame": screenshot_path
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
                "frame": screenshot_path,
                "image": get_largest_image(info),
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

@app.post("/api/watched")
async def mark_watched(request: WatchedRequest):
    """Mark movie as watched or unwatched, optionally with rating"""
    db = SessionLocal()
    try:
        # Log the incoming request for debugging
        logger.debug(f"mark_watched called with path={request.path}, watch_status={request.watch_status}, rating={request.rating} (type: {type(request.rating)})")
        
        # Resolve movie by path, with path normalization to be robust to slash variants on Windows
        movie = None
        if request.path:
            input_path = request.path
            try:
                # Normalize path (handles both forward and backslashes)
                normalized_path = os.path.normpath(input_path)
            except Exception:
                normalized_path = input_path

            # First try exact match as stored
            movie = db.query(Movie).filter(Movie.path == normalized_path).first()

            if not movie and os.name == 'nt':
                # On Windows, also attempt a case-insensitive comparison, since some sources may vary in case
                movie = db.query(Movie).filter(func.lower(Movie.path) == normalized_path.lower()).first()

            if not movie:
                # As a last attempt, try swapping slashes in case the DB stored variant differs
                swapped = normalized_path.replace("\\", "/") if "\\" in normalized_path else normalized_path.replace("/", "\\")
                movie = db.query(Movie).filter(Movie.path == swapped).first()
                if not movie and os.name == 'nt':
                    movie = db.query(Movie).filter(func.lower(Movie.path) == swapped.lower()).first()

        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {request.path}")
        
        # Delete all existing watch history entries for this movie
        db.query(WatchHistory).filter(
            WatchHistory.movie_id == movie.id
        ).delete()
        
        # If watch_status is not None, create a new entry with that status
        if request.watch_status is not None:
            watch_entry = WatchHistory(
                movie_id=movie.id,
                watch_status=request.watch_status
            )
            db.add(watch_entry)
            
            # Update rating if provided (only when setting to watched)
            if request.rating is not None and request.watch_status is True:
                # Ensure rating is a float
                rating_value = float(request.rating) if request.rating is not None else None
                logger.debug(f"Saving rating {rating_value} for movie {movie.id}")
                rating_entry = Rating(
                    movie_id=movie.id,
                    rating=rating_value
                )
                db.merge(rating_entry)
        
        db.commit()
        
        # Return the new watch_status
        return {"status": "updated", "watch_status": request.watch_status}
    except Exception as e:
        logger.error(f"Error in mark_watched endpoint: {e}", exc_info=True)
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
                    try:
                        # Get rating
                        rating_entry = db.query(Rating).filter(Rating.movie_id == movie.id).first()
                        
                        # Get images and screenshots from tables
                        images = [img.image_path for img in db.query(Image).filter(Image.movie_id == movie.id).all()]
                        screenshots = [s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()]
                        
                        # Get screenshot path
                        screenshot_path = get_movie_screenshot_path(db, movie.id)
                        
                        info = {
                            "images": images,
                            "screenshots": screenshots,
                            "frame": screenshot_path
                        }
                        
                        # Safely get largest image with error handling
                        largest_image = None
                        try:
                            largest_image = get_largest_image(info)
                        except Exception as e:
                            logger.warning(f"Error getting largest image for movie {movie.id}: {e}")
                        
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
                            "frame": screenshot_path,
                            "image": largest_image,
                            "year": movie.year,
                            "has_launched": db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0
                        }
                        watched_movies_list.append(movie_info)
                    except Exception as e:
                        logger.error(f"Error processing movie {watch_entry.movie_id} in watched list: {e}", exc_info=True)
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

@app.get("/api/language-counts")
async def get_language_counts():
    """Get counts of movies by language"""
    db = SessionLocal()
    try:
        # Get all movies with language set, filter to valid movies (length >= 60 or null)
        from sqlalchemy import or_
        language_counts = db.query(
            Movie.language,
            func.count(Movie.id).label('count')
        ).filter(
            Movie.language.isnot(None),
            Movie.language != '',
            or_(Movie.length == None, Movie.length >= 60)
        ).group_by(Movie.language).order_by(func.count(Movie.id).desc()).all()
        
        # Convert to dictionary
        counts_dict = {lang: count for lang, count in language_counts if lang}
        
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
    per_page: int = Query(15, ge=1, le=100),
    filter_type: str = Query("all", pattern="^(all|watched|unwatched)$"),
    letter: Optional[str] = Query(None, pattern="^[A-Z#]$"),
    year: Optional[int] = Query(None, ge=1900, le=2035),
    decade: Optional[int] = Query(None, ge=1900, le=2030)
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
        # Base query for movies
        from sqlalchemy import or_
        movie_q = db.query(Movie).filter(or_(Movie.length == None, Movie.length >= 60))

        # Apply watched filter using EXISTS subquery for performance
        if filter_type == "watched":
            exists_watch = db.query(WatchHistory.id).filter(
                (WatchHistory.movie_id == Movie.id) & (WatchHistory.watch_status == True)
            ).exists()
            movie_q = movie_q.filter(exists_watch)
        elif filter_type == "unwatched":
            exists_watch = db.query(WatchHistory.id).filter(
                (WatchHistory.movie_id == Movie.id) & (WatchHistory.watch_status == True)
            ).exists()
            movie_q = movie_q.filter(~exists_watch)

        # Letter filter (SQL-side for A-Z; '#' handled client-like is tricky)
        if letter and letter != "#" and len(letter) == 1 and letter.isalpha():
            # Simple prefix match
            prefix = f"{letter}%"
            movie_q = movie_q.filter(func.substr(Movie.name, 1, 1) == letter)

        # Year filter (exact year match)
        if year is not None:
            movie_q = movie_q.filter(Movie.year == year)
        
        # Decade filter (e.g., 1980 filters for years 1980-1989)
        if decade is not None:
            # Ensure decade is a multiple of 10
            decade_start = (decade // 10) * 10
            decade_end = decade_start + 9
            movie_q = movie_q.filter(Movie.year >= decade_start, Movie.year <= decade_end)

        # Total count after filters
        total = movie_q.count()

        # Order and paginate
        rows = movie_q.order_by(Movie.name.asc()).offset((page - 1) * per_page).limit(per_page).all()
        movie_ids = [m.id for m in rows]

        # Batched fetch for watch status info (latest watch entry regardless of status) limited to page ids
        watch_status_info = {}
        if movie_ids:
            subq = db.query(
                WatchHistory.movie_id, func.max(WatchHistory.updated).label("max_updated")
            ).filter(
                WatchHistory.movie_id.in_(movie_ids)
            ).group_by(WatchHistory.movie_id).subquery()

            wh_alias = aliased(WatchHistory)
            latest_watches = db.query(wh_alias).join(
                subq,
                (wh_alias.movie_id == subq.c.movie_id) & (wh_alias.updated == subq.c.max_updated)
            ).all()
            for w in latest_watches:
                watch_status_info[w.movie_id] = {
                    "watch_status": w.watch_status,
                    "watched": w.watch_status is True,  # Keep for backward compatibility
                    "watched_date": w.updated.isoformat() if w.updated else None
                }

            # Ratings for page ids
            ratings = db.query(Rating).filter(Rating.movie_id.in_(movie_ids)).all()
            rating_map = {r.movie_id: r.rating for r in ratings}
            for mid in movie_ids:
                if mid in watch_status_info and mid in rating_map:
                    watch_status_info[mid]["rating"] = rating_map[mid]

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
                "rating": info.get("rating"),
                "year": m.year,
                "has_launched": (m.id in launched_set),
                "screenshot_id": screenshot_map.get(m.id)  # frontend will call /api/screenshot/{id}
            })

        # Letter counts across all movies respecting watched filter (but not letter/year/decade filters)
        # Fetch only needed columns for speed
        counts_q = db.query(Movie.id, Movie.name, Movie.year)
        if filter_type == "watched":
            exists_watch = db.query(WatchHistory.id).filter(
                (WatchHistory.movie_id == Movie.id) & (WatchHistory.watch_status == True)
            ).exists()
            counts_q = counts_q.filter(exists_watch)
        elif filter_type == "unwatched":
            exists_watch = db.query(WatchHistory.id).filter(
                (WatchHistory.movie_id == Movie.id) & (WatchHistory.watch_status == True)
            ).exists()
            counts_q = counts_q.filter(~exists_watch)
        letter_counts = {}
        year_counts = {}
        decade_counts = {}
        for _, nm, yr in counts_q.all():
            lt = get_first_letter(nm)
            letter_counts[lt] = letter_counts.get(lt, 0) + 1
            if yr is not None:
                year_counts[yr] = year_counts.get(yr, 0) + 1
                # Calculate decade (e.g., 1987 -> 1980s)
                decade_start = (yr // 10) * 10
                decade_counts[decade_start] = decade_counts.get(decade_start, 0) + 1

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
            "decade_counts": decade_counts
        }
    except Exception as e:
        logger.error(f"Error in explore endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
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
    
    # Configure uvicorn logging
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
        logger.info("=" * 60)
        uvicorn.run(
            "main:app",
            host="127.0.0.1",
            port=8002,
            reload=False,
            use_colors=False,
            log_config=uvicorn_log_config
        )
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
        shutdown_flag.set()
        kill_all_active_subprocesses()
        sys.exit(0)

