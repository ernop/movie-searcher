import subprocess

def get_video_stream_duration(file_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration",
        "-of", "default=nw=1:nk=1",
        str(file_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"ffprobe stdout for {file_path}: '{result.stdout.strip()}'")

if __name__ == "__main__":
    print("Checking stream=duration for video_ok.mp4:")
    get_video_stream_duration("video_ok.mp4")
    print("\nChecking stream=duration for audio_only.mp4:")
    get_video_stream_duration("audio_only.mp4")

