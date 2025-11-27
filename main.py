from fastapi import FastAPI, HTTPException, Query, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from typing import List, Optional, Literal
import os
import json
import subprocess
import re
import shutil
import webbrowser
from pathlib import Path
from datetime import datetime
import hashlib
import logging
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import atexit
import uuid
from decimal import Decimal, ROUND_HALF_UP

# AI Search imports
import openai
import anthropic
from fuzzywuzzy import fuzz, process

# Setup logging
from utils.logging import setup_logging, set_app_shutting_down
setup_logging()
logger = logging.getLogger(__name__)

# Browser URL to open on startup (set by server.py if launched via start.py)
_open_browser_url: str | None = None

# Database setup - import from database module
from database import (
    Base, SessionLocal, get_db,
    Movie, Rating, MovieStatus, SearchHistory, LaunchHistory, IndexedPath, Config, Screenshot, SchemaVersion,
    Playlist, PlaylistItem, ExternalMovie, Person, MovieCredit,
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
    process_frame_queue, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
)

# FastAPI app will be created after lifespan function is defined
# (temporary placeholder - will be replaced)
app = None

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()

# Initialize video processing immediately at module load time
# (lifespan function is not reliably called in all uvicorn configurations)
logger.info(f"Initializing video processing at module load...")
from video_processing import initialize_video_processing
initialize_video_processing(SCRIPT_DIR)
logger.info(f"Video processing initialized")

# Prevent duplicate scan starts (race between concurrent requests)
scan_start_lock = threading.Lock()

SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.vtt', '.ass', '.ssa'}

# Import config functions from shared module
from config import load_config, save_config, get_movies_folder

def filter_existing_screenshots(screenshot_objs: list) -> list:
    """
    Filter screenshot objects to only include those where the file actually exists on disk.
    Returns list of Screenshot objects with existing files.
    """
    existing = []
    from video_processing import SCREENSHOT_DIR
    
    for screenshot in screenshot_objs:
        if not screenshot.shot_path:
            continue
        
        path_obj = Path(screenshot.shot_path)
        path_exists = False
        
        # Check exact path
        if path_obj.exists():
            path_exists = True
        # If relative, check in SCREENSHOT_DIR
        elif SCREENSHOT_DIR and not path_obj.is_absolute():
            if (SCREENSHOT_DIR / path_obj).exists():
                path_exists = True
        
        if path_exists:
            existing.append(screenshot)
        else:
            logger.debug(f"Screenshot file missing: {screenshot.shot_path}")
            
    return existing

def get_image_url_path(image_path: str) -> Optional[str]:
    """
    Convert absolute image path to relative URL path for static serving.
    Returns None if path is a screenshot (handled separately) or if movies folder not configured.
    """
    if not image_path:
        return None
    
    # Screenshots are handled separately via /screenshots/ endpoint
    if 'screenshots' in image_path:
        return None
    
    movies_folder = get_movies_folder()
    if not movies_folder:
        return None
    
    try:
        image_path_obj = Path(image_path).resolve()
        movies_folder_obj = Path(movies_folder).resolve()
        
        # Check if image is within movies folder
        relative_path = image_path_obj.relative_to(movies_folder_obj)
        # Convert to forward slashes for URL
        return str(relative_path).replace('\\', '/')
    except (ValueError, OSError):
        # Image is not in movies folder or path resolution failed
        return None

def ensure_movie_has_screenshot(movie_id: int, movie_path: str, has_image: bool, screenshot_objs: list):
    """
    Ensure movie has at least one screenshot or image. If not, queue a screenshot at 5 minutes (300s).
    This is called when viewing movie details or cards to ensure every movie has visual content.
    """
    has_screenshots = len(screenshot_objs) > 0
    
    if not has_image and not has_screenshots:
        # Queue mandatory screenshot at 5 minutes (300 seconds)
        logger.info(f"Movie movie_id={movie_id} has no images or screenshots. Queuing mandatory screenshot at 300s...")
        try:
            # Check queue size before to verify if screenshot gets queued
            from video_processing import frame_extraction_queue
            queue_size_before = frame_extraction_queue.qsize()
            
            result = extract_movie_screenshot(
                movie_path,
                timestamp_seconds=300,
                priority="normal",
                movie_id=movie_id
            )
            # extract_movie_screenshot returns:
            # - str(path) if screenshot already exists (file on disk)
            # - None if queued successfully OR if ffmpeg not found
            # We check queue size to verify it was actually queued
            if isinstance(result, str):
                logger.info(f"Screenshot already exists for movie_id={movie_id} at 300s: {result}")
            else:
                # Check if queue size increased (indicates successful queue)
                queue_size_after = frame_extraction_queue.qsize()
                if queue_size_after > queue_size_before:
                    logger.info(f"Successfully queued mandatory screenshot at 300s for movie_id={movie_id}, path={movie_path} (queue size: {queue_size_before} -> {queue_size_after})")
                else:
                    logger.warning(f"Failed to queue screenshot for movie_id={movie_id} - queue size unchanged ({queue_size_before}). Check if ffmpeg is configured.")
        except Exception as e:
            logger.error(f"Failed to queue mandatory screenshot for movie_id={movie_id}, path={movie_path}: {e}", exc_info=True)
    else:
        logger.debug(f"Movie movie_id={movie_id} already has image={has_image} or screenshots={has_screenshots}, skipping auto-queue")


