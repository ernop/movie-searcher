"""
FFmpeg setup and configuration module.
Handles detection, installation, testing, and configuration of ffmpeg and ffprobe.
"""
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path for imports
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config, save_config
from video_processing import test_ffmpeg_comprehensive, validate_ffmpeg_path

logger = logging.getLogger(__name__)


def find_ffmpeg_and_ffprobe_in_winget():
    """Find both ffmpeg and ffprobe in winget installation directory"""
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if not localappdata:
        return None, None

    winget_pattern = Path(localappdata) / "Microsoft" / "WinGet" / "Packages"
    if not winget_pattern.exists():
        return None, None

    # Look for Gyan.FFmpeg installations
    for ffmpeg_dir in winget_pattern.glob("Gyan.FFmpeg_*"):
        ffmpeg_path = None
        ffprobe_path = None

        # Check all subdirectories for ffmpeg and ffprobe
        for subdir in ffmpeg_dir.iterdir():
            if not subdir.is_dir():
                continue

            bin_dir = subdir / "bin"
            if not bin_dir.exists():
                continue

            # Check for ffmpeg
            ffmpeg_candidate = bin_dir / "ffmpeg.exe"
            if ffmpeg_candidate.exists() and not ffmpeg_path:
                ffmpeg_path = str(ffmpeg_candidate)

            # Check for ffprobe
            ffprobe_candidate = bin_dir / "ffprobe.exe"
            if ffprobe_candidate.exists() and not ffprobe_path:
                ffprobe_path = str(ffprobe_candidate)

        # If we found both, return them
        if ffmpeg_path and ffprobe_path:
            return ffmpeg_path, ffprobe_path

    return None, None


