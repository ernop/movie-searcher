# Movie Searcher

Simple movie search and management application for local movie collections. Perfect for managing movies on a removable drive.

## Quick Start

1. **Setup on Removable Drive:**
   - Copy all project files to your removable drive
   - Create a `movies` folder in the same directory
   - Put your movie files in the `movies` folder

2. **Run the Application:**
   - Double-click `start.bat`
   - If the server isn't running, it will start automatically in a separate window
   - If the server is already running, it will just open the browser
   - A browser window will open to the control page

3. **First Time Setup:**
   - Click "Scan Movies Folder" to index your movies
   - Wait for the scan to complete
   - Start searching and watching!

**Note:** The server runs continuously in the background. You can minimize the server window - it will keep running. Double-click `start.bat` anytime to open the control page.

## Features

- **Fast, simple search with autocomplete**
  - Instant results as you type
  - Autocomplete suggestions for quick selection
  - Search limited to a sensible number of matches for responsiveness (defaults to 50)

- **Incremental, reliable indexing**
  - One-time deep scan of your library, then only re-indexes changed files
  - File change detection via modified time and size
  - Supports common video formats: `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv`, `.flv`, `.webm`, `.m4v`, `.mpg`, `.mpeg`, `.3gp`
  - Extracts key metadata such as video length (via Mutagen)

- **Playback via VLC (Windows)**
  - Launches videos directly in VLC from the browser
  - Auto-detects VLC in common installation paths and via PATH
  - Starts VLC without blocking the server

- **Subtitle handling**
  - Automatic subtitle discovery next to the video
  - Supports `.srt`, `.sub`, `.vtt`, `.ass`, `.ssa`
  - Manual selection when multiple subtitle files are available

- **Watched and history tracking**
  - Mark movies as watched/unwatched
  - Browse your watch history
  - Launch/search history kept to a practical recent window (defaults to last 100 entries)

- **Clean, static web UI**
  - Works locally in your browser
  - Shows posters/images when available
  - Falls back to extracted screenshots when images are not present (if ffmpeg is available)

- **Practical performance limits**
  - Search results intentionally capped to keep the app responsive
  - Autocomplete suggestions limited to a small, useful set

- **Durable, local storage**
  - Local SQLite database for library state and history
  - Data lives in the project folder with your app (portable on the same machine)

## File Structure

```
removable-drive/
├── start.bat              # Double-click to start/open browser
├── stop.bat               # Double-click to stop server
├── main.py                # Server application
├── index.html             # Control page UI
├── requirements.txt       # Python dependencies
├── movies/                # Your movie files go here
│   ├── movie1.mp4
│   ├── movie1.srt        # Subtitles (optional)
│   └── ...
├── movie_searcher.db      # Auto-generated SQLite database
├── movie_index.json       # (If present) legacy JSON index - migrated on first run
├── search_history.json    # (If present) legacy JSON history - migrated on first run
└── logs/                  # Log files (may be created automatically)
```

## Requirements

- **Python 3.8+** (will be checked automatically)
- **VLC Media Player** (optional, only required for launching playback)
- **Windows** (for start.bat script)

## Usage

### Scanning Movies

Click the "Scan Movies Folder" button to index all movies in the `movies` folder. This only needs to be done:
- First time setup
- After adding new movies
- The scan is incremental - only new/changed files are processed

### Searching

Type in the search box to find movies. Results appear instantly with autocomplete suggestions.

### Launching Movies

Click the "Launch" button next to any movie to open it in VLC (Windows). If a subtitle file is found automatically, it will be loaded. You can also manually select a subtitle from the dropdown if multiple are available.

### Tracking Watched Movies

- Click "Mark Watched" to mark a movie as watched
- Click "View Watched" to see all watched movies
- Use the "Show only watched movies" filter to filter search results
- Click "Mark Unwatched" to remove from watched list

### Subtitles

The application automatically detects subtitle files in the same directory as the movie:
- Looks for files with the same name (e.g., `movie.mp4` and `movie.srt`)
- Supports: `.srt`, `.sub`, `.vtt`, `.ass`, `.ssa`
- If multiple subtitles are found, use the dropdown to select one

## How it works (at a glance)

- **Architecture:** FastAPI backend with a static HTML/JS frontend
- **Indexing:** One-time deep scan followed by incremental updates using file metadata
- **Metadata:** Video length extracted via Mutagen; images/screenshots associated for display
- **Storage:** SQLite database holds the library, history, and state
- **Playback:** VLC is detected and launched in a separate process on Windows
- **Limits:** Search results capped for responsiveness; history trimmed to recent entries

## Stopping the Server

The server runs continuously in a separate window titled "Movie Searcher Server". To stop it:
- Close the "Movie Searcher Server" window, or
- Double-click `stop.bat` to stop it automatically

## Troubleshooting

**Python not found:**
- Install Python 3.8 or higher from python.org
- Make sure Python is added to your system PATH

**VLC not found:**
- Install VLC Media Player from videolan.org
- The application will look in common installation locations

**Movies not found:**
- Make sure the `movies` folder exists in the same directory as `start.bat`
- Click "Scan Movies Folder" to index your movies

**Port already in use:**
- Close any other instances of the application
- Or modify `main.py` to use a different port

## Technical Details

- **Backend:** FastAPI (Python)
- **Frontend:** Vanilla HTML/CSS/JavaScript
- **Data Storage:** SQLite database (automatic migration from legacy JSON on first run)
- **Indexing:** Incremental using file mtime + size, with hashing avoidance for speed
- **Supported video formats:** `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv`, `.flv`, `.webm`, `.m4v`, `.mpg`, `.mpeg`, `.3gp`
- **Optional tools:** ffmpeg (to extract screenshots when images are not available)
- **Port:** 8002 (default)
- **Host:** localhost (127.0.0.1)

### Images vs Screenshots

The application distinguishes between two types of images:

- **Images**: Media files that came with the movie (posters, covers, thumbnails, etc.). These are existing image files found in the movie's folder, not generated by the application.

- **Screenshots**: Frames extracted from the video file itself using ffmpeg. These are generated by the application during scanning, not pre-existing files.

Both are stored and displayed in the movie gallery. Images are preferred if found; otherwise, screenshots are extracted when possible.
