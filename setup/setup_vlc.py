"""
Helper script to detect and save VLC path to config.
Called from start.py during startup.

CRITICAL: VLC executable must exist and be accessible before the server starts.
We don't run '--version' because on Windows it can pop up GUI dialogs that
require user interaction (pressing Enter). File existence check is sufficient.
"""
import os
import shutil
import sys
from pathlib import Path

# Add parent directory to path so we can import from the main project
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

from config import load_config, save_config
from database import init_db


def setup_vlc():
    """Detect VLC and save to config"""
    # Initialize database if needed
    try:
        init_db()
    except Exception:
        # Database might already be initialized, that's fine
        pass

    try:
        config = load_config()
    except Exception as e:
        print(f"WARNING: Error loading config: {e}")
        return

    # If already configured and valid, skip
    if config.get("vlc_path"):
        vlc_path = config["vlc_path"]
        if Path(vlc_path).exists():
            print(f"VLC already configured: {vlc_path}")
            return
        else:
            print(f"WARNING: Configured VLC path no longer exists: {vlc_path}")
            print("Searching for VLC...")

    # Try to find VLC in PATH
    vlc_exe = shutil.which("vlc")
    if vlc_exe:
        config["vlc_path"] = vlc_exe
        try:
            save_config(config)
            print(f"Detected and saved VLC from PATH: {vlc_exe}")
            return
        except Exception as e:
            print(f"WARNING: Failed to save VLC config: {e}")
            return

    # Try common installation locations (Windows)
    common_paths = [
        Path(r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
        Path(r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"),
        Path(os.path.expanduser(r"~\AppData\Local\Programs\VideoLAN\vlc.exe")),
    ]

    # Also check winget installation locations
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        winget_pattern = Path(localappdata) / "Microsoft" / "WinGet" / "Packages"
        if winget_pattern.exists():
            # Look for VideoLAN.VLC installations
            for vlc_dir in winget_pattern.glob("VideoLAN.VLC*"):
                vlc_exe_path = vlc_dir / "vlc.exe"
                if vlc_exe_path.exists():
                    common_paths.append(vlc_exe_path)
                # Also check in subdirectories
                for subdir in vlc_dir.iterdir():
                    if subdir.is_dir():
                        vlc_exe_path = subdir / "vlc.exe"
                        if vlc_exe_path.exists():
                            common_paths.append(vlc_exe_path)

    # Also check Program Files for any VLC installation
    program_files = [
        Path(r"C:\Program Files"),
        Path(r"C:\Program Files (x86)")
    ]
    for pf in program_files:
        if pf.exists():
            # Look for VideoLAN directories
            for vlc_dir in pf.glob("VideoLAN*/VLC"):
                vlc_exe = vlc_dir / "vlc.exe"
                if vlc_exe.exists():
                    common_paths.append(vlc_exe)
            # Also direct VLC directories
            for vlc_dir in pf.glob("VLC*"):
                vlc_exe = vlc_dir / "vlc.exe"
                if vlc_exe.exists():
                    common_paths.append(vlc_exe)

    # Check Windows Registry (Windows only)
    if os.name == 'nt':
        try:
            import winreg
            registry_keys = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VideoLAN\VLC"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\VideoLAN\VLC"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\VideoLAN\VLC"),
            ]

            for hkey, key_path in registry_keys:
                try:
                    with winreg.OpenKey(hkey, key_path) as key:
                        install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                        vlc_path = Path(install_dir) / "vlc.exe"
                        if vlc_path.exists():
                            common_paths.append(vlc_path)
                except (FileNotFoundError, OSError):
                    continue
        except Exception:
            pass  # Registry check failed, continue with other methods

    for vlc_path in common_paths:
        if vlc_path.exists():
            # VLC found - save it (we don't test --version as it can pop up dialogs on Windows)
            config["vlc_path"] = str(vlc_path)
            try:
                save_config(config)
                print(f"Detected and saved VLC: {vlc_path}")
                return
            except Exception as e:
                print(f"WARNING: Failed to save VLC config: {e}")
                return

    print("WARNING: VLC not found. Install from https://www.videolan.org/")
    print("Checked locations:")
    for path in common_paths[:3]:  # Show first 3 common paths
        print(f"  - {path}")
    # Exit with 0 - VLC is optional (app works for browsing without it)
    sys.exit(0)

if __name__ == "__main__":
    setup_vlc()
