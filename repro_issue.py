import subprocess
import os
from pathlib import Path

def create_test_files():
    # Create audio-only file
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", 
        "-t", "5", "audio_only.mp4"
    ], capture_output=True)
    
    # Create video file
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=5:size=1280x720:rate=30", 
        "video_ok.mp4"
    ], capture_output=True)

def try_extract(filename):
    out_path = f"{filename}.jpg"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", "2",
        "-i", filename,
        "-vf", "scale=iw:ih",
        "-vframes", "1",
        "-q:v", "2",
        "-y",
        out_path
    ]
    
    print(f"Running for {filename}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Return code: {result.returncode}")
    print(f"Stderr: {result.stderr}")
    if result.returncode != 0:
        print("FAILED as expected" if "does not contain any stream" in result.stderr or result.returncode != 0 else "FAILED with unknown error")
    else:
        print("SUCCESS")

if __name__ == "__main__":
    create_test_files()
    try_extract("video_ok.mp4")
    try_extract("audio_only.mp4")

