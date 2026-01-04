"""
VLC player integration and currently playing detection for Movie Searcher.

CRITICAL: When parsing VLC command lines from PowerShell, quotes MUST be stripped
from all arguments after parsing. See get_vlc_command_lines() docstring for details.
"""
import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

# In-memory debounce for launch history
_last_launch_movie_id = None
_last_launch_time = 0.0

# =============================================================================
# VLC HTTP Interface Configuration
# =============================================================================
# VLC can run an HTTP interface that exposes playback status. We use this to
# capture the current playback position before closing/replacing VLC, enabling
# "resume from where you left off" functionality in the history page.

VLC_HTTP_PORT = 9090
VLC_HTTP_PASSWORD = "moviesearcher"  # Required by VLC, localhost only so not a security concern

# =============================================================================
# VLC Flag Testing System
# =============================================================================
# Tests which VLC command-line flags are safe on this system.
# Flags are tested by launching VLC briefly and checking if it stays running.

# All possible optimization flags to test (in order of likely benefit)
VLC_OPTIMIZATION_FLAGS = [
    # Performance - likely safe
    ("--no-video-title-show", "Don't show title on screen"),
    ("--no-qt-updates-notif", "Disable update notifications"),
    ("--no-qt-privacy-ask", "Skip privacy dialog"),
    ("--no-album-art", "Don't fetch album art"),

    # Performance - medium risk
    ("--file-caching=300", "Reduced file caching"),
    ("--no-metadata-network-access", "No network metadata"),
    ("--no-auto-preparse", "Don't preparse files"),

    # Performance - higher risk (disabled by default due to known issues)
    # ("--no-media-library", "Disable media library"),  # Can cause issues
    # ("--no-lua", "Disable Lua"),  # Known to cause crashes
    # ("--input-fast-seek", "Fast seeking"),  # Can cause playback issues
]

# Hardware acceleration flags (separate category, user opt-in)
VLC_HW_ACCEL_FLAGS_WINDOWS = [
    ("--avcodec-hw=d3d11va", "D3D11 hardware decoding"),
    ("--vout=direct3d11", "Direct3D 11 video output"),
]

# Cache of tested flags: flag -> True (safe) / False (unsafe) / None (untested)
_tested_flags_cache = {}
_flags_test_in_progress = False


def get_safe_vlc_flags():
    """Get list of VLC flags that have been tested safe on this system.
    
    Returns flags from config if available, otherwise returns empty list
    (will be populated as flags are tested).
    """
    try:
        from config import load_config
        config = load_config()
        safe_flags = config.get("vlc_safe_flags", [])

        # Also add hardware acceleration if enabled and tested safe
        if config.get("vlc_hardware_acceleration", False):
            hw_flags = config.get("vlc_hw_accel_safe", False)
            if hw_flags and os.name == 'nt':
                safe_flags.extend(["--avcodec-hw=d3d11va", "--vout=direct3d11"])

        return safe_flags
    except Exception as e:
        logger.warning(f"Could not load safe VLC flags from config: {e}")
        return []


def save_safe_vlc_flags(safe_flags):
    """Save the list of tested-safe VLC flags to config."""
    try:
        from config import load_config, save_config
        config = load_config()
        config["vlc_safe_flags"] = safe_flags
        save_config(config)
        logger.info(f"Saved {len(safe_flags)} safe VLC flags to config")
    except Exception as e:
        logger.error(f"Failed to save safe VLC flags: {e}")


def test_vlc_flag(vlc_exe, flag, timeout_seconds=2.0):
    """Test if a single VLC flag is safe by launching VLC briefly.
    
    Args:
        vlc_exe: Path to VLC executable
        flag: The flag to test (e.g., "--no-video-title-show")
        timeout_seconds: How long to wait to see if VLC stays running
    
    Returns:
        dict with 'safe' (bool), 'error' (str or None), 'time_ms' (float)
    """
    import time as time_module
    start_time = time_module.perf_counter()

    try:
        # Launch VLC with just this flag (no video file - just test the flag)
        cmd = [vlc_exe, flag, "--play-and-exit"]

        logger.info(f"Testing VLC flag: {flag}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            shell=False
        )

        # Wait a bit to see if it crashes immediately
        time_module.sleep(0.5)

        poll_result = process.poll()

        if poll_result is not None:
            # Process exited - check if it was a crash
            stderr = ""
            try:
                stderr = process.stderr.read().decode('utf-8', errors='replace')[:500]
            except:
                pass

            elapsed_ms = (time_module.perf_counter() - start_time) * 1000

            # Exit code 0 is OK (--play-and-exit with no file exits quickly)
            # Other exit codes might indicate a problem
            if poll_result == 0:
                logger.info(f"Flag {flag}: SAFE (clean exit)")
                return {"safe": True, "error": None, "time_ms": elapsed_ms, "exit_code": poll_result}
            else:
                logger.warning(f"Flag {flag}: UNSAFE (exit code {poll_result})")
                return {"safe": False, "error": f"Exit code {poll_result}: {stderr}", "time_ms": elapsed_ms, "exit_code": poll_result}

        # Process still running after timeout - that's good, kill it
        elapsed_ms = (time_module.perf_counter() - start_time) * 1000
        try:
            process.terminate()
            process.wait(timeout=1)
        except:
            try:
                process.kill()
            except:
                pass

        logger.info(f"Flag {flag}: SAFE (stayed running)")
        return {"safe": True, "error": None, "time_ms": elapsed_ms}

    except FileNotFoundError:
        return {"safe": False, "error": "VLC executable not found", "time_ms": 0}
    except Exception as e:
        elapsed_ms = (time_module.perf_counter() - start_time) * 1000
        return {"safe": False, "error": str(e), "time_ms": elapsed_ms}


def test_all_vlc_flags(vlc_exe=None):
    """Test all VLC optimization flags and return results.
    
    Returns:
        dict with 'results' (list of flag test results), 'safe_flags' (list of safe flag strings)
    """
    global _flags_test_in_progress

    if _flags_test_in_progress:
        return {"error": "Flag testing already in progress", "results": [], "safe_flags": []}

    _flags_test_in_progress = True

    try:
        if not vlc_exe:
            vlc_exe = find_vlc_executable()

        if not vlc_exe:
            return {"error": "VLC not found", "results": [], "safe_flags": []}

        results = []
        safe_flags = []

        for flag, description in VLC_OPTIMIZATION_FLAGS:
            result = test_vlc_flag(vlc_exe, flag)
            result["flag"] = flag
            result["description"] = description
            results.append(result)

            if result["safe"]:
                safe_flags.append(flag)

            # Small delay between tests
            time.sleep(0.2)

        # Save safe flags to config
        save_safe_vlc_flags(safe_flags)

        return {
            "results": results,
            "safe_flags": safe_flags,
            "total_tested": len(results),
            "safe_count": len(safe_flags)
        }
    finally:
        _flags_test_in_progress = False


