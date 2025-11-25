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
from queue import Queue, PriorityQueue
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, Future

# Video length extraction will use ffprobe (from the configured ffmpeg bundle)

# Import database models and session
from database import SessionLocal, Movie, Screenshot

# Import screenshot synchronization functions
from screenshot_sync import sync_existing_screenshot, save_screenshot_to_db

logger = logging.getLogger(__name__)

# Configuration - will be set by main.py
SCRIPT_DIR = None
SCREENSHOT_DIR = None
# Cache for resolved tool paths
_CACHED_FFMPEG_PATH = None

# Import subtitle functions from video.subtitle module
from video.subtitle import parse_srt_at_timestamp, burn_subtitle_text_onto_image

def _ffmpeg_job(video_path_local, ts, ffmpeg, out_path, subtitle_path=None):
    logger.info(f"_ffmpeg_job called: video={Path(video_path_local).name}, ts={ts}s, subtitle_path={subtitle_path}, out_path={Path(out_path).name}")
    try:
        # Resolve paths
        video_path_normalized = Path(video_path_local).resolve()
        
        # Build ffmpeg command to extract frame WITHOUT subtitles
        # We'll add subtitles using PIL after extraction
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-ss", str(ts),
            "-i", str(video_path_normalized),
            "-vf", "scale=iw:ih",  # Preserve aspect ratio
            "-vframes", "1",
            "-q:v", "2",
            "-y",
            str(Path(out_path).resolve())
        ]
        
        logger.debug(f"ffmpeg command: {' '.join(cmd)}")
        start = time.time()
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
        elapsed = time.time() - start
        
        if proc.returncode == 0 and Path(out_path).exists():
            logger.info(f"Extracted frame successfully")
            
            # If subtitle path provided, burn subtitle text onto the image
            if subtitle_path and os.path.exists(subtitle_path):
                subtitle_text = parse_srt_at_timestamp(subtitle_path, ts)
                if subtitle_text:
                    logger.info(f"Found subtitle text at {ts}s: {subtitle_text[:50]}...")
                    success = burn_subtitle_text_onto_image(out_path, subtitle_text)
                    if not success:
                        logger.warning(f"Failed to burn subtitle text onto {out_path}")
                else:
                    logger.debug(f"No subtitle text found at timestamp {ts}s")
        
        logger.info(f"_ffmpeg_job completed: returncode={proc.returncode}, elapsed={elapsed:.2f}s, output_exists={Path(out_path).exists()}")
        if proc.returncode != 0:
            stderr_full = (proc.stderr.decode("utf-8", "ignore") if proc.stderr else "")
            stdout_full = (proc.stdout.decode("utf-8", "ignore") if proc.stdout else "")
            stderr_preview = stderr_full[:500] if len(stderr_full) > 500 else stderr_full
            stdout_preview = stdout_full[:200] if len(stdout_full) > 200 else stdout_full
            
            # Collect diagnostic information
            video_exists = Path(video_path_normalized).exists()
            output_dir = Path(out_path).parent
            output_dir_exists = output_dir.exists()
            output_dir_writable = os.access(output_dir, os.W_OK) if output_dir_exists else False
            output_file_exists = Path(out_path).exists()
            
            # Try to get video length to check if timestamp is valid
            video_length = None
            try:
                video_length = get_video_length(str(video_path_normalized))
            except:
                pass
            
            error_msg = f"_ffmpeg_job failed: video={Path(video_path_local).name}, ts={ts}s, returncode={proc.returncode}"
            if stderr_preview:
                error_msg += f", stderr={stderr_preview}"
            elif not stderr_full:
                error_msg += f", stderr=(empty)"
            if stdout_preview:
                error_msg += f", stdout={stdout_preview}"
            
            # Add diagnostic info
            error_msg += f", video_exists={video_exists}"
            if not video_exists:
                error_msg += f", video_path={video_path_normalized}"
            error_msg += f", output_dir_exists={output_dir_exists}"
            if output_dir_exists:
                error_msg += f", output_dir_writable={output_dir_writable}"
            else:
                error_msg += f", output_dir={output_dir}"
            if not output_file_exists:
                error_msg += f", output_file_missing={Path(out_path).name}"
            if video_length is not None:
                error_msg += f", video_length={video_length:.1f}s"
                if ts > video_length:
                    error_msg += f", timestamp_exceeds_length=True"
            
            logger.error(error_msg)
            
        return {
            "returncode": proc.returncode,
            "stderr": (proc.stderr.decode("utf-8", "ignore") if proc.stderr else ""),
            "stdout": (proc.stdout.decode("utf-8", "ignore") if proc.stdout else ""),
            "elapsed": elapsed,
            "out_path": str(out_path),
            "video_path": str(video_path_local)
        }
    except subprocess.TimeoutExpired:
        logger.error(f"_ffmpeg_job timed out after 30s")
        return {
            "returncode": -1,
            "stderr": "timeout",
            "stdout": "",
            "elapsed": 30.0,
            "out_path": str(out_path),
            "video_path": str(video_path_local)
        }
    except Exception as e:
        logger.error(f"_ffmpeg_job exception: {e}", exc_info=True)
        return {
            "returncode": -2,
            "stderr": str(e),
            "stdout": "",
            "elapsed": 0.0,
            "out_path": str(out_path),
            "video_path": str(video_path_local)
        }

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
# Use PriorityQueue so interactive/on-demand work can preempt backlog
frame_extraction_queue = PriorityQueue()
frame_executor = None
process_executor = None
frame_processing_active = False

