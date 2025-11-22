import video_processing
import subprocess
import os
from unittest.mock import MagicMock, patch
import repro_issue

# Create test files if they don't exist
if not os.path.exists("video_ok.mp4") or not os.path.exists("audio_only.mp4"):
    repro_issue.create_test_files()

# Mock _get_ffprobe_path_from_config to return "ffprobe" (assuming it's in path)
def mock_get_ffprobe():
    return "ffprobe"

with patch('video_processing._get_ffprobe_path_from_config', side_effect=mock_get_ffprobe):
    print(f"Checking video_ok.mp4: {video_processing.has_video_stream('video_ok.mp4')}")
    print(f"Checking audio_only.mp4: {video_processing.has_video_stream('audio_only.mp4')}")

