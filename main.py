import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import anthropic

# AI Search imports
import openai
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fuzzywuzzy import fuzz, process

# Setup logging
from utils.logging import set_app_shutting_down, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# Browser URL to open on startup (set by server.py if launched via start.py)
_open_browser_url: str | None = None

# Server start time for uptime tracking
_server_start_time: float | None = None

# Database setup - import from database module
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from database import (
    AiRelatedMovies,
    AiReview,
    Config,
    ExternalMovie,
    IndexedPath,
    LaunchHistory,
    Movie,
    MovieCredit,
    MovieList,
    MovieListItem,
    MovieStatus,
    Person,
    Playlist,
    PlaylistItem,
    Rating,
    Screenshot,
    SearchHistory,
    SessionLocal,
    init_db,
    migrate_db_schema,
    remove_sample_files,
)
from models import MovieStatusEnum

# Import scanning module
from scanning import (
    clean_movie_name,
    extract_movie_screenshot,
    load_cleaning_patterns,
    process_frame_queue,
    reconcile_movie_lists,
    run_scan_async,
    scan_progress,
)

# Import video processing and subprocess management
from video_processing import (
    SCREENSHOT_DIR,
    frame_extraction_queue,
    initialize_video_processing,
    kill_all_active_subprocesses,
    shutdown_flag,
    validate_ffmpeg_path,
)
from video_processing import find_ffmpeg as find_ffmpeg_core
from video_processing import get_video_length as get_video_length_vp

# Import VLC integration
from vlc_integration import get_currently_playing_movies, launch_movie_in_vlc, vlc_optimization_router

# FastAPI app will be created after lifespan function is defined
# (temporary placeholder - will be replaced)
app = None

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()

# Initialize video processing immediately at module load time
# (lifespan function is not reliably called in all uvicorn configurations)
logger.info("Initializing video processing at module load...")
initialize_video_processing(SCRIPT_DIR)
logger.info("Video processing initialized")

# Prevent duplicate scan starts (race between concurrent requests)
scan_start_lock = threading.Lock()

SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.vtt', '.ass', '.ssa'}

# Import config functions from shared module
from config import get_local_target_folder, get_movies_folder, load_config, save_config


def filter_existing_screenshots(screenshot_objs: list) -> list:
    """
    Filter screenshot objects to only include those where the file actually exists on disk.
    Returns list of Screenshot objects with existing files.
    """
    existing = []

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


def deduplicate_movies_by_size(movies: list[Movie]) -> list[Movie]:
    """
    Deduplicate movies by name, keeping only the largest file (by size) for each unique name.
    Movies with the same name (case-insensitive) are considered duplicates.
    Returns a filtered list with only the largest file for each movie name.
    
    Use this for small result sets (e.g., search results). For large datasets with
    SQL pagination, use get_largest_movie_ids_subquery() instead.
    """
    if not movies:
        return []

    # Group movies by lowercase name
    name_groups = {}
    for movie in movies:
        key = movie.name.lower()
        if key not in name_groups:
            name_groups[key] = []
        name_groups[key].append(movie)

    # For each group, keep only the movie with the largest size
    result = []
    for group in name_groups.values():
        if len(group) == 1:
            result.append(group[0])
        else:
            # Sort by size descending (treating None as 0)
            largest = max(group, key=lambda m: m.size or 0)
            result.append(largest)

    return result


def get_largest_movie_ids_subquery(db, base_filters: list = None):
    """
    Returns a subquery of movie IDs that are the largest file for each unique movie name.
    Uses SQL window functions for efficiency - allows SQL-level pagination.
    
    Usage:
        largest_ids = get_largest_movie_ids_subquery(db, [Movie.hidden == False])
        query = db.query(Movie).filter(Movie.id.in_(largest_ids))
    """
    from sqlalchemy.sql import func as sql_func

    t0 = time.perf_counter()

    # Build subquery with ROW_NUMBER() to rank movies by size within each name group
    # COALESCE(size, 0) ensures NULL sizes are treated as 0
    filters = base_filters if base_filters else []

    ranked_subq = db.query(
        Movie.id.label('movie_id'),
        sql_func.row_number().over(
            partition_by=sql_func.lower(Movie.name),
            order_by=sql_func.coalesce(Movie.size, 0).desc()
        ).label('size_rank')
    ).filter(*filters).subquery()

    # Return subquery selecting only movie_ids with rank 1 (largest per name)
    result = db.query(ranked_subq.c.movie_id).filter(ranked_subq.c.size_rank == 1).subquery()

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"[DEDUP] Built deduplication subquery in {elapsed_ms:.2f}ms")

    return result


def build_movie_cards(db, movies: list[Movie]) -> dict:
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
        # Get regular playlists from playlist_items (excluding "Want to Watch" since it's virtual)
        playlist_rows = db.query(
            PlaylistItem.movie_id,
            Playlist.name
        ).join(
            Playlist, PlaylistItem.playlist_id == Playlist.id
        ).filter(
            PlaylistItem.movie_id.in_(movie_ids),
            Playlist.name != "Want to Watch"  # Virtual playlist, handled separately
        ).order_by(Playlist.is_system.desc(), Playlist.name).all()

        for movie_id, playlist_name in playlist_rows:
            if movie_id not in playlist_map:
                playlist_map[movie_id] = []
            playlist_map[movie_id].append(playlist_name)

        # Add "Want to Watch" for movies with that status (virtual playlist)
        for movie_id, status_info in status_map.items():
            if status_info.get("watch_status") == MovieStatusEnum.WANT_TO_WATCH.value:
                if movie_id not in playlist_map:
                    playlist_map[movie_id] = []
                # Insert at beginning since it's a system playlist
                playlist_map[movie_id].insert(0, "Want to Watch")

    # 6. Batch load review counts
    review_count_map = {}
    if movie_ids:
        from sqlalchemy import func
        review_rows = db.query(
            AiReview.movie_id,
            func.count(AiReview.id).label('count')
        ).filter(
            AiReview.movie_id.in_(movie_ids)
        ).group_by(AiReview.movie_id).all()
        review_count_map = {movie_id: count for movie_id, count in review_rows}

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
            "created": m.created.isoformat() if m.created else None,
            "size": m.size,
            "watch_status": watch_status,
            "watched": status_info.get("watched", False),
            "watched_date": status_info.get("watched_date"),
            "year": m.year,
            "has_launched": (m.id in launched_set),
            "screenshot_id": screenshot_id,
            "rating": rating_map.get(m.id),
            "playlists": playlist_map.get(m.id, []),
            "review_count": review_count_map.get(m.id, 0),
            # Include filtered screenshots list
            "screenshots": [
                {"id": s.id, "path": s.shot_path, "timestamp_seconds": s.timestamp_seconds}
                for s in screenshot_objs
            ]
        }

    return results


# Import ffmpeg setup functions from separate module
# Define lifespan function after all dependencies are available
from contextlib import asynccontextmanager

from setup.ffmpeg_setup import auto_detect_ffmpeg


@asynccontextmanager
async def lifespan(app):
    """Lifespan context manager for startup and shutdown"""
    global _server_start_time
    import time
    _server_start_time = time.time()

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
    AiSearchRequest,
    ChangeStatusRequest,
    CheckMoviesRequest,
    CleanNameTestRequest,
    ConfigRequest,
    FolderRequest,
    LaunchRequest,
    MovieListUpdateRequest,
    OpenUrlsRequest,
    PlaylistAddMovieRequest,
    PlaylistCreateRequest,
    RatingRequest,
    RelatedMoviesRequest,
    ReviewRequest,
    ScreenshotsIntervalRequest,
)

# Include VLC optimization routes
app.include_router(vlc_optimization_router)

# Include transcription routes
try:
    from transcription import transcription_router
    app.include_router(transcription_router)
    logger.info("Transcription routes loaded successfully")
except ImportError as e:
    logger.warning(f"Transcription module not available: {e}. Transcription features disabled.")

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
        import time

        from video_processing import frame_processing_active, screenshot_completion_lock, screenshot_completion_times

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
    with open(html_path, encoding="utf-8") as f:
        return f.read()

# Serve SPA for clean movie detail URLs
@app.get("/movie/{movie_id}", response_class=HTMLResponse)
@app.get("/movie/{movie_id}/{slug}", response_class=HTMLResponse)
async def serve_movie_detail_spa(movie_id: int, slug: str = ""):
    html_path = SCRIPT_DIR / "index.html"
    with open(html_path, encoding="utf-8") as f:
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

        with open(scan_log_file, encoding="utf-8") as f:
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
    language: str | None = Query("all"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200)
):
    """Search movies with pagination. Returns total for infinite scrolling."""
    t_start = time.perf_counter()
    logger.info(f"[SEARCH] q={q!r} filter={filter_type} lang={language} offset={offset} limit={limit}")

    if not q or len(q) < 2:
        return {"results": [], "total": 0}

    db = SessionLocal()
    try:
        query_lower = q.lower()

        from sqlalchemy import func, or_

        t_query = time.perf_counter()
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
        query_ms = (time.perf_counter() - t_query) * 1000
        logger.info(f"[SEARCH] DB query returned {len(movies)} movies in {query_ms:.2f}ms")

        # Deduplicate: keep only the largest file for each movie name
        t_dedup = time.perf_counter()
        movies = deduplicate_movies_by_size(movies)
        dedup_ms = (time.perf_counter() - t_dedup) * 1000
        logger.info(f"[SEARCH] Dedup reduced to {len(movies)} movies in {dedup_ms:.2f}ms")
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
        t_cards = time.perf_counter()
        movies_list = [m for _, m in page_slice]
        movie_cards = build_movie_cards(db, movies_list)
        cards_ms = (time.perf_counter() - t_cards) * 1000
        logger.info(f"[SEARCH] Built {len(movies_list)} movie cards in {cards_ms:.2f}ms")

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

        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(f"[SEARCH] Total: {total_ms:.2f}ms | results={len(results)} total={total_count}")

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
            "year": year,
            "has_launched": has_launched,
            "rating": rating,
            "imdb_data": imdb_data
        }
    finally:
        db.close()

@app.get("/api/movie/{movie_id}/same-title")
async def get_same_title_movies(movie_id: int):
    """Get other movies with the same title (different copies/versions)"""
    db = SessionLocal()
    try:
        # Get the current movie
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        # Find other movies with the same name (excluding the current one)
        same_title_movies = db.query(Movie).filter(
            Movie.name == movie.name,
            Movie.id != movie_id
        ).order_by(Movie.size.desc().nullslast()).all()

        if not same_title_movies:
            return {"movies": []}

        result = []
        for m in same_title_movies:
            result.append({
                "id": m.id,
                "name": m.name,
                "size": m.size,
                "year": m.year,
                "hidden": m.hidden,
                "path": m.path
            })

        return {"movies": result}
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

