"""
Test script to verify ffmpeg setup works correctly.
Clears config and runs setup from scratch to ensure both ffmpeg and ffprobe are tested and saved.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config, save_config
from database import init_db
from setup.ffmpeg_setup import ensure_ffmpeg_configured
from video_processing import test_ffmpeg_comprehensive


def clear_ffmpeg_config():
    """Clear ffmpeg and ffprobe config to test from scratch"""
    from config import load_config
    config = load_config()
    config.pop("ffmpeg_path", None)
    config.pop("ffprobe_path", None)
    save_config(config)
    print("Cleared ffmpeg and ffprobe config")

def verify_config():
    """Verify that both paths are stored and working"""
    config = load_config()

    ffmpeg_path = config.get("ffmpeg_path")
    ffprobe_path = config.get("ffprobe_path")

    print("\n" + "=" * 70)
    print("Configuration Verification")
    print("=" * 70)

    if not ffmpeg_path:
        print("[FAIL] ffmpeg_path: NOT CONFIGURED")
        return False
    else:
        print(f"[OK] ffmpeg_path: {ffmpeg_path}")

    if not ffprobe_path:
        print("[FAIL] ffprobe_path: NOT CONFIGURED")
        return False
    else:
        print(f"[OK] ffprobe_path: {ffprobe_path}")

    # Test both
    print("\nTesting ffmpeg and ffprobe...")
    test_result = test_ffmpeg_comprehensive(ffmpeg_path)

    if test_result["ok"] and test_result["ffmpeg_ok"] and test_result["ffprobe_ok"]:
        print("[OK] Both ffmpeg and ffprobe are working")
        print(f"  ffmpeg version: {test_result.get('ffmpeg_version', 'unknown')}")
        print(f"  ffprobe version: {test_result.get('ffprobe_version', 'unknown')}")

        # Verify stored ffprobe_path matches tested path
        if test_result.get("ffprobe_path") == ffprobe_path:
            print("[OK] Stored ffprobe_path matches tested path")
            return True
        else:
            print(f"[FAIL] Stored ffprobe_path ({ffprobe_path}) does not match tested path ({test_result.get('ffprobe_path')})")
            return False
    else:
        print("[FAIL] Tests failed:")
        for error in test_result.get("errors", []):
            print(f"  - {error}")
        return False

def main():
    print("=" * 70)
    print("FFmpeg Setup Test - From Scratch")
    print("=" * 70)

    # Initialize database
    try:
        init_db()
    except Exception as e:
        print(f"ERROR: Failed to initialize database: {e}")
        return False

    # Clear config to test from scratch
    print("\nStep 1: Clearing existing config...")
    clear_ffmpeg_config()

    # Run setup
    print("\nStep 2: Running ffmpeg setup...")
    success = ensure_ffmpeg_configured()

    if not success:
        print("\n[FAIL] Setup failed!")
        return False

    print("\nStep 3: Verifying configuration...")
    if verify_config():
        print("\n" + "=" * 70)
        print("[SUCCESS] ALL TESTS PASSED - Setup is working correctly!")
        print("=" * 70)
        return True
    else:
        print("\n" + "=" * 70)
        print("[FAIL] VERIFICATION FAILED")
        print("=" * 70)
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

