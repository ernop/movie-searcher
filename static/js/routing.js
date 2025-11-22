// Routing
// Initialize scroll positions from sessionStorage if available
let scrollPositions;
try {
    const saved = sessionStorage.getItem('movieSearcher_scrollPositions');
    scrollPositions = new Map(saved ? JSON.parse(saved) : []);
} catch (e) {
    console.warn('Failed to load scroll positions:', e);
    scrollPositions = new Map();
}

function saveScrollState() {
    try {
        sessionStorage.setItem('movieSearcher_scrollPositions', JSON.stringify([...scrollPositions]));
    } catch (e) {
        console.warn('Failed to save scroll positions:', e);
    }
}

function getRoute() {
    const hash = window.location.hash || '#/home';
    return hash.substring(1).split('?')[0]; // Remove # and query params
}

function saveScrollPosition() {
    const currentRoute = getRoute();
    if (!currentRoute) return;
    
    // Save window scroll position
    const scrollPos = window.scrollY;
    scrollPositions.set(currentRoute, scrollPos);
    saveScrollState(); // Persist to storage
    // console.log(`Saved scroll position for ${currentRoute}: ${scrollPos}`);
}

function restoreScrollPosition() {
    const currentRoute = getRoute();
    const scrollPos = scrollPositions.get(currentRoute);
    
    if (scrollPos !== undefined) {
        // Use requestAnimationFrame to ensure DOM has been updated, then restore scroll
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                window.scrollTo(0, scrollPos);
                console.log(`Restored scroll position for ${currentRoute}: ${scrollPos}`);
            });
        });
    } else {
        // No saved position, scroll to top
        requestAnimationFrame(() => {
            window.scrollTo(0, 0);
        });
    }
}

function getRouteParams() {
    const hash = window.location.hash || '#/home';
    const parts = hash.substring(1).split('?');
    if (parts.length < 2) return {};
    const params = new URLSearchParams(parts[1]);
    const result = {};
    for (const [key, value] of params.entries()) {
        result[key] = value;
    }
    return result;
}

function updateRouteParams(newParams) {
    const route = getRoute();
    const currentParams = getRouteParams();
    // Merge current params with new params
    const merged = { ...currentParams, ...newParams };
    // Remove params with null/undefined values
    Object.keys(merged).forEach(key => {
        if (merged[key] === null || merged[key] === undefined || merged[key] === 'all') {
            delete merged[key];
        }
    });
    // Build new URL
    const paramsStr = Object.keys(merged).length > 0 
        ? '?' + new URLSearchParams(merged).toString()
        : '';
    window.location.hash = route + paramsStr;
}

function navigateTo(route) {
    saveScrollPosition();
    window.location.hash = route;
    handleRoute();
}

// Back navigation that prefers real history; falls back to home
function historyBack() {
    // saveScrollPosition is handled by hashchange/popstate event listeners if we rely on them, 
    // but explicit navigateTo calls it. 
    // For history.back(), the popstate event fires.
    try {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            navigateTo('/home');
        }
    } catch (e) {
        navigateTo('/home');
    }
}

