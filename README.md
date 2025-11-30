# Movie Searcher

A personal video library browser. Faster than Netflix, Amazon, Plex. Instant scrubbing, instant loading, instant stopping. Changes how you watch movies.

## Quick Start

**Windows:** Double-click `run.bat`

**Any platform:** 
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python start.py
```

That's it. The launcher handles everything else (ffmpeg, VLC detection/installation).

## First Time Setup

1. Create a `movies` folder in the project directory (or configure a custom path)
2. Click "Scan Movies Folder" in the web interface
3. Install VLC Media Player for playback (auto-installed on Windows if missing)

## Per-Machine Configuration

Edit `settings.json` to configure paths for each machine:

```json
{
  "movies_folder": "D:\\Movies",
  "local_target_folder": "C:\\LocalMovies"
}
```

This file is gitignored, so each installation can have its own paths.

## Stopping

- Press `Ctrl+C` in the server window, or
- Run `python stop.py`

---

## Speed

No slow loading. No waiting. Type and see results immediately. Launch movies instantly. Faster than streaming services because everything runs locally on your computer.

Instant scrubbing. Click any screenshot thumbnail to jump to that exact moment. Visual timeline browsing changes the movie watching experience.

## Visual Browsing

Every movie has an image. Uses existing artwork when available. If none exists, extracts screenshots from video files automatically.

**Screenshot sophistication:**
- Extracts frames at precise timestamps
- With subtitle files: burns actual subtitle text onto screenshots at that moment
- Handles multiple languages, encoding issues, multiline text
- Screenshots show timestamps so you know what moment they capture

Generate screenshots every few minutes across the entire film. See the visual artistry unfold. Browse the composition, lighting, and mood of each scene. Click any screenshot to launch at that exact moment and quickly see the scenes you care about.

Click any movie to see full gallery. Hover screenshots to launch at that exact moment.

## Launch at Any Spot

Click any screenshot thumbnail to launch the movie at that exact timestamp. Visual guides show you where you're starting. Jump to any moment instantly.

Launch with subtitles automatically detected. Select which subtitle file if multiple exist. Currently playing indicator shows what's running without switching windows.

## Organization

[![Explore Movies](docs/explore-header.png)](#organization)

Filter by alphabet, year, audio language, watch status. Combine filters. Browse unwatched films with Spanish audio from the 1980s. Find exactly what you want.

Open folder in file explorer. Manage your files directly. Trash what you don't want. Share what you love.

## Watch Status

Mark movies as watched or unwatched with a single click. Your watch history persists across sessions. Filter your collection to show only watched movies, only unwatched ones, or everything.

View your complete watch history to see what you've been watching and when. Track your viewing patterns over time. Use watch status to plan your next viewing session or revisit favorites.

## Ratings

Rate movies with star ratings from 1 to 5 stars. Your ratings are saved and displayed throughout the interface. See which movies you've rated highly at a glance.

Filter and organize by rating. Find your favorites quickly. Build a personal collection of rated films.

## Search

Type any part of a movie name. See results instantly as you type. Autocomplete suggests titles. No loading screens.

## Technical Details

Requires Python 3.8+. Optional: VLC Media Player for playback.

**Automatic Setup:**
- Python: https://www.python.org/downloads/
- ffmpeg: **Automatically installed and configured** - no manual setup needed!
- VLC: **Automatically installed on Windows** if missing
- See `docs/installation.md` for detailed setup instructions
