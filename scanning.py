"""
Scanning and indexing logic for Movie Searcher.
Handles directory scanning, movie indexing, and progress tracking.
"""
import os
import json
import re
import hashlib
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql import func

# Database imports
from database import SessionLocal, Movie, Screenshot, IndexedPath, MovieAudio, Rating, MovieStatus, LaunchHistory, PlaylistItem
from models import MovieList, MovieListItem

# Fuzzy matching for movie list reconciliation
from fuzzywuzzy import fuzz, process as fuzz_process

# Centralized cleaning patterns
from cleaning_patterns import (
    QUALITY_SOURCE_PATTERNS,
    EDITION_PATTERNS,
    get_forbidden_markers,
    get_forbidden_union_pattern,
    clean_folder_name,
    remove_website_prefixes,
    normalize_separators,
    remove_quality_tags,
    remove_edition_tags,
    remove_language_tags,
    clean_release_group_suffix,
)

# Video processing imports
from video_processing import (
    shutdown_flag, frame_extraction_queue,
    get_video_length as get_video_length_vp,
    extract_screenshots as extract_screenshots_core,
    extract_movie_screenshot as extract_movie_screenshot_core,
    find_ffmpeg as find_ffmpeg_core,
    process_frame_queue as process_frame_queue_core,
    _get_ffprobe_path_from_config
)

logger = logging.getLogger(__name__)

# File extensions
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg', '.3gp'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

# Minimum file size threshold (bytes) for inclusion in index
# Requirement: Skip including files smaller than 50 MB entirely
MIN_FILE_SIZE_BYTES = 50 * 1024 * 1024

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
    "frames_total": 0,
    "movies_added": 0,      # New movies added to database
    "movies_updated": 0,    # Existing movies updated
    "movies_removed": 0     # Orphaned movies removed
}

# Import config functions directly from shared module
from config import load_config, get_movies_folder

def add_scan_log(level: str, message: str):
    """Add a log entry to scan progress and permanent log file"""
    global scan_progress
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "level": level,  # "info", "success", "warning", "error"
        "message": message
    }
    scan_progress["logs"].append(log_entry)
    
    # Also write to permanent log file
    if level == "error":
        logger.error(f"[SCAN] {message}")
    elif level == "warning":
        logger.warning(f"[SCAN] {message}")
    elif level == "success":
        logger.info(f"[SCAN] ✓ {message}")
    else:
        logger.info(f"[SCAN] {message}")

def is_sample_file(file_path):
    """Check if a file should be excluded (contains 'sample' in name, case-insensitive)"""
    if isinstance(file_path, Path):
        name = file_path.stem.lower()
    else:
        name = Path(file_path).stem.lower()
    return 'sample' in name

