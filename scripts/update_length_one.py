import os, sys
from datetime import datetime
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database import SessionLocal, Movie
from video_processing import get_video_length

PATH = r"D:\movies\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg)\Harold and Maude (1971) 720p BRrip_sujaidr (pimprg).mkv"

def main():
    length = get_video_length(PATH)
    print("LENGTH", length)
    if length is None:
        print("NO_UPDATE")
        return
    db = SessionLocal()
    try:
        m = db.query(Movie).filter(Movie.path == PATH).first()
        if not m:
            print("NOT_FOUND")
            return
        m.length = float(length)
        m.updated = datetime.now()
        db.commit()
        print("UPDATED")
    finally:
        db.close()

if __name__ == "__main__":
    main()

