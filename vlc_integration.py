"""
VLC player integration and currently playing detection for Movie Searcher.

CRITICAL: When parsing VLC command lines from PowerShell, quotes MUST be stripped
from all arguments after parsing. See get_vlc_command_lines() docstring for details.
"""
import os
import subprocess
import shlex
import re
import logging
from pathlib import Path
from datetime import datetime
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import time
import json

if os.name == 'nt':
	# Windows-specific imports via ctypes to avoid extra dependencies
	import ctypes
	from ctypes import wintypes, Structure, POINTER, byref
	
	# Windows API structures for monitor enumeration
	class RECT(Structure):
		_fields_ = [("left", ctypes.c_long),
					("top", ctypes.c_long),
					("right", ctypes.c_long),
					("bottom", ctypes.c_long)]
	
	class MONITORINFO(Structure):
		_fields_ = [("cbSize", ctypes.c_ulong),
					("rcMonitor", RECT),
					("rcWork", RECT),
					("dwFlags", ctypes.c_ulong)]

# Import database models and session
from database import SessionLocal, Movie, LaunchHistory, WatchHistory

logger = logging.getLogger(__name__)

# Video and subtitle extensions (matching main.py)
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp'}
SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.vtt', '.ass', '.ssa'}

def find_subtitle_file(video_path):
    """Find subtitle file for a video
    
    Searches in:
    1. Current folder (same directory as video)
    2. "subs" folder (case insensitive) if it exists in the same directory
    
    Returns the first subtitle file found (any file with a subtitle extension).
    """
    video_path_obj = Path(video_path)
    video_dir = video_path_obj.parent
    
    # Search directories: current folder and "subs" folder (case insensitive)
    search_dirs = [video_dir]
    
    # Check if "subs" folder exists (case insensitive)
    for item in video_dir.iterdir():
        if item.is_dir() and item.name.lower() == "subs":
            search_dirs.append(item)
            break
    
    # Search in each directory for any file with a subtitle extension
    for search_dir in search_dirs:
        try:
            for item in search_dir.iterdir():
                if item.is_file() and item.suffix.lower() in SUBTITLE_EXTENSIONS:
                    return str(item)
        except (PermissionError, OSError):
            # Skip directories we can't read
            continue
    
    return None

