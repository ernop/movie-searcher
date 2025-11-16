"""Test converting SRT to ASS and using ass filter"""
import subprocess
from pathlib import Path

video = r"D:\movies\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous_.mp4"
sub_srt = r"D:\movies\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous_eng.srt"
sub_ass = r"D:\movies\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous\Akira.1988.25th.Anniversary.Edition.1080p.BluRay.x264.anoXmous_eng.ass"
ffmpeg = r"C:\ProgramData\chocolatey\bin\ffmpeg.EXE"

video_dir = Path(video).parent
video_name = Path(video).name
sub_srt_name = Path(sub_srt).name
sub_ass_name = Path(sub_ass).name

timestamp = "1036"  # 17m 16s

print("Step 1: Converting SRT to ASS...")
print("=" * 60)
cmd_convert = [
    ffmpeg,
    "-i", str(sub_srt),
    "-y",
    str(sub_ass)
]
result_convert = subprocess.run(cmd_convert, capture_output=True, text=True, timeout=10)
print(f"Return code: {result_convert.returncode}")
if result_convert.returncode == 0:
    print(f"SUCCESS: ASS file created at {sub_ass}")
    print(f"File size: {Path(sub_ass).stat().st_size} bytes")
else:
    print(f"FAILED")
    if result_convert.stderr:
        print(f"Stderr: {result_convert.stderr}")

if not Path(sub_ass).exists():
    print("\nERROR: ASS file not created, stopping test")
    exit(1)

print("\nStep 2: Test with 'ass' filter...")
print("=" * 60)
output_ass = r"D:\proj\movie-searcher\test_ass_filter.jpg"
cmd_ass = [
    ffmpeg,
    "-ss", timestamp,
    "-i", video_name,
    "-vf", f"ass={sub_ass_name}",
    "-vframes", "1",
    "-y",
    str(Path(output_ass).resolve())
]
print(f"Command: {' '.join(cmd_ass)}")
result_ass = subprocess.run(cmd_ass, cwd=str(video_dir), capture_output=True, text=True, timeout=30)
size_ass = Path(output_ass).stat().st_size if Path(output_ass).exists() else 0
print(f"Return code: {result_ass.returncode}")
print(f"File size: {size_ass} bytes")
if result_ass.returncode != 0 and result_ass.stderr:
    print(f"Stderr: {result_ass.stderr[-300:]}")

print("\nStep 3: Test with 'subtitles' filter on SRT...")
print("=" * 60)
output_srt = r"D:\proj\movie-searcher\test_subtitles_filter.jpg"
cmd_srt = [
    ffmpeg,
    "-ss", timestamp,
    "-i", video_name,
    "-vf", f"subtitles={sub_srt_name}",
    "-vframes", "1",
    "-y",
    str(Path(output_srt).resolve())
]
result_srt = subprocess.run(cmd_srt, cwd=str(video_dir), capture_output=True, text=True, timeout=30)
size_srt = Path(output_srt).stat().st_size if Path(output_srt).exists() else 0
print(f"Return code: {result_srt.returncode}")
print(f"File size: {size_srt} bytes")

print("\nStep 4: No subtitles for comparison...")
print("=" * 60)
output_none = r"D:\proj\movie-searcher\test_no_subtitles.jpg"
cmd_none = [
    ffmpeg,
    "-ss", timestamp,
    "-i", video_name,
    "-vframes", "1",
    "-y",
    str(Path(output_none).resolve())
]
result_none = subprocess.run(cmd_none, cwd=str(video_dir), capture_output=True, timeout=30)
size_none = Path(output_none).stat().st_size if Path(output_none).exists() else 0
print(f"Return code: {result_none.returncode}")
print(f"File size: {size_none} bytes")

print("\n" + "=" * 60)
print("Summary:")
print("=" * 60)
print(f"ASS filter:        {size_ass} bytes")
print(f"Subtitles filter:  {size_srt} bytes")
print(f"No subtitles:      {size_none} bytes")
print(f"\nDifference (ASS vs none):       {size_ass - size_none} bytes ({((size_ass - size_none) / max(size_none, 1) * 100):.1f}%)")
print(f"Difference (Subtitles vs none): {size_srt - size_none} bytes ({((size_srt - size_none) / max(size_none, 1) * 100):.1f}%)")

if abs(size_ass - size_none) > 1000:
    print("\nVERDICT: ASS filter produces different file - subtitles ARE working!")
elif abs(size_srt - size_none) > 1000:
    print("\nVERDICT: Subtitles filter produces different file - subtitles ARE working!")
else:
    print("\nVERDICT: All files same size - subtitles NOT working")