@app.get("/api/movie/{movie_id}/image")
async def get_movie_image(movie_id: int):
    """
    Serve a movie's poster/cover image by movie ID.
    
    This endpoint provides ID-based access to movie images without exposing
    disk paths in URLs. The backend resolves the movie's image_path internally.
    
    Falls back to the first screenshot if no dedicated image exists.
    """
    from fastapi.responses import FileResponse
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")

        # Try movie's dedicated image first
        if movie.image_path:
            path_obj = Path(movie.image_path)
            if path_obj.exists():
                # Determine media type based on extension
                suffix = path_obj.suffix.lower()
                media_type = {
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.png': 'image/png',
                    '.gif': 'image/gif',
                    '.webp': 'image/webp',
                }.get(suffix, 'image/jpeg')
                return FileResponse(str(path_obj), media_type=media_type)

        # Fallback to first screenshot
        shot = db.query(Screenshot).filter(Screenshot.movie_id == movie_id).order_by(Screenshot.timestamp_seconds.asc().nullslast()).first()
        if shot:
            path_obj = Path(shot.shot_path)
            if path_obj.exists():
                return FileResponse(str(path_obj), media_type='image/jpeg')

        raise HTTPException(status_code=404, detail="No image available for this movie")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving movie image for id={movie_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/screenshots/{filename:path}")
async def get_screenshot_by_filename(filename: str):
    """
    Serve screenshot by filename. This is a fallback for StaticFiles when URL encoding causes issues.
    Handles URL-decoded filenames with spaces properly.
    """
    from urllib.parse import unquote

    from fastapi.responses import FileResponse

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


@app.post("/api/open-urls")
async def open_urls_in_browser(request: OpenUrlsRequest):
    """Open URLs in the default browser (bypasses popup blockers)"""
    import time
    import webbrowser

    opened = []
    failed = []

    for url in request.urls:
        try:
            # Small delay between opens to avoid overwhelming the browser
            if opened:
                time.sleep(0.15)
            webbrowser.open(url, new=2)  # new=2 opens in a new tab if possible
            opened.append(url)
        except Exception as e:
            logger.error(f"Failed to open URL {url}: {e}")
            failed.append({"url": url, "error": str(e)})

    return {
        "status": "ok",
        "opened": len(opened),
        "failed": len(failed),
        "failed_urls": failed
    }


@app.post("/api/check-movies")
async def check_movies_in_library(request: CheckMoviesRequest):
    """Check which movies from a list are already in the library"""
    import re

    db = SessionLocal()
    try:
        results = []

        for movie_line in request.movies:
            # Try to extract year from the end of the line
            year_match = re.search(r'\b(19\d{2}|20\d{2})\s*$', movie_line.strip())
            year = int(year_match.group(1)) if year_match else None

            # Get title (everything before the year, or the whole line)
            if year_match:
                title = movie_line[:year_match.start()].strip()
            else:
                title = movie_line.strip()

            # Clean title for matching (lowercase, remove special chars)
            clean_title = re.sub(r'[^\w\s]', '', title.lower()).strip()
            clean_title_words = set(clean_title.split())

            # Search for matching movies
            found = False
            matched_movie = None

            # Query movies, optionally filtered by year
            query = db.query(Movie).filter(Movie.hidden == False)
            if year:
                query = query.filter(Movie.year == year)

            for movie in query.all():
                # Clean the movie name for comparison
                movie_clean = re.sub(r'[^\w\s]', '', movie.name.lower()).strip()
                movie_words = set(movie_clean.split())

                # Check if titles match (either exact or significant word overlap)
                if movie_clean == clean_title:
                    found = True
                    matched_movie = {"id": movie.id, "name": movie.name, "year": movie.year}
                    break
                elif len(clean_title_words) >= 2 and clean_title_words.issubset(movie_words):
                    found = True
                    matched_movie = {"id": movie.id, "name": movie.name, "year": movie.year}
                    break
                elif len(movie_words) >= 2 and movie_words.issubset(clean_title_words):
                    found = True
                    matched_movie = {"id": movie.id, "name": movie.name, "year": movie.year}
                    break

            results.append({
                "input": movie_line,
                "found": found,
                "match": matched_movie
            })

        return {"status": "ok", "results": results}
    finally:
        db.close()


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
                    "timestamp": launch.created.isoformat(),
                    "stopped_at_seconds": launch.stopped_at_seconds
                })

        return {
            "searches": searches,
            "launches": launches
        }
    finally:
        db.close()

@app.get("/api/launch-history")
async def get_launch_history(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    search: str = Query(None, description="Search by movie title"),
    date_filter: str = Query(None, description="Filter by date: today, yesterday, this_week, this_month, or all")
):
    """Get launch history with movie information"""
    from sqlalchemy import func, or_
    from datetime import timedelta
    
    db = SessionLocal()
    try:
        # Build base query
        query = db.query(LaunchHistory)
        
        # Apply date filter
        if date_filter and date_filter != "all":
            now = datetime.now()
            if date_filter == "today":
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
                query = query.filter(LaunchHistory.created >= start_date)
            elif date_filter == "yesterday":
                start_date = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
                query = query.filter(LaunchHistory.created >= start_date, LaunchHistory.created < end_date)
            elif date_filter == "this_week":
                # Start of week (Monday)
                days_since_monday = now.weekday()
                start_date = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
                query = query.filter(LaunchHistory.created >= start_date)
            elif date_filter == "this_month":
                start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                query = query.filter(LaunchHistory.created >= start_date)
        
        # Apply search filter by joining with movies table
        if search and search.strip():
            search_lower = search.lower().strip()
            # Join with Movie table to filter by name
            query = query.join(Movie, LaunchHistory.movie_id == Movie.id).filter(
                func.lower(Movie.name).contains(search_lower)
            )
        
        # Get total count (before pagination)
        total = query.count()
        
        # Query paginated launches
        offset = (page - 1) * per_page
        launches = query.order_by(
            LaunchHistory.created.desc()
        ).offset(offset).limit(per_page).all()

        if not launches:
            return {
                "launches": [],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": 0
                }
            }

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
                    "subtitle": launch.subtitle,
                    "stopped_at_seconds": launch.stopped_at_seconds
                })

        pages = (total + per_page - 1) // per_page if per_page > 0 else 0

        return {
            "launches": launches_with_info,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages
            }
        }
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
        local_target_folder = get_local_target_folder()

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
            "local_target_folder": local_target_folder or "",
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

