# Movie Action Menu

The **Movie Action Menu** is the context-sensitive "..." menu that appears on movie cards and movie detail pages. It provides quick access to common movie actions.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         SERVER SIDE                              │
│  build_movie_cards() computes menu_state for each movie:        │
│  - copy_to_local: null | 'not_copied' | 'already_copied'        │
│                                                                  │
│  Menu state is included in every movie API response             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT SIDE                               │
│  movie-menu.js defines actions and renders menu HTML            │
│  No additional AJAX calls needed - state is pre-computed        │
│                                                                  │
│  components.js & movie-details.js use central renderer          │
└─────────────────────────────────────────────────────────────────┘
```

### Why Server-Side State?

1. **Performance**: No extra API calls when page loads
2. **Consistency**: Menu state is computed once, displayed everywhere
3. **Simplicity**: Frontend just renders, doesn't compute

## Menu Items

| ID | Label | Contexts | Requires | Description |
|----|-------|----------|----------|-------------|
| `open-folder` | Open Folder | card, details | - | Opens file explorer at movie location |
| `add-to-playlist` | Add to playlist | card, details | - | Shows playlist selection submenu |
| `copy-to-local` | Copy to Local / Already Copied | card, details | `local_target_folder` configured | Copies movie files to local folder |
| `hide-movie` | Don't show this anymore | card, details | - | Hides movie from search/browse |

### Dynamic Labels

Some items have dynamic labels based on state:

- **copy-to-local**: Shows "Already Copied" (green) when movie is already in local folder

### Configuration-Dependent Items

Items with requirements won't appear if configuration is missing:

- **copy-to-local**: Requires `local_target_folder` to be set in Settings

## Files

| File | Purpose |
|------|---------|
| `static/js/movie-menu.js` | Central menu definition and rendering |
| `main.py` (`build_movie_cards`) | Server-side menu state computation |
| `docs/MOVIE_MENU.md` | This documentation |

## API Response Format

Movie API responses include `menu_state`:

```json
{
  "id": 123,
  "name": "Movie Name",
  "path": "D:\\movies\\...",
  "menu_state": {
    "copy_to_local": "not_copied"  // or "already_copied" or null
  }
}
```

### `menu_state.copy_to_local` values:

| Value | Meaning |
|-------|---------|
| `null` | Feature not configured (don't show item) |
| `"not_copied"` | Movie not yet copied to local |
| `"already_copied"` | Movie already exists in local folder |

## Adding a New Menu Item

### 1. Add to `MOVIE_MENU_ACTIONS` in `movie-menu.js`:

```javascript
{
    id: 'my-action',
    label: 'My Action',  // or function(movie) => string for dynamic
    action: (movieId, movie) => {
        // Handler code
    },
    contexts: ['card', 'details'],  // or just ['details']
    enabled: (movie) => true,  // Optional: condition to show
    className: ''  // Optional: CSS class
}
```

### 2. If state is needed from server, update `build_movie_cards()` in `main.py`:

```python
menu_state = {
    "copy_to_local": copy_status_map.get(m.id) if local_target_configured else None,
    "my_feature": my_computed_state  # Add new state here
}
```

### 3. Update this documentation

## Contexts

| Context | Where Used | Example |
|---------|------------|---------|
| `card` | Movie cards in grids (Explore, Search, Playlists) | Compact view |
| `details` | Movie details page | Full movie info |

**Principle**: Movie card is a minified version of movie details. Details page is a superset - may show additional actions.

## CSS Classes

| Class | Purpose |
|-------|---------|
| `.movie-card-menu-btn` | Menu button on cards (⋮) |
| `.movie-card-menu-dropdown` | Dropdown container |
| `.movie-card-menu-item` | Individual menu item |
| `.menu-item-success` | Green text (e.g., "Already Copied") |
| `.active` | Dropdown is visible |

