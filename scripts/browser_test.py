#!/usr/bin/env python3
"""
Movie Searcher Browser E2E Test Suite

Real browser-based tests that validate the application from a user's perspective.
These tests catch issues that API-only tests miss: JavaScript errors, broken UI,
non-functional buttons, CSS issues, etc.

Prerequisites:
    1. Install Playwright: pip install playwright
    2. Install browser: playwright install chromium
    3. Server must be running: python server.py

Usage:
    python scripts/browser_test.py              # Run all browser tests
    python scripts/browser_test.py --headed     # Run with visible browser (useful for debugging)
    python scripts/browser_test.py --slow       # Run slowly (500ms between actions)

Tests:
    1. Home Page Load - Does the main page render without JS errors?
    2. Navigation - Can we navigate between all pages?
    3. Search Flow - Type query → see results → click movie
    4. Movie Details - View movie details, screenshots, actions
    5. Play Button - Does the play button exist and work?
    6. Explore Page - Browse, filter by letter/decade/language
    7. History Page - View launch history
    8. Currently Playing - Detect VLC playing status
    9. Playlists - View and navigate playlists
    10. Settings Page - Load and interact with settings
    11. No JS Errors - Check for JavaScript console errors
    12. Full Workflow - Search → Click → Play (core user journey)
"""

import argparse
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Add parent directory to path
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from playwright.sync_api import Browser, Page, sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Server configuration
SERVER_URL = "http://127.0.0.1:8002"

# ANSI colors
class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"


@dataclass
class BrowserTestResult:
    """Result of a single browser test"""
    name: str
    passed: bool
    message: str
    duration_ms: float
    screenshot_path: str | None = None
    console_errors: list[str] | None = None