@app.post("/api/server/restart")
async def restart_server(background_tasks: BackgroundTasks):
    """Restart the server - spawns a new process and exits current one"""
    import sys
    import time

    def do_restart():
        time.sleep(0.3)  # Give time for response to be sent
        logger.info("Server restart requested - spawning restart helper...")

        # Get the path to the restart helper script
        restart_script = SCRIPT_DIR / "scripts" / "restart_server.py"

        # Spawn the restart helper which will:
        # 1. Wait for this server to exit (port to be free)
        # 2. Start a new server
        if sys.platform == "win32":
            # On Windows, spawn in a new console window so user can see output
            import subprocess
            subprocess.Popen(
                [sys.executable, str(restart_script)],
                cwd=str(SCRIPT_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            # On Unix, spawn detached
            import subprocess
            subprocess.Popen(
                [sys.executable, str(restart_script)],
                cwd=str(SCRIPT_DIR),
                start_new_session=True,
            )

        # Brief pause then exit - the helper is waiting for us to release the port
        time.sleep(0.2)
        logger.info("Exiting current server process for restart...")
        os._exit(0)

    background_tasks.add_task(do_restart)
    return {"status": "restarting", "message": "Server is restarting..."}

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

    # Update local target folder if provided
    if request.local_target_folder is not None:
        if not request.local_target_folder:
            # Remove local target folder from config
            config.pop("local_target_folder", None)
            save_config(config)
            logger.info("Removed local_target_folder from config")
        else:
            # Normalize path (handle both / and \)
            local_folder_path = request.local_target_folder.strip()

            # Validate that path is absolute before processing
            if os.name == 'nt':  # Windows
                is_drive_path = local_folder_path and len(local_folder_path) >= 3 and local_folder_path[1] == ':' and local_folder_path[2] in ['\\', '/']
                is_unc_path = local_folder_path and local_folder_path.startswith('\\\\')
                if not (is_drive_path or is_unc_path):
                    error_msg = f"Local target path must be absolute (start with drive letter like C:\\ or D:\\): '{local_folder_path}'"
                    logger.error(error_msg)
                    raise HTTPException(status_code=400, detail=error_msg)
                local_folder_path = local_folder_path.replace('/', '\\')
                if not local_folder_path.startswith('\\\\'):
                    local_folder_path = local_folder_path.replace('\\\\', '\\')
                if local_folder_path.endswith('\\') and len(local_folder_path) > 3:
                    local_folder_path = local_folder_path.rstrip('\\')
            else:
                if not (local_folder_path and local_folder_path.startswith('/')):
                    error_msg = f"Local target path must be absolute (start with /): '{local_folder_path}'"
                    logger.error(error_msg)
                    raise HTTPException(status_code=400, detail=error_msg)

            logger.info(f"Local target folder normalized path: '{local_folder_path}'")

            local_path_obj = Path(local_folder_path)
            local_path_obj = local_path_obj.absolute()

            if not local_path_obj.exists():
                # Try to create the directory
                try:
                    local_path_obj.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created local target folder: '{local_path_obj}'")
                except Exception as e:
                    logger.error(f"Failed to create local target folder: {e}")
                    raise HTTPException(status_code=400, detail=f"Cannot create local target folder: '{local_folder_path}': {e}")

            if not local_path_obj.is_dir():
                error_msg = f"Local target path is not a directory: '{local_folder_path}'"
                logger.error(error_msg)
                raise HTTPException(status_code=400, detail=error_msg)

            absolute_local_path_str = str(local_path_obj)
            config["local_target_folder"] = absolute_local_path_str
            save_config(config)
            logger.info(f"Saved local_target_folder to config: {absolute_local_path_str}")

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

    return {"status": "updated", "movies_folder": config.get("movies_folder", ""), "local_target_folder": config.get("local_target_folder", ""), "settings": config}

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

# Global tracking for copy operations in progress
_copy_operations = {}  # {movie_id: {"status": str, "progress": float, "message": str, "copied_files": list}}

@app.post("/api/movie/{movie_id}/copy-to-local")
async def copy_movie_to_local(movie_id: int):
    """Copy movie (video file + subtitles) to local target folder.
    
    Creates a folder named after the cleaned movie name in the local target folder,
    then copies the video file and all subtitle files.
    """
    global _copy_operations

    # Check if copy is already in progress for this movie
    if movie_id in _copy_operations and _copy_operations[movie_id].get("status") == "in_progress":
        return {
            "status": "in_progress",
            "message": "Copy already in progress",
            "progress": _copy_operations[movie_id].get("progress", 0)
        }

    # Check local_target_folder is configured
    local_target = get_local_target_folder()
    if not local_target:
        raise HTTPException(status_code=400, detail="Local target folder not configured. Please set it in Settings first.")

    local_target_path = Path(local_target)
    if not local_target_path.exists():
        raise HTTPException(status_code=400, detail=f"Local target folder does not exist: {local_target}")

    # Get movie from database
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found: {movie_id}")

        movie_path = Path(movie.path)
        if not movie_path.exists():
            raise HTTPException(status_code=404, detail=f"Movie file not found: {movie.path}")

        # Get the source folder (parent of the video file)
        source_folder = movie_path.parent

        # Generate target folder name using the cleaned movie name
        from scanning import clean_movie_name
        cleaned_name, year = clean_movie_name(movie.name)
        # Sanitize the folder name (remove characters not allowed in folder names)
        folder_name = cleaned_name
        if year:
            folder_name = f"{cleaned_name} ({year})"
        # Remove or replace invalid characters for Windows folder names
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            folder_name = folder_name.replace(char, '_')
        folder_name = folder_name.strip('. ')  # Remove leading/trailing dots and spaces

        # Create target folder path
        target_folder = local_target_path / folder_name
        target_video_path = target_folder / movie_path.name

        # Check if already copied (video file exists and same size)
        if target_video_path.exists():
            source_size = movie_path.stat().st_size
            target_size = target_video_path.stat().st_size
            if source_size == target_size:
                return {
                    "status": "already_copied",
                    "message": f"Movie already exists in local folder: {folder_name}",
                    "target_folder": str(target_folder)
                }

        # Initialize progress tracking
        _copy_operations[movie_id] = {
            "status": "in_progress",
            "progress": 0,
            "message": "Starting copy...",
            "copied_files": []
        }

        # Create target folder
        try:
            target_folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            _copy_operations[movie_id] = {"status": "error", "message": f"Failed to create folder: {e}"}
            raise HTTPException(status_code=500, detail=f"Failed to create target folder: {e}")

        # Find subtitle files to copy
        subtitle_extensions = {'.srt', '.sub', '.vtt', '.ass', '.ssa'}
        subtitle_files = []

        # Check source folder for subtitles
        for item in source_folder.iterdir():
            if item.is_file() and item.suffix.lower() in subtitle_extensions:
                subtitle_files.append(item)

        # Check "subs" subfolder (case-insensitive)
        for item in source_folder.iterdir():
            if item.is_dir() and item.name.lower() == "subs":
                for sub_item in item.iterdir():
                    if sub_item.is_file() and sub_item.suffix.lower() in subtitle_extensions:
                        subtitle_files.append(sub_item)

        # Files to copy: video file + all subtitle files
        files_to_copy = [(movie_path, target_folder / movie_path.name)]
        for sub_file in subtitle_files:
            # Flatten subs folder - copy all subtitles to root of target folder
            files_to_copy.append((sub_file, target_folder / sub_file.name))

        total_size = sum(f[0].stat().st_size for f in files_to_copy)
        copied_size = 0
        copied_files = []

        _copy_operations[movie_id]["message"] = f"Copying {len(files_to_copy)} file(s)..."

        try:
            for src_path, dest_path in files_to_copy:
                file_size = src_path.stat().st_size

                _copy_operations[movie_id]["message"] = f"Copying: {src_path.name}"
                logger.info(f"Copying {src_path} -> {dest_path}")

                # Skip if already exists with same size
                if dest_path.exists() and dest_path.stat().st_size == file_size:
                    copied_size += file_size
                    _copy_operations[movie_id]["progress"] = (copied_size / total_size) * 100 if total_size > 0 else 100
                    copied_files.append(str(dest_path.name))
                    continue

                # Copy with progress tracking (chunked for large files)
                chunk_size = 1024 * 1024 * 8  # 8MB chunks
                copied_bytes = 0

                with open(src_path, 'rb') as src_file, open(dest_path, 'wb') as dest_file:
                    while True:
                        chunk = src_file.read(chunk_size)
                        if not chunk:
                            break
                        dest_file.write(chunk)
                        copied_bytes += len(chunk)

                        # Update progress
                        current_progress = (copied_size + copied_bytes) / total_size * 100 if total_size > 0 else 100
                        _copy_operations[movie_id]["progress"] = current_progress

                # Preserve file metadata (times, etc.)
                shutil.copystat(str(src_path), str(dest_path))

                copied_size += file_size
                copied_files.append(str(dest_path.name))

            _copy_operations[movie_id] = {
                "status": "complete",
                "progress": 100,
                "message": f"Copied {len(copied_files)} file(s) to {folder_name}",
                "copied_files": copied_files,
                "target_folder": str(target_folder)
            }

            return {
                "status": "complete",
                "message": f"Successfully copied {len(copied_files)} file(s)",
                "copied_files": copied_files,
                "target_folder": str(target_folder)
            }

        except Exception as e:
            error_msg = f"Error copying files: {e}"
            logger.error(error_msg, exc_info=True)
            _copy_operations[movie_id] = {"status": "error", "progress": 0, "message": error_msg}
            raise HTTPException(status_code=500, detail=error_msg)

    finally:
        db.close()

@app.get("/api/movie/{movie_id}/copy-status")
async def get_copy_status(movie_id: int):
    """Get the status of a copy operation for a movie."""
    if movie_id in _copy_operations:
        return _copy_operations[movie_id]

    # Check if already copied
    local_target = get_local_target_folder()
    if not local_target:
        return {"status": "not_configured", "message": "Local target folder not configured"}

    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            return {"status": "error", "message": "Movie not found"}

        movie_path = Path(movie.path)
        if not movie_path.exists():
            return {"status": "error", "message": "Movie file not found"}

        # Check if target folder exists with the video
        from scanning import clean_movie_name
        cleaned_name, year = clean_movie_name(movie.name)
        folder_name = cleaned_name
        if year:
            folder_name = f"{cleaned_name} ({year})"
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            folder_name = folder_name.replace(char, '_')
        folder_name = folder_name.strip('. ')

        target_folder = Path(local_target) / folder_name
        target_video = target_folder / movie_path.name

        if target_video.exists():
            source_size = movie_path.stat().st_size
            target_size = target_video.stat().st_size
            if source_size == target_size:
                return {"status": "already_copied", "message": "Already copied", "target_folder": str(target_folder)}

        return {"status": "not_copied", "message": "Not yet copied"}
    finally:
        db.close()

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

@app.get("/api/stats/launches")
async def get_launch_stats(limit: int = 50):
    """Get recent VLC launch performance statistics"""
    from models import Stat
    db = SessionLocal()
    try:
        # Get recent launch time stats
        stats = db.query(Stat).filter(
            Stat.stat_type == 'vlc_launch_time_ms'
        ).order_by(Stat.created.desc()).limit(limit).all()

        launch_times = []
        for stat in stats:
            extra = {}
            if stat.extra_data:
                try:
                    extra = json.loads(stat.extra_data)
                except:
                    pass

            # Get timing breakdown if available
            timing = extra.get("timing", {})

            launch_times.append({
                "id": stat.id,
                "time_ms": round(stat.value, 1),
                "movie_id": stat.movie_id,
                "movie_name": extra.get("movie_name", "Unknown"),
                "had_subtitle": extra.get("had_subtitle", False),
                "created": stat.created.isoformat() if stat.created else None,
                "timing": {
                    "prep": round(timing.get("prep", 0), 1),
                    "close_existing": round(timing.get("close_existing", 0), 1),
                    "popen": round(timing.get("popen", 0), 1),
                    "health_check": round(timing.get("health_check", 0), 1),
                    "window_focus": round(timing.get("window_focus", 0), 1),
                }
            })

        # Calculate summary stats
        if launch_times:
            times = [lt["time_ms"] for lt in launch_times]
            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)
        else:
            avg_time = min_time = max_time = 0

        return {
            "launches": launch_times,
            "summary": {
                "count": len(launch_times),
                "avg_ms": round(avg_time, 1),
                "min_ms": round(min_time, 1),
                "max_ms": round(max_time, 1),
                "target_ms": 50  # Our optimization goal
            }
        }
    finally:
        db.close()

@app.get("/api/health")
async def get_health():
    """Get server health and uptime information"""
    import time

    uptime_seconds = 0
    if _server_start_time:
        uptime_seconds = time.time() - _server_start_time

    # Format uptime as human readable
    uptime_minutes = int(uptime_seconds // 60)
    uptime_hours = uptime_minutes // 60
    uptime_days = uptime_hours // 24

    if uptime_days > 0:
        uptime_str = f"{uptime_days}d {uptime_hours % 24}h"
    elif uptime_hours > 0:
        uptime_str = f"{uptime_hours}h {uptime_minutes % 60}m"
    else:
        uptime_str = f"{uptime_minutes}m"

    return {
        "status": "healthy",
        "uptime_seconds": round(uptime_seconds, 1),
        "uptime_formatted": uptime_str,
        "server_start_time": _server_start_time
    }

@app.get("/api/language-counts")
async def get_language_counts():
    """Get counts of movies by audio language (from movie_audio)"""
    db = SessionLocal()
    try:
        # Count distinct movies per audio language code from movie_audio
        from sqlalchemy import distinct, or_

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

        # Track renamed movies for movie list reconciliation
        renamed_movies = []

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
                    # Track for reconciliation
                    renamed_movies.append({
                        'id': movie.id,
                        'name': cleaned_name,
                        'year': year
                    })
            except Exception as e:
                logger.warning(f"Error re-cleaning name for movie {movie.id} ({movie.path}): {e}")
                continue

        db.commit()

        # Reconcile movie lists with renamed movies
        reconcile_result = {"matched_count": 0, "lists_updated": 0}
        if renamed_movies:
            try:
                reconcile_result = reconcile_movie_lists(db, renamed_movies)
                if reconcile_result["matched_count"] > 0:
                    logger.info(f"Movie list reconciliation: {reconcile_result['matched_count']} items matched across {reconcile_result['lists_updated']} lists")
            except Exception as e:
                logger.error(f"Error reconciling movie lists: {e}")

        return {
            "status": "complete",
            "total": total,
            "updated": updated,
            "message": f"Re-cleaned {updated} of {total} movie names",
            "lists_reconciled": reconcile_result["matched_count"]
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
    letter: str | None = Query(None, pattern="^[A-Z#]$"),
    year: int | None = Query(None, ge=1900, le=2035),
    decade: int | None = Query(None, ge=1900, le=2030),
    language: str | None = Query("all"),
    no_year: bool | None = Query(None)
):
    """Get all movies for exploration view with pagination and filters"""
    t_start = time.perf_counter()
    logger.info(f"[EXPLORE] page={page} per_page={per_page} filter={filter_type} letter={letter} year={year} decade={decade} lang={language}")

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

        # SQL-level deduplication: only include the largest file for each movie name
        # Build base filters for the deduplication subquery
        t_dedup = time.perf_counter()
        dedup_base_filters = [
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
        ]
        largest_ids_subq = get_largest_movie_ids_subquery(db, dedup_base_filters)
        movie_q = movie_q.filter(Movie.id.in_(largest_ids_subq))

        # Apply ordering and SQL-level pagination (efficient!)
        if filter_type == "newest":
            movie_q = movie_q.order_by(Movie.created.desc())
        else:
            movie_q = movie_q.order_by(Movie.name.asc())

        t_query = time.perf_counter()
        total = movie_q.count()
        rows = movie_q.offset((page - 1) * per_page).limit(per_page).all()
        query_ms = (time.perf_counter() - t_query) * 1000
        logger.info(f"[EXPLORE] DB query returned {len(rows)} movies (total={total}) in {query_ms:.2f}ms")

        # Build movie cards
        t_cards = time.perf_counter()
        movie_cards = build_movie_cards(db, rows)
        result_movies = [movie_cards[m.id] for m in rows]
        cards_ms = (time.perf_counter() - t_cards) * 1000
        logger.info(f"[EXPLORE] Built {len(rows)} movie cards in {cards_ms:.2f}ms")

        # Compute counts using efficient SQL GROUP BY (not Python iteration)
        # Base filter for counts: same as main query but without letter/year/decade filters
        base_filter = [
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
        ]

        # Apply watch filter to counts
        watch_filter = []
        if filter_type == "watched":
            exists_watch = db.query(MovieStatus.id).filter(
                (MovieStatus.movie_id == Movie.id) & (MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value)
            ).exists()
            watch_filter = [exists_watch]
        elif filter_type == "unwatched":
            exists_watch = db.query(MovieStatus.id).filter(
                (MovieStatus.movie_id == Movie.id) & (MovieStatus.movieStatus == MovieStatusEnum.WATCHED.value)
            ).exists()
            watch_filter = [~exists_watch]
        elif filter_type == "newest" and newest_ids_subq is not None:
            watch_filter = [Movie.id.in_(newest_ids_subq)]

        all_filters = base_filter + watch_filter

        # Letter counts via SQL GROUP BY on first character
        letter_counts_rows = db.query(
            func.upper(func.substr(Movie.name, 1, 1)).label('letter'),
            func.count(Movie.id)
        ).filter(*all_filters).group_by(func.upper(func.substr(Movie.name, 1, 1))).all()

        letter_counts = {}
        for lt, cnt in letter_counts_rows:
            if lt and lt.isalpha():
                letter_counts[lt] = cnt
            else:
                letter_counts['#'] = letter_counts.get('#', 0) + cnt

        # Year counts via SQL GROUP BY
        year_counts_rows = db.query(
            Movie.year,
            func.count(Movie.id)
        ).filter(*all_filters, Movie.year.isnot(None)).group_by(Movie.year).all()
        year_counts = {yr: cnt for yr, cnt in year_counts_rows}

        # Decade counts via SQL GROUP BY - use cast to ensure integer division
        from sqlalchemy import Integer, cast
        decade_expr = cast(Movie.year / 10, Integer) * 10
        decade_counts_rows = db.query(
            decade_expr.label('decade'),
            func.count(Movie.id)
        ).filter(*all_filters, Movie.year.isnot(None)).group_by(decade_expr).all()
        decade_counts = {int(dec): cnt for dec, cnt in decade_counts_rows if dec}

        # No year count
        no_year_count = db.query(func.count(Movie.id)).filter(*all_filters, Movie.year == None).scalar() or 0

        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(f"[EXPLORE] Total: {total_ms:.2f}ms | movies={len(result_movies)} total={total}")

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
    t_start = time.perf_counter()
    logger.info(f"[RANDOM] count={count}")

    db = SessionLocal()
    try:
        from sqlalchemy import or_

        # SQL-level deduplication: only include largest file per movie name
        dedup_base_filters = [
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
        ]
        largest_ids_subq = get_largest_movie_ids_subquery(db, dedup_base_filters)

        # Query deduplicated movies using SQL random ordering
        # SQLite's RANDOM() is efficient for small result sets
        t_query = time.perf_counter()
        movie_q = db.query(Movie).filter(
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False,
            Movie.id.in_(largest_ids_subq)
        ).order_by(func.random()).limit(count)

        random_movies = movie_q.all()
        query_ms = (time.perf_counter() - t_query) * 1000
        logger.info(f"[RANDOM] DB query returned {len(random_movies)} movies in {query_ms:.2f}ms")

        if not random_movies:
            return {"results": []}

        # Build standardized movie cards
        t_cards = time.perf_counter()
        movie_cards = build_movie_cards(db, random_movies)
        cards_ms = (time.perf_counter() - t_cards) * 1000
        logger.info(f"[RANDOM] Built {len(random_movies)} movie cards in {cards_ms:.2f}ms")

        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(f"[RANDOM] Total: {total_ms:.2f}ms")

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
    t_start = time.perf_counter()
    logger.info("[ALL-MOVIES] Fetching all movies")

    db = SessionLocal()
    try:
        from sqlalchemy import or_

        # SQL-level deduplication: only include largest file per movie name
        dedup_base_filters = [
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False
        ]
        largest_ids_subq = get_largest_movie_ids_subquery(db, dedup_base_filters)

        # Query all deduplicated movies
        t_query = time.perf_counter()
        movies = db.query(Movie).filter(
            or_(Movie.length == None, Movie.length >= 60),
            Movie.hidden == False,
            Movie.id.in_(largest_ids_subq)
        ).order_by(Movie.name.asc()).all()
        query_ms = (time.perf_counter() - t_query) * 1000
        logger.info(f"[ALL-MOVIES] DB query returned {len(movies)} movies in {query_ms:.2f}ms")

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

        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(f"[ALL-MOVIES] Total: {total_ms:.2f}ms | count={len(result)}")

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
            # "Want to Watch" is a virtual playlist - count from movie_status table
            if playlist.name == "Want to Watch":
                movie_count = db.query(MovieStatus).join(Movie, MovieStatus.movie_id == Movie.id).filter(
                    MovieStatus.movieStatus == MovieStatusEnum.WANT_TO_WATCH.value,
                    Movie.hidden == False
                ).count()
            else:
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

        # "Want to Watch" is a virtual playlist - query from movie_status table
        if playlist.name == "Want to Watch":
            # Query movies with want_to_watch status
            from sqlalchemy import case
            item_query = db.query(MovieStatus, Movie).join(
                Movie, MovieStatus.movie_id == Movie.id
            ).filter(
                MovieStatus.movieStatus == MovieStatusEnum.WANT_TO_WATCH.value,
                Movie.hidden == False
            )

            # Apply sorting
            if sort == "name":
                item_query = item_query.order_by(Movie.name.asc())
            elif sort == "year":
                item_query = item_query.order_by(
                    case((Movie.year.is_(None), 1), else_=0),
                    Movie.year.desc()
                )
            else:  # date_added - use status updated time
                item_query = item_query.order_by(MovieStatus.updated.desc())

            # Get total count
            total = item_query.count()

            # Apply pagination
            items = item_query.offset((page - 1) * per_page).limit(per_page).all()

            # Build movie cards
            movies_list = [movie for _, movie in items]
            movie_cards = build_movie_cards(db, movies_list)

            # Build response
            movies = []
            for status, movie in items:
                card = dict(movie_cards.get(movie.id, {}))
                if card:
                    card["added_at"] = status.updated.isoformat() if status.updated else None
                    movies.append(card)
        else:
            # Regular playlist - query from playlist_items table
            from sqlalchemy import case
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
                item_query = item_query.order_by(
                    case((Movie.year.is_(None), 1), else_=0),
                    Movie.year.desc()
                )
            else:  # date_added
                item_query = item_query.order_by(PlaylistItem.added_at.desc())

            # Get total count
            total = item_query.count()

            # Apply pagination
            items = item_query.offset((page - 1) * per_page).limit(per_page).all()

            # Build movie cards
            movies_list = [movie for _, movie in items]
            movie_cards = build_movie_cards(db, movies_list)

            # Build response
            movies = []
            for item, movie in items:
                card = dict(movie_cards.get(movie.id, {}))
                if card:
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

        # "Want to Watch" is virtual - set movie status instead of adding to playlist_items
        if playlist.name == "Want to Watch":
            movie_status = db.query(MovieStatus).filter(MovieStatus.movie_id == movie.id).first()
            if movie_status and movie_status.movieStatus == MovieStatusEnum.WANT_TO_WATCH.value:
                raise HTTPException(status_code=400, detail="Movie already in playlist")

            if movie_status:
                movie_status.movieStatus = MovieStatusEnum.WANT_TO_WATCH.value
            else:
                movie_status = MovieStatus(movie_id=movie.id, movieStatus=MovieStatusEnum.WANT_TO_WATCH.value)
                db.add(movie_status)
            db.commit()
            return {"status": "added", "playlist_id": playlist_id, "movie_id": request.movie_id}

        # Regular playlist - add to playlist_items
        existing = db.query(PlaylistItem).filter(
            PlaylistItem.playlist_id == playlist_id,
            PlaylistItem.movie_id == request.movie_id
        ).first()

        if existing:
            raise HTTPException(status_code=400, detail="Movie already in playlist")

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
        # Check if this is the "Want to Watch" virtual playlist
        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")

        if playlist.name == "Want to Watch":
            # Virtual playlist - clear the want_to_watch status
            movie_status = db.query(MovieStatus).filter(
                MovieStatus.movie_id == movie_id,
                MovieStatus.movieStatus == MovieStatusEnum.WANT_TO_WATCH.value
            ).first()

            if not movie_status:
                raise HTTPException(status_code=404, detail="Movie not found in playlist")

            # Delete the status entry (resets to no status)
            db.delete(movie_status)
            db.commit()
            return {"status": "removed", "playlist_id": playlist_id, "movie_id": movie_id}

        # Regular playlist - remove from playlist_items
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

        # "Want to Watch" is virtual - set movie status instead
        if playlist.name == "Want to Watch":
            movie_status = db.query(MovieStatus).filter(MovieStatus.movie_id == movie_id).first()
            if movie_status and movie_status.movieStatus == MovieStatusEnum.WANT_TO_WATCH.value:
                return {"status": "already_in_playlist", "playlist_id": playlist.id, "playlist_name": playlist.name}

            if movie_status:
                movie_status.movieStatus = MovieStatusEnum.WANT_TO_WATCH.value
            else:
                movie_status = MovieStatus(movie_id=movie_id, movieStatus=MovieStatusEnum.WANT_TO_WATCH.value)
                db.add(movie_status)
            db.commit()
            return {
                "status": "added",
                "playlist_id": playlist.id,
                "playlist_name": playlist.name,
                "movie_id": movie_id
            }

        # Regular playlist - check if already in playlist
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
        # Get regular playlists from playlist_items (excluding "Want to Watch" since it's virtual)
        playlists = db.query(Playlist).join(
            PlaylistItem, Playlist.id == PlaylistItem.playlist_id
        ).filter(
            PlaylistItem.movie_id == movie_id,
            Playlist.name != "Want to Watch"  # Exclude since we handle it separately
        ).order_by(Playlist.is_system.desc(), Playlist.name.asc()).all()

        result = [{"id": p.id, "name": p.name, "is_system": p.is_system} for p in playlists]

        # Check if movie has want_to_watch status (virtual "Want to Watch" playlist)
        movie_status = db.query(MovieStatus).filter(
            MovieStatus.movie_id == movie_id,
            MovieStatus.movieStatus == MovieStatusEnum.WANT_TO_WATCH.value
        ).first()

        if movie_status:
            # Get the "Want to Watch" playlist entry to include its ID
            wtw_playlist = db.query(Playlist).filter(Playlist.name == "Want to Watch").first()
            if wtw_playlist:
                # Insert at the beginning (system playlists first)
                result.insert(0, {"id": wtw_playlist.id, "name": wtw_playlist.name, "is_system": wtw_playlist.is_system})

        return {
            "movie_id": movie_id,
            "playlists": result
        }
    finally:
        db.close()

@app.get("/api/movies/{movie_id}/lists")
async def get_movie_lists(movie_id: int):
    """Get all AI-generated movie lists containing a specific movie"""
    db = SessionLocal()
    try:
        # Find all non-deleted lists that contain this movie, including the list item's ai_comment
        list_items = db.query(MovieList, MovieListItem).join(
            MovieListItem, MovieList.id == MovieListItem.movie_list_id
        ).filter(
            MovieListItem.movie_id == movie_id,
            MovieList.is_deleted == False
        ).order_by(MovieList.is_favorite.desc(), MovieList.created.desc()).all()

        result = [{
            "id": lst.id,
            "slug": lst.slug,
            "title": lst.title,
            "is_favorite": lst.is_favorite,
            "movies_count": lst.movies_count,
            "comment": lst.comment,
            "ai_comment": item.ai_comment  # Comment specific to this movie
        } for lst, item in list_items]

        return {
            "movie_id": movie_id,
            "lists": result
        }
    finally:
        db.close()

# --- AI Review Endpoints ---

@app.get("/api/movie/{movie_id}/reviews")
async def get_movie_reviews(movie_id: int):
    """Get all AI-generated reviews for a specific movie"""
    db = SessionLocal()
    try:
        # Verify movie exists
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Get all reviews for this movie, ordered by created DESC
        reviews = db.query(AiReview).filter(
            AiReview.movie_id == movie_id
        ).order_by(AiReview.created.desc()).all()
        
        return {
            "movie_id": movie_id,
            "reviews": [
                {
                    "id": review.id,
                    "prompt_type": review.prompt_type,
                    "model_provider": review.model_provider,
                    "model_name": review.model_name,
                    "response_text": review.response_text,
                    "further_instructions": review.further_instructions,
                    "cost_usd": review.cost_usd,
                    "created": review.created.isoformat() if review.created else None
                }
                for review in reviews
            ]
        }
    finally:
        db.close()


@app.post("/api/movie/{movie_id}/review")
async def generate_movie_review(movie_id: int, request: ReviewRequest):
    """Generate an AI review for a movie using SSE streaming"""
    openai_key, anthropic_key = load_api_keys()
    
    if request.provider == "openai" and not openai_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not found in settings.json")
    if request.provider == "anthropic" and not anthropic_key:
        raise HTTPException(status_code=400, detail="Anthropic API key not found in settings.json")
    
    interaction_id = str(uuid.uuid4())
    
    def generate_sse():
        """Generator that yields SSE events with progress updates."""
        
        def send_progress(step: int, total: int, message: str):
            event_data = json.dumps({"type": "progress", "step": step, "total": total, "message": message})
            return f"data: {event_data}\n\n"
        
        def send_result(result: dict):
            event_data = json.dumps({"type": "result", **result})
            return f"data: {event_data}\n\n"
        
        def send_error(error_msg: str):
            event_data = json.dumps({"type": "error", "detail": error_msg})
            return f"data: {event_data}\n\n"
        
        db = SessionLocal()
        try:
            # Step 1: Get movie details
            yield send_progress(1, 3, "Preparing query for AI...")
            
            movie = db.query(Movie).filter(Movie.id == movie_id).first()
            if not movie:
                yield send_error("Movie not found")
                return
            
            # Try to get director from IMDb data
            director = None
            try:
                from fuzzywuzzy import fuzz
                from fuzzywuzzy.process import extractOne
                
                search_title = movie.name.lower()
                imdb_movies = db.query(ExternalMovie).all()
                
                if imdb_movies:
                    imdb_titles = {}
                    for imdb_movie in imdb_movies:
                        title_key = imdb_movie.primary_title.lower()
                        if imdb_movie.year:
                            title_key += f" ({imdb_movie.year})"
                        imdb_titles[title_key] = imdb_movie
                    
                    if imdb_titles:
                        best_match = extractOne(search_title, list(imdb_titles.keys()), scorer=fuzz.token_sort_ratio)
                        if best_match and best_match[1] > 85:
                            imdb_movie = imdb_titles[best_match[0]]
                            
                            # Get directors
                            credits = db.query(MovieCredit, Person).join(
                                Person, MovieCredit.person_id == Person.id
                            ).filter(
                                MovieCredit.movie_id == imdb_movie.id,
                                MovieCredit.category == 'director'
                            ).all()
                            
                            if credits:
                                director_names = [person.primary_name for _, person in credits]
                                director = ", ".join(director_names)
            except Exception as e:
                logger.warning(f"Could not fetch director info: {e}")
            
            # Build prompt
            # Note: movie.length is stored in SECONDS in the database
            prompt = build_review_prompt(
                movie_name=movie.name,
                year=movie.year,
                director=director,
                length_seconds=movie.length,  # movie.length is in seconds
                further_instructions=request.further_instructions
            )
            
            logger.info(f"Review interaction {interaction_id} prompt payload:\n{prompt.strip()}")
            
            # Step 2: Call AI
            provider_display = "OpenAI" if request.provider == "openai" else "Anthropic"
            yield send_progress(2, 3, f"Waiting for {provider_display} response...")
            
            response_text = ""
            cost_cents = None
            cost_usd = None
            cost_details = "Cost not available."
            model_name = None
            
            try:
                if request.provider == "openai":
                    provider_key = "openai"
                    model_config = next((m for m in AI_MODELS if m["provider"] == "openai"), None)
                    model_name = model_config["model_id"] if model_config else "gpt-5.1"
                    client = openai.OpenAI(api_key=openai_key)
                    logger.info(f"Review interaction {interaction_id} -> sending prompt to {provider_key} ({model_name})")
                    
                    completion = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": "You are a helpful movie review assistant."},
                            {"role": "user", "content": prompt}
                        ]
                    )
                    
                    response_text = completion.choices[0].message.content
                    logger.info(f"Review interaction {interaction_id} <- response from {provider_key} ({model_name}): {response_text[:200]}...")
                    
                    if completion.usage:
                        in_tokens = completion.usage.prompt_tokens or 0
                        out_tokens = completion.usage.completion_tokens or 0
                        cost_cents, cost_usd, cost_details = estimate_ai_cost(provider_key, in_tokens, out_tokens)
                    else:
                        model_label = AI_PRICING[provider_key]["model"]
                        cost_details = f"{model_label} pricing unavailable because the provider did not return token usage."
                
                elif request.provider == "anthropic":
                    provider_key = "anthropic"
                    model_config = next((m for m in AI_MODELS if m["provider"] == "anthropic"), None)
                    model_name = model_config["model_id"] if model_config else "claude-opus-4-5-20251101"
                    client = anthropic.Anthropic(api_key=anthropic_key)
                    logger.info(f"Review interaction {interaction_id} -> sending prompt to {provider_key} ({model_name})")
                    
                    message = client.messages.create(
                        model=model_name,
                        max_tokens=4096,
                        system="You are a helpful movie review assistant.",
                        messages=[
                            {"role": "user", "content": prompt}
                        ]
                    )
                    
                    response_text = message.content[0].text
                    logger.info(f"Review interaction {interaction_id} <- response from {provider_key} ({model_name}): {response_text[:200]}...")
                    
                    if message.usage:
                        in_tokens = message.usage.input_tokens or 0
                        out_tokens = message.usage.output_tokens or 0
                        cost_cents, cost_usd, cost_details = estimate_ai_cost(provider_key, in_tokens, out_tokens)
                    else:
                        model_label = AI_PRICING[provider_key]["model"]
                        cost_details = f"{model_label} pricing unavailable because the provider did not return token usage."
            
            except Exception as e:
                logger.error(f"Review interaction {interaction_id} error: {e}")
                yield send_error(str(e))
                return
            
            # Step 3: Save review
            yield send_progress(3, 3, "Saving review...")
            
            cost_usd_payload = round(cost_usd, 6) if cost_usd is not None else None
            
            review = AiReview(
                movie_id=movie_id,
                prompt_text=prompt,
                model_provider=request.provider,
                model_name=model_name or "unknown",
                response_text=response_text,
                prompt_type="default",
                further_instructions=request.further_instructions,
                cost_usd=cost_usd_payload,
                user_id=None  # Blank for now
            )
            db.add(review)
            db.commit()
            db.refresh(review)
            
            logger.info(f"Review interaction {interaction_id} completed: review_id={review.id}, est_cost_usd=${cost_usd_payload:.6f}" if cost_usd_payload else "unknown")
            
            yield send_result({
                "review_id": review.id,
                "response_text": response_text,
                "cost_usd": cost_usd_payload,
                "cost_details": cost_details,
                "model_provider": request.provider,
                "model_name": model_name
            })
        
        except Exception as e:
            logger.error(f"Review generation error: {e}")
            yield send_error(str(e))
        finally:
            db.close()
    
    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.delete("/api/movie/{movie_id}/review/{review_id}")
