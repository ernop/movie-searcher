"""
Screenshot extraction functionality for Movie Searcher.
"""
import os
import hashlib
import re
import logging
import time
from pathlib import Path
from concurrent.futures import Future

# Import database models and session
from database import SessionLocal, Movie

# Import screenshot synchronization functions
from screenshot_sync import sync_existing_screenshot, save_screenshot_to_db

# Import subtitle functions
from video.subtitle import parse_srt_at_timestamp, burn_subtitle_text_onto_image

logger = logging.getLogger(__name__)

# Import shared resources from video_processing module
# These will be available after video_processing is initialized
def _get_shared_resources():
    """Get shared resources from video_processing module"""
    from video_processing import (
        SCREENSHOT_DIR, frame_extraction_queue, process_executor, shutdown_flag,
        screenshot_completion_times, screenshot_completion_lock,
        increment_active_extractions, decrement_active_extractions
    )
    return {
        'SCREENSHOT_DIR': SCREENSHOT_DIR,
        'frame_extraction_queue': frame_extraction_queue,
        'process_executor': process_executor,
        'shutdown_flag': shutdown_flag,
        'screenshot_completion_times': screenshot_completion_times,
        'screenshot_completion_lock': screenshot_completion_lock,
        'increment_active_extractions': increment_active_extractions,
        'decrement_active_extractions': decrement_active_extractions
    }


def generate_screenshot_filename(video_path, timestamp_seconds, suffix="", movie_id=None):
    """Generate a sensible screenshot filename based on movie name and timestamp
    
    Args:
        video_path: Path to video file
        timestamp_seconds: Timestamp in seconds
        suffix: Optional suffix to add before .jpg (e.g., "_subs" for subtitles)
        movie_id: Movie ID to look up cleaned name (required - should always be available)
    """
    # Get shared resources
    resources = _get_shared_resources()
    SCREENSHOT_DIR = resources['SCREENSHOT_DIR']
    
    video_path_obj = Path(video_path)
    
    # Get cleaned movie name from database using movie_id
    movie_name = None
    if movie_id:
        db = SessionLocal()
        try:
            movie = db.query(Movie).filter(Movie.id == movie_id).first()
            if movie and movie.name:
                movie_name = movie.name
            else:
                logger.error(f"Movie ID {movie_id} not found in database when generating screenshot filename. This is a programming error.")
        except Exception as e:
            logger.error(f"Database error when looking up movie_id={movie_id} for screenshot filename: {e}", exc_info=True)
        finally:
            db.close()
    
    # If movie_id not provided or lookup failed, use sanitized video filename
    # This should never happen in normal operation - indicates programming error
    if not movie_name:
        if not movie_id:
            logger.error(f"generate_screenshot_filename called without movie_id for {video_path}. This is a programming error.")
        movie_name = video_path_obj.stem  # Get filename without extension
    
    # Sanitize filename: remove invalid characters for Windows/Linux
    # Replace invalid filename characters with underscore
    sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', movie_name)
    # Remove leading/trailing dots and spaces
    sanitized_name = sanitized_name.strip('. ')
    # Limit length to avoid filesystem issues
    if len(sanitized_name) > 100:
        sanitized_name = sanitized_name[:100]
    
    # Format: movie_name_screenshot150s.jpg or movie_name_screenshot150s_subs.jpg
    screenshot_filename = f"{sanitized_name}_screenshot{int(timestamp_seconds)}s{suffix}.jpg"
    return SCREENSHOT_DIR / screenshot_filename


