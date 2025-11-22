import subprocess

def check_video_stream(file_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=nw=1:nk=1",
        str(file_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"ffprobe stdout for {file_path}: '{result.stdout.strip()}'")
    return "codec_type=video" in result.stdout

if __name__ == "__main__":
    print("Has video stream (video_ok.mp4):", check_video_stream("video_ok.mp4"))
    print("Has video stream (audio_only.mp4):", check_video_stream("audio_only.mp4"))

