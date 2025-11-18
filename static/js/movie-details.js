// Movie Navigation

let savedHistoryScroll = null;

function openMovieHash(id, slug) {
    const safeSlug = (slug || '').toString();
    const current = getRoute();
    if (current === '/history') {
        const list = document.getElementById('historyList');
        if (list) savedHistoryScroll = list.scrollTop;
    }
    navigateTo(`/movie/${id}/${safeSlug}`);
}

async function goToRandomMovie() {
    try {
        const response = await fetch('/api/random-movie');
        if (!response.ok) {
            let errorMessage = 'Unknown error';
            try {
                const errorData = await response.json();
                errorMessage = errorData.detail || errorMessage;
            } catch (e) {
                errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            }
            showStatus('Failed to load random movie: ' + errorMessage, 'error');
            return;
        }
        
        const data = await response.json();
        const movieId = data.id;
        
        if (!movieId) {
            showStatus('Invalid response from server', 'error');
            return;
        }
        
        navigateTo(`/movie/${movieId}`);
    } catch (error) {
        showStatus('Error fetching random movie: ' + error.message, 'error');
    }
}

async function showRandomMovies() {
    try {
        const results = document.getElementById('results');
        if (!results) {
            showStatus('Results container not found', 'error');
            return;
        }
        
        results.innerHTML = '<div class="loading">Loading random movies...</div>';
        
        const response = await fetch('/api/random-movies?count=10');
        if (!response.ok) {
            let errorMessage = 'Unknown error';
            try {
                const errorData = await response.json();
                errorMessage = errorData.detail || errorMessage;
            } catch (e) {
                errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            }
            results.innerHTML = '<div class="empty-state">Failed to load random movies: ' + errorMessage + '</div>';
            showStatus('Failed to load random movies: ' + errorMessage, 'error');
            return;
        }
        
        const data = await response.json();
        const movies = data.results || [];
        
        if (movies.length === 0) {
            results.innerHTML = '<div class="empty-state">No movies found</div>';
            return;
        }
        
        displayResults(movies);
        showStatus(`Showing ${movies.length} random movies`, 'success');
    } catch (error) {
        const results = document.getElementById('results');
        if (results) {
            results.innerHTML = '<div class="empty-state">Error loading random movies: ' + error.message + '</div>';
        }
        showStatus('Error fetching random movies: ' + error.message, 'error');
    }
}

// Movie Actions

async function toggleWatched(movieId, currentStatus) {
    const cycleOrder = [null, 'want_to_watch', 'unwatched', 'watched'];
    const currentIdx = cycleOrder.indexOf(currentStatus === 'null' ? null : currentStatus);
    const nextIdx = (currentIdx + 1) % cycleOrder.length;
    const nextStatus = cycleOrder[nextIdx];
    
    try {
        const response = await fetch('/api/watched', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                watch_status: nextStatus
            })
        });
        
        if (response.ok) {
            const route = getRoute();
            if (route === '/explore') {
                loadExploreMovies();
            } else if (route.startsWith('/movie/')) {
                loadMovieDetailsById(movieId);
            }
        }
    } catch (error) {
        console.error('Error updating watched status:', error);
    }
}

async function launchMovie(movieId) {
    try {
        const selectedSubtitle = currentSubtitles[movieId] || null;
        
        const response = await fetch('/api/launch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                subtitle_file: selectedSubtitle
            })
        });
        
        if (response.ok) {
            showStatus('Movie launched', 'success');
            updateCurrentlyPlaying();
        } else {
            const data = await response.json();
            showStatus('Failed to launch: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (error) {
        showStatus('Error launching movie: ' + error.message, 'error');
    }
}

let selectedIndex = -1;
let currentResults = [];
let watchedViewActive = false;
let exploreViewActive = false;
let currentSubtitles = {};
// Search pagination state for infinite scroll
let searchPage = {
    query: '',
    filterType: 'all',
    language: 'all',
    offset: 0,
    limit: 50,
    total: 0,
    loading: false,
    done: false,
    requestId: -1,
    observer: null
};
// Explore page state - only for tracking current page, not for building requests
let currentExplorePage = 1;
const EXPLORE_PER_PAGE = 15;
// Request sequencing to handle out-of-order AJAX responses
let searchRequestCounter = 0;
let lastDisplayedRequestId = -1;
let searchAbortController = null;
let searchDebounceTimer = null;
let currentResultsAreLite = true;