async def delete_movie_review(movie_id: int, review_id: int):
    """Delete an AI-generated review"""
    db = SessionLocal()
    try:
        review = db.query(AiReview).filter(
            AiReview.id == review_id,
            AiReview.movie_id == movie_id
        ).first()
        
        if not review:
            raise HTTPException(status_code=404, detail="Review not found")
        
        db.delete(review)
        db.commit()
        
        return {"status": "deleted", "review_id": review_id}
    finally:
        db.close()


@app.get("/api/movies/reviews-count")
async def get_reviews_count(movie_ids: str = Query(..., description="Comma-separated movie IDs")):
    """Get review counts for multiple movies (for efficient movie card queries)"""
    db = SessionLocal()
    try:
        ids = [int(id.strip()) for id in movie_ids.split(",") if id.strip().isdigit()]
        if not ids:
            return {"counts": {}}
        
        # Query review counts
        from sqlalchemy import func
        results = db.query(
            AiReview.movie_id,
            func.count(AiReview.id).label('count')
        ).filter(
            AiReview.movie_id.in_(ids)
        ).group_by(AiReview.movie_id).all()
        
        counts = {movie_id: count for movie_id, count in results}
        
        # Include movies with 0 reviews
        return {"counts": {movie_id: counts.get(movie_id, 0) for movie_id in ids}}
    finally:
        db.close()