def has_been_launched(movie_path):
    """Check if a movie has ever been launched"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.path == movie_path).first()
        if not movie:
            return False
        count = db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).count()
        return count > 0
    finally:
        db.close()

def find_vlc_executable():
    """Find VLC executable in common locations or PATH"""
    vlc_paths = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\VideoLAN\vlc.exe"),
        "vlc"  # If in PATH
    ]
    
    for path in vlc_paths:
        if path == "vlc":
            # Check if vlc is in PATH
            try:
                result = subprocess.run(["vlc", "--version"], capture_output=True, timeout=2)
                if result.returncode == 0:
                    return path
            except:
                continue
        elif os.path.exists(path):
            return path
    
    return None

def close_vlc_processes():
    """Close all running VLC processes"""
    try:
        if os.name == 'nt':  # Windows
            # Find all VLC processes
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq vlc.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                # Count processes
                lines = [line for line in result.stdout.strip().split('\n') if line.strip()]
                process_count = len(lines)
                
                # Close them
                kill_result = subprocess.run(
                    ["taskkill", "/F", "/IM", "vlc.exe"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if kill_result.returncode == 0:
                    return process_count
        else:
            # Linux/Mac - use pkill or killall
            try:
                result = subprocess.run(
                    ["pkill", "-f", "vlc"],
                    capture_output=True,
                    timeout=5
                )
                if result.returncode == 0:
                    return True
            except FileNotFoundError:
                # Try killall if pkill unavailable
                try:
                    subprocess.run(["killall", "vlc"], capture_output=True, timeout=5)
                    return True
                except FileNotFoundError:
                    logger.warning("Neither pkill nor killall available - cannot close VLC processes")
                    return False
    except Exception as e:
        logger.warning(f"Error closing VLC processes: {e}")
        return False
    
    return 0

def get_vlc_window_titles():
    """Get window titles from running VLC instances on Windows"""
    if os.name != 'nt':  # Windows only for now
        return []
    
    try:
        # Use PowerShell to get VLC window titles
        ps_command = """
        Get-Process | Where-Object {$_.ProcessName -eq 'vlc'} | ForEach-Object {
            $proc = $_
            Add-Type -TypeDefinition @"
                using System;
                using System.Runtime.InteropServices;
                public class Win32 {
                    [DllImport("user32.dll")]
                    public static extern IntPtr GetForegroundWindow();
                    [DllImport("user32.dll")]
                    public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
                }
"@
            $hwnd = $proc.MainWindowHandle
            if ($hwnd -ne [IntPtr]::Zero) {
                $title = New-Object System.Text.StringBuilder 256
                [Win32]::GetWindowText($hwnd, $title, $title.Capacity) | Out-Null
                $titleText = $title.ToString()
                if ($titleText) {
                    Write-Output "$titleText|$($proc.Id)"
                }
            }
        }
        """
        
        result = subprocess.run(
            ["powershell", "-Command", ps_command],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            titles = []
            for line in result.stdout.strip().split('\n'):
                if '|' in line:
                    title, pid = line.split('|', 1)
                    if title and title.strip():
                        titles.append({"title": title.strip(), "pid": pid.strip()})
            return titles
    except Exception as e:
        logger.warning(f"Error getting VLC window titles: {e}")
    
    return []

def get_vlc_command_lines():
    """Get command line arguments from running VLC processes (Windows).
    
    Returns a list of dicts with 'path' and 'pid' keys for each VLC process
    that has a video file in its command line.
    
    CRITICAL QUOTE-STRIPPING REQUIREMENT:
    PowerShell's Win32_Process.CommandLine returns paths with quotes preserved
    (e.g., '"C:\Program Files\VLC\vlc.exe" "D:\movies\file.mkv"'). When we parse
    this with shlex.split(), it correctly splits the arguments but may leave
    quotes in the resulting strings. Since os.path.exists() will fail on a path
    like '"D:\movies\file.mkv"' (with quotes), we MUST strip quotes from all
    arguments after parsing. This is why both the primary shlex.split() path
    and the fallback regex path strip quotes - it's essential for correct
    path detection.
    
    Without quote stripping, currently-playing detection silently fails because
    os.path.exists() returns False for quoted paths, causing valid VLC processes
    to be ignored.
    """
    if os.name != 'nt':
        return []

    try:
        # Use PowerShell CIM to get CommandLine and ProcessId for vlc.exe, return as JSON
        ps_script = (
            "Get-CimInstance Win32_Process -Filter \"name = 'vlc.exe'\" "
            "| Select-Object CommandLine, ProcessId "
            "| ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse PowerShell JSON for VLC processes: {e}")
            return []

        # Normalize to a list (PowerShell returns single object for one process, array for multiple)
        processes = data if isinstance(data, list) else [data]
        command_lines = []

        for proc in processes:
            cmd_line = (proc.get("CommandLine") or "").strip()
            pid = str(proc.get("ProcessId") or "").strip()
            if not cmd_line:
                continue
            
            # Parse command line arguments
            # PowerShell returns: '"C:\Program Files\VLC\vlc.exe" "D:\movies\file.mkv"'
            # We need to split this into individual arguments and strip quotes
            try:
                args = shlex.split(cmd_line, posix=False)
                # CRITICAL: Strip quotes - shlex.split() may leave quotes in the strings,
                # and os.path.exists() will fail on quoted paths like '"D:\path\file.mkv"'
                args = [a.strip('"') for a in args]
            except Exception:
                # Fallback: simple regex to extract quoted and unquoted args
                import re
                args = re.findall(r'"[^"]+"|[^\s]+', cmd_line)
                # CRITICAL: Strip quotes here too - same reason as above
                args = [a.strip('"') for a in args]

            # Skip empty or single-arg (just vlc.exe) invocations
            if len(args) <= 1:
                continue

            # Look for a path argument with a known video extension
            for arg in args[1:]:
                try:
                    # Skip command-line flags (e.g., --started-from-file, --sub-file, etc.)
                    if arg.startswith("-"):
                        continue
                    
                    # Normalize path for existence check (resolve relative paths, handle case)
                    # This ensures we match paths correctly even if they're stored differently in DB
                    try:
                        normalized_arg = str(Path(arg).resolve())
                    except (OSError, ValueError):
                        # If resolve fails (e.g., path doesn't exist), try original
                        normalized_arg = arg
                    
                    # Check if path exists and has a video extension
                    # Note: We check the normalized path but store the original arg
                    # to preserve the exact format from the command line
                    if os.path.exists(normalized_arg):
                        suffix = Path(normalized_arg).suffix.lower()
                        if suffix in VIDEO_EXTENSIONS:
                            command_lines.append({"path": normalized_arg, "pid": pid})
                            break
                except Exception as e:
                    # Log but continue - don't let one bad argument break the whole function
                    logger.debug(f"Error processing VLC command line argument '{arg}': {e}")
                    continue

        return command_lines
    except Exception as e:
        logger.warning(f"Error getting VLC command lines via PowerShell: {e}")
        return []

def _find_vlc_window_handle():
	"""Locate a VLC window handle on Windows by enumerating top-level windows.
	Returns the first matching HWND or 0 if none found.
	"""
	if os.name != 'nt':
		return 0

	user32 = ctypes.windll.user32
	kernel32 = ctypes.windll.kernel32

	EnumWindows = user32.EnumWindows
	EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
	IsWindowVisible = user32.IsWindowVisible
	GetWindowTextW = user32.GetWindowTextW
	GetWindowTextLengthW = user32.GetWindowTextLengthW

	found_hwnd = ctypes.c_void_p(0)

	def _callback(hwnd, lParam):
		if not IsWindowVisible(hwnd):
			return True
		length = GetWindowTextLengthW(hwnd)
		if length == 0:
			return True
		buffer = ctypes.create_unicode_buffer(length + 1)
		GetWindowTextW(hwnd, buffer, length + 1)
		title = buffer.value or ""
		# Heuristic: VLC window title usually contains "VLC"
		if "vlc" in title.lower():
			# Store and stop enumeration
			found_hwnd.value = hwnd
			return False
		return True

	EnumWindows(EnumWindowsProc(_callback), 0)
	return found_hwnd.value or 0

def _bring_window_to_foreground(hwnd):
	"""Bring a given window to the foreground on Windows."""
	if os.name != 'nt' or not hwnd:
		return False
	user32 = ctypes.windll.user32
	ShowWindow = user32.ShowWindow
	SetForegroundWindow = user32.SetForegroundWindow
	# SW_RESTORE = 9 brings the window to its previous size/position if minimized/maximized
	SW_RESTORE = 9
	ShowWindow(hwnd, SW_RESTORE)
	# Attempt to set foreground
	return bool(SetForegroundWindow(hwnd))

def _get_monitor_bounds():
	"""Get bounds of all monitors on Windows.
	Returns a list of RECT structures representing monitor boundaries.
	"""
	if os.name != 'nt':
		return []
	
	user32 = ctypes.windll.user32
	monitors = []
	
	# MONITORINFO structure
	MONITORINFOF_PRIMARY = 0x00000001
	
	def _monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
		mi = MONITORINFO()
		mi.cbSize = ctypes.sizeof(MONITORINFO)
		if user32.GetMonitorInfoW(hMonitor, byref(mi)):
			monitors.append(mi.rcMonitor)
		return True
	
	MonitorEnumProc = ctypes.WINFUNCTYPE(
		ctypes.c_bool,
		wintypes.HMONITOR,
		wintypes.HDC,
		POINTER(RECT),
		wintypes.LPARAM
	)
	
	user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_monitor_enum_proc), 0)
	return monitors

def _get_window_rect(hwnd):
	"""Get window rectangle (position and size) on Windows.
	Returns RECT structure or None on failure.
	"""
	if os.name != 'nt' or not hwnd:
		return None
	
	user32 = ctypes.windll.user32
	rect = RECT()
	if user32.GetWindowRect(hwnd, byref(rect)):
		return rect
	return None

def _rect_intersects(rect1, rect2):
	"""Check if two rectangles intersect."""
	return not (rect1.right <= rect2.left or 
				rect1.left >= rect2.right or 
				rect1.bottom <= rect2.top or 
				rect1.top >= rect2.bottom)

def _rect_contains(outer, inner):
	"""Check if outer rectangle fully contains inner rectangle."""
	return (inner.left >= outer.left and 
			inner.right <= outer.right and 
			inner.top >= outer.top and 
			inner.bottom <= outer.bottom)

def _ensure_window_in_single_monitor(hwnd):
	"""Ensure a window is fully contained within a single monitor.
	If the window spans multiple monitors, reposition/resize it to fit within one.
	Returns True if repositioned, False otherwise.
	"""
	if os.name != 'nt' or not hwnd:
		return False
	
	try:
		user32 = ctypes.windll.user32
		
		# Get window rectangle
		window_rect = _get_window_rect(hwnd)
		if not window_rect:
			return False
		
		# Get all monitors
		monitors = _get_monitor_bounds()
		if not monitors:
			return False
		
		# Check which monitors the window overlaps
		overlapping_monitors = []
		for i, monitor in enumerate(monitors):
			if _rect_intersects(window_rect, monitor):
				overlapping_monitors.append((i, monitor))
		
		# If window is already fully within a single monitor, no action needed
		if len(overlapping_monitors) == 1:
			monitor = overlapping_monitors[0][1]
			if _rect_contains(monitor, window_rect):
				return False
		
		# Window spans multiple monitors or extends beyond monitor bounds
		# Find the monitor that contains the center of the window
		window_center_x = (window_rect.left + window_rect.right) // 2
		window_center_y = (window_rect.top + window_rect.bottom) // 2
		
		target_monitor = None
		for i, monitor in enumerate(monitors):
			if (monitor.left <= window_center_x <= monitor.right and
				monitor.top <= window_center_y <= monitor.bottom):
				target_monitor = monitor
				break
		
		# If center not in any monitor, use primary monitor (first one)
		if not target_monitor:
			target_monitor = monitors[0]
		
		# Calculate new position and size
		window_width = window_rect.right - window_rect.left
		window_height = window_rect.bottom - window_rect.top
		monitor_width = target_monitor.right - target_monitor.left
		monitor_height = target_monitor.bottom - target_monitor.top
		
		# If window is too large for monitor, resize it
		if window_width > monitor_width:
			window_width = monitor_width - 20  # Leave small margin
		if window_height > monitor_height:
			window_height = monitor_height - 20
		
		# Center window in target monitor (or position at top-left with small margin)
		new_x = target_monitor.left + (monitor_width - window_width) // 2
		new_y = target_monitor.top + (monitor_height - window_height) // 2
		
		# Ensure window doesn't go outside monitor bounds
		new_x = max(target_monitor.left, min(new_x, target_monitor.right - window_width))
		new_y = max(target_monitor.top, min(new_y, target_monitor.bottom - window_height))
		
		# Reposition/resize window
		# SWP_NOZORDER = 0x0004 keeps z-order unchanged
		# SWP_SHOWWINDOW = 0x0040 shows window
		SWP_NOZORDER = 0x0004
		SWP_SHOWWINDOW = 0x0040
		result = user32.SetWindowPos(
			hwnd,
			0,  # hWndInsertAfter (0 = no change to z-order)
			new_x,
			new_y,
			window_width,
			window_height,
			SWP_NOZORDER | SWP_SHOWWINDOW
		)
		
		return bool(result)
	except Exception as e:
		logger.warning(f"Error ensuring window in single monitor: {e}")
		return False

def bring_vlc_to_foreground(wait_timeout_seconds=3.0, poll_interval_seconds=0.1):
	"""Attempt to bring a VLC window to the foreground on Windows.
	Will poll for up to wait_timeout_seconds to allow VLC to create its window.
	Also ensures the window is contained within a single monitor.
	"""
	if os.name != 'nt':
		return False

	end_time = time.time() + wait_timeout_seconds
	last_result = False
	hwnd_found = None
	while time.time() < end_time:
		hwnd = _find_vlc_window_handle()
		if hwnd:
			hwnd_found = hwnd
			last_result = _bring_window_to_foreground(hwnd)
			if last_result:
				break
		time.sleep(poll_interval_seconds)
	
	# Ensure window is in single monitor after bringing to foreground
	if hwnd_found:
		# Small delay to let window finish positioning
		time.sleep(0.2)
		_ensure_window_in_single_monitor(hwnd_found)
	
	return last_result

def launch_movie_in_vlc(movie_path, subtitle_path=None, close_existing=False, start_time=None):
    """Launch movie in VLC with optional subtitle and start time
    
    Args:
        movie_path: Path to video file
        subtitle_path: Optional path to subtitle file
        close_existing: Whether to close existing VLC windows
        start_time: Optional start time in seconds
    """
    steps = []
    results = []
    
    # Step 1: Verify file exists
    steps.append("Step 1: Verifying movie file exists")
    if not os.path.exists(movie_path):
        error_msg = f"File not found: {movie_path}"
        steps.append(f"  ERROR: {error_msg}")
        results.append({"step": 1, "status": "error", "message": error_msg})
        raise FileNotFoundError(error_msg)
    results.append({"step": 1, "status": "success", "message": f"File found: {movie_path}"})
    steps.append(f"  SUCCESS: File exists at {movie_path}")
    
    # Step 2: Find VLC executable
    steps.append("Step 2: Locating VLC executable")
    vlc_exe = find_vlc_executable()
    checked_paths = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\VideoLAN\vlc.exe"),
        "vlc"
    ]
    
    if not vlc_exe:
        error_msg = "VLC not found. Please install VLC or set path."
        steps.append(f"  ERROR: {error_msg}")
        steps.append(f"  Checked paths: {', '.join(checked_paths)}")
        results.append({"step": 2, "status": "error", "message": error_msg, "checked_paths": checked_paths})
        raise FileNotFoundError(error_msg)
    results.append({"step": 2, "status": "success", "message": f"VLC found at: {vlc_exe}"})
    steps.append(f"  Found VLC at: {vlc_exe}")
    
    # Step 2.5: Close existing VLC windows if requested
    if close_existing:
        steps.append("Step 2.5: Closing existing VLC windows")
        try:
            closed_count = close_vlc_processes()
            if closed_count:
                steps.append(f"  Successfully closed {closed_count} VLC process(es)")
                results.append({"step": 2.5, "status": "success", "message": f"Closed {closed_count} existing VLC process(es)"})
            else:
                steps.append("  No existing VLC processes found")
                results.append({"step": 2.5, "status": "info", "message": "No existing VLC processes to close"})
        except Exception as e:
            steps.append(f"  WARNING: Error closing existing VLC processes: {str(e)}")
            results.append({"step": 2.5, "status": "warning", "message": f"Error closing existing VLC: {str(e)}"})
    else:
        steps.append("Step 2.5: Skipping close existing VLC (option disabled)")
        results.append({"step": 2.5, "status": "info", "message": "Close existing VLC option disabled"})
    
    # Step 3: Build VLC command
    steps.append("Step 3: Building VLC command")
    vlc_cmd = [vlc_exe, movie_path]
    steps.append(f"  Base command: {vlc_exe} {movie_path}")
    results.append({"step": 3, "status": "success", "message": f"Command prepared: {vlc_exe}"})
    
    # Step 4: Handle subtitles
    steps.append("Step 4: Checking for subtitles")
    if not subtitle_path:
        steps.append("  No subtitle provided, attempting auto-detection")
        subtitle_path = find_subtitle_file(movie_path)
        if subtitle_path:
            steps.append(f"  Auto-detected subtitle: {subtitle_path}")
        else:
            steps.append("  No subtitle file found")
    else:
        steps.append(f"  Subtitle provided: {subtitle_path}")
    
    if subtitle_path and os.path.exists(subtitle_path):
        vlc_cmd.extend(["--sub-file", subtitle_path])
        steps.append(f"  Added subtitle to command: {subtitle_path}")
        results.append({"step": 4, "status": "success", "message": f"Subtitle loaded: {subtitle_path}"})
    else:
        if subtitle_path:
            steps.append(f"  WARNING: Subtitle file not found: {subtitle_path}")
            results.append({"step": 4, "status": "warning", "message": f"Subtitle file not found: {subtitle_path}"})
        else:
            steps.append("  No subtitle will be used")
            results.append({"step": 4, "status": "info", "message": "No subtitle file"})
    
    # Step 4.5: Handle start time
    if start_time is not None and start_time > 0:
        vlc_cmd.extend(["--start-time", str(start_time)])
        steps.append(f"  Added start time: {start_time}s")
        results.append({"step": 4.5, "status": "success", "message": f"Start time set to {start_time}s"})
    
    # Step 5: Launch VLC
    steps.append("Step 5: Launching VLC")
    steps.append(f"  Full command: {' '.join(vlc_cmd)}")
    try:
        process = subprocess.Popen(vlc_cmd, shell=False)
        steps.append(f"  VLC process started (PID: {process.pid})")
        results.append({"step": 5, "status": "success", "message": f"VLC launched successfully (PID: {process.pid})"})
    except Exception as e:
        error_msg = f"Failed to launch VLC: {str(e)}"
        steps.append(f"  ERROR: {error_msg}")
        results.append({"step": 5, "status": "error", "message": error_msg})
        raise

    # Step 5.1: Bring VLC to foreground on Windows
    if os.name == 'nt':
        steps.append("Step 5.1: Bringing VLC window to foreground (Windows)")
        try:
            focused = bring_vlc_to_foreground(wait_timeout_seconds=3.0, poll_interval_seconds=0.1)
            if focused:
                steps.append("  VLC window brought to foreground")
                results.append({"step": 5.1, "status": "success", "message": "Foreground set"})
            else:
                steps.append("  WARNING: Unable to bring VLC to foreground")
                results.append({"step": 5.1, "status": "warning", "message": "Failed to set foreground"})
        except Exception as e:
            steps.append(f"  WARNING: Error attempting foreground: {str(e)}")
            results.append({"step": 5.1, "status": "warning", "message": f"Foreground error: {str(e)}"})
    
    # Step 6: Save to history
    steps.append("Step 6: Saving to history")
    db = SessionLocal()
    try:
        # Get movie ID from path
        movie = db.query(Movie).filter(Movie.path == movie_path).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found in database: {movie_path}")
        
        # Only add if the last entry is different (prevent duplicate consecutive entries)
        last_launch = db.query(LaunchHistory).order_by(LaunchHistory.created.desc()).first()
        if not last_launch or last_launch.movie_id != movie.id:
            launch_entry = LaunchHistory(
                movie_id=movie.id,
                subtitle=subtitle_path
            )
            db.add(launch_entry)
        
        # Create watch history entry for launch (watch session started)
        watch_entry = WatchHistory(
            movie_id=movie.id,
            watch_status=None  # NULL = unknown (started watching but not finished)
        )
        db.add(watch_entry)
        
        db.commit()
        steps.append("  History saved successfully")
        results.append({"step": 6, "status": "success", "message": "Launch saved to history"})
    finally:
        db.close()
    
    # Final summary
    steps.append("=" * 50)
    steps.append("LAUNCH COMPLETE")
    steps.append(f"Movie: {movie_path}")
    steps.append(f"VLC: {vlc_exe}")
    steps.append(f"Subtitle: {subtitle_path or 'None'}")
    steps.append(f"Process ID: {process.pid}")
    steps.append("=" * 50)
    
    return {
        "status": "launched",
        "subtitle": subtitle_path,
        "steps": steps,
        "results": results,
        "vlc_path": vlc_exe,
        "command": " ".join(vlc_cmd),
        "process_id": process.pid
    }

def get_currently_playing_movies():
    """Get currently playing movies from VLC instances"""
    db = SessionLocal()
    try:
        playing = []
        
        # Get command line arguments
        vlc_processes = get_vlc_command_lines()
        
        # Process command line results
        for proc_info in vlc_processes:
            file_path = proc_info["path"]
            # Normalize path for comparison
            try:
                normalized_path = str(Path(file_path).resolve())
            except:
                normalized_path = file_path
            
            # Check if this path is in our index
            movie = db.query(Movie).filter(Movie.path == normalized_path).first()
            if movie:
                playing.append({
                    "id": movie.id,
                    "path": normalized_path,
                    "name": movie.name,
                    "pid": proc_info["pid"]
                })
            else:
                # Try case-insensitive match
                from sqlalchemy.sql import func as sql_func
                movie = db.query(Movie).filter(sql_func.lower(Movie.path) == normalized_path.lower()).first()
                if movie:
                    playing.append({
                        "id": movie.id,
                        "path": movie.path,
                        "name": movie.name,
                        "pid": proc_info["pid"]
                    })
        
        return playing
    finally:
        db.close()