def extract_movie_screenshot(video_path, timestamp_seconds, load_config_func, find_ffmpeg_func, scan_progress_dict, add_scan_log_func, priority: str = "normal", subtitle_path=None, movie_id=None):
    """Queue a screenshot extraction for async processing
    
    Args:
        subtitle_path: Optional path to subtitle file to burn in
        movie_id: Optional movie ID to use for database operations (avoids path lookup)
    """
    # Import here to avoid circular dependency
    from video_processing import process_frame_queue, get_video_length, has_video_stream
    
    video_path_obj = Path(video_path)
    
    # Get shared resources
    resources = _get_shared_resources()
    SCREENSHOT_DIR = resources['SCREENSHOT_DIR']
    frame_extraction_queue = resources['frame_extraction_queue']
    
    # Create screenshots directory if it doesn't exist
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    # Generate screenshot filename based on movie name and timestamp
    suffix = "_subs" if subtitle_path else ""
    screenshot_path = generate_screenshot_filename(video_path, timestamp_seconds, suffix=suffix, movie_id=movie_id)
    
    # Check if screenshot already exists
    if screenshot_path.exists():
        logger.info(f"Screenshot already exists, skipping queue: {screenshot_path.name} (subtitle_path={subtitle_path})")
        add_scan_log_func("info", f"Screenshot already exists: {screenshot_path.name}")
        # Sync to database if missing (file exists but not in DB)
        if movie_id:
            if not sync_existing_screenshot(movie_id, screenshot_path, timestamp_seconds):
                logger.error(f"Failed to sync existing screenshot to database: movie_id={movie_id}, path={screenshot_path.name}. This is a bug, not a transient error.")
                add_scan_log_func("error", f"Database sync failed: {screenshot_path.name}")
        return str(screenshot_path)
    
    logger.debug(f"Screenshot does not exist, will queue: {screenshot_path.name} (subtitle_path={subtitle_path})")
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg_func(load_config_func)
    if not ffmpeg_exe:
        add_scan_log_func("warning", f"ffmpeg not found, skipping screenshot extraction")
        logger.warning(f"ffmpeg not found, skipping screenshot extraction for {video_path}")
        return None
    
    # Queue it for background processing
    # Map priority label to numeric (lower number = higher priority)
    prio_map = {"user_high": 0, "high": 1, "normal": 5, "low": 9}
    prio_value = prio_map.get(priority or "normal", 5)
    frame_extraction_queue.put((
        prio_value,
        time.time(),  # tie-breaker for FIFO within same priority
        {
            "video_path": video_path,
            "timestamp_seconds": timestamp_seconds,
            "subtitle_path": subtitle_path,
            "ffmpeg_exe": ffmpeg_exe,
            "load_config_func": load_config_func,
            "find_ffmpeg_func": find_ffmpeg_func,
            "scan_progress_dict": scan_progress_dict,
            "add_scan_log_func": add_scan_log_func,
            "movie_id": movie_id  # Pass movie_id to avoid path lookup issues
        }
    ))
    queue_size = frame_extraction_queue.qsize()
    scan_progress_dict["frame_queue_size"] = queue_size
    scan_progress_dict["frames_total"] = scan_progress_dict.get("frames_total", 0) + 1
    logger.info(f"Queued screenshot extraction: video={Path(video_path).name}, timestamp={timestamp_seconds}s, subtitle_path={subtitle_path}, queue_size={queue_size}")
    add_scan_log_func("info", f"Queued screenshot extraction (queue: {queue_size})")
    # Ensure worker is running to process the queue
    try:
        process_frame_queue(3, scan_progress_dict, add_scan_log_func)
    except Exception as e:
        logger.error(f"Failed to start process_frame_queue: {e}", exc_info=True)
    return None  # Return None to indicate it's queued, will be processed later


