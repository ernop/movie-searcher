# TV Show vs Movie Detection - Implementation Plan

> **Status:** Planning (not yet implemented)  
> **Created:** December 2024  
> **Priority:** Future enhancement

## Problem Statement

Currently, Movie Searcher treats all video files as "movies" even when they're TV show episodes. This makes it difficult to:

- Query "show me all TV shows" vs "show me only movies"
- Group episodes by show name (e.g., all Breaking Bad episodes together)
- Sort episodes within a show by season/episode number
- Track watch progress per show (e.g., "I'm on season 2 of The Wire")
- Search for a show and see it as one result instead of 62 separate episodes

---

## Current State Analysis

### What's Already Implemented ✅

The `clean_movie_name()` function in `scanning.py` already has extensive TV detection logic:

**Pattern Detection:**
- `S01E01` format (most common)
- `Season 1`, `Season 01`, `S1`, `S01` in folder names
- `Episode 1`, `E01`, `ep01` patterns
- `Vol1-Episode2` custom formats
- Leading episode numbers like `02-A Sound of Dolphins`

**Extraction Logic:**
- Extracts season number from parent folder or filename
- Extracts episode number from filename
- Extracts episode title (text after S01E01)
- Extracts show name from parent/grandparent folders
- Cleans quality tags, release groups, etc.

**Current Behavior:**
All extracted info is **embedded into the `name` field**:
```
"Breaking Bad S01E02 Cats in the Bag"
"Babylon 5 024 S02E01 Points of Departure"
"BEASTARS S01E10 A Wolf in Sheeps Clothing"
```

### What's Missing ❌

**Database Model (`Movie` table):**
```python
# Current fields only:
id, path, name, year, length, size, hash, language, image_path, hidden, created, updated

# Missing fields:
is_tv_show      # Boolean - quick filter
show_name       # String - for grouping episodes
season          # Integer - for sorting
episode         # Integer - for sorting
episode_title   # String - individual episode name
```

**UI Features:**
- No Movies vs TV Shows tabs/filters
- No grouping by show
- No season/episode sorting within a show
- No show-level progress tracking

---

## Proposed Solution

### Option A: Add Fields to Movie Model (Recommended)

Add new columns to the existing `Movie` table:

```python
class Movie(Base):
    __tablename__ = "movies"
    
    # ... existing fields ...
    
    # New TV show fields
    is_tv_show = Column(Boolean, default=False, nullable=False, index=True)
    show_name = Column(String, nullable=True, index=True)    # "Breaking Bad"
    season = Column(Integer, nullable=True)                   # 1
    episode = Column(Integer, nullable=True)                  # 2
    episode_title = Column(String, nullable=True)             # "Cats in the Bag"
```

**Pros:**
- Simple, backward compatible (new fields are nullable)
- Easy queries: `WHERE is_tv_show = True`
- Group by show: `GROUP BY show_name`
- Sort episodes: `ORDER BY season, episode`
- No data migration complexity

**Cons:**
- Data denormalization (show_name repeated for each episode)
- Show-level metadata (poster, description) would need separate handling

### Option B: Separate TVShow Table (More Normalized)

Create a new table for TV shows:

```python
class TVShow(Base):
    __tablename__ = "tv_shows"
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=True)
    poster_path = Column(String, nullable=True)
    # ... other show-level metadata

class Movie(Base):
    # ... existing fields ...
    tv_show_id = Column(Integer, ForeignKey('tv_shows.id'), nullable=True)
    season = Column(Integer, nullable=True)
    episode = Column(Integer, nullable=True)
    episode_title = Column(String, nullable=True)
```

**Pros:**
- Cleaner data model
- Show-level metadata stored once
- Easier to add show descriptions, posters, etc.

**Cons:**
- More complex migration
- Need to deduplicate/match show names
- Foreign key management

### Recommendation

**Start with Option A** for quick implementation, then migrate to Option B later if needed.

---

## Detection Strategies

### 1. Filename Pattern Detection (Already Implemented)

| Pattern | Example | Reliability |
|---------|---------|-------------|
| S01E01 | `Breaking.Bad.S01E01.720p.mkv` | High |
| Season X folder | `Season 1/episode.mkv` | High |
| Episode XX | `Episode.01.mkv` | Medium |
| Leading number | `02-A Sound of Dolphins.mp4` | Medium |

### 2. Folder Structure Detection

```
D:\TV Shows\
└── Breaking Bad\
    └── Season 1\
        └── S01E01.mkv    ← Detected as TV show
```

Key indicators:
- Parent folder named "TV Shows", "Series", "TV"
- Grandparent is show name, parent is "Season X"

### 3. Video Length Heuristic

