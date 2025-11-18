// Utility Functions

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

function showStatus(message, type = 'info') {
    const statusEl = document.getElementById('status');
    if (!statusEl) return;
    
    statusEl.textContent = message;
    statusEl.className = `status ${type}`;
    statusEl.style.display = 'block';
    
    setTimeout(() => {
        statusEl.style.display = 'none';
    }, 3000);
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

function getFilename(path) {
    if (!path) return null;
    const parts = path.replace(/\\/g, '/').split('/');
    return parts[parts.length - 1];
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
        letterboxd: `https://letterboxd.com/search/${letterboxdSlug}/`,
        google: `https://www.google.com/search?q=${googleQuery}`,
        douban: `https://www.douban.com/search?q=${doubanQuery}`
    };
}

async function openFolder(path) {
    try {
        const response = await fetch('/api/open-folder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: path})
        });
        
        if (response.ok) {
            showStatus('Folder opened', 'success');
        } else {
            const data = await response.json();
            showStatus('Failed to open folder: ' + (data.detail || 'Unknown error'), 'error');
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

async function saveFolderPath() {
    const input = document.getElementById('folderPathInput');
    let folderPath = input.value.trim();
    
    if (!folderPath) {
        showStatus('Please enter a folder path', 'error');
        return;
    }
    
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

// Screenshot Processor Status

async function updateScreenshotProcessorStatus() {
    try {
        const response = await fetch('/api/frames/status');
        const data = await response.json();
        const statusEl = document.getElementById('screenshotProcessorStatus');
        
        if (!statusEl) return;
        
        const isRunning = data.is_running || false;
        const queueSize = data.queue_size || 0;
        const processedLastMinute = data.processed_last_minute || 0;
        
        statusEl.innerHTML = `
            <div class="status-item">
                <span class="status-label">Status:</span>
                <span class="status-value ${isRunning ? 'running' : 'idle'}">${isRunning ? 'Running' : 'Idle'}</span>
            </div>
            <div class="status-item">
                <span class="status-label">Queued:</span>
                <span class="status-value">${queueSize}</span>
            </div>
            <div class="status-item">
                <span class="status-label">Last min:</span>
                <span class="status-value">${processedLastMinute}</span>
            </div>
        `;
    } catch (error) {
        console.error('Error updating screenshot processor status:', error);
        const statusEl = document.getElementById('screenshotProcessorStatus');
        if (statusEl) {
            statusEl.innerHTML = '<div class="status-item"><span class="status-label">Status:</span><span class="status-value idle">Error</span></div>';
        }
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
