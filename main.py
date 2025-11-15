from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime
import hashlib
import logging
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('movie_searcher.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# Database setup
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, Text, Index
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.sql import func

Base = declarative_base()

class Movie(Base):
    __tablename__ = "movies"
    
    path = Column(String, primary_key=True)
    name = Column(String, nullable=False, index=True)
    length = Column(Float, nullable=True)
    created = Column(String, nullable=True)
    size = Column(Integer, nullable=True)
    hash = Column(String, nullable=True, index=True)
    images = Column(Text, nullable=True)  # JSON array as string
    screenshots = Column(Text, nullable=True)  # JSON array as string
    indexed_at = Column(DateTime, default=func.now(), onupdate=func.now())

class Rating(Base):
    __tablename__ = "ratings"
    
    movie_id = Column(String, primary_key=True)  # Foreign key to Movie.path
    rating = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class WatchHistory(Base):
    __tablename__ = "watch_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    movie_id = Column(String, nullable=False, index=True)  # Foreign key to Movie.path
    watch_status = Column(String, nullable=False)  # e.g., "watched", "watching", "completed", "abandoned"
    timestamp = Column(DateTime, default=func.now(), index=True)

class SearchHistory(Base):
    __tablename__ = "search_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    query = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, default=func.now(), index=True)
    results_count = Column(Integer, nullable=True)

class LaunchHistory(Base):
    __tablename__ = "launch_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String, nullable=False, index=True)
    subtitle = Column(String, nullable=True)
    timestamp = Column(DateTime, default=func.now(), index=True)

class IndexedPath(Base):
    __tablename__ = "indexed_paths"
    
    path = Column(String, primary_key=True)
    indexed_at = Column(DateTime, default=func.now())

class Config(Base):
    __tablename__ = "config"
    
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)

class MovieFrame(Base):
    __tablename__ = "movie_frames"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    movie_id = Column(String, nullable=False, index=True)  # Foreign key to Movie.path
    path = Column(String, nullable=False)  # Path to the extracted frame image
    created_at = Column(DateTime, default=func.now())

# Indexes are defined on columns directly (name and path already have indexes)
# Additional indexes can be added via migration if needed

app = FastAPI(title="Movie Searcher")

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()
DB_FILE = SCRIPT_DIR / "movie_searcher.db"
# Keep JSON files for migration/backup
STATE_FILE = SCRIPT_DIR / "movie_index.json"
HISTORY_FILE = SCRIPT_DIR / "search_history.json"
WATCHED_FILE = SCRIPT_DIR / "watched_movies.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"

