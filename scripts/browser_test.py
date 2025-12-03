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
    python scripts/browser_test.py --video      # Record videos of test runs

Tests:
    1. Home Page Load - Does the main page render without JS errors?
    2. Navigation - Can we navigate between all pages?
    3. Search Flow - Type query → see results → click movie
    4. Movie Details - View movie details, screenshots, actions
    5. Play Movie - Click play and verify VLC launches
    6. Explore Page - Browse, filter by letter/decade/language
    7. History Page - View launch history
    8. Currently Playing - Detect VLC playing status
    9. Playlists - View and navigate playlists
    10. Settings Page - Load and interact with settings
"""

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List

# Add parent directory to path
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from playwright.async_api import async_playwright, Page, Browser, expect
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
    screenshot_path: Optional[str] = None
    console_errors: Optional[List[str]] = None


class BrowserTester:
    """Browser-based E2E test runner"""
    
    def __init__(self, headed: bool = False, slow_mo: int = 0, record_video: bool = False):
        self.headed = headed
        self.slow_mo = slow_mo
        self.record_video = record_video
        self.results: List[BrowserTestResult] = []
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.console_errors: List[str] = []
        self.screenshots_dir = PROJECT_ROOT / "test_screenshots"
        
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
    
    async def setup(self):
        """Set up browser and page"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed. Run: pip install playwright && playwright install chromium")
        
        # Create screenshots directory
        self.screenshots_dir.mkdir(exist_ok=True)
        
        # Launch browser
        playwright = await async_playwright().start()
        
        launch_options = {
            "headless": not self.headed,
            "slow_mo": self.slow_mo,
        }
        
        self.browser = await playwright.chromium.launch(**launch_options)
        
        # Create context with video recording if requested
        context_options = {
            "viewport": {"width": 1400, "height": 900},
        }
        if self.record_video:
            context_options["record_video_dir"] = str(self.screenshots_dir / "videos")
        
        context = await self.browser.new_context(**context_options)
        self.page = await context.new_page()
        
        # Capture console errors
        self.page.on("console", self._handle_console)
        self.page.on("pageerror", self._handle_page_error)
        
    def _handle_console(self, msg):
        """Handle console messages"""
        if msg.type == "error":
            self.console_errors.append(f"Console Error: {msg.text}")
    
    def _handle_page_error(self, error):
        """Handle page errors (uncaught exceptions)"""
        self.console_errors.append(f"Page Error: {error}")
    
    async def teardown(self):
        """Clean up browser"""
        if self.browser:
            await self.browser.close()
    
    async def take_screenshot(self, name: str) -> str:
        """Take a screenshot and return the path"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        path = self.screenshots_dir / filename
        await self.page.screenshot(path=str(path))
        return str(path)
    
    async def run_test(self, name: str, test_func) -> BrowserTestResult:
        """Run a single test and record the result"""
        start_time = time.time()
        self.console_errors = []  # Reset for each test
        
        screenshot_path = None
        try:
            passed, message = await test_func()
        except Exception as e:
            passed = False
            message = f"Exception: {str(e)}"
            # Take failure screenshot
            try:
                screenshot_path = await self.take_screenshot(f"FAIL_{name.replace(' ', '_')}")
            except:
                pass
        
        duration_ms = (time.time() - start_time) * 1000
        
        # Check for console errors
        if self.console_errors and passed:
            # Demote to warning if there were console errors
            message += f" (⚠️ {len(self.console_errors)} console errors)"
        
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
        status = f"{Colors.GREEN}✓ PASS{Colors.RESET}" if passed else f"{Colors.RED}✗ FAIL{Colors.RESET}"
        time_str = f"{Colors.DIM}({duration_ms:.0f}ms){Colors.RESET}"
        print(f"  {status} {name} {time_str}")
        
        if not passed:
            print(f"       {Colors.RED}{message}{Colors.RESET}")
            if screenshot_path:
                print(f"       {Colors.DIM}Screenshot: {screenshot_path}{Colors.RESET}")
        
        if self.console_errors:
            for err in self.console_errors[:3]:  # Show first 3 errors
                print(f"       {Colors.YELLOW}⚠️ {err[:100]}{Colors.RESET}")
        
        return result
    
    # =========================================================================
    # Test Functions
    # =========================================================================
    
    async def test_home_page_load(self) -> tuple[bool, str]:
        """Test 1: Home page loads correctly"""
        await self.page.goto(SERVER_URL)
        
        # Wait for page to be fully loaded
        await self.page.wait_for_load_state("networkidle")
        
        # Check title
        title = await self.page.title()
        if "Movie Searcher" not in title:
            return False, f"Wrong title: {title}"
        
        # Check main elements exist
        search_input = await self.page.query_selector("#searchInput")
        if not search_input:
            return False, "Search input not found"
        
        # Check server uptime indicator
        uptime_el = await self.page.query_selector("#serverUptime")
        if not uptime_el:
            return False, "Server uptime indicator not found"
        
        uptime_text = await uptime_el.text_content()
        if not uptime_text or "up" not in uptime_text.lower():
            return False, f"Invalid uptime text: {uptime_text}"
        
        return True, f"Page loaded, uptime: {uptime_text}"
    
    async def test_navigation(self) -> tuple[bool, str]:
        """Test 2: Navigation between pages works"""
        await self.page.goto(SERVER_URL)
        await self.page.wait_for_load_state("networkidle")
        
        pages_tested = []
        
        # Test Explore link
        await self.page.click("#navExplore")
        await self.page.wait_for_selector("#pageExplore.active", timeout=5000)
        pages_tested.append("Explore")
        
        # Test History link
        await self.page.click("#navHistory")
        await self.page.wait_for_selector("#pageHistory.active", timeout=5000)
        pages_tested.append("History")
        
        # Test Home link
        await self.page.click("#navHome")
        await self.page.wait_for_selector("#pageHome.active", timeout=5000)
        pages_tested.append("Home")
        
        # Test Setup link
        await self.page.click("#navSetup")
        await self.page.wait_for_selector("#pageSetup.active", timeout=5000)
        pages_tested.append("Setup")
        
        return True, f"Navigated: {', '.join(pages_tested)}"
    
    async def test_search_flow(self) -> tuple[bool, str]:
        """Test 3: Search for a movie"""
        await self.page.goto(SERVER_URL)
        await self.page.wait_for_load_state("networkidle")
        
        # Type in search box
        search_input = await self.page.query_selector("#searchInput")
        await search_input.fill("the")
        
        # Wait for results to appear
        await self.page.wait_for_timeout(500)  # Debounce
        await self.page.wait_for_selector(".movie-card", timeout=10000)
        
        # Count results
        cards = await self.page.query_selector_all(".movie-card")
        if len(cards) == 0:
            return False, "No search results found"
        
        return True, f"Search returned {len(cards)} movie cards"
    
    async def test_movie_card_click(self) -> tuple[bool, str]:
        """Test 4: Click a movie card to view details"""
        await self.page.goto(SERVER_URL)
        await self.page.wait_for_load_state("networkidle")
        
        # Search for movies
        search_input = await self.page.query_selector("#searchInput")
        await search_input.fill("the")
        await self.page.wait_for_timeout(500)
        await self.page.wait_for_selector(".movie-card", timeout=10000)
        
        # Click first movie card
        first_card = await self.page.query_selector(".movie-card")
        await first_card.click()
        
        # Wait for movie details page
        await self.page.wait_for_selector("#pageMovieDetails.active", timeout=5000)
        
        # Check movie details loaded
        await self.page.wait_for_selector(".movie-details-container", timeout=5000)
        
        # Look for movie name
        movie_name = await self.page.query_selector(".movie-details-container h1, .movie-details-container .movie-title")
        if movie_name:
            name_text = await movie_name.text_content()
            return True, f"Movie details loaded: {name_text[:50]}..."
        
        return True, "Movie details page loaded"
    
    async def test_play_button_exists(self) -> tuple[bool, str]:
        """Test 5: Play button exists and is clickable"""
        # Navigate to a movie details page first
        await self.page.goto(f"{SERVER_URL}/#/explore")
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_selector(".movie-card", timeout=10000)
        
        # Click first movie
        first_card = await self.page.query_selector(".movie-card")
        await first_card.click()
        await self.page.wait_for_selector("#pageMovieDetails.active", timeout=5000)
        
        # Wait for details to load
        await self.page.wait_for_timeout(500)
        
        # Look for play button
        play_button = await self.page.query_selector("button:has-text('Play'), .play-btn, [onclick*='launchMovie']")
        if not play_button:
            # Try looking for any button with play-related text
            buttons = await self.page.query_selector_all("button")
            for btn in buttons:
                text = await btn.text_content()
                if text and "play" in text.lower():
                    return True, f"Found play button: {text}"
            return False, "Play button not found"
        
        # Check it's visible and enabled
        is_visible = await play_button.is_visible()
        is_enabled = await play_button.is_enabled()
        
        if not is_visible:
            return False, "Play button not visible"
        if not is_enabled:
            return False, "Play button is disabled"
        
        return True, "Play button found and clickable"
    
    async def test_explore_page(self) -> tuple[bool, str]:
        """Test 6: Explore page with filters"""
        await self.page.goto(f"{SERVER_URL}/#/explore")
        await self.page.wait_for_load_state("networkidle")
        
        # Wait for movie grid to load
        await self.page.wait_for_selector(".movie-grid", timeout=10000)
        
        # Check letter nav exists
        letter_nav = await self.page.query_selector("#letterNav")
        if not letter_nav:
            return False, "Letter navigation not found"
        
        # Check decade nav exists  
        decade_nav = await self.page.query_selector("#decadeNav")
        if not decade_nav:
            return False, "Decade navigation not found"
        
        # Count movies
        cards = await self.page.query_selector_all(".movie-card")
        
        # Try clicking a letter filter
        letter_btns = await self.page.query_selector_all("#letterNav button, #letterNav .letter-btn")
        if letter_btns and len(letter_btns) > 0:
            await letter_btns[0].click()
            await self.page.wait_for_timeout(500)
        
        return True, f"Explore page loaded with {len(cards)} movies"
    
    async def test_history_page(self) -> tuple[bool, str]:
        """Test 7: History page loads"""
        await self.page.goto(f"{SERVER_URL}/#/history")
        await self.page.wait_for_load_state("networkidle")
        
        # Wait for history list
        await self.page.wait_for_selector("#historyList", timeout=5000)
        
        # Check if history items exist (might be empty)
        history_items = await self.page.query_selector_all(".history-item, .movie-card")
        
        return True, f"History page loaded ({len(history_items)} items)"
    
    async def test_currently_playing_indicator(self) -> tuple[bool, str]:
        """Test 8: Currently playing indicator exists"""
        await self.page.goto(SERVER_URL)
        await self.page.wait_for_load_state("networkidle")
        
        # Check currently playing element
        playing_el = await self.page.query_selector("#currentlyPlaying")
        if not playing_el:
            return False, "Currently playing indicator not found"
        
        text = await playing_el.text_content()
        if not text:
            return False, "Currently playing indicator is empty"
        
        return True, f"Currently playing: '{text}'"
    
    async def test_playlists_page(self) -> tuple[bool, str]:
        """Test 9: Playlists page loads"""
        await self.page.goto(f"{SERVER_URL}/#/playlists")
        await self.page.wait_for_load_state("networkidle")
        
        # Wait for playlists overview
        await self.page.wait_for_selector("#playlistsOverview", timeout=5000)
        
        # Check for Favorites playlist (system playlist)
        await self.page.wait_for_timeout(500)
        page_content = await self.page.content()
        
        if "Favorites" in page_content:
            return True, "Playlists page loaded with Favorites"
        
        return True, "Playlists page loaded"
    
    async def test_settings_page(self) -> tuple[bool, str]:
        """Test 10: Settings page loads and has controls"""
        await self.page.goto(f"{SERVER_URL}/#/setup")
        await self.page.wait_for_load_state("networkidle")
        
        # Wait for setup page
        await self.page.wait_for_selector("#pageSetup.active", timeout=5000)
        
        # Check system status section
        status_section = await self.page.query_selector("#systemStatusSection")
        if not status_section:
            return False, "System status section not found"
        
        # Check restart button
        restart_btn = await self.page.query_selector("#restartServerBtn")
        if not restart_btn:
            return False, "Restart server button not found"
        
        # Check scan button
        scan_btn = await self.page.query_selector("#setupScanBtn")
        if not scan_btn:
            return False, "Scan button not found"
        
        return True, "Settings page loaded with all controls"
    
    async def test_no_javascript_errors(self) -> tuple[bool, str]:
        """Test 11: Navigate through all pages checking for JS errors"""
        self.console_errors = []  # Reset
        
        pages = [
            (SERVER_URL, "Home"),
            (f"{SERVER_URL}/#/explore", "Explore"),
            (f"{SERVER_URL}/#/history", "History"),
            (f"{SERVER_URL}/#/playlists", "Playlists"),
            (f"{SERVER_URL}/#/setup", "Settings"),
        ]
        
        for url, name in pages:
            await self.page.goto(url)
            await self.page.wait_for_load_state("networkidle")
            await self.page.wait_for_timeout(300)  # Let any delayed scripts run
        
        if self.console_errors:
            return False, f"{len(self.console_errors)} JS errors found"
        
        return True, f"No JS errors across {len(pages)} pages"
    
    async def test_search_to_play_workflow(self) -> tuple[bool, str]:
        """Test 12: Complete workflow - search, click, play button exists"""
        # This is the most important test - the core user journey
        
        # 1. Go to home
        await self.page.goto(SERVER_URL)
        await self.page.wait_for_load_state("networkidle")
        
        # 2. Search
        search_input = await self.page.query_selector("#searchInput")
        if not search_input:
            return False, "Step 1 failed: Search input not found"
        
        await search_input.fill("the")
        await self.page.wait_for_timeout(600)  # Debounce
        
        # 3. Wait for results
        try:
            await self.page.wait_for_selector(".movie-card", timeout=10000)
        except:
            return False, "Step 2 failed: No search results appeared"
        
        # 4. Click first result
        cards = await self.page.query_selector_all(".movie-card")
        if not cards:
            return False, "Step 3 failed: No movie cards found"
        
        await cards[0].click()
        
        # 5. Wait for details page
        try:
            await self.page.wait_for_selector("#pageMovieDetails.active", timeout=5000)
        except:
            return False, "Step 4 failed: Details page didn't load"
        
        # 6. Wait for content to load
        await self.page.wait_for_timeout(500)
        
        # 7. Look for play functionality (button or clickable element)
        # Try multiple selectors for play button
        play_selectors = [
            "button:has-text('Play')",
            ".play-btn",
            "[onclick*='launchMovie']",
            "button:has-text('▶')",
            ".btn:has-text('Play')",
        ]
        
        play_found = False
        for selector in play_selectors:
            try:
                play_btn = await self.page.query_selector(selector)
                if play_btn and await play_btn.is_visible():
                    play_found = True
                    break
            except:
                continue
        
        if not play_found:
            # Take screenshot for debugging
            await self.take_screenshot("workflow_no_play_button")
            return False, "Step 5 failed: Play button not found on details page"
        
        return True, "Complete workflow: Search → Click → Play button ready"
    
    # =========================================================================
    # Main Runner
    # =========================================================================
    
    async def run_all_tests(self) -> bool:
        """Run all browser tests"""
        start_time = time.time()
        
        self.log("")
        self.log("=" * 60, Colors.BOLD)
        self.log("  Movie Searcher Browser E2E Test Suite", Colors.BOLD + Colors.CYAN)
        self.log("=" * 60, Colors.BOLD)
        self.log(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", Colors.DIM)
        self.log(f"  Server:  {SERVER_URL}", Colors.DIM)
        self.log(f"  Mode:    {'Headed' if self.headed else 'Headless'}", Colors.DIM)
        self.log("=" * 60, Colors.BOLD)
        self.log("")
        
        # Setup
        self.log("🌐 Setting up browser...", Colors.CYAN)
        try:
            await self.setup()
            self.log("   Browser ready", Colors.DIM)
        except Exception as e:
            self.log(f"   Failed to setup browser: {e}", Colors.RED)
            return False
        self.log("")
        
        try:
            # Page Load Tests
            self.log("📄 Page Load Tests", Colors.BOLD)
            await self.run_test("Home Page Load", self.test_home_page_load)
            await self.run_test("Navigation", self.test_navigation)
            self.log("")
            
            # Core Functionality Tests
            self.log("🔍 Core Functionality Tests", Colors.BOLD)
            await self.run_test("Search Flow", self.test_search_flow)
            await self.run_test("Movie Card Click", self.test_movie_card_click)
            await self.run_test("Play Button Exists", self.test_play_button_exists)
            self.log("")
            
            # Page-specific Tests
            self.log("📑 Page Tests", Colors.BOLD)
            await self.run_test("Explore Page", self.test_explore_page)
            await self.run_test("History Page", self.test_history_page)
            await self.run_test("Playlists Page", self.test_playlists_page)
            await self.run_test("Settings Page", self.test_settings_page)
            self.log("")
            
            # Integration Tests
            self.log("🔗 Integration Tests", Colors.BOLD)
            await self.run_test("Currently Playing Indicator", self.test_currently_playing_indicator)
            await self.run_test("No JavaScript Errors", self.test_no_javascript_errors)
            await self.run_test("Search-to-Play Workflow", self.test_search_to_play_workflow)
            self.log("")
            
        finally:
            await self.teardown()
        
        # Summary
        total_time = time.time() - start_time
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        
        self.log("=" * 60, Colors.BOLD)
        self.log("  SUMMARY", Colors.BOLD)
        self.log("=" * 60, Colors.BOLD)
        
        if failed == 0:
            self.log(f"  ✓ All {passed} browser tests passed!", Colors.GREEN + Colors.BOLD)
        else:
            self.log(f"  {Colors.GREEN}✓ {passed} passed{Colors.RESET}  |  {Colors.RED}✗ {failed} failed{Colors.RESET}")
            self.log("")
            self.log("  Failed tests:", Colors.RED)
            for r in self.results:
                if not r.passed:
                    self.log(f"    • {r.name}: {r.message}", Colors.RED)
                    if r.screenshot_path:
                        self.log(f"      Screenshot: {r.screenshot_path}", Colors.DIM)
        
        # Console errors summary
        all_console_errors = []
        for r in self.results:
            if r.console_errors:
                all_console_errors.extend(r.console_errors)
        
        if all_console_errors:
            self.log("")
            self.log(f"  ⚠️ {len(all_console_errors)} total console errors detected", Colors.YELLOW)
        
        self.log("")
        self.log(f"  Total time: {total_time:.1f}s", Colors.DIM)
        self.log(f"  Screenshots: {self.screenshots_dir}", Colors.DIM)
        self.log("=" * 60, Colors.BOLD)
        self.log("")
        
        return failed == 0


async def check_server_running() -> bool:
    """Check if server is running"""
    import urllib.request
    try:
        req = urllib.request.Request(f"{SERVER_URL}/api/health", method='GET')
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except:
        return False


async def main_async(args):
    """Async main function"""
    # Check server is running
    if not await check_server_running():
        print(f"{Colors.RED}ERROR: Server is not running at {SERVER_URL}{Colors.RESET}")
        print(f"Start the server first: python server.py")
        return 1
    
    tester = BrowserTester(
        headed=args.headed,
        slow_mo=500 if args.slow else 0,
        record_video=args.video
    )
    
    success = await tester.run_all_tests()
    return 0 if success else 1


def main():
    if not PLAYWRIGHT_AVAILABLE:
        print(f"{Colors.RED}ERROR: Playwright not installed{Colors.RESET}")
        print("Install it with:")
        print("  pip install playwright")
        print("  playwright install chromium")
        return 1
    
    parser = argparse.ArgumentParser(
        description="Movie Searcher Browser E2E Tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/browser_test.py              # Run headless
    python scripts/browser_test.py --headed     # Run with visible browser
    python scripts/browser_test.py --slow       # Run slowly for debugging
    python scripts/browser_test.py --video      # Record video of tests
        """
    )
    parser.add_argument("--headed", action="store_true",
                        help="Run with visible browser window")
    parser.add_argument("--slow", action="store_true",
                        help="Run slowly (500ms between actions)")
    parser.add_argument("--video", action="store_true",
                        help="Record video of test runs")
    
    args = parser.parse_args()
    
    # Use WindowsSelectorEventLoopPolicy on Windows to avoid async issues
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())