def test_hw_acceleration(vlc_exe=None):
    """Test if hardware acceleration flags work on this system.
    
    Returns:
        dict with 'safe' (bool), 'error' (str or None)
    """
    if os.name != 'nt':
        return {"safe": False, "error": "Hardware acceleration test only supported on Windows"}

    if not vlc_exe:
        vlc_exe = find_vlc_executable()

    if not vlc_exe:
        return {"safe": False, "error": "VLC not found"}

    # Test both flags together since they work as a pair
    flags = ["--avcodec-hw=d3d11va", "--vout=direct3d11", "--play-and-exit"]

    try:
        cmd = [vlc_exe] + flags
        logger.info("Testing hardware acceleration flags")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            shell=False
        )

        time.sleep(1.0)  # Give it a bit longer for HW accel init

        poll_result = process.poll()

        if poll_result is not None and poll_result != 0:
            stderr = ""
            try:
                stderr = process.stderr.read().decode('utf-8', errors='replace')[:500]
            except:
                pass
            return {"safe": False, "error": f"Exit code {poll_result}: {stderr}"}

        # Clean up
        if poll_result is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except:
                pass

        # Save result to config
        try:
            from config import load_config, save_config
            config = load_config()
            config["vlc_hw_accel_safe"] = True
            save_config(config)
        except:
            pass

        return {"safe": True, "error": None}

    except Exception as e:
        return {"safe": False, "error": str(e)}

if os.name == 'nt':
	# Windows-specific imports via ctypes to avoid extra dependencies
	import ctypes
	from ctypes import POINTER, Structure, byref, wintypes

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
from database import LaunchHistory, Movie, SessionLocal

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
    """Find VLC executable in common locations, PATH, and Windows registry"""

    # First, check if VLC path is saved in config
    try:
        from config import load_config
        config = load_config()
        vlc_path = config.get("vlc_path")
        if vlc_path and os.path.exists(vlc_path):
            logger.info(f"Using VLC from config: {vlc_path}")
            return vlc_path
    except Exception as e:
        logger.debug(f"Error loading VLC path from config: {e}")

    # Next check PATH
    import shutil
    vlc_in_path = shutil.which("vlc")
    if vlc_in_path:
        logger.info(f"Found VLC in PATH: {vlc_in_path}")
        return vlc_in_path

    # Common installation paths
    vlc_paths = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\VideoLAN\VLC\vlc.exe"),
    ]

    # Check common paths
    for path in vlc_paths:
        if os.path.exists(path):
            logger.info(f"Found VLC at: {path}")
            return path

    # On Windows, check registry
    if os.name == 'nt':
        try:
            import winreg
            # Check both 64-bit and 32-bit registry keys
            registry_keys = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VideoLAN\VLC"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\VideoLAN\VLC"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\VideoLAN\VLC"),
            ]

            for hkey, key_path in registry_keys:
                try:
                    with winreg.OpenKey(hkey, key_path) as key:
                        install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                        vlc_path = os.path.join(install_dir, "vlc.exe")
                        if os.path.exists(vlc_path):
                            logger.info(f"Found VLC via registry: {vlc_path}")
                            return vlc_path
                except (FileNotFoundError, OSError):
                    continue
        except Exception as e:
            logger.debug(f"Error checking registry for VLC: {e}")

    # Search in Program Files directories
    if os.name == 'nt':
        program_files_dirs = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
        ]

        for base_dir in program_files_dirs:
            if not base_dir or not os.path.exists(base_dir):
                continue
            try:
                for item in Path(base_dir).iterdir():
                    if item.is_dir() and "vlc" in item.name.lower():
                        vlc_exe = item / "vlc.exe"
                        if vlc_exe.exists():
                            logger.info(f"Found VLC by searching Program Files: {vlc_exe}")
                            return str(vlc_exe)
            except (PermissionError, OSError):
                continue

    logger.warning("VLC not found in any common locations")
    return None

def test_vlc_comprehensive(vlc_path=None):
    """
    Comprehensive test of VLC installation.
    Simply checks if VLC executable exists and is accessible - we don't run --version
    because on Windows that can pop up dialogs requiring user interaction.
    
    Args:
        vlc_path: Optional path to VLC executable. If not provided, will search for it.
    """
    if not vlc_path:
        vlc_path = find_vlc_executable()

    vlc_search_info = [
        "PATH environment variable",
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\VideoLAN\vlc.exe"),
        "Windows registry (HKLM and HKCU)",
        "Program Files directories"
    ]

    if not vlc_path:
        return {
            "ok": False,
            "vlc_ok": False,
            "errors": ["VLC not found. Install from https://www.videolan.org/vlc/"],
            "vlc_path": None,
            "vlc_version": None,
            "checked_locations": vlc_search_info
        }

    # Simple validation: check if file exists and is executable
    # We DON'T run 'vlc --version' because on Windows it can pop up GUI dialogs
    # that require user interaction (pressing Enter). Since we only need VLC to
    # launch movies, verifying the executable exists and is accessible is sufficient.
    vlc_ok = False
    vlc_version = None
    errors = []

    if not os.path.exists(vlc_path):
        errors.append(f"VLC executable not found at: {vlc_path}")
    elif not os.access(vlc_path, os.X_OK):
        errors.append(f"VLC file exists but is not executable: {vlc_path}")
    else:
        # File exists and is executable - that's all we need!
        vlc_ok = True
        vlc_version = "OK"
        logger.info(f"VLC validated: {vlc_path}")

    return {
        "ok": vlc_ok,
        "vlc_ok": vlc_ok,
        "errors": errors,
        "vlc_path": str(vlc_path) if vlc_path else None,
        "vlc_version": vlc_version,
        "checked_locations": vlc_search_info
    }

def get_vlc_playback_position():
    """Query VLC's HTTP interface for current playback position.
    
    Returns:
        dict with playback info if successful:
            - position_seconds: Current position in seconds (int)
            - length_seconds: Total length in seconds (int)
            - position_percent: Position as 0.0-1.0 float
            - state: 'playing', 'paused', or 'stopped'
            - filename: Name of file being played
        None if VLC HTTP interface is not available or query fails.
    """
    import base64
    import urllib.error
    import urllib.request

    try:
        # VLC HTTP API uses password-only auth (empty username)
        url = f"http://127.0.0.1:{VLC_HTTP_PORT}/requests/status.json"

        # Create request with basic auth
        request = urllib.request.Request(url)
        credentials = f":{VLC_HTTP_PASSWORD}"
        b64_credentials = base64.b64encode(credentials.encode()).decode()
        request.add_header("Authorization", f"Basic {b64_credentials}")

        with urllib.request.urlopen(request, timeout=1.0) as response:
            data = json.loads(response.read().decode('utf-8'))

            # Extract relevant fields
            position_seconds = data.get("time", 0)
            length_seconds = data.get("length", 0)
            position_percent = data.get("position", 0.0)
            state = data.get("state", "unknown")

            # Try to get filename from metadata
            filename = None
            info = data.get("information", {})
            if info:
                category = info.get("category", {})
                meta = category.get("meta", {})
                filename = meta.get("filename")

            logger.info(f"VLC playback position: {position_seconds}s / {length_seconds}s ({state})")

            return {
                "position_seconds": position_seconds,
                "length_seconds": length_seconds,
                "position_percent": position_percent,
                "state": state,
                "filename": filename
            }
    except urllib.error.URLError as e:
        # HTTP interface not available (VLC not running or HTTP not enabled)
        logger.debug(f"VLC HTTP interface not available: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error querying VLC playback position: {e}")
        return None


