import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from scanning import index_movie

if __name__ == "__main__":
    path = r"D:\movies\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg)\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg).mkv"
    ok = index_movie(path)
    print("INDEXED" if ok else "UNCHANGED")

