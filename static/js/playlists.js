// Playlists Management

let currentPlaylistId = null;
let currentPlaylistSort = 'date_added';
let currentPlaylistPage = 1;

// Initialize playlists page
async function loadPlaylistsPage() {
    // Switch to playlists page
    const pages = document.querySelectorAll('.page');
    pages.forEach(page => page.classList.remove('active'));
    const pagePlaylists = document.getElementById('pagePlaylists');
    if (pagePlaylists) {
        pagePlaylists.classList.add('active');
    }
    await loadPlaylistsOverview();
}

// Load playlists overview (shows all playlists)
async function loadPlaylistsOverview() {
    const overview = document.getElementById('playlistsOverview');
    const playlistView = document.getElementById('playlistView');

    overview.style.display = 'block';
    playlistView.style.display = 'none';

    try {
        const response = await fetch('/api/playlists');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        overview.innerHTML = renderPlaylistsOverview(data.playlists);
        
        // Restore scroll position if available
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
    } catch (error) {
        console.error('Error loading playlists:', error);
        overview.innerHTML = '<div class="empty-state">Error loading playlists</div>';
    }
}

// Render playlists overview
function renderPlaylistsOverview(playlists) {
    if (!playlists || playlists.length === 0) {
        return `
            <div class="empty-state">
                <h3>No playlists yet</h3>
                <p>Create your first playlist to organize your movies!</p>
                <button class="btn" onclick="showCreatePlaylistDialog()">Create Playlist</button>
            </div>
        `;
    }

    const systemPlaylists = playlists.filter(p => p.is_system);
    const userPlaylists = playlists.filter(p => !p.is_system);

    let html = '';

    // System playlists
    if (systemPlaylists.length > 0) {
        html += '<h3>Default Playlists</h3>';
        html += '<div class="playlists-grid">';
        systemPlaylists.forEach(playlist => {
            html += renderPlaylistCard(playlist);
        });
        html += '</div>';
    }

    // User playlists
    if (userPlaylists.length > 0) {
        html += '<h3 style="margin-top: 30px;">My Playlists</h3>';
        html += '<div class="playlists-grid">';
        userPlaylists.forEach(playlist => {
            html += renderPlaylistCard(playlist);
        });
        html += '</div>';
    }

    return html;
}

// Render individual playlist card
function renderPlaylistCard(playlist) {
    return `
        <div class="playlist-card" onclick="viewPlaylist(${playlist.id})">
            <div class="playlist-card-header">
                <h4>${escapeHtml(playlist.name)}</h4>
                ${playlist.is_system ? '<span class="system-badge">System</span>' : ''}
            </div>
            <div class="playlist-card-meta">
                ${playlist.movie_count} movie${playlist.movie_count !== 1 ? 's' : ''}
            </div>
            ${!playlist.is_system ? `
                <div class="playlist-card-actions">
                    <button class="btn btn-small btn-danger" onclick="event.stopPropagation(); deletePlaylist(${playlist.id}, '${escapeJsString(playlist.name)}')">Delete</button>
                </div>
            ` : ''}
        </div>
    `;
}