def close_vlc_processes(capture_position=False):
    """Close all running VLC processes.
    
    Args:
        capture_position: If True, attempt to query playback position before killing.
                         Returns the position info along with the count.
    
    Returns:
        If capture_position is False: int (number of processes closed) or False on error
        If capture_position is True: tuple of (count, position_info) where position_info
                                     may be None if position could not be captured.
    """
    playback_info = None

    # If requested, try to capture position before killing
    if capture_position:
        playback_info = get_vlc_playback_position()
        if playback_info:
            logger.info(f"Captured VLC position before close: {playback_info['position_seconds']}s")

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
                    if capture_position:
                        return (process_count, playback_info)
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
                    if capture_position:
                        return (True, playback_info)
                    return True
            except FileNotFoundError:
                # Try killall if pkill unavailable
                try:
                    subprocess.run(["killall", "vlc"], capture_output=True, timeout=5)
                    if capture_position:
                        return (True, playback_info)
                    return True
                except FileNotFoundError:
                    logger.warning("Neither pkill nor killall available - cannot close VLC processes")
                    if capture_position:
                        return (False, playback_info)
                    return False
    except Exception as e:
        logger.warning(f"Error closing VLC processes: {e}")
        if capture_position:
            return (False, playback_info)
        return False

    if capture_position:
        return (0, playback_info)
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
    (e.g., '"C:\\Program Files\\VLC\\vlc.exe" "D:\\movies\\file.mkv"'). When we parse
    this with shlex.split(), it correctly splits the arguments but may leave
    quotes in the resulting strings. Since os.path.exists() will fail on a path
    like '"D:\\movies\\file.mkv"' (with quotes), we MUST strip quotes from all
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