# Track completion timestamps for rate calculation
screenshot_completion_times = []
screenshot_completion_lock = threading.Lock()

# Track active extractions to prevent premature shutdown
active_extractions_count = 0
active_extractions_lock = threading.Lock()

def increment_active_extractions():
    global active_extractions_count
    with active_extractions_lock:
        active_extractions_count += 1

def decrement_active_extractions():
    global active_extractions_count
    with active_extractions_lock:
        active_extractions_count = max(0, active_extractions_count - 1)

def get_active_extractions():
    with active_extractions_lock:
        return active_extractions_count

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

def run_interruptible_subprocess(cmd, timeout=30, capture_output=True, cwd=None):
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
            cwd=cwd,
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

def _get_ffprobe_path_from_config() -> str:
    """
    Get ffprobe path from config file.
    NO STRING MANIPULATION - we ONLY use the explicitly stored and tested ffprobe_path.
    If not configured or invalid, return None.
    """
    from config import load_config
    config = load_config()
    ffprobe_path = config.get('ffprobe_path')
    
    if not ffprobe_path:
        logger.error("ffprobe_path not configured. Setup must test and save ffprobe_path before use.")
        return None
    
    ffprobe_path = str(ffprobe_path)
    
    # Verify the path still exists
    if not os.path.exists(ffprobe_path):
        logger.error(f"Stored ffprobe_path no longer exists: {ffprobe_path}")
        return None
        
    return ffprobe_path

