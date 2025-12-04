# Movie Searcher Testing Guide

This document describes the testing infrastructure for Movie Searcher. The goal is to make the system **bulletproof** by catching issues before they become problems.

## Testing Approaches

We have two complementary testing approaches:

### 1. API Sanity Tests (`scripts/sanity_test.py`)

Automated tests that validate all backend API endpoints work correctly.

**What it tests:**
- Server health and uptime
- Database statistics  
- FFmpeg/VLC detection
- Search functionality (basic, by letter, by year, by decade)
- Movie details API
- Language filters
- Playlist listing and details
- History endpoint
- Currently playing detection

**How to run:**

```powershell
# Activate virtual environment first
.\venv\Scripts\Activate.ps1

# Run all tests (requires server running)
python scripts/sanity_test.py

# Run without VLC-specific tests
python scripts/sanity_test.py --no-vlc
```

**When to run:**
- After any code changes
- Before committing changes
- After server restart

### 2. Browser E2E Tests (Cursor MCP Tools)

Real browser-based tests using Cursor's built-in browser automation. These catch issues that API tests miss: JavaScript errors, broken UI elements, CSS issues, etc.

**What it validates:**
- Page loads correctly
- Search input works and shows results
- Navigation between pages (Home, Explore, History, Playlists, Setup)
- Movie details page with Launch button
- Currently Playing indicator updates
- Server uptime display

**How to test manually (using Cursor):**

The MCP browser tools are the most reliable way to test the frontend. In Cursor's AI chat:

```
Navigate to http://127.0.0.1:8002 and take a snapshot
```

Key test flows:
1. **Home Page**: Navigate to `/`, verify search input exists
2. **Search**: Type in search box, verify results appear
3. **Movie Details**: Navigate to `/#/movie/1`, verify Launch button exists
4. **Explore**: Navigate to `/#/explore`, verify movie grid loads
5. **History**: Navigate to `/#/history`, verify history items display
6. **Setup**: Navigate to `/#/setup`, verify Restart button exists

### 3. Playwright Browser Tests (`scripts/browser_test.py`)

Automated browser tests using Playwright.

**Prerequisites:**
```powershell
pip install playwright
playwright install chromium
```

**How to run:**
```powershell
# Run all browser tests (headless)
python scripts/browser_test.py

# Run with visible browser (for debugging)
python scripts/browser_test.py --headed

# Run slowly (500ms between actions)
python scripts/browser_test.py --slow
```

**Note:** On Windows, these may timeout due to async issues. The MCP browser tools (above) are more reliable.

## Test Coverage Matrix

| Feature | API Test | Browser Test |
|---------|----------|--------------|
| Server starts | ✅ | ✅ |
| Server uptime | ✅ | ✅ |
| Search works | ✅ | ✅ |
| Movie details | ✅ | ✅ |
| Launch button | - | ✅ |
| VLC playback | ✅ | - |
| Explore page | ✅ | ✅ |
| History page | ✅ | ✅ |
| Playlists | ✅ | ✅ |
| Settings page | - | ✅ |
| Currently playing | ✅ | ✅ |
| JavaScript errors | - | ✅ |
| CSS/Layout | - | ✅ |

## Quick Validation Checklist

Before considering the app "working", verify:

1. ✅ Server starts without errors
2. ✅ Home page loads (`http://127.0.0.1:8002`)
3. ✅ Search returns results
4. ✅ Can navigate to movie details
5. ✅ Launch button is visible and clickable
6. ✅ Currently Playing updates when VLC is running
7. ✅ Server uptime indicator shows in header

## Troubleshooting

### Server won't start
- Check port 8002 is available: `netstat -ano | findstr 8002`
- Check for Python errors in console

### Tests fail to connect
- Ensure server is running: `python server.py`
- Check firewall settings

### Browser tests timeout
- Use MCP browser tools instead
- Or run with `--headed` flag to see what's happening

### API tests fail
- Check server console for error messages
- Verify database exists (`movie_searcher.db`)

## CI/CD Integration

For automated testing in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run API Tests
  run: |
    python -m pip install -r requirements.txt
    python server.py &
    sleep 5
    python scripts/sanity_test.py --no-vlc
```

## Adding New Tests

### API Tests
Edit `scripts/sanity_test.py` and add a new test method:

```python
def test_my_new_feature(self) -> Tuple[bool, str, Optional[str]]:
    """Test description"""
    data = self.api_get("/api/my-endpoint")
    if data is None:
        return False, "API call failed", None
    if "expected_field" not in data:
        return False, "Missing field", None
    return True, "Feature works", None
```

### Browser Tests
Use Cursor's MCP browser tools to validate UI changes interactively, or add to `scripts/browser_test.py`:

```python
def test_my_new_ui_feature(self) -> tuple:
    """Test new UI feature"""
    self.page.goto(f"{SERVER_URL}/#/my-page")
    self.page.wait_for_load_state("networkidle")
    
    element = self.page.query_selector("#myElement")
    if not element:
        return False, "Element not found"
    
    return True, "UI feature works"
```

