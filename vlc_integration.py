"""
VLC player integration and currently playing detection for Movie Searcher.
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
	from ctypes import wintypes

# Import database models and session
from database import SessionLocal, Movie, LaunchHistory, WatchHistory

logger = logging.getLogger(__name__)

# Video and subtitle extensions (matching main.py)
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp'}
SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.vtt', '.ass', '.ssa'}

def find_subtitle_file(video_path):
    """Find subtitle file for a video"""
    video_path_obj = Path(video_path)
    base_name = video_path_obj.stem
    
    # Check same directory first
    video_dir = video_path_obj.parent
    for ext in SUBTITLE_EXTENSIONS:
        subtitle_path = video_dir / f"{base_name}{ext}"
        if subtitle_path.exists():
            return str(subtitle_path)
    
    # Check for common subtitle naming patterns
    for ext in SUBTITLE_EXTENSIONS:
        for pattern in [f"{base_name}.en{ext}", f"{base_name}.eng{ext}", f"{base_name}_en{ext}"]:
            subtitle_path = video_dir / pattern
            if subtitle_path.exists():
                return str(subtitle_path)
    
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
    """Get command line arguments from running VLC processes (Windows)."""
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

        # Normalize to a list
        processes = data if isinstance(data, list) else [data]
        command_lines = []

        for proc in processes:
            cmd_line = (proc.get("CommandLine") or "").strip()
            pid = str(proc.get("ProcessId") or "").strip()
            if not cmd_line:
                continue
            # Parse arguments - use shlex with posix=False for Windows paths
            try:
                args = shlex.split(cmd_line, posix=False)
            except Exception:
                # Fallback: simple regex to extract quoted and unquoted args
                import re
                args = re.findall(r'"[^"]+"|[^\s]+', cmd_line)
                args = [a.strip('"') for a in args]

            # Skip empty or single-arg (just vlc.exe) invocations
            if len(args) <= 1:
                continue

            # Look for a path argument with a known video extension
            for arg in args[1:]:
                try:
                    # Handle cases like --started-from-file or other flags
                    if arg.startswith("-"):
                        continue
                    if os.path.exists(arg) and Path(arg).suffix.lower() in VIDEO_EXTENSIONS:
                        command_lines.append({"path": arg, "pid": pid})
                        break
                except Exception:
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

def bring_vlc_to_foreground(wait_timeout_seconds=3.0, poll_interval_seconds=0.1):
	"""Attempt to bring a VLC window to the foreground on Windows.
	Will poll for up to wait_timeout_seconds to allow VLC to create its window.
	"""
	if os.name != 'nt':
		return False

	end_time = time.time() + wait_timeout_seconds
	last_result = False
	while time.time() < end_time:
		hwnd = _find_vlc_window_handle()
		if hwnd:
			last_result = _bring_window_to_foreground(hwnd)
			if last_result:
				return True
		time.sleep(poll_interval_seconds)
	return last_result

def launch_movie_in_vlc(movie_path, subtitle_path=None, close_existing=False):
    """Launch movie in VLC with optional subtitle"""
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