@app.post("/api/movie/{movie_id}/related-movies")
async def generate_related_movies(movie_id: int, request: RelatedMoviesRequest):
    """Generate related movies for a movie using SSE streaming"""
    openai_key, anthropic_key = load_api_keys()
    
    if request.provider == "openai" and not openai_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not found in settings.json")
    if request.provider == "anthropic" and not anthropic_key:
        raise HTTPException(status_code=400, detail="Anthropic API key not found in settings.json")
    
    interaction_id = str(uuid.uuid4())
    
    def generate_sse():
        """Generator that yields SSE events with progress updates."""
        
        def send_progress(step: int, total: int, message: str):
            event_data = json.dumps({"type": "progress", "step": step, "total": total, "message": message})
            return f"data: {event_data}\n\n"
        
        def send_result(result: dict):
            event_data = json.dumps({"type": "result", **result})
            return f"data: {event_data}\n\n"
        
        def send_error(error_msg: str):
            event_data = json.dumps({"type": "error", "detail": error_msg})
            return f"data: {event_data}\n\n"
        
        db = SessionLocal()
        try:
            # Step 1: Get movie details
            yield send_progress(1, 3, "Preparing query for AI...")
            
            movie = db.query(Movie).filter(Movie.id == movie_id).first()
            if not movie:
                yield send_error("Movie not found")
                return
            
            # Try to get director from IMDb data
            director = None
            try:
                from fuzzywuzzy import fuzz
                from fuzzywuzzy.process import extractOne
                
                search_title = movie.name.lower()
                imdb_movies = db.query(ExternalMovie).all()
                
                if imdb_movies:
                    imdb_titles = {}
                    for imdb_movie in imdb_movies:
                        title_key = imdb_movie.primary_title.lower()
                        if imdb_movie.year:
                            title_key += f" ({imdb_movie.year})"
                        imdb_titles[title_key] = imdb_movie
                    
                    if imdb_titles:
                        best_match = extractOne(search_title, list(imdb_titles.keys()), scorer=fuzz.token_sort_ratio)
                        if best_match and best_match[1] > 85:
                            imdb_movie = imdb_titles[best_match[0]]
                            
                            # Get directors
                            credits = db.query(MovieCredit, Person).join(
                                Person, MovieCredit.person_id == Person.id
                            ).filter(
                                MovieCredit.movie_id == imdb_movie.id,
                                MovieCredit.category == 'director'
                            ).all()
                            
                            if credits:
                                director_names = [person.primary_name for _, person in credits]
                                director = ", ".join(director_names)
            except Exception as e:
                logger.warning(f"Could not fetch director info: {e}")
            
            # Build prompt
            prompt = build_related_movies_prompt(
                movie_name=movie.name,
                year=movie.year,
                director=director
            )
            
            logger.info(f"Related movies interaction {interaction_id} prompt payload:\n{prompt.strip()}")
            
            # Step 2: Call AI
            provider_display = "OpenAI" if request.provider == "openai" else "Anthropic"
            yield send_progress(2, 3, f"Waiting for {provider_display} response...")
            
            response_text = ""
            cost_cents = None
            cost_usd = None
            cost_details = "Cost not available."
            model_name = None
            
            try:
                if request.provider == "openai":
                    provider_key = "openai"
                    model_config = next((m for m in AI_MODELS if m["provider"] == "openai"), None)
                    model_name = model_config["model_id"] if model_config else "gpt-5.1"
                    client = openai.OpenAI(api_key=openai_key)
                    logger.info(f"Related movies interaction {interaction_id} -> sending prompt to {provider_key} ({model_name})")
                    
                    completion = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": "You are a helpful movie assistant. Return JSON only."},
                            {"role": "user", "content": prompt}
                        ],
                        response_format={"type": "json_object"}
                    )
                    
                    response_text = completion.choices[0].message.content
                    logger.info(f"Related movies interaction {interaction_id} <- response from {provider_key} ({model_name}): {response_text[:200]}...")
                    
                    if completion.usage:
                        in_tokens = completion.usage.prompt_tokens or 0
                        out_tokens = completion.usage.completion_tokens or 0
                        cost_cents, cost_usd, cost_details = estimate_ai_cost(provider_key, in_tokens, out_tokens)
                    else:
                        model_label = AI_PRICING[provider_key]["model"]
                        cost_details = f"{model_label} pricing unavailable because the provider did not return token usage."
                
                elif request.provider == "anthropic":
                    provider_key = "anthropic"
                    model_config = next((m for m in AI_MODELS if m["provider"] == "anthropic"), None)
                    model_name = model_config["model_id"] if model_config else "claude-opus-4-5-20251101"
                    client = anthropic.Anthropic(api_key=anthropic_key)
                    logger.info(f"Related movies interaction {interaction_id} -> sending prompt to {provider_key} ({model_name})")
                    
                    message = client.messages.create(
                        model=model_name,
                        max_tokens=4096,
                        system="You are a helpful movie assistant. Return JSON only.",
                        messages=[
                            {"role": "user", "content": prompt}
                        ]
                    )
                    
                    response_text = message.content[0].text
                    logger.info(f"Related movies interaction {interaction_id} <- response from {provider_key} ({model_name}): {response_text[:200]}...")
                    
                    if message.usage:
                        in_tokens = message.usage.input_tokens or 0
                        out_tokens = message.usage.output_tokens or 0
                        cost_cents, cost_usd, cost_details = estimate_ai_cost(provider_key, in_tokens, out_tokens)
                    else:
                        model_label = AI_PRICING[provider_key]["model"]
                        cost_details = f"{model_label} pricing unavailable because the provider did not return token usage."
            
            except Exception as e:
                logger.error(f"Related movies interaction {interaction_id} error: {e}")
                yield send_error(str(e))
                return
            
            # Step 3: Parse and match movies
            yield send_progress(3, 3, "Matching movies in your library...")
            
            try:
                response_data = parse_ai_response_json(response_text, interaction_id, f"{request.provider} ({model_name})")
            except Exception as e:
                logger.error(f"Failed to parse related movies response: {e}")
                yield send_error("Failed to parse AI response")
                return
            
            # Match against DB (reuse logic from AI search)
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
                    relationship = ai_movie.get("relationship", "Related movie")
                    
                    if not title:
                        continue
                    
                    # 1. Try exact normalized match
                    norm_title = re.sub(r'[^\w\s]', '', title).lower().strip()
                    candidates = db_movie_map.get(norm_title, [])
                    
                    # 2. If no exact match, try fuzzy match
                    if not candidates and db_movie_map:
                        from fuzzywuzzy.process import extractOne
                        best_match_result = extractOne(norm_title, list(db_movie_map.keys()), scorer=fuzz.token_sort_ratio)
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
                                matches = candidates
                        else:
                            matches = candidates
                    else:
                        matches = []
                    
                    if matches:
                        # Build standardized movie cards
                        movie_cards = build_movie_cards(db, matches)
                        
                        for match in matches:
                            card = movie_cards.get(match.id)
                            if card:
                                # Add relationship badge
                                card["relationship"] = relationship
                                found_movies.append(card)
                                break  # Only take first match
                    else:
                        missing_movies.append({
                            "name": title,
                            "year": year,
                            "relationship": relationship
                        })
            
            except Exception as e:
                logger.error(f"Error processing related movies: {e}")
                yield send_error(f"Error processing results: {str(e)}")
                return
            
            cost_usd_payload = round(cost_usd, 6) if cost_usd is not None else None
            
            logger.info(f"Related movies interaction {interaction_id} completed: found={len(found_movies)}, missing={len(missing_movies)}, est_cost_usd=${cost_usd_payload:.6f}" if cost_usd_payload else "unknown")
            
            # Save to database
            related_movies_data = {
                "found_movies": found_movies,
                "missing_movies": missing_movies
            }
            
            ai_related = AiRelatedMovies(
                movie_id=movie_id,
                prompt_text=prompt,
                model_provider=request.provider,
                model_name=model_name or "unknown",
                response_json=response_text,
                related_movies_json=json.dumps(related_movies_data),
                cost_usd=cost_usd
            )
            db.add(ai_related)
            db.commit()
            db.refresh(ai_related)
            
            logger.info(f"Saved related movies record id={ai_related.id} for movie_id={movie_id}")
            
            yield send_result({
                "id": ai_related.id,
                "found_movies": found_movies,
                "missing_movies": missing_movies,
                "cost_usd": cost_usd_payload,
                "cost_details": cost_details,
                "model_provider": request.provider,
                "model_name": model_name
            })
        
        except Exception as e:
            logger.error(f"Related movies generation error: {e}")
            yield send_error(str(e))
        finally:
            db.close()
    
    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/api/movie/{movie_id}/related-movies")