def _find_vlc_window_handle(target_pid=None):
	"""Locate a VLC window handle on Windows by enumerating top-level windows.
	If target_pid is provided, looks for a visible window belonging to that process.
	Otherwise, looks for the first visible window with 'vlc' in the title.
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
	GetWindowThreadProcessId = user32.GetWindowThreadProcessId

	found_hwnd = ctypes.c_void_p(0)

	def _callback(hwnd, lParam):
		if not IsWindowVisible(hwnd):
			return True

		# Check PID if requested
		if target_pid:
			pid = ctypes.c_ulong()
			GetWindowThreadProcessId(hwnd, byref(pid))
			if pid.value != target_pid:
				return True
			# If PID matches and is visible, we assume it's the one
			found_hwnd.value = hwnd
			return False

		# Fallback to title search
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

def _set_window_rect(hwnd, rect):
	"""Set window position and size on Windows."""
	if os.name != 'nt' or not hwnd or not rect:
		return False

	try:
		user32 = ctypes.windll.user32
		width = rect.right - rect.left
		height = rect.bottom - rect.top

		# SWP_NOZORDER = 0x0004 keeps z-order unchanged
		# SWP_SHOWWINDOW = 0x0040 shows window
		SWP_NOZORDER = 0x0004
		SWP_SHOWWINDOW = 0x0040

		result = user32.SetWindowPos(
			hwnd,
			0,  # hWndInsertAfter
			rect.left,
			rect.top,
			width,
			height,
			SWP_NOZORDER | SWP_SHOWWINDOW
		)
		return bool(result)
	except Exception as e:
		logger.warning(f"Error setting window rect: {e}")
		return False

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

def bring_vlc_to_foreground(wait_timeout_seconds=3.0, poll_interval_seconds=0.1, target_pid=None, target_rect=None):
	"""Attempt to bring a VLC window to the foreground on Windows.
	Will poll for up to wait_timeout_seconds to allow VLC to create its window.
	If target_pid is provided, waits for window belonging to that process.
	If target_rect is provided, applies that position and size to the window.
	Also ensures the window is contained within a single monitor.
	"""
	if os.name != 'nt':
		return False

	end_time = time.time() + wait_timeout_seconds
	last_result = False
	hwnd_found = None
	rect_applied = False
	while time.time() < end_time:
		hwnd = _find_vlc_window_handle(target_pid)
		if hwnd:
			hwnd_found = hwnd
			# If we have a target rect, apply it to preserve window position across launches
			if target_rect and not rect_applied:
				logger.info(f"Restoring VLC window position to ({target_rect.left}, {target_rect.top})")
				if _set_window_rect(hwnd, target_rect):
					logger.info("Window position restored successfully")
					rect_applied = True
				else:
					logger.warning("Failed to restore window position")
				# No sleep needed - SetWindowPos is synchronous

			last_result = _bring_window_to_foreground(hwnd)
			if last_result:
				break
		time.sleep(poll_interval_seconds)

	# After VLC has initialized, handle window positioning
	# Do this asynchronously so we don't block the response
	if hwnd_found:
		def finalize_window_position():
			"""Background task to ensure window size and position stick after VLC init.
			
			Even with --no-qt-video-autoresize, VLC may still try to adjust the window
			during video load. We apply the rect multiple times to ensure it sticks.
			"""
			try:
				if rect_applied and target_rect:
					# Apply multiple times as video loads (VLC may try to resize during decode)
					target_width = target_rect.right - target_rect.left
					target_height = target_rect.bottom - target_rect.top
					logger.info(f"Preserving window size: {target_width}x{target_height} at ({target_rect.left}, {target_rect.top})")

					# Apply at staggered intervals during video load
					# Delays: 0.15s, then 0.25s more (0.4s total), then 0.4s more (0.8s total)
					delays = [0.15, 0.25, 0.4]
					for i, delay in enumerate(delays):
						time.sleep(delay)
						_set_window_rect(hwnd_found, target_rect)
						logger.debug(f"Re-applied window rect (attempt {i+1}/{len(delays)})")
				else:
					time.sleep(0.15)
					_ensure_window_in_single_monitor(hwnd_found)
			except Exception as e:
				logger.warning(f"Background window finalization failed: {e}")

		# Fire and forget - don't block on this
		threading.Thread(target=finalize_window_position, daemon=True).start()

	return last_result

def launch_movie_in_vlc(movie_path, subtitle_path=None, close_existing=False, start_time=None, movie_id=None):
    """Launch movie in VLC with optional subtitle and start time
    
    Args:
        movie_path: Path to video file
        subtitle_path: Optional path to subtitle file
        close_existing: Whether to close existing VLC windows
        start_time: Optional start time in seconds
        movie_id: Optional ID of the movie (for history tracking)
    """
    # Start timing for performance measurement
    import time as time_module
    launch_start_time = time_module.perf_counter()

    # Timing breakdown dict to track where time is spent
    timing = {
        'total': 0,
        'prep': 0,           # File checks, VLC path lookup, command building
        'close_existing': 0, # Closing existing VLC windows
        'popen': 0,          # subprocess.Popen call
        'health_check': 0,   # 0.5s health check wait
        'window_focus': 0,   # Finding and focusing VLC window
        'history_save': 0,   # Database history save
    }

    steps = []
    results = []

    # Load config for launch settings
    config = {}  # Default empty config
    try:
        from config import load_config
        config = load_config()
    except Exception as e:
        logger.warning(f"Failed to load config: {e}. Using defaults.")

    launch_with_subtitles_on = config.get("launch_with_subtitles_on", True)

    # Step 1: Verify file exists
    steps.append("Step 1: Verifying movie file exists")
    logger.info(f"launch_movie_in_vlc: Checking file existence for path: {movie_path}")
    logger.info(f"launch_movie_in_vlc: Path type: {type(movie_path)}, Path repr: {repr(movie_path)}")

    # Normalize path before checking (same as indexing does)
    try:
        normalized_path_obj = Path(movie_path).resolve()
        normalized_path = str(normalized_path_obj)
        logger.info(f"launch_movie_in_vlc: Normalized path: {normalized_path}")
        if normalized_path != movie_path:
            logger.info(f"launch_movie_in_vlc: Path changed after normalization: '{movie_path}' -> '{normalized_path}'")
            movie_path = normalized_path
            steps.append(f"  Path normalized: {normalized_path}")
    except (OSError, RuntimeError) as e:
        logger.warning(f"launch_movie_in_vlc: Failed to resolve path '{movie_path}': {e}, using original path")
        # Try absolute() as fallback
        try:
            normalized_path = str(Path(movie_path).absolute())
            logger.info(f"launch_movie_in_vlc: Using absolute() fallback: {normalized_path}")
            if normalized_path != movie_path:
                logger.info(f"launch_movie_in_vlc: Path changed after absolute(): '{movie_path}' -> '{normalized_path}'")
                movie_path = normalized_path
                steps.append(f"  Path normalized (absolute): {normalized_path}")
        except Exception as e2:
            logger.warning(f"launch_movie_in_vlc: Failed to get absolute path: {e2}, using original path")

    if not os.path.exists(movie_path):
        error_msg = f"File not found: {movie_path}"
        steps.append(f"  ERROR: {error_msg}")
        results.append({"step": 1, "status": "error", "message": error_msg})
        logger.error(f"launch_movie_in_vlc: File does not exist at path: {movie_path}")
        logger.error(f"launch_movie_in_vlc: Path type: {type(movie_path)}, Path repr: {repr(movie_path)}")
        # Try to find similar files in the same directory
        try:
            parent_dir = Path(movie_path).parent
            if parent_dir.exists():
                logger.error(f"launch_movie_in_vlc: Parent directory exists: {parent_dir}")
                files_in_dir = list(parent_dir.iterdir())
                logger.error(f"launch_movie_in_vlc: Files in parent directory ({len(files_in_dir)} total): {[f.name for f in files_in_dir[:20]]}")
                # Check for case-insensitive match
                expected_filename = Path(movie_path).name
                for f in files_in_dir:
                    if f.name.lower() == expected_filename.lower() and f.name != expected_filename:
                        logger.error(f"launch_movie_in_vlc: Found case-insensitive match: '{f.name}' (expected: '{expected_filename}')")
            else:
                logger.error(f"launch_movie_in_vlc: Parent directory does not exist: {parent_dir}")
        except Exception as e:
            logger.error(f"launch_movie_in_vlc: Error checking parent directory: {e}")
        raise FileNotFoundError(error_msg)
    results.append({"step": 1, "status": "success", "message": f"File found: {movie_path}"})
    steps.append(f"  SUCCESS: File exists at {movie_path}")
    logger.info(f"launch_movie_in_vlc: File exists, proceeding: {movie_path}")

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

    # Step 2.4: Capture existing VLC window geometry if replacing
    # This preserves the window position across monitor setups - when replacing VLC,
    # the new window will open at the same position (same monitor, same coordinates)
    existing_rect = None
    if close_existing and os.name == 'nt':
        steps.append("Step 2.4: Capturing existing VLC window position for restoration")
        try:
            # Find ANY VLC window to capture geometry
            # We don't have a PID yet, so finding any VLC window is fine as we are about to close them all
            hwnd_existing = _find_vlc_window_handle()
            if hwnd_existing:
                existing_rect = _get_window_rect(hwnd_existing)
                if existing_rect:
                    logger.info(f"Captured VLC window position: ({existing_rect.left}, {existing_rect.top}) size: {existing_rect.right-existing_rect.left}x{existing_rect.bottom-existing_rect.top}")
                    steps.append(f"  Captured window position: ({existing_rect.left}, {existing_rect.top}) size: {existing_rect.right-existing_rect.left}x{existing_rect.bottom-existing_rect.top}")
                    results.append({"step": 2.4, "status": "success", "message": f"Window position captured at ({existing_rect.left}, {existing_rect.top})"})
                else:
                    logger.warning("Found VLC window but failed to get its rect")
                    steps.append("  WARNING: Found VLC window but failed to get its position")
                    results.append({"step": 2.4, "status": "warning", "message": "Could not get window position"})
            else:
                logger.info("No existing VLC window found to capture position from")
                steps.append("  No existing VLC window found")
                results.append({"step": 2.4, "status": "info", "message": "No existing VLC window"})
        except Exception as e:
            logger.warning(f"Failed to capture existing window geometry: {e}")
            steps.append(f"  WARNING: Failed to capture position: {e}")
            results.append({"step": 2.4, "status": "warning", "message": f"Error: {e}"})

    # TIMING: End of prep phase
    timing['prep'] = (time_module.perf_counter() - launch_start_time) * 1000
    t_close_start = time_module.perf_counter()

    # Step 2.5: Close existing VLC windows if requested
    # When replacing VLC, we capture playback position first and save it to history
    if close_existing:
        steps.append("Step 2.5: Closing existing VLC windows (with position capture)")
        try:
            # Capture position before killing, then close
            close_result = close_vlc_processes(capture_position=True)
            closed_count, playback_info = close_result

            if closed_count:
                steps.append(f"  Successfully closed {closed_count} VLC process(es)")

                # If we captured playback position, save it to the most recent launch history
                if playback_info and playback_info.get("position_seconds", 0) > 0:
                    position_secs = playback_info["position_seconds"]
                    steps.append(f"  Captured playback position: {position_secs}s ({position_secs // 3600}h {(position_secs % 3600) // 60}m {position_secs % 60}s)")

                    # Save to most recent launch history entry
                    try:
                        db = SessionLocal()
                        try:
                            # Get the most recent launch history entry
                            last_launch = db.query(LaunchHistory).order_by(
                                LaunchHistory.created.desc()
                            ).first()

                            if last_launch:
                                last_launch.stopped_at_seconds = float(position_secs)
                                db.commit()
                                logger.info(f"Saved playback position {position_secs}s to launch history ID {last_launch.id}")
                                steps.append(f"  Saved position to history entry ID {last_launch.id}")
                            else:
                                logger.warning("No launch history entry found to save position to")
                                steps.append("  No history entry found to save position")
                        finally:
                            db.close()
                    except Exception as e:
                        logger.warning(f"Failed to save playback position to history: {e}")
                        steps.append(f"  WARNING: Failed to save position: {e}")
                else:
                    steps.append("  No playback position captured (VLC may not have HTTP interface enabled)")

                results.append({"step": 2.5, "status": "success", "message": f"Closed {closed_count} VLC, position: {playback_info.get('position_seconds') if playback_info else 'unknown'}s"})
            else:
                steps.append("  No existing VLC processes found")
                results.append({"step": 2.5, "status": "info", "message": "No existing VLC processes to close"})
        except Exception as e:
            steps.append(f"  WARNING: Error closing existing VLC processes: {str(e)}")
            results.append({"step": 2.5, "status": "warning", "message": f"Error closing existing VLC: {str(e)}"})
    else:
        steps.append("Step 2.5: Skipping close existing VLC (option disabled)")
        results.append({"step": 2.5, "status": "info", "message": "Close existing VLC option disabled"})

    timing['close_existing'] = (time_module.perf_counter() - t_close_start) * 1000

    # Step 3: Build VLC command with tested-safe optimization flags
    steps.append("Step 3: Building VLC command")

    # Get safe flags from our tested cache
    safe_flags = get_safe_vlc_flags()

    # Build command with safe flags + HTTP interface for playback position tracking
    # HTTP interface allows us to query current position before closing/replacing VLC
    http_interface_flags = [
        "--extraintf", "http",
        "--http-port", str(VLC_HTTP_PORT),
        "--http-password", VLC_HTTP_PASSWORD,
    ]

    vlc_cmd = [vlc_exe] + safe_flags + http_interface_flags + [movie_path]

    if safe_flags:
        steps.append(f"  Using {len(safe_flags)} optimization flags")
        logger.info(f"launch_movie_in_vlc: Using safe flags: {safe_flags}")
    else:
        steps.append("  No optimization flags (none tested safe yet)")

    # If we have a previous window size to restore, disable VLC's auto-resize
    # This prevents VLC from overriding our SetWindowPos after the video loads
    if existing_rect:
        vlc_cmd.insert(1, "--no-qt-video-autoresize")
        steps.append("  Disabled VLC auto-resize to preserve previous window size")
        logger.info("launch_movie_in_vlc: Added --no-qt-video-autoresize to preserve window size")

    steps.append(f"  HTTP interface enabled on port {VLC_HTTP_PORT} for resume tracking")
    results.append({"step": 3, "status": "success", "message": f"Command prepared with {len(safe_flags)} safe flags + HTTP interface"})

    # Step 4: Handle subtitles
    steps.append("Step 4: Checking for subtitles")

    # Check if user explicitly selected a subtitle vs automatic loading
    user_selected_subtitle = subtitle_path is not None and subtitle_path != ""

    if user_selected_subtitle:
        # User explicitly selected a subtitle - use it regardless of global setting
        steps.append(f"  User selected subtitle: {subtitle_path}")
    elif not launch_with_subtitles_on:
        # Global subtitle setting is off and no explicit selection
        steps.append("  Global subtitle setting is disabled - not loading any subtitle")
        subtitle_path = None
    else:
        # Global setting allows subtitles - look for subtitle file automatically
        subtitle_path = find_subtitle_file(movie_path)
        if subtitle_path:
            steps.append(f"  Found subtitle automatically: {subtitle_path}")
        else:
            steps.append("  No subtitle file found automatically")

    if subtitle_path and os.path.exists(subtitle_path):
        # Load the subtitle file and make it active
        vlc_cmd.extend(["--sub-file", subtitle_path, "--sub-track", "1"])
        steps.append(f"  Added subtitle and set as active: {subtitle_path}")
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
    logger.info(f"launch_movie_in_vlc: Launching VLC with command: {vlc_cmd}")

    # TIMING: Popen call
    t_popen_start = time_module.perf_counter()

    # Capture stderr to diagnose VLC failures (but not stdout as that can be noisy)
    # Use PIPE for stderr so we can read error messages if VLC fails
    try:
        process = subprocess.Popen(
            vlc_cmd,
            shell=False,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,  # Discard stdout (noisy)
            # Don't use text=True here; we'll decode manually to handle encoding issues
        )
        timing['popen'] = (time_module.perf_counter() - t_popen_start) * 1000
        logger.info(f"launch_movie_in_vlc: VLC process started with PID: {process.pid} (Popen took {timing['popen']:.1f}ms)")
        steps.append(f"  VLC process started (PID: {process.pid})")
        results.append({"step": 5, "status": "success", "message": f"VLC launched successfully (PID: {process.pid})"})
    except FileNotFoundError as e:
        error_msg = f"VLC executable not found at '{vlc_exe}': {e}"
        logger.error(f"launch_movie_in_vlc: {error_msg}")
        steps.append(f"  ERROR: {error_msg}")
        results.append({"step": 5, "status": "error", "message": error_msg})
        raise
    except PermissionError as e:
        error_msg = f"Permission denied launching VLC '{vlc_exe}': {e}"
        logger.error(f"launch_movie_in_vlc: {error_msg}")
        steps.append(f"  ERROR: {error_msg}")
        results.append({"step": 5, "status": "error", "message": error_msg})
        raise
    except Exception as e:
        error_msg = f"Failed to launch VLC: {type(e).__name__}: {str(e)}"
        logger.error(f"launch_movie_in_vlc: {error_msg}")
        steps.append(f"  ERROR: {error_msg}")
        results.append({"step": 5, "status": "error", "message": error_msg})
        raise

    # Close stderr pipe asynchronously (we captured it for debugging but don't need to wait)
    def close_stderr():
        try:
            if process.stderr:
                process.stderr.close()
        except Exception:
            pass
    threading.Thread(target=close_stderr, daemon=True).start()

    # NOTE: Removed 500ms health check - it was adding latency to every launch.
    # If VLC crashes, we'll know from window detection failing.
    # Flag testing system prevents problematic flags from being used.
    timing['health_check'] = 0  # No longer waiting

    # Step 5.1: Bring VLC to foreground on Windows
    t_focus_start = time_module.perf_counter()
    if os.name == 'nt':
        steps.append("Step 5.1: Bringing VLC window to foreground (Windows)")
        logger.info(f"launch_movie_in_vlc: Attempting to bring VLC window to foreground (PID: {process.pid})")
        try:
            focused = bring_vlc_to_foreground(
                wait_timeout_seconds=3.0,
                poll_interval_seconds=0.05,  # 50ms polls for faster window detection
                target_pid=process.pid,
                target_rect=existing_rect
            )
            timing['window_focus'] = (time_module.perf_counter() - t_focus_start) * 1000
            if focused:
                logger.info("launch_movie_in_vlc: VLC window brought to foreground successfully")
                steps.append("  VLC window brought to foreground")
                results.append({"step": 5.1, "status": "success", "message": "Foreground set"})
            else:
                # Check if process is still running
                poll_result = process.poll()
                if poll_result is not None:
                    logger.warning(f"launch_movie_in_vlc: VLC process exited (code {poll_result}) - cannot bring to foreground")
                    steps.append(f"  WARNING: VLC process exited with code {poll_result}")
                    results.append({"step": 5.1, "status": "error", "message": f"VLC exited (code {poll_result})"})
                else:
                    logger.warning(f"launch_movie_in_vlc: Unable to find VLC window for PID {process.pid}")
                    steps.append("  WARNING: Unable to bring VLC to foreground (window not found)")
                    results.append({"step": 5.1, "status": "warning", "message": "Window not found"})
        except Exception as e:
            logger.warning(f"launch_movie_in_vlc: Error bringing VLC to foreground: {e}")
            steps.append(f"  WARNING: Error attempting foreground: {str(e)}")
            results.append({"step": 5.1, "status": "warning", "message": f"Foreground error: {str(e)}"})

    # Step 6: Save to history
    # To prevent "spamming" history with consecutive duplicates (e.g. accidental double-clicks),
    # we check if the movie was launched very recently or is the same as the last entry.

    # Use a module-level variable for simple in-memory debouncing across threads/requests
    global _last_launch_movie_id, _last_launch_time
    current_time = time.time()

    # Check in-memory debounce (catch rapid-fire requests)
    is_debounce_duplicate = False
    if movie_id is not None and _last_launch_movie_id == movie_id and (current_time - _last_launch_time) < 5.0:
        is_debounce_duplicate = True

    if is_debounce_duplicate:
         steps.append("  Skipping history save: Duplicate launch detected (debounce)")
         results.append({"step": 6, "status": "info", "message": "Launch history skipped (debounce)"})
         # Update time to extend debounce window
         _last_launch_time = current_time
    else:
         # Check persistent history
         db = SessionLocal()
         try:
             # Get movie object if needed
             movie = None
             if movie_id:
                 movie = db.query(Movie).filter(Movie.id == movie_id).first()

             if not movie:
                 # Fallback to path lookup
                 movie = db.query(Movie).filter(Movie.path == movie_path).first()

             if not movie:
                 # Should not happen as we checked existence
                 steps.append("  WARNING: Movie not found in database, cannot save history")
             else:
                 # Check if the last entry is the same movie
                 last_launch = db.query(LaunchHistory).order_by(LaunchHistory.created.desc(), LaunchHistory.id.desc()).first()

                 # Always create a new entry
                 # If the previous entry is the same movie, we leave it as is (do NOT update timestamp)
                 # BUT we also check to ensure we aren't spamming: if the last entry is the same movie,
                 # we ONLY add a new one if some time has passed or if the user explicitly wants it.
                 # Per user instruction: "just create a history entry every time. and before you do so,
                 # insist that the db not have the previous entry pointing to the exact same movie."

                 if last_launch and last_launch.movie_id == movie.id:
                     steps.append("  Skipping history save: Consecutive duplicate detected")
                     results.append({"step": 6, "status": "info", "message": "Launch history skipped (consecutive duplicate)"})
                 else:
                     # New entry
                     launch_entry = LaunchHistory(
                         movie_id=movie.id,
                         subtitle=subtitle_path
                     )
                     db.add(launch_entry)
                     db.commit()
                     steps.append("  Added new history entry")
                     results.append({"step": 6, "status": "success", "message": "Launch saved to history"})

                 # Update in-memory debounce
                 _last_launch_movie_id = movie.id
                 _last_launch_time = current_time
         except Exception as e:
             steps.append(f"  WARNING: Error saving history: {e}")
             results.append({"step": 6, "status": "warning", "message": f"History error: {e}"})
         finally:
             db.close()

    # Final process verification
    final_poll = process.poll()
    if final_poll is not None:
        # Try to get any stderr that might still be available
        vlc_stderr = ""
        try:
            if process.stderr and not process.stderr.closed:
                stderr_bytes = process.stderr.read()
                vlc_stderr = stderr_bytes.decode('utf-8', errors='replace').strip()
        except Exception:
            pass

        logger.error(f"launch_movie_in_vlc: LAUNCH FAILED - VLC exited with code {final_poll}")
        if vlc_stderr:
            logger.error(f"launch_movie_in_vlc: VLC stderr: {vlc_stderr}")
        steps.append(f"LAUNCH FAILED: VLC exited with code {final_poll}")
        if vlc_stderr:
            steps.append(f"VLC error output: {vlc_stderr[:500]}")

        return {
            "status": "failed",
            "error": f"VLC exited immediately with code {final_poll}",
            "vlc_stderr": vlc_stderr[:1000] if vlc_stderr else None,
            "subtitle": subtitle_path,
            "steps": steps,
            "results": results,
            "vlc_path": vlc_exe,
            "command": " ".join(vlc_cmd),
            "process_id": process.pid,
            "exit_code": final_poll
        }

    # Calculate and record launch time
    launch_end_time = time_module.perf_counter()
    launch_time_ms = (launch_end_time - launch_start_time) * 1000
    timing['total'] = launch_time_ms

    # Log timing breakdown
    logger.info("launch_movie_in_vlc: TIMING BREAKDOWN:")
    logger.info(f"  - Prep (file check, VLC lookup, cmd build): {timing['prep']:.1f}ms")
    logger.info(f"  - Close existing VLC: {timing['close_existing']:.1f}ms")
    logger.info(f"  - Popen (create process): {timing['popen']:.1f}ms")
    logger.info(f"  - Health check (0.5s sleep): {timing['health_check']:.1f}ms")
    logger.info(f"  - Window focus: {timing['window_focus']:.1f}ms")
    logger.info(f"  - TOTAL: {timing['total']:.1f}ms")

    # Save launch time stat to database with timing breakdown
    try:
        from models import Stat
        db = SessionLocal()
        try:
            stat = Stat(
                stat_type='vlc_launch_time_ms',
                value=launch_time_ms,
                movie_id=movie_id,
                extra_data=json.dumps({
                    'movie_name': Path(movie_path).name,
                    'had_subtitle': subtitle_path is not None,
                    'close_existing': close_existing,
                    'start_time': start_time,
                    'timing': timing  # Include full timing breakdown
                })
            )
            db.add(stat)
            db.commit()
            logger.info(f"launch_movie_in_vlc: Recorded launch time: {launch_time_ms:.1f}ms")
        except Exception as e:
            logger.warning(f"launch_movie_in_vlc: Failed to record launch stat: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"launch_movie_in_vlc: Could not import Stat model: {e}")

    # Final summary
    steps.append("=" * 50)
    steps.append("LAUNCH COMPLETE")
    steps.append(f"Movie: {movie_path}")
    steps.append(f"VLC: {vlc_exe}")
    steps.append(f"Subtitle: {subtitle_path or 'None'}")
    steps.append(f"Process ID: {process.pid}")
    steps.append(f"Launch time: {launch_time_ms:.1f}ms")
    steps.append(f"  Prep: {timing['prep']:.1f}ms | Popen: {timing['popen']:.1f}ms | Health: {timing['health_check']:.1f}ms | Focus: {timing['window_focus']:.1f}ms")
    steps.append("=" * 50)

    logger.info(f"launch_movie_in_vlc: LAUNCH COMPLETE - PID {process.pid} playing {Path(movie_path).name} ({launch_time_ms:.1f}ms)")

    return {
        "status": "launched",
        "subtitle": subtitle_path,
        "steps": steps,
        "results": results,
        "vlc_path": vlc_exe,
        "command": " ".join(vlc_cmd),
        "process_id": process.pid,
        "launch_time_ms": launch_time_ms,
        "timing": timing  # Detailed timing breakdown
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


# =============================================================================
# VLC Configuration Optimization
# =============================================================================
# These functions modify VLC's vlcrc configuration file for faster startup.
# They require explicit user opt-in and create backups before making changes.

# Optimization settings to apply to vlcrc
# Format: (key, value, description)
VLC_OPTIMIZATION_SETTINGS = [
    # Performance optimizations
    ("file-caching", "300", "Reduced file caching for faster local file playback"),
    ("input-fast-seek", "1", "Fast (but less accurate) seeking"),
    ("metadata-network-access", "0", "Disable network metadata lookups"),
    ("auto-preparse", "0", "Disable automatic file preparsing"),
    ("media-library", "0", "Disable media library"),

    # UI optimizations
    ("video-title-show", "0", "Disable on-screen video title"),
    ("qt-privacy-ask", "0", "Skip privacy dialog"),
    ("qt-updates-notif", "0", "Disable update notifications"),
    ("album-art", "0", "Disable album art fetching"),

    # Note: Hardware acceleration is NOT included here as it can cause issues
    # on some systems. It's offered as a separate opt-in option via command line.
]


def get_vlcrc_path():
    """
    Get the path to VLC's configuration file (vlcrc) based on OS.
    
    Returns:
        Path object to vlcrc file, or None if not found
    """
    if os.name == 'nt':  # Windows
        # Windows: %APPDATA%\vlc\vlcrc
        appdata = os.environ.get('APPDATA')
        if appdata:
            vlcrc = Path(appdata) / 'vlc' / 'vlcrc'
            return vlcrc
    else:
        # Linux/Mac: ~/.config/vlc/vlcrc
        config_home = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        vlcrc = Path(config_home) / 'vlc' / 'vlcrc'
        return vlcrc

    return None


def get_vlcrc_backup_path():
    """Get the path for the vlcrc backup file."""
    vlcrc = get_vlcrc_path()
    if vlcrc:
        return vlcrc.with_suffix('.vlcrc.backup')
    return None


def check_vlcrc_status():
    """
    Check the current status of VLC configuration.
    
    Returns:
        dict with:
        - exists: bool - whether vlcrc file exists
        - path: str - path to vlcrc
        - backup_exists: bool - whether backup file exists
        - backup_path: str - path to backup file
        - is_optimized: bool - whether file appears to already have optimizations
        - size: int - file size in bytes
    """
    vlcrc = get_vlcrc_path()
    backup = get_vlcrc_backup_path()

    status = {
        "exists": False,
        "path": str(vlcrc) if vlcrc else None,
        "backup_exists": False,
        "backup_path": str(backup) if backup else None,
        "is_optimized": False,
        "size": 0
    }

    if vlcrc and vlcrc.exists():
        status["exists"] = True
        status["size"] = vlcrc.stat().st_size

        # Check if already optimized by looking for our marker comment
        try:
            content = vlcrc.read_text(encoding='utf-8', errors='ignore')
            status["is_optimized"] = "# Movie Searcher Optimization" in content
        except Exception:
            pass

    if backup and backup.exists():
        status["backup_exists"] = True

    return status


def create_vlcrc_backup():
    """
    Create a backup of the current vlcrc file.
    
    Returns:
        dict with success status and message
    """
    vlcrc = get_vlcrc_path()
    backup = get_vlcrc_backup_path()

    if not vlcrc or not vlcrc.exists():
        return {
            "success": False,
            "message": "VLC configuration file not found. VLC may not have been run yet."
        }

    try:
        shutil.copy2(vlcrc, backup)
        return {
            "success": True,
            "message": f"Backup created at: {backup}",
            "backup_path": str(backup)
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to create backup: {e}"
        }


def restore_vlcrc_backup():
    """
    Restore vlcrc from backup.
    
    Returns:
        dict with success status and message
    """
    vlcrc = get_vlcrc_path()
    backup = get_vlcrc_backup_path()

    if not backup or not backup.exists():
        return {
            "success": False,
            "message": "No backup file found to restore from."
        }

    try:
        shutil.copy2(backup, vlcrc)
        return {
            "success": True,
            "message": "VLC configuration restored from backup."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to restore backup: {e}"
        }


def apply_vlcrc_optimizations():
    """
    Apply fast-startup optimizations to vlcrc file.
    Creates a backup first if one doesn't exist.
    
    Returns:
        dict with success status, message, and details of changes made
    """

    vlcrc = get_vlcrc_path()

    if not vlcrc:
        return {
            "success": False,
            "message": "Could not determine VLC configuration path for this OS."
        }

    # Ensure vlc config directory exists
    vlcrc.parent.mkdir(parents=True, exist_ok=True)

    # Create backup if vlcrc exists and backup doesn't
    backup = get_vlcrc_backup_path()
    if vlcrc.exists() and backup and not backup.exists():
        backup_result = create_vlcrc_backup()
        if not backup_result["success"]:
            return {
                "success": False,
                "message": f"Failed to create backup before optimization: {backup_result['message']}"
            }

    # Read existing content or start fresh
    if vlcrc.exists():
        try:
            content = vlcrc.read_text(encoding='utf-8', errors='ignore')
            lines = content.split('\n')
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to read vlcrc: {e}"
            }
    else:
        lines = []

    # Parse existing settings into a dict
    settings = {}
    setting_lines = {}  # Track which line each setting is on

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key, _, value = stripped.partition('=')
            key = key.strip()
            value = value.strip()
            settings[key] = value
            setting_lines[key] = i

    # Apply optimizations
    changes_made = []

    for key, value, description in VLC_OPTIMIZATION_SETTINGS:
        old_value = settings.get(key)

        if old_value != value:
            if key in setting_lines:
                # Update existing line
                line_num = setting_lines[key]
                lines[line_num] = f"{key}={value}"
                changes_made.append(f"Updated {key}: {old_value} → {value} ({description})")
            else:
                # Add new setting
                lines.append(f"{key}={value}")
                changes_made.append(f"Added {key}={value} ({description})")

            settings[key] = value

    # Add marker comment if not present
    marker = "# Movie Searcher Optimization"
    if marker not in '\n'.join(lines):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.insert(0, f"{marker} applied on {timestamp}")
        lines.insert(1, "# Original settings backed up. Use Movie Searcher settings to restore.")
        lines.insert(2, "")

    # Write back
    try:
        vlcrc.write_text('\n'.join(lines), encoding='utf-8')
        return {
            "success": True,
            "message": f"Applied {len(changes_made)} optimizations to VLC configuration.",
            "changes": changes_made,
            "vlcrc_path": str(vlcrc)
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to write vlcrc: {e}"
        }


def remove_vlcrc_optimizations():
    """
    Remove Movie Searcher optimizations from vlcrc.
    If a backup exists, restores from backup.
    Otherwise, removes the specific settings we added.
    
    Returns:
        dict with success status and message
    """
    backup = get_vlcrc_backup_path()

    # If backup exists, restore from it
    if backup and backup.exists():
        return restore_vlcrc_backup()

    # No backup - try to reset just our settings
    vlcrc = get_vlcrc_path()

    if not vlcrc or not vlcrc.exists():
        return {
            "success": True,
            "message": "VLC configuration file not found. Nothing to remove."
        }

    try:
        content = vlcrc.read_text(encoding='utf-8', errors='ignore')
        lines = content.split('\n')

        # Get list of our optimization keys
        opt_keys = {key for key, _, _ in VLC_OPTIMIZATION_SETTINGS}

        # Filter out our settings and marker comments
        new_lines = []
        for line in lines:
            stripped = line.strip()

            # Skip our marker comments
            if "Movie Searcher Optimization" in stripped:
                continue
            if "Original settings backed up" in stripped:
                continue

            # Skip our optimization settings
            if stripped and not stripped.startswith('#') and '=' in stripped:
                key = stripped.partition('=')[0].strip()
                if key in opt_keys:
                    continue

            new_lines.append(line)

        vlcrc.write_text('\n'.join(new_lines), encoding='utf-8')

        return {
            "success": True,
            "message": "Removed Movie Searcher optimizations from VLC configuration."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to remove optimizations: {e}"
        }


def get_vlcrc_optimization_info():
    """
    Get information about what optimizations would be applied.
    
    Returns:
        dict with optimization details
    """
    return {
        "settings": [
            {
                "key": key,
                "value": value,
                "description": description
            }
            for key, value, description in VLC_OPTIMIZATION_SETTINGS
        ],
        "description": """
