"""
Check audio language for 30 random movies using ffprobe.
"""
import os
import subprocess
import random
import json
from pathlib import Path
import sys

# Add parent directory to path to import database modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import SessionLocal, Movie, Config
from video_processing import _get_ffprobe_path_from_config

def get_audio_languages(file_path, ffprobe_path):
    """Extract audio language information from video file using ffprobe"""
    try:
        cmd = [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "a",  # Select all audio streams
            "-show_entries", "stream=index:stream_tags=language",
            "-of", "json",
            str(file_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None, f"ffprobe error: {result.stderr.strip()}"
        
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        
        languages = []
        for stream in streams:
            tags = stream.get("tags", {})
            lang = tags.get("language", "unknown")
            stream_index = stream.get("index", "?")
            languages.append(f"Stream {stream_index}: {lang}")
        
        if not languages:
            return None, "No audio streams found"
        
        return languages, None
    except subprocess.TimeoutExpired:
        return None, "Timeout"
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    except Exception as e:
        return None, f"Error: {e}"

def main():
    # Get ffprobe path
    ffprobe = _get_ffprobe_path_from_config()
    if not ffprobe:
        print("ERROR: ffprobe not found. Please configure ffmpeg_path in settings.")
        return
    
    print(f"Using ffprobe: {ffprobe}\n")
    
    # Get all movies from database
    db = SessionLocal()
    try:
        all_movies = db.query(Movie).all()
        total = len(all_movies)
        
        if total == 0:
            print("No movies found in database.")
            return
        
        print(f"Total movies in database: {total}")
        
        # Select 30 random movies (or all if less than 30)
        sample_size = min(30, total)
        random_movies = random.sample(all_movies, sample_size)
        
        print(f"Checking {sample_size} random movies:\n")
        print("=" * 80)
        
        results = []
        for i, movie in enumerate(random_movies, 1):
            print(f"\n[{i}/{sample_size}] {movie.name}")
            print(f"  Path: {movie.path}")
            
            # Check if file exists
            if not os.path.exists(movie.path):
                print(f"  Language: FILE NOT FOUND")
                results.append((movie.name, "FILE NOT FOUND"))
                continue
            
            # Get audio languages
            languages, error = get_audio_languages(movie.path, ffprobe)
            if error:
                print(f"  Language: {error}")
                results.append((movie.name, error))
            elif languages:
                lang_str = ", ".join(languages)
                print(f"  Language: {lang_str}")
                results.append((movie.name, lang_str))
            else:
                print(f"  Language: No audio streams")
                results.append((movie.name, "No audio streams"))
        
        print("\n" + "=" * 80)
        print("\nSummary:")
        print("=" * 80)
        
        # Count languages
        lang_counts = {}
        for name, lang_info in results:
            if ":" in lang_info and "Stream" in lang_info:
                # Extract language codes
                parts = lang_info.split(", ")
                for part in parts:
                    if ":" in part:
                        lang_code = part.split(": ")[1].strip()
                        lang_counts[lang_code] = lang_counts.get(lang_code, 0) + 1
            elif lang_info not in ["FILE NOT FOUND", "No audio streams"] and "error" not in lang_info.lower() and "timeout" not in lang_info.lower():
                lang_counts[lang_info] = lang_counts.get(lang_info, 0) + 1
        
        if lang_counts:
            print("\nLanguage distribution:")
            for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
                print(f"  {lang}: {count}")
        
        # Count errors/unknown
        errors = sum(1 for _, info in results if "error" in info.lower() or "timeout" in info.lower() or "FILE NOT FOUND" in info or "No audio streams" in info)
        if errors > 0:
            print(f"\nErrors/Unknown: {errors}")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()