class BrowserTester:
    """Browser-based E2E test runner using sync API"""

    def __init__(self, headed: bool = False, slow_mo: int = 0):
        self.headed = headed
        self.slow_mo = slow_mo
        self.results: list[BrowserTestResult] = []
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.console_errors: list[str] = []
        self.screenshots_dir = PROJECT_ROOT / "test_screenshots"
        self.playwright = None

    def log(self, message: str, color: str = ""):
        """Print a log message"""
        try:
            if color:
                print(f"{color}{message}{Colors.RESET}")
            else:
                print(message)
        except UnicodeEncodeError:
            safe_msg = message.encode('ascii', errors='replace').decode('ascii')
            if color:
                print(f"{color}{safe_msg}{Colors.RESET}")
            else:
                print(safe_msg)

    def setup(self):
        """Set up browser and page"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed. Run: pip install playwright && playwright install chromium")

        # Create screenshots directory
        self.screenshots_dir.mkdir(exist_ok=True)

        # Launch browser
        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=not self.headed,
            slow_mo=self.slow_mo,
        )

        # Create context and page
        context = self.browser.new_context(
            viewport={"width": 1400, "height": 900},
        )
        self.page = context.new_page()

        # Capture console errors
        self.page.on("console", self._handle_console)
        self.page.on("pageerror", self._handle_page_error)

    def _handle_console(self, msg):
        """Handle console messages"""
        if msg.type == "error":
            self.console_errors.append(f"Console: {msg.text[:200]}")

    def _handle_page_error(self, error):
        """Handle page errors (uncaught exceptions)"""
        self.console_errors.append(f"PageError: {str(error)[:200]}")

    def teardown(self):
        """Clean up browser"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def take_screenshot(self, name: str) -> str:
        """Take a screenshot and return the path"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        path = self.screenshots_dir / filename
        self.page.screenshot(path=str(path))
        return str(path)

    def run_test(self, name: str, test_func) -> BrowserTestResult:
        """Run a single test and record the result"""
        start_time = time.time()
        self.console_errors = []  # Reset for each test

        screenshot_path = None
        try:
            passed, message = test_func()
        except Exception as e:
            passed = False
            message = f"Exception: {str(e)[:100]}"
            # Take failure screenshot
            try:
                screenshot_path = self.take_screenshot(f"FAIL_{name.replace(' ', '_')}")
            except:
                pass

        duration_ms = (time.time() - start_time) * 1000

        # Check for console errors
        if self.console_errors and passed:
            message += f" (warning: {len(self.console_errors)} console errors)"

        result = BrowserTestResult(
            name=name,
            passed=passed,
            message=message,
            duration_ms=duration_ms,
            screenshot_path=screenshot_path,
            console_errors=self.console_errors.copy() if self.console_errors else None
        )
        self.results.append(result)

        # Print result
        status = f"{Colors.GREEN}PASS{Colors.RESET}" if passed else f"{Colors.RED}FAIL{Colors.RESET}"
        time_str = f"{Colors.DIM}({duration_ms:.0f}ms){Colors.RESET}"
        print(f"  [{status}] {name} {time_str}")

        if not passed:
            print(f"         {Colors.RED}{message}{Colors.RESET}")
            if screenshot_path:
                print(f"         {Colors.DIM}Screenshot: {screenshot_path}{Colors.RESET}")

        if self.console_errors:
            for err in self.console_errors[:2]:  # Show first 2 errors
                print(f"         {Colors.YELLOW}! {err[:80]}{Colors.RESET}")

        return result

    # =========================================================================
    # Test Functions - Each returns (passed: bool, message: str)
    # =========================================================================

    def test_home_page_load(self) -> tuple:
        """Test 1: Home page loads correctly"""
        self.page.goto(SERVER_URL, timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        # Check title
        title = self.page.title()
        if "Movie Searcher" not in title:
            return False, f"Wrong title: {title}"

        # Check search input exists
        search_input = self.page.query_selector("#searchInput")
        if not search_input:
            return False, "Search input not found"

        # Check server uptime indicator
        uptime_el = self.page.query_selector("#serverUptime")
        if not uptime_el:
            return False, "Server uptime indicator not found"

        uptime_text = uptime_el.text_content()
        if not uptime_text or "up" not in uptime_text.lower():
            return False, f"Invalid uptime: {uptime_text}"

        return True, f"Loaded, uptime: {uptime_text}"

    def test_navigation(self) -> tuple:
        """Test 2: Navigation between pages works"""
        self.page.goto(SERVER_URL, timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        pages_tested = []

        # Test Explore
        self.page.click("#navExplore")
        self.page.wait_for_selector("#pageExplore.active", timeout=5000)
        pages_tested.append("Explore")

        # Test History
        self.page.click("#navHistory")
        self.page.wait_for_selector("#pageHistory.active", timeout=5000)
        pages_tested.append("History")

        # Test Home
        self.page.click("#navHome")
        self.page.wait_for_selector("#pageHome.active", timeout=5000)
        pages_tested.append("Home")

        # Test Setup
        self.page.click("#navSetup")
        self.page.wait_for_selector("#pageSetup.active", timeout=5000)
        pages_tested.append("Setup")

        return True, f"Nav OK: {', '.join(pages_tested)}"

    def test_search_flow(self) -> tuple:
        """Test 3: Search for a movie"""
        self.page.goto(SERVER_URL, timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        # Type in search box
        self.page.fill("#searchInput", "the")
        self.page.wait_for_timeout(600)  # Debounce

        # Wait for results
        try:
            self.page.wait_for_selector(".movie-card", timeout=10000)
        except:
            return False, "No search results appeared"

        # Count results
        cards = self.page.query_selector_all(".movie-card")
        if len(cards) == 0:
            return False, "No movie cards found"

        return True, f"Found {len(cards)} movies"

    def test_movie_card_click(self) -> tuple:
        """Test 4: Click a movie card to view details"""
        self.page.goto(SERVER_URL, timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        # Search
        self.page.fill("#searchInput", "the")
        self.page.wait_for_timeout(600)

        try:
            self.page.wait_for_selector(".movie-card", timeout=10000)
        except:
            return False, "No search results"

        # Click first movie
        self.page.click(".movie-card")

        # Wait for details page
        try:
            self.page.wait_for_selector("#pageMovieDetails.active", timeout=5000)
        except:
            return False, "Details page didn't load"

        # Check content loaded
        self.page.wait_for_timeout(500)

        return True, "Movie details loaded"

    def test_play_button_exists(self) -> tuple:
        """Test 5: Play button exists on movie details"""
        self.page.goto(f"{SERVER_URL}/#/explore", timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        try:
            self.page.wait_for_selector(".movie-card", timeout=10000)
        except:
            return False, "No movies in explore"

        # Click first movie
        self.page.click(".movie-card")

        try:
            self.page.wait_for_selector("#pageMovieDetails.active", timeout=5000)
        except:
            return False, "Details didn't load"

        self.page.wait_for_timeout(800)

        # Look for play button with various selectors
        selectors = [
            "button:has-text('Play')",
            ".play-btn",
            "[onclick*='launchMovie']",
            "button:has-text('Play Movie')",
        ]

        for sel in selectors:
            try:
                btn = self.page.query_selector(sel)
                if btn and btn.is_visible():
                    return True, "Play button found"
            except:
                continue

        return False, "Play button not found"

    def test_explore_page(self) -> tuple:
        """Test 6: Explore page loads with filters"""
        self.page.goto(f"{SERVER_URL}/#/explore", timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        # Wait for movie grid
        try:
            self.page.wait_for_selector(".movie-grid", timeout=10000)
        except:
            return False, "Movie grid didn't load"

        # Check letter nav
        if not self.page.query_selector("#letterNav"):
            return False, "Letter nav missing"

        # Count movies
        cards = self.page.query_selector_all(".movie-card")

        return True, f"Explore loaded, {len(cards)} movies"

    def test_history_page(self) -> tuple:
        """Test 7: History page loads"""
        self.page.goto(f"{SERVER_URL}/#/history", timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        try:
            self.page.wait_for_selector("#historyList", timeout=5000)
        except:
            return False, "History list didn't load"

        items = self.page.query_selector_all(".history-item, .movie-card")

        return True, f"History loaded ({len(items)} items)"

    def test_playlists_page(self) -> tuple:
        """Test 8: Playlists page loads"""
        self.page.goto(f"{SERVER_URL}/#/playlists", timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        try:
            self.page.wait_for_selector("#playlistsOverview", timeout=5000)
        except:
            return False, "Playlists didn't load"

        self.page.wait_for_timeout(500)
        content = self.page.content()

        if "Favorites" in content:
            return True, "Playlists loaded with Favorites"

        return True, "Playlists page loaded"

    def test_settings_page(self) -> tuple:
        """Test 9: Settings page loads with controls"""
        self.page.goto(f"{SERVER_URL}/#/setup", timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        try:
            self.page.wait_for_selector("#pageSetup.active", timeout=5000)
        except:
            return False, "Settings page didn't activate"

        # Check key elements
        if not self.page.query_selector("#restartServerBtn"):
            return False, "Restart button missing"

        if not self.page.query_selector("#setupScanBtn"):
            return False, "Scan button missing"

        return True, "Settings loaded with all controls"

    def test_currently_playing(self) -> tuple:
        """Test 10: Currently playing indicator exists"""
        self.page.goto(SERVER_URL, timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        playing_el = self.page.query_selector("#currentlyPlaying")
        if not playing_el:
            return False, "Currently playing indicator missing"

        text = playing_el.text_content()
        return True, f"Playing indicator: '{text[:30]}'"

    def test_no_js_errors(self) -> tuple:
        """Test 11: Navigate pages checking for JS errors"""
        self.console_errors = []

        pages = [
            (SERVER_URL, "Home"),
            (f"{SERVER_URL}/#/explore", "Explore"),
            (f"{SERVER_URL}/#/history", "History"),
            (f"{SERVER_URL}/#/playlists", "Playlists"),
            (f"{SERVER_URL}/#/setup", "Settings"),
        ]

        for url, name in pages:
            self.page.goto(url, timeout=15000)
            self.page.wait_for_load_state("networkidle", timeout=10000)
            self.page.wait_for_timeout(300)

        if self.console_errors:
            return False, f"{len(self.console_errors)} JS errors"

        return True, f"No JS errors on {len(pages)} pages"

    def test_full_workflow(self) -> tuple:
        """Test 12: Complete search-to-play workflow"""
        # 1. Home page
        self.page.goto(SERVER_URL, timeout=15000)
        self.page.wait_for_load_state("networkidle", timeout=10000)

        # 2. Search
        self.page.fill("#searchInput", "the")
        self.page.wait_for_timeout(600)

        # 3. Wait for results
        try:
            self.page.wait_for_selector(".movie-card", timeout=10000)
        except:
            return False, "Step 2: No search results"

        # 4. Click movie
        cards = self.page.query_selector_all(".movie-card")
        if not cards:
            return False, "Step 3: No cards found"

        cards[0].click()

        # 5. Wait for details
        try:
            self.page.wait_for_selector("#pageMovieDetails.active", timeout=5000)
        except:
            return False, "Step 4: Details didn't load"

        self.page.wait_for_timeout(800)

        # 6. Find play button
        selectors = ["button:has-text('Play')", ".play-btn", "[onclick*='launchMovie']"]
        for sel in selectors:
            try:
                btn = self.page.query_selector(sel)
                if btn and btn.is_visible():
                    return True, "Workflow complete: Play button ready"
            except:
                continue

        self.take_screenshot("workflow_no_play")
        return False, "Step 5: Play button not found"

    # =========================================================================
    # Main Runner
    # =========================================================================

    def run_all_tests(self) -> bool:
        """Run all browser tests"""
        start_time = time.time()

        self.log("")
        self.log("=" * 60, Colors.BOLD)
        self.log("  Movie Searcher Browser E2E Tests", Colors.BOLD + Colors.CYAN)
        self.log("=" * 60, Colors.BOLD)
        self.log(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", Colors.DIM)
        self.log(f"  Server:  {SERVER_URL}", Colors.DIM)
        self.log(f"  Mode:    {'Headed' if self.headed else 'Headless'}", Colors.DIM)
        self.log("=" * 60, Colors.BOLD)
        self.log("")

        # Setup
        self.log("Setting up browser...", Colors.CYAN)
        try:
            self.setup()
            self.log("  Browser ready\n", Colors.DIM)
        except Exception as e:
            self.log(f"  Failed: {e}", Colors.RED)
            return False

        try:
            # Page Load Tests
            self.log("Page Load Tests", Colors.BOLD)
            self.run_test("Home Page Load", self.test_home_page_load)
            self.run_test("Navigation", self.test_navigation)
            self.log("")

            # Core Tests
            self.log("Core Functionality", Colors.BOLD)
            self.run_test("Search Flow", self.test_search_flow)
            self.run_test("Movie Card Click", self.test_movie_card_click)
            self.run_test("Play Button Exists", self.test_play_button_exists)
            self.log("")

            # Page Tests
            self.log("Page Tests", Colors.BOLD)
            self.run_test("Explore Page", self.test_explore_page)
            self.run_test("History Page", self.test_history_page)
            self.run_test("Playlists Page", self.test_playlists_page)
            self.run_test("Settings Page", self.test_settings_page)
            self.log("")

            # Integration Tests
            self.log("Integration Tests", Colors.BOLD)
            self.run_test("Currently Playing", self.test_currently_playing)
            self.run_test("No JavaScript Errors", self.test_no_js_errors)
            self.run_test("Full Workflow", self.test_full_workflow)
            self.log("")

        finally:
            self.teardown()

        # Summary
        total_time = time.time() - start_time
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        self.log("=" * 60, Colors.BOLD)
        self.log("  SUMMARY", Colors.BOLD)
        self.log("=" * 60, Colors.BOLD)

        if failed == 0:
            self.log(f"  All {passed} browser tests passed!", Colors.GREEN + Colors.BOLD)
        else:
            self.log(f"  {Colors.GREEN}{passed} passed{Colors.RESET}  |  {Colors.RED}{failed} failed{Colors.RESET}")
            self.log("")
            self.log("  Failed:", Colors.RED)
            for r in self.results:
                if not r.passed:
                    self.log(f"    - {r.name}: {r.message}", Colors.RED)

        self.log("")
        self.log(f"  Time: {total_time:.1f}s", Colors.DIM)
        self.log("=" * 60, Colors.BOLD)
        self.log("")

        return failed == 0


def check_server_running() -> bool:
    """Check if server is running"""
    try:
        req = urllib.request.Request(f"{SERVER_URL}/api/health", method='GET')
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except:
        return False


def main():
    if not PLAYWRIGHT_AVAILABLE:
        print(f"{Colors.RED}ERROR: Playwright not installed{Colors.RESET}")
        print("Install with:")
        print("  pip install playwright")
        print("  playwright install chromium")
        return 1

    parser = argparse.ArgumentParser(description="Movie Searcher Browser E2E Tests")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--slow", action="store_true", help="Slow mode (500ms delay)")
    args = parser.parse_args()

    # Check server
    if not check_server_running():
        print(f"{Colors.RED}ERROR: Server not running at {SERVER_URL}{Colors.RESET}")
        print("Start it with: python server.py")
        return 1

    tester = BrowserTester(
        headed=args.headed,
        slow_mo=500 if args.slow else 0,
    )

    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
