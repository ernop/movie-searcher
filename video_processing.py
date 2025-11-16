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

# Import PIL at module level so it's available in subprocesses
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None
    ImageDraw = None
    ImageFont = None

# Import database models and session
from database import SessionLocal, Movie, Screenshot, Config

logger = logging.getLogger(__name__)

# Configuration - will be set by main.py
SCRIPT_DIR = None
SCREENSHOT_DIR = None
# Cache for resolved tool paths
_CACHED_FFMPEG_PATH = None

def _parse_srt_at_timestamp(srt_path, timestamp_seconds):
    """Parse SRT file and return subtitle text at given timestamp
    
    Returns:
        str or None: Subtitle text if found at timestamp, None otherwise
    """
    try:
        # Try different encodings to read the SRT file
        content = None
        encodings = ['utf-8', 'latin-1', 'windows-1252', 'iso-8859-1', 'cp1252']
        
        for encoding in encodings:
            try:
                with open(srt_path, 'r', encoding=encoding) as f:
                    content = f.read()
                break  # Successfully read with this encoding
            except (UnicodeDecodeError, LookupError):
                continue
        
        if content is None:
            # If all encodings fail, try with errors='ignore'
            with open(srt_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        
        # Parse SRT format: 
        # Number
        # HH:MM:SS,mmm --> HH:MM:SS,mmm
        # Text (can be multiline)
        # Empty line
        pattern = r'(?:\d+\s*\n)?(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n(.+?)(?=\n\n|\n\d+\s*\n\d{2}:|\Z)'
        matches = re.findall(pattern, content, re.DOTALL)
        
        for match in matches:
            start_h, start_m, start_s, start_ms = int(match[0]), int(match[1]), int(match[2]), int(match[3])
            end_h, end_m, end_s, end_ms = int(match[4]), int(match[5]), int(match[6]), int(match[7])
            start_sec = start_h * 3600 + start_m * 60 + start_s + start_ms / 1000
            end_sec = end_h * 3600 + end_m * 60 + end_s + end_ms / 1000
            
            # Check if timestamp falls within this subtitle's time range
            if start_sec <= timestamp_seconds <= end_sec:
                text = match[8].strip()
                # Clean up HTML tags and excessive newlines
                text = re.sub(r'<[^>]+>', '', text)  # Remove HTML tags
                text = re.sub(r'\n+', '\n', text)  # Normalize newlines
                return text.strip()
        
        return None
    except Exception as e:
        logger.error(f"Error parsing SRT file {srt_path}: {e}")
        return None

def _burn_subtitle_text_onto_image(image_path, subtitle_text):
    """Burn subtitle text onto an image using PIL/Pillow - standard subtitle appearance
    
    Args:
        image_path: Path to image file
        subtitle_text: Text to overlay (can be multiline)
    
    Returns:
        bool: True if successful, False otherwise
    """
    if not PIL_AVAILABLE:
        logger.error(f"PIL/Pillow not available - cannot burn subtitles. Please install Pillow: pip install Pillow")
        return False
    
    try:
        
        # Open image
        img = Image.open(image_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        draw = ImageDraw.Draw(img)
        
        # Use standard subtitle font size (48px for better visibility)
        font_size = 48
        font = None
        
        # Try standard subtitle fonts in order
        font_paths = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/verdana.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
        ]
        
        # Load font
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    break
                except Exception as e:
                    logger.debug(f"Failed to load font {font_path}: {e}")
                    continue
        
        # Fallback to default font if no TrueType font found
        if not font:
            font = ImageFont.load_default()
        
        # Handle multiline text - split by newlines
        lines = subtitle_text.split('\n')
        lines = [line.strip() for line in lines if line.strip()]  # Remove empty lines
        
        if not lines:
            logger.warning(f"No text to burn after splitting: '{subtitle_text}'")
            return False
        
        # Get text dimensions for each line
        line_heights = []
        line_widths = []
        for line in lines:
            try:
                # Use textsize for compatibility (works in older PIL)
                w, h = draw.textsize(line, font=font)
                line_widths.append(w)
                line_heights.append(h)
            except AttributeError:
                # Fallback for newer PIL versions that removed textsize
                try:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    w = bbox[2] - bbox[0]
                    h = bbox[3] - bbox[1]
                    line_widths.append(w)
                    line_heights.append(h)
                except Exception as e:
                    logger.error(f"Failed to measure text size: {e}")
                    return False
        
        # Calculate total height and max width
        total_height = sum(line_heights) + (len(lines) - 1) * 5  # 5px spacing between lines
        max_width = max(line_widths) if line_widths else 0
        
        # Position at bottom center (standard subtitle position)
        x = (img.size[0] - max_width) // 2
        y = img.size[1] - total_height - 40  # 40px margin from bottom
        
        # Draw each line
        current_y = y
        for i, line in enumerate(lines):
            line_w = line_widths[i]
            line_x = (img.size[0] - line_w) // 2  # Center each line individually
            
            # Draw black outline (standard subtitle outline)
            outline_range = 2
            for x_offset in range(-outline_range, outline_range + 1):
                for y_offset in range(-outline_range, outline_range + 1):
                    if x_offset != 0 or y_offset != 0:  # Skip center position
                        draw.text((line_x + x_offset, current_y + y_offset), line, font=font, fill='black')
            
            # Draw white text on top (standard subtitle color)
            draw.text((line_x, current_y), line, font=font, fill='white')
            
            # Move to next line
            current_y += line_heights[i] + 5
        
        # Save the modified image
        img.save(image_path)
        logger.info(f"Successfully burned subtitle text onto {image_path}: '{subtitle_text[:50].replace(chr(10), ' ')}...'")
        return True
    except Exception as e:
        logger.error(f"Error burning subtitle text onto {image_path}: {e}", exc_info=True)
        return False

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
                subtitle_text = _parse_srt_at_timestamp(subtitle_path, ts)
                if subtitle_text:
                    logger.info(f"Found subtitle text at {ts}s: {subtitle_text[:50]}...")
                    success = _burn_subtitle_text_onto_image(out_path, subtitle_text)
                    if not success:
                        logger.warning(f"Failed to burn subtitle text onto {out_path}")
                else:
                    logger.debug(f"No subtitle text found at timestamp {ts}s")
        
        logger.info(f"_ffmpeg_job completed: returncode={proc.returncode}, elapsed={elapsed:.2f}s, output_exists={Path(out_path).exists()}")
        if proc.returncode != 0:
            stderr_preview = (proc.stderr.decode("utf-8", "ignore")[:200] if proc.stderr else "")
            logger.error(f"_ffmpeg_job failed: stderr={stderr_preview}")
            
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
    Resolve ffprobe path based on configured ffmpeg_path in the database config.
    We do not use PATH fallbacks. If not configured or invalid, return None.
    """
    db = SessionLocal()
    try:
        row = db.query(Config).filter(Config.key == 'ffmpeg_path').first()
        if not row or not row.value:
            logger.error("ffmpeg_path not configured in database; cannot determine ffprobe path for duration extraction.")
            return None
        try:
            import json as _json
            cfg = _json.loads(row.value) if isinstance(row.value, str) else row.value
            ffmpeg_path = cfg if isinstance(cfg, str) else cfg.get("path") or cfg.get("ffmpeg_path") or cfg.get("value")
            if not ffmpeg_path:
                ffmpeg_path = row.value if isinstance(row.value, str) else None
        except Exception:
            ffmpeg_path = row.value if isinstance(row.value, str) else None
        if not ffmpeg_path:
            logger.error("ffmpeg_path config present but empty/unusable.")
            return None
        ffmpeg_path = str(ffmpeg_path)
        ffprobe_candidate = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
        if os.path.exists(ffprobe_candidate):
            return ffprobe_candidate
        logger.error(f"Derived ffprobe not found next to configured ffmpeg: {ffprobe_candidate}")
        return None
    finally:
        db.close()

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

def generate_screenshot_filename(video_path, timestamp_seconds, suffix=""):
    """Generate a sensible screenshot filename based on movie name and timestamp
    
    Args:
        video_path: Path to video file
        timestamp_seconds: Timestamp in seconds
        suffix: Optional suffix to add before .jpg (e.g., "_subs" for subtitles)
    """
    video_path_obj = Path(video_path)
    
    # Try to get cleaned movie name from database
    movie_name = None
    try:
        db = SessionLocal()
        try:
            movie = db.query(Movie).filter(Movie.path == str(video_path_obj.resolve())).first()
            if movie and movie.name:
                movie_name = movie.name
        finally:
            db.close()
    except Exception:
        pass  # Fall back to raw filename if DB lookup fails
    
    # Fall back to raw filename if no cleaned name found
    if not movie_name:
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

def extract_movie_screenshot_sync(video_path, timestamp_seconds, find_ffmpeg_func, subtitle_path=None):
    """Extract a single screenshot from video synchronously (blocking)
    
    Args:
        video_path: Path to video file
        timestamp_seconds: Timestamp in seconds to extract screenshot
        find_ffmpeg_func: Function to find ffmpeg executable
        subtitle_path: Optional path to subtitle file to burn in
    """
    video_path_obj = Path(video_path)
    
    # Create screenshots directory if it doesn't exist
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    # Generate screenshot filename based on movie name and timestamp
    # Include subtitle indicator in filename if burning subtitles
    suffix = "_subs" if subtitle_path else ""
    screenshot_path = generate_screenshot_filename(video_path, timestamp_seconds, suffix=suffix)
    
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
        # Resolve paths
        video_path_normalized = Path(video_path).resolve()
        
        # Build ffmpeg command to extract frame WITHOUT subtitles
        # We'll add subtitles using PIL after extraction
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel", "error",
            "-ss", str(timestamp_seconds),
            "-i", str(video_path_normalized),
            "-vf", "scale=iw:ih",  # Preserve aspect ratio
            "-vframes", "1",
            "-q:v", "2",  # High quality
            "-y",  # Overwrite
            str(screenshot_path.resolve())
        ]
        
        logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")
        start_time = time.time()
        result = run_interruptible_subprocess(cmd, timeout=30, capture_output=True)
        elapsed = time.time() - start_time
        if elapsed > 1:
            logger.warning(f"ffmpeg took {elapsed:.2f}s for screenshot extraction from {video_path}")
        
        if result and result.returncode == 0 and screenshot_path.exists():
            logger.info(f"Extracted screenshot from {video_path} at {timestamp_seconds}s")
            
            # If subtitle path provided, burn subtitle text onto the image using PIL
            if subtitle_path and os.path.exists(subtitle_path):
                subtitle_text = _parse_srt_at_timestamp(subtitle_path, timestamp_seconds)
                if subtitle_text:
                    logger.info(f"Found subtitle text at {timestamp_seconds}s: {subtitle_text[:50]}...")
                    success = _burn_subtitle_text_onto_image(str(screenshot_path), subtitle_text)
                    if not success:
                        logger.warning(f"Failed to burn subtitle text onto {screenshot_path}")
                else:
                    logger.debug(f"No subtitle text found at timestamp {timestamp_seconds}s")
            
            return str(screenshot_path)
        elif result:
            stderr_msg = result.stderr.decode('utf-8', errors='ignore') if result.stderr else ''
            stdout_msg = result.stdout.decode('utf-8', errors='ignore') if result.stdout else ''
            stderr_preview = (stderr_msg[:200] + "...") if len(stderr_msg) > 200 else stderr_msg
            stdout_preview = (stdout_msg[:200] + "...") if len(stdout_msg) > 200 else stdout_msg
            error_detail = f"exit={result.returncode}"
            if stderr_preview:
                error_detail += f", stderr={stderr_preview}"
            if stdout_preview:
                error_detail += f", stdout={stdout_preview}"
            logger.warning(f"Failed to extract screenshot from {video_path}: {error_detail}")
            return None
        else:
            return None
    except subprocess.TimeoutExpired:
        logger.warning(f"Screenshot extraction timed out for {video_path}")
        return None
    except Exception as e:
        logger.error(f"Error extracting screenshot from {video_path}: {e}")
        return None

def extract_movie_screenshot(video_path, timestamp_seconds, async_mode, load_config_func, find_ffmpeg_func, scan_progress_dict, add_scan_log_func, priority: str = "normal", subtitle_path=None):
    """Extract a single screenshot from video - can be synchronous or queued for async processing
    
    Args:
        subtitle_path: Optional path to subtitle file to burn in
    """
    video_path_obj = Path(video_path)
    
    # Create screenshots directory if it doesn't exist
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    # Generate screenshot filename based on movie name and timestamp
    suffix = "_subs" if subtitle_path else ""
    screenshot_path = generate_screenshot_filename(video_path, timestamp_seconds, suffix=suffix)
    
    # Check if screenshot already exists
    if screenshot_path.exists():
        logger.info(f"Screenshot already exists, skipping queue: {screenshot_path.name} (subtitle_path={subtitle_path})")
        add_scan_log_func("info", f"Screenshot already exists: {screenshot_path.name}")
        return str(screenshot_path)
    
    logger.debug(f"Screenshot does not exist, will queue: {screenshot_path.name} (subtitle_path={subtitle_path})")
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg_func(load_config_func)
    if not ffmpeg_exe:
        add_scan_log_func("warning", f"ffmpeg not found, skipping screenshot extraction")
        logger.warning(f"ffmpeg not found, skipping screenshot extraction for {video_path}")
        return None
    
    # If async mode, queue it for background processing
    if async_mode:
        global frame_extraction_queue
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
                "add_scan_log_func": add_scan_log_func
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
    else:
        # Synchronous mode
        return extract_movie_screenshot_sync(video_path, timestamp_seconds, lambda: find_ffmpeg_func(load_config_func), subtitle_path=subtitle_path)

def process_screenshot_extraction_worker(screenshot_info):
    """Worker function to extract a screenshot - runs in thread pool"""
    try:
        video_path = screenshot_info["video_path"]
        timestamp_seconds = screenshot_info["timestamp_seconds"]
        subtitle_path = screenshot_info.get("subtitle_path")
        ffmpeg_exe = screenshot_info["ffmpeg_exe"]
        scan_progress_dict = screenshot_info["scan_progress_dict"]
        add_scan_log_func = screenshot_info["add_scan_log_func"]
        
        logger.info(f"process_screenshot_extraction_worker: {Path(video_path).name} at {timestamp_seconds}s, subtitle_path={subtitle_path}")

        # Get (or compute) screenshot output path
        if "screenshot_path" in screenshot_info:
            screenshot_path = Path(screenshot_info["screenshot_path"])
        else:
            length = get_video_length(video_path)
            if length and timestamp_seconds > length:
                timestamp_seconds = min(30, max(10, length * 0.1))
            suffix = "_subs" if subtitle_path else ""
            screenshot_path = generate_screenshot_filename(video_path, timestamp_seconds, suffix=suffix)

        # Early-out if already exists (quick DB sync only, no ffmpeg)
        if screenshot_path.exists():
            logger.info(f"Screenshot file exists on disk, skipping extraction: {screenshot_path.name} (subtitle_path={subtitle_path})")
            add_scan_log_func("info", f"Screenshot already exists: {screenshot_path.name}")
            db = SessionLocal()
            try:
                movie = db.query(Movie).filter(Movie.path == video_path).first()
                if movie:
                    existing = db.query(Screenshot).filter(Screenshot.movie_id == movie.id, Screenshot.shot_path == str(screenshot_path)).first()
                    if not existing:
                        logger.info(f"Adding screenshot to database: {screenshot_path.name}")
                        db.add(Screenshot(movie_id=movie.id, shot_path=str(screenshot_path), timestamp_seconds=timestamp_seconds))
                        db.commit()
                    else:
                        logger.debug(f"Screenshot already in database: {screenshot_path.name}")
            finally:
                db.close()
            return True
        
        logger.info(f"Screenshot file does not exist, proceeding with extraction: {screenshot_path.name} (subtitle_path={subtitle_path})")

        def _on_done(fut: Future):
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
                db = SessionLocal()
                try:
                    movie = db.query(Movie).filter(Movie.path == vid_path).first()
                    if movie:
                        existing = db.query(Screenshot).filter(Screenshot.movie_id == movie.id, Screenshot.shot_path == str(out_path)).first()
                        if not existing:
                            db.add(Screenshot(movie_id=movie.id, shot_path=str(out_path), timestamp_seconds=timestamp_seconds))
                            db.commit()
                            # Track completion time
                            with screenshot_completion_lock:
                                screenshot_completion_times.append(time.time())
                                # Keep only last 1000 timestamps to avoid memory growth
                                if len(screenshot_completion_times) > 1000:
                                    screenshot_completion_times.pop(0)
                    scan_progress_dict["frames_processed"] = scan_progress_dict.get("frames_processed", 0) + 1
                    scan_progress_dict["frame_queue_size"] = frame_extraction_queue.qsize()
                    si = screenshot_info.get("screenshot_index", None)
                    ts = screenshot_info.get("total_screenshots", None)
                    if si and ts:
                        add_scan_log_func("success", f"Screenshot {si}/{ts} extracted: {Path(vid_path).name}")
                    else:
                        add_scan_log_func("success", f"Screenshot extracted: {Path(vid_path).name}")
                finally:
                    db.close()
            else:
                rc = result.get("returncode", -99)
                stderr_msg = result.get("stderr", "") or ""
                stdout_msg = result.get("stdout", "") or ""
                stderr_preview = (stderr_msg[:200] + "...") if len(stderr_msg) > 200 else stderr_msg
                stdout_preview = (stdout_msg[:200] + "...") if len(stdout_msg) > 200 else stdout_msg
                error_detail = f"exit={rc}"
                if stderr_preview:
                    error_detail += f", stderr={stderr_preview}"
                if stdout_preview:
                    error_detail += f", stdout={stdout_preview}"
                add_scan_log_func("warning", f"Screenshot extraction failed: {Path(vid_path).name} - {error_detail}")

        if shutdown_flag.is_set():
            return False

        # Dispatch to process pool
        global process_executor
        if process_executor is None:
            # Default to a sensible parallelism
            workers = max(2, min(6, (os.cpu_count() or 4)))
            process_executor = ProcessPoolExecutor(max_workers=workers)

        # Submit job; if the executor was previously shut down, recreate and retry once
        logger.info(f"Submitting ffmpeg job: video={Path(video_path).name}, timestamp={timestamp_seconds}s, subtitle_path={subtitle_path}, output={screenshot_path.name}")
        try:
            future = process_executor.submit(_ffmpeg_job, str(video_path), float(timestamp_seconds), ffmpeg_exe, str(screenshot_path), subtitle_path)
        except Exception as submit_err:
            logger.error(f"Failed to submit ffmpeg job, will retry: {submit_err}", exc_info=True)
            # Handle 'cannot schedule new futures after shutdown' and similar states
            try:
                # Best-effort: shutdown in case it's a half-closed pool, then recreate
                process_executor.shutdown(wait=False, cancel_futures=False)
            except Exception:
                pass
            # Recreate a fresh executor and retry submission once
            workers = max(2, min(6, (os.cpu_count() or 4)))
            process_executor = ProcessPoolExecutor(max_workers=workers)
            logger.info(f"Retrying ffmpeg job submission with subtitle_path={subtitle_path}")
            future = process_executor.submit(_ffmpeg_job, str(video_path), float(timestamp_seconds), ffmpeg_exe, str(screenshot_path), subtitle_path)
        future.add_done_callback(_on_done)
        # Do not block here; success indicates submission happened
        return True
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
                        logger.info(f"Queue empty and scan not running, breaking worker loop (processed: {processed_count})")
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
            # Give a short time for tasks to finish, then kill subprocesses
            time.sleep(0.5)
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
                "-hide_banner",
                "-loglevel", "error",
                "-ss", str(timestamp),
                "-i", str(video_path),
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