| Duration | Likely Type | Confidence |
|----------|-------------|------------|
| < 30 min | TV episode (sitcom/anime) | Medium |
| 30-50 min | TV episode (drama) | Medium |
| 50-70 min | Ambiguous | Low |
| > 70 min | Movie | Medium |
| > 100 min | Movie | High |

**Note:** Many prestige TV shows have 60+ min episodes, so this is just a hint.

### 4. User Override (Future)

Allow users to:
- Manually mark items as Movie/TV Show
- Correct misdetections
- Specify show name for undetected episodes

---

## Implementation Steps

### Phase 1: Database Changes

1. Add new columns to `Movie` model in `models.py`
2. Create database migration in `database.py`
3. Increment `CURRENT_SCHEMA_VERSION`

### Phase 2: Scanning Changes

1. Modify `clean_movie_name()` to return a dictionary instead of tuple:
   ```python
   return {
       'name': name,           # Display name (same as current)
       'year': year,
       'is_tv_show': bool(season or episode),
       'show_name': show_name,
       'season': season,
       'episode': episode,
       'episode_title': episode_title
   }
   ```

2. Update `index_movie()` to store new fields

3. Add re-scan option to populate existing entries

### Phase 3: API Changes

1. Add filter parameters to search/explore endpoints:
   - `?type=movie` or `?type=tv`
   - `?show_name=Breaking%20Bad`
   
2. Add grouping endpoint:
   - `GET /api/tv-shows` → list of unique show names with episode counts
   - `GET /api/tv-shows/{show_name}/episodes` → episodes grouped by season

### Phase 4: UI Changes

1. Add "Movies" / "TV Shows" toggle in Explore
2. Add show grouping view (grid of show posters)
3. Add season/episode list within show detail
4. Add "Continue Watching" for partially-watched shows

---

## API Endpoint Ideas

```
# List all TV shows (grouped)
GET /api/tv-shows
→ [{"show_name": "Breaking Bad", "seasons": 5, "episodes": 62, "poster": "..."}]

# Get episodes for a show
GET /api/tv-shows/Breaking%20Bad/episodes
→ {"seasons": [{"number": 1, "episodes": [...]}]}

# Get next unwatched episode
GET /api/tv-shows/Breaking%20Bad/next
→ {"season": 2, "episode": 3, "title": "Bit by a Dead Bee"}

# Search with type filter
GET /api/search?q=breaking&type=tv
GET /api/search?q=inception&type=movie
```

---

## UI Mockup Ideas

### Explore Page with Type Toggle
```
[Movies] [TV Shows] [All]

┌─────────────────┐ ┌─────────────────┐
│  Breaking Bad   │ │  The Wire       │
│  ★★★★★          │ │  ★★★★☆          │
│  5 seasons      │ │  5 seasons      │
│  62 episodes    │ │  60 episodes    │
│  [Continue S2E3]│ │  [Start]        │
└─────────────────┘ └─────────────────┘
```

### Show Detail Page
```
← Back

Breaking Bad (2008-2013)
★★★★★ | 5 Seasons | 62 Episodes

[▶ Continue S02E03] [Mark All Watched]

Season 1 ──────────────────────────
  ✓ E01 Pilot
  ✓ E02 Cat's in the Bag...
  ▶ E03 ...And the Bag's in the River  ← Currently watching
    E04 Cancer Man
    E05 Gray Matter
```

---

## Edge Cases to Handle

1. **Miniseries**: Single season, 4-8 episodes (treat as TV show)
2. **Anthology shows**: Each season is different story (group by show name still)
3. **Specials**: S00E01 format (season 0 = specials)
4. **Movies in TV folders**: User might have movies mixed in
5. **Multi-part movies**: "Kill Bill Vol. 1" shouldn't be detected as TV
6. **Documentaries**: Could be movie or series
7. **Anime**: Often numbered 001-500 without season structure

---

## Testing Considerations

Test cases needed:
- [ ] Standard S01E01 naming detected correctly
- [ ] Season folder structure detected
- [ ] Movies not misidentified as TV
- [ ] Multi-part movies (Kill Bill, LOTR) stay as movies
- [ ] Anime with 100+ episodes grouped correctly
- [ ] Shows without season structure (just episode numbers)
- [ ] Mixed folders (movies + TV in same parent)

---

## Future Enhancements

1. **TMDb/TVDb Integration**: Fetch show metadata, posters, episode summaries
2. **Automatic Episode Naming**: Match files to episode names from database
3. **Watch Progress Sync**: Track which episodes are watched
4. **Up Next Widget**: "Continue Breaking Bad S02E03"
5. **Calendar View**: Show episodes by air date
6. **Batch Operations**: Mark entire season as watched

---

## References

- Current detection code: `scanning.py` lines 288-953 (`clean_movie_name` function)
- Cleaning patterns: `cleaning_patterns.py`
- Database models: `models.py`
- Related: `docs/TESTING.md` for test infrastructure

