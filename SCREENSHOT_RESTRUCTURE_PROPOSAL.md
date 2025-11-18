# Screenshot System Restructure Proposal

## Problems with Current Code

### 1. Retry Logic Hides Real Bugs
- **Current**: 3 retry attempts with delays
- **Problem**: SQLite doesn't randomly fail. Retries mask:
  - Path normalization bugs (storing `str(path)` vs `str(path.resolve())`)
  - Session management issues (querying after commit in same session)
  - Race conditions (multiple workers saving same screenshot)

### 2. Path Normalization Inconsistency
- **Current**: Mix of `str(screenshot_path)`, `str(out_path)`, `Path(...).resolve()`
- **Problem**: Paths stored/queried inconsistently → "commit succeeded but not found"
- **Solution**: Always normalize paths before storage/query

### 3. No Synchronization Mechanism
- **Current**: No way to detect/fix DB vs disk mismatches
- **Problem**: Orphaned files accumulate, missing files go undetected
- **Solution**: Add sync functions to detect and fix mismatches

### 4. Mixed Concerns
- **Current**: Extraction, DB save, retry logic all mixed together
- **Problem**: Hard to test, hard to reason about
- **Solution**: Separate persistence into dedicated module

## Proposed Structure (Without Big Service Refactor)

### New Module: `screenshot_sync.py` ✅ (Already Created)
**Purpose**: Centralized screenshot database operations with proper path normalization

**Functions**:
- `normalize_screenshot_path(path)` - Always normalize paths consistently
- `save_screenshot_to_db(movie_id, path, timestamp)` - Save with NO retries (fail = bug)
- `sync_existing_screenshot(movie_id, path, timestamp)` - Sync file to DB if missing
- `find_orphaned_files(movie_id, screenshot_dir)` - Find files on disk not in DB
- `find_missing_files(movie_id)` - Find DB entries without files
- `sync_movie_screenshots(movie_id, screenshot_dir)` - Full sync (detect + fix orphaned)
- `restore_missing_screenshot(screenshot, video_path, extract_func)` - Queue re-extraction of missing files

### Changes to `video_processing.py`

**Replace retry logic with direct calls to `screenshot_sync`**:

```python
# OLD (lines 844-897): 3 retry attempts with verification
# NEW: Single call to screenshot_sync
from screenshot_sync import sync_existing_screenshot

if screenshot_path.exists():
    if not sync_existing_screenshot(movie_id, screenshot_path, timestamp_seconds):
        logger.error(f"Failed to sync screenshot to DB - this is a bug, not a transient error")
    return True
```

```python
# OLD (lines 919-979): 3 retry attempts in callback
# NEW: Single call to screenshot_sync
from screenshot_sync import save_screenshot_to_db

if rc == 0 and out_path.exists():
    if not save_screenshot_to_db(movie_id_to_use, out_path, timestamp_seconds):
        logger.error(f"Failed to save screenshot to DB - this is a bug, not a transient error")
        # File exists but not in DB - will be caught by sync function
    else:
        # Success - update progress
        scan_progress_dict["frames_processed"] = ...
```

**Key Changes**:
1. Remove all retry loops (lines 731-772, 852-897, 928-975)
2. Replace with single calls to `screenshot_sync` functions
3. Use normalized paths everywhere (via `normalize_screenshot_path`)
4. Log errors clearly - failures indicate bugs, not transient issues

### Changes to `main.py`

**Add sync endpoint**:
```python
@app.post("/api/movie/{movie_id}/sync-screenshots")
async def sync_movie_screenshots_endpoint(movie_id: int):
    """Synchronize screenshots for a movie: detect and fix mismatches"""
    from screenshot_sync import sync_movie_screenshots
    from video_processing import SCREENSHOT_DIR
    
    result = sync_movie_screenshots(movie_id, SCREENSHOT_DIR)
    return result
```

**Update interval screenshots endpoint** to use sync:
```python
# After deleting screenshots, run sync to catch any orphaned files
sync_result = sync_movie_screenshots(movie_id, SCREENSHOT_DIR)
if sync_result["orphaned_files"]:
    logger.info(f"Synced {sync_result['synced_count']} orphaned files to DB")
```

### Changes to `scanning.py`

**Use screenshot_sync in `index_movie`**:
```python
# When syncing existing screenshots
from screenshot_sync import sync_existing_screenshot

if existing_screenshot and os.path.exists(existing_screenshot.shot_path):
    # Already in DB, skip
    pass
else:
    # Sync if file exists, or queue extraction
    if screenshot_path.exists():
        sync_existing_screenshot(movie.id, screenshot_path, timestamp_seconds)
    else:
        extract_movie_screenshot(...)
```

## Migration Steps

1. ✅ Create `screenshot_sync.py` with proper path normalization
2. Update `video_processing.py`:
   - Replace retry logic in `extract_movie_screenshot()` (lines 728-773)
   - Replace retry logic in `process_screenshot_extraction_worker()` (lines 844-897, 919-979)
   - Use `screenshot_sync` functions instead
3. Update `main.py`:
   - Add sync endpoint
   - Use sync in interval screenshots endpoint
4. Update `scanning.py`:
   - Use sync functions when syncing existing screenshots
5. Test: Verify no retry logic remains, errors are logged clearly

## Benefits

1. **No Retry Logic**: Failures are bugs, not transient errors
2. **Path Normalization**: Consistent paths prevent "not found" issues
3. **Synchronization**: Can detect and fix mismatches
4. **Clear Errors**: Failures logged with full context for debugging
5. **Testable**: Sync functions can be tested independently
6. **Maintainable**: Single source of truth for DB operations

## Why This Works

- **SQLite is reliable**: If it fails, it's our bug (path normalization, session management)
- **Path normalization fixes**: The "commit succeeded but not found" issue is likely path mismatch
- **Sync functions**: Can detect and fix mismatches after the fact
- **No big refactor**: Just extract DB operations, keep extraction logic as-is