def process_screenshot_extraction_worker(screenshot_info):
    """Worker function to extract a screenshot - runs in thread pool"""
    # Import here to avoid circular dependency
    from video_processing import (
        _ffmpeg_job, get_video_length, has_video_stream,
        increment_active_extractions, decrement_active_extractions
    )
    
    try:
        video_path = screenshot_info["video_path"]
        timestamp_seconds = screenshot_info["timestamp_seconds"]
        subtitle_path = screenshot_info.get("subtitle_path")
        ffmpeg_exe = screenshot_info["ffmpeg_exe"]
        scan_progress_dict = screenshot_info["scan_progress_dict"]
        add_scan_log_func = screenshot_info["add_scan_log_func"]
        
        logger.info(f"process_screenshot_extraction_worker: {Path(video_path).name} at {timestamp_seconds}s, subtitle_path={subtitle_path}")

        # Check if video stream exists before proceeding
        # This prevents "Output file #0 does not contain any stream" errors for audio files
        if not has_video_stream(video_path):
            logger.info(f"No video stream found in {video_path}, skipping screenshot extraction")
            # We return True to indicate "success" in handling this item (by skipping it)
            # rather than failing and potentially retrying or logging errors.
            return True

        # Get shared resources
        resources = _get_shared_resources()
        SCREENSHOT_DIR = resources['SCREENSHOT_DIR']
        screenshot_completion_times = resources['screenshot_completion_times']
        screenshot_completion_lock = resources['screenshot_completion_lock']
        process_executor = resources['process_executor']
        shutdown_flag = resources['shutdown_flag']
        increment_active_extractions = resources['increment_active_extractions']
        decrement_active_extractions = resources['decrement_active_extractions']
        frame_extraction_queue = resources['frame_extraction_queue']
        
        # Get (or compute) screenshot output path
        if "screenshot_path" in screenshot_info:
            screenshot_path = Path(screenshot_info["screenshot_path"])
        else:
            length = get_video_length(video_path)
            if length and timestamp_seconds > length:
                timestamp_seconds = min(30, max(10, length * 0.1))
            suffix = "_subs" if subtitle_path else ""
            movie_id_from_info = screenshot_info.get("movie_id")
            screenshot_path = generate_screenshot_filename(video_path, timestamp_seconds, suffix=suffix, movie_id=movie_id_from_info)

        # Early-out if already exists (quick DB sync only, no ffmpeg)
        if screenshot_path.exists():
            logger.info(f"Screenshot file exists on disk, skipping extraction: {screenshot_path.name} (subtitle_path={subtitle_path})")
            add_scan_log_func("info", f"Screenshot already exists: {screenshot_path.name}")
            
            # Sync to database if missing (file exists but not in DB)
            movie_id_to_use = screenshot_info.get("movie_id")
            if not movie_id_to_use:
                logger.error(f"movie_id not provided when syncing existing screenshot {screenshot_path.name}. This is a programming error - movie_id must be passed.")
                add_scan_log_func("error", f"Programming error: movie_id missing when syncing screenshot")
                return True
            
            if not sync_existing_screenshot(movie_id_to_use, screenshot_path, timestamp_seconds):
                logger.error(f"Failed to sync existing screenshot to database: movie_id={movie_id_to_use}, path={screenshot_path.name}. This is a bug, not a transient error.")
                add_scan_log_func("error", f"Database sync failed: {screenshot_path.name}")
            
            return True
        
        logger.info(f"Screenshot file does not exist, proceeding with extraction: {screenshot_path.name} (subtitle_path={subtitle_path})")

        def _on_done(fut: Future):
            try:
                try:
                    result = fut.result()
                except Exception as e:
                    add_scan_log_func("error", f"Screenshot extraction error callback: {e}")
                    return

                out_path = Path(result.get("out_path", ""))
                vid_path = result.get("video_path", video_path)
                rc = result.get("returncode", -99)
                elapsed = result.get("elapsed", 0.0)

                if elapsed > 1:
                    add_scan_log_func("warning", f"Screenshot extraction took {elapsed:.1f}s (expected <1s)")

                if rc == 0 and out_path.exists():
                    # Save screenshot to database (no retries - failures are bugs, not transient errors)
                    movie_id_to_use = screenshot_info.get("movie_id")
                    if not movie_id_to_use:
                        logger.error(f"movie_id not provided when saving screenshot {out_path.name}. This is a programming error - movie_id must be passed.")
                        add_scan_log_func("error", f"Programming error: movie_id missing when saving screenshot")
                        return
                    
                    if save_screenshot_to_db(movie_id_to_use, out_path, timestamp_seconds):
                        # Success - update progress
                        scan_progress_dict["frames_processed"] = scan_progress_dict.get("frames_processed", 0) + 1
                        scan_progress_dict["frame_queue_size"] = frame_extraction_queue.qsize()
                        # Track completion time
                        with screenshot_completion_lock:
                            screenshot_completion_times.append(time.time())
                            if len(screenshot_completion_times) > 1000:
                                screenshot_completion_times.pop(0)
                        si = screenshot_info.get("screenshot_index", None)
                        ts = screenshot_info.get("total_screenshots", None)
                        if si and ts:
                            add_scan_log_func("success", f"Screenshot {si}/{ts} extracted: {Path(vid_path).name}")
                        else:
                            add_scan_log_func("success", f"Screenshot extracted: {Path(vid_path).name}")
                    else:
                        logger.error(f"Failed to save screenshot to database: movie_id={movie_id_to_use}, path={out_path.name}. This is a bug, not a transient error. File exists on disk but will not be displayed.")
                        add_scan_log_func("error", f"Database save failed: {out_path.name}")
                        # File exists but not in DB - will be caught by sync function if called
                else:
                    rc = result.get("returncode", -99)
                    stderr_msg = result.get("stderr", "") or ""
                    stdout_msg = result.get("stdout", "") or ""
                    out_path = Path(result.get("out_path", ""))
                    stderr_preview = (stderr_msg[:200] + "...") if len(stderr_msg) > 200 else stderr_msg
                    stdout_preview = (stdout_msg[:200] + "...") if len(stdout_msg) > 200 else stdout_msg
                    error_detail = f"exit={rc}"
                    if stderr_preview:
                        error_detail += f", stderr={stderr_preview}"
                    if stdout_preview:
                        error_detail += f", stdout={stdout_preview}"
                    file_exists = out_path.exists() if out_path else False
                    error_msg = f"Screenshot extraction failed: {Path(vid_path).name} at {timestamp_seconds}s - {error_detail}"
                    if file_exists:
                        error_msg += f" (output file exists: {out_path.name})"
                    logger.error(error_msg)
                    add_scan_log_func("error", f"Screenshot extraction failed: {Path(vid_path).name} at {timestamp_seconds}s - exit={rc}")
            except Exception as e:
                logger.error(f"Error in _on_done callback: {e}", exc_info=True)
            finally:
                decrement_active_extractions()

        if shutdown_flag.is_set():
            return False

        # Dispatch to process pool
        # Note: process_executor is managed by video_processing module

        # Submit job
        logger.info(f"Submitting ffmpeg job: video={Path(video_path).name}, timestamp={timestamp_seconds}s, subtitle_path={subtitle_path}, output={screenshot_path.name}")
        increment_active_extractions()
        try:
            future = process_executor.submit(_ffmpeg_job, str(video_path), float(timestamp_seconds), ffmpeg_exe, str(screenshot_path), subtitle_path)
            future.add_done_callback(_on_done)
            # Do not block here; success indicates submission happened
            return True
        except Exception as submit_err:
            decrement_active_extractions()
            logger.error(f"Failed to submit ffmpeg job: {submit_err}", exc_info=True)
            return False
            
    except Exception as e:
        add_scan_log_func("error", f"Screenshot extraction error: {Path(video_path).name} - {str(e)[:80]}")
        logger.error(f"Error extracting screenshot from {video_path}: {e}")
        return False


