"""
Video processing, subprocess management, and frame extraction for Movie Searcher.
"""
import os
import subprocess
import threading
import time
import hashlib
import re
import logging
from pathlib import Path
from queue import Queue
from concurrent.futures import ThreadPoolExecutor

# Import for video length extraction
try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# Import database models and session
from database import SessionLocal, Movie, Screenshot

logger = logging.getLogger(__name__)

# Configuration - will be set by main.py
SCRIPT_DIR = None
SCREENSHOT_DIR = None
# Cache for resolved tool paths
_CACHED_FFMPEG_PATH = None

def initialize_video_processing(script_dir):
    """Initialize video processing with script directory"""
    global SCRIPT_DIR, SCREENSHOT_DIR
    SCRIPT_DIR = Path(script_dir)
    SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"

# Shutdown and process tracking
shutdown_flag = threading.Event()
active_subprocesses = []  # List of active subprocess.Popen objects
active_subprocesses_lock = threading.Lock()

# Frame extraction queue and executor
frame_extraction_queue = Queue()
frame_executor = None
frame_processing_active = False

def register_subprocess(proc: subprocess.Popen):
    """Register a subprocess so it can be killed on shutdown"""
    with active_subprocesses_lock:
        active_subprocesses.append(proc)

def unregister_subprocess(proc: subprocess.Popen):
    """Unregister a subprocess when it completes"""
    with active_subprocesses_lock:
        if proc in active_subprocesses:
            active_subprocesses.remove(proc)

def kill_all_ffmpeg_processes():
    """Kill all ffmpeg processes on the system"""
    try:
        import platform
        if platform.system() == "Windows":
            # Windows: use taskkill
            subprocess.run(["taskkill", "/F", "/IM", "ffmpeg.exe"], 
                         capture_output=True, timeout=5)
        else:
            # Unix: use pkill
            subprocess.run(["pkill", "-9", "ffmpeg"], 
                         capture_output=True, timeout=5)
        logger.info("Killed all ffmpeg processes")
    except Exception as e:
        logger.warning(f"Error killing ffmpeg processes: {e}")

def kill_all_active_subprocesses():
    """Kill all registered subprocesses"""
    with active_subprocesses_lock:
        for proc in active_subprocesses[:]:  # Copy list to avoid modification during iteration
            try:
                if proc.poll() is None:  # Process still running
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                unregister_subprocess(proc)
            except Exception as e:
                logger.warning(f"Error killing subprocess: {e}")
        active_subprocesses.clear()
    kill_all_ffmpeg_processes()

def run_interruptible_subprocess(cmd, timeout=30, capture_output=True):
    """Run a subprocess that can be interrupted by shutdown flag"""
    if shutdown_flag.is_set():
        return None
    
    proc = None
    start_time = time.time()
    try:
        # Diagnostic: Time subprocess creation
        create_start = time.time()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        create_time = time.time() - create_start
        if create_time > 0.1:
            cmd_name = Path(cmd[0]).name if cmd else "unknown"
            logger.warning(f"Subprocess {cmd_name} creation took {create_time:.2f}s (slow)")
        
        register_subprocess(proc)
        
        try:
            # Diagnostic: Time the actual communication/wait
            comm_start = time.time()
            stdout, stderr = proc.communicate(timeout=timeout)
            comm_time = time.time() - comm_start
            elapsed = time.time() - start_time
            
            cmd_name = Path(cmd[0]).name if cmd else "unknown"
            if elapsed > 1:
                logger.warning(f"Subprocess {cmd_name} took {elapsed:.2f}s total (create: {create_time:.3f}s, execute: {comm_time:.2f}s)")
                if stderr:
                    stderr_preview = stderr.decode('utf-8', errors='ignore')[:500]
                    logger.debug(f"Stderr preview: {stderr_preview}")
            
            return subprocess.CompletedProcess(
                cmd, proc.returncode, stdout, stderr
            )
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            cmd_name = Path(cmd[0]).name if cmd else "unknown"
            logger.error(f"Subprocess {cmd_name} timed out after {elapsed:.1f}s (timeout={timeout}s)")
            if proc.poll() is None:
                logger.error(f"Process still running, killing...")
            proc.kill()
            proc.wait()
            raise
    except KeyboardInterrupt:
        if proc:
            proc.kill()
            proc.wait()
        raise
    finally:
        if proc:
            unregister_subprocess(proc)

