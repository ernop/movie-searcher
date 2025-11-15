# Developer Notes

## Architecture

FastAPI backend with static HTML frontend. State persisted in JSON files.

## Components

- `main.py`: FastAPI application, indexing logic, API endpoints
- `index.html`: Frontend interface, search UI, autocomplete
- `movie_index.json`: Persistent movie database (auto-generated)
- `search_history.json`: Search and launch history (auto-generated)

## Indexing Strategy

- One-time deep scan on `/api/index` endpoint
- File hash-based change detection (mtime + size)
- Incremental updates: only re-index changed files
- Video length extraction via mutagen library
- Supports: .mp4, .avi, .mkv, .mov, .wmv, .flv, .webm, .m4v, .mpg, .mpeg, .3gp

## State Management

- `movie_index.json`: Stores all movie metadata
- Hash-based deduplication prevents unnecessary re-scanning
- Indexed paths tracked to avoid duplicate scanning

## VLC Integration

- Auto-detects VLC installation paths on Windows
- Falls back to PATH if VLC executable found
- Launches via subprocess without blocking

## Performance Considerations

- Search limited to 50 results
- Autocomplete shows top 10 matches
- History limited to last 100 entries
- File hashing avoids full re-scan on unchanged files

## Future Improvements

- File system watcher for real-time updates
- Multiple root folder support
- Configurable video extensions
- Better error handling for corrupted video files

