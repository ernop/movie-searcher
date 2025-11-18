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

