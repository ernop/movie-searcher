// Application initialization

window.addEventListener('load', startCurrentlyPlayingPolling);

// Load stats and folder path on page load
loadStats();
loadLanguageFilters();
loadCurrentFolder();
updateCurrentlyPlaying();
updateServerUptime();
startServerUptimePolling();

// Update All Movies nav visibility based on setting
function updateAllMoviesNavVisibility(show) {
    const navAllMovies = document.getElementById('navAllMovies');
    if (navAllMovies) {
        navAllMovies.style.display = show ? '' : 'none';
    }
}

// Save VLC and interface settings when checkboxes change
document.addEventListener('DOMContentLoaded', () => {
    // Launch with subtitles setting
    const launchWithSubtitlesOnEl = document.getElementById('setupLaunchWithSubtitlesOn');
    if (launchWithSubtitlesOnEl) {
        launchWithSubtitlesOnEl.addEventListener('change', async () => {
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        settings: {
                            launch_with_subtitles_on: launchWithSubtitlesOnEl.checked
                        }
                    })
                });
                if (response.ok) {
                    showStatus('Setting saved', 'success');
                } else {
                    const data = await response.json();
                    showStatus('Failed to save setting: ' + (data.detail || 'Unknown error'), 'error');
                }
            } catch (error) {
                showStatus('Failed to save setting: ' + error.message, 'error');
            }
        });
    }

    // Save show all movies tab setting when checkbox changes
    const showAllMoviesTabEl = document.getElementById('setupShowAllMoviesTab');
    if (showAllMoviesTabEl) {
        showAllMoviesTabEl.addEventListener('change', async () => {
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        settings: {
                            show_all_movies_tab: showAllMoviesTabEl.checked
                        }
                    })
                });
                if (response.ok) {
                    showStatus('Setting saved', 'success');
                    updateAllMoviesNavVisibility(showAllMoviesTabEl.checked);
                } else {
                    const data = await response.json();
                    showStatus('Failed to save setting: ' + (data.detail || 'Unknown error'), 'error');
                }
            } catch (error) {
                showStatus('Failed to save setting: ' + error.message, 'error');
            }
        });
    }

    // Save file size visibility preference
    const showFullMovieSizeEl = document.getElementById('setupShowFullMovieSize');
    if (showFullMovieSizeEl) {
        showFullMovieSizeEl.addEventListener('change', async () => {
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        settings: {
                            show_full_movie_size: showFullMovieSizeEl.checked
                        }
                    })
                });
                if (response.ok) {
                    window.userSettings = {
                        ...(window.userSettings || {}),
                        show_full_movie_size: showFullMovieSizeEl.checked
                    };
                    if (typeof applyMovieSizeVisibilitySetting === 'function') {
                        applyMovieSizeVisibilitySetting();
                    }
                    showStatus('Setting saved', 'success');
                } else {
                    const data = await response.json();
                    showStatus('Failed to save setting: ' + (data.detail || 'Unknown error'), 'error');
                }
            } catch (error) {
                showStatus('Failed to save setting: ' + error.message, 'error');
            }
        });
    }
});