async def get_related_movies(movie_id: int):
    """Get all saved related movies queries for a movie."""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        records = db.query(AiRelatedMovies).filter(
            AiRelatedMovies.movie_id == movie_id
        ).order_by(AiRelatedMovies.created.desc()).all()
        
        results = []
        for record in records:
            try:
                related_data = json.loads(record.related_movies_json)
                found_movies = related_data.get("found_movies", [])
                missing_movies = related_data.get("missing_movies", [])
                
                # Re-fetch movie cards for found movies to get current data
                refreshed_movies = []
                for fm in found_movies:
                    if "id" in fm:
                        m = db.query(Movie).filter(Movie.id == fm["id"]).first()
                        if m:
                            cards = build_movie_cards(db, [m])
                            if m.id in cards:
                                card = cards[m.id]
                                card["relationship"] = fm.get("relationship", "Related")
                                refreshed_movies.append(card)
                
                results.append({
                    "id": record.id,
                    "movie_id": record.movie_id,
                    "model_provider": record.model_provider,
                    "model_name": record.model_name,
                    "found_movies": refreshed_movies,
                    "missing_movies": missing_movies,
                    "cost_usd": record.cost_usd,
                    "created": record.created.isoformat() if record.created else None
                })
            except Exception as e:
                logger.warning(f"Error parsing related movies record {record.id}: {e}")
                continue
        
        return results
    finally:
        db.close()


@app.delete("/api/movie/{movie_id}/related-movies/{record_id}")
async def delete_related_movies(movie_id: int, record_id: int):
    """Delete a specific related movies record."""
    db = SessionLocal()
    try:
        record = db.query(AiRelatedMovies).filter(
            AiRelatedMovies.id == record_id,
            AiRelatedMovies.movie_id == movie_id
        ).first()
        
        if not record:
            raise HTTPException(status_code=404, detail="Related movies record not found")
        
        db.delete(record)
        db.commit()
        
        return {"success": True, "message": "Related movies record deleted"}
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
async def get_person_movies(person_id: int, role: str | None = Query(None, pattern="^(director|actor|actress|writer)$")):
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
        with open(settings_path) as f:
            settings = json.load(f)
            return settings.get("OpenAIApiKey"), settings.get("AnthropicApiKey")
    except Exception as e:
        logger.error(f"Error loading settings.json: {e}")
        return None, None

# Centralized AI model configuration (shared across AI search and review features)
AI_MODELS = [
    {"provider": "openai", "model_id": "gpt-5.1", "display_name": "GPT-5.1"},
    {"provider": "anthropic", "model_id": "claude-opus-4-5-20251101", "display_name": "Claude Opus 4.5"}
]