def get_video_length(file_path):
    """
    Extract video length using ffprobe from the configured ffmpeg bundle.
    Returns duration in seconds as float, or None if unavailable.
    """
    ffprobe = _get_ffprobe_path_from_config()
    if not ffprobe:
        return None
    try:
        cmd = [
            ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(file_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            logger.warning(f"ffprobe failed for {file_path}: rc={result.returncode}, err={result.stderr.strip()}")
            return None
        out = (result.stdout or "").strip()
        if not out:
            return None
        try:
            return float(out)
        except ValueError:
            # Sometimes ffprobe prints as key=value; try to parse numeric tail
            m = re.search(r'([0-9]+(?:\\.[0-9]+)?)', out)
            return float(m.group(1)) if m else None
    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe timeout for {file_path}")
        return None
    except Exception as e:
        logger.error(f"Error running ffprobe for {file_path}: {e}")
        return None

def has_video_stream(file_path):
    """
    Check if file has a video stream using ffprobe.
    Returns True if a video stream is present, False otherwise.
    """
    ffprobe = _get_ffprobe_path_from_config()
    if not ffprobe:
        return False
        
    try:
        cmd = [
            ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=nw=1:nk=1",
            str(file_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False
        return result.stdout.strip() == "video"
    except Exception as e:
        logger.error(f"Error checking video stream for {file_path}: {e}")
        return False

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

def test_ffmpeg_comprehensive(ffmpeg_path, ffprobe_path=None):
    """
    Comprehensive test of ffmpeg installation.
    Tests both ffmpeg and ffprobe executables.
    Returns dict with status, errors, and details.
    
    Args:
        ffmpeg_path: Path to ffmpeg executable
        ffprobe_path: Optional path to ffprobe executable. If provided, this path will be used directly
                     instead of trying to derive it from ffmpeg_path.
    """
    if not ffmpeg_path:
        return {
            "ok": False,
            "ffmpeg_ok": False,
            "ffprobe_ok": False,
            "errors": ["ffmpeg path not configured"],
            "ffmpeg_path": None,
            "ffprobe_path": None,
            "ffmpeg_version": None,
            "ffprobe_version": None
        }
    
    results = {
        "ok": True,
        "ffmpeg_ok": False,
        "ffprobe_ok": False,
        "errors": [],
        "ffmpeg_path": str(ffmpeg_path),
        "ffprobe_path": None,
        "ffmpeg_version": None,
        "ffprobe_version": None
    }
    
    # Test ffmpeg
    ffmpeg_path_obj = Path(ffmpeg_path)
    if not ffmpeg_path_obj.exists():
        results["ok"] = False
        results["ffmpeg_ok"] = False
        results["errors"].append(f"ffmpeg executable not found: {ffmpeg_path}")
        return results
    
    try:
        result = subprocess.run([str(ffmpeg_path_obj), "-version"], capture_output=True, timeout=5, text=True)
        if result.returncode == 0:
            results["ffmpeg_ok"] = True
            # Extract version from output (first line usually contains version)
            if result.stdout:
                first_line = result.stdout.split('\n')[0]
                results["ffmpeg_version"] = first_line.strip()
        else:
            results["ok"] = False
            results["errors"].append(f"ffmpeg -version failed with exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        results["ok"] = False
        results["errors"].append("ffmpeg -version timed out")
    except Exception as e:
        results["ok"] = False
        results["errors"].append(f"Error testing ffmpeg: {str(e)}")
    
    # Test ffprobe
    # If ffprobe_path is provided, use it directly (e.g., from find_ffmpeg_and_ffprobe_in_winget)
    if ffprobe_path:
        ffprobe_candidates = [Path(ffprobe_path)]
    else:
        # Try multiple locations to find ffprobe
        ffprobe_candidates = [
            # Same directory as ffmpeg
            ffmpeg_path_obj.parent / "ffprobe.exe",
            # Same directory, different name pattern
            Path(str(ffmpeg_path_obj).replace("ffmpeg.exe", "ffprobe.exe")),
            # Check PATH
            None  # Will check via shutil.which below
        ]
        
        # Also check PATH for ffprobe
        import shutil
        ffprobe_in_path = shutil.which("ffprobe")
        if ffprobe_in_path:
            ffprobe_candidates.insert(0, Path(ffprobe_in_path))
        
        # For winget installations, check sibling directories
        if "WinGet" in str(ffmpeg_path_obj) or "winget" in str(ffmpeg_path_obj).lower():
            # Look in parent directories for ffprobe
            current_dir = ffmpeg_path_obj.parent
            for _ in range(3):  # Check up to 3 levels up
                parent_dir = current_dir.parent
                for subdir in parent_dir.iterdir():
                    if subdir.is_dir():
                        probe_candidate = subdir / "bin" / "ffprobe.exe"
                        if probe_candidate.exists():
                            ffprobe_candidates.insert(0, probe_candidate)
                current_dir = parent_dir
    
    ffprobe_found = False
    for ffprobe_candidate in ffprobe_candidates:
        if ffprobe_candidate is None:
            continue
        if ffprobe_candidate.exists():
            results["ffprobe_path"] = str(ffprobe_candidate)
            try:
                result = subprocess.run([str(ffprobe_candidate), "-version"], capture_output=True, timeout=5, text=True)
                if result.returncode == 0:
                    results["ffprobe_ok"] = True
                    ffprobe_found = True
                    if result.stdout:
                        first_line = result.stdout.split('\n')[0]
                        results["ffprobe_version"] = first_line.strip()
                    break
                else:
                    results["errors"].append(f"ffprobe -version failed with exit code {result.returncode} at {ffprobe_candidate}")
            except subprocess.TimeoutExpired:
                results["errors"].append(f"ffprobe -version timed out at {ffprobe_candidate}")
            except Exception as e:
                results["errors"].append(f"Error testing ffprobe at {ffprobe_candidate}: {str(e)}")
    
    if not ffprobe_found:
        results["ok"] = False
        if not results["errors"]:
            results["errors"].append(f"ffprobe not found. Checked: {[str(c) for c in ffprobe_candidates if c]}")
    
    # Overall status
    if not results["ffmpeg_ok"] or not results["ffprobe_ok"]:
        results["ok"] = False
    
    return results

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

# Import screenshot functions from video.screenshot module (at end to avoid circular import)
# These will be imported after module initialization

# Re-export screenshot functions for backward compatibility
# The actual implementations are in video.screenshot module
def _import_screenshot_functions():
    """Lazy import to avoid circular dependencies"""
    from video.screenshot import (
        generate_screenshot_filename,
        extract_movie_screenshot,
        process_screenshot_extraction_worker,
        extract_screenshots
    )
    return {
        'generate_screenshot_filename': generate_screenshot_filename,
        'extract_movie_screenshot': extract_movie_screenshot,
        'process_screenshot_extraction_worker': process_screenshot_extraction_worker,
        'extract_screenshots': extract_screenshots
    }

# Create lazy wrapper functions
def generate_screenshot_filename(*args, **kwargs):
    return _import_screenshot_functions()['generate_screenshot_filename'](*args, **kwargs)

def extract_movie_screenshot(*args, **kwargs):
    return _import_screenshot_functions()['extract_movie_screenshot'](*args, **kwargs)

def process_screenshot_extraction_worker(*args, **kwargs):
    return _import_screenshot_functions()['process_screenshot_extraction_worker'](*args, **kwargs)

def extract_screenshots(*args, **kwargs):
    return _import_screenshot_functions()['extract_screenshots'](*args, **kwargs)

# Import screenshot functions at module end (after all definitions to avoid circular imports)
# The actual implementations are in video.screenshot module
# Old duplicate function definitions removed - using lazy wrappers above instead

def process_frame_queue(max_workers, scan_progress_dict, add_scan_log_func):
    """Process queued screenshot extractions in background thread pool"""
    global frame_executor, process_executor, frame_processing_active, frame_extraction_queue
    
    queue_size = frame_extraction_queue.qsize()
    logger.info(f"process_frame_queue called: max_workers={max_workers}, queue_size={queue_size}, frame_processing_active={frame_processing_active}")
    
    if frame_processing_active:
        # Log that it's already running to aid diagnostics
        logger.info(f"Background screenshot extraction already running (queue: {queue_size}), skipping start")
        try:
            add_scan_log_func("info", f"Background screenshot extraction already running (queue: {queue_size})")
        except Exception:
            pass
        return
    
    frame_processing_active = True
    logger.info(f"Starting background screenshot extraction worker: queue_size={queue_size}, max_workers={max_workers}")
    add_scan_log_func("info", f"Starting background screenshot extraction... (queue: {queue_size})")
    
    def worker():
        # Thread pool here is only used to parallelize lightweight submission if desired
        # We prioritize process-based parallelism for ffmpeg itself.
        global frame_executor, process_executor
        frame_executor = ThreadPoolExecutor(max_workers=max_workers)
        if process_executor is None:
            workers = max(2, min(6, (os.cpu_count() or 4)))
            process_executor = ProcessPoolExecutor(max_workers=workers)
        
        # Continue processing while queue has items or scan is still running
        processed_count = 0
        logger.info(f"Worker thread started, entering main loop")
        while not shutdown_flag.is_set():
            try:
                queue_size = frame_extraction_queue.qsize()
                # Get screenshot info from queue (with timeout to periodically check scan status)
                try:
                    queued_item = frame_extraction_queue.get(timeout=2)
                    logger.debug(f"Got item from queue (remaining: {frame_extraction_queue.qsize()})")
                except:
                    # Queue empty, check if scan is done and queue is truly empty
                    queue_size = frame_extraction_queue.qsize()
                    is_scanning = scan_progress_dict.get("is_scanning", False)
                    logger.debug(f"Queue get timeout: queue_size={queue_size}, is_scanning={is_scanning}, shutdown={shutdown_flag.is_set()}")
                    if shutdown_flag.is_set():
                        logger.info("Shutdown flag set, breaking worker loop")
                        break
                    if not is_scanning and frame_extraction_queue.empty():
                        # Wait for active extractions to complete before shutting down
                        active_count = get_active_extractions()
                        if active_count > 0:
                            logger.debug(f"Queue empty and scan done, but {active_count} extractions active. Waiting...")
                            time.sleep(0.5)
                            continue
                            
                        logger.info(f"Queue empty, scan not running, and no active extractions. Breaking worker loop (processed: {processed_count})")
                        break
                    continue

                # Support both (priority, ts, info) and legacy dicts
                if isinstance(queued_item, tuple) and len(queued_item) == 3:
                    _, _, screenshot_info = queued_item
                else:
                    screenshot_info = queued_item

                video_path = screenshot_info.get("video_path", "unknown")
                timestamp = screenshot_info.get("timestamp_seconds", "unknown")
                logger.info(f"Processing screenshot: {Path(video_path).name} at {timestamp}s (processed: {processed_count + 1})")

                # Submit a light task to thread pool that will in turn submit to process pool
                future = frame_executor.submit(process_screenshot_extraction_worker, screenshot_info)
                processed_count += 1
                frame_extraction_queue.task_done()
                
                # Don't wait for result here - let it run in parallel
                # Just track that we submitted it
                
            except Exception as e:
                logger.error(f"Error in screenshot extraction worker: {e}", exc_info=True)
        
        # Shutdown executor with timeout (interruptible)
        if frame_executor:
            frame_executor.shutdown(wait=False)  # Don't wait, allow interruption
            # Give a short time for tasks to finish
            time.sleep(0.5)
            
            # Only kill subprocesses on forced shutdown
            if shutdown_flag.is_set():
                kill_all_active_subprocesses()
                
            # Allow clean recreation on next start
            frame_executor = None
        if process_executor:
            try:
                process_executor.shutdown(wait=False, cancel_futures=False)
            except Exception:
                pass
            # Allow clean recreation on next start
            process_executor = None
        
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

