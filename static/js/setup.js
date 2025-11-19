async function loadStats() {
    try {
        const response = await fetch('/api/stats');
        const data = await response.json();
        const pathCount = Array.isArray(data.indexed_paths) ? data.indexed_paths.length : 0;
        if (stats) {
            stats.innerHTML = `
                <div>${data.total_movies} movies indexed total</div>
            `;
        }
        return data;
    } catch (error) {
        console.error('Stats error:', error);
        return null;
    }
}

async function loadCurrentFolder() {
    try {
        const response = await fetch('/api/config');
        const data = await response.json();
        const folderPath = data.movies_folder || data.default_folder || 'Not set';
        const currentFolderEl = document.getElementById('currentFolder');
        const setupCurrentFolderEl = document.getElementById('setupCurrentFolder');
        if (currentFolderEl) currentFolderEl.textContent = folderPath;
        if (setupCurrentFolderEl) setupCurrentFolderEl.textContent = folderPath;
    } catch (error) {
        console.error('Config error:', error);
        const currentFolderEl = document.getElementById('currentFolder');
        const setupCurrentFolderEl = document.getElementById('setupCurrentFolder');
        if (currentFolderEl) currentFolderEl.textContent = 'Error loading';
        if (setupCurrentFolderEl) setupCurrentFolderEl.textContent = 'Error loading';
    }
}

async function loadSetupPage() {
    await loadCurrentFolder();
    const statsData = await loadStats();
    const setupStatsEl = document.getElementById('setupStats');
    if (setupStatsEl && statsData) {
        const pathCount = Array.isArray(statsData.indexed_paths) ? statsData.indexed_paths.length : 0;
        setupStatsEl.innerHTML = `
            <div style="color: #e0e0e0; margin-bottom: 10px;">
                <div style="margin-bottom: 5px;"><strong>${statsData.total_movies}</strong> movies indexed total</div>
            </div>
        `;
    }
    
    // Load VLC settings
    try {
        const response = await fetch('/api/config');
        const data = await response.json();
        const settings = data.settings || {};
        
        // Load close existing VLC setting (default to true if not set)
        const closeExistingVlcEl = document.getElementById('setupCloseExistingVlc');
        if (closeExistingVlcEl) {
            // This setting is not saved to config currently, keep default checked state
            // In future, can be saved like: closeExistingVlcEl.checked = settings.close_existing_vlc !== false;
        }
        
        // Load launch with subtitles setting (default to true if not set)
        const launchWithSubtitlesOnEl = document.getElementById('setupLaunchWithSubtitlesOn');
        if (launchWithSubtitlesOnEl) {
            launchWithSubtitlesOnEl.checked = settings.launch_with_subtitles_on !== false;
        }
    } catch (error) {
        console.error('Error loading VLC settings:', error);
    }
}

function normalizePath(path) {
    // Normalize path separators for Windows
    // Convert forward slashes to backslashes, handle double backslashes
    if (!path) return path;
    
    // Replace forward slashes with backslashes
    path = path.replace(/\//g, '\\');
    
    // Normalize double backslashes (but preserve UNC paths like \\server\share)
    // Only normalize if it's not at the start (UNC path)
    if (path.startsWith('\\\\') && path.length > 2) {
        // UNC path - keep the first two backslashes, normalize the rest
        const rest = path.substring(2).replace(/\\\\+/g, '\\');
        path = '\\\\' + rest;
    } else {
        // Regular path - normalize all double+ backslashes
        path = path.replace(/\\\\+/g, '\\');
    }
    
    // Remove trailing backslash (unless it's a root like C:\)
    if (path.length > 3 && path.endsWith('\\') && path.match(/^[A-Za-z]:\\$/)) {
        // Keep it - it's a drive root
    } else if (path.endsWith('\\')) {
        path = path.slice(0, -1);
    }
    
    return path;
}

function showFolderDialog() {
    const dialog = document.getElementById('folderDialog');
    const input = document.getElementById('folderPathInput');
    
    // Load current path into input
    loadCurrentFolder().then(() => {
        const setupCurrentFolderEl = document.getElementById('setupCurrentFolder');
        const currentPath = setupCurrentFolderEl ? setupCurrentFolderEl.textContent : '';
        if (currentPath && currentPath !== 'Loading...' && currentPath !== 'Error loading' && currentPath !== 'Not set') {
            input.value = currentPath;
        } else {
            input.value = '';
        }
    });
    
    dialog.classList.add('active');
    // Focus the input after a short delay to ensure dialog is visible
    setTimeout(() => {
        input.focus();
        input.select();
    }, 100);
}

function hideFolderDialog() {
    const dialog = document.getElementById('folderDialog');
    dialog.classList.remove('active');
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
            html += createHiddenMovieCard(movie);
        });
        html += '</div>';
        container.innerHTML = html;

        // Initialize star ratings
        initAllStarRatings();
    } catch (error) {
        container.innerHTML = `<div class="status-message error">Error: ${error.message}</div>`;
    }
}

