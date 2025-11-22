// Setup Page Functions

async function loadCurrentFolder() {
    const setupCurrentFolderEl = document.getElementById('setupCurrentFolder');
    if (setupCurrentFolderEl) {
        setupCurrentFolderEl.textContent = 'Loading...';
    }
    
    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})  // Empty body to get current config
        });
        
        const data = await response.json();
        
        if (response.ok) {
            const folderPath = data.movies_folder || 'Not set';
            if (setupCurrentFolderEl) {
                setupCurrentFolderEl.textContent = folderPath;
            }
            
            // Also update setup checkboxes if present
            if (data.settings) {
                const closeVlcEl = document.getElementById('setupCloseExistingVlc');
                const launchSubsEl = document.getElementById('setupLaunchWithSubtitlesOn');
                
                if (closeVlcEl && data.settings.close_existing_vlc !== undefined) {
                    closeVlcEl.checked = data.settings.close_existing_vlc;
                }
                if (launchSubsEl && data.settings.launch_with_subtitles_on !== undefined) {
                    launchSubsEl.checked = data.settings.launch_with_subtitles_on;
                }
            }
            
            return folderPath;
        } else {
            if (setupCurrentFolderEl) {
                setupCurrentFolderEl.textContent = 'Error loading';
            }
            return null;
        }
    } catch (error) {
        if (setupCurrentFolderEl) {
            setupCurrentFolderEl.textContent = 'Error loading';
        }
        return null;
    }
}

async function loadStats() {
    const setupStatsEl = document.getElementById('setupStats');
    const statsEl = document.getElementById('stats');
    
    try {
        const response = await fetch('/api/stats');
        const data = await response.json();
        
        if (!response.ok) {
            console.error('Failed to load stats');
            return;
        }
        
        // Format numbers
        const totalMovies = data.total_movies || 0;
        const watchedCount = data.watched_count || 0;
        const watchedPercent = totalMovies > 0 ? Math.round((watchedCount / totalMovies) * 100) : 0;
        
        const statsHtml = `
            <div class="stats-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px;">
                <div class="stat-card" style="background: #1a1a1a; padding: 15px; border-radius: 6px; border: 1px solid #3a3a3a;">
                    <div style="font-size: 12px; color: #999; margin-bottom: 5px;">Total Movies</div>
                    <div style="font-size: 24px; font-weight: 500; color: #fff;">${totalMovies}</div>
                </div>
                <div class="stat-card" style="background: #1a1a1a; padding: 15px; border-radius: 6px; border: 1px solid #3a3a3a;">
                    <div style="font-size: 12px; color: #999; margin-bottom: 5px;">Watched</div>
                    <div style="font-size: 24px; font-weight: 500; color: #4caf50;">${watchedCount} <span style="font-size: 14px; color: #666;">(${watchedPercent}%)</span></div>
                </div>
            </div>
        `;
        
        if (setupStatsEl) {
            setupStatsEl.innerHTML = statsHtml;
        }
        
        if (statsEl) {
            statsEl.innerHTML = `
                <div>Total Movies: <span style="color: #fff;">${totalMovies}</span></div>
                <div>Watched: <span style="color: #4caf50;">${watchedCount}</span> (${watchedPercent}%)</div>
            `;
        }
        
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

async function saveFolderPath() {
    const input = document.getElementById('folderPathInput');
    let folderPath = input.value.trim();
    
    if (!folderPath) {
        showStatus('Please enter a folder path', 'error');
        return;
    }
    
    // Normalize the path (handle /, \, \\)
    folderPath = normalizePath(folderPath);
    
    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({movies_folder: folderPath})
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showStatus('Movies folder updated successfully', 'success');
            loadCurrentFolder();
            loadStats();
            hideFolderDialog();
        } else {
            showStatus('Failed to update folder: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (error) {
        showStatus('Failed to update folder: ' + error.message, 'error');
    }
}

async function loadHiddenMovies() {
    const container = document.getElementById('hiddenMoviesList');
    container.style.display = 'block';
    container.innerHTML = '<div class="loading">Loading hidden movies...</div>';

    try {
        const response = await fetch('/api/hidden-movies');
        if (!response.ok) throw new Error('Failed to load hidden movies');

        const data = await response.json();
        const movies = data.movies || [];

        if (movies.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding: 20px;">No hidden movies</div>';
            return;
        }

        let html = '<div class="movie-grid">';
        movies.forEach(movie => {
            html += createMovieCard(movie, {
                showMenu: false,
                showRating: true,
                watchStatusControl: false, // Don't show watch status for hidden movies
                customButtons: `<button class="btn btn-success" style="width:100%" onclick="event.stopPropagation(); unhideMovie(${movie.id})">Unhide</button>`
            });
        });
        html += '</div>';
        container.innerHTML = html;

        // Initialize star ratings
        initAllStarRatings();
        
        // Restore scroll position if available
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
    } catch (error) {
        container.innerHTML = `<div class="status-message error">Error: ${error.message}</div>`;
    }
}

async function unhideMovie(movieId) {
    try {
        const response = await fetch(`/api/movie/${movieId}/unhide`, {
            method: 'POST'
        });

        if (response.ok) {
            showStatus('Movie unhidden', 'success');
            loadHiddenMovies(); // Reload list
        } else {
            showStatus('Failed to unhide movie', 'error');
        }
    } catch (error) {
        console.error('Error unhiding movie:', error);
        showStatus('Error unhiding movie', 'error');
    }
}

async function loadDuplicateMovies() {
    const container = document.getElementById('duplicateMoviesList');
    container.style.display = 'block';
    container.innerHTML = '<div class="loading">Searching for duplicates...</div>';

    try {
        const response = await fetch('/api/duplicates');
        if (!response.ok) throw new Error('Failed to load duplicates');

        const data = await response.json();
        const duplicates = data.duplicates || [];

        if (duplicates.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding: 20px;">No duplicate movies found</div>';
            return;
        }

        let html = '<div class="duplicates-list">';
        duplicates.forEach(group => {
            html += `
                <div class="duplicate-group" style="margin-bottom: 30px; border-bottom: 1px solid #3a3a3a; padding-bottom: 20px;">
                    <h4 style="color: #fff; margin-bottom: 15px;">${escapeHtml(group.name)} (${group.count})</h4>
                    <div class="movie-grid">
                        ${group.movies.map(movie => createMovieCard(movie)).join('')}
                    </div>
                </div>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
        
        // Initialize star ratings for the new cards
        if (typeof initAllStarRatings === 'function') {
            initAllStarRatings();
        }
        
        // Restore scroll position if available
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
        
    } catch (error) {
        container.innerHTML = `<div class="status-message error">Error: ${error.message}</div>`;
    }
}

function loadSetupPage() {
    loadCurrentFolder();
    loadStats();
    
    // Clear dynamic lists to avoid staleness
    const hiddenContainer = document.getElementById('hiddenMoviesList');
    if (hiddenContainer) hiddenContainer.style.display = 'none';
    
    const recleanStatus = document.getElementById('recleanStatus');
    if (recleanStatus) recleanStatus.style.display = 'none';
}