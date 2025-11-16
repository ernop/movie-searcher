import sys
from pathlib import Path

try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except Exception as e:
    print("HAS_MUTAGEN", False)
    print("MUTAGEN_IMPORT_ERROR", repr(e))
    sys.exit(0)

video_path = r"D:\movies\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg)\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg).mkv"
print("HAS_MUTAGEN", True)
print("PATH_EXISTS", Path(video_path).exists())
media = MutagenFile(video_path)
print("OBJ", type(media).__name__ if media else None)
length = getattr(getattr(media, "info", None), "length", None) if media else None
print("LENGTH", length)

