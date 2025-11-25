# Developer Notes

## Architecture

FastAPI backend with static HTML frontend. State persisted in SQLite database (`movie_searcher.db`).

## Components

- `main.py`: FastAPI application, API endpoints, business logic
- `server.py`: Uvicorn server configuration and startup
- `start.py`: Cross-platform startup script (replaces start.bat)
- `index.html`: Frontend interface, search UI, autocomplete
- `database.py`: Database setup, migrations, utilities
- `models.py`: SQLAlchemy database models (table definitions)
- `core/models.py`: Pydantic models for API request/response validation
- `scanning.py`: Directory scanning and movie indexing
- `video_processing.py`: Video processing, screenshot extraction, ffmpeg integration
- `vlc_integration.py`: VLC player integration and launch management
- `screenshot_sync.py`: Screenshot database synchronization
- `config.py`: Configuration management (uses `settings.json` for API keys)
- `ffmpeg_setup.py`: FFmpeg detection and configuration
- `setup_ffmpeg.py`: FFmpeg setup helper script

## Database

- **Database**: SQLite (`movie_searcher.db`)
- **ORM**: SQLAlchemy
- **Schema Version**: Tracked via `schema_version` table (current: 12)
- **Migrations**: Automatic schema migrations on startup
- **Tables**: movies, ratings, movie_status, search_history, launch_history, indexed_paths, config, screenshots, movie_audio, playlists, playlist_items, external_movies, people, movie_credits, schema_version

## Indexing Strategy

- One-time deep scan on `/api/index` endpoint
- File hash-based change detection (mtime + size)
- Incremental updates: only re-index changed files
- Video length extraction via ffprobe (ffmpeg)
- Supports: .mp4, .avi, .mkv, .mov, .wmv, .flv, .webm, .m4v, .mpg, .mpeg, .3gp

## State Management

- **Database**: All movie metadata, ratings, watch status, history stored in SQLite
- **Configuration**: API keys stored in `settings.json` (gitignored)
- **Screenshots**: Extracted frames stored in `screenshots/` directory
- Hash-based deduplication prevents unnecessary re-scanning
- Indexed paths tracked in `indexed_paths` table to avoid duplicate scanning

## VLC Integration

- Auto-detects VLC installation paths on Windows
- Falls back to PATH if VLC executable found
- Launches via subprocess without blocking
- Tracks currently playing movies via process detection

## Screenshot System

- Screenshots extracted at precise timestamps using ffmpeg
- Subtitle text burned onto screenshots when subtitle files available
- Screenshots stored in `screenshots/` directory
- Database synchronization via `screenshot_sync.py` (no retry logic - failures indicate bugs)
- Path normalization ensures consistent storage/retrieval
- **Design Principle**: No fallback/retry logic - if screenshot save fails, it's a bug that must be fixed, not masked
- Path normalization: Always use `normalize_screenshot_path()` before storing/querying to prevent path mismatch issues

## Performance Considerations

- Search limited to 50 results
- Autocomplete shows top 10 matches
- History limited to last 100 entries
- File hashing avoids full re-scan on unchanged files
- Database uses WAL mode for better concurrency
- Foreign keys enabled for data integrity

## Future Improvements

- File system watcher for real-time updates
- Multiple root folder support
- Configurable video extensions
- Better error handling for corrupted video files