These optimizations modify VLC's global configuration to reduce startup time:

1. **File Caching**: Reduced from 1200ms to 300ms for faster local file playback
2. **Fast Seeking**: Uses faster (but less frame-accurate) seeking when jumping to timestamps
3. **Metadata**: Disables network lookups for metadata and album art
4. **Preparsing**: Disables automatic file scanning
5. **Media Library**: Disables VLC's built-in media library
6. **UI Elements**: Disables on-screen title display and update notifications

Note: These changes affect ALL VLC usage, not just launches from Movie Searcher.
A backup of your original settings is created before applying changes.
        """.strip(),
        "notes": [
            "Hardware acceleration is NOT included as it can cause issues on some systems",
            "Changes affect all VLC usage system-wide",
            "A backup is created before any changes",
            "You can restore original settings at any time"
        ]
    }


# =============================================================================
# VLC Optimization API Router
# =============================================================================

vlc_optimization_router = APIRouter(prefix="/api/vlc/optimization", tags=["vlc"])


@vlc_optimization_router.get("/status")
async def get_vlc_optimization_status():
    """
    Get the current VLC optimization status.
    Returns info about vlcrc file, backup status, and whether optimizations are applied.
    """
    try:
        status = check_vlcrc_status()
        info = get_vlcrc_optimization_info()

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


@vlc_optimization_router.post("/apply")
async def apply_vlc_optimization():
    """
    Apply VLC fast-startup optimizations to vlcrc config file.
    This affects ALL VLC usage system-wide, not just Movie Searcher launches.
    Creates a backup before making changes.
    """
    try:
        status = check_vlcrc_status()

        if status["is_optimized"]:
            return {
                "success": True,
                "message": "VLC configuration is already optimized.",
                "already_optimized": True
            }

        result = apply_vlcrc_optimizations()
        return result
    except Exception as e:
        logger.error(f"Error applying VLC optimization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@vlc_optimization_router.post("/remove")
async def remove_vlc_optimization():
    """
    Remove VLC optimizations and restore original settings.
    If a backup exists, restores from backup.
    """
    try:
        result = remove_vlcrc_optimizations()
        return result
    except Exception as e:
        logger.error(f"Error removing VLC optimization: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@vlc_optimization_router.post("/backup")
async def create_vlc_backup_endpoint():
    """
    Create a backup of the current VLC configuration.
    """
    try:
        result = create_vlcrc_backup()
        return result
    except Exception as e:
        logger.error(f"Error creating VLC backup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@vlc_optimization_router.post("/restore")
async def restore_vlc_backup_endpoint():
    """
    Restore VLC configuration from backup.
    """
    try:
        result = restore_vlcrc_backup()
        return result
    except Exception as e:
        logger.error(f"Error restoring VLC backup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@vlc_optimization_router.get("/flags")
async def get_vlc_flags_status():
    """
    Get current status of VLC optimization flags.
    Returns which flags are available, which are tested safe, and current config.
    """
    try:
        from config import load_config
        config = load_config()

        safe_flags = config.get("vlc_safe_flags", [])
        hw_accel_enabled = config.get("vlc_hardware_acceleration", False)
        hw_accel_safe = config.get("vlc_hw_accel_safe", False)

        return {
            "available_flags": [
                {"flag": flag, "description": desc, "safe": flag in safe_flags}
                for flag, desc in VLC_OPTIMIZATION_FLAGS
            ],
            "safe_flags": safe_flags,
            "safe_count": len(safe_flags),
            "total_available": len(VLC_OPTIMIZATION_FLAGS),
            "hw_acceleration": {
                "enabled": hw_accel_enabled,
                "tested_safe": hw_accel_safe
            }
        }
    except Exception as e:
        logger.error(f"Error getting VLC flags status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@vlc_optimization_router.post("/flags/test")
async def test_vlc_flags_endpoint():
    """
    Test all VLC optimization flags to determine which are safe on this system.
    This launches VLC multiple times briefly to test each flag.
    Results are saved to config for future launches.
    """
    try:
        result = test_all_vlc_flags()
        return result
    except Exception as e:
        logger.error(f"Error testing VLC flags: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@vlc_optimization_router.post("/flags/test-hw")
async def test_hw_acceleration_endpoint():
    """
    Test if hardware acceleration (GPU decoding) works on this system.
    """
    try:
        result = test_hw_acceleration()
        return result
    except Exception as e:
        logger.error(f"Error testing hardware acceleration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@vlc_optimization_router.post("/flags/clear")
async def clear_vlc_flags_endpoint():
    """
    Clear all tested flags and start fresh.
    """
    try:
        from config import load_config, save_config
        config = load_config()
        config["vlc_safe_flags"] = []
        config["vlc_hw_accel_safe"] = False
        save_config(config)
        return {"success": True, "message": "Cleared all tested VLC flags"}
    except Exception as e:
        logger.error(f"Error clearing VLC flags: {e}")
        raise HTTPException(status_code=500, detail=str(e))