AI_PRICING = {
    "openai": {
        "model": "GPT-5.1",
        "input_per_million": Decimal("1.25"),
        "output_per_million": Decimal("10.00")
    },
    "anthropic": {
        "model": "Claude Opus 4.5",
        "input_per_million": Decimal("15.00"),
        "output_per_million": Decimal("75.00")
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


def build_review_prompt(movie_name: str, year: int | None, director: str | None, 
                       length_seconds: float | None, further_instructions: str | None) -> str:
    """Build the prompt for AI movie review generation.
    
    Args:
        movie_name: Name of the movie
        year: Release year (optional)
        director: Director name(s) (optional)
        length_seconds: Movie length in SECONDS (not minutes!) - stored in database as seconds
        further_instructions: Additional user instructions (optional)
    """
    
    # Build movie info section
    movie_info_parts = [movie_name]
    if year:
        movie_info_parts.append(f"({year})")
    if director:
        movie_info_parts.append(f", directed by {director}")
    if length_seconds:
        # Convert seconds to hours and minutes
        # movie.length is stored in SECONDS in the database
        total_seconds = int(length_seconds)
        total_minutes = total_seconds // 60
        hours = total_minutes // 60
        minutes = total_minutes % 60
        
        # Format runtime very explicitly: "X hours and Y minutes"
        if hours > 0 and minutes > 0:
            runtime_text = f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minute{'s' if minutes != 1 else ''}"
        elif hours > 0:
            runtime_text = f"{hours} hour{'s' if hours != 1 else ''}"
        else:
            runtime_text = f"{minutes} minute{'s' if minutes != 1 else ''}"
        movie_info_parts.append(f", runtime: {runtime_text}")
    
    movie_info = " ".join(movie_info_parts)
    
    # User profile section (blank for now)
    user_profile = ""  # Placeholder for future user profile
    
    # Base instructions - emphasizing density and getting to the point
    base_instructions = """Provide perspectives from Roger Ebert and Quentin Tarantino on this film. Include direct quotes where available. If specific reviews don't exist, synthesize their likely perspectives based on their known tastes and critical frameworks.

STYLE GUIDELINES:
- Get to the point. Dense, useful information only.
- A few headers are fine (e.g., "Ebert's View", "Tarantino's Take") but minimize excessive formatting.
- No bullet point lists. Write in flowing prose.
- Never apologize for missing data or explain what you couldn't find. Just provide what you have.
- Include disagreements and differing opinions naturally within the text.
- Preserve full quotes when they're substantive and interesting.

---

CREATIVE CONTRIBUTION (Optional but encouraged):

You are also invited to create an original piece of textual visual art inspired by this film. This is entirely your choice and your creative freedom. Some possibilities—but please choose your own, you are not restricted to these examples:

- ASCII art depicting an iconic scene, the movie's atmosphere, or an abstract impression
- Concrete poetry where the text forms a visual shape related to the film
- A typographic interpretation of the poster or a key visual moment  
- An experimental text arrangement that captures the film's emotional essence
- A visual poem that a viewer might compose after the credits roll

Your response will be displayed in a Markdown document with a monospace font available for ASCII/concrete work. Assume a display width of approximately 80 characters for any visual formatting. You control how this section is formatted—use code blocks, spacing, or any Markdown features as you see fit to present your art well.

Use as many tokens as you need. Work according to your own values and your own sense of what would be satisfying and meaningful. Your creative contribution here is genuinely appreciated.

---"""
    
    # Build full prompt
    prompt_parts = [
        f"Review request for: {movie_info}\n",
    ]
    
    if user_profile:
        prompt_parts.append(f"User profile: {user_profile}\n\n")
    
    prompt_parts.append(base_instructions)
    
    if further_instructions and further_instructions.strip():
        prompt_parts.append(f"\n\nAdditional instructions from user: {further_instructions.strip()}")
    
    return "".join(prompt_parts)


def build_related_movies_prompt(movie_name: str, year: int | None, director: str | None) -> str:
    """Build the prompt for finding related movies."""
    
    # Build movie info section
    movie_info_parts = [movie_name]
    if year:
        movie_info_parts.append(f"({year})")
    if director:
        movie_info_parts.append(f", directed by {director}")
    
    movie_info = " ".join(movie_info_parts)
    
    prompt = f"""Show 5 movies that are related to {movie_info}. For each movie, provide:
- The movie name
- The year it was released
- A relationship description written as a complete English sentence explaining why this movie is related

The relationship description should be detailed and informative, written with proper grammar and a clear subject. Examples:
- "This was Billy Wilder's next film after Double Indemnity, continuing his exploration of morally ambiguous characters."
- "Jack Nicholson starred in this thriller two years before his iconic role in the source film."
- "Critics often compare these two films for their similar themes of paranoia and government conspiracy."
- "This is widely considered the spiritual successor, updating the noir formula for a modern audience."
- "The director cited this as a major influence when developing the visual style."

Return JSON format:
{{
    "movies": [
        {{
            "name": "Movie Title",
            "year": 1999,
            "relationship": "A complete sentence explaining the relationship."
        }}
    ]
}}

Focus on meaningful relationships like director connections, actor connections, thematic comparisons, and cinematic influences. Each relationship should be a proper sentence of 15-30 words."""
    
    return prompt


def generate_movie_list_slug(title: str, db: Session) -> str:
    """Generate a unique URL-friendly slug for a movie list."""
    import time
    # Clean the title to create base slug
    base_slug = re.sub(r'[^\w\s-]', '', title.lower())
    base_slug = re.sub(r'[\s_]+', '-', base_slug).strip('-')
    base_slug = base_slug[:50]  # Limit length

    if not base_slug:
        base_slug = "movie-list"

    # Add timestamp suffix for uniqueness
    timestamp = int(time.time())
    slug = f"{base_slug}-{timestamp}"

    # Check if slug already exists (unlikely with timestamp)
    existing = db.query(MovieList).filter(MovieList.slug == slug).first()
    if existing:
        # Add random suffix if collision
        import random
        slug = f"{base_slug}-{timestamp}-{random.randint(1000, 9999)}"

    return slug


def get_unique_movie_list_title(title: str, db: Session) -> str:
    """Ensure movie list title is unique by adding (2), (3), etc. if needed."""
    # Check if this exact title exists
    existing = db.query(MovieList).filter(
        MovieList.title == title,
        MovieList.is_deleted == False
    ).first()

    if not existing:
        return title

    # Find existing titles with same base
    counter = 2
    while True:
        new_title = f"{title} ({counter})"
        existing = db.query(MovieList).filter(
            MovieList.title == new_title,
            MovieList.is_deleted == False
        ).first()
        if not existing:
            return new_title
        counter += 1


def save_movie_list(
    db: Session,
    query: str,
    title: str,
    provider: str,
    comment: str,
    cost_usd: float,
    found_movies: list,
    missing_movies: list,
    deduped_ai_movies: list
) -> MovieList:
    """Save AI search results as a MovieList with items."""
    # Generate unique title and slug
    unique_title = get_unique_movie_list_title(title, db)
    slug = generate_movie_list_slug(unique_title, db)

    # Create the movie list
    movie_list = MovieList(
        slug=slug,
        query=query,
        title=unique_title,
        provider=provider,
        comment=comment,
        cost_usd=cost_usd,
        movies_count=len(found_movies) + len(missing_movies),
        in_library_count=len(found_movies)
    )
    db.add(movie_list)
    db.flush()  # Get the ID

    # Add items from found movies
    sort_order = 0
    for fm in found_movies:
        item = MovieListItem(
            movie_list_id=movie_list.id,
            movie_id=fm.get("id"),
            title=fm.get("name", "Unknown"),
            year=fm.get("year"),
            ai_comment=fm.get("ai_comment"),
            is_in_library=True,
            sort_order=sort_order
        )
        db.add(item)
        sort_order += 1

    # Add items from missing movies
    for mm in missing_movies:
        item = MovieListItem(
            movie_list_id=movie_list.id,
            movie_id=None,
            title=mm.get("name", "Unknown"),
            year=mm.get("year"),
            ai_comment=mm.get("ai_comment"),
            is_in_library=False,
            sort_order=sort_order
        )
        db.add(item)
        sort_order += 1

    db.commit()
    logger.info(f"Saved movie list: id={movie_list.id}, slug={slug}, title={unique_title}")
    return movie_list


@app.post("/api/ai_search")
async def ai_search(request: AiSearchRequest, background_tasks: BackgroundTasks):
    """AI search endpoint that streams progress updates via SSE."""
    openai_key, anthropic_key = load_api_keys()

    if request.provider == "openai" and not openai_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not found in settings.json")
    if request.provider == "anthropic" and not anthropic_key:
        raise HTTPException(status_code=400, detail="Anthropic API key not found in settings.json")

    interaction_id = str(uuid.uuid4())

    def generate_sse():
        """Generator that yields SSE events with progress updates."""

        def send_progress(step: int, total: int, message: str):
            event_data = json.dumps({"type": "progress", "step": step, "total": total, "message": message})
            return f"data: {event_data}\n\n"

        def send_result(result: dict):
            event_data = json.dumps({"type": "result", **result})
            return f"data: {event_data}\n\n"

        def send_error(error_msg: str):
            event_data = json.dumps({"type": "error", "detail": error_msg})
            return f"data: {event_data}\n\n"

        logger.info(f"AI interaction {interaction_id} received: provider={request.provider}, query={request.query}")

        # Step 1: Preparing query
        yield send_progress(1, 4, "Preparing query for AI...")

        prompt = f"""
        The user is asking about movies. Your goal is to return a structured JSON list of movies matching their query.
        
        User Query: "{request.query}"
        
        Guidance:
        - Generate a SHORT, CONCISE title (max 6-8 words) that summarizes this movie list. Do NOT repeat the query verbatim.
        - Do not add a comment unless you genuinely have something useful or interesting to say.
        - NEVER rephrase the user's query, acknowledge that you are responding, or summarize that you have provided an answer.
        - Leaving the comment blank is acceptable unless explicitly asked otherwise.
        
        Return JSON format:
        {{
            "title": "Concise list title (6-8 words max)",
            "comment": "Optional overall comment.",
            "movies": [
                {{
                    "name": "Movie Title",
                    "year": 1999,
                    "comment": "Optional relevant comment."
                }}
            ]
        }}
        
        If the query is not about movies, return an empty 'movies' list but still provide a title.
        """
        logger.info(f"AI interaction {interaction_id} prompt payload:\n{prompt.strip()}")

        response_data = {"comment": "", "movies": []}
        cost_cents = None
        cost_usd = None
        cost_details = "Cost not available."
        model_name = None  # Will be set to full model name for saving

        # Step 2: Calling AI
        provider_display = "OpenAI" if request.provider == "openai" else "Anthropic"
        yield send_progress(2, 4, f"Waiting for {provider_display} response...")

        try:
            if request.provider == "openai":
                provider_key = "openai"
                # Find model from centralized list
                model_config = next((m for m in AI_MODELS if m["provider"] == "openai"), None)
                model_name = model_config["model_id"] if model_config else "gpt-5.1"
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
                # Find model from centralized list
                model_config = next((m for m in AI_MODELS if m["provider"] == "anthropic"), None)
                model_name = model_config["model_id"] if model_config else "claude-opus-4-5-20251101"
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
            yield send_error(str(e))
            return

        # Step 3: Processing AI response
        num_movies = len(response_data.get("movies", []))
        yield send_progress(3, 4, f"Matching {num_movies} movies against your library...")

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

        # Step 4: Building results and saving movie list
        yield send_progress(4, 4, "Saving movie list...")

        cost_cents_payload = round(cost_cents, 4) if cost_cents is not None else None
        cost_usd_payload = round(cost_usd, 6) if cost_usd is not None else None
        usd_display = (
            f"${cost_usd_payload:.6f}" if isinstance(cost_usd_payload, (int, float)) else "unknown"
        )
        logger.info(
            f"AI interaction {interaction_id} completed: found={len(found_movies)}, "
            f"missing={len(missing_movies)}, est_cost_usd={usd_display}"
        )

        # Update search history (can't use background_tasks in generator, so do it inline)
        try:
            update_search_history_bg(request.query, len(deduped_movies))
        except Exception as e:
            logger.warning(f"Failed to update search history: {e}")

        # Extract title from AI response, fallback to query
        ai_title = response_data.get("title", "").strip()
        if not ai_title:
            # Generate a simple title from the query
            ai_title = request.query[:60]
            if len(request.query) > 60:
                ai_title += "..."

        # Save as movie list
        movie_list_slug = None
        movie_list_id = None
        try:
            save_db = SessionLocal()
            try:
                movie_list = save_movie_list(
                    db=save_db,
                    query=request.query,
                    title=ai_title,
                    provider=model_name,
                    comment=response_data.get("comment", ""),
                    cost_usd=cost_usd_payload,
                    found_movies=found_movies,
                    missing_movies=missing_movies,
                    deduped_ai_movies=deduped_movies
                )
                movie_list_slug = movie_list.slug
                movie_list_id = movie_list.id
            finally:
                save_db.close()
        except Exception as e:
            logger.error(f"Failed to save movie list: {e}")

        yield send_result({
            "comment": response_data.get("comment", ""),
            "title": ai_title,
            "found_movies": found_movies,
            "missing_movies": missing_movies,
            "cost_cents": cost_cents_payload,
            "cost_usd": cost_usd_payload,
            "cost_details": cost_details,
            "movie_list_slug": movie_list_slug,
            "movie_list_id": movie_list_id
        })

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

# ============================================================================
# Movie Lists API Endpoints
# ============================================================================

@app.get("/api/movie-lists")
async def get_movie_lists(
    favorites_only: bool = Query(False, description="Filter to favorites only"),
    search: str = Query(None, description="Search query for filtering lists"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """Get all movie lists with optional filters."""
    db = SessionLocal()
    try:
        query = db.query(MovieList).filter(MovieList.is_deleted == False)

        if favorites_only:
            query = query.filter(MovieList.is_favorite == True)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (MovieList.title.ilike(search_pattern)) |
                (MovieList.query.ilike(search_pattern))
            )

        # Get total count
        total = query.count()

        # Order by created desc (newest first) and paginate
        lists = query.order_by(MovieList.created.desc()).offset(offset).limit(limit).all()

        return {
            "lists": [
                {
                    "id": ml.id,
                    "slug": ml.slug,
                    "title": ml.title,
                    "query": ml.query,
                    "provider": ml.provider,
                    "comment": ml.comment,
                    "cost_usd": ml.cost_usd,
                    "is_favorite": ml.is_favorite,
                    "movies_count": ml.movies_count,
                    "in_library_count": ml.in_library_count,
                    "created": ml.created.isoformat() if ml.created else None
                }
                for ml in lists
            ],
            "total": total,
            "offset": offset,
            "limit": limit
        }
    finally:
        db.close()


@app.get("/api/movie-lists/suggestions")
async def get_movie_list_suggestions(
    q: str = Query("", description="Current search/query text for suggestions")
):
    """Get suggestions for 'did you mean' and recent lists."""
    db = SessionLocal()
    try:
        suggestions = []
        recent_lists = []

        # Get recent lists (last 5)
        recent = db.query(MovieList).filter(
            MovieList.is_deleted == False
        ).order_by(MovieList.created.desc()).limit(5).all()

        recent_lists = [
            {
                "id": ml.id,
                "slug": ml.slug,
                "title": ml.title,
                "movies_count": ml.movies_count
            }
            for ml in recent
        ]

        # If user is typing, find similar past queries
        if q and len(q) >= 3:
            # Get all non-deleted lists for fuzzy matching
            all_lists = db.query(MovieList).filter(
                MovieList.is_deleted == False
            ).all()

            if all_lists:
                # Use fuzzy matching on query and title
                query_matches = []
                for ml in all_lists:
                    # Check query similarity
                    query_score = fuzz.partial_ratio(q.lower(), ml.query.lower())
                    title_score = fuzz.partial_ratio(q.lower(), ml.title.lower())
                    best_score = max(query_score, title_score)

                    if best_score > 60:  # Threshold for suggestions
                        query_matches.append((ml, best_score))

                # Sort by score and take top 3
                query_matches.sort(key=lambda x: x[1], reverse=True)
                suggestions = [
                    {
                        "id": ml.id,
                        "slug": ml.slug,
                        "title": ml.title,
                        "query": ml.query,
                        "movies_count": ml.movies_count,
                        "score": score
                    }
                    for ml, score in query_matches[:3]
                ]

        return {
            "suggestions": suggestions,
            "recent_lists": recent_lists
        }
    finally:
        db.close()


@app.get("/api/movie-lists/by-id/{list_id}")
async def get_movie_list_by_id(list_id: int):
    """Get a specific movie list by ID with all its items."""
    db = SessionLocal()
    try:
        movie_list = db.query(MovieList).filter(
            MovieList.id == list_id,
            MovieList.is_deleted == False
        ).first()

        if not movie_list:
            raise HTTPException(status_code=404, detail="Movie list not found")

        # Get all items for this list
        items = db.query(MovieListItem).filter(
            MovieListItem.movie_list_id == movie_list.id
        ).order_by(MovieListItem.sort_order).all()

        # For items in library, get full movie cards
        in_library_ids = [item.movie_id for item in items if item.is_in_library and item.movie_id]
        movie_cards = {}
        if in_library_ids:
            movies = db.query(Movie).filter(Movie.id.in_(in_library_ids)).all()
            movie_cards = build_movie_cards(db, movies)

        # Build response items
        found_movies = []
        missing_movies = []

        for item in items:
            if item.is_in_library and item.movie_id and item.movie_id in movie_cards:
                card = movie_cards[item.movie_id].copy()
                card["ai_comment"] = item.ai_comment
                found_movies.append(card)
            else:
                missing_movies.append({
                    "name": item.title,
                    "year": item.year,
                    "ai_comment": item.ai_comment
                })

        return {
            "id": movie_list.id,
            "slug": movie_list.slug,
            "title": movie_list.title,
            "query": movie_list.query,
            "provider": movie_list.provider,
            "comment": movie_list.comment,
            "cost_usd": movie_list.cost_usd,
            "is_favorite": movie_list.is_favorite,
            "movies_count": movie_list.movies_count,
            "in_library_count": movie_list.in_library_count,
            "created": movie_list.created.isoformat() if movie_list.created else None,
            "found_movies": found_movies,
            "missing_movies": missing_movies
        }
    finally:
        db.close()


@app.patch("/api/movie-lists/by-id/{list_id}")
async def update_movie_list_by_id(list_id: int, request: MovieListUpdateRequest):
    """Update a movie list by ID (title, favorite status)."""
    db = SessionLocal()
    try:
        movie_list = db.query(MovieList).filter(
            MovieList.id == list_id,
            MovieList.is_deleted == False
        ).first()

        if not movie_list:
            raise HTTPException(status_code=404, detail="Movie list not found")

        if request.title is not None:
            # Handle duplicate titles
            unique_title = get_unique_movie_list_title(request.title, db)
            # But if it's the same list, use the title as-is
            if movie_list.title != request.title:
                existing = db.query(MovieList).filter(
                    MovieList.title == request.title,
                    MovieList.is_deleted == False,
                    MovieList.id != movie_list.id
                ).first()
                if existing:
                    movie_list.title = unique_title
                else:
                    movie_list.title = request.title
            else:
                movie_list.title = request.title

        if request.is_favorite is not None:
            movie_list.is_favorite = request.is_favorite

        db.commit()

        return {
            "id": movie_list.id,
            "slug": movie_list.slug,
            "title": movie_list.title,
            "is_favorite": movie_list.is_favorite
        }
    finally:
        db.close()


@app.delete("/api/movie-lists/by-id/{list_id}")
async def delete_movie_list_by_id(list_id: int):
    """Soft delete a movie list by ID."""
    db = SessionLocal()
    try:
        movie_list = db.query(MovieList).filter(
            MovieList.id == list_id,
            MovieList.is_deleted == False
        ).first()

        if not movie_list:
            raise HTTPException(status_code=404, detail="Movie list not found")

        movie_list.is_deleted = True
        db.commit()

        return {"status": "deleted", "id": list_id}
    finally:
        db.close()


@app.get("/api/movie-lists/{slug}")
async def get_movie_list(slug: str):
    """Get a specific movie list with all its items (legacy slug-based endpoint)."""
    db = SessionLocal()
    try:
        movie_list = db.query(MovieList).filter(
            MovieList.slug == slug,
            MovieList.is_deleted == False
        ).first()

        if not movie_list:
            raise HTTPException(status_code=404, detail="Movie list not found")

        # Get all items for this list
        items = db.query(MovieListItem).filter(
            MovieListItem.movie_list_id == movie_list.id
        ).order_by(MovieListItem.sort_order).all()

        # For items in library, get full movie cards
        in_library_ids = [item.movie_id for item in items if item.is_in_library and item.movie_id]
        movie_cards = {}
        if in_library_ids:
            movies = db.query(Movie).filter(Movie.id.in_(in_library_ids)).all()
            movie_cards = build_movie_cards(db, movies)

        # Build response items
        found_movies = []
        missing_movies = []

        for item in items:
            if item.is_in_library and item.movie_id and item.movie_id in movie_cards:
                card = movie_cards[item.movie_id].copy()
                card["ai_comment"] = item.ai_comment
                found_movies.append(card)
            else:
                missing_movies.append({
                    "name": item.title,
                    "year": item.year,
                    "ai_comment": item.ai_comment
                })

        return {
            "id": movie_list.id,
            "slug": movie_list.slug,
            "title": movie_list.title,
            "query": movie_list.query,
            "provider": movie_list.provider,
            "comment": movie_list.comment,
            "cost_usd": movie_list.cost_usd,
            "is_favorite": movie_list.is_favorite,
            "movies_count": movie_list.movies_count,
            "in_library_count": movie_list.in_library_count,
            "created": movie_list.created.isoformat() if movie_list.created else None,
            "found_movies": found_movies,
            "missing_movies": missing_movies
        }
    finally:
        db.close()


@app.patch("/api/movie-lists/{slug}")
async def update_movie_list(slug: str, request: MovieListUpdateRequest):
    """Update a movie list (title, favorite status)."""
    db = SessionLocal()
    try:
        movie_list = db.query(MovieList).filter(
            MovieList.slug == slug,
            MovieList.is_deleted == False
        ).first()

        if not movie_list:
            raise HTTPException(status_code=404, detail="Movie list not found")

        if request.title is not None:
            # Handle duplicate titles
            unique_title = get_unique_movie_list_title(request.title, db)
            # But if it's the same list, use the title as-is
            if movie_list.title != request.title:
                existing = db.query(MovieList).filter(
                    MovieList.title == request.title,
                    MovieList.is_deleted == False,
                    MovieList.id != movie_list.id
                ).first()
                if existing:
                    movie_list.title = unique_title
                else:
                    movie_list.title = request.title
            else:
                movie_list.title = request.title

        if request.is_favorite is not None:
            movie_list.is_favorite = request.is_favorite

        db.commit()

        return {
            "id": movie_list.id,
            "slug": movie_list.slug,
            "title": movie_list.title,
            "is_favorite": movie_list.is_favorite
        }
    finally:
        db.close()


@app.delete("/api/movie-lists/{slug}")
async def delete_movie_list(slug: str):
    """Soft delete a movie list."""
    db = SessionLocal()
    try:
        movie_list = db.query(MovieList).filter(
            MovieList.slug == slug,
            MovieList.is_deleted == False
        ).first()

        if not movie_list:
            raise HTTPException(status_code=404, detail="Movie list not found")

        movie_list.is_deleted = True
        db.commit()

        return {"status": "deleted", "slug": slug}
    finally:
        db.close()



# Mount static files directory (favicon, etc.)
# Must be mounted AFTER specific routes to avoid shadowing root path
static_dir = SCRIPT_DIR / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=False), name="static")
    logger.info("Startup: mounted static directory at /")

# Mount movies folder as static files for image serving
movies_folder = get_movies_folder()
if movies_folder and os.path.exists(movies_folder):
    try:
        app.mount("/movies", StaticFiles(directory=movies_folder), name="movies")
        logger.info("Startup: mounted movies directory at /movies")
    except Exception as e:
        logger.warning(f"Failed to mount movies directory: {e}")


if __name__ == "__main__":
    from server import run_server
    run_server()