def extract_screenshots(video_path, num_screenshots, load_config_func, find_ffmpeg_func, add_scan_log_func=None, scan_progress_dict=None):
    """Queue screenshot extractions for async processing"""
    # Import here to avoid circular dependency
    from video_processing import process_frame_queue, get_video_length, has_video_stream
    
    video_path_obj = Path(video_path)
    video_name = video_path_obj.name
    
    # Get shared resources
    resources = _get_shared_resources()
    SCREENSHOT_DIR = resources['SCREENSHOT_DIR']
    frame_extraction_queue = resources['frame_extraction_queue']
    
    # Create screenshots directory if it doesn't exist
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    # Generate screenshot filename based on video hash
    video_hash = hashlib.md5(str(video_path).encode()).hexdigest()[:8]
    screenshot_base = SCREENSHOT_DIR / f"{video_hash}"
    
    # Check if screenshots already exist
    existing_screenshots = []
    for i in range(num_screenshots):
        screenshot_path = screenshot_base.parent / f"{screenshot_base.name}_{i+1}.jpg"
        if screenshot_path.exists():
            existing_screenshots.append(str(screenshot_path))
    
    if len(existing_screenshots) == num_screenshots:
        if add_scan_log_func:
            add_scan_log_func("info", f"  All {num_screenshots} screenshots already exist")
        return existing_screenshots
    
    # Check if video stream exists
    if not has_video_stream(video_path):
        if add_scan_log_func:
            add_scan_log_func("info", f"  Skipping audio-only file (no video stream)")
        return existing_screenshots if existing_screenshots else []
    
    # Try to get video length
    if add_scan_log_func:
        add_scan_log_func("info", f"  Getting video length...")
    length = get_video_length(video_path)
    if not length or length < 1:
        if add_scan_log_func:
            add_scan_log_func("warning", f"  Could not determine video length, skipping screenshots")
        logger.warning(f"Could not determine video length for {video_path}, skipping screenshots")
        return existing_screenshots if existing_screenshots else []
    
    if add_scan_log_func:
        add_scan_log_func("info", f"  Video length: {length:.1f}s")
    
    # Find ffmpeg
    if add_scan_log_func:
        add_scan_log_func("info", f"  Finding ffmpeg...")
    ffmpeg_exe = find_ffmpeg_func(load_config_func)
    if not ffmpeg_exe:
        error_msg = "ffmpeg not found, skipping screenshot extraction"
        if add_scan_log_func:
            add_scan_log_func("warning", f"  {error_msg}")
        logger.warning(f"{error_msg} for {video_path}")
        return existing_screenshots if existing_screenshots else []
    
    if add_scan_log_func:
        add_scan_log_func("info", f"  Using ffmpeg: {Path(ffmpeg_exe).name}")
    
    # Queue it for background processing
    if scan_progress_dict is None or add_scan_log_func is None:
        logger.error(f"extract_screenshots called without required scan_progress_dict and add_scan_log_func. Screenshots will not be queued.")
        return existing_screenshots
    
    # Queue each screenshot extraction individually
    for i in range(num_screenshots):
        screenshot_path = screenshot_base.parent / f"{screenshot_base.name}_{i+1}.jpg"
        if screenshot_path.exists():
            continue  # Skip existing screenshots
        
        # Calculate timestamp (distribute evenly across video)
        timestamp = (length / (num_screenshots + 1)) * (i + 1)
        
        # Normal priority for background/batch work
        frame_extraction_queue.put((
            5,  # normal priority
            time.time(),
            {
                "video_path": video_path,
                "timestamp_seconds": timestamp,
                "ffmpeg_exe": ffmpeg_exe,
                "load_config_func": load_config_func,
                "find_ffmpeg_func": find_ffmpeg_func,
                "scan_progress_dict": scan_progress_dict,
                "add_scan_log_func": add_scan_log_func,
                "screenshot_index": i + 1,
                "total_screenshots": num_screenshots,
                "screenshot_path": str(screenshot_path)
            }
        ))
    
    queue_size = frame_extraction_queue.qsize()
    scan_progress_dict["frame_queue_size"] = queue_size
    scan_progress_dict["frames_total"] = scan_progress_dict.get("frames_total", 0) + num_screenshots
    if add_scan_log_func:
        add_scan_log_func("info", f"  Queued {num_screenshots} screenshot extractions (queue: {queue_size})")
    # Ensure worker is running to process the queue
    try:
        process_frame_queue(3, scan_progress_dict, add_scan_log_func)
    except Exception:
        pass
    return existing_screenshots  # Return existing screenshots immediately, rest will be processed in background

