import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from video_processing import get_video_length

p = r"D:\movies\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg)\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg).mkv"
print("LEN", get_video_length(p))