function createHiddenMovieCard(movie) {
    // Helper to extract filename from path
    function getFilename(path) {
        if (!path) return null;
        const parts = path.replace(/\\/g, '/').split('/');
        return parts[parts.length - 1];
    }

    // Prefer API endpoint by screenshot_id for reliability, fallback to image_path, then first screenshot
    let imageUrl = '';
    if (movie.screenshot_id) {
        // Use API endpoint - most reliable, handles path issues correctly
        imageUrl = `/api/screenshot/${movie.screenshot_id}`;
    } else if (movie.image_path) {
        // Use movie.image_path directly - check if it's a screenshot or movie image
        const filename = getFilename(movie.image_path);
        if (filename && movie.image_path.includes('screenshots')) {
            // Screenshot: use /screenshots/ endpoint
            imageUrl = `/screenshots/${encodeURIComponent(filename)}`;
        } else {
            // Movie image: use image_path_url if available (relative path from backend), otherwise extract from absolute path
            if (movie.image_path_url) {
                imageUrl = `/movies/${encodeURIComponent(movie.image_path_url)}`;
            } else {
                // Fallback: extract relative path manually (shouldn't happen if backend is correct)
                const pathParts = movie.image_path.replace(/\\/g, '/').split('/');
                const moviesIndex = pathParts.findIndex(p => p.toLowerCase().includes('movies') || p.toLowerCase().includes('movie'));
                if (moviesIndex >= 0 && moviesIndex < pathParts.length - 1) {
                    const relativePath = pathParts.slice(moviesIndex + 1).join('/');
                    imageUrl = `/movies/${encodeURIComponent(relativePath)}`;
                } else {
                    imageUrl = `/movies/${encodeURIComponent(filename)}`;
                }
            }
        }
    } else if (movie.screenshots && movie.screenshots.length > 0) {
        // Fallback to first screenshot if no image_path
        const firstScreenshot = movie.screenshots[0];
        if (firstScreenshot && firstScreenshot.id) {
            imageUrl = `/api/screenshot/${firstScreenshot.id}`;
        }
    }

    // Create slug for URL
    const slug = (movie.name || '').toString()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
    const cardClick = `openMovieHash(${movie.id}, '${encodeURIComponent(slug)}')`;

    return `
        <div class="movie-card" data-movie-id="${movie.id || ''}" onclick="${cardClick}">
            <div class="movie-card-image">
                ${imageUrl ? `<img src="${imageUrl}" alt="${escapeHtml(movie.name)}" loading="lazy" onerror="this.parentElement.innerHTML='No Image'" onload="const img = this; const container = img.parentElement; if (img.naturalWidth && img.naturalHeight) { const ar = img.naturalWidth / img.naturalHeight; container.style.aspectRatio = ar + ' / 1'; }">` : 'No Image'}
            </div>
            <div class="movie-card-body">
                <div class="movie-card-title">${escapeHtml(movie.name)}</div>
                <div class="movie-card-meta">
                    ${movie.year ? `<span class="year-link" onclick="event.stopPropagation(); navigateToExploreWithYear(${movie.year}, ${movie.id || 'null'});" title="Filter by ${movie.year}">${movie.year}</span>` : ''}
                </div>
                <div class="movie-card-buttons">
                    <button class="btn btn-success" onclick="event.stopPropagation(); unhideMovie(${movie.id})">Unhide</button>
                </div>
            </div>
        </div>
    `;
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