def get_file_hash(file_path):
    """Generate hash for file to detect changes"""
    stat = os.stat(file_path)
    return hashlib.md5(f"{file_path}:{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()

def extract_video_metadata_with_ffprobe(file_path):
    """
    Extract both video duration and audio types in a single ffprobe call.
    Returns (duration_seconds, audio_languages_list) tuple.
    """
    ffprobe = _get_ffprobe_path_from_config()
    if not ffprobe:
        return None, ["unknown"]

    try:
        import subprocess, json as _json
        # Single ffprobe call to get both duration and audio info
        cmd = [
            ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-select_streams", "a",
            "-show_entries", "stream=index:stream_tags=language",
            "-of", "json",
            str(file_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None, ["unknown"]

        data = _json.loads(result.stdout or "{}")

        # Extract duration
        duration = None
        if "format" in data:
            duration_str = data["format"].get("duration")
            if duration_str:
                try:
                    duration = float(duration_str)
                except ValueError:
                    pass

        # Extract audio languages
        streams = data.get("streams", []) or []
        langs = []
        for s in streams:
            tags = s.get("tags") or {}
            lang = tags.get("language") or "unknown"
            langs.append(str(lang).strip() or "unknown")
        audio_langs = langs if langs else ["unknown"]

        return duration, audio_langs
    except Exception:
        return None, ["unknown"]

def _extract_audio_types_with_ffprobe(file_path):
    """
    Use ffprobe to extract audio stream language tags. Returns a list of strings.
    If no tags are present, returns ['unknown'].
    """
    # Use the combined function but only return audio types
    _, audio_types = extract_video_metadata_with_ffprobe(file_path)
    return audio_types

def _refresh_movie_audio_rows(db: Session, movie_id: int, audio_types):
    """
    Replace movie_audio rows for a given movie with provided audio_types.
    Ensures uniqueness and minimal writes.
    """
    try:
        # Normalize and deduplicate
        normalized = []
        seen = set()
        for a in audio_types or []:
            at = (a or "unknown").strip().lower()
            if not at:
                at = "unknown"
            if at not in seen:
                seen.add(at)
                normalized.append(at)
        # Fetch existing
        existing_rows = db.query(MovieAudio).filter(MovieAudio.movie_id == movie_id).all()
        existing_set = {row.audio_type for row in existing_rows}
        new_set = set(normalized if normalized else ["unknown"])
        # Delete removed
        to_delete = existing_set - new_set
        if to_delete:
            db.query(MovieAudio).filter(
                MovieAudio.movie_id == movie_id,
                MovieAudio.audio_type.in_(list(to_delete))
            ).delete(synchronize_session=False)
        # Insert new
        to_insert = new_set - existing_set
        for at in to_insert:
            db.add(MovieAudio(movie_id=movie_id, audio_type=at))
    except Exception:
        # Fail safe: do not block scan on audio metadata failure
        pass

def find_images_in_folder(video_path):
    """
    Find image files in the same folder as the video.
    
    Simplified: just glob for any image files (*.jpg, *.png, etc.) in the folder.
    Filters out YTS images.
    
    Returns a list of image file paths found in the video's folder.
    """
    video_path_obj = Path(video_path)
    video_dir = video_path_obj.parent
    
    images = []
    for ext in IMAGE_EXTENSIONS:
        # Find all files with this extension in the folder
        for img_file in video_dir.glob(f"*{ext}"):
            img_path_str = str(img_file)
            # Filter out YTS images
            if "www.yts" not in img_file.name.lower():
                images.append(img_path_str)
    
    return images

def filter_yts_images(image_paths):
    """Filter out images with 'www.YTS.AM' in filename"""
    if not image_paths:
        return []
    filtered = []
    for img_path in image_paths:
        # Check if filename contains www.YTS.AM
        img_name = Path(img_path).name
        if "www.yts" in img_name.lower():
            continue
        if "www.yify" in img_name.lower():
            continue
        if "torrents" in img_name.lower():
            continue
        if "Kolla denn" in img_name.lower():
            continue
        filtered.append(img_path)
    return filtered

def extract_year_from_name(name):
    """Extract year from movie name (1900-2035)"""
    # Look for 4-digit years in the range 1900-2035
    year_pattern = r'\b(19\d{2}|20[0-2]\d|203[0-5])\b'
    matches = re.findall(year_pattern, name)
    if matches:
        # Return the first valid year found
        year = int(matches[0])
        if 1900 <= year <= 2035:
            return year
    return None

def load_cleaning_patterns():
    """Load approved cleaning patterns from config file"""
    from config import load_config
    try:
        config = load_config()
        data = config.get('cleaning_patterns')
        if data:
            if isinstance(data, dict):
                return {
                    'exact_strings': set(data.get('exact_strings', [])),
                    'bracket_patterns': data.get('bracket_patterns', []),
                    'parentheses_patterns': data.get('parentheses_patterns', []),
                    'year_patterns': data.get('year_patterns', True),  # Default to True
                }
    except Exception as e:
        logger.error(f"Error loading cleaning patterns: {e}")
    
    # Return defaults if not found
    return {
        'exact_strings': set(),
        'bracket_patterns': [],
        'parentheses_patterns': [],
        'year_patterns': True,
    }

def clean_movie_name(name, patterns=None):
    """Clean movie name using approved patterns and extract year.
    Can handle both filenames and full paths. For full paths, extracts season/episode info.
    """
    if patterns is None:
        patterns = load_cleaning_patterns()
    
    original_name = name
    year = None
    season = None
    episode = None
    
    # Check if input is a full path (contains path separators)
    is_full_path = '/' in name or '\\' in name
    path_obj = None
    parent_folder = None
    
    if is_full_path:
        # Extract path components
        path_obj = Path(name)
        parent_folder = path_obj.parent.name if path_obj.parent.name else None
        # Use filename for initial cleaning
        name = path_obj.stem
        
        # If filename is very short (1-2 chars), use parent folder instead as it likely contains the real title
        # e.g., "The Fruit is Ripe 3 (HK 1999 Cert. III\b.mpeg" -> use parent folder
        if len(name) <= 2 and parent_folder and parent_folder.lower() not in ['movies', 'tv', 'series', 'shows', 'video', 'videos', '_done', 'done']:
            name = parent_folder
        # If filename looks like a cryptic release code (group-abbreviation format) and parent has a year,
        # use parent folder. E.g., "ssf-sil1080" in "Movie.Title.1998.1080p.BluRay.x264-SSF" folder
        elif parent_folder and parent_folder.lower() not in ['movies', 'tv', 'series', 'shows', 'video', 'videos', '_done', 'done']:
            # Check if filename is cryptic: short group prefix followed by dash and code
            # Pattern: 2-4 letter group, dash, then alphanumeric code (e.g., "ssf-sil1080", "xyz-abc720")
            is_cryptic_filename = re.match(r'^[a-zA-Z]{2,4}-[a-zA-Z0-9]+$', name, re.IGNORECASE)
            # Also check if parent has a year (indicating it's a proper scene release folder)
            parent_has_year = re.search(r'\b(19\d{2}|20[0-2]\d)\b', parent_folder)
            if is_cryptic_filename and parent_has_year:
                name = parent_folder
    else:
        # Just a filename, use as-is
        name = name
    
    # STEP 0.5: Remove website prefixes and markers
    
    # First, handle bracketed website prefixes like "[ www.UsaBit.com ] - "
    # These must be removed BEFORE other patterns to avoid partial matches
    name = re.sub(r'^\s*\[\s*www\.[^\]]+\]\s*-\s*', '', name, flags=re.IGNORECASE)
    
    # Handle non-bracketed website prefixes like "www.MovieRulz.lt - "
    name = re.sub(r'^\s*www\.[^\s]+\s+-\s*', '', name, flags=re.IGNORECASE)
    
    # Handle ".Com_" markers like "720pMkv.Com_The.Baader.Meinhof.Complex" -> "The Baader Meinhof Complex"
    name = re.sub(r'^.*?\.Com[._\s]+', '', name, flags=re.IGNORECASE)
    
    name = name.strip()
    
    # STEP 1: Normalize separators and trivial punctuation
    # - Convert runs of '.' or '_' into single spaces
    # - Collapse multiple spaces
    # - Trim leading/trailing spaces/dots/dashes/underscores
    name = re.sub(r'[._]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip(' \t.-_')

    # STEP 2: Remove consecutive dots (in case any remain due to other chars)
    name = re.sub(r'\.{2,}', ' ', name)

    # STEP 3: Strip explicit trailing '-' or '.' (defensive after earlier trims)
    name = re.sub(r'[\s\-\.]+$', '', name)

    # Helper: forbidden markers to strip or detect inside brackets (from centralized patterns)
    forbidden_union = get_forbidden_union_pattern()

    # Extract year first if enabled
    if patterns.get('year_patterns', True):
        year = extract_year_from_name(name)
        # If year found, remove everything from the year onwards (including parentheses/brackets around year)
        if year:
            # Pattern to match: optional opening bracket/paren, whitespace, year (with word boundaries), whitespace, optional closing bracket/paren, and everything after
            # This handles: (1971), [1971], {1971}, <1971>, or just 1971
            year_with_context_pattern = rf'(?:[([{{<]\s*)?\b{year}\b\s*(?:[)\]}}>])?.*$'
            # Replace the year and everything after it with empty string
            name = re.sub(year_with_context_pattern, '', name, count=1).strip()
            # If removing the year left a dangling, unmatched opening bracket at the end
            # (e.g., "Love and Death (Woody Allen"), drop that trailing bracketed fragment.
            name = re.sub(r'\s*[\(\[\{<][^)\]}>]*$', '', name).strip()
    
    # STEP 4: Remove exact strings (from DB-configured patterns)
    for exact_str in patterns.get('exact_strings', set()):
        name = name.replace(exact_str, ' ')
    
    # STEP 5: Remove bracket content if configured via patterns list
    for pattern in patterns.get('bracket_patterns', []):
        if pattern == '[anything]':
            name = re.sub(r'\[.*?\]', '', name)
        else:
            name = name.replace(pattern, ' ')
    
    # STEP 6: Remove parentheses content if configured via patterns list
    for pattern in patterns.get('parentheses_patterns', []):
        if pattern == '(anything)':
            # Remove parentheses content, but be smart about it
            # Don't remove if it's just a year or looks like part of title
            name = re.sub(r'\([^)]*\)', '', name)
        else:
            name = name.replace(pattern, ' ')

    # STEP 7: Remove common quality/resolution/source/codec/audio tags (using centralized patterns)
    for p in QUALITY_SOURCE_PATTERNS:
        name = re.sub(p, ' ', name, flags=re.IGNORECASE)

    # STEP 8: Remove edition/packaging flags (using centralized patterns)
    for p in EDITION_PATTERNS:
        name = re.sub(p, ' ', name, flags=re.IGNORECASE)

    # STEP 9: Extract season/episode info BEFORE removing tags (for TV series)
    episode_title = None
    if is_full_path and path_obj:
        # Extract season from parent folder name (e.g., "Season 1", "Season 01", "S1", "S01")
        parent_str = str(path_obj.parent.name) if path_obj.parent.name else str(path_obj.parent)
        season_match = re.search(r'(?:Season|season)\s*(\d+)', parent_str, re.IGNORECASE)
        if not season_match:
            season_match = re.search(r'\bS(\d+)\b', parent_str, re.IGNORECASE)
        if season_match:
            season = int(season_match.group(1))
        
        # Extract episode from original filename (before cleaning)
        original_filename = path_obj.stem
        
        # First try to find SXXEXX pattern (most common) - this also gives us season if not found in parent
        sxxexx_match = re.search(r'\bS(\d+)E(\d+)\b', original_filename, re.IGNORECASE)
        if sxxexx_match:
            # If we didn't find season in parent folder, use the one from filename
            if season is None:
                season = int(sxxexx_match.group(1))
            episode = int(sxxexx_match.group(2))
        else:
            # Try to find episode number in various formats
            # First check for custom formats like "Vol1-Episode2"
            vol_ep_match = re.search(r'\bVol(\d+)-Episode(\d+)\b', original_filename, re.IGNORECASE)
            if vol_ep_match:
                # For Vol-Episode format, treat as episode within a volume
                episode = int(vol_ep_match.group(2))
                # Also set episode_title to the full Vol-Episode string for proper formatting
                episode_title = vol_ep_match.group(0)
            else:
                episode_match = re.search(r'[_-](\d+)(?:\.|$)', original_filename)
                if not episode_match:
                    episode_match = re.search(r'\bE(\d+)\b', original_filename, re.IGNORECASE)
                if not episode_match:
                    episode_match = re.search(r'\b(?:ep|episode)\s*(\d+)\b', original_filename, re.IGNORECASE)
                # Also check for leading episode number (e.g., "02-A Sound of Dolphins")
                # Only infer from leading numbers when a season context exists
                if not episode_match and season is not None:
                    leading_ep_match = re.search(r'^\s*(\d+)[._\s-]+', original_filename)
                    if leading_ep_match:
                        episode = int(leading_ep_match.group(1))
                elif episode_match:
                    episode = int(episode_match.group(1))
        
        # Extract episode title from filename (text after SXXEXX or episode number)
        # For example: "024 S02E01 Points of Departure" -> "024 Points of Departure"
        # Or: "BEASTARS.S01E10.A.Wolf.in.Sheeps.Clothing.1080p..." -> "A Wolf in Sheeps Clothing"
        # Or: "02-A Sound of Dolphins" -> "02 A Sound of Dolphins"
        if season is not None or episode is not None:
            # Try to find text after SXXEXX pattern, including leading number if present
            # Pattern: optional leading number, SXXEXX, then episode title (don't require start of string)
            sxxexx_match = re.search(r'(\d+\s+)?\bS\d{2}E\d{2}\b[._\s]+(.+)$', original_filename, re.IGNORECASE)
            if sxxexx_match:
                leading_num = sxxexx_match.group(1) or ""
                title_part = sxxexx_match.group(2).strip()
                episode_title = (leading_num + title_part).strip()
            else:
                # Try to find text after episode number (E\d+)
                ep_match = re.search(r'(\d+\s+)?\bE\d+\b[._\s]+(.+)$', original_filename, re.IGNORECASE)
                if ep_match:
                    leading_num = ep_match.group(1) or ""
                    title_part = ep_match.group(2).strip()
                    episode_title = (leading_num + title_part).strip()
                else:
                    # Try to find text after "Episode XX" format (e.g., "ShowName Episode 01 - Title")
                    episode_word_match = re.search(r'\b(?:ep|episode)\s*\d+\s*[._\s-]+(.+)$', original_filename, re.IGNORECASE)
                    if episode_word_match:
                        episode_title = episode_word_match.group(1).strip()
                    else:
                        # Try to find text after standalone episode number (e.g., "02-A Sound of Dolphins")
                        num_match = re.search(r'^\s*(\d+)[._\s-]+(.+)$', original_filename)
                        if num_match:
                            episode_title = num_match.group(1) + " " + num_match.group(2).strip()
        
        # If we still don't have episode_title but filename starts with a number, extract it
        # Only do this when a season context exists to avoid misclassifying movies like "13 Assassins"
        if season is not None and not episode_title and is_full_path and path_obj and re.match(r'^\s*\d+[._\s-]+', original_filename):
            num_match = re.search(r'^\s*(\d+)[._\s-]+(.+)$', original_filename)
            if num_match:
                if episode is None:
                    episode = int(num_match.group(1))
                if not episode_title:
                    episode_title = num_match.group(1) + " " + num_match.group(2).strip()
        
        # Extract year from parent folder if not found in filename yet
        # Skip if parent looks like a year RANGE (e.g., "[1971-5]") to avoid incorrect assignment
        if year is None and parent_str:
            if not re.search(r'(?:\[\s*(19\d{2}|20[0-2]\d|203[0-5])\s*-\s*\d+\s*\])|(?:\b(19\d{2}|20[0-2]\d|203[0-5])\s*-\s*\d+\b)', parent_str):
                parent_year = extract_year_from_name(parent_str)
                if parent_year:
                    year = parent_year
        
        # Try to get show name from parent or grandparent folder
        # Only when we have explicit episode/season info; do NOT infer from leading numbers
        has_episode_info = (season is not None or episode is not None)
        
        if has_episode_info:
            show_name_extracted = False
            # First try parent folder (for cases like "Babylon 5 (1993)")
            parent_name = parent_str
            if parent_name and parent_name.lower() not in ['movies', 'tv', 'series', 'shows', 'video', 'videos', 'season 1', 'season 2', 's1', 's2', '_done', 'done']:
                # Check if parent folder looks like a show name (not a season folder)
                # Only skip if it looks like a DEDICATED season folder (starts with Season X, or is SXX)
                # "Forbrydelsen - Season 1" should be treated as a show name (which we'll clean later)
                is_dedicated_season_folder = re.match(r'^\s*(?:Season|season)\s*\d+', parent_name, re.IGNORECASE) or \
                                           re.match(r'^\s*S\d+\s*$', parent_name, re.IGNORECASE)
                
                if not is_dedicated_season_folder:
                    # Use parent folder as show name, but clean it thoroughly
                    show_name = parent_name
                    # Remove website prefixes like "www.UIndex.org -" BEFORE other cleaning
                    show_name = re.sub(r'^www\.[^\s]+\.\w+\s*-\s*', '', show_name, flags=re.IGNORECASE)
                    # Remove quality/resolution/source/codec/audio tags BEFORE normalizing (using centralized patterns)
                    for p in QUALITY_SOURCE_PATTERNS:
                        show_name = re.sub(p, ' ', show_name, flags=re.IGNORECASE)
                    for p in EDITION_PATTERNS:
                        show_name = re.sub(p, ' ', show_name, flags=re.IGNORECASE)
                    # Remove specific patterns that might have dots (BEFORE normalizing)
                    show_name = re.sub(r'\b(?:NF|WEBRip|WEB-DL|DDP\d+\.?\d*|x264|x265|1080p|720p|480p|4k|uhd)\b', ' ', show_name, flags=re.IGNORECASE)
                    # Remove standalone decimal numbers that are likely quality tags (like "2.0" from "DDP2.0")
                    show_name = re.sub(r'\b\d+\.\d+\b', ' ', show_name)
                    # Now normalize dots/underscores to spaces
                    show_name = re.sub(r'[._]+', ' ', show_name)
                    # Remove year in parentheses
                    show_name = re.sub(r'\([^)]*\)', '', show_name)
                    # Remove common folder patterns (brackets)
                    show_name = re.sub(r'\[.*?\]', '', show_name)
                    # Remove season/episode patterns (S01, S02, S01-05 etc.)
                    show_name = re.sub(r'\bS\d+(?:-\d+)?\b', ' ', show_name, flags=re.IGNORECASE)
                    show_name = re.sub(r'\bSeason\s*\d+(?:-\d+)?\b', ' ', show_name, flags=re.IGNORECASE)
                    show_name = re.sub(r'\bS\d+E\d+\b', ' ', show_name, flags=re.IGNORECASE)
                    # Remove language tags
                    show_name = re.sub(r'\b(?:japanese|english|french|german|spanish|italian|russian|korean|hindi|eng|dan|ita|en-sub)\b', ' ', show_name, flags=re.IGNORECASE)
                    # Remove release group suffixes - only if:
                    # 1. Preceded by space+dash (like " -YIFY"), OR
                    # 2. Is all lowercase with 4+ chars (like "moviesbyrizzo")
                    # This avoids removing legitimate name parts like "Saul" in "Better Call Saul"
                    # or hyphenated titles like "The A-Team"
                    show_name = re.sub(r'\s+\b[a-z]{4,15}\b\s*$', ' ', show_name)
                    show_name = re.sub(r'\s-\s*\b[A-Za-z0-9]{2,15}\b\s*$', ' ', show_name)
                    
                    # Remove empty parentheses (left over from removing content)
                    show_name = re.sub(r'\(\s*\)', ' ', show_name)
                    # Clean up multiple dashes and trailing/leading dashes
                    show_name = re.sub(r'[–—\-]{2,}', ' ', show_name)
                    show_name = re.sub(r'[–—\-]+\s*$', ' ', show_name)
                    show_name = re.sub(r'^\s*[–—\-]+', ' ', show_name)

                    # Don't remove single digits - they might be part of the show name (e.g., "Babylon 5")
                    # Clean up spaces
                    show_name = re.sub(r'\s+', ' ', show_name).strip()
                    if show_name:
                        name = show_name
                        show_name_extracted = True
            
            if not show_name_extracted:
                # Before falling back to grandparent, check if the filename already contains
                # the show name before episode patterns (SxxExx or "Season X Episode Y")
                # This is more reliable than grandparent folder which may have extra metadata
                original_filename = path_obj.stem
                # Try SxxExx format first (e.g., "Knight Rider S02E11 Knightmares")
                episode_pattern_match = re.search(r'^(.+?)\s*S\d{1,2}E\d{1,2}\b', original_filename, re.IGNORECASE)
                if not episode_pattern_match:
                    # Try "Season X Episode Y" format (e.g., "Show Name Season 4 Episode 12 - Title")
                    episode_pattern_match = re.search(r'^(.+?)\s*Season\s*\d+\s*Episode\s*\d+', original_filename, re.IGNORECASE)
                if episode_pattern_match:
                    show_name_from_filename = episode_pattern_match.group(1).strip()
                    # Clean it (normalize dots/underscores, remove brackets/parentheses, trailing dashes)
                    show_name_from_filename = re.sub(r'[._]+', ' ', show_name_from_filename)
                    show_name_from_filename = re.sub(r'\[.*?\]', '', show_name_from_filename)
                    show_name_from_filename = re.sub(r'\([^)]*\)', '', show_name_from_filename)
                    show_name_from_filename = re.sub(r'\s*-\s*$', '', show_name_from_filename)
                    show_name_from_filename = re.sub(r'\s+', ' ', show_name_from_filename).strip()
                    if show_name_from_filename and len(show_name_from_filename) >= 2:
                        name = show_name_from_filename
                        show_name_extracted = True
                        # Still try to extract year from grandparent folder if we haven't found one yet
                        if year is None:
                            grandparent = path_obj.parent.parent.name if path_obj.parent.parent.name else None
                            if grandparent and grandparent.lower() not in ['movies', 'tv', 'series', 'shows', 'video', 'videos', '_done', 'done']:
                                grandparent_year = extract_year_from_name(grandparent)
                                if grandparent_year:
                                    year = grandparent_year
            
            if not show_name_extracted:
                # Fall back to grandparent folder (the show name, skipping the season folder)
                # But skip if parent has fake episode numbering
                grandparent = path_obj.parent.parent.name if path_obj.parent.parent.name else None
                if grandparent and grandparent.lower() not in ['movies', 'tv', 'series', 'shows', 'video', 'videos', '_done', 'done']:
                    # Use grandparent folder as show name, but clean it first
                    show_name = grandparent
                    # Normalize dots/underscores to spaces first
                    show_name = re.sub(r'[._]+', ' ', show_name)
                    # Remove common folder patterns
                    show_name = re.sub(r'\[.*?\]', '', show_name)
                    show_name = re.sub(r'\(.*?\)', '', show_name)
                    # Remove quality tags (using centralized patterns)
                    for p in QUALITY_SOURCE_PATTERNS:
                        show_name = re.sub(p, ' ', show_name, flags=re.IGNORECASE)
                    for p in EDITION_PATTERNS:
                        show_name = re.sub(p, ' ', show_name, flags=re.IGNORECASE)
                    
                    # Remove season/episode patterns (S01, S02, S01-05 etc.)
                    show_name = re.sub(r'\bS\d+(?:-\d+)?\b', ' ', show_name, flags=re.IGNORECASE)
                    show_name = re.sub(r'\bSeason\s*\d+(?:-\d+)?\b', ' ', show_name, flags=re.IGNORECASE)
                    show_name = re.sub(r'\bS\d+E\d+\b', ' ', show_name, flags=re.IGNORECASE)

                    # Remove language tags from grandparent too
                    show_name = re.sub(r'\b(?:japanese|english|french|german|spanish|italian|russian|korean|hindi|eng|dan|ita|en-sub)\b', ' ', show_name, flags=re.IGNORECASE)
                    
                    # Remove release group suffixes (all-lowercase 4+ chars, or preceded by space+dash, or alphanumeric with digits)
                    show_name = re.sub(r'\s+\b[a-z]{4,15}\b\s*$', ' ', show_name)
                    show_name = re.sub(r'\s+\b[A-Za-z]+\d+[A-Za-z0-9]*\b\s*$', ' ', show_name)  # e.g., Retic1337
                    show_name = re.sub(r'\s-\s*\b[A-Za-z0-9]{2,15}\b\s*$', ' ', show_name)
                    
                    # Remove empty parentheses
                    show_name = re.sub(r'\(\s*\)', ' ', show_name)
                    # Clean up dashes (leading, trailing, multiple)
                    show_name = re.sub(r'[–—\-]{2,}', ' ', show_name)
                    show_name = re.sub(r'[–—\-]+\s*$', ' ', show_name)
                    show_name = re.sub(r'^\s*[–—\-]+', ' ', show_name)
                    
                    # Clean up spaces
                    show_name = re.sub(r'\s+', ' ', show_name).strip()
                    if show_name:
                        name = show_name

        # Special case: parent-folder-as-show with numeric episode filenames (no season context)
        # Example: "<Show Name> [1971-5]\\02-A Sound of Dolphins.mp4" -> "Show Name 02 A Sound of Dolphins"
        if season is None and episode is None:
            num_title_match = re.match(r'^\s*(\d{1,3})[._\s-]+(.+)$', original_filename)
            # Parent should exist and not be generic placeholders
            parent_is_generic = parent_str and parent_str.lower() in ['movies', 'tv', 'series', 'shows', 'video', 'videos']
            if num_title_match and parent_str and not parent_is_generic:
                leading_num = num_title_match.group(1)
                title_part = num_title_match.group(2).strip()
                
                # Use parent folder as show name
                show_name = parent_str
                # Clean show name (using centralized patterns)
                show_name = re.sub(r'^www\.[^\s]+\.\w+\s*-\s*', '', show_name, flags=re.IGNORECASE)
                for p in QUALITY_SOURCE_PATTERNS:
                    show_name = re.sub(p, ' ', show_name, flags=re.IGNORECASE)
                for p in EDITION_PATTERNS:
                    show_name = re.sub(p, ' ', show_name, flags=re.IGNORECASE)
                
                show_name = re.sub(r'\b(?:NF|WEBRip|WEB-DL|DDP\d+\.?\d*|x264|x265|1080p|720p|480p|4k|uhd)\b', ' ', show_name, flags=re.IGNORECASE)
                show_name = re.sub(r'\b\d+\.\d+\b', ' ', show_name)
                show_name = re.sub(r'[._]+', ' ', show_name)
                show_name = re.sub(r'\[.*?\]', '', show_name)
                show_name = re.sub(r'\(.*?\)', '', show_name)
                show_name = re.sub(r'\bS\d+(?:-\d+)?\b', ' ', show_name, flags=re.IGNORECASE)
                show_name = re.sub(r'\bSeason\s*\d+(?:-\d+)?\b', ' ', show_name, flags=re.IGNORECASE)
                show_name = re.sub(r'-\b[A-Za-z0-9]{2,10}\b\s*$', ' ', show_name)
                
                # Remove language tags
                show_name = re.sub(r'\b(?:japanese|english|french|german|spanish|italian|russian|korean|hindi|eng|dan|ita|en-sub)\b', ' ', show_name, flags=re.IGNORECASE)
                
                # Clean up dashes
                show_name = re.sub(r'[–—\-]{2,}', ' ', show_name)
                show_name = re.sub(r'[–—\-]+\s*$', ' ', show_name)
                show_name = re.sub(r'^\s*[–—\-]+', ' ', show_name)
                show_name = re.sub(r'\s+', ' ', show_name).strip()
                
                # Clean episode title (using centralized patterns)
                episode_title_cleaned = title_part
                for p in QUALITY_SOURCE_PATTERNS:
                    episode_title_cleaned = re.sub(p, ' ', episode_title_cleaned, flags=re.IGNORECASE)
                for p in EDITION_PATTERNS:
                    episode_title_cleaned = re.sub(p, ' ', episode_title_cleaned, flags=re.IGNORECASE)
                episode_title_cleaned = re.sub(r'[._]+', ' ', episode_title_cleaned)
                episode_title_cleaned = re.sub(r'\s+', ' ', episode_title_cleaned).strip()
                
                # Only adopt if show name is valid and different from filename
                # Clean the original filename similarly to compare properly (using centralized patterns)
                original_cleaned = original_filename
                for p in QUALITY_SOURCE_PATTERNS:
                    original_cleaned = re.sub(p, ' ', original_cleaned, flags=re.IGNORECASE)
                for p in EDITION_PATTERNS:
                    original_cleaned = re.sub(p, ' ', original_cleaned, flags=re.IGNORECASE)
                original_cleaned = re.sub(r'[._]+', ' ', original_cleaned)
                original_cleaned = re.sub(r'\b\d+\.\d+\b', ' ', original_cleaned)
                original_cleaned = re.sub(r'\([^)]*\)', '', original_cleaned)
                original_cleaned = re.sub(r'\[.*?\]', '', original_cleaned)
                original_cleaned = re.sub(r'\bS\d+\b', ' ', original_cleaned, flags=re.IGNORECASE)
                original_cleaned = re.sub(r'\bSeason\s*\d+\b', ' ', original_cleaned, flags=re.IGNORECASE)
                original_cleaned = re.sub(r'-\b[A-Za-z0-9]{2,10}\b\s*$', ' ', original_cleaned)
                # Remove year if present
                original_cleaned = re.sub(r'\b(19\d{2}|20[0-2]\d|203[0-5])\b', '', original_cleaned)
                original_cleaned = re.sub(r'\s+', ' ', original_cleaned).strip()
                
                # Also remove year from show_name for comparison
                show_name_cmp = re.sub(r'\b(19\d{2}|20[0-2]\d|203[0-5])\b', '', show_name)
                show_name_cmp = re.sub(r'\s+', ' ', show_name_cmp).strip()

                # Remove the leading number from original_cleaned for comparison
                original_cleaned_no_num = re.sub(r'^\s*\d+\s+', '', original_cleaned).strip()
                
                # Only treat as episode if parent folder is significantly different from filename
                if show_name and show_name_cmp.lower() != original_cleaned_no_num.lower() and show_name_cmp.lower() not in original_cleaned.lower():
                     name = f"{show_name} {int(leading_num):02d} {episode_title_cleaned}"
    
    # Remove season/episode tags from name (they're already extracted)
    # But only if name still contains the original filename (not if we've already set it to show name)
    # Check if name looks like it still has episode info (has SXXEXX or starts with a number)
    if is_full_path and path_obj and (season is not None or episode is not None):
        # Only clean the name if it still looks like the filename (has SXXEXX or starts with number)
        # If we've already set it to the show name, skip this cleaning
        if re.search(r'\bS\d{2}E\d{2}\b', name, re.IGNORECASE) or re.match(r'^\s*\d+', name):
            # This is still the filename, clean it
            name = re.sub(r'\bS\d{2}E\d{2}\b\s*', ' ', name, flags=re.IGNORECASE)
            name = re.sub(r'\bSeason\s*\d+\b', ' ', name, flags=re.IGNORECASE)
            name = re.sub(r'\bE\d+\b(?=\s|$)', ' ', name, flags=re.IGNORECASE)
            name = re.sub(r'\b(?:ep|episode)\s*\d+\b', ' ', name, flags=re.IGNORECASE)
            # Remove leading episode numbers (e.g., "024 S02E01" -> remove "024")
            name = re.sub(r'^\s*\d+\s+', ' ', name)
            # Remove episode numbers that are standalone or after dashes/underscores at the start
            name = re.sub(r'^[_-]\d+(?:\.|$)', ' ', name)
            # Remove trailing dash/underscore followed by episode number (e.g., "Kaiji - 12")
            name = re.sub(r'[\s_-]+\d{1,3}\s*$', ' ', name)
    else:
        # Not a TV series path, clean normally
        name = re.sub(r'\bS\d{2}E\d{2}\b\s*', ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'\bSeason\s*\d+\b', ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'\bE\d+\b(?=\s|$)', ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'\b(?:ep|episode)\s*\d+\b', ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'^[_-]\d+(?:\.|$)', ' ', name)

    # STEP 10: Remove release group suffixes like " -RARBG", " -YTS", " -EVO" at end
    # Require space before dash to avoid removing hyphenated title words like "A-Team"
    name = re.sub(r'\s-\b[A-Za-z0-9]{2,10}\b\s*$', ' ', name)

    # STEP 11: Remove language tags when dashed or standalone (e.g., "- FRENCH")
    name = re.sub(r'[\s\-\_]*\b(eng|english|french|german|spanish|italian|russian|japanese|korean|hindi|dan|ita|en-sub)\b', ' ', name, flags=re.IGNORECASE)

    # STEP 12: Bracket-aware truncation if illegal content found AFTER a leading plain title
    # Pattern: <plain text> <[bracket with forbidden]> <anything>  → keep only <plain text>
    # But DO NOT apply if the name starts with brackets (to avoid losing true title).
    # Supports (), [], {}, <> as brackets
    bracket_any = r'(?:\([^)]*\)|\[[^\]]*\]|\{[^}]*\}|<[^>]*>)'
    m = re.match(r'^(?P<prefix>[^()\[\]{}<>]+?)\s*(?P<bracket>' + bracket_any + r')\s*(?P<suffix>.+)$', name)
    if m:
        prefix = m.group('prefix').strip()
        bracket = m.group('bracket')
        # Extract inner text of the bracket
        inner = re.sub(r'^[\(\[\{<]|[\)\]\}>]$', '', bracket)
        if re.search(forbidden_union, inner, flags=re.IGNORECASE):
            # Only keep prefix; drop bracket and everything after
            name = prefix

    # STEP 13: Normalize leftover punctuation/spaces: remove stray dashes/underscores and extra spaces
    # Preserve dashes that are part of the title (e.g., "L'ultima onda - The Last Wave")
    # Only convert dashes to spaces if they're clearly separators (multiple dashes, or at start/end)
    # Single dashes surrounded by spaces are likely part of the title, so preserve them
    name = re.sub(r'[–—\-]{2,}', ' ', name)  # multiple dashes (separators)
    name = re.sub(r'[–—\-]+\s*$', ' ', name)  # trailing dashes
    name = re.sub(r'^\s*[–—\-]+', ' ', name)  # leading dashes
    name = re.sub(r'\s+', ' ', name).strip(' _-.')

    # STEP 14: Remove "Title1", "Title2", etc. suffixes (common in DVD rips)
    name = re.sub(r'\s+Title\d+\s*$', '', name, flags=re.IGNORECASE)
    
    # STEP 15: Final cleanup for trailing punctuation
    name = re.sub(r'[.\-]+$', '', name).strip()
    
    # Clean up multiple spaces and trim
    name = re.sub(r'\s+', ' ', name).strip()
    
    # If name becomes empty, use original
    if not name:
        name = original_name
    
    # Helper function for smart title casing
    def apply_smart_title_case(text, preserve_single_word_caps=False, force=False):
        """Apply smart title casing that preserves acronyms and handles minor words"""
        if not force and not (text.isupper() or text.islower()):
            return text  # Preserve mixed case
        
        words = text.split()
        
        # If preserve_single_word_caps is True and text is a single word in all caps, keep it
        if preserve_single_word_caps and len(words) == 1 and text.isupper():
            return text
        
        title_cased_words = []
        # Minor words that should be lowercase (unless first/last word)
        minor_words = {'a', 'an', 'and', 'as', 'at', 'about', 'but', 'by', 'for', 'from', 'her', 'him', 'his', 'in', 
                      'into', 'of', 'on', 'or', 'the', 'to', 'with'}
        # Common words that should NOT be treated as acronyms
        common_words = {'the', 'and', 'for', 'from', 'with', 'that', 'this', 'boys', 'girl', 
                       'girls', 'man', 'men', 'boys', 'last', 'first', 'good', 'bad', 'new', 
                       'old', 'big', 'over', 'just', 'only', 'very', 'also', 'back', 'here', 
                       'come', 'some', 'them', 'then', 'than', 'when', 'what', 'your', 'more'}
        
        for i, word in enumerate(words):
            is_first = (i == 0)
            is_last = (i == len(words) - 1)
            
            # Preserve short uppercase words (likely acronyms like "UHF", "TV", "DVD")
            # But NOT if they're common English words
            if len(word) <= 4 and word.isupper() and word.lower() not in common_words:
                title_cased_words.append(word)
            # Keep minor words lowercase unless first/last
            # Some words (like "her") should always be lowercase even when first/last
            elif word.lower() in {'her', 'him', 'his'}:
                title_cased_words.append(word.lower())
            elif word.lower() in minor_words and not is_first and not is_last:
                title_cased_words.append(word.lower())
            else:
                # Use title() but fix apostrophe handling - Python's title() 
                # incorrectly capitalizes after apostrophes (Cuckoo'S instead of Cuckoo's)
                titled = word.title()
                # Fix: lowercase the letter after apostrophe only for possessives/contractions
                # (where there are multiple chars before the apostrophe, like "Cuckoo's")
                # But preserve capitalization for French elisions (like "L'Avare" where L' is an article)
                titled = re.sub(r"(?<=\w{2})'([A-Z])", lambda m: "'" + m.group(1).lower(), titled)
                title_cased_words.append(titled)
        
        return ' '.join(title_cased_words)
    
    # STEP 16: Apply smart title casing (but skip for TV shows - handle those separately)
    # Only apply if the name is not mixed case (to preserve intentional casing like "eBay")
    # But also handle cases where filename cleaning left title-case minor words that should be lowercase
    if season is None and episode is None:
        # Check if name has title-case minor words in the middle that should be lowercase
        # (e.g., "About", "Her" in "2 or 3 Things I Know About Her")
        # NOTE: "the" is excluded from normalization because it often refers to proper nouns in movie titles
        # (e.g., "One Flew Over The Cuckoo's Nest" where "The Cuckoo's Nest" is a proper noun)
        words = name.split()
        minor_words_set = {'a', 'an', 'and', 'as', 'at', 'about', 'but', 'by', 'for', 'from', 'her', 'him', 'his', 'in', 
                          'into', 'of', 'on', 'or', 'to', 'with'}
        always_lowercase_set = {'her', 'him', 'his'}
        # Normalize incorrectly cased minor words to lowercase first
        # But preserve capitalization for words after dashes (they're start of new phrases)
        normalized_words = []
        for i, word in enumerate(words):
            word_lower = word.lower()
            is_first = (i == 0)
            is_last = (i == len(words) - 1)
            # Check if previous word is a dash (indicating new phrase)
            is_after_dash = i > 0 and words[i-1] == '-'
            # Always lowercase words (like "her") should always be lowercase (unless after dash)
            if word_lower in always_lowercase_set and word != word_lower and not is_after_dash:
                normalized_words.append(word_lower)
            # Minor words in the middle should be lowercase (if they're title case), but not after dash
            elif word_lower in minor_words_set and not is_first and not is_last and not is_after_dash and word[0].isupper() and word[1:].islower():
                normalized_words.append(word_lower)
            else:
                normalized_words.append(word)
        name = ' '.join(normalized_words)
        # Apply title case if name is all uppercase, all lowercase, or we normalized some words
        was_normalized = any(words[i] != normalized_words[i] for i in range(len(words)))
        if was_normalized and not (name.isupper() or name.islower()):
            # If we normalized words but name is still mixed case, normalize to lowercase first
            # then apply title case to get proper capitalization
            name = name.lower()
        
        # Check for "Sentence case" (only first char capitalized, rest lowercase)
        # This handles "My fair lady" -> "My Fair Lady"
        is_sentence_case = False
        if len(name) > 0 and name[0].isupper():
             # Check if rest contains any uppercase letters
             if not any(c.isupper() for c in name[1:]):
                 is_sentence_case = True
        
        if name.isupper() or name.islower():
            name = apply_smart_title_case(name)
        elif is_sentence_case:
            name = apply_smart_title_case(name, force=True)
    
    # Format TV series name with season/episode if found
    if season is not None or episode is not None:
        # Apply smart title casing to the show name (if all caps or all lowercase)
        # Preserve single-word all-caps names (like "BEASTARS") as they may be stylized
        name = apply_smart_title_case(name, preserve_single_word_caps=True)
        
        season_str = f"S{season:02d}" if season is not None else ""
        episode_str = f"E{episode:02d}" if episode is not None else ""
        
        # If we have an episode title, include it
        custom_episode_format = False
        if episode_title:
            # For custom episode formats like "Vol1-Episode2", skip cleaning and use custom formatting
            if re.match(r'Vol\d+-Episode\d+', episode_title, re.IGNORECASE):
                episode_title_cleaned = episode_title
                leading_num = None
                # For custom formats, don't add standard EXX prefix
                custom_episode_format = True
            else:
                # Clean the episode title (remove quality tags, etc. but keep the text)
                episode_title_cleaned = episode_title
                # Check for leading number BEFORE any cleaning (e.g., "024 Points of Departure")
                # But only if it's a multi-digit number (to avoid matching single digits from quality tags)
                leading_num_match = re.match(r'^(\d{2,})\s+(.+)$', episode_title_cleaned)
                leading_num = None
                if leading_num_match:
                    leading_num = leading_num_match.group(1)
                    episode_title_cleaned = leading_num_match.group(2).strip()
                
                # Remove quality tag patterns BEFORE normalizing (using centralized patterns)
                for p in QUALITY_SOURCE_PATTERNS:
                    episode_title_cleaned = re.sub(p, ' ', episode_title_cleaned, flags=re.IGNORECASE)
                for p in EDITION_PATTERNS:
                    episode_title_cleaned = re.sub(p, ' ', episode_title_cleaned, flags=re.IGNORECASE)
                # Remove specific patterns that might have dots
                episode_title_cleaned = re.sub(r'\b(?:NF|WEBRip|WEB-DL|DDP\d+\.?\d*|x264|x265|H\.?264|1080p|720p|480p|4k|uhd)\b', ' ', episode_title_cleaned, flags=re.IGNORECASE)
                # Remove release group suffixes (require space before dash to preserve hyphenated words)
                episode_title_cleaned = re.sub(r'\s-\b[A-Za-z0-9]{2,10}\b\s*$', ' ', episode_title_cleaned)
                
                # Now normalize dots/underscores to spaces (for cases like "A.Wolf.in.Sheeps.Clothing")
                episode_title_cleaned = re.sub(r'[._]+', ' ', episode_title_cleaned)
                
                # Remove standalone decimal numbers (like "2.0" from "DDP2.0")
                episode_title_cleaned = re.sub(r'\b\d+\.\d+\b', ' ', episode_title_cleaned)
                # Remove standalone single digits (likely fragments from quality tags like "0" from "DDP2.0")
                # Only remove 0, as 1-9 are often valid parts of titles (e.g., "Part 1", "November 4")
                episode_title_cleaned = re.sub(r'\b0\b', ' ', episode_title_cleaned)
                # Don't remove 2-3 digit numbers if we already extracted them as leading_num
                # Only remove if they're clearly fragments (not if they're the leading number we want to keep)
                if not leading_num:
                    # Remove standalone 2-3 digit numbers that are likely fragments (but preserve multi-digit episode numbers)
                    # But preserve numbers that are part of hyphenated sequences like "9-11"
                    episode_title_cleaned = re.sub(r'(?<![0-9\-])\b\d{2,3}\b(?!\-?\d)(?=\s|$)', ' ', episode_title_cleaned)
                # Clean up spaces
                episode_title_cleaned = re.sub(r'\s+', ' ', episode_title_cleaned).strip()

                # Remove trailing brackets/parentheses - for episode titles, these are almost always
                # release group tags like [Demon], [YIFY], etc. and should be removed
                bracket_pattern = r'\s*[\(\[\{<][^)\]}>]*[\)\]\}>]\s*$'
                episode_title_cleaned = re.sub(bracket_pattern, '', episode_title_cleaned).strip()

                # Clean up stray dashes
                episode_title_cleaned = re.sub(r'[–—\-]{2,}', ' ', episode_title_cleaned)
                episode_title_cleaned = re.sub(r'[–—\-]+\s*$', ' ', episode_title_cleaned)
                episode_title_cleaned = re.sub(r'^\s*[–—\-]+', ' ', episode_title_cleaned)
                episode_title_cleaned = re.sub(r'\s+', ' ', episode_title_cleaned).strip()

                # Apply smart title casing to episode title
                episode_title_cleaned = apply_smart_title_case(episode_title_cleaned)
            
            # Check if leading_num is just the episode number padded and redundant with SxxExx
            if leading_num and season_str and episode_str:
                try:
                    if int(leading_num) == episode:
                        leading_num = None
                except Exception:
                    pass

            # Format with leading number if we found one
            if leading_num:
                if season_str and episode_str:
                    name = f"{name} {leading_num} {season_str}{episode_str} {episode_title_cleaned}"
                elif season_str:
                    name = f"{name} {leading_num} {season_str} {episode_title_cleaned}"
                elif episode_str:
                    # For cases like "02 A Sound of Dolphins", just use leading_num + title (no E02)
                    name = f"{name} {leading_num} {episode_title_cleaned}"
            else:
                # No leading number, just add title after SXXEXX
                if custom_episode_format:
                    # For custom formats, just add the episode title without standard prefixes
                    name = f"{name} {episode_title_cleaned}"
                elif season_str and episode_str:
                    name = f"{name} {season_str}{episode_str} {episode_title_cleaned}"
                elif season_str:
                    name = f"{name} {season_str} {episode_title_cleaned}"
                elif episode_str:
                    name = f"{name} {episode_str} {episode_title_cleaned}"
        else:
            # No episode title, just add season/episode
            if season_str and episode_str:
                name = f"{name} {season_str}{episode_str}"
            elif season_str:
                name = f"{name} {season_str}"
            elif episode_str:
                name = f"{name} {episode_str}"
        # Final cleanup for TV names: remove stray " - <number>" fragments before SxxExx
        name = re.sub(r'\s*-\s*\d{1,3}\s+(?=S\d{2}E\d{2}\b)', ' ', name)
    
    return name, year

def get_video_length(file_path):
    """Extract video length, now using combined metadata extraction for efficiency"""
    duration, _ = extract_video_metadata_with_ffprobe(file_path)
    return duration

def extract_screenshots(video_path, num_screenshots=5, scan_progress_dict=None):
    """
    Extract screenshots from video using ffmpeg.
    
    These are "screenshots" - frames extracted from the video file itself.
    These are generated by us during scanning, not pre-existing files.
    
    Returns a list of screenshot file paths (existing ones immediately, rest queued for background processing).
    """
    return extract_screenshots_core(video_path, num_screenshots, load_config, find_ffmpeg_core, add_scan_log, scan_progress_dict)

def extract_movie_screenshot(video_path, timestamp_seconds=150, priority: str = "normal", subtitle_path=None, movie_id=None):
    """Queue a screenshot extraction for async processing"""
    return extract_movie_screenshot_core(
        video_path, timestamp_seconds,
        load_config, find_ffmpeg_core, scan_progress, add_scan_log, priority, subtitle_path=subtitle_path, movie_id=movie_id
    )

def process_frame_queue(max_workers=3):
    """Process queued frame extractions in background thread pool"""
    process_frame_queue_core(max_workers, scan_progress, add_scan_log)

def index_movie(file_path, db: Session = None, patterns=None):
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
        # Load patterns if not provided (for standalone calls)
        if patterns is None:
            patterns = load_cleaning_patterns()

        # Clean movie name and extract year early (to detect improvements/changes in logic)
        cleaned_name, year = clean_movie_name(normalized_path, patterns)

        # Check if already indexed and unchanged
        existing = db.query(Movie).filter(Movie.path == normalized_path).first()
        file_unchanged = existing and existing.hash == file_hash
        
        # Check if screenshot exists for this movie
        existing_screenshot = None
        if existing:
            existing_screenshot = db.query(Screenshot).filter(Screenshot.movie_id == existing.id).first()
        has_screenshot = existing_screenshot and os.path.exists(existing_screenshot.shot_path) if existing_screenshot else False
        
        # Check if name/year needs update due to code changes
        name_changed = existing and (existing.name != cleaned_name or existing.year != year)
        
        # If file unchanged and screenshot exists, handle potential name update or audio refresh then skip
        if file_unchanged and has_screenshot:
            if existing:
                # If name changed, update it
                if name_changed:
                    existing.name = cleaned_name
                    existing.year = year
                    existing.updated = datetime.now()
                    add_scan_log("info", f"  Updated name: {existing.name}")
                
                # Refresh audio info (always do this as it's fast and might be missing)
                _, audio_types = extract_video_metadata_with_ffprobe(normalized_path)
                _refresh_movie_audio_rows(db, existing.id, audio_types)
                
                db.commit()
                
                # If name changed, count as updated
                if name_changed:
                    return True
            return False  # No other updates needed
        
        add_scan_log("info", f"  Getting file metadata...")
        
        stat = os.stat(normalized_path)
        created = datetime.fromtimestamp(stat.st_ctime)
        size = stat.st_size
        
        # Exclude files smaller than minimum threshold
        if size < MIN_FILE_SIZE_BYTES:
            add_scan_log("warning", f"  Skipping (too small: {size / (1024*1024):.1f}MB; requires >= 50MB)")
            # If it exists in DB already, remove it to enforce exclusion
            if existing:
                try:
                    # Delete related screenshots first
                    db.query(Screenshot).filter(Screenshot.movie_id == existing.id).delete()
                    db.delete(existing)
                    db.commit()
                    add_scan_log("info", f"  Removed existing DB entry for small file")
                except Exception:
                    db.rollback()
            return False
        
        # Extract both video duration and audio types in single ffprobe call
        length, audio_types = extract_video_metadata_with_ffprobe(normalized_path)

        # Exclude files shorter than 60 seconds when length is known
        if length is not None and length < 60:
            add_scan_log("warning", f"  Skipping (too short: {length:.1f}s)")
            # If it exists in DB already, remove it to enforce exclusion
            if existing:
                try:
                    # Delete related screenshots first
                    db.query(Screenshot).filter(Screenshot.movie_id == existing.id).delete()
                    db.delete(existing)
                    db.commit()
                    add_scan_log("info", f"  Removed existing DB entry for short file")
                except Exception:
                    db.rollback()
            return False
        
        # Find images in folder
        # "images" = media files that came with the movie (posters, covers, etc.)
        add_scan_log("info", f"  Searching for images in folder...")
        images = find_images_in_folder(normalized_path)
        if images:
            add_scan_log("success", f"  Found {len(images)} image(s)")
        
        # Check for existing screenshots in database
        existing_screenshots_list = []
        if existing:
            existing_screenshots_list = [s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == existing.id).all()]
            # Filter to only those that still exist on disk
            existing_screenshots_list = [s for s in existing_screenshots_list if os.path.exists(s)]
        
        # We only take one screenshot per movie now. Use any existing one if present.
        screenshots = existing_screenshots_list
        if len(screenshots) > 0:
            add_scan_log("info", f"  Using existing screenshot")
        
        # Filter out YTS images before processing (defense in depth)
        images = filter_yts_images(images)
        
        # cleaned_name and year are already calculated at the start of function
        
        # Create or update movie record FIRST - we need movie.id before queuing screenshots
        if existing:
            # Update existing movie
            existing.name = cleaned_name
            existing.year = year
            existing.length = length
            existing.size = size
            existing.hash = file_hash
            existing.updated = datetime.now()
            movie = existing
            # Flush to ensure we have the latest state
            db.flush()
        else:
            # Create new movie
            movie = Movie(
                path=normalized_path,
                name=cleaned_name,
                year=year,
                length=length,
                created=created,
                size=size,
                hash=file_hash
            )
            db.add(movie)
            db.flush()  # Flush to get movie.id
            add_scan_log("success", f"New Movie Discovered: {cleaned_name}")
        
        # Determine movie.image_path: find largest image, or generate fallback screenshot
        selected_image_path = None
        is_fallback_screenshot = False
        
        if images:
            # Find largest image by file size
            largest_image = None
            largest_size = 0
            for img_path in images:
                try:
                    if os.path.exists(img_path):
                        size = os.path.getsize(img_path)
                        if size > largest_size:
                            largest_size = size
                            largest_image = img_path
                except Exception:
                    continue
            
            if largest_image:
                selected_image_path = str(Path(largest_image).resolve())
                add_scan_log("info", f"  Selected largest image: {Path(largest_image).name}")
        
        # If no image found, check for or generate fallback screenshot at 300s
        if not selected_image_path:
            from video_processing import generate_screenshot_filename, SCREENSHOT_DIR
            fallback_screenshot_path = generate_screenshot_filename(normalized_path, timestamp_seconds=300, movie_id=movie.id)
            
            # Check if fallback screenshot already exists
            if fallback_screenshot_path.exists():
                selected_image_path = str(fallback_screenshot_path.resolve())
                is_fallback_screenshot = True
                add_scan_log("info", f"  Using existing fallback screenshot at 300s")
            else:
                # Queue fallback screenshot generation (will be processed asynchronously)
                # Store the expected path - it will be filled in once generated
                selected_image_path = str(fallback_screenshot_path.resolve())
                is_fallback_screenshot = True
                add_scan_log("info", f"  No image found, queuing fallback screenshot at 300s...")
                extract_movie_screenshot(normalized_path, timestamp_seconds=300, movie_id=movie.id)
        
        # Update movie.image_path:
        # - Always update if not set
        # - Update if current file is missing
        # - Update if we found a real image (not fallback screenshot) - allows upgrading from fallback to real image
        # - Protect fallback screenshots from being overwritten by other fallback screenshots
        
        # Check if current image_path is the expected fallback screenshot path (robust path comparison)
        current_is_fallback = False
        if movie.image_path:
            try:
                expected_fallback_path = str(generate_screenshot_filename(normalized_path, timestamp_seconds=300, movie_id=movie.id).resolve())
                current_path_resolved = str(Path(movie.image_path).resolve())
                current_is_fallback = current_path_resolved == expected_fallback_path
            except Exception:
                # If path resolution fails, fall back to filename check
                current_is_fallback = '_screenshot300s.jpg' in movie.image_path
        
        should_update = (
            not movie.image_path or 
            not os.path.exists(movie.image_path) or
            (not is_fallback_screenshot and not current_is_fallback) or  # Real image can replace real image
            (not is_fallback_screenshot and current_is_fallback)  # Real image can replace fallback
        )
        
        if should_update:
            movie.image_path = selected_image_path
        
        # Extract one movie screenshot (~3 minutes = 180 seconds) for screenshots table
        # This is separate from the image_path fallback screenshot at 300s
        # Skip if we already have a fallback screenshot at 300s (to avoid duplicate generation)
        add_scan_log("info", f"  Checking screenshot...")
        if existing_screenshot:
            # Check if the screenshot file still exists
            if os.path.exists(existing_screenshot.shot_path):
                add_scan_log("info", f"  Screenshot already exists")
            else:
                # Screenshot file was deleted, remove from DB and queue for re-extraction
                add_scan_log("warning", f"  Screenshot file missing, queuing re-extraction...")
                db.delete(existing_screenshot)
                # Only queue if we don't already have a fallback screenshot queued/generated
                if not is_fallback_screenshot:
                    extract_movie_screenshot(normalized_path, timestamp_seconds=180, movie_id=movie.id)
        else:
            # No screenshot exists, queue for extraction
            # Skip if we already queued a fallback screenshot at 300s (avoid duplicate)
            if not is_fallback_screenshot:
                add_scan_log("info", f"  No screenshot found, queuing extraction at 180s...")
                extract_movie_screenshot(normalized_path, timestamp_seconds=180, movie_id=movie.id)
            else:
                add_scan_log("info", f"  Skipping 180s screenshot (fallback at 300s already queued)")
        
        # Store existing screenshot in DB (background worker adds one if queued above)
        if screenshots:
            # Keep only one path
            shot_path = screenshots[0]
            existing_shot_paths = {s.shot_path for s in db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()}
            if shot_path not in existing_shot_paths:
                # Extract timestamp from filename if possible (format: movie_name_screenshot150s.jpg)
                timestamp_seconds = None
                try:
                    import re
                    match = re.search(r'_screenshot(\d+)s\.jpg$', shot_path)
                    if match:
                        timestamp_seconds = float(match.group(1))
                except Exception:
                    pass
                screenshot = Screenshot(movie_id=movie.id, shot_path=shot_path, timestamp_seconds=timestamp_seconds)
                db.add(screenshot)

        # Refresh audio metadata (languages available) - already extracted above
        _refresh_movie_audio_rows(db, movie.id, audio_types)
        
        db.commit()
        return True
    finally:
        if should_close:
            db.close()

def scan_directory(root_path, state=None, progress_callback=None):
    """Scan directory for video files with optional progress callback
    Each movie commits individually - no transaction wrapping the scan.
    If any movie fails, the entire scan stops immediately.
    """
    root = Path(root_path)
    if not root.exists():
        add_scan_log("error", f"Path does not exist: {root_path}")
        raise ValueError(f"Path does not exist: {root_path}")
    
    add_scan_log("info", f"Starting scan of: {root_path}")
    db = SessionLocal()
    
    # Load cleaning patterns once to pass to index_movie
    # This avoids querying the DB for patterns for every single file
    patterns = load_cleaning_patterns()
    
    try:
        # First pass: count total files
        global scan_progress
        scan_progress["status"] = "counting"
        scan_progress["current_file"] = "Counting files..."
        add_scan_log("info", "Counting video files...")
        
        total_files = 0
        for ext in VIDEO_EXTENSIONS:
            files = [
                f for f in root.rglob(f"*{ext}")
                if not is_sample_file(f) and os.path.getsize(f) >= MIN_FILE_SIZE_BYTES
            ]
            count = len(files)
            total_files += count
            if count > 0:
                add_scan_log("info", f"Found {count} {ext} files")
        
        scan_progress["total"] = total_files
        scan_progress["current"] = 0
        scan_progress["status"] = "scanning"
        scan_progress["movies_added"] = 0
        scan_progress["movies_updated"] = 0
        scan_progress["movies_removed"] = 0
        add_scan_log("success", f"Total files to process: {total_files}")
        
        indexed = 0
        updated = 0
        
        # Track newly added movies for movie list reconciliation
        new_movies_for_reconciliation = []
        
        # Second pass: actually scan
        # Each movie commits individually - no transaction wrapping the scan
        # If any movie fails, the entire scan stops immediately
        add_scan_log("info", "Starting file processing...")
        for ext in VIDEO_EXTENSIONS:
            if shutdown_flag.is_set():
                add_scan_log("warning", "Scan interrupted by shutdown")
                break
            for file_path in root.rglob(f"*{ext}"):
                if shutdown_flag.is_set():
                    add_scan_log("warning", "Scan interrupted by shutdown")
                    break
                
                # Skip sample files
                if is_sample_file(file_path):
                    add_scan_log("info", f"Skipping sample file: {file_path.name}")
                    continue
                
                # Process each movie - if it fails, stop the entire scan immediately
                # Each movie commits individually, no transaction wrapping the whole scan
                scan_progress["current"] = indexed + 1
                scan_progress["current_file"] = file_path.name
                
                add_scan_log("info", f"[{indexed + 1}/{total_files}] Processing: {file_path.name}")
                
                # Check if movie already exists to track add vs update
                normalized_path = str(file_path.resolve() if hasattr(file_path, 'resolve') else Path(file_path).resolve())
                movie_existed = db.query(Movie).filter(Movie.path == normalized_path).first() is not None
                
                was_updated = index_movie(file_path, db, patterns)
                if was_updated:
                    if movie_existed:
                        scan_progress["movies_updated"] += 1
                    else:
                        scan_progress["movies_added"] += 1
                        # Track newly added movie for reconciliation
                        new_movie = db.query(Movie).filter(Movie.path == normalized_path).first()
                        if new_movie:
                            new_movies_for_reconciliation.append({
                                'id': new_movie.id,
                                'name': new_movie.name,
                                'year': new_movie.year
                            })
                    updated += 1
                indexed += 1
                
                if progress_callback:
                    progress_callback(indexed, total_files, file_path.name)
        
        # Mark path as indexed
        stmt = sqlite_insert(IndexedPath).values(path=str(root_path))
        # Upsert on UNIQUE(path): update the 'updated' timestamp if it exists
        stmt = stmt.on_conflict_do_update(
            index_elements=[IndexedPath.path],
            set_={"updated": func.now()}
        )
        db.execute(stmt)
        db.commit()
        
        # Build summary message with counts
        summary_parts = [f"{indexed} files processed"]
        if scan_progress["movies_added"] > 0:
            summary_parts.append(f"{scan_progress['movies_added']} added")
        if scan_progress["movies_updated"] > 0:
            summary_parts.append(f"{scan_progress['movies_updated']} updated")
        add_scan_log("success", f"Scan complete: {', '.join(summary_parts)}")
        
        # Clean up orphaned database entries (movies whose files no longer exist)
        add_scan_log("info", "Checking for orphaned database entries...")
        root_path_str = str(root_path)
        # Find all movies in database that start with this root path
        orphaned_movies = []
        all_movies_in_path = db.query(Movie).filter(
            Movie.path.like(f"{root_path_str}%")
        ).all()
        
        for movie in all_movies_in_path:
            if not os.path.exists(movie.path):
                orphaned_movies.append(movie)
        
        add_scan_log("info", f"Scanned {len(all_movies_in_path)} movies, found {len(orphaned_movies)} with missing files")
        
        if orphaned_movies:
            add_scan_log("warning", f"Found {len(orphaned_movies)} movies with missing files, removing from database...")
            removed_count = 0
            failed_count = 0
            for movie in orphaned_movies:
                try:
                    # Delete related screenshots first
                    screenshots = db.query(Screenshot).filter(Screenshot.movie_id == movie.id).all()
                    for screenshot in screenshots:
                        # Try to delete screenshot file from disk if it exists
                        if screenshot.shot_path and os.path.exists(screenshot.shot_path):
                            try:
                                os.remove(screenshot.shot_path)
                            except Exception as e:
                                logger.debug(f"Could not delete screenshot file {screenshot.shot_path}: {e}")
                        db.delete(screenshot)
                    
                    # Delete other related records
                    db.query(Rating).filter(Rating.movie_id == movie.id).delete()
                    db.query(MovieStatus).filter(MovieStatus.movie_id == movie.id).delete()
                    db.query(LaunchHistory).filter(LaunchHistory.movie_id == movie.id).delete()
                    db.query(PlaylistItem).filter(PlaylistItem.movie_id == movie.id).delete()
                    db.query(MovieAudio).filter(MovieAudio.movie_id == movie.id).delete()
                    
                    # Unlink from movie lists (mark as not-in-library)
                    unlink_movie_from_lists(db, movie.id)
                    
                    # Delete the movie itself
                    db.delete(movie)
                    
                    # Commit each deletion individually so one failure doesn't affect others
                    db.commit()
                    removed_count += 1
                    scan_progress["movies_removed"] += 1
                    
                    if removed_count <= 10 or removed_count % 50 == 0:
                        add_scan_log("info", f"Removed: {movie.name} (file not found: {Path(movie.path).name})")
                    
                except Exception as e:
                    logger.error(f"Error removing orphaned movie {movie.name}: {e}")
                    db.rollback()
                    failed_count += 1
            
            if failed_count > 0:
                add_scan_log("warning", f"Failed to remove {failed_count} orphaned movie(s)")
            add_scan_log("success", f"Removed {removed_count} orphaned movie(s) from database")
        else:
            add_scan_log("info", "No orphaned entries found")
        
        # Reconcile movie lists with newly added movies
        if new_movies_for_reconciliation:
            try:
                reconcile_result = reconcile_movie_lists(db, new_movies_for_reconciliation)
                if reconcile_result["matched_count"] > 0:
                    add_scan_log("success", f"Movie list reconciliation: {reconcile_result['matched_count']} items matched across {reconcile_result['lists_updated']} lists")
            except Exception as e:
                logger.error(f"Error reconciling movie lists: {e}")
                add_scan_log("warning", f"Movie list reconciliation failed: {e}")
        
        # After scan completes, enqueue screenshot jobs for movies without screenshots
        add_scan_log("info", "Checking for movies without screenshots...")
        movies_without_screenshots = db.query(Movie).outerjoin(
            Screenshot, Movie.id == Screenshot.movie_id
        ).filter(
            Screenshot.id == None
        ).all()
        
        if movies_without_screenshots:
            add_scan_log("info", f"Found {len(movies_without_screenshots)} movies without screenshots, enqueueing initial screenshot at 5-minute mark...")
            enqueued_count = 0
            skipped_count = 0
            
            for movie in movies_without_screenshots:
                if shutdown_flag.is_set():
                    add_scan_log("warning", "Screenshot enqueueing interrupted by shutdown")
                    break
                
                # Skip if movie length is too short (less than 5 minutes)
                if movie.length and movie.length < 300:
                    skipped_count += 1
                    continue
                
                # Enqueue screenshot at 5-minute mark (300 seconds)
                try:
                    result = extract_movie_screenshot(
                        movie.path,
                        timestamp_seconds=300,
                        priority="low",  # Low priority for background work
                        movie_id=movie.id
                    )
                    if result is None:
                        # None means it was queued successfully
                        enqueued_count += 1
                        if enqueued_count <= 10 or enqueued_count % 50 == 0:
                            add_scan_log("info", f"Enqueued screenshot for {movie.name} (total: {enqueued_count})")
                    elif isinstance(result, str):
                        # String means screenshot already exists (shouldn't happen, but handle it)
                        skipped_count += 1
                except Exception as e:
                    logger.warning(f"Failed to enqueue screenshot for movie_id={movie.id}, path={movie.path}: {e}", exc_info=True)
                    skipped_count += 1
            
            add_scan_log("success", f"Screenshot enqueueing complete: {enqueued_count} enqueued, {skipped_count} skipped")
            if enqueued_count > 0:
                add_scan_log("info", f"Screenshots will be generated in background. Queue size: {frame_extraction_queue.qsize()}")
        else:
            add_scan_log("info", "All movies already have screenshots")
        
        return {"indexed": indexed, "updated": updated}
    finally:
        db.close()


def reconcile_movie_lists(db: Session, new_movies: list) -> dict:
    """
    Reconcile AI-generated movie lists with newly added/renamed library movies.
    
    When movies are added to the library or renamed, check if any MovieListItem
    entries that were marked as 'not in library' now match. If so, update them.
    
    Args:
        db: Database session
        new_movies: List of dicts with 'id', 'name', 'year' for newly added/renamed movies
    
    Returns:
        dict with 'matched_count' and 'lists_updated'
    """
    if not new_movies:
        return {"matched_count": 0, "lists_updated": 0}
    
    # Load all not-in-library items (these are candidates for matching)
    missing_items = db.query(MovieListItem).filter(
        MovieListItem.is_in_library == False
    ).all()
    
    if not missing_items:
        return {"matched_count": 0, "lists_updated": 0}
    
    add_scan_log("info", f"Reconciling {len(new_movies)} new movies against {len(missing_items)} missing list items...")
    
    # Build lookup map for new movies: normalized_name -> list of (movie_id, year)
    new_movie_map = {}
    for m in new_movies:
        norm_name = re.sub(r'[^\w\s]', '', m['name']).lower().strip()
        if norm_name not in new_movie_map:
            new_movie_map[norm_name] = []
        new_movie_map[norm_name].append({'id': m['id'], 'year': m.get('year')})
    
    # Track which lists need their counts updated
    lists_to_update = set()
    matched_count = 0
    
    for item in missing_items:
        # Normalize the list item's title
        norm_title = re.sub(r'[^\w\s]', '', item.title).lower().strip()
        
        # Try exact match first
        candidates = new_movie_map.get(norm_title, [])
        
        # If no exact match, try fuzzy match
        if not candidates and new_movie_map:
            best_match = fuzz_process.extractOne(
                norm_title, 
                list(new_movie_map.keys()), 
                scorer=fuzz.token_sort_ratio
            )
            if best_match:
                match_name, score = best_match
                if score > 85:
                    candidates = new_movie_map[match_name]
        
        if not candidates:
            continue
        
        # Disambiguate by year if needed
        match = None
        if len(candidates) == 1:
            match = candidates[0]
        elif item.year:
            # Try to find one with matching year (within 1 year tolerance)
            for cand in candidates:
                if cand['year'] and abs(cand['year'] - item.year) <= 1:
                    match = cand
                    break
            # If no year match, just take the first candidate
            if not match:
                match = candidates[0]
        else:
            match = candidates[0]
        
        if match:
            # Update the item
            item.movie_id = match['id']
            item.is_in_library = True
            item.updated = datetime.now()
            lists_to_update.add(item.movie_list_id)
            matched_count += 1
            add_scan_log("success", f"  Matched list item '{item.title}' to library movie id={match['id']}")
    
    # Update the in_library_count for affected lists
    for list_id in lists_to_update:
        movie_list = db.query(MovieList).filter(MovieList.id == list_id).first()
        if movie_list:
            # Recount in-library items
            in_lib_count = db.query(MovieListItem).filter(
                MovieListItem.movie_list_id == list_id,
                MovieListItem.is_in_library == True
            ).count()
            movie_list.in_library_count = in_lib_count
            movie_list.updated = datetime.now()
    
    if matched_count > 0:
        db.commit()
        add_scan_log("success", f"Reconciled {matched_count} movie list item(s) across {len(lists_to_update)} list(s)")
    
    return {"matched_count": matched_count, "lists_updated": len(lists_to_update)}


def unlink_movie_from_lists(db: Session, movie_id: int) -> int:
    """
    Unlink a movie from all movie lists before deletion.
    
    When a movie is about to be deleted from the library, this function:
    1. Finds all MovieListItem entries referencing this movie
    2. Sets is_in_library=False (movie_id will be SET NULL by FK cascade)
    3. Updates the parent MovieList.in_library_count
    
    Call this BEFORE deleting the movie.
    
    Args:
        db: Database session
        movie_id: ID of the movie being deleted
    
    Returns:
        Number of list items updated
    """
    # Find all list items referencing this movie
    affected_items = db.query(MovieListItem).filter(
        MovieListItem.movie_id == movie_id
    ).all()
    
    if not affected_items:
        return 0
    
    # Track which lists need count updates
    lists_to_update = set()
    
    for item in affected_items:
        item.is_in_library = False
        item.movie_id = None  # Explicitly set to NULL (FK cascade would do this anyway)
        item.updated = datetime.now()
        lists_to_update.add(item.movie_list_id)
    
    # Update the in_library_count for affected lists
    for list_id in lists_to_update:
        movie_list = db.query(MovieList).filter(MovieList.id == list_id).first()
        if movie_list:
            # Recount in-library items
            in_lib_count = db.query(MovieListItem).filter(
                MovieListItem.movie_list_id == list_id,
                MovieListItem.is_in_library == True
            ).count()
            movie_list.in_library_count = in_lib_count
            movie_list.updated = datetime.now()
    
    # Note: Don't commit here - let the caller commit after deleting the movie
    return len(affected_items)


def run_scan_async(root_path: str):
    """Run scan in background thread"""
    global scan_progress, frame_extraction_queue
    try:
        if shutdown_flag.is_set():
            return
        scan_progress["is_scanning"] = True
        scan_progress["current"] = 0
        scan_progress["total"] = 0
        scan_progress["current_file"] = ""
        scan_progress["status"] = "starting"
        scan_progress["logs"] = []  # Clear previous logs
        scan_progress["frames_processed"] = 0
        scan_progress["frames_total"] = 0
        scan_progress["movies_added"] = 0
        scan_progress["movies_updated"] = 0
        scan_progress["movies_removed"] = 0
        
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
        if scan_progress["movies_added"] > 0:
            add_scan_log("info", f"  Movies added: {scan_progress['movies_added']}")
        if scan_progress["movies_updated"] > 0:
            add_scan_log("info", f"  Movies updated: {scan_progress['movies_updated']}")
        if scan_progress["movies_removed"] > 0:
            add_scan_log("info", f"  Movies removed: {scan_progress['movies_removed']}")
        queue_size = frame_extraction_queue.qsize()
        if queue_size > 0:
            add_scan_log("info", f"  Frames queued: {queue_size} (processing in background)")
        add_scan_log("info", "=" * 60)
        
        scan_progress["status"] = "complete"
        scan_progress["is_scanning"] = False
        logger.info(f"Scan complete: {result}")
    except Exception as e:
        # If any movie fails, stop the entire scan immediately
        # Log the error and mark scan as failed - don't continue processing
        error_msg = str(e)
        add_scan_log("error", f"Scan failed: {error_msg}")
        scan_progress["status"] = "error"
        scan_progress["is_scanning"] = False
        logger.error(f"Scan failed: {e}", exc_info=True)
        # Don't re-raise in background thread - just stop and report error