# Database engine and session
engine = create_engine(f"sqlite:///{DB_FILE}", echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized")

def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp'}
SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.vtt', '.ass', '.ssa'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"
FRAMES_DIR = SCRIPT_DIR / "frames"

def migrate_json_to_db():
    """Migrate data from JSON files to database if JSON exists and DB is empty"""
    db = SessionLocal()
    try:
        # Check if database has any movies
        movie_count = db.query(Movie).count()
        if movie_count > 0:
            logger.info("Database already has data, skipping migration")
            return False
        
        # Migrate movies
        if STATE_FILE.exists():
            logger.info("Migrating movies from JSON to database...")
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                movies = data.get("movies", {})
                for path, info in movies.items():
                    movie = Movie(
                        path=path,
                        name=info.get("name", ""),
                        length=info.get("length"),
                        created=info.get("created"),
                        size=info.get("size"),
                        hash=info.get("hash"),
                        images=json.dumps(info.get("images", [])),
                        screenshots=json.dumps(info.get("screenshots", []))
                    )
                    db.merge(movie)
                
                # Migrate indexed paths
                indexed_paths = data.get("indexed_paths", [])
                for path in indexed_paths:
                    indexed_path = IndexedPath(path=path)
                    db.merge(indexed_path)
            
            logger.info(f"Migrated {len(movies)} movies to database")
        
        # Migrate watched movies
        if WATCHED_FILE.exists():
            logger.info("Migrating watched movies from JSON to database...")
            with open(WATCHED_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                watched_paths = data.get("watched", [])
                watched_dates = data.get("watched_dates", {})
                ratings = data.get("ratings", {})
                
                for path in watched_paths:
                    watched_date_str = watched_dates.get(path)
                    watched_date = None
                    if watched_date_str:
                        try:
                            watched_date = datetime.fromisoformat(watched_date_str)
                        except:
                            watched_date = datetime.now()
                    
                    # Create watch history entry
                    watch_entry = WatchHistory(
                        movie_id=path,
                        watch_status="watched",
                        timestamp=watched_date or datetime.now()
                    )
                    db.add(watch_entry)
                    
                    # Create rating entry if rating exists
                    if path in ratings and ratings[path] is not None:
                        rating_entry = Rating(
                            movie_id=path,
                            rating=ratings[path]
                        )
                        db.merge(rating_entry)
            
            logger.info(f"Migrated {len(watched_paths)} watched movies to database")
        
        # Migrate history
        if HISTORY_FILE.exists():
            logger.info("Migrating history from JSON to database...")
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Migrate searches
                searches = data.get("searches", [])
                for search in searches[-100:]:  # Keep last 100
                    search_entry = SearchHistory(
                        query=search.get("query", ""),
                        timestamp=datetime.fromisoformat(search.get("timestamp", datetime.now().isoformat())),
                        results_count=search.get("results_count")
                    )
                    db.add(search_entry)
                
                # Migrate launches
                launches = data.get("launches", [])
                for launch in launches:
                    launch_entry = LaunchHistory(
                        path=launch.get("path", ""),
                        subtitle=launch.get("subtitle"),
                        timestamp=datetime.fromisoformat(launch.get("timestamp", datetime.now().isoformat()))
                    )
                    db.add(launch_entry)
            
            logger.info("Migrated history to database")
        
        db.commit()
        logger.info("Migration completed successfully")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Migration error: {e}")
        return False
    finally:
        db.close()

def load_config():
    """Load configuration from database, fallback to JSON"""
    db = SessionLocal()
    try:
        config = {}
        try:
            config_rows = db.query(Config).all()
            for row in config_rows:
                # Try to parse as JSON, fallback to string
                try:
                    config[row.key] = json.loads(row.value)
                except:
                    config[row.key] = row.value
        except Exception as e:
            # Database tables not initialized yet, fall back to JSON file
            logger.debug(f"Database not initialized yet, using JSON fallback: {e}")
            pass
        
        # If no config in DB, try JSON file
        if not config and CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Try to migrate to DB (only if tables exist)
                    try:
                        for key, value in config.items():
                            value_str = json.dumps(value) if not isinstance(value, str) else value
                            db.merge(Config(key=key, value=value_str))
                        db.commit()
                    except Exception:
                        # Tables don't exist yet, will migrate later in startup_event
                        pass
            except:
                pass
        
        return config
    finally:
        db.close()

def save_config(config):
    """Save configuration to database"""
    db = SessionLocal()
    try:
        for key, value in config.items():
            value_str = json.dumps(value) if not isinstance(value, str) else value
            db.merge(Config(key=key, value=value_str))
        db.commit()
    finally:
        db.close()

def get_movies_folder():
    """Get the movies folder path, checking config, env, then default"""
    config = load_config()
    logger.info(f"get_movies_folder called. Config: {config}")
    
    # Check config file first
    if config.get("movies_folder"):
        path = config["movies_folder"]
        logger.info(f"Found config path: '{path}'")
        path_obj = Path(path)
        if path_obj.exists() and path_obj.is_dir():
            logger.info(f"Config path exists and is directory: {path}")
            return path
        else:
            logger.warning(f"Config path does not exist or is not a directory: {path}")
    
    # Check environment variable
    env_path = os.environ.get("MOVIE_ROOT_PATH", "")
    if env_path:
        logger.info(f"Found env path: '{env_path}'")
        path_obj = Path(env_path)
        if path_obj.exists() and path_obj.is_dir():
            logger.info(f"Env path exists and is directory: {env_path}")
            return env_path
        else:
            logger.warning(f"Env path does not exist or is not a directory: {env_path}")
    
    # Default: look for "movies" folder in same directory as script
    movies_folder = SCRIPT_DIR / "movies"
    logger.info(f"Checking default folder: {movies_folder}")
    if movies_folder.exists() and movies_folder.is_dir():
        logger.info(f"Default folder exists: {movies_folder}")
        return str(movies_folder)
    else:
        logger.info(f"Default folder does not exist: {movies_folder}")
    
    logger.info("No movies folder found")
    return None

# Get initial movies folder path
ROOT_MOVIE_PATH = get_movies_folder()

# Scan progress tracking (in-memory)
scan_progress = {
    "is_scanning": False,
    "current": 0,
    "total": 0,
    "current_file": "",
    "status": "idle",
    "logs": [],  # List of log entries: {"timestamp": str, "level": str, "message": str}
    "frame_queue_size": 0,
    "frames_processed": 0,
    "frames_total": 0
}

# Frame extraction queue and executor
frame_extraction_queue = Queue()
frame_executor = None
frame_processing_active = False

def add_scan_log(level: str, message: str):
    """Add a log entry to scan progress"""
    global scan_progress
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "level": level,  # "info", "success", "warning", "error"
        "message": message
    }
    scan_progress["logs"].append(log_entry)
    # Keep only last 1000 log entries to prevent memory issues
    if len(scan_progress["logs"]) > 1000:
        scan_progress["logs"] = scan_progress["logs"][-1000:]

class MovieInfo(BaseModel):
    path: str
    name: str
    length: Optional[float] = None
    created: Optional[str] = None
    size: Optional[int] = None

class SearchRequest(BaseModel):
    query: str

class LaunchRequest(BaseModel):
    path: str
    subtitle_path: Optional[str] = None
    close_existing_vlc: bool = True

class WatchedRequest(BaseModel):
    path: str
    watched: bool
    rating: Optional[float] = None

class ConfigRequest(BaseModel):
    movies_folder: str

# Database state functions
def get_movies_dict(db: Session):
    """Get all movies as a dictionary (for backward compatibility)"""
    movies = {}
    for movie in db.query(Movie).all():
        movies[movie.path] = {
            "name": movie.name,
            "length": movie.length,
            "created": movie.created,
            "size": movie.size,
            "hash": movie.hash,
            "images": json.loads(movie.images) if movie.images else [],
            "screenshots": json.loads(movie.screenshots) if movie.screenshots else []
        }
    return movies

def get_indexed_paths_set(db: Session):
    """Get all indexed paths as a set"""
    paths = set()
    for indexed_path in db.query(IndexedPath).all():
        paths.add(indexed_path.path)
    return paths

def load_state():
    """Load state from database (returns dict for backward compatibility)"""
    db = SessionLocal()
    try:
        return {
            "movies": get_movies_dict(db),
            "indexed_paths": get_indexed_paths_set(db)
        }
    finally:
        db.close()

def save_state(state):
    """Save state to database"""
    db = SessionLocal()
    try:
        # Update movies
        for path, info in state.get("movies", {}).items():
            movie = Movie(
                path=path,
                name=info.get("name", ""),
                length=info.get("length"),
                created=info.get("created"),
                size=info.get("size"),
                hash=info.get("hash"),
                images=json.dumps(info.get("images", [])),
                screenshots=json.dumps(info.get("screenshots", []))
            )
            db.merge(movie)
        
        # Update indexed paths
        indexed_paths = state.get("indexed_paths", set())
        for path in indexed_paths:
            db.merge(IndexedPath(path=path))
        
        db.commit()
    finally:
        db.close()

def load_watched():
    """Load watched movies from database (returns dict for backward compatibility)"""
    db = SessionLocal()
    try:
        watched = []
        watched_dates = {}
        ratings = {}
        
        # Get all movies with "watched" status in watch_history
        watch_entries = db.query(WatchHistory).filter(
            WatchHistory.watch_status == "watched"
        ).all()
        
        # Get most recent watch entry per movie
        watched_paths_set = set()
        for entry in watch_entries:
            if entry.movie_id not in watched_paths_set:
                watched.append(entry.movie_id)
                watched_dates[entry.movie_id] = entry.timestamp.isoformat()
                watched_paths_set.add(entry.movie_id)
        
        # Get ratings
        for rating in db.query(Rating).all():
            ratings[rating.movie_id] = rating.rating
        
        return {
            "watched": watched,
            "watched_dates": watched_dates,
            "ratings": ratings
        }
    finally:
        db.close()

def save_watched(watched_data):
    """Save watched movies to database (for backward compatibility - not used in new structure)"""
    # This function is kept for backward compatibility but new code should use
    # the normalized Rating and WatchHistory tables directly
    db = SessionLocal()
    try:
        watched_paths = set(watched_data.get("watched", []))
        watched_dates = watched_data.get("watched_dates", {})
        ratings = watched_data.get("ratings", {})
        
        # Get current watched movies (movies with "watched" status)
        current_watched = set()
        for entry in db.query(WatchHistory).filter(WatchHistory.watch_status == "watched").all():
            current_watched.add(entry.movie_id)
        
        # Remove unwatched movies (delete watch history entries)
        for path in current_watched - watched_paths:
            db.query(WatchHistory).filter(
                WatchHistory.movie_id == path,
                WatchHistory.watch_status == "watched"
            ).delete()
            db.query(Rating).filter(Rating.movie_id == path).delete()
        
        # Add/update watched movies
        for path in watched_paths:
            watched_date_str = watched_dates.get(path)
            watched_date = None
            if watched_date_str:
                try:
                    watched_date = datetime.fromisoformat(watched_date_str)
                except:
                    watched_date = datetime.now()
            
            # Create watch history entry
            watch_entry = WatchHistory(
                movie_id=path,
                watch_status="watched",
                timestamp=watched_date or datetime.now()
            )
            db.add(watch_entry)
            
            # Update rating if provided
            if path in ratings and ratings[path] is not None:
                rating_entry = Rating(
                    movie_id=path,
                    rating=ratings[path]
                )
                db.merge(rating_entry)
        
        db.commit()
    finally:
        db.close()

def load_history():
    """Load history from database (returns dict for backward compatibility)"""
    db = SessionLocal()
    try:
        searches = []
        for search in db.query(SearchHistory).order_by(SearchHistory.timestamp.desc()).limit(100).all():
            searches.append({
                "query": search.query,
                "timestamp": search.timestamp.isoformat(),
                "results_count": search.results_count
            })
        
        launches = []
        for launch in db.query(LaunchHistory).order_by(LaunchHistory.timestamp.desc()).all():
            launches.append({
                "path": launch.path,
                "subtitle": launch.subtitle,
                "timestamp": launch.timestamp.isoformat()
            })
        
        return {
            "searches": searches,
            "launches": launches
        }
    finally:
        db.close()

def save_history(history):
    """Save history to database"""
    db = SessionLocal()
    try:
        # Save searches (keep last 100)
        searches = history.get("searches", [])
        for search in searches[-100:]:
            search_entry = SearchHistory(
                query=search.get("query", ""),
                timestamp=datetime.fromisoformat(search.get("timestamp", datetime.now().isoformat())),
                results_count=search.get("results_count")
            )
            db.add(search_entry)
        
        # Save launches
        launches = history.get("launches", [])
        for launch in launches:
            launch_entry = LaunchHistory(
                path=launch.get("path", ""),
                subtitle=launch.get("subtitle"),
                timestamp=datetime.fromisoformat(launch.get("timestamp", datetime.now().isoformat()))
            )
            db.add(launch_entry)
        
        db.commit()
    finally:
        db.close()

def has_been_launched(movie_path):
    """Check if a movie has ever been launched"""
    db = SessionLocal()
    try:
        count = db.query(LaunchHistory).filter(LaunchHistory.path == movie_path).count()
        return count > 0
    finally:
        db.close()

def get_video_length(file_path):
    """Extract video length using mutagen if available, otherwise return None"""
    if not HAS_MUTAGEN:
        return None
    
    try:
        audio = MutagenFile(file_path)
        if audio is not None and hasattr(audio, 'info'):
            length = getattr(audio.info, 'length', None)
            return length
    except:
        pass
    return None

def get_file_hash(file_path):
    """Generate hash for file to detect changes"""
    stat = os.stat(file_path)
    return hashlib.md5(f"{file_path}:{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()

def find_images_in_folder(video_path):
    """Find image files in the same folder as the video"""
    video_path_obj = Path(video_path)
    video_dir = video_path_obj.parent
    base_name = video_path_obj.stem
    
    images = []
    for ext in IMAGE_EXTENSIONS:
        # Check for exact match
        img_path = video_dir / f"{base_name}{ext}"
        if img_path.exists():
            images.append(str(img_path))
        
        # Check for common patterns (poster, cover, etc.)
        for pattern in [f"{base_name}_poster{ext}", f"{base_name}_cover{ext}", f"{base_name}_thumb{ext}",
                        f"poster{ext}", f"cover{ext}", f"folder{ext}", f"thumb{ext}"]:
            img_path = video_dir / pattern
            if img_path.exists() and str(img_path) not in images:
                images.append(str(img_path))
    
    # Also check for any images in the folder (limit to first 10)
    for img_file in video_dir.iterdir():
        if img_file.suffix.lower() in IMAGE_EXTENSIONS and str(img_file) not in images:
            images.append(str(img_file))
            if len(images) >= 10:
                break
    
    return images[:10]  # Limit to 10 images

def find_ffmpeg():
    """Find ffmpeg executable"""
    ffmpeg_paths = [
        "ffmpeg",  # In PATH
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ]
    
    for path in ffmpeg_paths:
        if path == "ffmpeg":
            # Check if ffmpeg is in PATH
            try:
                result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=2)
                if result.returncode == 0:
                    return "ffmpeg"
            except:
                pass
        elif os.path.exists(path):
            return path
    
    return None

def generate_frame_filename(video_path, timestamp_seconds):
    """Generate a sensible frame filename based on movie name and timestamp"""
    video_path_obj = Path(video_path)
    movie_name = video_path_obj.stem  # Get filename without extension
    
    # Sanitize filename: remove invalid characters for Windows/Linux
    import re
    # Replace invalid filename characters with underscore
    sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', movie_name)
    # Remove leading/trailing dots and spaces
    sanitized_name = sanitized_name.strip('. ')
    # Limit length to avoid filesystem issues
    if len(sanitized_name) > 100:
        sanitized_name = sanitized_name[:100]
    
    # Format: movie_name_frame150s.jpg
    frame_filename = f"{sanitized_name}_frame{int(timestamp_seconds)}s.jpg"
    return FRAMES_DIR / frame_filename

def extract_movie_frame_sync(video_path, timestamp_seconds=150):
    """Extract a single frame from video synchronously (blocking)"""
    video_path_obj = Path(video_path)
    
    # Create frames directory if it doesn't exist
    FRAMES_DIR.mkdir(exist_ok=True)
    
    # Generate frame filename based on movie name and timestamp
    frame_path = generate_frame_filename(video_path, timestamp_seconds)
    
    # Check if frame already exists
    if frame_path.exists():
        return str(frame_path)
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        logger.warning(f"ffmpeg not found, skipping frame extraction for {video_path}")
        return None
    
    # Try to get video length to validate timestamp
    length = get_video_length(video_path)
    if length and timestamp_seconds > length:
        # If requested timestamp is beyond video length, use 30 seconds or 10% into the video, whichever is smaller
        timestamp_seconds = min(30, max(10, length * 0.1))
        logger.info(f"Timestamp exceeds video length {length}s, using {timestamp_seconds}s instead")
    
    # Extract frame
    try:
        cmd = [
            ffmpeg_exe,
            "-i", str(video_path),
            "-ss", str(timestamp_seconds),
            "-vframes", "1",
            "-q:v", "2",  # High quality
            "-y",  # Overwrite
            str(frame_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and frame_path.exists():
            logger.info(f"Extracted frame from {video_path} at {timestamp_seconds}s")
            return str(frame_path)
        else:
            error_msg = result.stderr.decode() if result.stderr else 'Unknown error'
            logger.warning(f"Failed to extract frame from {video_path}: {error_msg}")
            return None
    except subprocess.TimeoutExpired:
        logger.warning(f"Frame extraction timed out for {video_path}")
        return None
    except Exception as e:
        logger.error(f"Error extracting frame from {video_path}: {e}")
        return None

def extract_movie_frame(video_path, timestamp_seconds=150, async_mode=True):
    """Extract a single frame from video - can be synchronous or queued for async processing"""
    video_path_obj = Path(video_path)
    
    # Create frames directory if it doesn't exist
    FRAMES_DIR.mkdir(exist_ok=True)
    
    # Generate frame filename based on movie name and timestamp
    frame_path = generate_frame_filename(video_path, timestamp_seconds)
    
    # Check if frame already exists
    if frame_path.exists():
        add_scan_log("info", f"Frame already exists: {frame_path.name}")
        return str(frame_path)
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        add_scan_log("warning", f"ffmpeg not found, skipping frame extraction")
        logger.warning(f"ffmpeg not found, skipping frame extraction for {video_path}")
        return None
    
    # If async mode, queue it for background processing
    if async_mode:
        global frame_extraction_queue, scan_progress
        frame_extraction_queue.put({
            "video_path": video_path,
            "timestamp_seconds": timestamp_seconds,
            "ffmpeg_exe": ffmpeg_exe
        })
        scan_progress["frame_queue_size"] = frame_extraction_queue.qsize()
        scan_progress["frames_total"] = scan_progress.get("frames_total", 0) + 1
        add_scan_log("info", f"Queued frame extraction (queue: {frame_extraction_queue.qsize()})")
        return None  # Return None to indicate it's queued, will be processed later
    else:
        # Synchronous mode (for backwards compatibility)
        return extract_movie_frame_sync(video_path, timestamp_seconds)

def process_frame_extraction_worker(frame_info):
    """Worker function to extract a frame - runs in thread pool"""
    try:
        video_path = frame_info["video_path"]
        timestamp_seconds = frame_info["timestamp_seconds"]
        ffmpeg_exe = frame_info["ffmpeg_exe"]
        
        # Try to get video length to validate timestamp
        length = get_video_length(video_path)
        if length and timestamp_seconds > length:
            timestamp_seconds = min(30, max(10, length * 0.1))
        
        # Regenerate frame path with potentially adjusted timestamp
        frame_path = generate_frame_filename(video_path, timestamp_seconds)
        
        add_scan_log("info", f"Extracting frame: {Path(video_path).name} at {timestamp_seconds:.1f}s...")
        
        cmd = [
            ffmpeg_exe,
            "-i", str(video_path),
            "-ss", str(timestamp_seconds),
            "-vframes", "1",
            "-q:v", "2",
            "-y",
            str(frame_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and Path(frame_path).exists():
            # Save to database
            db = SessionLocal()
            try:
                # Check if entry already exists
                existing = db.query(MovieFrame).filter(MovieFrame.movie_id == video_path).first()
                if not existing:
                    movie_frame = MovieFrame(
                        movie_id=video_path,
                        path=frame_path
                    )
                    db.add(movie_frame)
                    db.commit()
                
                global scan_progress
                scan_progress["frames_processed"] = scan_progress.get("frames_processed", 0) + 1
                scan_progress["frame_queue_size"] = frame_extraction_queue.qsize()
                add_scan_log("success", f"Frame extracted: {Path(video_path).name}")
                logger.info(f"Extracted frame from {video_path}")
            finally:
                db.close()
            return True
        else:
            error_msg = result.stderr.decode() if result.stderr else 'Unknown error'
            add_scan_log("error", f"Frame extraction failed: {Path(video_path).name} - {error_msg[:80]}")
            logger.warning(f"Failed to extract frame from {video_path}: {error_msg}")
            return False
    except subprocess.TimeoutExpired:
        add_scan_log("error", f"Frame extraction timed out: {Path(video_path).name}")
        logger.warning(f"Frame extraction timed out for {video_path}")
        return False
    except Exception as e:
        add_scan_log("error", f"Frame extraction error: {Path(video_path).name} - {str(e)[:80]}")
        logger.error(f"Error extracting frame from {video_path}: {e}")
        return False

def process_frame_queue(max_workers=3):
    """Process queued frame extractions in background thread pool"""
    global frame_executor, frame_processing_active, frame_extraction_queue
    
    if frame_processing_active:
        return
    
    frame_processing_active = True
    add_scan_log("info", "Starting background frame extraction...")
    
    def worker():
        global frame_executor
        frame_executor = ThreadPoolExecutor(max_workers=max_workers)
        
        # Continue processing while queue has items or scan is still running
        processed_count = 0
        while True:
            try:
                # Get frame info from queue (with timeout to periodically check scan status)
                try:
                    frame_info = frame_extraction_queue.get(timeout=2)
                except:
                    # Queue empty, check if scan is done and queue is truly empty
                    if not scan_progress.get("is_scanning", False) and frame_extraction_queue.empty():
                        break
                    continue
                
                # Submit to thread pool (non-blocking)
                future = frame_executor.submit(process_frame_extraction_worker, frame_info)
                processed_count += 1
                frame_extraction_queue.task_done()
                
                # Don't wait for result here - let it run in parallel
                # Just track that we submitted it
                
            except Exception as e:
                logger.error(f"Error in frame extraction worker: {e}")
        
        # Give some time for all tasks to complete
        import time
        time.sleep(1)
        
        # Shutdown executor (will wait for all tasks)
        if frame_executor:
            frame_executor.shutdown(wait=True)
        
        global frame_processing_active
        frame_processing_active = False
        remaining = frame_extraction_queue.qsize()
        if remaining == 0:
            add_scan_log("success", f"All frame extractions completed ({processed_count} processed)")
        else:
            add_scan_log("warning", f"Frame extraction stopped with {remaining} items remaining")
    
    # Start worker thread
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

def extract_screenshots(video_path, num_screenshots=5):
    """Extract screenshots from video using ffmpeg"""
    video_path_obj = Path(video_path)
    
    # Create screenshots directory if it doesn't exist
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    
    # Generate screenshot filename based on video hash
    video_hash = hashlib.md5(str(video_path).encode()).hexdigest()[:8]
    screenshot_base = SCREENSHOT_DIR / f"{video_hash}"
    
    screenshots = []
    
    # Check if screenshots already exist
    existing_screenshots = []
    for i in range(num_screenshots):
        screenshot_path = screenshot_base.parent / f"{screenshot_base.name}_{i+1}.jpg"
        if screenshot_path.exists():
            existing_screenshots.append(str(screenshot_path))
    
    if len(existing_screenshots) == num_screenshots:
        return existing_screenshots
    
    # Try to get video length
    length = get_video_length(video_path)
    if not length or length < 1:
        return existing_screenshots if existing_screenshots else []
    
    # Find ffmpeg
    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        logger.warning(f"ffmpeg not found, skipping screenshot extraction for {video_path}")
        return existing_screenshots if existing_screenshots else []
    
    # Extract screenshots at evenly spaced intervals
    try:
        for i in range(num_screenshots):
            screenshot_path = screenshot_base.parent / f"{screenshot_base.name}_{i+1}.jpg"
            if screenshot_path.exists():
                screenshots.append(str(screenshot_path))
                continue
            
            # Calculate timestamp (distribute evenly across video)
            timestamp = (length / (num_screenshots + 1)) * (i + 1)
            
            # Extract frame
            cmd = [
                ffmpeg_exe,
                "-i", str(video_path),
                "-ss", str(timestamp),
                "-vframes", "1",
                "-q:v", "2",  # High quality
                "-y",  # Overwrite
                str(screenshot_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0 and screenshot_path.exists():
                screenshots.append(str(screenshot_path))
            else:
                logger.warning(f"Failed to extract screenshot {i+1} from {video_path}")
    except subprocess.TimeoutExpired:
        logger.warning(f"Screenshot extraction timed out for {video_path}")
    except Exception as e:
        logger.error(f"Error extracting screenshots from {video_path}: {e}")
    
    return screenshots

def index_movie(file_path, db: Session = None):
    """Index a single movie file"""
    # Normalize the path to ensure consistent storage
    # file_path can be either a Path object or a string
    if isinstance(file_path, Path):
        path_obj = file_path
    else:
        path_obj = Path(file_path)
    
    # Use resolve() to get absolute normalized path
    try:
        normalized_path_obj = path_obj.resolve()
    except (OSError, RuntimeError):
        # If resolve fails, use absolute()
        normalized_path_obj = path_obj.absolute()
    
    # Convert to string - Path objects on Windows already use backslashes
    normalized_path = str(normalized_path_obj)
    
    file_hash = get_file_hash(normalized_path)
    
    # Use provided session or create new one
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        # Check if already indexed and unchanged
        existing = db.query(Movie).filter(Movie.path == normalized_path).first()
        file_unchanged = existing and existing.hash == file_hash
        
        # Check if frame exists for this movie
        existing_frame = db.query(MovieFrame).filter(MovieFrame.movie_id == normalized_path).first()
        has_frame = existing_frame and os.path.exists(existing_frame.path) if existing_frame else False
        
        # If file unchanged and frame exists, no update needed
        if file_unchanged and has_frame:
            return False  # No update needed
        
        add_scan_log("info", f"  Getting file metadata...")
        
        stat = os.stat(normalized_path)
        created = datetime.fromtimestamp(stat.st_ctime).isoformat()
        size = stat.st_size
        
        # Try to get video length
        length = get_video_length(normalized_path)
        
        # Find images in folder
        add_scan_log("info", f"  Searching for images in folder...")
        images = find_images_in_folder(normalized_path)
        if images:
            add_scan_log("success", f"  Found {len(images)} image(s)")
        
        # Extract screenshots (only if no images found or screenshots missing)
        screenshots = []
        if existing and existing.screenshots:
            screenshots = json.loads(existing.screenshots) if existing.screenshots else []
            add_scan_log("info", f"  Using existing screenshots")
        elif len(images) == 0 or not (existing and existing.screenshots):
            add_scan_log("info", f"  Extracting screenshots...")
            screenshots = extract_screenshots(normalized_path, num_screenshots=5)
            if screenshots:
                add_scan_log("success", f"  Extracted {len(screenshots)} screenshot(s)")
        
        # Extract movie frame (at 2-3 minutes, default 2.5 minutes = 150 seconds)
        add_scan_log("info", f"  Checking frame...")
        frame_path = None
        if existing_frame:
            # Check if the frame file still exists
            if os.path.exists(existing_frame.path):
                frame_path = existing_frame.path
                add_scan_log("info", f"  Frame already exists")
            else:
                # Frame file was deleted, remove from DB and queue for re-extraction
                add_scan_log("warning", f"  Frame file missing, queuing re-extraction...")
                db.delete(existing_frame)
                extract_movie_frame(normalized_path, timestamp_seconds=150, async_mode=True)
        else:
            # No frame exists, queue for extraction (even if file unchanged)
            add_scan_log("info", f"  No frame found, queuing extraction...")
            extract_movie_frame(normalized_path, timestamp_seconds=150, async_mode=True)
        
        # Create or update movie record
        movie = Movie(
            path=normalized_path,
            name=normalized_path_obj.stem,
            length=length,
            created=created,
            size=size,
            hash=file_hash,
            images=json.dumps(images),
            screenshots=json.dumps(screenshots)
        )
        db.merge(movie)
        db.commit()
        return True
    finally:
        if should_close:
            db.close()

def scan_directory(root_path, state=None, progress_callback=None):
    """Scan directory for video files with optional progress callback"""
    root = Path(root_path)
    if not root.exists():
        add_scan_log("error", f"Path does not exist: {root_path}")
        return {"indexed": 0, "updated": 0, "errors": []}
    
    add_scan_log("info", f"Starting scan of: {root_path}")
    db = SessionLocal()
    try:
        # First pass: count total files
        global scan_progress
        scan_progress["status"] = "counting"
        scan_progress["current_file"] = "Counting files..."
        add_scan_log("info", "Counting video files...")
        
        total_files = 0
        for ext in VIDEO_EXTENSIONS:
            count = len(list(root.rglob(f"*{ext}")))
            total_files += count
            if count > 0:
                add_scan_log("info", f"Found {count} {ext} files")
        
        scan_progress["total"] = total_files
        scan_progress["current"] = 0
        scan_progress["status"] = "scanning"
        add_scan_log("success", f"Total files to process: {total_files}")
        
        indexed = 0
        updated = 0
        errors = []
        
        # Second pass: actually scan
        add_scan_log("info", "Starting file processing...")
        for ext in VIDEO_EXTENSIONS:
            for file_path in root.rglob(f"*{ext}"):
                try:
                    scan_progress["current"] = indexed + 1
                    scan_progress["current_file"] = file_path.name
                    
                    add_scan_log("info", f"[{indexed + 1}/{total_files}] Processing: {file_path.name}")
                    
                    if index_movie(file_path, db):
                        updated += 1
                        add_scan_log("success", f"Indexed: {file_path.name}")
                    else:
                        add_scan_log("info", f"Skipped (unchanged): {file_path.name}")
                    indexed += 1
                    
                    if progress_callback:
                        progress_callback(indexed, total_files, file_path.name)
                except Exception as e:
                    errors.append(str(file_path))
                    error_msg = str(e)
                    add_scan_log("error", f"Error indexing {file_path.name}: {error_msg[:150]}")
                    logger.error(f"Error indexing {file_path}: {e}")
        
        # Mark path as indexed
        db.merge(IndexedPath(path=str(root_path)))
        db.commit()
        
        add_scan_log("success", f"Scan complete: {indexed} files processed, {updated} updated, {len(errors)} errors")
        
        return {"indexed": indexed, "updated": updated, "errors": errors}
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
async def read_root():
    html_path = SCRIPT_DIR / "index.html"
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/star-rating.js")
async def get_star_rating_js():
    """Serve the star rating JavaScript file"""
    js_path = SCRIPT_DIR / "star-rating.js"
    if js_path.exists():
        with open(js_path, "r", encoding="utf-8") as f:
            from fastapi.responses import Response
            return Response(content=f.read(), media_type="application/javascript")
    raise HTTPException(status_code=404, detail="star-rating.js not found")

def run_scan_async(root_path: str):
    """Run scan in background thread"""
    global scan_progress, frame_extraction_queue
    try:
        scan_progress["is_scanning"] = True
        scan_progress["current"] = 0
        scan_progress["total"] = 0
        scan_progress["current_file"] = ""
        scan_progress["status"] = "starting"
        scan_progress["logs"] = []  # Clear previous logs
        scan_progress["frames_processed"] = 0
        scan_progress["frames_total"] = 0
        
        # Clear frame queue
        while not frame_extraction_queue.empty():
            try:
                frame_extraction_queue.get_nowait()
            except:
                break
        
        add_scan_log("info", "=" * 60)
        add_scan_log("info", "Starting movie scan")
        add_scan_log("info", f"Root path: {root_path}")
        add_scan_log("info", "=" * 60)
        
        # Start frame extraction processing in parallel (if not already running)
        process_frame_queue(max_workers=3)
        
        result = scan_directory(root_path, progress_callback=None)
        
        add_scan_log("info", "=" * 60)
        add_scan_log("success", f"Scan completed successfully!")
        add_scan_log("info", f"  Files processed: {result['indexed']}")
        add_scan_log("info", f"  Files updated: {result['updated']}")
        if result['errors']:
            add_scan_log("warning", f"  Errors: {len(result['errors'])}")
        queue_size = frame_extraction_queue.qsize()
        if queue_size > 0:
            add_scan_log("info", f"  Frames queued: {queue_size} (processing in background)")
        add_scan_log("info", "=" * 60)
        
        scan_progress["status"] = "complete"
        scan_progress["is_scanning"] = False
        logger.info(f"Scan complete: {result}")
    except Exception as e:
        add_scan_log("error", f"Fatal scan error: {str(e)}")
        scan_progress["status"] = f"error: {str(e)}"
        scan_progress["is_scanning"] = False
        logger.error(f"Scan error: {e}")

@app.post("/api/index")
async def index_movies(root_path: str = Query(None)):
    """One-time deep index scan (runs in background)"""
    global scan_progress
    
    if scan_progress["is_scanning"]:
        raise HTTPException(status_code=400, detail="Scan already in progress")
    
    logger.info(f"index_movies called with root_path: {root_path}")
    
    if not root_path:
        root_path = get_movies_folder()
        logger.info(f"Got root_path from get_movies_folder: {root_path}")
    
    if not root_path:
        error_msg = "Movies folder not found. Please create a 'movies' folder in the same directory as this script, or use 'Change Movies Folder' to select one."
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    
    path_obj = Path(root_path)
    logger.info(f"Checking path: {root_path}")
    
    if not path_obj.exists() and not os.path.exists(root_path):
        error_msg = f"Path not found: {root_path}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    
    logger.info(f"Starting scan of: {root_path}")
    
    # Run scan in background
    import threading
    thread = threading.Thread(target=run_scan_async, args=(root_path,))
    thread.daemon = True
    thread.start()
    
    return {"status": "started", "message": "Scan started in background"}

@app.get("/api/scan-progress")
async def get_scan_progress():
    """Get current scan progress"""
    global scan_progress, frame_extraction_queue
    return {
        "is_scanning": scan_progress["is_scanning"],
        "current": scan_progress["current"],
        "total": scan_progress["total"],
        "current_file": scan_progress["current_file"],
        "status": scan_progress["status"],
        "progress_percent": (scan_progress["current"] / scan_progress["total"] * 100) if scan_progress["total"] > 0 else 0,
        "logs": scan_progress.get("logs", []),
        "frame_queue_size": frame_extraction_queue.qsize(),
        "frames_processed": scan_progress.get("frames_processed", 0),
        "frames_total": scan_progress.get("frames_total", 0)
    }

@app.post("/api/admin/reindex")
async def admin_reindex(root_path: str = Query(None)):
    """Admin endpoint to reindex - uses same code as frontend"""
    global scan_progress
    
    if scan_progress["is_scanning"]:
        raise HTTPException(status_code=400, detail="Scan already in progress")
    
    logger.info(f"admin_reindex called with root_path: {root_path}")
    
    if not root_path:
        root_path = get_movies_folder()
        logger.info(f"Got root_path from get_movies_folder: {root_path}")
    
    if not root_path:
        error_msg = "Movies folder not found. Please create a 'movies' folder in the same directory as this script, or use 'Change Movies Folder' to select one."
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    
    path_obj = Path(root_path)
    logger.info(f"Checking path: {root_path}")
    
    if not path_obj.exists() and not os.path.exists(root_path):
        error_msg = f"Path not found: {root_path}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    
    logger.info(f"Starting scan of: {root_path}")
    
    # Run scan in background (same as frontend)
    import threading
    thread = threading.Thread(target=run_scan_async, args=(root_path,))
    thread.daemon = True
    thread.start()
    
    return {"status": "started", "message": "Reindex started in background"}

@app.get("/api/search")
async def search_movies(q: str, filter_type: str = Query("all", regex="^(all|watched|unwatched)$")):
    """Search movies with autocomplete"""
    if not q or len(q) < 1:
        return {"results": []}
    
    db = SessionLocal()
    try:
        query_lower = q.lower()
        
        # Build query with search filter
        from sqlalchemy import or_
        movie_query = db.query(Movie).filter(
            func.lower(Movie.name).contains(query_lower)
        )
        
        # Get watched paths (movies with "watched" status in watch_history)
        watched_paths = set()
        watched_dict = {}
        # Get most recent "watched" entry per movie
        watch_entries = db.query(WatchHistory).filter(
            WatchHistory.watch_status == "watched"
        ).order_by(WatchHistory.timestamp.desc()).all()
        
        for entry in watch_entries:
            if entry.movie_id not in watched_paths:
                watched_paths.add(entry.movie_id)
                watched_dict[entry.movie_id] = {
                    "watched_date": entry.timestamp.isoformat() if entry.timestamp else None,
                    "rating": None
                }
        
        # Get ratings
        for rating in db.query(Rating).all():
            if rating.movie_id in watched_dict:
                watched_dict[rating.movie_id]["rating"] = rating.rating
        
        results = []
        for movie in movie_query.all():
            is_watched = movie.path in watched_paths
            
            # Apply watched/unwatched filter
            if filter_type == "watched" and not is_watched:
                continue
            if filter_type == "unwatched" and is_watched:
                continue
            
            name_lower = movie.name.lower()
            # Calculate match score (exact start = higher score)
            score = 100 if name_lower.startswith(query_lower) else 50
            
            # Parse images and screenshots
            images = json.loads(movie.images) if movie.images else []
            screenshots = json.loads(movie.screenshots) if movie.screenshots else []
            
            # Get frame path
            frame_path = get_movie_frame_path(db, movie.path)
            
            # Build info dict for get_largest_image (include frame)
            info = {
                "images": images,
                "screenshots": screenshots,
                "frame": frame_path
            }
            
            # Get largest image
            largest_image = get_largest_image(info)
            
            # Extract year from name
            year = extract_year_from_name(movie.name)
            
            # Check if launched
            has_launched = db.query(LaunchHistory).filter(LaunchHistory.path == movie.path).count() > 0
            
            results.append({
                "path": movie.path,
                "name": movie.name,
                "length": movie.length,
                "created": movie.created,
                "size": movie.size,
                "watched": is_watched,
                "watched_date": watched_dict.get(movie.path, {}).get("watched_date") if is_watched else None,
                "rating": watched_dict.get(movie.path, {}).get("rating") if is_watched else None,
                "score": score,
                "images": images,
                "screenshots": screenshots,
                "frame": frame_path,
                "image": largest_image,
                "year": year,
                "has_launched": has_launched
            })
        
        # Sort by score, then name
        results.sort(key=lambda x: (-x["score"], x["name"].lower()))
        
        # Save to history
        search_entry = SearchHistory(
            query=q,
            timestamp=datetime.now(),
            results_count=len(results)
        )
        db.add(search_entry)
        
        # Keep last 100 searches
        search_count = db.query(SearchHistory).count()
        if search_count > 100:
            oldest = db.query(SearchHistory).order_by(SearchHistory.timestamp.asc()).limit(search_count - 100).all()
            for old_search in oldest:
                db.delete(old_search)
        
        db.commit()
        
        return {"results": results[:50]}  # Limit to 50 results
    finally:
        db.close()

@app.get("/api/movie")
async def get_movie_details(path: str = Query(...)):
    """Get detailed information about a specific movie"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.path == path).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Check if watched (has "watched" status in watch_history)
        watch_entry = db.query(WatchHistory).filter(
            WatchHistory.movie_id == path,
            WatchHistory.watch_status == "watched"
        ).order_by(WatchHistory.timestamp.desc()).first()
        is_watched = watch_entry is not None
        
        # Get rating
        rating_entry = db.query(Rating).filter(Rating.movie_id == path).first()
        
        images = json.loads(movie.images) if movie.images else []
        screenshots = json.loads(movie.screenshots) if movie.screenshots else []
        
        # Get frame path
        frame_path = get_movie_frame_path(db, path)
        
        info = {
            "images": images,
            "screenshots": screenshots,
            "frame": frame_path
        }
        
        # Get largest image
        largest_image = get_largest_image(info)
        
        # Extract year from name
        year = extract_year_from_name(movie.name)
        
        has_launched = db.query(LaunchHistory).filter(LaunchHistory.path == path).count() > 0
        
        return {
            "path": movie.path,
            "name": movie.name,
            "length": movie.length,
            "created": movie.created,
            "size": movie.size,
            "watched": is_watched,
            "watched_date": watch_entry.timestamp.isoformat() if watch_entry and watch_entry.timestamp else None,
            "rating": rating_entry.rating if rating_entry else None,
            "images": images,
            "screenshots": screenshots,
            "frame": frame_path,
            "image": largest_image,
            "year": year,
            "has_launched": has_launched
        }
    finally:
        db.close()

@app.get("/api/image")
async def get_image(image_path: str):
    """Serve image files"""
    from fastapi.responses import FileResponse
    
    try:
        # Normalize path - handle both forward and backslashes on Windows
        # URL decode first in case it was encoded
        import urllib.parse
        decoded_path = urllib.parse.unquote(image_path)
        # Convert forward slashes to backslashes on Windows for path operations
        if os.name == 'nt':  # Windows
            normalized_path = decoded_path.replace('/', '\\')
        else:
            normalized_path = decoded_path.replace('\\', '/')
        
        path_obj = Path(normalized_path)
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail="Image not found")
        
        # Security: ensure path is within allowed directories
        movies_folder = get_movies_folder()
        if movies_folder:
            movies_path = Path(movies_folder)
            try:
                path_obj.resolve().relative_to(movies_path.resolve())
            except ValueError:
                # Also allow screenshots and frames directories
                try:
                    path_obj.resolve().relative_to(SCREENSHOT_DIR.resolve())
                except ValueError:
                    try:
                        path_obj.resolve().relative_to(FRAMES_DIR.resolve())
                    except ValueError:
                        raise HTTPException(status_code=403, detail="Access denied")
        
        return FileResponse(str(path_obj))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving image {image_path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def find_subtitle_file(video_path):
    """Find subtitle file for a video"""
    video_path_obj = Path(video_path)
    base_name = video_path_obj.stem
    
    # Check same directory first
    video_dir = video_path_obj.parent
    for ext in SUBTITLE_EXTENSIONS:
        subtitle_path = video_dir / f"{base_name}{ext}"
        if subtitle_path.exists():
            return str(subtitle_path)
    
    # Check for common subtitle naming patterns
    for ext in SUBTITLE_EXTENSIONS:
        for pattern in [f"{base_name}.en{ext}", f"{base_name}.eng{ext}", f"{base_name}_en{ext}"]:
            subtitle_path = video_dir / pattern
            if subtitle_path.exists():
                return str(subtitle_path)
    
    return None

@app.post("/api/launch")
async def launch_movie(request: LaunchRequest):
    """Launch movie in VLC with optional subtitle"""
    steps = []
    results = []
    
    # The path should always be in the index - use it directly
    # Paths are stored correctly in the index during scanning
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.path == request.path).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie not found in index: {request.path}")
    finally:
        db.close()
    
    movie_path = request.path
    
    # Step 1: Verify file exists
    steps.append("Step 1: Verifying movie file exists")
    if not os.path.exists(movie_path):
        error_msg = f"File not found: {movie_path} (original: {request.path})"
        steps.append(f"  ERROR: {error_msg}")
        results.append({"step": 1, "status": "error", "message": error_msg})
        # Return error with steps included
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "detail": error_msg,
                "steps": steps,
                "results": results
            }
        )
    results.append({"step": 1, "status": "success", "message": f"File found: {movie_path}"})
    steps.append(f"  SUCCESS: File exists at {movie_path}")
    
    try:
        # Step 2: Find VLC executable
        steps.append("Step 2: Locating VLC executable")
        vlc_paths = [
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\VideoLAN\vlc.exe"),
            "vlc"  # If in PATH
        ]
        
        vlc_exe = None
        checked_paths = []
        for path in vlc_paths:
            checked_paths.append(path)
            if path == "vlc":
                # Check if vlc is in PATH
                try:
                    result = subprocess.run(["vlc", "--version"], capture_output=True, timeout=2)
                    if result.returncode == 0:
                        vlc_exe = path
                        steps.append(f"  Found VLC in PATH")
                        break
                except:
                    steps.append(f"  Checked PATH: not found")
            elif os.path.exists(path):
                vlc_exe = path
                steps.append(f"  Found VLC at: {path}")
                break
            else:
                steps.append(f"  Checked: {path} (not found)")
        
        if not vlc_exe:
            error_msg = "VLC not found. Please install VLC or set path."
            steps.append(f"  ERROR: {error_msg}")
            steps.append(f"  Checked paths: {', '.join(checked_paths)}")
            results.append({"step": 2, "status": "error", "message": error_msg, "checked_paths": checked_paths})
            # Return error with steps included
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "detail": error_msg,
                    "steps": steps,
                    "results": results,
                    "checked_paths": checked_paths
                }
            )
        results.append({"step": 2, "status": "success", "message": f"VLC found at: {vlc_exe}"})
        
        # Step 2.5: Close existing VLC windows if requested
        if request.close_existing_vlc:
            steps.append("Step 2.5: Closing existing VLC windows")
            try:
                if os.name == 'nt':  # Windows
                    # Find all VLC processes
                    result = subprocess.run(
                        ["tasklist", "/FI", "IMAGENAME eq vlc.exe", "/FO", "CSV", "/NH"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        # Count processes
                        lines = [line for line in result.stdout.strip().split('\n') if line.strip()]
                        process_count = len(lines)
                        steps.append(f"  Found {process_count} existing VLC process(es)")
                        
                        # Close them
                        kill_result = subprocess.run(
                            ["taskkill", "/F", "/IM", "vlc.exe"],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if kill_result.returncode == 0:
                            steps.append(f"  Successfully closed {process_count} VLC process(es)")
                            results.append({"step": 2.5, "status": "success", "message": f"Closed {process_count} existing VLC process(es)"})
                        else:
                            steps.append(f"  WARNING: Failed to close some VLC processes: {kill_result.stderr}")
                            results.append({"step": 2.5, "status": "warning", "message": "Some VLC processes may not have closed"})
                    else:
                        steps.append("  No existing VLC processes found")
                        results.append({"step": 2.5, "status": "info", "message": "No existing VLC processes to close"})
                else:
                    # Linux/Mac - use pkill or killall
                    try:
                        result = subprocess.run(
                            ["pkill", "-f", "vlc"],
                            capture_output=True,
                            timeout=5
                        )
                        if result.returncode == 0:
                            steps.append("  Closed existing VLC processes")
                            results.append({"step": 2.5, "status": "success", "message": "Closed existing VLC processes"})
                        else:
                            steps.append("  No existing VLC processes found")
                            results.append({"step": 2.5, "status": "info", "message": "No existing VLC processes to close"})
                    except FileNotFoundError:
                        # Try killall as fallback
                        try:
                            subprocess.run(["killall", "vlc"], capture_output=True, timeout=5)
                            steps.append("  Closed existing VLC processes (using killall)")
                            results.append({"step": 2.5, "status": "success", "message": "Closed existing VLC processes"})
                        except:
                            steps.append("  WARNING: Could not close existing VLC processes (pkill/killall not available)")
                            results.append({"step": 2.5, "status": "warning", "message": "Could not close existing VLC processes"})
            except Exception as e:
                steps.append(f"  WARNING: Error closing existing VLC processes: {str(e)}")
                results.append({"step": 2.5, "status": "warning", "message": f"Error closing existing VLC: {str(e)}"})
        else:
            steps.append("Step 2.5: Skipping close existing VLC (option disabled)")
            results.append({"step": 2.5, "status": "info", "message": "Close existing VLC option disabled"})
        
        # Step 3: Build VLC command
        steps.append("Step 3: Building VLC command")
        vlc_cmd = [vlc_exe, movie_path]
        steps.append(f"  Base command: {vlc_exe} {movie_path}")
        results.append({"step": 3, "status": "success", "message": f"Command prepared: {vlc_exe}"})
        
        # Step 4: Handle subtitles
        steps.append("Step 4: Checking for subtitles")
        subtitle_path = request.subtitle_path
        if not subtitle_path:
            steps.append("  No subtitle provided, attempting auto-detection")
            subtitle_path = find_subtitle_file(movie_path)
            if subtitle_path:
                steps.append(f"  Auto-detected subtitle: {subtitle_path}")
            else:
                steps.append("  No subtitle file found")
        else:
            steps.append(f"  Subtitle provided: {subtitle_path}")
        
        if subtitle_path and os.path.exists(subtitle_path):
            vlc_cmd.extend(["--sub-file", subtitle_path])
            steps.append(f"  Added subtitle to command: {subtitle_path}")
            results.append({"step": 4, "status": "success", "message": f"Subtitle loaded: {subtitle_path}"})
        else:
            if subtitle_path:
                steps.append(f"  WARNING: Subtitle file not found: {subtitle_path}")
                results.append({"step": 4, "status": "warning", "message": f"Subtitle file not found: {subtitle_path}"})
            else:
                steps.append("  No subtitle will be used")
                results.append({"step": 4, "status": "info", "message": "No subtitle file"})
        
        # Step 5: Launch VLC
        steps.append("Step 5: Launching VLC")
        steps.append(f"  Full command: {' '.join(vlc_cmd)}")
        try:
            process = subprocess.Popen(vlc_cmd, shell=False)
            steps.append(f"  VLC process started (PID: {process.pid})")
            results.append({"step": 5, "status": "success", "message": f"VLC launched successfully (PID: {process.pid})"})
        except Exception as e:
            error_msg = f"Failed to launch VLC: {str(e)}"
            steps.append(f"  ERROR: {error_msg}")
            results.append({"step": 5, "status": "error", "message": error_msg})
            raise
        
        # Step 6: Save to history
        steps.append("Step 6: Saving to history")
        db = SessionLocal()
        try:
            launch_entry = LaunchHistory(
                path=movie_path,
                subtitle=subtitle_path,
                timestamp=datetime.now()
            )
            db.add(launch_entry)
            
            # Create watch history entry for launch (watch session started)
            watch_entry = WatchHistory(
                movie_id=movie_path,
                watch_status="watching",
                timestamp=datetime.now()
            )
            db.add(watch_entry)
            
            db.commit()
            steps.append("  History saved successfully")
            results.append({"step": 6, "status": "success", "message": "Launch saved to history"})
        finally:
            db.close()
        
        # Final summary
        steps.append("=" * 50)
        steps.append("LAUNCH COMPLETE")
        steps.append(f"Movie: {movie_path}")
        steps.append(f"VLC: {vlc_exe}")
        steps.append(f"Subtitle: {subtitle_path or 'None'}")
        steps.append(f"Process ID: {process.pid}")
        steps.append("=" * 50)
        
        return {
            "status": "launched",
            "subtitle": subtitle_path,
            "steps": steps,
            "results": results,
            "vlc_path": vlc_exe,
            "command": " ".join(vlc_cmd),
            "process_id": process.pid
        }
    except HTTPException as he:
        # Include steps in error response if possible
        error_detail = str(he.detail)
        steps.append(f"  HTTP ERROR: {error_detail}")
        results.append({"step": "error", "status": "error", "message": error_detail})
        # Try to return steps in error response
        try:
            return JSONResponse(
                status_code=he.status_code,
                content={
                    "status": "error",
                    "detail": error_detail,
                    "steps": steps,
                    "results": results
                }
            )
        except:
            raise he
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        steps.append(f"  FATAL ERROR: {error_msg}")
        results.append({"step": "error", "status": "error", "message": error_msg})
        # Try to return steps in error response
        try:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "detail": error_msg,
                    "steps": steps,
                    "results": results
                }
            )
        except:
            raise HTTPException(status_code=500, detail=error_msg)

@app.get("/api/history")
async def get_history():
    """Get search and launch history"""
    return load_history()

@app.get("/api/launch-history")
async def get_launch_history():
    """Get launch history with movie information"""
    db = SessionLocal()
    try:
        launches = db.query(LaunchHistory).order_by(LaunchHistory.timestamp.desc()).all()
        
        launches_with_info = []
        for launch in launches:
            movie = db.query(Movie).filter(Movie.path == launch.path).first()
            if movie:
                # Check if watched
                watch_entry = db.query(WatchHistory).filter(
                    WatchHistory.movie_id == launch.path,
                    WatchHistory.watch_status == "watched"
                ).order_by(WatchHistory.timestamp.desc()).first()
                
                # Get rating
                rating_entry = db.query(Rating).filter(Rating.movie_id == launch.path).first()
                
                images = json.loads(movie.images) if movie.images else []
                screenshots = json.loads(movie.screenshots) if movie.screenshots else []
                
                # Get frame path
                frame_path = get_movie_frame_path(db, launch.path)
                
                info = {
                    "images": images,
                    "screenshots": screenshots,
                    "frame": frame_path
                }
                
                movie_info = {
                    "path": movie.path,
                    "name": movie.name,
                    "length": movie.length,
                    "created": movie.created,
                    "size": movie.size,
                    "watched": watch_entry is not None,
                    "watched_date": watch_entry.timestamp.isoformat() if watch_entry and watch_entry.timestamp else None,
                    "rating": rating_entry.rating if rating_entry else None,
                    "images": images,
                    "screenshots": screenshots,
                    "frame": frame_path,
                    "image": get_largest_image(info),
                    "year": extract_year_from_name(movie.name)
                }
                
                launches_with_info.append({
                    "movie": movie_info,
                    "timestamp": launch.timestamp.isoformat(),
                    "subtitle": launch.subtitle
                })
        
        return {"launches": launches_with_info}
    finally:
        db.close()

@app.post("/api/watched")
async def mark_watched(request: WatchedRequest):
    """Mark movie as watched or unwatched, optionally with rating"""
    db = SessionLocal()
    try:
        if request.watched:
            # Create watch history entry
            watch_entry = WatchHistory(
                movie_id=request.path,
                watch_status="watched",
                timestamp=datetime.now()
            )
            db.add(watch_entry)
            
            # Update rating if provided
            if request.rating is not None:
                rating_entry = Rating(
                    movie_id=request.path,
                    rating=request.rating
                )
                db.merge(rating_entry)
        else:
            # Remove watch status (delete "watched" entries)
            db.query(WatchHistory).filter(
                WatchHistory.movie_id == request.path,
                WatchHistory.watch_status == "watched"
            ).delete()
            # Note: We keep the rating even when unwatched, but you can delete it if desired
            # db.query(Rating).filter(Rating.movie_id == request.path).delete()
        
        db.commit()
        return {"status": "updated"}
    finally:
        db.close()

@app.get("/api/watched")
async def get_watched():
    """Get list of watched movies"""
    db = SessionLocal()
    try:
        watched_movies_list = []
        
        # Get all movies with "watched" status, get most recent entry per movie
        watch_entries = db.query(WatchHistory).filter(
            WatchHistory.watch_status == "watched"
        ).order_by(WatchHistory.timestamp.desc()).all()
        
        watched_movie_ids = set()
        for watch_entry in watch_entries:
            if watch_entry.movie_id not in watched_movie_ids:
                watched_movie_ids.add(watch_entry.movie_id)
                
                movie = db.query(Movie).filter(Movie.path == watch_entry.movie_id).first()
                if movie:
                    # Get rating
                    rating_entry = db.query(Rating).filter(Rating.movie_id == watch_entry.movie_id).first()
                    
                    images = json.loads(movie.images) if movie.images else []
                    screenshots = json.loads(movie.screenshots) if movie.screenshots else []
                    
                    # Get frame path
                    frame_path = get_movie_frame_path(db, watch_entry.movie_id)
                    
                    info = {
                        "images": images,
                        "screenshots": screenshots,
                        "frame": frame_path
                    }
                    
                    movie_info = {
                        "path": movie.path,
                        "name": movie.name,
                        "length": movie.length,
                        "created": movie.created,
                        "size": movie.size,
                        "watched_date": watch_entry.timestamp.isoformat() if watch_entry.timestamp else None,
                        "rating": rating_entry.rating if rating_entry else None,
                        "images": images,
                        "screenshots": screenshots,
                        "frame": frame_path,
                        "image": get_largest_image(info),
                        "year": extract_year_from_name(movie.name),
                        "has_launched": db.query(LaunchHistory).filter(LaunchHistory.path == movie.path).count() > 0
                    }
                    watched_movies_list.append(movie_info)
        
        # Sort by watched date (most recent first)
        watched_movies_list.sort(key=lambda x: x.get("watched_date", ""), reverse=True)
        
        return {"watched": watched_movies_list}
    finally:
        db.close()

@app.get("/api/subtitles")
async def get_subtitles(video_path: str):
    """Find available subtitle files for a video"""
    video_path_obj = Path(video_path)
    video_dir = video_path_obj.parent
    base_name = video_path_obj.stem
    
    subtitles = []
    for ext in SUBTITLE_EXTENSIONS:
        # Check exact match
        subtitle_path = video_dir / f"{base_name}{ext}"
        if subtitle_path.exists():
            subtitles.append({
                "path": str(subtitle_path),
                "name": subtitle_path.name,
                "type": ext[1:].upper()
            })
        
        # Check common patterns
        for pattern in [f"{base_name}.en{ext}", f"{base_name}.eng{ext}", f"{base_name}_en{ext}"]:
            subtitle_path = video_dir / pattern
            if subtitle_path.exists() and str(subtitle_path) not in [s["path"] for s in subtitles]:
                subtitles.append({
                    "path": str(subtitle_path),
                    "name": subtitle_path.name,
                    "type": ext[1:].upper()
                })
    
    return {"subtitles": subtitles}

@app.get("/api/watch-history")
async def get_watch_history(movie_id: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=1000)):
    """Get watch history for a specific movie or all movies"""
    db = SessionLocal()
    try:
        if movie_id:
            watch_history = db.query(WatchHistory).filter(
                WatchHistory.movie_id == movie_id
            ).order_by(WatchHistory.timestamp.desc()).limit(limit).all()
        else:
            watch_history = db.query(WatchHistory).order_by(
                WatchHistory.timestamp.desc()
            ).limit(limit).all()
        
        history_list = []
        for entry in watch_history:
            movie = db.query(Movie).filter(Movie.path == entry.movie_id).first()
            history_list.append({
                "id": entry.id,
                "movie_id": entry.movie_id,
                "name": movie.name if movie else entry.movie_id,
                "watch_status": entry.watch_status,
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None
            })
        
        return {"history": history_list}
    finally:
        db.close()

@app.get("/api/config")
async def get_config():
    """Get current configuration"""
    db = SessionLocal()
    try:
        config = load_config()
        movies_folder = get_movies_folder()
        logger.info(f"get_config returning movies_folder: {movies_folder}")
        
        # Return all config settings
        return {
            "movies_folder": movies_folder or "",
            "default_folder": str(SCRIPT_DIR / "movies"),
            "settings": config  # Return all settings
        }
    finally:
        db.close()

@app.post("/api/config")
async def set_config(request: ConfigRequest):
    """Set movies folder path and/or user settings"""
    global ROOT_MOVIE_PATH
    logger.info(f"set_config called with: {request.movies_folder}, settings: {request.settings}")
    
    config = load_config()
    
    # Update movies folder if provided
    if request.movies_folder is not None:
        if not request.movies_folder:
            # Reset to default
            config.pop("movies_folder", None)
            save_config(config)
            ROOT_MOVIE_PATH = get_movies_folder()
            logger.info(f"Reset to default folder: {ROOT_MOVIE_PATH}")
            return {"status": "reset", "movies_folder": ROOT_MOVIE_PATH or ""}
        
        # Normalize path (handle both / and \)
        folder_path = request.movies_folder.strip()
        # Convert forward slashes to backslashes on Windows
        if os.name == 'nt':  # Windows
            folder_path = folder_path.replace('/', '\\')
            # Normalize double backslashes (but preserve UNC paths)
            if not folder_path.startswith('\\\\'):
                folder_path = folder_path.replace('\\\\', '\\')
            # Remove trailing backslash (unless it's a drive root like C:\)
            if folder_path.endswith('\\') and len(folder_path) > 3:
                folder_path = folder_path.rstrip('\\')
        
        logger.info(f"Normalized path: '{folder_path}'")
        logger.info(f"Path type: {type(folder_path)}")
        logger.info(f"Path length: {len(folder_path)}")
        logger.info(f"Path repr: {repr(folder_path)}")
        
        # Try Path object approach
        path_obj = Path(folder_path)
        logger.info(f"Path object: {path_obj}")
        logger.info(f"Path object absolute: {path_obj.absolute()}")
        logger.info(f"Path object exists (Path): {path_obj.exists()}")
        logger.info(f"Path object is_dir (Path): {path_obj.is_dir()}")
        
        # Try os.path approach
        logger.info(f"os.path.exists: {os.path.exists(folder_path)}")
        logger.info(f"os.path.isdir: {os.path.isdir(folder_path)}")
        logger.info(f"os.path.abspath: {os.path.abspath(folder_path)}")
        
        # Check if path exists using both methods
        exists_pathlib = path_obj.exists()
        exists_os = os.path.exists(folder_path)
        
        logger.info(f"Path exists check - pathlib: {exists_pathlib}, os.path: {exists_os}")
        
        if not exists_pathlib and not exists_os:
            error_msg = f"Path not found: '{folder_path}' (checked with both pathlib and os.path)"
            logger.error(error_msg)
            # Try to list parent directory to help debug
            parent = path_obj.parent
            if parent.exists():
                try:
                    contents = list(parent.iterdir())
                    logger.info(f"Parent directory exists. Contents: {[str(c) for c in contents[:10]]}")
                except Exception as e:
                    logger.error(f"Error listing parent directory: {e}")
            raise HTTPException(status_code=404, detail=error_msg)
        
        # Check if it's a directory
        is_dir_pathlib = path_obj.is_dir()
        is_dir_os = os.path.isdir(folder_path)
        
        logger.info(f"Is directory check - pathlib: {is_dir_pathlib}, os.path: {is_dir_os}")
        
        if not is_dir_pathlib and not is_dir_os:
            error_msg = f"Path is not a directory: '{folder_path}'"
            logger.error(error_msg)
            raise HTTPException(status_code=400, detail=error_msg)
        
        # Save movies folder to config
        config["movies_folder"] = folder_path
        save_config(config)
        logger.info(f"Saved to config: {folder_path}")
        
        # Update global
        ROOT_MOVIE_PATH = folder_path
        logger.info(f"Updated ROOT_MOVIE_PATH to: {ROOT_MOVIE_PATH}")
    
    # Update user settings if provided
    if request.settings:
        for key, value in request.settings.items():
            config[key] = value
        save_config(config)
        logger.info(f"Updated user settings: {list(request.settings.keys())}")
    
    return {"status": "updated", "movies_folder": config.get("movies_folder", ""), "settings": config}

@app.post("/api/open-folder")
async def open_folder(path: str = Query(...)):
    """Open file explorer at the folder containing the movie file"""
    try:
        path_obj = Path(path)
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        folder_path = path_obj.parent
        
        if os.name == 'nt':  # Windows
            subprocess.Popen(f'explorer.exe /select,"{path_obj}"', shell=True)
        elif os.name == 'posix':  # Linux/Mac
            if os.uname().sysname == 'Darwin':  # macOS
                subprocess.Popen(['open', '-R', str(path_obj)])
            else:  # Linux
                # Try various file managers
                for cmd in ['xdg-open', 'nautilus', 'dolphin', 'thunar']:
                    try:
                        subprocess.Popen([cmd, str(folder_path)])
                        break
                    except FileNotFoundError:
                        continue
                else:
                    raise HTTPException(status_code=500, detail="No file manager found")
        else:
            raise HTTPException(status_code=500, detail="Unsupported operating system")
        
        return {"status": "opened", "folder": str(folder_path)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error opening folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
async def get_stats():
    """Get indexing statistics"""
    db = SessionLocal()
    try:
        total_movies = db.query(Movie).count()
        # Count distinct movies with "watched" status
        watched_movie_ids = {entry.movie_id for entry in db.query(WatchHistory).filter(
            WatchHistory.watch_status == "watched"
        ).all()}
        watched_count = len(watched_movie_ids)
        indexed_paths = [ip.path for ip in db.query(IndexedPath).all()]
        movies_folder = get_movies_folder()
        return {
            "total_movies": total_movies,
            "watched_count": watched_count,
            "indexed_paths": indexed_paths,
            "movies_folder": movies_folder or ""
        }
    finally:
        db.close()

def get_vlc_window_titles():
    """Get window titles from running VLC instances on Windows"""
    if os.name != 'nt':  # Windows only for now
        return []
    
    try:
        # Use PowerShell to get VLC window titles
        ps_command = """
        Get-Process | Where-Object {$_.ProcessName -eq 'vlc'} | ForEach-Object {
            $proc = $_
            Add-Type -TypeDefinition @"
                using System;
                using System.Runtime.InteropServices;
                public class Win32 {
                    [DllImport("user32.dll")]
                    public static extern IntPtr GetForegroundWindow();
                    [DllImport("user32.dll")]
                    public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
                }
"@
            $hwnd = $proc.MainWindowHandle
            if ($hwnd -ne [IntPtr]::Zero) {
                $title = New-Object System.Text.StringBuilder 256
                [Win32]::GetWindowText($hwnd, $title, $title.Capacity) | Out-Null
                $titleText = $title.ToString()
                if ($titleText) {
                    Write-Output "$titleText|$($proc.Id)"
                }
            }
        }
        """
        
        result = subprocess.run(
            ["powershell", "-Command", ps_command],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            titles = []
            for line in result.stdout.strip().split('\n'):
                if '|' in line:
                    title, pid = line.split('|', 1)
                    if title and title.strip():
                        titles.append({"title": title.strip(), "pid": pid.strip()})
            return titles
    except Exception as e:
        logger.warning(f"Error getting VLC window titles: {e}")
    
    return []

def get_vlc_command_lines():
    """Get command line arguments from running VLC processes"""
    if os.name != 'nt':  # Windows only
        return []
    
    try:
        import shlex
        import re
        
        # Use wmic to get command line arguments
        result = subprocess.run(
            ["wmic", "process", "where", "name='vlc.exe'", "get", "CommandLine,ProcessId", "/format:csv"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and result.stdout.strip():
            command_lines = []
            lines = result.stdout.strip().split('\n')
            
            # Find header line to determine column positions
            header_line = None
            for line in lines:
                if 'CommandLine' in line and 'ProcessId' in line:
                    header_line = line
                    break
            
            if header_line:
                # Parse header to find column indices
                header_parts = [p.strip() for p in header_line.split(',')]
                try:
                    cmd_idx = header_parts.index('CommandLine')
                    pid_idx = header_parts.index('ProcessId')
                except ValueError:
                    # Fallback: assume standard order
                    cmd_idx = -2
                    pid_idx = -1
            
            for line in lines:
                if not line.strip() or 'CommandLine' in line or 'Node' in line:
                    continue
                
                parts = [p.strip() for p in line.split(',')]
                if len(parts) < 2:
                    continue
                
                if header_line:
                    cmd_line = parts[cmd_idx] if cmd_idx < len(parts) else ''
                    pid = parts[pid_idx] if pid_idx < len(parts) else ''
                else:
                    # Fallback parsing
                    cmd_line = parts[-2] if len(parts) >= 2 else ''
                    pid = parts[-1] if len(parts) >= 1 else ''
                
                if not cmd_line or 'vlc.exe' not in cmd_line.lower():
                    continue
                
                # Extract file path from command line
                # VLC command line format: "C:\path\to\vlc.exe" "C:\path\to\movie.mp4"
                try:
                    args = shlex.split(cmd_line)
                    # Find the first argument that's a file path (not vlc.exe itself)
                    for arg in args[1:]:  # Skip vlc.exe path
                        if os.path.exists(arg) and Path(arg).suffix.lower() in VIDEO_EXTENSIONS:
                            command_lines.append({"path": arg, "pid": pid})
                            break
                except:
                    # Fallback: try to extract path manually using regex
                    # Look for quoted paths or paths with video extensions
                    matches = re.findall(r'["\']([^"\']+\.(?:mp4|avi|mkv|mov|wmv|flv|webm|m4v|mpg|mpeg|3gp))["\']', cmd_line, re.IGNORECASE)
                    if matches:
                        for match in matches:
                            if os.path.exists(match):
                                command_lines.append({"path": match, "pid": pid})
                                break
                    else:
                        # Try unquoted paths
                        matches = re.findall(r'([A-Za-z]:[^"\']+\.(?:mp4|avi|mkv|mov|wmv|flv|webm|m4v|mpg|mpeg|3gp))', cmd_line, re.IGNORECASE)
                        for match in matches:
                            if os.path.exists(match):
                                command_lines.append({"path": match, "pid": pid})
                                break
            return command_lines
    except Exception as e:
        logger.warning(f"Error getting VLC command lines: {e}")
    
    return []

@app.get("/api/currently-playing")
async def get_currently_playing():
    """Get currently playing movies from VLC instances"""
    db = SessionLocal()
    try:
        playing = []
        
        # Try to get command line arguments first (more reliable)
        vlc_processes = get_vlc_command_lines()
        
        # If no command lines found, try window titles as fallback
        if not vlc_processes:
            titles = get_vlc_window_titles()
            # Try to match window titles to movie names
            for title_info in titles:
                title = title_info["title"]
                # VLC window title format is often: "movie_name - VLC media player"
                # Extract movie name
                if " - VLC" in title:
                    movie_name = title.split(" - VLC")[0].strip()
                    # Try to find matching movie in index
                    movie = db.query(Movie).filter(func.lower(Movie.name) == movie_name.lower()).first()
                    if movie:
                        playing.append({
                            "path": movie.path,
                            "name": movie.name,
                            "pid": title_info["pid"]
                        })
        
        # Process command line results
        for proc_info in vlc_processes:
            file_path = proc_info["path"]
            # Normalize path for comparison
            try:
                normalized_path = str(Path(file_path).resolve())
            except:
                normalized_path = file_path
            
            # Check if this path is in our index
            movie = db.query(Movie).filter(Movie.path == normalized_path).first()
            if movie:
                playing.append({
                    "path": normalized_path,
                    "name": movie.name,
                    "pid": proc_info["pid"]
                })
            else:
                # Try case-insensitive match
                movie = db.query(Movie).filter(func.lower(Movie.path) == normalized_path.lower()).first()
                if movie:
                    playing.append({
                        "path": movie.path,
                        "name": movie.name,
                        "pid": proc_info["pid"]
                    })
        
        return {"playing": playing}
    finally:
        db.close()

def extract_year_from_name(name):
    """Extract year from movie name (common patterns: (2023), 2023, -2023)"""
    import re
    # Try patterns: (2023), [2023], 2023, -2023
    patterns = [
        r'\((\d{4})\)',  # (2023)
        r'\[(\d{4})\]',  # [2023]
        r'\b(19\d{2}|20\d{2})\b',  # 2023 or 1999
    ]
    
    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            year = int(match.group(1))
            # Reasonable year range
            if 1900 <= year <= 2100:
                return year
    return None

def get_movie_frame_path(db: Session, movie_path: str):
    """Get the frame path for a movie from the database"""
    frame = db.query(MovieFrame).filter(MovieFrame.movie_id == movie_path).first()
    if frame and os.path.exists(frame.path):
        return frame.path
    return None

def get_largest_image(movie_info):
    """Get the largest image file from movie's images or screenshots"""
    all_images = []
    
    # Add folder images
    if movie_info.get("images"):
        for img_path in movie_info["images"]:
            try:
                if os.path.exists(img_path):
                    size = os.path.getsize(img_path)
                    all_images.append((img_path, size))
            except:
                pass
    
    # Add screenshots
    if movie_info.get("screenshots"):
        for screenshot_path in movie_info["screenshots"]:
            try:
                if os.path.exists(screenshot_path):
                    size = os.path.getsize(screenshot_path)
                    all_images.append((screenshot_path, size))
            except:
                pass
    
    # Add frame if available
    if movie_info.get("frame"):
        try:
            if os.path.exists(movie_info["frame"]):
                size = os.path.getsize(movie_info["frame"])
                all_images.append((movie_info["frame"], size))
        except:
            pass
    
    if not all_images:
        return None
    
    # Return the path of the largest image
    largest = max(all_images, key=lambda x: x[1])
    return largest[0]

def get_first_letter(name):
    """Get the first letter of a movie name for alphabet navigation"""
    if not name:
        return "#"
    name_stripped = name.strip()
    if not name_stripped:
        return "#"
    first_char = name_stripped[0].upper()
    return first_char if first_char.isalpha() else "#"

@app.get("/api/explore")
async def explore_movies(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=100),
    filter_type: str = Query("all", regex="^(all|watched|unwatched)$"),
    letter: Optional[str] = Query(None, regex="^[A-Z#]$")
):
    """Get all movies for exploration view with pagination and filters"""
    # Normalize letter to uppercase if provided
    if letter is not None:
        letter = letter.upper()
    
    # Log the actual request URL and query params to debug letter filtering
    query_params = dict(request.query_params)
    logger.info(f"Explore endpoint called: URL={request.url}")
    logger.info(f"Query params: {query_params}")
    logger.info(f"Parsed letter parameter: {letter!r} (type: {type(letter)})")
    
    db = SessionLocal()
    try:
        # Get watched paths (movies with "watched" status)
        watched_paths = set()
        watched_dict = {}
        watch_entries = db.query(WatchHistory).filter(
            WatchHistory.watch_status == "watched"
        ).order_by(WatchHistory.timestamp.desc()).all()
        
        for entry in watch_entries:
            if entry.movie_id not in watched_paths:
                watched_paths.add(entry.movie_id)
                watched_dict[entry.movie_id] = {
                    "watched_date": entry.timestamp.isoformat() if entry.timestamp else None,
                    "rating": None
                }
        
        # Get ratings
        for rating in db.query(Rating).all():
            if rating.movie_id in watched_dict:
                watched_dict[rating.movie_id]["rating"] = rating.rating
        
        # First pass: build all movies matching the filter (for letter counts)
        all_filtered_movies = []
        for movie in db.query(Movie).all():
            is_watched = movie.path in watched_paths
            
            # Apply watched/unwatched filter
            if filter_type == "watched" and not is_watched:
                continue
            if filter_type == "unwatched" and is_watched:
                continue
            
            first_letter = get_first_letter(movie.name)
            all_filtered_movies.append({
                "path": movie.path,
                "name": movie.name,
                "first_letter": first_letter,
                "is_watched": is_watched
            })
        
        # Calculate letter counts from all filtered movies (not affected by letter filter)
        letter_counts = {}
        for movie in all_filtered_movies:
            movie_letter = movie["first_letter"]
            letter_counts[movie_letter] = letter_counts.get(movie_letter, 0) + 1
        
        # Second pass: apply letter filter and build full movie list
        movies = []
        skipped_by_watched = 0
        skipped_by_letter = 0
        for movie in db.query(Movie).all():
            is_watched = movie.path in watched_paths
            
            # Apply watched/unwatched filter
            if filter_type == "watched" and not is_watched:
                skipped_by_watched += 1
                continue
            if filter_type == "unwatched" and is_watched:
                skipped_by_watched += 1
                continue
            
            first_letter = get_first_letter(movie.name)
            
            # Filter by letter if specified - only show movies that START with the letter
            if letter is not None and letter != "":
                if first_letter != letter:
                    skipped_by_letter += 1
                    continue
                # Debug: log first few matches
                if len(movies) < 3:
                    logger.info(f"Letter filter '{letter}' MATCH: '{movie.name}' -> first_letter='{first_letter}'")
            else:
                # Debug: log when no letter filter is applied
                if len(movies) < 3:
                    logger.debug(f"No letter filter: '{movie.name}' -> first_letter='{first_letter}'")
            
            images = json.loads(movie.images) if movie.images else []
            screenshots = json.loads(movie.screenshots) if movie.screenshots else []
            
            # Get frame path
            frame_path = get_movie_frame_path(db, movie.path)
            
            info = {
                "images": images,
                "screenshots": screenshots,
                "frame": frame_path
            }
            
            # Get largest image
            largest_image = get_largest_image(info)
            
            # Extract year from name
            year = extract_year_from_name(movie.name)
            
            has_launched = db.query(LaunchHistory).filter(LaunchHistory.path == movie.path).count() > 0
            
            movies.append({
                "path": movie.path,
                "name": movie.name,
                "length": movie.length,
                "created": movie.created,
                "size": movie.size,
                "watched": is_watched,
                "watched_date": watched_dict.get(movie.path, {}).get("watched_date") if is_watched else None,
                "rating": watched_dict.get(movie.path, {}).get("rating") if is_watched else None,
                "frame": frame_path,
                "image": largest_image,
                "first_letter": first_letter,
                "year": year,
                "has_launched": has_launched
            })
        
        # Sort by name (case-insensitive, ignoring leading dots/numbers)
        # Strip leading non-alphabetic characters for proper alphabetical sorting
        def sort_key(movie):
            name = movie["name"].strip()
            # Remove leading dots, numbers, and special chars for sorting
            name_clean = name.lstrip('.-_0123456789 ')
            if not name_clean:
                name_clean = name
            return name_clean.lower()
        
        movies.sort(key=sort_key)
        
        # Calculate pagination
        total = len(movies)
        start = (page - 1) * per_page
        end = start + per_page
        paginated_movies = movies[start:end]
        
        logger.info(f"Explore API: letter={letter}, filter_type={filter_type}, total_movies={total}, paginated={len(paginated_movies)}, skipped_by_watched={skipped_by_watched}, skipped_by_letter={skipped_by_letter}")
        if letter is not None:
            if paginated_movies:
                first_letters = [get_first_letter(m['name']) for m in paginated_movies[:10]]
                logger.info(f"After letter filter '{letter}': First 10 movie names: {[m['name'] for m in paginated_movies[:10]]}")
                logger.info(f"First letters of those movies: {first_letters}")
                # Verify filtering worked
                mismatches = [m['name'] for m in paginated_movies[:10] if get_first_letter(m['name']) != letter]
                if mismatches:
                    logger.error(f"FILTERING BUG: Found {len(mismatches)} movies that don't match letter '{letter}': {mismatches[:5]}")
            else:
                logger.warning(f"Letter filter '{letter}' applied but no movies returned! Total before filter: {len(movies)}")
        else:
            if paginated_movies:
                first_letters = [get_first_letter(m['name']) for m in paginated_movies[:10]]
                logger.info(f"No letter filter: First 10 movie names: {[m['name'] for m in paginated_movies[:10]]}")
                logger.info(f"First letters of those movies: {first_letters}")
        
        return {
            "movies": paginated_movies,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page if total > 0 else 0
            },
            "letter_counts": letter_counts
        }
    finally:
        db.close()

@app.on_event("startup")
async def startup_event():
    """Initialize database and auto-index on startup if root path configured"""
    # Initialize database
    init_db()
    
    # Migrate from JSON if needed
    migrate_json_to_db()
    
    # Auto-index on startup if root path configured
    movies_folder = get_movies_folder()
    if movies_folder and os.path.exists(movies_folder):
        result = scan_directory(movies_folder)
        print(f"Startup indexing: {result['indexed']} files found, {result['updated']} updated")

if __name__ == "__main__":
    import uvicorn
    # Auto-reload enabled by default - server restarts when Python files change
    # Disable colored output for Windows PowerShell compatibility
    config = uvicorn.Config(
        app, 
        host="127.0.0.1", 
        port=8002, 
        reload=True,
        use_colors=False
    )
    server = uvicorn.Server(config)
    server.run()