def build_movie_cards(db, movies: List[Movie]) -> dict:
    """
    Build standardized movie card dictionaries for a list of movies.
    Performs batch fetching of related data (screenshots, ratings, etc.) for performance.
    Returns a dictionary mapping movie_id -> movie_card_dict.
    """
    if not movies:
        return {}
    
    movie_ids = [m.id for m in movies]
    
    # 1. Batch load screenshots
    screenshots_dict = {}
    if movie_ids:
        all_screenshots = db.query(Screenshot).filter(Screenshot.movie_id.in_(movie_ids)).all()
        for s in all_screenshots:
            if s.movie_id not in screenshots_dict:
                screenshots_dict[s.movie_id] = []
            screenshots_dict[s.movie_id].append(s)

    # 2. Batch load ratings
    rating_map = {}
    if movie_ids:
        rating_rows = db.query(Rating.movie_id, Rating.rating).filter(
            Rating.movie_id.in_(movie_ids)
        ).all()
        rating_map = {movie_id: int(rating) for movie_id, rating in rating_rows}

    # 3. Batch load watch status and watched_date
    status_map = {}
    if movie_ids:
        statuses = db.query(MovieStatus).filter(MovieStatus.movie_id.in_(movie_ids)).all()
        for s in statuses:
            status_map[s.movie_id] = {
                "watch_status": s.movieStatus,
                "watched": s.movieStatus == MovieStatusEnum.WATCHED.value,
                "watched_date": s.updated.isoformat() if s.updated else None
            }

    # 4. Batch load LaunchHistory for has_launched
    launched_set = set()
    if movie_ids:
        launched_rows = db.query(LaunchHistory.movie_id).filter(
            LaunchHistory.movie_id.in_(movie_ids)
        ).distinct().all()
        launched_set = {r.movie_id for r in launched_rows}
        
    # 5. Batch load playlists
    playlist_map = {}
    if movie_ids:
        playlist_rows = db.query(
            PlaylistItem.movie_id,
            Playlist.name
        ).join(
            Playlist, PlaylistItem.playlist_id == Playlist.id
        ).filter(
            PlaylistItem.movie_id.in_(movie_ids)
        ).order_by(Playlist.is_system.desc(), Playlist.name).all()

        for movie_id, playlist_name in playlist_rows:
            if movie_id not in playlist_map:
                playlist_map[movie_id] = []
            playlist_map[movie_id].append(playlist_name)

    # Build result
    results = {}
    for m in movies:
        # Screenshot logic
        screenshot_objs_raw = screenshots_dict.get(m.id, [])
        screenshot_objs = filter_existing_screenshots(screenshot_objs_raw)
        
        # Check image path existence
        has_image = bool(m.image_path and os.path.exists(m.image_path))
        
        # Queue generation if needed (side effect!)
        ensure_movie_has_screenshot(m.id, m.path, has_image, screenshot_objs)
        
        # Pick screenshot
        screenshot_obj = screenshot_objs[0] if screenshot_objs else None
        screenshot_id = screenshot_obj.id if screenshot_obj else None
        
        # Status info
        status_info = status_map.get(m.id, {})
        watch_status = status_info.get("watch_status")
        
        results[m.id] = {
            "id": m.id,
            "path": m.path,
            "name": m.name,
            "length": m.length,
            "created": m.created,
            "size": m.size,
            "watch_status": watch_status,
            "watched": status_info.get("watched", False),
            "watched_date": status_info.get("watched_date"),
            "year": m.year,
            "has_launched": (m.id in launched_set),
            "screenshot_id": screenshot_id,
            "image_path": m.image_path if has_image else None,
            "image_path_url": get_image_url_path(m.image_path) if m.image_path else None,
            "rating": rating_map.get(m.id),
            "playlists": playlist_map.get(m.id, []),
            # Include filtered screenshots list
            "screenshots": [
                {"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds}
                for s in screenshot_objs
            ]
        }
        
    return results


# Import ffmpeg setup functions from separate module
from setup.ffmpeg_setup import auto_detect_ffmpeg

# Define lifespan function after all dependencies are available
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    """Lifespan context manager for startup and shutdown"""
    try:
        # Startup
        logger.info("=== LIFESPAN STARTUP BEGIN ===")
        logger.info("Startup: initializing database...")
        # Note: init_db creates missing tables but doesn't modify existing ones.
        # migrate_db_schema handles one-time migration from old schema.
        init_db()
        migrate_db_schema()
        logger.info("Startup: database ready.")
    except Exception as e:
        logger.error(f"Error during lifespan startup: {e}", exc_info=True)
        raise

    # Initialize video processing and ffmpeg after DB is ready
    logger.info("Startup: initializing video processing...")
    initialize_video_processing(SCRIPT_DIR)
    
    # Comprehensive ffmpeg check: find, test, install, configure - retries until working
    logger.info("Startup: checking ffmpeg configuration...")
    ffmpeg_configured = auto_detect_ffmpeg()
    
    if ffmpeg_configured:
        # Verify one more time that everything is working
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            from video_processing import test_ffmpeg_comprehensive
            test_result = test_ffmpeg_comprehensive(ffmpeg_path)
            if test_result["ok"] and test_result["ffmpeg_ok"] and test_result["ffprobe_ok"]:
                logger.info(f"FFmpeg fully operational: {test_result.get('ffmpeg_version', 'unknown version')}")
                logger.info(f"FFprobe fully operational: {test_result.get('ffprobe_version', 'unknown version')}")
            else:
                logger.error(f"FFmpeg configuration incomplete: {', '.join(test_result.get('errors', []))}")
        else:
            logger.error("FFmpeg path not found after configuration")
    else:
        logger.error("Failed to configure ffmpeg. Screenshot extraction will be disabled.")
    
    logger.info("Startup: video processing ready.")
    
    # Check and configure VLC (REQUIRED - server will not start without working VLC)
    logger.info("Startup: checking VLC configuration...")
    from vlc_integration import find_vlc_executable, test_vlc_comprehensive
    vlc_path = find_vlc_executable()
    
    if not vlc_path:
        logger.error("VLC not found. VLC is required to launch movies.")
        logger.error("Install VLC from https://www.videolan.org/vlc/")
        logger.error("Or use: winget install --id=VideoLAN.VLC")
        raise RuntimeError("VLC not found - cannot start server")
    
    # Save VLC path to config
    config = load_config()
    if config.get("vlc_path") != vlc_path:
        config["vlc_path"] = vlc_path
        save_config(config)
        logger.info(f"VLC path saved to config: {vlc_path}")
    
    # Test VLC - must pass to start server
    vlc_test = test_vlc_comprehensive(vlc_path)
    if vlc_test["ok"]:
        logger.info(f"VLC fully operational: {vlc_test.get('vlc_version', 'OK')}")
        logger.info(f"  VLC path: {vlc_path}")
    else:
        logger.error(f"VLC found but not working: {', '.join(vlc_test.get('errors', []))}")
        logger.error(f"VLC path: {vlc_path}")
        logger.error("VLC must be fully functional to start server")
        raise RuntimeError(f"VLC validation failed: {', '.join(vlc_test.get('errors', []))}")
    
    # Screenshots are served via custom endpoint /screenshots/{filename} for proper URL encoding handling
    # StaticFiles mount removed - using custom endpoint handles spaces and special characters correctly
    from video_processing import SCREENSHOT_DIR
    if SCREENSHOT_DIR and SCREENSHOT_DIR.exists():
        logger.info(f"Startup: screenshots directory ready at {SCREENSHOT_DIR} (served via /screenshots/ endpoint)")

    try:
        removed_count = remove_sample_files()
        if removed_count > 0:
            print(f"Removed {removed_count} sample file(s) from database")
        
        # No longer needed - scanning module imports config directly
        logger.info("=== LIFESPAN STARTUP COMPLETE ===")
    except Exception as e:
        logger.error(f"Error during lifespan startup (final phase): {e}", exc_info=True)
        raise
    
    # Open browser if requested (set by server.py before uvicorn starts)
    if _open_browser_url:
        logger.info(f"Opening browser to {_open_browser_url}")
        webbrowser.open(_open_browser_url)
    
    yield
    
    # Shutdown
    logger.info("Shutdown event triggered, cleaning up...")
    set_app_shutting_down(True)
    shutdown_flag.set()
    kill_all_active_subprocesses()

# Create FastAPI app with lifespan
app = FastAPI(title="Movie Searcher", lifespan=lifespan)


# Import Pydantic models from core module
from core.models import (
    MovieInfo, SearchRequest, LaunchRequest, ChangeStatusRequest,
    RatingRequest, ConfigRequest, FolderRequest, CleanNameTestRequest,
    ScreenshotsIntervalRequest, AiSearchRequest, PlaylistCreateRequest,
    PlaylistAddMovieRequest
)

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
                r'\\b\\d{3,4}p\\b',  # 1080p, 720p, etc.
                r'\\b\\d{3,4}x\\d{3,4}\\b',  # 1920x1080, etc.
                r'\\b(BluRay|BRRip|DVDRip|WEBRip|HDTV|HDRip|BDRip)\\b',
                r'\\b(x264|x265|HEVC|AVC|H\\.264|H\\.265)\\b',
                r'\\b(AC3|DTS|AAC|MP3)\\b',
                r'\\b(REPACK|PROPER|RERIP)\\b',
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
        "frames_total": scan_progress.get("frames_total", 0),
        "movies_added": scan_progress.get("movies_added", 0),
        "movies_updated": scan_progress.get("movies_updated", 0),
        "movies_removed": scan_progress.get("movies_removed", 0)
    }

