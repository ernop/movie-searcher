"""
Helper script to detect and save ffmpeg path to config.
Called from start.bat after installing ffmpeg.
"""
import os
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config, save_config
from database import init_db
from video_processing import validate_ffmpeg_path


def setup_ffmpeg():
    """Detect ffmpeg and save to config"""
    # Initialize database if needed
    try:
        init_db()
    except Exception:
        # Database might already be initialized, that's fine
        pass

    try:
        config = load_config()
    except Exception as e:
        print(f"ERROR: Error loading config: {e}")
        sys.exit(1)

    # If already configured and valid, skip
    if config.get("ffmpeg_path"):
        try:
            is_valid, _ = validate_ffmpeg_path(config["ffmpeg_path"])
            if is_valid:
                print(f"ffmpeg already configured: {config['ffmpeg_path']}")
                return
        except Exception as e:
            print(f"ERROR: Failed to validate existing ffmpeg path: {e}")
            sys.exit(1)

    # Try to find ffmpeg in PATH
    ffmpeg_exe = shutil.which("ffmpeg")
    if ffmpeg_exe:
        try:
            is_valid, _ = validate_ffmpeg_path(ffmpeg_exe)
            if is_valid:
                config["ffmpeg_path"] = ffmpeg_exe
                try:
                    save_config(config)
                    print(f"Detected and saved ffmpeg: {ffmpeg_exe}")
                    return
                except Exception as e:
                    print(f"ERROR: Failed to save config: {e}")
                    sys.exit(1)
        except Exception as e:
            print(f"ERROR: Failed to validate ffmpeg path: {e}")
            sys.exit(1)

    # Try common installation locations (check if they exist, regardless of OS)
    common_paths = [
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files (x86)/ffmpeg/bin/ffmpeg.exe"),
    ]

    # Check winget installation location if LOCALAPPDATA exists (indicates Windows)
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        winget_pattern = Path(localappdata) / "Microsoft" / "WinGet" / "Packages"
        if winget_pattern.exists():
            # Look for Gyan.FFmpeg installations
            for ffmpeg_dir in winget_pattern.glob("Gyan.FFmpeg_*"):
                # Find ffmpeg-* subdirectories
                for version_dir in ffmpeg_dir.glob("ffmpeg-*"):
                    ffmpeg_bin = version_dir / "bin" / "ffmpeg.exe"
                    if ffmpeg_bin.exists():
                        common_paths.append(ffmpeg_bin)

    for ffmpeg_path in common_paths:
        if ffmpeg_path.exists():
            try:
                is_valid, _ = validate_ffmpeg_path(str(ffmpeg_path))
                if is_valid:
                    config["ffmpeg_path"] = str(ffmpeg_path)
                    try:
                        save_config(config)
                        print(f"Detected and saved ffmpeg: {ffmpeg_path}")
                        return
                    except Exception as e:
                        print(f"ERROR: Failed to save config: {e}")
                        sys.exit(1)
            except Exception as e:
                print(f"ERROR: Failed to validate ffmpeg path: {e}")
                sys.exit(1)

    print("ERROR: ffmpeg not found. Please install manually or configure path in settings.")
    sys.exit(1)

if __name__ == "__main__":
    setup_ffmpeg()