def get_video_length(file_path):
    """Extract video length using mutagen if available, otherwise return None"""
    if not HAS_MUTAGEN:
        return None
    
    try:
        audio = MutagenFile(file_path)
        if audio is not None and hasattr(audio, 'info'):
            length = getattr(audio.info, 'length', None)
            return length
    except:
        pass
    return None

def validate_ffmpeg_path(ffmpeg_path):
    """Validate that an ffmpeg path exists and is executable"""
    if not ffmpeg_path:
        return False, "Path is empty"
    
    path_obj = Path(ffmpeg_path)
    
    # Check if file exists
    if not path_obj.exists():
        return False, f"Path does not exist: {ffmpeg_path}"
    
    # Check if it's a file (not a directory)
    if not path_obj.is_file():
        return False, f"Path is not a file: {ffmpeg_path}"
    
    # Try to execute ffmpeg -version to verify it's actually ffmpeg
    try:
        result = subprocess.run([str(path_obj), "-version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            return True, "Valid"
        else:
            return False, f"ffmpeg -version returned non-zero exit code: {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg -version timed out"
    except Exception as e:
        return False, f"Error executing ffmpeg: {str(e)}"

def find_ffmpeg(load_config_func):
    """Find ffmpeg executable - requires configured path, no fallbacks"""
    global _CACHED_FFMPEG_PATH
    if _CACHED_FFMPEG_PATH:
        return _CACHED_FFMPEG_PATH
    
    config = load_config_func()
    configured_path = config.get("ffmpeg_path")
    
    if not configured_path:
        logger.error("ffmpeg_path not configured. Set ffmpeg_path in configuration to use frame extraction.")
        return None
    
    # Validate the configured path
    is_valid, error_msg = validate_ffmpeg_path(configured_path)
    if is_valid:
        logger.info(f"Using configured ffmpeg path: {configured_path}")
        _CACHED_FFMPEG_PATH = configured_path
        return _CACHED_FFMPEG_PATH
    else:
        logger.error(f"Configured ffmpeg path is invalid: {configured_path} - {error_msg}")
        logger.error("Please fix the ffmpeg_path configuration. Frame extraction will not work until this is corrected.")
        return None

def generate_screenshot_filename(video_path, timestamp_seconds):
    """Generate a sensible screenshot filename based on movie name and timestamp"""
    video_path_obj = Path(video_path)
    movie_name = video_path_obj.stem  # Get filename without extension
    
    # Sanitize filename: remove invalid characters for Windows/Linux
    # Replace invalid filename characters with underscore
    sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', movie_name)
    # Remove leading/trailing dots and spaces
    sanitized_name = sanitized_name.strip('. ')
    # Limit length to avoid filesystem issues
    if len(sanitized_name) > 100:
        sanitized_name = sanitized_name[:100]
    
    # Format: movie_name_screenshot150s.jpg
    screenshot_filename = f"{sanitized_name}_screenshot{int(timestamp_seconds)}s.jpg"
    return SCREENSHOT_DIR / screenshot_filename

def extract_movie_screenshot_sync(video_path, timestamp_seconds, find_ffmpeg_func):
    """Extract a single screenshot from video synchronously (blocking)"""
    video_path_obj = Path(video_path)
    
    # Create screenshots directory if it doesn't exist
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    # Generate screenshot filename based on movie name and timestamp
    screenshot_path = generate_screenshot_filename(video_path, timestamp_seconds)
    
    # Check if screenshot already exists
    if screenshot_path.exists():
        return str(screenshot_path)
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg_func()
    if not ffmpeg_exe:
        logger.warning(f"ffmpeg not found, skipping screenshot extraction for {video_path}")
        return None
    
    # Try to get video length to validate timestamp
    length = get_video_length(video_path)
    if length and timestamp_seconds > length:
        # If requested timestamp is beyond video length, use 30 seconds or 10% into the video, whichever is smaller
        timestamp_seconds = min(30, max(10, length * 0.1))
        logger.info(f"Timestamp exceeds video length {length}s, using {timestamp_seconds}s instead")
    
    # Extract screenshot
    try:
        # Fast seek: place -ss before -i to avoid decoding from start
        cmd = [
            ffmpeg_exe,
            "-ss", str(timestamp_seconds),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",  # High quality
            "-y",  # Overwrite
            str(screenshot_path)
        ]
        
        logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")
        start_time = time.time()
        result = run_interruptible_subprocess(cmd, timeout=30, capture_output=True)
        elapsed = time.time() - start_time
        if elapsed > 1:
            logger.warning(f"ffmpeg took {elapsed:.2f}s for screenshot extraction from {video_path}")
        if result and result.returncode == 0 and screenshot_path.exists():
            logger.info(f"Extracted screenshot from {video_path} at {timestamp_seconds}s")
            return str(screenshot_path)
        elif result:
            error_msg = result.stderr.decode() if result.stderr else 'Unknown error'
            logger.warning(f"Failed to extract screenshot from {video_path}: {error_msg}")
            return None
        else:
            return None
    except subprocess.TimeoutExpired:
        logger.warning(f"Screenshot extraction timed out for {video_path}")
        return None
    except Exception as e:
        logger.error(f"Error extracting screenshot from {video_path}: {e}")
        return None

def extract_movie_screenshot(video_path, timestamp_seconds, async_mode, load_config_func, find_ffmpeg_func, scan_progress_dict, add_scan_log_func):
    """Extract a single screenshot from video - can be synchronous or queued for async processing"""
    video_path_obj = Path(video_path)
    
    # Create screenshots directory if it doesn't exist
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    # Generate screenshot filename based on movie name and timestamp
    screenshot_path = generate_screenshot_filename(video_path, timestamp_seconds)
    
    # Check if screenshot already exists
    if screenshot_path.exists():
        add_scan_log_func("info", f"Screenshot already exists: {screenshot_path.name}")
        return str(screenshot_path)
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg_func(load_config_func)
    if not ffmpeg_exe:
        add_scan_log_func("warning", f"ffmpeg not found, skipping screenshot extraction")
        logger.warning(f"ffmpeg not found, skipping screenshot extraction for {video_path}")
        return None
    
    # If async mode, queue it for background processing
    if async_mode:
        global frame_extraction_queue
        frame_extraction_queue.put({
            "video_path": video_path,
            "timestamp_seconds": timestamp_seconds,
            "ffmpeg_exe": ffmpeg_exe,
            "load_config_func": load_config_func,
            "find_ffmpeg_func": find_ffmpeg_func,
            "scan_progress_dict": scan_progress_dict,
            "add_scan_log_func": add_scan_log_func
        })
        scan_progress_dict["frame_queue_size"] = frame_extraction_queue.qsize()
        scan_progress_dict["frames_total"] = scan_progress_dict.get("frames_total", 0) + 1
        add_scan_log_func("info", f"Queued screenshot extraction (queue: {frame_extraction_queue.qsize()})")
        return None  # Return None to indicate it's queued, will be processed later
    else:
        # Synchronous mode
        return extract_movie_screenshot_sync(video_path, timestamp_seconds, lambda: find_ffmpeg_func(load_config_func))

def process_screenshot_extraction_worker(screenshot_info):
    """Worker function to extract a screenshot - runs in thread pool"""
    try:
        video_path = screenshot_info["video_path"]
        timestamp_seconds = screenshot_info["timestamp_seconds"]
        ffmpeg_exe = screenshot_info["ffmpeg_exe"]
        scan_progress_dict = screenshot_info["scan_progress_dict"]
        add_scan_log_func = screenshot_info["add_scan_log_func"]
        
        # Get screenshot path (either provided or generate from timestamp)
        if "screenshot_path" in screenshot_info:
            screenshot_path = Path(screenshot_info["screenshot_path"])
        else:
            # Try to get video length to validate timestamp
            length = get_video_length(video_path)
            if length and timestamp_seconds > length:
                timestamp_seconds = min(30, max(10, length * 0.1))
            screenshot_path = generate_screenshot_filename(video_path, timestamp_seconds)
        
        # Check if screenshot already exists
        if screenshot_path.exists():
            add_scan_log_func("info", f"Screenshot already exists: {screenshot_path.name}")
            # Still save to database if needed
            db = SessionLocal()
            try:
                movie = db.query(Movie).filter(Movie.path == video_path).first()
                if movie:
                    existing = db.query(Screenshot).filter(Screenshot.movie_id == movie.id, Screenshot.shot_path == str(screenshot_path)).first()
                    if not existing:
                        screenshot = Screenshot(movie_id=movie.id, shot_path=str(screenshot_path))
                        db.add(screenshot)
                        db.commit()
            finally:
                db.close()
            return True
        
        # Get screenshot index info if available
        screenshot_index = screenshot_info.get("screenshot_index", None)
        total_screenshots = screenshot_info.get("total_screenshots", None)
        if screenshot_index and total_screenshots:
            add_scan_log_func("info", f"Extracting screenshot {screenshot_index}/{total_screenshots}: {Path(video_path).name} at {timestamp_seconds:.1f}s...")
        else:
            add_scan_log_func("info", f"Extracting screenshot: {Path(video_path).name} at {timestamp_seconds:.1f}s...")
        
        # Diagnostic: Check file access
        video_path_obj = Path(video_path)
        try:
            file_size = video_path_obj.stat().st_size
            file_size_mb = file_size / (1024 * 1024)
            add_scan_log_func("info", f"File size: {file_size_mb:.1f}MB")
        except Exception as e:
            add_scan_log_func("warning", f"Could not stat file: {e}")
        
        # Fast seek: place -ss before -i to avoid decoding from start
        cmd = [
            ffmpeg_exe,
            "-ss", str(timestamp_seconds),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            "-y",
            str(screenshot_path)
        ]
        
        if shutdown_flag.is_set():
            return False
        
        add_scan_log_func("info", f"Running: {Path(ffmpeg_exe).name} -ss {timestamp_seconds:.1f}s -i [video] -vframes 1")
        start_time = time.time()
        result = run_interruptible_subprocess(cmd, timeout=30, capture_output=True)
        elapsed = time.time() - start_time
        
        if elapsed > 1:
            add_scan_log_func("warning", f"Screenshot extraction took {elapsed:.1f}s (expected <1s)")
        if result and result.returncode == 0 and Path(screenshot_path).exists():
            # Save to database
            db = SessionLocal()
            try:
                # Get movie ID from path
                movie = db.query(Movie).filter(Movie.path == video_path).first()
                if not movie:
                    logger.warning(f"Movie not found for screenshot extraction: {video_path}")
                    return False
                
                # Check if entry already exists
                existing = db.query(Screenshot).filter(Screenshot.movie_id == movie.id, Screenshot.shot_path == str(screenshot_path)).first()
                if not existing:
                    screenshot = Screenshot(
                        movie_id=movie.id,
                        shot_path=str(screenshot_path)
                    )
                    db.add(screenshot)
                    db.commit()
                
                scan_progress_dict["frames_processed"] = scan_progress_dict.get("frames_processed", 0) + 1
                scan_progress_dict["frame_queue_size"] = frame_extraction_queue.qsize()
                if screenshot_index and total_screenshots:
                    add_scan_log_func("success", f"Screenshot {screenshot_index}/{total_screenshots} extracted: {Path(video_path).name}")
                else:
                    add_scan_log_func("success", f"Screenshot extracted: {Path(video_path).name}")
                logger.info(f"Extracted screenshot from {video_path}")
            finally:
                db.close()
            return True
        else:
            error_msg = result.stderr.decode('utf-8', errors='ignore') if result and result.stderr else 'Unknown error'
            add_scan_log_func("error", f"Screenshot extraction failed: {Path(video_path).name} - {error_msg[:80]}")
            logger.warning(f"Failed to extract screenshot from {video_path}: {error_msg}")
            return False
    except subprocess.TimeoutExpired:
        add_scan_log_func("error", f"Screenshot extraction timed out: {Path(video_path).name}")
        logger.warning(f"Screenshot extraction timed out for {video_path}")
        return False
    except Exception as e:
        add_scan_log_func("error", f"Screenshot extraction error: {Path(video_path).name} - {str(e)[:80]}")
        logger.error(f"Error extracting screenshot from {video_path}: {e}")
        return False

def process_frame_queue(max_workers, scan_progress_dict, add_scan_log_func):
    """Process queued screenshot extractions in background thread pool"""
    global frame_executor, frame_processing_active, frame_extraction_queue
    
    if frame_processing_active:
        return
    
    frame_processing_active = True
    add_scan_log_func("info", "Starting background screenshot extraction...")
    
    def worker():
        global frame_executor
        frame_executor = ThreadPoolExecutor(max_workers=max_workers)
        
        # Continue processing while queue has items or scan is still running
        processed_count = 0
        while not shutdown_flag.is_set():
            try:
                # Get screenshot info from queue (with timeout to periodically check scan status)
                try:
                    screenshot_info = frame_extraction_queue.get(timeout=2)
                except:
                    # Queue empty, check if scan is done and queue is truly empty
                    if shutdown_flag.is_set():
                        break
                    if not scan_progress_dict.get("is_scanning", False) and frame_extraction_queue.empty():
                        break
                    continue
                
                # Submit to thread pool (non-blocking)
                future = frame_executor.submit(process_screenshot_extraction_worker, screenshot_info)
                processed_count += 1
                frame_extraction_queue.task_done()
                
                # Don't wait for result here - let it run in parallel
                # Just track that we submitted it
                
            except Exception as e:
                logger.error(f"Error in screenshot extraction worker: {e}")
        
        # Shutdown executor with timeout (interruptible)
        if frame_executor:
            frame_executor.shutdown(wait=False)  # Don't wait, allow interruption
            # Give a short time for tasks to finish, then kill subprocesses
            time.sleep(0.5)
            kill_all_active_subprocesses()
        
        global frame_processing_active
        frame_processing_active = False
        remaining = frame_extraction_queue.qsize()
        if remaining == 0:
            add_scan_log_func("success", f"All screenshot extractions completed ({processed_count} processed)")
        else:
            add_scan_log_func("warning", f"Screenshot extraction stopped with {remaining} items remaining")
    
    # Start worker thread
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

def extract_screenshots(video_path, num_screenshots, load_config_func, find_ffmpeg_func, add_scan_log_func=None, async_mode=True, scan_progress_dict=None):
    """Extract screenshots from video using ffmpeg - can be synchronous or queued for async processing"""
    video_path_obj = Path(video_path)
    video_name = video_path_obj.name
    
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
    
    # If async mode, queue it for background processing
    if async_mode and scan_progress_dict is not None and add_scan_log_func is not None:
        global frame_extraction_queue
        # Queue each screenshot extraction individually
        for i in range(num_screenshots):
            screenshot_path = screenshot_base.parent / f"{screenshot_base.name}_{i+1}.jpg"
            if screenshot_path.exists():
                continue  # Skip existing screenshots
            
            # Calculate timestamp (distribute evenly across video)
            timestamp = (length / (num_screenshots + 1)) * (i + 1)
            
            frame_extraction_queue.put({
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
            })
        
        queue_size = frame_extraction_queue.qsize()
        scan_progress_dict["frame_queue_size"] = queue_size
        scan_progress_dict["frames_total"] = scan_progress_dict.get("frames_total", 0) + num_screenshots
        if add_scan_log_func:
            add_scan_log_func("info", f"  Queued {num_screenshots} screenshot extractions (queue: {queue_size})")
        return existing_screenshots  # Return existing screenshots immediately, rest will be processed in background
    
    # Synchronous mode (for backwards compatibility or when async_mode=False)
    screenshots = existing_screenshots.copy()
    try:
        for i in range(num_screenshots):
            screenshot_path = screenshot_base.parent / f"{screenshot_base.name}_{i+1}.jpg"
            if screenshot_path.exists():
                screenshots.append(str(screenshot_path))
                if add_scan_log_func:
                    add_scan_log_func("info", f"  Screenshot {i+1}/{num_screenshots} already exists")
                continue
            
            # Calculate timestamp (distribute evenly across video)
            timestamp = (length / (num_screenshots + 1)) * (i + 1)
            
            if add_scan_log_func:
                add_scan_log_func("info", f"  Extracting screenshot {i+1}/{num_screenshots} at {timestamp:.1f}s...")
            
            # Basic ffmpeg command (no optimizations - diagnose why it's slow)
            cmd = [
                ffmpeg_exe,
                "-i", str(video_path),
                "-ss", str(timestamp),
                "-vframes", "1",
                "-q:v", "2",  # High quality
                "-y",  # Overwrite
                str(screenshot_path)
            ]
            
            if shutdown_flag.is_set():
                if add_scan_log_func:
                    add_scan_log_func("warning", f"  Screenshot extraction interrupted")
                break
            
            start_time = time.time()
            try:
                result = run_interruptible_subprocess(cmd, timeout=30, capture_output=True)
                elapsed = time.time() - start_time
                
                if add_scan_log_func:
                    add_scan_log_func("info", f"  Subprocess total time: {elapsed:.2f}s")
                
                if result and result.returncode == 0 and screenshot_path.exists():
                    file_size = screenshot_path.stat().st_size
                    if add_scan_log_func:
                        add_scan_log_func("success", f"  Screenshot {i+1}/{num_screenshots} extracted in {elapsed:.2f}s ({file_size/1024:.1f}KB)")
                    screenshots.append(str(screenshot_path))
                elif result:
                    error_msg = result.stderr.decode('utf-8', errors='ignore') if result.stderr else 'Unknown error'
                    error_preview = error_msg[:200] + "..." if len(error_msg) > 200 else error_msg
                    if add_scan_log_func:
                        add_scan_log_func("error", f"  Screenshot {i+1}/{num_screenshots} failed (exit {result.returncode}): {error_preview}")
                    logger.warning(f"Failed to extract screenshot {i+1} from {video_path}: {error_preview}")
            except subprocess.TimeoutExpired:
                elapsed = time.time() - start_time
                if add_scan_log_func:
                    add_scan_log_func("error", f"  Screenshot {i+1}/{num_screenshots} timed out after {elapsed:.1f}s at {timestamp:.1f}s")
                logger.warning(f"Screenshot {i+1} extraction timed out for {video_path} at {timestamp:.1f}s (took {elapsed:.1f}s)")
    except subprocess.TimeoutExpired:
        if add_scan_log_func:
            add_scan_log_func("error", f"  Screenshot extraction timed out for {video_name}")
        logger.warning(f"Screenshot extraction timed out for {video_path}")
    except Exception as e:
        if add_scan_log_func:
            add_scan_log_func("error", f"  Screenshot extraction error: {str(e)[:100]}")
        logger.error(f"Error extracting screenshots from {video_path}: {e}", exc_info=True)
    
    if add_scan_log_func:
        if screenshots:
            add_scan_log_func("success", f"  Extracted {len(screenshots)}/{num_screenshots} screenshot(s)")
        else:
            add_scan_log_func("warning", f"  No screenshots extracted")
    
    return screenshots