@app.get("/api/scan-logs")
async def get_scan_logs(lines: int = Query(500, description="Number of lines to return from end of log")):
    """Get complete scan logs from file"""
    try:
        scan_log_file = Path(__file__).parent / "scan_log.txt"
        if not scan_log_file.exists():
            return {"logs": [], "message": "No scan log file found"}

        with open(scan_log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        # Return last N lines to avoid overwhelming the frontend
        lines_to_return = all_lines[-lines:] if lines > 0 else all_lines

        return {
            "logs": [line.strip() for line in lines_to_return],
            "total_lines": len(all_lines),
            "returned_lines": len(lines_to_return)
        }
    except Exception as e:
        return {"error": str(e), "logs": []}

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

def update_search_history_bg(q: str, results_count: int):
    """Background task to update search history. No auto-deletion."""
    db = SessionLocal()
    try:
        # Prevent duplicate consecutive entries
        last_search = db.query(SearchHistory).order_by(SearchHistory.created.desc(), SearchHistory.id.desc()).first()
        
        if last_search and last_search.query == q:
            # Same query as last time - do nothing
            return

        # New query
        search_entry = SearchHistory(
            query=q,
            results_count=results_count
        )
        db.add(search_entry)
        db.commit()
    except Exception as e:
        logging.error(f"Error updating search history: {e}")
    finally:
        db.close()

@app.get("/api/search")
async def search_movies(
    q: str,
    background_tasks: BackgroundTasks,
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

        from sqlalchemy import or_, and_, case, func
        # Build base query
        movie_query = db.query(Movie).filter(
            func.lower(Movie.name).contains(query_lower),
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
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
        
        # Build movie cards
        movies_list = [m for _, m in page_slice]
        movie_cards = build_movie_cards(db, movies_list)
        
        results = []
        for score, movie in page_slice:
            card = movie_cards.get(movie.id)
            if card:
                # Create a copy to add search-specific fields without mutating shared cache
                card_copy = dict(card)
                card_copy["score"] = score
                # Search endpoint legacy fields (if any consumers rely on them)
                # screenshot_path was returned previously but createMovieCard doesn't seem to use it.
                # We'll skip it unless needed.
                results.append(card_copy)

        # Offload history update to background task
        background_tasks.add_task(update_search_history_bg, q, len(results))

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

        # Get screenshots from table
        screenshot_objs_raw = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).order_by(Screenshot.timestamp_seconds.asc().nullslast()).all()

        # Filter to only screenshots that actually exist on disk
        screenshot_objs = filter_existing_screenshots(screenshot_objs_raw)

        screenshots = [{"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds} for s in screenshot_objs]

        # Get screenshot ID and path (for frame) - only from existing screenshots
        screenshot_obj = screenshot_objs[0] if screenshot_objs else None
        screenshot_id = screenshot_obj.id if screenshot_obj else None
        screenshot_path = screenshot_obj.shot_path if screenshot_obj else None

        year = movie.year
        has_launched = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count() > 0

        # Get rating
        rating_entry = db.query(Rating).filter(Rating.movie_id == movie.id).first()
        rating = int(rating_entry.rating) if rating_entry and rating_entry.rating is not None else None

        # Get IMDb data if available
        imdb_data = None
        try:
            # Try to find matching IMDb movie using fuzzy matching on title
            from fuzzywuzzy import fuzz
            from fuzzywuzzy.process import extractOne

            search_title = movie.name.lower()
            imdb_movies = db.query(ExternalMovie).all()

            if imdb_movies:
                # Create searchable titles
                imdb_titles = {}
                for imdb_movie in imdb_movies:
                    title_key = imdb_movie.primary_title.lower()
                    if imdb_movie.year:
                        title_key += f" ({imdb_movie.year})"
                    imdb_titles[title_key] = imdb_movie

                # Try exact match first
                if search_title in imdb_titles:
                    imdb_movie = imdb_titles[search_title]
                    match_score = 100
                else:
                    # Try fuzzy match
                    best_match, match_score = extractOne(
                        search_title,
                        imdb_titles.keys(),
                        scorer=fuzz.token_sort_ratio
                    )

                    if match_score >= 85:  # High confidence threshold
                        imdb_movie = imdb_titles[best_match]
                    else:
                        imdb_movie = None

                if imdb_movie:
                    # Get cast/crew for this movie
                    credits = db.query(MovieCredit, Person).join(
                        Person, MovieCredit.person_id == Person.id
                    ).filter(
                        MovieCredit.movie_id == imdb_movie.id
                    ).order_by(MovieCredit.category, Person.primary_name).all()

                    # Organize by role
                    directors = []
                    actors = []
                    writers = []

                    for credit, person in credits:
                        if credit.category == 'director':
                            directors.append({
                                "id": person.id,
                                "imdb_id": person.imdb_id,
                                "name": person.primary_name,
                                "birth_year": person.birth_year,
                                "death_year": person.death_year
                            })
                        elif credit.category in ['actor', 'actress']:
                            actors.append({
                                "id": person.id,
                                "imdb_id": person.imdb_id,
                                "name": person.primary_name,
                                "character": credit.characters[0] if credit.characters else None,
                                "birth_year": person.birth_year,
                                "death_year": person.death_year
                            })
                        elif credit.category == 'writer':
                            writers.append({
                                "id": person.id,
                                "imdb_id": person.imdb_id,
                                "name": person.primary_name,
                                "birth_year": person.birth_year,
                                "death_year": person.death_year
                            })

                    imdb_data = {
                        "imdb_id": imdb_movie.imdb_id,
                        "primary_title": imdb_movie.primary_title,
                        "original_title": imdb_movie.original_title,
                        "year": imdb_movie.year,
                        "runtime_minutes": imdb_movie.runtime_minutes,
                        "genres": imdb_movie.genres,
                        "rating": imdb_movie.rating,
                        "votes": imdb_movie.votes,
                        "directors": directors,
                        "actors": actors[:10],  # Limit to top 10 actors
                        "writers": writers,
                        "match_score": match_score
                    }

        except ImportError:
            # fuzzywuzzy not installed
            pass
        except Exception as e:
            logger.warning(f"Error fetching IMDb data for movie {movie_id}: {e}")

        # Ensure movie has at least one screenshot/image - queue if missing
        # Check movie.image_path instead of Image table
        has_image = bool(movie.image_path and os.path.exists(movie.image_path))
        ensure_movie_has_screenshot(movie.id, movie.path, has_image, screenshot_objs)

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
            "screenshots": screenshots,
            "screenshot_id": screenshot_id,
            "screenshot_path": screenshot_path,
            "image_path": movie.image_path if (movie.image_path and os.path.exists(movie.image_path)) else None,
            "year": year,
            "has_launched": has_launched,
            "rating": rating,
            "imdb_data": imdb_data
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

        # FIRST: Sync screenshots to detect orphaned files (files on disk not in database)
        # This uses the sync function which properly normalizes paths
        from screenshot_sync import sync_movie_screenshots
        from video_processing import SCREENSHOT_DIR
        
        sync_result = sync_movie_screenshots(movie.id, SCREENSHOT_DIR)
        orphaned_files = sync_result.get("orphaned_files", [])
        
        # Delete orphaned files before deleting from DB (prevents race conditions)
        orphaned_deleted = 0
        for orphaned_path in orphaned_files:
            try:
                if os.path.exists(orphaned_path):
                    os.remove(orphaned_path)
                    orphaned_deleted += 1
                    logger.info(f"Deleted orphaned screenshot file: {Path(orphaned_path).name}")
            except Exception as del_err:
                logger.warning(f"Failed to delete orphaned screenshot file {Path(orphaned_path).name}: {del_err}")
        
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

@app.post("/api/movie/{movie_id}/sync-screenshots")
async def sync_movie_screenshots_endpoint(movie_id: int):
    """Synchronize screenshots for a movie: detect and fix mismatches between DB and disk"""
    from screenshot_sync import sync_movie_screenshots
    from video_processing import SCREENSHOT_DIR
    
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {movie_id}")
        
        result = sync_movie_screenshots(movie_id, SCREENSHOT_DIR)
        return {
            "status": "success",
            "movie_id": movie_id,
            "orphaned_files": result["orphaned_files"],
            "missing_files": result["missing_files"],
            "synced_count": result["synced_count"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing screenshots for movie_id={movie_id}: {e}", exc_info=True)
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

@app.get("/screenshots/{filename:path}")
async def get_screenshot_by_filename(filename: str):
    """
    Serve screenshot by filename. This is a fallback for StaticFiles when URL encoding causes issues.
    Handles URL-decoded filenames with spaces properly.
    """
    from fastapi.responses import FileResponse
    from urllib.parse import unquote
    import video_processing
    
    logger.debug(f"Screenshot request - SCREENSHOT_DIR value: {video_processing.SCREENSHOT_DIR}")
    logger.debug(f"Screenshot request - SCREENSHOT_DIR exists: {video_processing.SCREENSHOT_DIR.exists() if video_processing.SCREENSHOT_DIR else 'N/A'}")
    
    if not video_processing.SCREENSHOT_DIR or not video_processing.SCREENSHOT_DIR.exists():
        raise HTTPException(status_code=404, detail=f"Screenshots directory not found (SCREENSHOT_DIR={video_processing.SCREENSHOT_DIR})")
    
    # URL decode the filename (handles %20 -> space, etc.)
    decoded_filename = unquote(filename)
    
    # Security: prevent directory traversal
    if '..' in decoded_filename or '/' in decoded_filename or '\\' in decoded_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    # Construct full path
    screenshot_path = video_processing.SCREENSHOT_DIR / decoded_filename
    
    # Verify file exists
    if not screenshot_path.exists():
        # Try to find by exact match in database for better error message
        db = SessionLocal()
        try:
            matching = db.query(Screenshot).filter(Screenshot.shot_path.like(f'%{decoded_filename}')).first()
            if matching:
                logger.warning(f"Screenshot file missing but DB entry exists: filename={decoded_filename}, db_path={matching.shot_path}, screenshot_id={matching.id}")
            else:
                logger.warning(f"Screenshot file not found: filename={decoded_filename}")
        finally:
            db.close()
        raise HTTPException(status_code=404, detail=f"Screenshot file not found: {decoded_filename}")
    
    return FileResponse(str(screenshot_path))

@app.post("/api/launch")
async def launch_movie(request: LaunchRequest):
    """Launch movie in VLC with optional subtitle"""
    logger.info(f"POST /api/launch - Request data: movie_id={request.movie_id}, subtitle_path={request.subtitle_path}, close_existing_vlc={request.close_existing_vlc}, start_time={request.start_time}")
    # Validate movie exists in index before launching
    movie_path = None
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {request.movie_id}")
        movie_path = movie.path
        logger.info(f"Launch: Retrieved movie path from database: movie_id={request.movie_id}, path={movie_path}")
        
        # Normalize path before checking existence (same as indexing does)
        from pathlib import Path
        try:
            normalized_path_obj = Path(movie_path).resolve()
            normalized_path = str(normalized_path_obj)
            logger.info(f"Launch: Normalized path: {normalized_path}")
            if normalized_path != movie_path:
                logger.info(f"Launch: Path changed after normalization: '{movie_path}' -> '{normalized_path}'")
                movie_path = normalized_path
        except (OSError, RuntimeError) as e:
            logger.warning(f"Launch: Failed to resolve path '{movie_path}': {e}, using original path")
            # Try absolute() as fallback
            try:
                normalized_path = str(Path(movie_path).absolute())
                logger.info(f"Launch: Using absolute() fallback: {normalized_path}")
                if normalized_path != movie_path:
                    logger.info(f"Launch: Path changed after absolute(): '{movie_path}' -> '{normalized_path}'")
                    movie_path = normalized_path
            except Exception as e2:
                logger.warning(f"Launch: Failed to get absolute path: {e2}, using original path")
        
        # Check if file exists before launching
        import os
        if not os.path.exists(movie_path):
            error_msg = f"File not found: {movie_path}"
            logger.error(f"Launch: File does not exist at path: {movie_path}")
            logger.error(f"Launch: Original database path was: {movie.path}")
            logger.error(f"Launch: Path type: {type(movie_path)}, Path repr: {repr(movie_path)}")
            # Try to find similar files in the same directory
            try:
                parent_dir = Path(movie_path).parent
                if parent_dir.exists():
                    logger.error(f"Launch: Parent directory exists: {parent_dir}")
                    logger.error(f"Launch: Files in parent directory: {list(parent_dir.iterdir())[:10]}")
                else:
                    logger.error(f"Launch: Parent directory does not exist: {parent_dir}")
            except Exception as e:
                logger.error(f"Launch: Error checking parent directory: {e}")
            raise HTTPException(status_code=404, detail=error_msg)
        else:
            logger.info(f"Launch: File exists, proceeding with launch: {movie_path}")
    finally:
        db.close()
    
    # Delegate to VLC integration module
    try:
        result = launch_movie_in_vlc(
            movie_path=movie_path,
            subtitle_path=request.subtitle_path,
            close_existing=request.close_existing_vlc,
            start_time=request.start_time,
            movie_id=request.movie_id
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
        # Query recent launches
        launches = db.query(LaunchHistory).order_by(
            LaunchHistory.created.desc()
        ).limit(100).all()
        
        if not launches:
            return {"launches": []}
            
        # Get associated movies
        movie_ids = {l.movie_id for l in launches}
        movies = db.query(Movie).filter(Movie.id.in_(movie_ids)).all()
        
        # Build standardized movie cards
        movie_cards = build_movie_cards(db, movies)
        
        launches_with_info = []
        for launch in launches:
            # Get the movie card for this launch
            card = movie_cards.get(launch.movie_id)
            if card:
                launches_with_info.append({
                    "movie": card,
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
        # Get all movies with "watched" status
        movie_statuses = db.query(MovieStatus).filter(
            MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value
        ).order_by(MovieStatus.updated.desc()).all()
        
        if not movie_statuses:
            return {"watched": []}
            
        watched_movie_ids = [ms.movie_id for ms in movie_statuses]
        
        # Fetch movies
        movies = db.query(Movie).filter(Movie.id.in_(watched_movie_ids)).all()
        
        # Build standardized movie cards
        movie_cards = build_movie_cards(db, movies)
        
        # Convert to list
        watched_movies_list = list(movie_cards.values())
        
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
        
        # Check ffmpeg status and run comprehensive test
        ffmpeg_path = find_ffmpeg()
        ffmpeg_test = None
        if ffmpeg_path:
            from video_processing import test_ffmpeg_comprehensive
            ffmpeg_test = test_ffmpeg_comprehensive(ffmpeg_path)
        
        ffmpeg_status = {
            "found": ffmpeg_path is not None,
            "path": ffmpeg_path or "",
            "configured": config.get("ffmpeg_path") or None,
            "test": ffmpeg_test
        }
        
        # Return all config settings
        return {
            "movies_folder": movies_folder or "",
            "ffmpeg": ffmpeg_status,
            "settings": config  # Return all settings
        }
    finally:
        db.close()

@app.get("/api/test-ffmpeg")
async def test_ffmpeg_endpoint():
    """Test ffmpeg installation comprehensively"""
    from video_processing import test_ffmpeg_comprehensive
    ffmpeg_path = find_ffmpeg_core(load_config)
    if not ffmpeg_path:
        return {
            "ok": False,
            "ffmpeg_ok": False,
            "ffprobe_ok": False,
            "errors": ["ffmpeg not configured. Please install ffmpeg and configure the path in settings."],
            "ffmpeg_path": None,
            "ffprobe_path": None,
            "ffmpeg_version": None,
            "ffprobe_version": None
        }
    # Get configured ffprobe_path if available (for winget installations where they're in separate dirs)
    config = load_config()
    ffprobe_path = config.get("ffprobe_path")
    return test_ffmpeg_comprehensive(ffmpeg_path, ffprobe_path=ffprobe_path)

@app.get("/api/test-vlc")
async def test_vlc_endpoint():
    """Test VLC installation comprehensively"""
    from vlc_integration import test_vlc_comprehensive
    return test_vlc_comprehensive()

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
        
        # Validate that path is absolute before processing
        if os.name == 'nt':  # Windows
            # On Windows, absolute paths start with drive letter (C:\) or UNC (\\)
            is_drive_path = folder_path and len(folder_path) >= 3 and folder_path[1] == ':' and folder_path[2] in ['\\', '/']
            is_unc_path = folder_path and folder_path.startswith('\\\\')
            if not (is_drive_path or is_unc_path):
                error_msg = f"Path must be absolute (start with drive letter like C:\\ or D:\\): '{folder_path}'"
                logger.error(error_msg)
                raise HTTPException(status_code=400, detail=error_msg)
            # Convert forward slashes to backslashes on Windows
            folder_path = folder_path.replace('/', '\\')
            # Normalize double backslashes (but preserve UNC paths)
            if not folder_path.startswith('\\\\'):
                folder_path = folder_path.replace('\\\\', '\\')
            # Remove trailing backslash (unless it's a drive root like C:\)
            if folder_path.endswith('\\') and len(folder_path) > 3:
                folder_path = folder_path.rstrip('\\')
        else:
            # On Unix-like systems, absolute paths start with /
            if not (folder_path and folder_path.startswith('/')):
                error_msg = f"Path must be absolute (start with /): '{folder_path}'"
                logger.error(error_msg)
                raise HTTPException(status_code=400, detail=error_msg)
        
        logger.info(f"Normalized path: '{folder_path}'")
        
        # Use pathlib for validation (No fallback to os.path)
        path_obj = Path(folder_path)
        # Get absolute path representation (doesn't require path to exist)
        path_obj = path_obj.absolute()
        logger.info(f"Absolute path: '{path_obj}'")
        
        if not path_obj.exists():
            # Additional diagnostics
            logger.error(f"Path does not exist: '{path_obj}'")
            logger.error(f"Path type: {type(path_obj)}")
            logger.error(f"Path parts: {path_obj.parts}")
            # Check if parent exists
            if path_obj.parent.exists():
                logger.error(f"Parent directory exists: '{path_obj.parent}'")
            else:
                logger.error(f"Parent directory also does not exist: '{path_obj.parent}'")
            error_msg = f"Path not found: '{folder_path}'"
            raise HTTPException(status_code=404, detail=error_msg)
        
        if not path_obj.is_dir():
            error_msg = f"Path is not a directory: '{folder_path}'"
            logger.error(error_msg)
            raise HTTPException(status_code=400, detail=error_msg)
        
        # Save movies folder to config (use absolute path for consistency)
        absolute_path_str = str(path_obj)
        config["movies_folder"] = absolute_path_str
        save_config(config)
        logger.info(f"Saved to config: {absolute_path_str}")
        
        # Update global
        ROOT_MOVIE_PATH = absolute_path_str
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
async def open_folder(request: FolderRequest):
    """Open file explorer at the folder containing the movie file"""
    try:
        path_obj = Path(request.path)
        folder_path = path_obj.parent
        
        # 1. Try to open with file selected (if file exists)
        if path_obj.exists() and path_obj.is_file():
            if os.name == 'nt':  # Windows
                subprocess.Popen(f'explorer.exe /select,"{path_obj}"', shell=True)
            elif os.name == 'posix':  # Linux/Mac
                if sys.platform == 'darwin':  # macOS
                    subprocess.Popen(['open', '-R', str(path_obj)])
                else:  # Linux
                    # Try various file managers to open folder (most don't support selecting file via command line easily)
                    for cmd in ['xdg-open', 'nautilus', 'dolphin', 'thunar']:
                        try:
                            subprocess.Popen([cmd, str(folder_path)])
                            break
                        except FileNotFoundError:
                            continue
                    else:
                         raise HTTPException(status_code=500, detail="No file manager found")
            return {"status": "opened", "folder": str(folder_path)}

        # 2. Fallback: Try to open folder only (if file missing but folder exists)
        if folder_path.exists() and folder_path.is_dir():
            if os.name == 'nt':  # Windows
                subprocess.Popen(f'explorer.exe "{folder_path}"', shell=True)
            elif os.name == 'posix':  # Linux/Mac
                if sys.platform == 'darwin':  # macOS
                    subprocess.Popen(['open', str(folder_path)])
                else:  # Linux
                    for cmd in ['xdg-open', 'nautilus', 'dolphin', 'thunar']:
                        try:
                            subprocess.Popen([cmd, str(folder_path)])
                            break
                        except FileNotFoundError:
                            continue
                    else:
                         raise HTTPException(status_code=500, detail="No file manager found")
            
            return {"status": "opened_folder", "folder": str(folder_path), "detail": "File not found, opened folder"}

        # 3. Neither exists
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder_path}")
        
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
                or_(Movie.length == None, Movie.length >= 60),
                Movie.hidden == False
            )
            .group_by(func.lower(func.trim(MovieAudio.audio_type)))
            .order_by(func.count(distinct(MovieAudio.movie_id)).desc())
            .all()
        )

        counts_dict = {lang: count for lang, count in counts_rows if lang}
        
        # Also get count for "all" (total movies)
        total_count = db.query(Movie).filter(
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
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
        
        # Load patterns once to improve performance
        patterns = load_cleaning_patterns()
        
        for movie in movies:
            try:
                # Re-clean the name using full path (to handle TV series with season/episode)
                cleaned_name, year = clean_movie_name(movie.path, patterns)
                
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
    filter_type: str = Query("all", pattern="^(all|watched|unwatched|newest)$"),
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
        
        # If newest filter is active, restrict the base set to the 100 newest movies
        newest_ids_subq = None
        if filter_type == "newest":
            # Subquery for IDs of top 100 newest movies
            newest_ids_subq = db.query(Movie.id).filter(
                or_(Movie.length == None, Movie.length >= 60),
                Movie.hidden == False
            ).order_by(Movie.created.desc()).limit(100).subquery()
            
            # Base query restricted to these IDs
            movie_q = db.query(Movie).filter(Movie.id.in_(newest_ids_subq))
        else:
            movie_q = db.query(Movie).filter(
                or_(Movie.length == None, Movie.length >= 60),
                Movie.hidden == False
            )

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
        # 'newest' filter does not filter by status, implies 'all' status-wise

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
        
        if filter_type == "newest":
            movie_q = movie_q.order_by(Movie.created.desc())
        else:
            movie_q = movie_q.order_by(Movie.name.asc())
            
        rows = movie_q.offset((page - 1) * per_page).limit(per_page).all()
        
        # Build movie cards
        movie_cards = build_movie_cards(db, rows)
        result_movies = [movie_cards[m.id] for m in rows]

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
        elif filter_type == "newest":
            if newest_ids_subq is not None:
                counts_q = counts_q.filter(Movie.id.in_(newest_ids_subq))
            
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
        movie_q = db.query(Movie.id).filter(
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
        )
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
        movie_q = db.query(Movie).filter(
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
        )
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
        
        # Build standardized movie cards
        movie_cards = build_movie_cards(db, random_movies)
        
        # Return list of card dictionaries
        return {"results": list(movie_cards.values())}
    except Exception as e:
        logger.error(f"Error in random-movies endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/all-movies")
async def get_all_movies():
    """Get all movies as a simple list"""
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        # Query all non-hidden movies with length >= 60 or null length
        movies = db.query(Movie).filter(
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
        ).order_by(Movie.name.asc()).all()
        
        # Return simplified list with just id, name, year, path
        result = [
            {
                "id": m.id,
                "name": m.name,
                "year": m.year,
                "path": m.path
            }
            for m in movies
        ]
        
        return {
            "movies": result,
            "total": len(result)
        }
    except Exception as e:
        logger.error(f"Error in all-movies endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/api/movie/{movie_id}/hide")
async def hide_movie(movie_id: int):
    """Hide a movie from search and explore"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        movie.hidden = True
        db.commit()
        return {"status": "hidden", "movie_id": movie_id}
    except Exception as e:
        db.rollback()
        logger.error(f"Error hiding movie {movie_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/movie/{movie_id}/unhide")
async def unhide_movie(movie_id: int):
    """Unhide a movie"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        movie.hidden = False
        db.commit()
        return {"status": "visible", "movie_id": movie_id}
    except Exception as e:
        db.rollback()
        logger.error(f"Error unhiding movie {movie_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/hidden-movies")
async def get_hidden_movies():
    """Get list of hidden movies"""
    db = SessionLocal()
    try:
        movies = db.query(Movie).filter(Movie.hidden == True).all()
        
        # Build movie cards
        movie_cards = build_movie_cards(db, movies)
        
        return {
            "movies": list(movie_cards.values())
        }
    finally:
        db.close()

@app.get("/api/duplicates")
async def get_duplicate_movies():
    """Get list of duplicate movies (same name)"""
    db = SessionLocal()
    try:
        # Find names that appear more than once and are not hidden
        subquery = (
            db.query(Movie.name)
            .filter(Movie.hidden == False)
            .group_by(Movie.name)
            .having(func.count(Movie.name) > 1)
            .subquery()
        )
        
        # Get all movies with those names
        movies = (
            db.query(Movie)
            .filter(Movie.name.in_(subquery))
            .filter(Movie.hidden == False)
            .order_by(Movie.name)
            .all()
        )
        
        # Build movie cards
        movie_cards = build_movie_cards(db, movies)
        
        # Group by name
        from itertools import groupby
        duplicates = []
        for name, group in groupby(movies, key=lambda x: x.name):
            movie_group = list(group)
            if len(movie_group) > 1:
                duplicates.append({
                    "name": name,
                    "count": len(movie_group),
                    "movies": [movie_cards[m.id] for m in movie_group]
                })
        
        return {"duplicates": duplicates}
    finally:
        db.close()

# --- Playlist API Endpoints ---

@app.get("/api/playlists")
async def get_playlists():
    """Get all playlists with movie counts"""
    db = SessionLocal()
    try:
        # Get playlists with movie counts
        playlists = []
        for playlist in db.query(Playlist).order_by(Playlist.is_system.desc(), Playlist.name.asc()).all():
            movie_count = db.query(PlaylistItem).filter(PlaylistItem.playlist_id == playlist.id).count()
            playlists.append({
                "id": playlist.id,
                "name": playlist.name,
                "is_system": playlist.is_system,
                "movie_count": movie_count,
                "created": playlist.created.isoformat() if playlist.created else None
            })

        return {"playlists": playlists}
    finally:
        db.close()

@app.post("/api/playlists")
async def create_playlist(request: PlaylistCreateRequest):
    """Create a new playlist"""
    db = SessionLocal()
    try:
        # Check if playlist name already exists
        existing = db.query(Playlist).filter(Playlist.name == request.name).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Playlist '{request.name}' already exists")

        playlist = Playlist(name=request.name, is_system=False)
        db.add(playlist)
        db.commit()
        db.refresh(playlist)

        return {
            "id": playlist.id,
            "name": playlist.name,
            "is_system": playlist.is_system,
            "movie_count": 0,
            "created": playlist.created.isoformat() if playlist.created else None
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating playlist: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int):
    """Delete a playlist (not system playlists)"""
    db = SessionLocal()
    try:
        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        if playlist.is_system:
            raise HTTPException(status_code=400, detail="Cannot delete system playlists")

        # Delete playlist items first (cascade should handle this, but be explicit)
        db.query(PlaylistItem).filter(PlaylistItem.playlist_id == playlist_id).delete()
        db.delete(playlist)
        db.commit()

        return {"status": "deleted", "playlist_id": playlist_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting playlist {playlist_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/playlists/{playlist_id}")
async def get_playlist_movies(
    playlist_id: int,
    sort: str = Query("date_added", pattern="^(date_added|name|year)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200)
):
    """Get movies in a playlist with sorting and pagination"""
    db = SessionLocal()
    try:
        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        # Base query for playlist items
        item_query = db.query(PlaylistItem, Movie).join(
            Movie, PlaylistItem.movie_id == Movie.id
        ).filter(
            PlaylistItem.playlist_id == playlist_id,
            Movie.hidden == False
        )

        # Apply sorting
        if sort == "name":
            item_query = item_query.order_by(Movie.name.asc())
        elif sort == "year":
            # Handle null years by putting them at the end
            from sqlalchemy import case
            item_query = item_query.order_by(
                case((Movie.year.is_(None), 1), else_=0),  # Nulls last
                Movie.year.desc()
            )
        else:  # date_added
            item_query = item_query.order_by(PlaylistItem.added_at.desc())

        # Get total count
        total = item_query.count()

        # Apply pagination
        items = item_query.offset((page - 1) * per_page).limit(per_page).all()

        # Extract movie IDs for batch loading
        movie_ids = [movie.id for _, movie in items]

        # Build movie cards
        movies_list = [movie for _, movie in items]
        movie_cards = build_movie_cards(db, movies_list)

        # Build response
        movies = []
        for item, movie in items:
            card = dict(movie_cards.get(movie.id, {}))
            if card:
                # Add playlist specific info
                card["added_at"] = item.added_at.isoformat() if item.added_at else None
                movies.append(card)

        return {
            "playlist": {
                "id": playlist.id,
                "name": playlist.name,
                "is_system": playlist.is_system
            },
            "movies": movies,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page if total > 0 else 0
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting playlist {playlist_id} movies: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/playlists/{playlist_id}/add")
async def add_movie_to_playlist(playlist_id: int, request: PlaylistAddMovieRequest):
    """Add a movie to a playlist"""
    db = SessionLocal()
    try:
        # Verify playlist exists
        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        # Verify movie exists
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        # Check if already in playlist
        existing = db.query(PlaylistItem).filter(
            PlaylistItem.playlist_id == playlist_id,
            PlaylistItem.movie_id == request.movie_id
        ).first()

        if existing:
            raise HTTPException(status_code=400, detail="Movie already in playlist")

        # Add to playlist
        item = PlaylistItem(playlist_id=playlist_id, movie_id=request.movie_id)
        db.add(item)
        db.commit()

        return {"status": "added", "playlist_id": playlist_id, "movie_id": request.movie_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error adding movie {request.movie_id} to playlist {playlist_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.delete("/api/playlists/{playlist_id}/remove/{movie_id}")
async def remove_movie_from_playlist(playlist_id: int, movie_id: int):
    """Remove a movie from a playlist"""
    db = SessionLocal()
    try:
        item = db.query(PlaylistItem).filter(
            PlaylistItem.playlist_id == playlist_id,
            PlaylistItem.movie_id == movie_id
        ).first()

        if not item:
            raise HTTPException(status_code=404, detail="Movie not found in playlist")

        db.delete(item)
        db.commit()

        return {"status": "removed", "playlist_id": playlist_id, "movie_id": movie_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing movie {movie_id} from playlist {playlist_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/movies/{movie_id}/add-to-playlist")
async def add_movie_to_playlist_by_name(movie_id: int, playlist_name: str = Query(..., description="Name of playlist to add to")):
    """Quick add movie to playlist by name (creates playlist if it doesn't exist, except for system playlists)"""
    db = SessionLocal()
    try:
        # Verify movie exists
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        # Find or create playlist
        playlist = db.query(Playlist).filter(Playlist.name == playlist_name).first()

        if not playlist:
            # Only create non-system playlists
            if playlist_name.lower() in ["favorites", "want to watch"]:
                raise HTTPException(status_code=400, detail=f"Cannot create system playlist '{playlist_name}'")

            playlist = Playlist(name=playlist_name, is_system=False)
            db.add(playlist)
            db.commit()
            db.refresh(playlist)

        # Check if already in playlist
        existing = db.query(PlaylistItem).filter(
            PlaylistItem.playlist_id == playlist.id,
            PlaylistItem.movie_id == movie_id
        ).first()

        if existing:
            return {"status": "already_in_playlist", "playlist_id": playlist.id, "playlist_name": playlist.name}

        # Add to playlist
        item = PlaylistItem(playlist_id=playlist.id, movie_id=movie_id)
        db.add(item)
        db.commit()

        return {
            "status": "added",
            "playlist_id": playlist.id,
            "playlist_name": playlist.name,
            "movie_id": movie_id
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error adding movie {movie_id} to playlist '{playlist_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/movies/{movie_id}/playlists")
async def get_movie_playlists(movie_id: int):
    """Get all playlists containing a specific movie"""
    db = SessionLocal()
    try:
        playlists = db.query(Playlist).join(
            PlaylistItem, Playlist.id == PlaylistItem.playlist_id
        ).filter(
            PlaylistItem.movie_id == movie_id
        ).order_by(Playlist.is_system.desc(), Playlist.name.asc()).all()

        return {
            "movie_id": movie_id,
            "playlists": [
                {
                    "id": p.id,
                    "name": p.name,
                    "is_system": p.is_system
                } for p in playlists
            ]
        }
    finally:
        db.close()

# --- IMDb/Person Endpoints ---

@app.get("/api/person/{person_id}")
async def get_person_details(person_id: int):
    """Get detailed information about a person (director/actor)"""
    db = SessionLocal()
    try:
        person = db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        # Get all movies this person worked on
        credits = db.query(MovieCredit, ExternalMovie).join(
            ExternalMovie, MovieCredit.movie_id == ExternalMovie.id
        ).filter(
            MovieCredit.person_id == person_id
        ).order_by(ExternalMovie.year.desc().nullslast()).all()

        # Group by role
        movies_by_role = {}
        for credit, movie in credits:
            role = credit.category
            if role not in movies_by_role:
                movies_by_role[role] = []

            movies_by_role[role].append({
                "imdb_id": movie.imdb_id,
                "title": movie.primary_title,
                "year": movie.year,
                "rating": movie.rating,
                "genres": movie.genres,
                "character": credit.characters[0] if credit.characters else None
            })

        # Limit to top movies per role to keep response manageable
        for role in movies_by_role:
            movies_by_role[role] = movies_by_role[role][:20]  # Top 20 movies per role

        return {
            "id": person.id,
            "imdb_id": person.imdb_id,
            "name": person.primary_name,
            "birth_year": person.birth_year,
            "death_year": person.death_year,
            "movies": movies_by_role,
            "total_movies": len(credits)
        }
    finally:
        db.close()

@app.get("/api/person/{person_id}/movies")
async def get_person_movies(person_id: int, role: Optional[str] = Query(None, pattern="^(director|actor|actress|writer)$")):
    """Get all movies for a person, optionally filtered by role"""
    db = SessionLocal()
    try:
        person = db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        # Build query
        query = db.query(MovieCredit, ExternalMovie).join(
            ExternalMovie, MovieCredit.movie_id == ExternalMovie.id
        ).filter(MovieCredit.person_id == person_id)

        if role:
            query = query.filter(MovieCredit.category == role)

        credits = query.order_by(ExternalMovie.year.desc().nullslast()).all()

        movies = []
        for credit, movie in credits:
            movies.append({
                "imdb_id": movie.imdb_id,
                "title": movie.primary_title,
                "year": movie.year,
                "rating": movie.rating,
                "genres": movie.genres,
                "role": credit.category,
                "character": credit.characters[0] if credit.characters else None
            })

        return {
            "person": {
                "id": person.id,
                "imdb_id": person.imdb_id,
                "name": person.primary_name
            },
            "movies": movies,
            "total": len(movies)
        }
    finally:
        db.close()

@app.get("/api/search-people")
async def search_people(q: str, limit: int = Query(20, ge=1, le=100)):
    """Search for people by name"""
    if not q or len(q) < 2:
        return {"results": []}

    db = SessionLocal()
    try:
        query_lower = q.lower()

        # Search people with fuzzy matching
        people = db.query(Person).filter(
            Person.primary_name.ilike(f"%{q}%")
        ).order_by(Person.primary_name).limit(limit).all()

        results = []
        for person in people:
            # Get movie count
            movie_count = db.query(MovieCredit).filter(
                MovieCredit.person_id == person.id
            ).count()

            results.append({
                "id": person.id,
                "imdb_id": person.imdb_id,
                "name": person.primary_name,
                "birth_year": person.birth_year,
                "death_year": person.death_year,
                "movie_count": movie_count
            })

        return {"results": results}
    finally:
        db.close()

@app.get("/api/imdb-stats")
async def get_imdb_stats():
    """Get statistics about imported IMDb data"""
    db = SessionLocal()
    try:
        movie_count = db.query(ExternalMovie).count()
        person_count = db.query(Person).count()
        credit_count = db.query(MovieCredit).count()

        # Get year distribution
        year_stats = db.query(
            ExternalMovie.year,
            func.count(ExternalMovie.id)
        ).filter(
            ExternalMovie.year.isnot(None)
        ).group_by(ExternalMovie.year).order_by(ExternalMovie.year).all()

        year_distribution = {year: count for year, count in year_stats}

        # Get genre distribution
        genre_counts = {}
        for movie in db.query(ExternalMovie).filter(ExternalMovie.genres.isnot(None)).all():
            if movie.genres:
                for genre in movie.genres.split(','):
                    genre = genre.strip()
                    genre_counts[genre] = genre_counts.get(genre, 0) + 1

        top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:20]

        return {
            "movies": movie_count,
            "people": person_count,
            "credits": credit_count,
            "year_distribution": year_distribution,
            "top_genres": [{"genre": g, "count": c} for g, c in top_genres]
        }
    finally:
        db.close()

def load_api_keys():
    settings_path = SCRIPT_DIR / "settings.json"
    if not settings_path.exists():
        return None, None
    
    try:
        with open(settings_path, "r") as f:
            settings = json.load(f)
            return settings.get("OpenAIApiKey"), settings.get("AnthropicApiKey")
    except Exception as e:
        logger.error(f"Error loading settings.json: {e}")
        return None, None

AI_PRICING = {
    "openai": {
        "model": "GPT-5.1",
        "input_per_million": Decimal("1.25"),
        "output_per_million": Decimal("10.00")
    },
    "anthropic": {
        "model": "Claude 4.5 Sonnet",
        "input_per_million": Decimal("3.00"),
        "output_per_million": Decimal("15.00")
    }
}

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

def estimate_ai_cost(provider_key: str, input_tokens: int, output_tokens: int):
    """Calculate cents/USD plus explanatory text for a given provider."""
    rates = AI_PRICING.get(provider_key)
    if not rates:
        return None, None, "No pricing metadata configured."
    
    million = Decimal("1000000")
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts cannot be negative")
    
    input_cost = (Decimal(input_tokens) / million) * rates["input_per_million"]
    output_cost = (Decimal(output_tokens) / million) * rates["output_per_million"]
    total_usd = (input_cost + output_cost).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    total_cents = (total_usd * Decimal("100")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    
    usd_text = format(total_usd, "f")
    details = (
        f"{rates['model']} pricing: {input_tokens} input tok @ ${rates['input_per_million']}/M + "
        f"{output_tokens} output tok @ ${rates['output_per_million']}/M = ${usd_text}."
    )
    return float(total_cents), float(total_usd), details

def parse_ai_response_json(raw_text: str, interaction_id: str, provider_label: str):
    """Extract and parse the structured JSON returned by the AI provider."""
    if not raw_text:
        raise ValueError(f"No response body returned from {provider_label}")
    
    trimmed = raw_text.strip()
    fence_match = JSON_FENCE_PATTERN.search(trimmed)
    if fence_match:
        trimmed = fence_match.group(1).strip()
    
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError as exc:
        preview = trimmed[:500]
        logger.error(
            f"AI interaction {interaction_id} invalid JSON from {provider_label}: {exc}. "
            f"Payload preview: {preview}"
        )
        raise ValueError("Failed to parse AI response JSON") from exc

@app.post("/api/ai_search")
async def ai_search(request: AiSearchRequest, background_tasks: BackgroundTasks):
    openai_key, anthropic_key = load_api_keys()
    
    if request.provider == "openai" and not openai_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not found in settings.json")
    if request.provider == "anthropic" and not anthropic_key:
        raise HTTPException(status_code=400, detail="Anthropic API key not found in settings.json")

    interaction_id = str(uuid.uuid4())
    logger.info(f"AI interaction {interaction_id} received: provider={request.provider}, query={request.query}")

    prompt = f"""
    The user is asking about movies. Your goal is to return a structured JSON list of movies matching their query.
    
    User Query: "{request.query}"
    
    Guidance:
    - Do not add a comment unless you genuinely have something useful or interesting to say.
    - NEVER rephrase the user's query, acknowledge that you are responding, or summarize that you have provided an answer.
    - Leaving the comment blank is acceptable unless explicitly asked otherwise.
    
    Return JSON format:
    {{
        "comment": "Optional overall comment.",
        "movies": [
            {{
                "name": "Movie Title",
                "year": 1999,
                "comment": "Optional relevant comment."
            }}
        ]
    }}
    
    If the query is not about movies, return an empty 'movies' list.
    """
    logger.info(f"AI interaction {interaction_id} prompt payload:\n{prompt.strip()}")
    
    response_data = {"comment": "", "movies": []}
    cost_cents = None
    cost_usd = None
    cost_details = "Cost not available."
    
    try:
        if request.provider == "openai":
            provider_key = "openai"
            model_name = "gpt-5.1"
            client = openai.OpenAI(api_key=openai_key)
            logger.info(f"AI interaction {interaction_id} -> sending prompt to {provider_key} ({model_name})")
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are a helpful movie assistant. Return JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            
            content = completion.choices[0].message.content
            logger.info(f"AI interaction {interaction_id} <- response from {provider_key} ({model_name}): {content}")
            response_data = parse_ai_response_json(content, interaction_id, f"{provider_key} ({model_name})")
            
            if completion.usage:
                in_tokens = completion.usage.prompt_tokens or 0
                out_tokens = completion.usage.completion_tokens or 0
                cost_cents, cost_usd, cost_details = estimate_ai_cost(provider_key, in_tokens, out_tokens)
                usd_display = f"${cost_usd:.6f}" if cost_usd is not None else "unknown"
                logger.info(
                    f"AI interaction {interaction_id} usage: provider={provider_key}, "
                    f"input_tokens={in_tokens}, output_tokens={out_tokens}, est_cost_usd={usd_display}"
                )
            else:
                model_label = AI_PRICING[provider_key]["model"]
                cost_details = f"{model_label} pricing unavailable because the provider did not return token usage."
                logger.warning(f"AI interaction {interaction_id} missing usage data from {provider_key}")
            
        elif request.provider == "anthropic":
            provider_key = "anthropic"
            model_name = "claude-sonnet-4-5"
            client = anthropic.Anthropic(api_key=anthropic_key)
            logger.info(f"AI interaction {interaction_id} -> sending prompt to {provider_key} ({model_name})")
            message = client.messages.create(
                model=model_name,
                max_tokens=4096,
                system="You are a helpful movie assistant. Return JSON only.",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            content = message.content[0].text
            logger.info(f"AI interaction {interaction_id} <- response from {provider_key} ({model_name}): {content}")
            response_data = parse_ai_response_json(content, interaction_id, f"{provider_key} ({model_name})")
            
            if message.usage:
                in_tokens = message.usage.input_tokens or 0
                out_tokens = message.usage.output_tokens or 0
                cost_cents, cost_usd, cost_details = estimate_ai_cost(provider_key, in_tokens, out_tokens)
                usd_display = f"${cost_usd:.6f}" if cost_usd is not None else "unknown"
                logger.info(
                    f"AI interaction {interaction_id} usage: provider={provider_key}, "
                    f"input_tokens={in_tokens}, output_tokens={out_tokens}, est_cost_usd={usd_display}"
                )
            else:
                model_label = AI_PRICING[provider_key]["model"]
                cost_details = f"{model_label} pricing unavailable because the provider did not return token usage."
                logger.warning(f"AI interaction {interaction_id} missing usage data from {provider_key}")
            
    except Exception as e:
        logger.error(f"AI interaction {interaction_id} error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    # Remove duplicate movies returned by the model (ignore differing comments)
    deduped_movies = []
    seen_keys = set()
    for movie in response_data.get("movies", []):
        title = (movie.get("name") or "").strip()
        if not title:
            continue
        year = movie.get("year")
        norm_title = re.sub(r'[^\w\s]', '', title).lower()
        key = (norm_title, year)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_movies.append(movie)
    response_data["movies"] = deduped_movies
    
    # Match against DB
    db = SessionLocal()
    found_movies = []
    missing_movies = []
    
    try:
        all_movies = db.query(Movie).filter(Movie.hidden == False).all()
        
        # Pre-process DB movies for faster matching
        db_movie_map = {}
        for m in all_movies:
            norm_name = re.sub(r'[^\w\s]', '', m.name).lower().strip()
            if norm_name not in db_movie_map:
                db_movie_map[norm_name] = []
            db_movie_map[norm_name].append(m)
            
        for ai_movie in response_data.get("movies", []):
            title = ai_movie.get("name", "")
            year = ai_movie.get("year")
            comment = ai_movie.get("comment", "")
            
            if not title:
                continue

            # 1. Try exact normalized match
            norm_title = re.sub(r'[^\w\s]', '', title).lower().strip()
            candidates = db_movie_map.get(norm_title, [])
            
            match = None
            
            # 2. If no exact match, try fuzzy match
            if not candidates and db_movie_map:
                # Get best match from keys
                best_match_result = process.extractOne(norm_title, list(db_movie_map.keys()), scorer=fuzz.token_sort_ratio)
                if best_match_result:
                    best_match_name, score = best_match_result
                    if score > 85:
                        candidates = db_movie_map[best_match_name]
            
            if candidates:
                # Disambiguate by year
                matches = []
                if year and len(candidates) > 1:
                    try:
                        target_year = int(year)
                        for cand in candidates:
                            if cand.year and abs(cand.year - target_year) <= 1:
                                matches.append(cand)
                    except (ValueError, TypeError):
                        pass
                    
                    if not matches:
                        # If year doesn't match any, but names match, include all candidates (uncertain)
                        matches = candidates
                else:
                    matches = candidates
            else:
                matches = []
            
            if matches:
                # Build standardized movie cards for all matched movies
                movie_cards = build_movie_cards(db, matches)
                
                for match in matches:
                    # Get the standardized card
                    card = movie_cards.get(match.id)
                    if card:
                        # Add AI-specific fields
                        card["ai_comment"] = comment
                        found_movies.append(card)
            else:
                missing_movies.append({
                    "name": title,
                    "year": year,
                    "ai_comment": comment
                })
                
    except Exception as e:
        logger.error(f"Error processing AI results: {e}")
        response_data["comment"] += f" (Error processing results: {str(e)})"
        
    finally:
        db.close()

    cost_cents_payload = round(cost_cents, 4) if cost_cents is not None else None
    cost_usd_payload = round(cost_usd, 6) if cost_usd is not None else None
    usd_display = (
        f"${cost_usd_payload:.6f}" if isinstance(cost_usd_payload, (int, float)) else "unknown"
    )
    logger.info(
        f"AI interaction {interaction_id} completed: found={len(found_movies)}, "
        f"missing={len(missing_movies)}, est_cost_usd={usd_display}"
    )

    # Update history in background
    background_tasks.add_task(update_search_history_bg, request.query, len(deduped_movies))
        
    return {
        "comment": response_data.get("comment", ""),
        "found_movies": found_movies,
        "missing_movies": missing_movies,
        "cost_cents": cost_cents_payload,
        "cost_usd": cost_usd_payload,
        "cost_details": cost_details
    }


# =============================================================================
# VLC Optimization API
# =============================================================================

@app.get("/api/vlc/optimization/status")
async def get_vlc_optimization_status():
    """
    Get the current VLC optimization status.
    Returns info about vlcrc file, backup status, and whether optimizations are applied.
    """
    try:
        from setup.vlc_optimize import check_vlcrc_status, get_optimization_info
        
        status = check_vlcrc_status()
        info = get_optimization_info()
        
        return {
            "status": status,
            "optimization_info": info,
            "command_line_optimizations": {
                "enabled": True,
                "description": "Command-line optimizations are always applied when launching movies from Movie Searcher. These don't affect VLC when launched separately."
            }
        }
    except Exception as e:
        logger.error(f"Error checking VLC optimization status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vlc/optimization/apply")
async def apply_vlc_optimization():
    """
    Apply VLC fast-startup optimizations to vlcrc config file.
    This affects ALL VLC usage system-wide, not just Movie Searcher launches.
    Creates a backup before making changes.
    """
    try:
        from setup.vlc_optimize import apply_optimizations, check_vlcrc_status
        
        # Check current status first
        status = check_vlcrc_status()
        
        if status["is_optimized"]:
            return {
                "success": True,
                "message": "VLC configuration is already optimized.",
                "already_optimized": True
            }
        
        # Apply optimizations
        result = apply_optimizations()
        
        return result
    except Exception as e:
        logger.error(f"Error applying VLC optimization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vlc/optimization/remove")
async def remove_vlc_optimization():
    """
    Remove VLC optimizations and restore original settings.
    If a backup exists, restores from backup.
    """
    try:
        from setup.vlc_optimize import remove_optimizations
        
        result = remove_optimizations()
        
        return result
    except Exception as e:
        logger.error(f"Error removing VLC optimization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vlc/optimization/backup")
async def create_vlc_backup():
    """
    Create a backup of the current VLC configuration.
    """
    try:
        from setup.vlc_optimize import create_backup
        
        result = create_backup()
        
        return result
    except Exception as e:
        logger.error(f"Error creating VLC backup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/vlc/optimization/restore")
async def restore_vlc_backup():
    """
    Restore VLC configuration from backup.
    """
    try:
        from setup.vlc_optimize import restore_backup
        
        result = restore_backup()
        
        return result
    except Exception as e:
        logger.error(f"Error restoring VLC backup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Mount static files directory (favicon, etc.)
# Must be mounted AFTER specific routes to avoid shadowing root path
static_dir = SCRIPT_DIR / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=False), name="static")
    logger.info(f"Startup: mounted static directory at /")

# Mount movies folder as static files for image serving
movies_folder = get_movies_folder()
if movies_folder and os.path.exists(movies_folder):
    try:
        app.mount("/movies", StaticFiles(directory=movies_folder), name="movies")
        logger.info(f"Startup: mounted movies directory at /movies")
    except Exception as e:
        logger.warning(f"Failed to mount movies directory: {e}")


if __name__ == "__main__":
    from server import run_server
    run_server()