def ensure_ffmpeg_configured():
    """
    Comprehensive startup check: find, test, install, and configure ffmpeg and ffprobe.
    Retries until both are working and saved to config.
    Only returns when everything is properly configured.
    """
    max_attempts = 3
    for attempt in range(max_attempts):
        logger.info(f"=== FFmpeg Configuration Check (Attempt {attempt + 1}/{max_attempts}) ===")

        config = load_config()

        # Test current configuration if it exists
        ffmpeg_path = config.get("ffmpeg_path")
        ffprobe_path = config.get("ffprobe_path")
        if ffmpeg_path:
            test_result = test_ffmpeg_comprehensive(ffmpeg_path)
            # REQUIRE both ffmpeg AND ffprobe to be tested and working
            if test_result["ok"] and test_result["ffmpeg_ok"] and test_result["ffprobe_ok"]:
                # REQUIRE ffprobe_path to be found and saved
                if not test_result.get("ffprobe_path"):
                    logger.error("ffprobe test passed but path not found in test results - this should not happen!")
                    # Continue to re-configure
                elif not ffprobe_path or ffprobe_path != test_result["ffprobe_path"]:
                    # Update ffprobe_path if missing or changed
                    config["ffprobe_path"] = test_result["ffprobe_path"]
                    save_config(config)
                    logger.info(f"Updated stored ffprobe_path: {test_result['ffprobe_path']}")
                logger.info("FFmpeg and ffprobe are properly configured and working")
                logger.info(f"  ffmpeg: {ffmpeg_path}")
                logger.info(f"  ffprobe: {config.get('ffprobe_path', 'NOT STORED!')}")
                # Double-check: if ffprobe_path is not stored, we're not ready
                if not config.get("ffprobe_path"):
                    logger.error("CRITICAL: ffprobe_path not stored in config despite test passing!")
                    # Continue to re-configure
                else:
                    return True
            else:
                logger.warning(f"Current config test failed: ffmpeg_ok={test_result.get('ffmpeg_ok')}, ffprobe_ok={test_result.get('ffprobe_ok')}")
                if test_result.get("errors"):
                    for error in test_result["errors"]:
                        logger.warning(f"  Error: {error}")

        # If we get here, we need to find/install ffmpeg
        logger.info("Searching for ffmpeg installation...")

        # 1. Check PATH
        ffmpeg_exe = shutil.which("ffmpeg")
        ffprobe_exe = shutil.which("ffprobe")

        if ffmpeg_exe and ffprobe_exe:
            ffmpeg_valid, _ = validate_ffmpeg_path(ffmpeg_exe)
            if ffmpeg_valid:
                # Test both - REQUIRE both to pass
                test_result = test_ffmpeg_comprehensive(ffmpeg_exe)
                if test_result["ok"] and test_result["ffmpeg_ok"] and test_result["ffprobe_ok"]:
                    # REQUIRE ffprobe_path to be found
                    if not test_result.get("ffprobe_path"):
                        logger.error("ffprobe test passed but path not found in test results!")
                        # Continue searching
                    else:
                        config["ffmpeg_path"] = ffmpeg_exe
                        config["ffprobe_path"] = test_result["ffprobe_path"]
                        save_config(config)
                        logger.info(f"Found and configured ffmpeg from PATH: {ffmpeg_exe}")
                        logger.info(f"  ffprobe: {test_result['ffprobe_path']}")
                        return True
                else:
                    logger.warning(f"PATH test failed: ffmpeg_ok={test_result.get('ffmpeg_ok')}, ffprobe_ok={test_result.get('ffprobe_ok')}")

        # 2. Check common installation locations
        common_paths = [
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/Program Files (x86)/ffmpeg/bin/ffmpeg.exe"),
        ]

        for ffmpeg_path_obj in common_paths:
            if ffmpeg_path_obj.exists():
                # Test ffmpeg - this will find ffprobe automatically
                test_result = test_ffmpeg_comprehensive(str(ffmpeg_path_obj))
                # REQUIRE both to pass
                if test_result["ok"] and test_result["ffmpeg_ok"] and test_result["ffprobe_ok"]:
                    # REQUIRE ffprobe_path to be found
                    if not test_result.get("ffprobe_path"):
                        logger.error("ffprobe test passed but path not found in test results!")
                        # Continue searching
                    else:
                        config["ffmpeg_path"] = str(ffmpeg_path_obj)
                        config["ffprobe_path"] = test_result["ffprobe_path"]
                        save_config(config)
                        logger.info(f"Found and configured ffmpeg: {ffmpeg_path_obj}")
                        logger.info(f"  ffprobe: {test_result['ffprobe_path']}")
                        return True
                else:
                    logger.debug(f"Common path test failed for {ffmpeg_path_obj}: ffmpeg_ok={test_result.get('ffmpeg_ok')}, ffprobe_ok={test_result.get('ffprobe_ok')}")

        # 3. Check winget installation location
        ffmpeg_path, ffprobe_path_found = find_ffmpeg_and_ffprobe_in_winget()
        if ffmpeg_path:
            # Test with the found ffmpeg and ffprobe paths - pass ffprobe_path directly
            # since we already know where it is (they're in separate subdirectories)
            test_result = test_ffmpeg_comprehensive(ffmpeg_path, ffprobe_path=ffprobe_path_found)
            # REQUIRE both to pass
            if test_result["ok"] and test_result["ffmpeg_ok"] and test_result["ffprobe_ok"]:
                # REQUIRE ffprobe_path to be found
                if not test_result.get("ffprobe_path"):
                    logger.error("ffprobe test passed but path not found in test results!")
                    # Continue searching
                else:
                    config["ffmpeg_path"] = ffmpeg_path
                    config["ffprobe_path"] = test_result["ffprobe_path"]
                    save_config(config)
                    logger.info(f"Found and configured ffmpeg from winget: {ffmpeg_path}")
                    logger.info(f"  ffprobe: {test_result['ffprobe_path']}")
                    return True
            else:
                logger.warning(f"Winget test failed: ffmpeg_ok={test_result.get('ffmpeg_ok')}, ffprobe_ok={test_result.get('ffprobe_ok')}")
                if test_result.get("errors"):
                    for error in test_result["errors"]:
                        logger.warning(f"  Error: {error}")
                # Continue to try installation

        # 4. If not found, try to install via winget
        if attempt < max_attempts - 1:  # Don't try to install on last attempt
            logger.info("ffmpeg not found. Attempting to install via winget...")
            try:
                winget_check = subprocess.run(["winget", "--version"], capture_output=True, timeout=5)
                if winget_check.returncode == 0:
                    logger.info("Installing ffmpeg via winget...")
                    install_result = subprocess.run(
                        ["winget", "install", "--id=Gyan.FFmpeg", "-e", "--accept-source-agreements", "--accept-package-agreements", "--silent"],
                        capture_output=True,
                        timeout=120
                    )
                    if install_result.returncode == 0:
                        logger.info("Installation completed. Waiting for files to be ready...")
                        time.sleep(5)  # Wait longer for installation to complete
                        # Will retry on next iteration
                        continue
                    else:
                        logger.warning(f"Installation failed: {install_result.stderr.decode('utf-8', errors='ignore')}")
                else:
                    logger.warning("winget not available")
            except Exception as e:
                logger.warning(f"Error during installation attempt: {e}")

        # Wait before retry
        if attempt < max_attempts - 1:
            time.sleep(2)

    # If we get here, we failed
    logger.error("Failed to configure ffmpeg after all attempts. Screenshot extraction will be disabled.")
    return False


def auto_detect_ffmpeg():
    """Auto-detect ffmpeg and save to config if found. Install if missing."""
    # Use the comprehensive check that tests everything and retries until working
    return ensure_ffmpeg_configured()

