#!/usr/bin/env python3
"""
Movie Searcher startup script.
Replaces start.bat with a cross-platform Python solution.
"""
import os
import sys
import socket
import subprocess
import shutil
import time
import webbrowser
from pathlib import Path

# Server configuration
SERVER_PORT = 8002
SERVER_URL = f"http://localhost:{SERVER_PORT}"

def check_port_in_use(port: int) -> bool:
    """Check if a port is already in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            return False
        except OSError:
            return True

def check_ffmpeg() -> bool:
    """Check if ffmpeg is available in PATH"""
    return shutil.which("ffmpeg") is not None

def check_vlc() -> bool:
    """Check if VLC is available in PATH"""
    return shutil.which("vlc") is not None

def try_install_ffmpeg() -> bool:
    """Try to install ffmpeg via winget (Windows only)"""
    if sys.platform != "win32":
        return False
    
    # Check if winget is available
    try:
        result = subprocess.run(
            ["winget", "--version"],
            capture_output=True,
            timeout=5
        )
        if result.returncode != 0:
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    
    print("ffmpeg not found in PATH. Attempting to install via winget...")
    try:
        result = subprocess.run(
            [
                "winget", "install", "--id=Gyan.FFmpeg", "-e",
                "--accept-source-agreements", "--accept-package-agreements", "--silent"
            ],
            timeout=60,
            capture_output=True
        )
        if result.returncode == 0:
            print("Waiting for installation to complete...")
            time.sleep(3)
            return True
        else:
            print(f"winget installation failed: {result.stderr.decode('utf-8', errors='ignore')}")
            return False
    except subprocess.TimeoutExpired:
        print("winget installation timed out")
        return False
    except Exception as e:
        print(f"Error during winget installation: {e}")
        return False

def try_install_vlc() -> bool:
    """Try to install VLC via winget (Windows only)"""
    if sys.platform != "win32":
        return False
    
    # Check if winget is available
    try:
        result = subprocess.run(
            ["winget", "--version"],
            capture_output=True,
            timeout=5
        )
        if result.returncode != 0:
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    
    print("VLC not found. Attempting to install via winget...")
    try:
        result = subprocess.run(
            [
                "winget", "install", "--id=VideoLAN.VLC", "-e",
                "--accept-source-agreements", "--accept-package-agreements", "--silent"
            ],
            timeout=120,  # VLC installation can take longer
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("VLC installation completed successfully!")
            print("Waiting for installation to finalize...")
            time.sleep(5)
            return True
        else:
            stderr = result.stderr if result.stderr else "Unknown error"
            print(f"winget installation failed: {stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("VLC installation timed out (this can happen with large downloads)")
        print("Please wait a moment and restart the server, or install VLC manually")
        return False
    except Exception as e:
        print(f"Error during VLC installation: {e}")
        return False

def run_setup_ffmpeg() -> bool:
    """Run setup/setup_ffmpeg.py to detect and save ffmpeg path"""
    print("Detecting and saving ffmpeg path...")
    try:
        result = subprocess.run(
            [sys.executable, "setup/setup_ffmpeg.py"],
            cwd=Path(__file__).parent,
            timeout=30
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("ERROR: setup/setup_ffmpeg.py timed out")
        return False
    except Exception as e:
        print(f"ERROR: Failed to run setup/setup_ffmpeg.py: {e}")
        return False

def run_setup_vlc() -> bool:
    """Run setup/setup_vlc.py to detect and save VLC path"""
    print("Detecting and saving VLC path...")
    try:
        result = subprocess.run(
            [sys.executable, "setup/setup_vlc.py"],
            cwd=Path(__file__).parent,
            timeout=30
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("ERROR: setup/setup_vlc.py timed out")
        return False
    except Exception as e:
        print(f"ERROR: Failed to run setup/setup_vlc.py: {e}")
        return False

def start_server(open_browser_url: str | None = None):
    """Start the server by importing and running it"""
    print("Starting Movie Searcher server...")
    print(f"Server will be available at {SERVER_URL}")
    print("Press Ctrl+C to stop the server")
    print("=" * 60)
    
    # Import and run the server
    from server import run_server
    run_server(open_browser_url=open_browser_url)

def main():
    """Main startup logic"""
    print("Movie Searcher Launcher")
    print()
    
    # Change to script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Check if server is already running
    print("Checking if server is already running...")
    if check_port_in_use(SERVER_PORT):
        print("Server is already running!")
        print(f"Opening browser to {SERVER_URL}...")
        webbrowser.open(SERVER_URL)
        print(f"\nServer URL: {SERVER_URL}")
        return 0
    
    # Check for ffmpeg
    print("Checking for ffmpeg...")
    if not check_ffmpeg():
        if sys.platform == "win32":
            if not try_install_ffmpeg():
                print("WARNING: ffmpeg not found and could not be installed automatically.")
                print("Please install ffmpeg manually:")
                print("  Download from: https://ffmpeg.org/download.html")
                print("  Or use: winget install --id=Gyan.FFmpeg")
        else:
            print("WARNING: ffmpeg not found in PATH.")
            print("Please install ffmpeg manually.")
    
    # Run setup/setup_ffmpeg.py to detect and save ffmpeg path
    if not run_setup_ffmpeg():
        print()
        print("ERROR: ffmpeg setup failed! Please fix the errors above before continuing.")
        print()
        input("Press Enter to exit...")
        return 1
    
    # Check for VLC
    print("Checking for VLC...")
    if not check_vlc():
        if sys.platform == "win32":
            if not try_install_vlc():
                print("WARNING: VLC not found and could not be installed automatically.")
                print("Please install VLC manually:")
                print("  Download from: https://www.videolan.org/vlc/")
                print("  Or use: winget install --id=VideoLAN.VLC")
        else:
            print("WARNING: VLC not found in PATH.")
            print("Please install VLC manually.")
    
    # Run setup/setup_vlc.py to detect and save VLC path
    if not run_setup_vlc():
        print()
        print("ERROR: VLC setup failed! VLC is required to launch movies.")
        print("Please install VLC from https://www.videolan.org/vlc/")
        print("Or use: winget install --id=VideoLAN.VLC")
        print()
        input("Press Enter to exit...")
        return 1
    
    # Start the server (blocks until Ctrl+C, opens browser when ready)
    try:
        start_server(open_browser_url=SERVER_URL)
    except KeyboardInterrupt:
        print("\n\nServer stopped by user.")
        return 0
    except Exception as e:
        print(f"\n\nERROR: Server failed to start: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        return 1

if __name__ == "__main__":
    sys.exit(main())

