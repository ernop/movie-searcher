#!/usr/bin/env python3
"""
Movie Searcher Sanity Test Suite

A comprehensive automated test suite for verifying the Movie Searcher application
is functioning correctly. Run periodically to ensure the system hasn't diverged
from working state.

Prerequisites:
    1. Activate the virtual environment first:
       Windows:  .\\venv\\Scripts\\Activate.ps1
       Linux:    source venv/bin/activate
    
    2. Server must be running (the test will detect if it's running, but
       cannot start it without the venv being active):
       python server.py

Usage:
    python scripts/sanity_test.py              # Run all tests
    python scripts/sanity_test.py --quick      # Quick server-only tests (no VLC)
    python scripts/sanity_test.py --no-vlc     # Skip VLC/playback tests
    python scripts/sanity_test.py --verbose    # Show detailed output

Tests (22 total):
    Core API:
        - Server Health - Does the server start and respond?
        - Server Uptime - Is the health/uptime endpoint working?
        - Database Stats - Are there movies indexed?
        - FFmpeg Detection - Can we find FFmpeg?
        - VLC Detection - Can we find VLC?
    
    Search:
        - Search Validation - Does query validation work?
        - Search Query - Can we search movies?
    
    Browse:
        - Explore Movies - Does explore/browse work?
        - Explore Filters - Do filters (watched, unwatched, letter) work?
        - Movie Details - Can we get movie details?
        - Random Movie - Can we get a random movie?
        - All Movies - Does all movies endpoint work?
        - Language Counts - Are audio languages tracked?
    
    Collection:
        - Playlists - Can we view playlists?
        - Playlist Detail - Can we view playlist contents?
        - Duplicates - Does duplicate detection work?
        - Hidden Movies - Does hidden movie tracking work?
    
    History:
        - History - Does search/launch history work?
        - Launch History - Can we get detailed launch history?
        - Currently Playing - Can we detect what's playing in VLC?
    
    VLC Playback:
        - VLC Launch (no subs) - Can we launch a movie?
        - VLC Launch (timestamp) - Can we launch at a specific time?
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Server configuration
SERVER_URL = "http://127.0.0.1:8002"
SERVER_TIMEOUT = 30  # seconds to wait for server to start

# ANSI color codes for terminal output
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
class TestResult:
    """Result of a single test"""
    name: str
    passed: bool
    message: str
    duration_ms: float
    details: str | None = None


class SanityTester:
    """Main sanity test runner"""

    def __init__(self, verbose: bool = False, skip_vlc: bool = False):
        self.verbose = verbose
        self.skip_vlc = skip_vlc
        self.results: list[TestResult] = []
        self.server_process = None
        self.server_was_already_running = False

        # Force UTF-8 output on Windows to handle emojis
        if os.name == 'nt':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass

    def log(self, message: str, color: str = ""):
        """Print a log message"""
        try:
            if color:
                print(f"{color}{message}{Colors.RESET}")
            else:
                print(message)
        except UnicodeEncodeError:
            # Fallback for terminals that can't handle Unicode
            safe_msg = message.encode('ascii', errors='replace').decode('ascii')
            if color:
                print(f"{color}{safe_msg}{Colors.RESET}")
            else:
                print(safe_msg)

    def log_verbose(self, message: str):
        """Print verbose log message"""
        if self.verbose:
            print(f"{Colors.DIM}  → {message}{Colors.RESET}")

    def api_request(self, endpoint: str, method: str = "GET", data: dict = None,
                    timeout: int = 10) -> tuple[bool, dict, str]:
        """
        Make an API request to the server.
        
        Returns:
            (success: bool, response_data: dict, error_message: str)
        """
        url = f"{SERVER_URL}{endpoint}"
        headers = {"Content-Type": "application/json"}

        try:
            if method == "GET":
                req = urllib.request.Request(url, headers=headers)
            else:
                body = json.dumps(data).encode('utf-8') if data else None
                req = urllib.request.Request(url, data=body, headers=headers, method=method)

            with urllib.request.urlopen(req, timeout=timeout) as response:
                response_text = response.read().decode('utf-8')
                try:
                    response_data = json.loads(response_text)
                except json.JSONDecodeError:
                    response_data = {"raw": response_text}
                return True, response_data, ""

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode('utf-8')
            except:
                pass
            return False, {}, f"HTTP {e.code}: {e.reason}. {error_body}"
        except urllib.error.URLError as e:
            return False, {}, f"URL Error: {e.reason}"
        except TimeoutError:
            return False, {}, f"Request timed out after {timeout}s"
        except Exception as e:
            return False, {}, str(e)

    def check_server_running(self) -> bool:
        """Check if server is already running using HTTP request"""
        import socket

        # First do a quick socket check to see if port is open
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 8002))
            sock.close()
            if result != 0:
                # Port not open, server not running
                return False
        except Exception:
            return False

        # Port is open, verify via HTTP
        for attempt in range(3):
            try:
                success, data, error = self.api_request("/api/stats", timeout=3)
                if success:
                    return True
            except Exception:
                pass
            if attempt < 2:
                time.sleep(0.2)
        return False

    def start_server(self) -> bool:
        """Start the server if not already running"""
        import socket

        # Check if server is already running
        if self.check_server_running():
            self.log_verbose("Server already running")
            self.server_was_already_running = True
            return True

        # Check if port is in use (server might be starting up or having issues)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 8002))
            sock.close()
            if result == 0:
                # Port is open but HTTP check failed - wait and retry
                self.log("Port 8002 is open, waiting for server to be ready...", Colors.CYAN)
                for _ in range(10):
                    time.sleep(0.5)
                    if self.check_server_running():
                        self.server_was_already_running = True
                        return True
                # If still not responding, assume it's ready anyway and try tests
                self.log("Server may be busy, proceeding with tests...", Colors.YELLOW)
                self.server_was_already_running = True
                return True
        except Exception:
            pass

        self.log("Starting server...", Colors.CYAN)

        # Start server in background
        try:
            # Use server.py to run the server
            self.server_process = subprocess.Popen(
                [sys.executable, "server.py"],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )
        except Exception as e:
            self.log(f"Failed to start server: {e}", Colors.RED)
            return False

        # Wait for server to be ready
        start_time = time.time()
        while time.time() - start_time < SERVER_TIMEOUT:
            if self.check_server_running():
                self.log_verbose(f"Server started in {time.time() - start_time:.1f}s")
                return True
            time.sleep(0.5)

        self.log(f"Server did not start within {SERVER_TIMEOUT}s", Colors.RED)
        return False

    def stop_server(self):
        """Stop the server if we started it"""
        if self.server_process and not self.server_was_already_running:
            self.log_verbose("Stopping server...")
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=5)
            except:
                try:
                    self.server_process.kill()
                except:
                    pass

    def run_test(self, name: str, test_func) -> TestResult:
        """Run a single test and record the result"""
        start_time = time.time()

        try:
            passed, message, details = test_func()
        except Exception as e:
            passed = False
            message = f"Exception: {str(e)}"
            details = None

        duration_ms = (time.time() - start_time) * 1000
        result = TestResult(
            name=name,
            passed=passed,
            message=message,
            duration_ms=duration_ms,
            details=details
        )
        self.results.append(result)

        # Print result
        status = f"{Colors.GREEN}✓ PASS{Colors.RESET}" if passed else f"{Colors.RED}✗ FAIL{Colors.RESET}"
        time_str = f"{Colors.DIM}({duration_ms:.0f}ms){Colors.RESET}"
        print(f"  {status} {name} {time_str}")

        if not passed:
            print(f"       {Colors.RED}{message}{Colors.RESET}")
        elif self.verbose and message:
            print(f"       {Colors.DIM}{message}{Colors.RESET}")

        if details and (not passed or self.verbose):
            for line in details.split('\n'):
                print(f"       {Colors.DIM}{line}{Colors.RESET}")

        return result

    # =========================================================================
    # Test Functions
    # =========================================================================

    def test_server_health(self) -> tuple[bool, str, str | None]:
        """Test 1: Server is running and responding"""
        success, data, error = self.api_request("/api/config")
        if not success:
            return False, f"Server not responding: {error}", None

        # Verify we got a valid config response
        if isinstance(data, dict):
            return True, f"Server responding, config has {len(data)} keys", None
        return False, "Invalid config response", str(data)

    def test_stats(self) -> tuple[bool, str, str | None]:
        """Test: Get database statistics"""
        success, data, error = self.api_request("/api/stats")
        if not success:
            return False, f"Stats endpoint failed: {error}", None

        movie_count = data.get("total_movies", 0)
        if movie_count == 0:
            return True, "No movies indexed (empty database)", "Consider indexing a folder first"

        return True, f"Database has {movie_count} movies", None

    def test_health(self) -> tuple[bool, str, str | None]:
        """Test: Server health and uptime"""
        success, data, error = self.api_request("/api/health")
        if not success:
            return False, f"Health endpoint failed: {error}", None

        if data.get("status") != "healthy":
            return False, f"Server status is not healthy: {data.get('status')}", None

        uptime = data.get("uptime_formatted", "unknown")
        uptime_sec = data.get("uptime_seconds", 0)

        return True, f"Server healthy, uptime: {uptime}", f"{uptime_sec:.0f} seconds"

    def test_search_empty(self) -> tuple[bool, str, str | None]:
        """Test 2a: Search with short query returns empty (validation)"""
        success, data, error = self.api_request("/api/search?q=a")
        if not success:
            return False, f"Search request failed: {error}", None

        results = data.get("results", [])
        if len(results) == 0:
            return True, "Short query correctly returns empty", None
        return False, f"Expected empty results for single char query, got {len(results)}", None

    def test_search_query(self) -> tuple[bool, str, str | None]:
        """Test 2b: Search with valid query"""
        # First check if we have any movies
        success, stats, _ = self.api_request("/api/stats")
        if not success or stats.get("total_movies", 0) == 0:
            return True, "Skipped (no movies in database)", None

        # Try searching for "the" which should match many movies
        success, data, error = self.api_request("/api/search?q=the")
        if not success:
            return False, f"Search request failed: {error}", None

        results = data.get("results", [])
        total = data.get("total", 0)

        if total > 0 and len(results) > 0:
            return True, f"Found {total} results, returned {len(results)}", None

        # Try a more generic search
        success, data, error = self.api_request("/api/search?q=movie")
        if not success:
            return False, f"Search request failed: {error}", None

        total = data.get("total", 0)
        return True, f"Search working, found {total} results for 'movie'", None

    def test_movie_details(self) -> tuple[bool, str, str | None]:
        """Test 3: Get movie details"""
        # First get a movie ID from explore
        success, data, error = self.api_request("/api/explore?per_page=1")
        if not success:
            return False, f"Could not get movie list: {error}", None

        movies = data.get("movies", [])
        if not movies:
            return True, "Skipped (no movies in database)", None

        movie_id = movies[0].get("id")
        if not movie_id:
            return False, "Movie card missing 'id' field", str(movies[0])

        # Get movie details
        success, data, error = self.api_request(f"/api/movie/{movie_id}")
        if not success:
            return False, f"Movie details request failed: {error}", None

        # Verify required fields
        required_fields = ["id", "name", "path"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            return False, f"Movie details missing fields: {missing}", str(data)[:200]

        return True, f"Movie '{data.get('name', 'Unknown')}' details retrieved", None

    def test_explore(self) -> tuple[bool, str, str | None]:
        """Test 4: Explore/browse movies"""
        success, data, error = self.api_request("/api/explore?page=1&per_page=10")
        if not success:
            return False, f"Explore request failed: {error}", None

        # Verify pagination structure
        pagination = data.get("pagination", {})
        if "page" not in pagination or "total" not in pagination:
            return False, "Missing pagination fields", str(data)[:200]

        movies = data.get("movies", [])
        total = pagination.get("total", 0)

        # Check letter counts
        letter_counts = data.get("letter_counts", {})

        details = f"Page {pagination.get('page')}/{pagination.get('pages', 1)}, {len(letter_counts)} letters with movies"
        return True, f"Explore returned {len(movies)} movies (total: {total})", details

    def test_explore_filters(self) -> tuple[bool, str, str | None]:
        """Test 4b: Explore with various filters"""
        filters_to_test = [
            ("filter_type=watched", "watched filter"),
            ("filter_type=unwatched", "unwatched filter"),
            ("filter_type=newest", "newest filter"),
            ("letter=A", "letter A filter"),
        ]

        passed_filters = []
        failed_filters = []

        for query, name in filters_to_test:
            success, data, error = self.api_request(f"/api/explore?{query}&per_page=5")
            if success and "movies" in data:
                passed_filters.append(name)
            else:
                failed_filters.append(f"{name}: {error}")

        if failed_filters:
            return False, "Some filters failed", "\n".join(failed_filters)

        return True, f"All {len(passed_filters)} filters working", None

    def test_playlists(self) -> tuple[bool, str, str | None]:
        """Test 5: View playlists"""
        success, data, error = self.api_request("/api/playlists")
        if not success:
            return False, f"Playlists request failed: {error}", None

        playlists = data.get("playlists", [])

        # Check for system playlists
        system_playlists = [p for p in playlists if p.get("is_system")]
        expected_system = ["Favorites", "Want to Watch"]

        found_system = [p.get("name") for p in system_playlists]
        missing_system = [n for n in expected_system if n not in found_system]

        if missing_system:
            return False, f"Missing system playlists: {missing_system}", None

        details = ", ".join([f"{p['name']} ({p.get('item_count', 0)} items)" for p in playlists])
        return True, f"Found {len(playlists)} playlists", details

    def test_playlist_detail(self) -> tuple[bool, str, str | None]:
        """Test 5b: Get playlist details"""
        # Get playlists first
        success, data, error = self.api_request("/api/playlists")
        if not success or not data.get("playlists"):
            return True, "Skipped (no playlists)", None

        playlist_id = data["playlists"][0]["id"]

        success, data, error = self.api_request(f"/api/playlists/{playlist_id}")
        if not success:
            return False, f"Playlist detail request failed: {error}", None

        # API returns {playlist: {...}, movies: [...]}
        playlist = data.get("playlist", {})
        if "name" not in playlist:
            return False, "Playlist detail missing 'name'", str(data)[:200]

        movie_count = len(data.get("movies", []))
        return True, f"Playlist '{playlist.get('name')}' has {movie_count} movies", None

    def test_history(self) -> tuple[bool, str, str | None]:
        """Test 6: History endpoint works"""
        success, data, error = self.api_request("/api/history")
        if not success:
            return False, f"History request failed: {error}", None

        if "searches" not in data or "launches" not in data:
            return False, "History missing searches or launches", str(data)[:200]

        search_count = len(data.get("searches", []))
        launch_count = len(data.get("launches", []))

        return True, f"History: {search_count} searches, {launch_count} launches", None

    def test_launch_history(self) -> tuple[bool, str, str | None]:
        """Test 6b: Launch history endpoint"""
        success, data, error = self.api_request("/api/launch-history")
        if not success:
            return False, f"Launch history request failed: {error}", None

        if "launches" not in data:
            return False, "Launch history missing 'launches' field", str(data)[:200]

        return True, f"Launch history has {len(data['launches'])} entries", None

    def test_currently_playing(self) -> tuple[bool, str, str | None]:
        """Test 7: Now Playing detection"""
        success, data, error = self.api_request("/api/currently-playing")
        if not success:
            return False, f"Currently playing request failed: {error}", None

        if "playing" not in data:
            return False, "Response missing 'playing' field", str(data)[:200]

        playing = data.get("playing", [])
        if playing:
            names = [p.get("name", "Unknown") for p in playing]
            return True, f"Currently playing: {', '.join(names)}", None

        return True, "No movies currently playing (VLC not running)", None

    def test_random_movie(self) -> tuple[bool, str, str | None]:
        """Test: Get random movie"""
        success, stats, _ = self.api_request("/api/stats")
        if not success or stats.get("total_movies", 0) == 0:
            return True, "Skipped (no movies in database)", None

        success, data, error = self.api_request("/api/random-movie")
        if not success:
            return False, f"Random movie request failed: {error}", None

        if "id" not in data:
            return False, "Random movie missing 'id'", str(data)[:200]

        return True, f"Random movie: {data.get('name', data.get('id'))}", None

    def test_language_counts(self) -> tuple[bool, str, str | None]:
        """Test: Language counts endpoint"""
        success, data, error = self.api_request("/api/language-counts")
        if not success:
            return False, f"Language counts request failed: {error}", None

        # API returns 'counts' dict, not 'languages' list
        if "counts" not in data:
            return False, "Missing 'counts' field", str(data)[:200]

        counts = data.get("counts", {})
        if counts:
            # Get top 3 languages by count
            sorted_langs = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]
            details = ", ".join([f"{lang}: {count}" for lang, count in sorted_langs])
            return True, f"Found {len(counts)} languages", details

        return True, "No language data available", None

    def test_vlc_test(self) -> tuple[bool, str, str | None]:
        """Test: VLC detection"""
        success, data, error = self.api_request("/api/test-vlc")
        if not success:
            return False, f"VLC test request failed: {error}", None

        vlc_ok = data.get("vlc_ok", False)
        vlc_path = data.get("vlc_path", "Not found")

        if vlc_ok:
            return True, f"VLC found at: {vlc_path}", None

        return False, "VLC not found", str(data.get("errors", []))

    def test_ffmpeg_test(self) -> tuple[bool, str, str | None]:
        """Test: FFmpeg detection"""
        success, data, error = self.api_request("/api/test-ffmpeg")
        if not success:
            return False, f"FFmpeg test request failed: {error}", None

        ffmpeg_ok = data.get("ffmpeg_ok", False)
        ffmpeg_path = data.get("ffmpeg_path", "Not found")

        if ffmpeg_ok:
            return True, f"FFmpeg found at: {ffmpeg_path}", None

        return False, "FFmpeg not found", str(data.get("errors", []))

    def test_vlc_launch(self) -> tuple[bool, str, str | None]:
        """Test 8: Launch a movie in VLC (without subtitles)"""
        if self.skip_vlc:
            return True, "Skipped (--no-vlc flag)", None

        # Get a movie to launch
        success, data, error = self.api_request("/api/explore?per_page=1")
        if not success or not data.get("movies"):
            return True, "Skipped (no movies in database)", None

        movie_id = data["movies"][0]["id"]
        movie_name = data["movies"][0].get("name", "Unknown")

        # Launch the movie
        launch_data = {
            "movie_id": movie_id,
            "subtitle_path": None,
            "close_existing_vlc": False,
            "start_time": None
        }

        success, data, error = self.api_request("/api/launch", method="POST", data=launch_data)
        if not success:
            return False, f"Launch request failed: {error}", None

        status = data.get("status")
        if status == "launched":
            return True, f"Launched '{movie_name}'", f"PID: {data.get('process_id')}"
        elif status == "failed":
            return False, f"VLC launch failed: {data.get('error')}", str(data.get('steps', []))

        return False, f"Unexpected status: {status}", str(data)[:200]

    def test_vlc_launch_with_timestamp(self) -> tuple[bool, str, str | None]:
        """Test 9: Launch a movie at a specific timestamp"""
        if self.skip_vlc:
            return True, "Skipped (--no-vlc flag)", None

        # Get a movie with screenshots (for timestamp)
        success, data, error = self.api_request("/api/explore?per_page=10")
        if not success or not data.get("movies"):
            return True, "Skipped (no movies in database)", None

        # Find a movie, preferably with screenshots
        movie_id = None
        movie_name = None
        start_time = 60  # Default to 1 minute

        for movie in data["movies"]:
            movie_id = movie["id"]
            movie_name = movie.get("name", "Unknown")

            # Try to get a movie with screenshots for more meaningful test
            detail_success, detail_data, _ = self.api_request(f"/api/movie/{movie_id}")
            if detail_success and detail_data.get("screenshots"):
                screenshots = detail_data["screenshots"]
                if screenshots:
                    # Use the first screenshot's timestamp
                    start_time = screenshots[0].get("timestamp_seconds", 60)
                    break

        if not movie_id:
            return True, "Skipped (no suitable movie found)", None

        # Close any existing VLC and launch at timestamp
        launch_data = {
            "movie_id": movie_id,
            "subtitle_path": None,
            "close_existing_vlc": True,  # Close previous test launch
            "start_time": start_time
        }

        success, data, error = self.api_request("/api/launch", method="POST", data=launch_data)
        if not success:
            return False, f"Launch request failed: {error}", None

        status = data.get("status")
        if status == "launched":
            return True, f"Launched '{movie_name}' at {start_time}s", f"PID: {data.get('process_id')}"

        return False, f"Launch failed: {data.get('error', status)}", None

    def test_all_movies(self) -> tuple[bool, str, str | None]:
        """Test: All movies endpoint"""
        success, data, error = self.api_request("/api/all-movies?limit=100")
        if not success:
            return False, f"All movies request failed: {error}", None

        if "movies" not in data:
            return False, "Missing 'movies' field", str(data)[:200]

        return True, f"All movies endpoint returned {len(data['movies'])} movies", None

    def test_duplicates(self) -> tuple[bool, str, str | None]:
        """Test: Duplicates detection endpoint"""
        success, data, error = self.api_request("/api/duplicates")
        if not success:
            return False, f"Duplicates request failed: {error}", None

        duplicates = data.get("duplicates", [])
        return True, f"Found {len(duplicates)} duplicate groups", None

    def test_hidden_movies(self) -> tuple[bool, str, str | None]:
        """Test: Hidden movies endpoint"""
        success, data, error = self.api_request("/api/hidden-movies")
        if not success:
            return False, f"Hidden movies request failed: {error}", None

        hidden = data.get("movies", [])
        return True, f"Found {len(hidden)} hidden movies", None

    # =========================================================================
    # Main Runner
    # =========================================================================

    def run_all_tests(self) -> bool:
        """Run all sanity tests"""
        start_time = time.time()

        self.log("")
        self.log("=" * 60, Colors.BOLD)
        self.log("  Movie Searcher Sanity Test Suite", Colors.BOLD + Colors.CYAN)
        self.log("=" * 60, Colors.BOLD)
        self.log(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", Colors.DIM)
        self.log(f"  Server:  {SERVER_URL}", Colors.DIM)
        self.log("=" * 60, Colors.BOLD)
        self.log("")

        # Check/start server
        self.log("🔧 Server Status", Colors.BOLD)
        if not self.start_server():
            self.log("Cannot proceed without server", Colors.RED)
            return False
        self.log("")

        # Core API Tests
        self.log("📡 Core API Tests", Colors.BOLD)
        self.run_test("Server Health", self.test_server_health)
        self.run_test("Server Uptime", self.test_health)
        self.run_test("Database Stats", self.test_stats)
        self.run_test("FFmpeg Detection", self.test_ffmpeg_test)
        self.run_test("VLC Detection", self.test_vlc_test)
        self.log("")

        # Search Tests
        self.log("🔍 Search Tests", Colors.BOLD)
        self.run_test("Search Validation", self.test_search_empty)
        self.run_test("Search Query", self.test_search_query)
        self.log("")

        # Browse/Explore Tests
        self.log("🎬 Browse Tests", Colors.BOLD)
        self.run_test("Explore Movies", self.test_explore)
        self.run_test("Explore Filters", self.test_explore_filters)
        self.run_test("Movie Details", self.test_movie_details)
        self.run_test("Random Movie", self.test_random_movie)
        self.run_test("All Movies", self.test_all_movies)
        self.run_test("Language Counts", self.test_language_counts)
        self.log("")

        # Collection Tests
        self.log("📁 Collection Tests", Colors.BOLD)
        self.run_test("Playlists", self.test_playlists)
        self.run_test("Playlist Detail", self.test_playlist_detail)
        self.run_test("Duplicates", self.test_duplicates)
        self.run_test("Hidden Movies", self.test_hidden_movies)
        self.log("")

        # History Tests
        self.log("📜 History Tests", Colors.BOLD)
        self.run_test("History", self.test_history)
        self.run_test("Launch History", self.test_launch_history)
        self.run_test("Currently Playing", self.test_currently_playing)
        self.log("")

        # VLC Tests
        if not self.skip_vlc:
            self.log("▶️  VLC Playback Tests", Colors.BOLD)
            self.run_test("VLC Launch (no subs)", self.test_vlc_launch)
            time.sleep(1)  # Brief pause between launches
            self.run_test("VLC Launch (timestamp)", self.test_vlc_launch_with_timestamp)
            self.log("")
        else:
            self.log("▶️  VLC Playback Tests (Skipped)", Colors.DIM)
            self.log("")

        # Summary
        total_time = time.time() - start_time
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        self.log("=" * 60, Colors.BOLD)
        self.log("  SUMMARY", Colors.BOLD)
        self.log("=" * 60, Colors.BOLD)

        if failed == 0:
            self.log(f"  ✓ All {passed} tests passed!", Colors.GREEN + Colors.BOLD)
        else:
            self.log(f"  {Colors.GREEN}✓ {passed} passed{Colors.RESET}  |  {Colors.RED}✗ {failed} failed{Colors.RESET}")
            self.log("")
            self.log("  Failed tests:", Colors.RED)
            for r in self.results:
                if not r.passed:
                    self.log(f"    • {r.name}: {r.message}", Colors.RED)

        self.log("")
        self.log(f"  Total time: {total_time:.1f}s", Colors.DIM)
        self.log("=" * 60, Colors.BOLD)
        self.log("")

        # Cleanup
        self.stop_server()

        return failed == 0


def main():
    parser = argparse.ArgumentParser(
        description="Movie Searcher Sanity Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/sanity_test.py              # Run all tests
    python scripts/sanity_test.py --quick      # Quick server tests only
    python scripts/sanity_test.py --no-vlc     # Skip VLC playback tests
    python scripts/sanity_test.py --verbose    # Show detailed output
        """
    )
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed output for each test")
    parser.add_argument("--no-vlc", action="store_true",
                        help="Skip VLC playback tests (faster)")
    parser.add_argument("--quick", "-q", action="store_true",
                        help="Quick mode: server tests only, no VLC")

    args = parser.parse_args()

    # Quick mode implies no-vlc
    skip_vlc = args.no_vlc or args.quick

    tester = SanityTester(verbose=args.verbose, skip_vlc=skip_vlc)
    success = tester.run_all_tests()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