function handleRoute() {
    // Hash-first routing
    const route = getRoute();

    // If hash changed via browser back/forward, save previous position first? 
    // No, popstate happens after the change.
    // Ideally we save scroll on scroll events or before unload, but simpler is before navigateTo.
    // For popstate, we rely on browser's native restoration mostly, but we can manual restore if needed.
    
    // Save scroll of PREVIOUS route if we can track it, but it's tricky with hashchange.
    // Instead, we rely on saveScrollPosition being called before navigation actions.
    
    const pages = document.querySelectorAll('.page');
    pages.forEach(page => page.classList.remove('active'));
    
    // Update nav links
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.remove('active');
        if (link.getAttribute('href') === '#' + route) {
            link.classList.add('active');
        }
    });
    
    const pageHome = document.getElementById('pageHome');
    const pageExplore = document.getElementById('pageExplore');
    const pageAllMovies = document.getElementById('pageAllMovies');
    const pageSetup = document.getElementById('pageSetup');
    const pageDuplicates = document.getElementById('pageDuplicates');
    const pageMovieDetails = document.getElementById('pageMovieDetails');
    const pageHistory = document.getElementById('pageHistory');
    const pagePlaylists = document.getElementById('pagePlaylists');

    // Detail routes (hash-based)
    if (route.startsWith('/movie/')) {
        const parts = route.split('/').filter(Boolean); // ["movie", "{id}", "{slug?}"]
        if (parts.length >= 2) {
            const movieId = parseInt(parts[1], 10);
            if (pageMovieDetails && !Number.isNaN(movieId)) {
                pageMovieDetails.classList.add('active');
                loadMovieDetailsById(movieId);
                return;
            }
        }
    }

    // Direct playlist routes
    if (route.startsWith('/playlist/')) {
        const parts = route.split('/').filter(Boolean); // ["playlist", "{id}"]
        if (parts.length >= 2) {
            const playlistId = parseInt(parts[1], 10);
            if (pagePlaylists && !Number.isNaN(playlistId)) {
                pagePlaylists.classList.add('active');
                handlePlaylistRoute(playlistId);
                return;
            }
        }
    }
    
    if (route === '/home' || route === '/') {
         if (pageHome) pageHome.classList.add('active');
         updateClearButtonVisibility();
         // Home page content is usually persistent, so just restore scroll
         restoreScrollPosition();
     } else if (route === '/explore') {
         if (pageExplore) {
             pageExplore.classList.add('active');
             // If returning to explore, don't re-fetch if we have content, just restore scroll
             // But loadExploreMovies handles state. We should let it decide.
             // We'll pass a flag or handle it in loadExploreMovies
             loadExploreMovies();
             // restoreScrollPosition handled inside loadExploreMovies after render
         }
     } else if (route === '/all-movies') {
         if (pageAllMovies) {
             pageAllMovies.classList.add('active');
             loadAllMovies();
             // restoreScrollPosition handled inside loadAllMovies after render
         }
     } else if (route === '/history') {
         if (pageHistory) {
             pageHistory.classList.add('active');
             loadHistory();
             // restoreScrollPosition handled inside loadHistory after render
         }
     } else if (route === '/playlists') {
         if (pagePlaylists) {
             pagePlaylists.classList.add('active');
             loadPlaylistsPage();
             // restoreScrollPosition handled inside loadPlaylistsPage
         }
     } else if (route === '/setup') {
         if (pageSetup) {
             pageSetup.classList.add('active');
             loadSetupPage();
             restoreScrollPosition();
         }
    } else if (route === '/duplicates') {
         if (pageDuplicates) {
             pageDuplicates.classList.add('active');
             if (typeof loadDuplicateMovies === 'function') {
                 loadDuplicateMovies();
                 // restoreScrollPosition handled inside loadDuplicateMovies after render
             }
         }
    } else {
         navigateTo('/home');
     }
}

// Save scroll position as user scrolls (debounced)
let scrollSaveTimeout = null;
window.addEventListener('scroll', () => {
    if (scrollSaveTimeout) {
        clearTimeout(scrollSaveTimeout);
    }
    scrollSaveTimeout = setTimeout(() => {
        const currentRoute = getRoute();
        const scrollPos = window.scrollY;
        scrollPositions.set(currentRoute, scrollPos);
        saveScrollState();
    }, 100); // Save after 100ms of no scrolling
});

window.addEventListener('beforeunload', saveScrollPosition);

// For hash changes triggered by browser back/forward
window.addEventListener('hashchange', () => {
    // Handle the new route
    handleRoute();
});
window.addEventListener('popstate', handleRoute);
window.addEventListener('load', handleRoute);
window.addEventListener('load', startCurrentlyPlayingPolling);
window.addEventListener('load', updateScreenshotProcessorStatus);

// Auto-update screenshot processor status every 10 seconds
let screenshotStatusInterval = null;
function startScreenshotStatusPolling() {
    updateScreenshotProcessorStatus();
    if (screenshotStatusInterval) {
        clearInterval(screenshotStatusInterval);
    }
    screenshotStatusInterval = setInterval(updateScreenshotProcessorStatus, 10000);
}
function stopScreenshotStatusPolling() {
    if (screenshotStatusInterval) {
        clearInterval(screenshotStatusInterval);
        screenshotStatusInterval = null;
    }
}
window.addEventListener('load', startScreenshotStatusPolling);

