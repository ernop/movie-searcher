import subprocess
import re

def get_video_length_sim(file_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(file_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"ffprobe return code: {result.returncode}")
    print(f"ffprobe stderr: {result.stderr}")
    print(f"ffprobe stdout: {result.stdout}")
    
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    if not out:
        return None
    return float(out)

if __name__ == "__main__":
    print("Checking video_ok.mp4:")
    print(get_video_length_sim("video_ok.mp4"))
    print("\nChecking audio_only.mp4:")
    print(get_video_length_sim("audio_only.mp4"))

