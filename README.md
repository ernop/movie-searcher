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

- **Search Movies:** Instant search with autocomplete
- **Track Watched:** Mark movies as watched/unwatched
- **View History:** See all movies you've watched
- **Launch with VLC:** Open movies directly in VLC player
- **Auto Subtitle Detection:** Automatically finds and loads subtitle files
- **Manual Subtitle Selection:** Choose from available subtitle files
- **Filter by Watched:** Show only watched or unwatched movies
- **Persistent Index:** Scans once, updates incrementally

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
├── movie_index.json       # Auto-generated index
├── watched_movies.json    # Auto-generated watched list
├── search_history.json    # Auto-generated history
└── config.json            # Auto-generated config (movies folder path)
```

## Requirements

- **Python 3.8+** (will be checked automatically)
- **VLC Media Player** (for playing movies)
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

Click the "Launch" button next to any movie to open it in VLC. If a subtitle file is found automatically, it will be loaded. You can also manually select a subtitle from the dropdown if multiple are available.

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
- Or modify `main.py` to use a different port (line 392)

## Technical Details

- **Backend:** FastAPI (Python)
- **Frontend:** Vanilla HTML/CSS/JavaScript
- **Data Storage:** JSON files (no database required)
- **Port:** 8002 (default)
- **Host:** localhost (127.0.0.1)