// View specific playlist
async function viewPlaylist(playlistId, sort = 'date_added', page = 1) {
    currentPlaylistId = playlistId;
    currentPlaylistSort = sort;
    currentPlaylistPage = page;

    const overview = document.getElementById('playlistsOverview');
    const playlistView = document.getElementById('playlistView');

    overview.style.display = 'none';
    playlistView.style.display = 'block';

    try {
        const response = await fetch(`/api/playlists/${playlistId}?sort=${sort}&page=${page}&per_page=50`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        renderPlaylistView(data);
        
        // Restore scroll position if available
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
    } catch (error) {
        console.error('Error loading playlist:', error);
        document.getElementById('playlistMovies').innerHTML = '<div class="empty-state">Error loading playlist</div>';
    }
}

// Render playlist view
function renderPlaylistView(data) {
    const { playlist, movies, pagination } = data;

    // Update header
    document.getElementById('playlistTitle').textContent = playlist.name;
    document.getElementById('playlistSortSelect').value = currentPlaylistSort;

    // Render movies
    const moviesContainer = document.getElementById('playlistMovies');
    if (!movies || movies.length === 0) {
        moviesContainer.innerHTML = `
            <div class="empty-state">
                <h3>This playlist is empty</h3>
                <p>Add movies to "${escapeHtml(playlist.name)}" to get started!</p>
                <button class="btn" onclick="backToPlaylistsOverview()">Browse All Movies</button>
            </div>
        `;
    } else {
        moviesContainer.innerHTML = movies.map(movie => createMovieCard(movie)).join('');
    }

    // Render pagination
    const paginationContainer = document.getElementById('playlistPagination');
    if (pagination.pages > 1) {
        paginationContainer.innerHTML = createPagination(pagination, (page) => {
            viewPlaylist(currentPlaylistId, currentPlaylistSort, page);
        });
        paginationContainer.style.display = 'flex';
    } else {
        paginationContainer.style.display = 'none';
    }
    
    // Restore scroll position
    if (typeof restoreScrollPosition === 'function') {
        restoreScrollPosition();
    }
}

// Back to playlists overview
function backToPlaylistsOverview() {
    currentPlaylistId = null;
    loadPlaylistsOverview();
}

// Change playlist sort
function changePlaylistSort(sort) {
    if (currentPlaylistId) {
        viewPlaylist(currentPlaylistId, sort, 1);
    }
}

// Create pagination HTML for playlists
function createPagination(pagination, onPageChange) {
    if (pagination.pages <= 1) {
        return '';
    }

    // Store the callback in a global function for onclick handlers
    window._playlistPaginationCallback = onPageChange;

    let html = '';
    const maxPages = 5;

    // Previous button
    const prevPage = pagination.page - 1;
    html += `<button class="pagination-btn" ${pagination.page === 1 ? 'disabled' : ''} onclick="window._playlistPaginationCallback(${prevPage})">Previous</button>`;

    // Page numbers
    let startPage = Math.max(1, pagination.page - Math.floor(maxPages / 2));
    let endPage = Math.min(pagination.pages, startPage + maxPages - 1);

    if (startPage > 1) {
        html += `<button class="pagination-btn" onclick="window._playlistPaginationCallback(1)">1</button>`;
        if (startPage > 2) {
            html += `<span class="pagination-info">...</span>`;
        }
    }

    for (let i = startPage; i <= endPage; i++) {
        const isActive = i === pagination.page;
        html += `<button class="pagination-btn ${isActive ? 'active' : ''}" onclick="window._playlistPaginationCallback(${i})">${i}</button>`;
    }

    if (endPage < pagination.pages) {
        if (endPage < pagination.pages - 1) {
            html += `<span class="pagination-info">...</span>`;
        }
        html += `<button class="pagination-btn" onclick="window._playlistPaginationCallback(${pagination.pages})">${pagination.pages}</button>`;
    }

    // Next button
    const nextPage = pagination.page + 1;
    html += `<button class="pagination-btn" ${pagination.page === pagination.pages ? 'disabled' : ''} onclick="window._playlistPaginationCallback(${nextPage})">Next</button>`;

    // Page info
    html += `<span class="pagination-info">Page ${pagination.page} of ${pagination.pages} (${pagination.total} total)</span>`;

    return html;
}

// Show create playlist dialog
function showCreatePlaylistDialog() {
    const dialog = document.createElement('div');
    dialog.className = 'dialog-overlay';
    dialog.innerHTML = `
        <div class="dialog">
            <h3>Create New Playlist</h3>
            <div style="margin: 20px 0;">
                <input type="text" id="newPlaylistName" placeholder="Playlist name" style="width: 100%; padding: 10px; border: 1px solid #555; border-radius: 4px; background: #2a2a2a; color: #e0e0e0;" maxlength="50">
            </div>
            <div class="dialog-buttons">
                <button class="btn btn-secondary" onclick="this.closest('.dialog-overlay').remove()">Cancel</button>
                <button class="btn" onclick="createPlaylist()">Create</button>
            </div>
        </div>
    `;
    document.body.appendChild(dialog);

    // Focus input
    setTimeout(() => {
        document.getElementById('newPlaylistName').focus();
    }, 100);
}

// Create playlist
async function createPlaylist() {
    const nameInput = document.getElementById('newPlaylistName');
    const name = nameInput.value.trim();

    if (!name) {
        showStatus('Please enter a playlist name', 'error');
        return;
    }

    try {
        const response = await fetch('/api/playlists', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        // Close dialog and refresh
        document.querySelector('.dialog-overlay').remove();
        await loadPlaylistsOverview();
        showStatus(`Created playlist "${name}"`, 'success');
    } catch (error) {
        console.error('Error creating playlist:', error);
        showStatus('Failed to create playlist: ' + error.message, 'error');
    }
}

// Delete playlist
async function deletePlaylist(playlistId, playlistName) {
    if (!confirm(`Delete playlist "${playlistName}"? This cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/playlists/${playlistId}`, {
            method: 'DELETE'
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        await loadPlaylistsOverview();
        showStatus(`Deleted playlist "${playlistName}"`, 'success');
    } catch (error) {
        console.error('Error deleting playlist:', error);
        showStatus('Failed to delete playlist', 'error');
    }
}

// Add movie to playlist from movie card menu
async function addMovieToPlaylist(movieId, playlistName) {
    try {
        const response = await fetch(`/api/movies/${movieId}/add-to-playlist?playlist_name=${encodeURIComponent(playlistName)}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        const result = await response.json();
        showStatus(`Added to "${result.playlist_name}"`, 'success');
    } catch (error) {
        console.error('Error adding to playlist:', error);
        showStatus('Failed to add to playlist: ' + error.message, 'error');
    }
}

// Show add to playlist submenu for movie card menu
async function showAddToPlaylistMenu(movieId) {
    // Close any existing submenu first
    closeAddToPlaylistMenu();

    // Try to find menu button for movie cards first
    let menuBtn = document.querySelector(`[data-movie-id="${movieId}"] .movie-card-menu-btn`);

    // If not found, try to find menu button for movie details page
    if (!menuBtn) {
        // Look for active movie card menu dropdown and find its corresponding button
        const activeMenu = document.querySelector('.movie-card-menu-dropdown.active');
        if (activeMenu && activeMenu.id.startsWith('menu-details-')) {
            // For movie details, find the button that triggered this menu
            menuBtn = document.querySelector(`button[onclick*="${activeMenu.id}"]`);
        }
    }

    if (!menuBtn) return;

    try {
        // Get available playlists
        const response = await fetch('/api/playlists');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        const playlists = data.playlists;

        // Create submenu element
        const submenu = document.createElement('div');
        submenu.className = 'movie-card-submenu';
        submenu.id = 'playlist-submenu';
        // Determine the menu ID to close (movie card or movie details)
        const activeMenu = document.querySelector('.movie-card-menu-dropdown.active');
        const menuIdToClose = activeMenu ? activeMenu.id : movieId;

        submenu.innerHTML = playlists.map(playlist => `
            <button class="movie-card-menu-item" onclick="event.stopPropagation(); addMovieToPlaylist(${movieId}, '${playlist.name.replace(/'/g, "\\'")}'); closeAddToPlaylistMenu(); toggleCardMenu(null, '${menuIdToClose}')">
                ${escapeHtml(playlist.name)}
            </button>
        `).join('');

        // Add "Create new playlist" option
        submenu.innerHTML += `
            <hr style="margin: 5px 0; border: none; border-top: 1px solid #555;">
            <button class="movie-card-menu-item" onclick="event.stopPropagation(); showQuickCreatePlaylist(${movieId}); closeAddToPlaylistMenu(); toggleCardMenu(null, '${menuIdToClose}')">
                + Create new playlist
            </button>
        `;

        // Position the submenu next to the menu button
        document.body.appendChild(submenu);
        positionSubmenu(submenu, menuBtn);

        // Show the submenu
        submenu.classList.add('active');

        // Close on outside click
        setTimeout(() => {
            document.addEventListener('click', closeAddToPlaylistMenuOnOutsideClick);
        }, 1);

    } catch (error) {
        console.error('Error loading playlists for menu:', error);
    }
}

// Position submenu relative to the menu button
function positionSubmenu(submenu, menuBtn) {
    const btnRect = menuBtn.getBoundingClientRect();
    const submenuRect = submenu.getBoundingClientRect();

    // Position to the left of the button by default
    let left = btnRect.left - submenuRect.width - 10;
    let top = btnRect.top;

    // If it would go off-screen to the left, position to the right
    if (left < 10) {
        left = btnRect.right + 10;
    }

    // If it would go off-screen to the right, position to the left anyway
    if (left + submenuRect.width > window.innerWidth - 10) {
        left = btnRect.left - submenuRect.width - 10;
    }

    // Ensure it doesn't go off the top or bottom
    if (top + submenuRect.height > window.innerHeight - 10) {
        top = window.innerHeight - submenuRect.height - 10;
    }

    submenu.style.left = left + 'px';
    submenu.style.top = top + 'px';
}

// Close the add to playlist submenu
function closeAddToPlaylistMenu() {
    const submenu = document.getElementById('playlist-submenu');
    if (submenu) {
        submenu.remove();
    }
    document.removeEventListener('click', closeAddToPlaylistMenuOnOutsideClick);
}

// Close submenu when clicking outside
function closeAddToPlaylistMenuOnOutsideClick(event) {
    const submenu = document.getElementById('playlist-submenu');
    if (submenu && !submenu.contains(event.target)) {
        closeAddToPlaylistMenu();
    }
}

// Quick create playlist from movie menu
function showQuickCreatePlaylist(movieId) {
    const playlistName = prompt('Enter playlist name:');
    if (playlistName && playlistName.trim()) {
        // First create the playlist
        fetch('/api/playlists', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name: playlistName.trim() })
        })
        .then(response => response.json())
        .then(data => {
            if (data.id) {
                // Then add the movie to it
                return addMovieToPlaylist(movieId, playlistName.trim());
            } else {
                throw new Error(data.detail || 'Failed to create playlist');
            }
        })
        .then(() => {
            showStatus(`Created playlist "${playlistName.trim()}" and added movie`, 'success');
        })
        .catch(error => {
            console.error('Error creating playlist:', error);
            showStatus('Failed to create playlist', 'error');
        });
    }
}

// Navigation dropdown functionality
let playlistsDropdownLoaded = false;

function togglePlaylistsDropdown() {
    const menu = document.getElementById('playlistsDropdownMenu');
    if (!menu) return;

    const isActive = menu.classList.contains('active');

    if (isActive) {
        closePlaylistsDropdown();
    } else {
        openPlaylistsDropdown();
    }
}

function openPlaylistsDropdown() {
    const menu = document.getElementById('playlistsDropdownMenu');
    if (!menu) return;

    // Load playlists if not already loaded
    if (!playlistsDropdownLoaded) {
        loadPlaylistsDropdown();
    }

    menu.classList.add('active');

    // Close when clicking outside
    document.addEventListener('click', closePlaylistsDropdownOnOutsideClick);
}

function closePlaylistsDropdown() {
    const menu = document.getElementById('playlistsDropdownMenu');
    if (menu) {
        menu.classList.remove('active');
    }
    document.removeEventListener('click', closePlaylistsDropdownOnOutsideClick);
}

function closePlaylistsDropdownOnOutsideClick(event) {
    const dropdown = document.getElementById('playlistsNavDropdown');
    if (dropdown && !dropdown.contains(event.target)) {
        closePlaylistsDropdown();
    }
}

async function loadPlaylistsDropdown() {
    try {
        const response = await fetch('/api/playlists');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        const playlists = data.playlists;

        const linksContainer = document.getElementById('quickPlaylistLinks');
        if (!linksContainer) return;

        if (playlists.length === 0) {
            linksContainer.innerHTML = '<div style="padding: 10px; color: #888; font-style: italic;">No playlists yet</div>';
        } else {
            linksContainer.innerHTML = playlists.map(playlist => `
                <a href="#/playlist/${playlist.id}" class="playlists-dropdown-item" onclick="closePlaylistsDropdown()">
                    ${playlist.is_system ? '⭐' : '📁'} ${escapeHtml(playlist.name)} (${playlist.movie_count})
                </a>
            `).join('');
        }

        playlistsDropdownLoaded = true;
    } catch (error) {
        console.error('Error loading playlists for dropdown:', error);
        const linksContainer = document.getElementById('quickPlaylistLinks');
        if (linksContainer) {
            linksContainer.innerHTML = '<div style="padding: 10px; color: #888;">Error loading playlists</div>';
        }
    }
}


// Handle direct playlist routes
function handlePlaylistRoute(playlistId) {
    // Switch to playlists page first
    const pages = document.querySelectorAll('.page');
    pages.forEach(page => page.classList.remove('active'));
    const pagePlaylists = document.getElementById('pagePlaylists');
    if (pagePlaylists) {
        pagePlaylists.classList.add('active');
    }

    // Load the playlist directly without going through the overview
    viewPlaylist(playlistId, 'date_added', 1);
}

// Initialize playlists functionality when loaded
document.addEventListener('DOMContentLoaded', () => {
    // Pre-load playlists for the dropdown
    loadPlaylistsDropdown();
});
