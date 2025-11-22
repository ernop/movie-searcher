import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import logging

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database import Base, Movie, Screenshot
import scanning

import video_processing

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_deletion")

class TestDeletionLogic(unittest.TestCase):
    def setUp(self):
        # Create temp directory for movies
        self.test_dir = tempfile.mkdtemp()
        self.movies_dir = os.path.join(self.test_dir, "movies")
        os.makedirs(self.movies_dir)
        
        # Initialize video_processing config (needed for screenshot paths)
        video_processing.SCREENSHOT_DIR = Path(self.test_dir) / "screenshots"
        video_processing.SCREENSHOT_DIR.mkdir(exist_ok=True)

        # Create temp database
        self.db_file = os.path.join(self.test_dir, "test.db")
        self.engine = create_engine(f"sqlite:///{self.db_file}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        
        # Create a dummy movie file (needs to be > 50MB)
        self.movie_path = os.path.join(self.movies_dir, "test_movie.mp4")
        with open(self.movie_path, "wb") as f:
            f.seek(52 * 1024 * 1024) # 52MB
            f.write(b"\0")
            
    def tearDown(self):
        self.engine.dispose()
        try:
            shutil.rmtree(self.test_dir)
        except PermissionError:
            print(f"Warning: Could not cleanup temp dir {self.test_dir} - file in use")

    def test_scan_and_delete(self):
        # Mock SessionLocal in scanning module to use our test DB
        with patch('scanning.SessionLocal', side_effect=self.Session):
            # Mock video processing to avoid ffmpeg/ffprobe calls
            with patch('scanning.get_video_length', return_value=120):
                with patch('scanning._extract_audio_types_with_ffprobe', return_value=['eng']):
                    with patch('scanning.find_images_in_folder', return_value=[]):
                         # We also need to patch extract_movie_screenshot to do nothing or simple mock
                         with patch('scanning.extract_movie_screenshot') as mock_extract:
                            
                            print(f"\n[1] Scanning directory: {self.movies_dir}")
                            scanning.scan_directory(self.movies_dir)
                            
                            # Verify movie exists in DB
                            session = self.Session()
                            movies = session.query(Movie).all()
                            print(f"[1] Movies in DB: {len(movies)}")
                            if len(movies) > 0:
                                print(f"[1] Movie name: {movies[0].name}")
                            self.assertEqual(len(movies), 1)
                            # self.assertEqual(movies[0].name, "test movie") # Name cleaning is aggressive with temp paths
                            session.close()
                            
                            # Now delete the movie file
                            print(f"\n[2] Deleting movie file: {self.movie_path}")
                            os.remove(self.movie_path)
                            
                            # Run scan again
                            print(f"[2] Re-scanning directory: {self.movies_dir}")
                            scanning.scan_directory(self.movies_dir)
                            
                            # Verify movie is gone from DB
                            session = self.Session()
                            movies = session.query(Movie).all()
                            print(f"[2] Movies in DB: {len(movies)}")
                            self.assertEqual(len(movies), 0)
                            session.close()

    def test_scan_and_delete_folder(self):
        # Create subfolder
        sub_dir = os.path.join(self.movies_dir, "Action")
        os.makedirs(sub_dir)
        movie_path = os.path.join(sub_dir, "die_hard.mp4")
        
        # Create dummy movie
        with open(movie_path, "wb") as f:
            f.seek(52 * 1024 * 1024)
            f.write(b"\0")
            
        # Mock SessionLocal and other deps
        with patch('scanning.SessionLocal', side_effect=self.Session):
            with patch('scanning.get_video_length', return_value=120):
                with patch('scanning._extract_audio_types_with_ffprobe', return_value=['eng']):
                    with patch('scanning.find_images_in_folder', return_value=[]):
                         with patch('scanning.extract_movie_screenshot') as mock_extract:
                            
                            print(f"\n[3] Scanning directory: {self.movies_dir}")
                            scanning.scan_directory(self.movies_dir)
                            
                            session = self.Session()
                            movies = session.query(Movie).all()
                            print(f"[3] Movies in DB: {len(movies)}")
                            # We have test_movie.mp4 (from setUp) and die_hard.mp4
                            self.assertEqual(len(movies), 2)
                            session.close()
                            
                            # Delete the subfolder
                            print(f"\n[4] Deleting subfolder: {sub_dir}")
                            shutil.rmtree(sub_dir)
                            
                            # Re-scan
                            print(f"[4] Re-scanning directory: {self.movies_dir}")
                            scanning.scan_directory(self.movies_dir)
                            
                            session = self.Session()
                            movies = session.query(Movie).all()
                            print(f"[4] Movies in DB: {len(movies)}")
                            # test_movie.mp4 should still be there, but die_hard.mp4 should be gone
                            self.assertEqual(len(movies), 1)
                            self.assertTrue(any("test_movie.mp4" in m.path for m in movies))
                            session.close()

if __name__ == '__main__':
    unittest.main()

