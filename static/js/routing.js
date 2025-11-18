// Routing
function getRoute() {
    const hash = window.location.hash || '#/home';
    return hash.substring(1).split('?')[0]; // Remove # and query params
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
    window.location.hash = route;
    handleRoute();
}

// Back navigation that prefers real history; falls back to home
function historyBack() {
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
    const pageSetup = document.getElementById('pageSetup');
    const pageMovieDetails = document.getElementById('pageMovieDetails');
    const pageHistory = document.getElementById('pageHistory');

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
    
    if (route === '/home' || route === '/') {
         if (pageHome) pageHome.classList.add('active');
         updateClearButtonVisibility();
     } else if (route === '/explore') {
         if (pageExplore) {
             pageExplore.classList.add('active');
             exploreViewActive = true;
             loadExploreMovies();
         }
     } else if (route === '/history') {
         if (pageHistory) {
             pageHistory.classList.add('active');
             loadHistory();
         }
     } else if (route === '/setup') {
         if (pageSetup) {
             pageSetup.classList.add('active');
             loadSetupPage();
         }
    } else {
         navigateTo('/home');
     }
}

window.addEventListener('hashchange', handleRoute);
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

