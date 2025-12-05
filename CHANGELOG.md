# Changelog

All notable changes to Movie Searcher are documented here.

## [1.4.1] - 2024-12-05

### Settings

**Show/Hide File Sizes**
- New toggle in Settings: "Show file size on movie cards and details"
- When disabled, file sizes are hidden across browse results and movie detail pages
- Takes effect immediately without page refresh
- Persists across sessions via settings.json

---

## [1.4.0] - 2024-12-04

### New Features

**Same-Title Movie Navigation**
- Movie details page now shows when other versions of the same movie exist
- Subtle badge next to title: "1 other version" / "X other versions"
- Click to reveal dropdown with links to all copies (shows file size, hidden status)
- Useful for finding smaller/hidden copies or alternative versions

### UI Changes

**Narrower Page Layout**
- Reduced overall page width from 94% to 65% for a more focused reading experience
- Content is now centered with generous margins on both sides
- Applies to all pages (Home, Explore, Movie Details, etc.)

### Technical Changes

**API Endpoints**
- `GET /api/movie/{id}/same-title` - Returns other movies with identical title

**Frontend**
- `movie-details.js` - Added `fetchSameTitleMovies()` and `renderSameTitleMovies()` functions
- `style.css` - Added `.same-title-indicator`, `.same-title-toggle`, `.same-title-dropdown` styles

---

## [1.3.0] - 2024-11-30

### New Features

**Movie Lists (Saved AI Searches)**
- AI search results are now automatically saved as Movie Lists
- Browse all your past AI queries in the new Lists page
- Mark lists as favorites for quick access
- Filter lists by favorites or search text
- Edit list titles inline
- Delete lists you no longer need
- See which suggested movies are in your library vs missing

**Copy to Local**
- Copy movies from network/NAS to a local folder for offline viewing
- Real-time progress indicator during file copy
- Automatic detection of already-copied movies
- Configure local target folder in Settings

**Movie Action Menu**
- Unified context menu ("⋮") on all movie cards and detail pages
- Quick access to: Open Folder, Add to Playlist, Copy to Local, Hide Movie
- Consistent behavior across Explore, Search, Playlists, and Movie Details
- Dynamic menu items based on configuration (Copy to Local only shows when configured)

### Technical Changes

**Database**
- Schema version: 13
- New tables: `movie_lists`, `movie_list_items`
- New config key: `local_target_folder`

**API Endpoints**
- `GET /api/movie-lists` - List all movie lists with filtering
- `GET /api/movie-lists/{slug}` - Get single movie list with movies
- `PATCH /api/movie-lists/{slug}` - Update title/favorite status
- `DELETE /api/movie-lists/{slug}` - Soft delete a movie list
- `GET /api/movie-lists/suggestions` - Get similar/recent lists
- `POST /api/movie/{id}/copy-to-local` - Start copying movie to local folder
- `GET /api/movie/{id}/copy-status` - Poll copy progress
- Updated `GET/POST /api/config` to include `local_target_folder`

**Frontend**
- New `movie-lists.js` - Movie lists page and management
- New `movie-menu.js` - Centralized movie action menu system
- Updated `setup.js` - Local target folder config, copy progress UI
- Updated `ai-search.js` - Auto-save results as movie lists
- Updated `components.js` - Use central menu renderer
- Updated `movie-details.js` - Use central menu renderer

**Models**
- `MovieList` - Stores AI query, title, provider, cost, favorite status
- `MovieListItem` - Individual movies in list with AI comments
- `MovieListUpdateRequest` - Pydantic model for PATCH requests

**Documentation**
- `docs/MOVIE_MENU.md` - Movie action menu architecture
- `docs/PRODUCT_NOTES.md` - Product values and design principles
- `docs/TECH_NOTES.md` - Performance guidelines and CSS standards

### Dependencies
- Updated `requirements.txt`

---

## Previous Versions

Prior changes were not tracked in this changelog format.

