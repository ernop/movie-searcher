"""Generate test screenshots with burned-in subtitles for Akira"""
import subprocess
import sys
from pathlib import Path

video_path = r"D:\movies\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous_.mp4"
subtitle_path = r"D:\movies\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous_eng.srt"
ffmpeg_path = r"C:\ProgramData\chocolatey\bin\ffmpeg.EXE"

# Create test output directory
output_dir = Path(r"D:\proj\movie-searcher\test_subs_screenshots")
output_dir.mkdir(exist_ok=True)

# Get timestamps that actually have subtitles
import subprocess
import re
subtitle_file_path = subtitle_path
with open(subtitle_file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Parse SRT format to find subtitle timestamps
pattern = r'(\d+)\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})'
matches = re.findall(pattern, content)

subtitle_timestamps = []
for match in matches[:20]:  # Get first 20
    start_h, start_m, start_s, start_ms = int(match[1]), int(match[2]), int(match[3]), int(match[4])
    end_h, end_m, end_s, end_ms = int(match[5]), int(match[6]), int(match[7]), int(match[8])
    start_sec = start_h * 3600 + start_m * 60 + start_s + start_ms / 1000
    end_sec = end_h * 3600 + end_m * 60 + end_s + end_ms / 1000
    mid_sec = int((start_sec + end_sec) / 2)
    subtitle_timestamps.append(mid_sec)

test_timestamps = sorted(subtitle_timestamps)
print(f"Found {len(test_timestamps)} timestamps with subtitles")

print(f"Generating test screenshots with burned-in subtitles:")
print(f"  Video: {video_path}")
print(f"  Subtitle: {subtitle_path}")
print(f"  Output directory: {output_dir}")
print(f"  Timestamps: {test_timestamps}")
print()

# Use relative path if subtitle is in same directory as video (same logic as video_processing.py)
video_dir = Path(video_path).parent.resolve()
subtitle_file = Path(subtitle_path).resolve()

if subtitle_file.parent == video_dir:
    subtitle_path_for_filter = subtitle_file.name
    print(f"Using relative subtitle path: {subtitle_path_for_filter}")
else:
    escaped_path = str(subtitle_file).replace('\\', '/')
    if ':' in escaped_path:
        escaped_path = escaped_path.replace(':', '\\\\:', 1)
    subtitle_path_for_filter = escaped_path
    print(f"Using absolute subtitle path: {subtitle_path_for_filter}")

print()

success_count = 0
for ts in test_timestamps:
    output_file = output_dir / f"akira_test_{ts}s_subs.jpg"
    
    print(f"Generating screenshot at {ts}s ({ts//60}m {ts%60}s)...", end=" ")
    
    # Use relative path for video input when running from video directory
    video_path_normalized = Path(video_path).resolve()
    video_input = video_path_normalized.name if subtitle_path_for_filter == subtitle_file.name else str(video_path_normalized)
    output_absolute = output_file.resolve()
    
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error",
        "-ss", str(ts),
        "-i", video_input,
        "-vf", f"scale=iw:ih,subtitles={subtitle_path_for_filter}:force_style='FontSize=24,OutlineColour=&H80000000,BorderStyle=3,MarginV=40'",
        "-vframes", "1",
        "-q:v", "2",
        "-y",
        str(output_absolute)
    ]
    
    try:
        # Run from video directory if using relative subtitle path
        working_dir = str(video_dir) if subtitle_path_for_filter == subtitle_file.name else None
        result = subprocess.run(cmd, cwd=working_dir, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and output_file.exists():
            file_size = output_file.stat().st_size / 1024  # KB
            print(f"SUCCESS ({file_size:.1f} KB)")
            success_count += 1
        else:
            print(f"FAILED (returncode={result.returncode})")
            if result.stderr:
                print(f"  Error: {result.stderr[:200]}")
    except Exception as e:
        print(f"ERROR: {e}")

print()
print(f"Generated {success_count}/{len(test_timestamps)} screenshots successfully")
print(f"Check the output directory: {output_dir}")

