// Utility Functions

// Persistent settings loaded from the server (default values applied client-side)
window.userSettings = window.userSettings || {};

function shouldShowMovieSizes() {
    // Default to true unless explicitly disabled in settings
    const settings = window.userSettings || {};
    return settings.show_full_movie_size !== false;
}

function applyMovieSizeVisibilitySetting() {
    const showSizes = shouldShowMovieSizes();
    document.body.classList.toggle('hide-movie-sizes', !showSizes);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeJsString(text) {
    return text
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/"/g, '\\"');
}

function formatSize(bytes) {
    if (!bytes || bytes === 0) return '';
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(2)} ${sizes[i]}`;
}

function formatMinutes(minutes) {
    if (!minutes || minutes === 0) return '';
    const hours = Math.floor(minutes / 60);
    const mins = Math.round(minutes % 60);
    if (hours > 0) {
        return `${hours}h ${mins}m`;
    }
    return `${mins}m`;
}

function showStatus(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    // Icon mapping
    const icons = {
        success: '✓',
        error: '✗',
        info: 'ℹ',
        warning: '⚠'
    };

    // Create toast element
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span class="toast-icon">${icons[type] || icons.info}</span>
        <span class="toast-message">${escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    // Auto-dismiss
    setTimeout(() => {
        toast.classList.add('toast-exit');
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 200); // Match animation duration
    }, duration);
}

function normalizePath(path) {
    if (!path) return path;

    path = path.replace(/\//g, '\\');

    if (path.startsWith('\\\\') && path.length > 2) {
        const rest = path.substring(2).replace(/\\\\+/g, '\\');
        path = '\\\\' + rest;
    } else {
        path = path.replace(/\\\\+/g, '\\');
    }

    if (path.length > 3 && path.endsWith('\\') && !path.match(/^[A-Za-z]:\\$/)) {
        path = path.substring(0, path.length - 1);
    }

    return path;
}

function isValidAbsolutePath(path) {
    if (!path || !path.trim()) return false;
    path = path.trim();
    // Windows: Check for drive letter (C:\ or C:/) or UNC path (\\)
    // Must have drive letter followed by colon and slash to be a valid absolute path
    // Examples: D:\movies, C:/Movies, \\server\share
    if (path.match(/^[A-Za-z]:[\\/]/) || path.startsWith('\\\\')) {
        return true;
    }
    // Note: We do NOT accept Unix-style paths (starting with /) on Windows
    // because they get normalized to \path which is not a valid absolute path
    return false;
}

function getFilename(path) {
    if (!path) return null;
    const parts = path.replace(/\\/g, '/').split('/');
    return parts[parts.length - 1];
}

function getMovieSlug(movie) {
    return (movie.name || '').toString()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
}

function getMovieImageUrl(movie) {
    // Primary: use screenshot_id via API endpoint (most reliable)
    if (movie.screenshot_id) {
        return `/api/screenshot/${movie.screenshot_id}`;
    }
    // Fallback: use movie image endpoint (ID-based, no paths exposed)
    if (movie.id) {
        return `/api/movie/${movie.id}/image`;
    }
    return '';
}

function formatDate(dateString) {
    if (!dateString) return '';
    const date = new Date(dateString);
    return date.toLocaleString();
}

function generateExternalLinks(movieName) {
    const cleanedName = movieName
        .replace(/\b(19|20)\d{2}\b/g, '')
        .replace(/\b(480p|720p|1080p|2160p|4K|BluRay|BRRip|WEB-DL|WEBRip|HDRip|DVDRip)\b/gi, '')
        .replace(/\b(x264|x265|H\.264|H\.265|HEVC)\b/gi, '')
        .replace(/\[.*?\]/g, '')
        .replace(/\(.*?\)/g, '')
        .trim();

    const letterboxdSlug = cleanedName
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');

    const googleQuery = encodeURIComponent(cleanedName + ' movie');
    const doubanQuery = encodeURIComponent(cleanedName);

    return {
        letterboxd: `https://letterboxd.com/film/${letterboxdSlug}/`,
        google: `https://www.google.com/search?q=${googleQuery}`,
        douban: `https://www.douban.com/search?q=${doubanQuery}`
    };
}

async function openFolder(path) {
    try {
        const response = await fetch('/api/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path })
        });

        if (response.ok) {
            showStatus('Folder opened', 'success');
        } else {
            const data = await response.json();
            let errorMsg = data.detail || 'Unknown error';
            if (typeof errorMsg === 'object') {
                errorMsg = JSON.stringify(errorMsg);
            }
            showStatus('Failed to open folder: ' + errorMsg, 'error');
        }
    } catch (error) {
        showStatus('Error opening folder: ' + error.message, 'error');
    }
}

// Folder Dialog Functions

function showFolderDialog() {
    const dialog = document.getElementById('folderDialog');
    const input = document.getElementById('folderPathInput');

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
}

function hideFolderDialog() {
    const dialog = document.getElementById('folderDialog');
    dialog.classList.remove('active');
}

async function browseFolder() {
    // Use file input with webkitdirectory to let user select a folder
    // Note: This requires selecting a file inside the folder, but helps with navigation
    const input = document.createElement('input');
    input.type = 'file';
    input.webkitdirectory = true;
    input.directory = true;
    input.style.display = 'none';
    document.body.appendChild(input);

    input.addEventListener('change', async (e) => {
        const files = e.target.files;
        if (files.length > 0) {
            const firstFile = files[0];
            const folderPathInput = document.getElementById('folderPathInput');

            // Try to extract path from file object
            // On some browsers, file.path contains the full path
            if (firstFile.path && firstFile.path.match(/^[A-Za-z]:/)) {
                // Extract directory from file path
                const filePath = firstFile.path.replace(/\//g, '\\');
                const lastBackslash = filePath.lastIndexOf('\\');
                if (lastBackslash > 0) {
                    const dirPath = filePath.substring(0, lastBackslash);
                    if (folderPathInput) {
                        folderPathInput.value = dirPath;
                        showStatus('Folder path extracted. Please verify it is correct.', 'info');
                    }
                }
            } else {
                // Can't extract full path - show instructions
                showStatus('Please copy the full path from Windows Explorer and paste it here (e.g., D:\\movies)', 'info');
                if (folderPathInput) {
                    folderPathInput.focus();
                }
            }
        }
        document.body.removeChild(input);
    });

    input.click();
}

async function saveFolderPath() {
    const input = document.getElementById('folderPathInput');
    let folderPath = input.value.trim();

    if (!folderPath) {
        showStatus('Please enter a folder path', 'error');
        return;
    }

    // Validate absolute path before normalizing
    if (!isValidAbsolutePath(folderPath)) {
        showStatus('Path must be absolute (e.g., D:\\movies or C:\\Movies)', 'error');
        return;
    }

    folderPath = normalizePath(folderPath);

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ movies_folder: folderPath })
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

// Currently Playing Functionality

let currentlyPlayingInterval = null;

async function updateCurrentlyPlaying() {
    try {
        const response = await fetch('/api/currently-playing');
        const data = await response.json();
        const playingEl = document.getElementById('currentlyPlaying');

        if (!playingEl) return;

        if (data.playing && data.playing.length > 0) {
            const firstMovie = data.playing[0];
            playingEl.className = 'currently-playing has-movie';
            playingEl.innerHTML = '<span class="prefix">now playing</span><span class="name"></span>';
            const nameEl = playingEl.querySelector('.name');
            if (nameEl) {
                nameEl.textContent = firstMovie.name;
            }
            if (firstMovie.id) {
                const slug = (firstMovie.name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
                playingEl.onclick = () => openMovieHash(firstMovie.id, encodeURIComponent(slug));
            } else {
                playingEl.onclick = null;
            }
        } else {
            playingEl.className = 'currently-playing';
            playingEl.textContent = 'nothing playing';
            playingEl.onclick = null;
        }
    } catch (error) {
        console.error('Error updating currently playing:', error);
    }
}

function startCurrentlyPlayingPolling() {
    updateCurrentlyPlaying();
    if (currentlyPlayingInterval) {
        clearInterval(currentlyPlayingInterval);
        currentlyPlayingInterval = null;
    }
}

function stopCurrentlyPlayingPolling() {
    if (currentlyPlayingInterval) {
        clearInterval(currentlyPlayingInterval);
        currentlyPlayingInterval = null;
    }
}

// Server Uptime Display

let serverUptimeInterval = null;

async function updateServerUptime() {
    const uptimeEl = document.getElementById('serverUptime');
    if (!uptimeEl) return;

    try {
        const response = await fetch('/api/health', { cache: 'no-store' });
        if (response.ok) {
            const data = await response.json();
            uptimeEl.textContent = `up ${data.uptime_formatted}`;
            uptimeEl.title = `Server uptime: ${data.uptime_formatted} (${Math.round(data.uptime_seconds)}s)`;
        } else {
            uptimeEl.textContent = 'server?';
            uptimeEl.title = 'Server not responding';
        }
    } catch (error) {
        uptimeEl.textContent = 'offline';
        uptimeEl.title = 'Cannot connect to server';
    }
}

function startServerUptimePolling() {
    // Update every 30 seconds
    serverUptimeInterval = setInterval(updateServerUptime, 30000);
}

function stopServerUptimePolling() {
    if (serverUptimeInterval) {
        clearInterval(serverUptimeInterval);
        serverUptimeInterval = null;
    }
}

// Keyboard Event Handlers

document.addEventListener('keydown', (e) => {
    const dialog = document.getElementById('folderDialog');
    const overlay = document.getElementById('mediaOverlay');

    if (dialog && dialog.classList.contains('active')) {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveFolderPath();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            hideFolderDialog();
        }
    } else if (overlay && overlay.classList.contains('active')) {
        if (e.key === 'Escape') {
            e.preventDefault();
            closeMediaOverlay();
        }
    }
});

// Dialog Click Outside Handler

document.addEventListener('click', (e) => {
    const dialog = document.getElementById('folderDialog');
    if (dialog && dialog.classList.contains('active') && e.target === dialog) {
        hideFolderDialog();
    }
});

// Copy Missing Movie Names to Clipboard

function copyMissingMovieNames() {
    const movies = window._currentMissingMovies;
    if (!movies || movies.length === 0) {
        showStatus('No movies to copy', 'error');
        return;
    }

    const lines = movies.map(movie => {
        const title = movie.name || 'Unknown title';
        const year = movie.year || '';
        return year ? `${title} ${year}` : title;
    });

    const text = lines.join('\n');

    navigator.clipboard.writeText(text).then(() => {
        showStatus(`Copied ${movies.length} movie names to clipboard`, 'success');
    }).catch(err => {
        console.error('Failed to copy:', err);
        showStatus('Failed to copy to clipboard', 'error');
    });
}
