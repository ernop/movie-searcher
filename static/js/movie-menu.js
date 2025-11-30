/**
 * Movie Action Menu - Central definition of all movie context menu actions.
 * 
 * This module provides a unified way to define, render, and manage the "..." menu
 * (Movie Action Menu) that appears on movie cards and movie detail pages.
 * 
 * ARCHITECTURE:
 * - Menu state is computed server-side and included in movie data
 * - Frontend renders menu based on pre-computed state (no additional AJAX)
 * - Movie cards and detail pages use the same menu definition
 * - Detail page is a superset of card (may show additional actions)
 * 
 * MENU STATE (from server):
 * movie.menu_state = {
 *     copy_to_local: null | 'not_copied' | 'already_copied'
 *     // null means feature not configured (don't show action)
 * }
 */

/**
 * Movie Action Menu Actions Definition
 * 
 * Each action has:
 * - id: Unique identifier for the action
 * - label: Display text (string or function(movie) => string)
 * - action: Handler function (movieId, movie) => void
 * - contexts: Array of contexts where action appears ['card', 'details']
 * - enabled: Optional function(movie) => boolean, defaults to true
 * - className: Optional CSS class for styling
 */
const MOVIE_MENU_ACTIONS = [
    {
        id: 'open-folder',
        label: 'Open Folder',
        action: (movieId, movie) => openFolder(movie.path),
        contexts: ['card', 'details']
    },
    {
        id: 'add-to-playlist',
        label: 'Add to playlist',
        action: (movieId, movie) => showAddToPlaylistMenu(movieId),
        contexts: ['card', 'details']
    },
    {
        id: 'copy-to-local',
        label: (movie) => {
            const status = movie.menu_state?.copy_to_local;
            if (status === 'already_copied') return 'Already Copied';
            return 'Copy to Local';
        },
        action: (movieId, movie) => {
            const status = movie.menu_state?.copy_to_local;
            if (status === 'already_copied') {
                showStatus('Movie already copied to local folder', 'info');
                return;
            }
            // Use the copy function from setup.js
            if (typeof copyMovieToLocal === 'function') {
                copyMovieToLocal(movieId, movie.name);
            } else {
                showStatus('Copy function not available', 'error');
            }
        },
        contexts: ['card', 'details'],
        // Only show if copy_to_local feature is configured (menu_state.copy_to_local is not null)
        enabled: (movie) => movie.menu_state?.copy_to_local !== null && movie.menu_state?.copy_to_local !== undefined,
        className: (movie) => movie.menu_state?.copy_to_local === 'already_copied' ? 'menu-item-success' : ''
    },
    {
        id: 'hide-movie',
        label: "Don't show this anymore",
        action: (movieId, movie) => hideMovie(movieId),
        contexts: ['card', 'details']
    }
];

/**
 * Get available actions for a given context and movie
 * @param {object} movie - Movie data object with menu_state
 * @param {string} context - 'card' or 'details'
 * @returns {Array} Filtered list of actions
 */
function getAvailableMenuActions(movie, context) {
    return MOVIE_MENU_ACTIONS.filter(action => {
        // Check context
        if (!action.contexts.includes(context)) {
            return false;
        }
        // Check enabled state
        if (action.enabled && !action.enabled(movie)) {
            return false;
        }
        return true;
    });
}

/**
 * Get the label for an action (handles dynamic labels)
 * @param {object} action - Action definition
 * @param {object} movie - Movie data
 * @returns {string} Label text
 */
function getActionLabel(action, movie) {
    if (typeof action.label === 'function') {
        return action.label(movie);
    }
    return action.label;
}

/**
 * Get the CSS class for an action (handles dynamic classes)
 * @param {object} action - Action definition
 * @param {object} movie - Movie data
 * @returns {string} CSS class or empty string
 */
function getActionClassName(action, movie) {
    if (typeof action.className === 'function') {
        return action.className(movie);
    }
    return action.className || '';
}

/**
 * Render the Movie Action Menu HTML for a given context
 * @param {object} movie - Movie data object (must include id, path, name, menu_state)
 * @param {string} context - 'card' or 'details'
 * @param {string} menuId - Optional custom menu ID (defaults to 'menu-{movieId}')
 * @returns {string} HTML string for the menu
 */
function renderMovieActionMenu(movie, context, menuId = null) {
    const actions = getAvailableMenuActions(movie, context);
    const id = menuId || `menu-${movie.id}`;
    
    // Menu button style differs between card and details
    const buttonClass = context === 'card' ? 'movie-card-menu-btn' : 'btn btn-secondary';
    const buttonText = context === 'card' ? '⋮' : '...';
    
    let menuItemsHtml = '';
    for (const action of actions) {
        const label = getActionLabel(action, movie);
        const className = getActionClassName(action, movie);
        const escapedPath = escapeJsString(movie.path || '').replace(/"/g, '&quot;');
        const escapedName = escapeJsString(movie.name || '').replace(/"/g, '&quot;');
        
        // Build the onclick handler
        // We pass movie data as a JSON string to preserve it
        const movieDataJson = JSON.stringify({
            id: movie.id,
            path: movie.path,
            name: movie.name,
            menu_state: movie.menu_state
        }).replace(/"/g, '&quot;');
        
        menuItemsHtml += `
            <button class="movie-card-menu-item ${className}" 
                    data-action="${action.id}"
                    onclick="event.stopPropagation(); handleMovieMenuAction('${action.id}', ${movie.id}, ${movieDataJson})">
                ${escapeHtml(label)}
            </button>
        `;
    }
    
    // For details context, position differently
    const dropdownStyle = context === 'details' ? 'style="right: auto; left: 0;"' : '';
    
    return `
        <div style="position: relative;${context === 'card' ? ' z-index: 2;' : ' display: inline-block;'}">
            <button class="${buttonClass}" onclick="event.stopPropagation(); toggleCardMenu(this, '${id}')">${buttonText}</button>
            <div class="movie-card-menu-dropdown" id="${id}" ${dropdownStyle}>
                ${menuItemsHtml}
            </div>
        </div>
    `;
}

/**
 * Handle a menu action click
 * @param {string} actionId - The action ID
 * @param {number} movieId - The movie ID
 * @param {object} movieData - Movie data object
 */
function handleMovieMenuAction(actionId, movieId, movieData) {
    // Close all menus first
    document.querySelectorAll('.movie-card-menu-dropdown.active').forEach(el => {
        el.classList.remove('active');
    });
    
    // Find and execute the action
    const action = MOVIE_MENU_ACTIONS.find(a => a.id === actionId);
    if (action) {
        action.action(movieId, movieData);
    } else {
        console.error(`Unknown menu action: ${actionId}`);
    }
}

/**
 * Render menu for movie card context
 * Convenience wrapper for renderMovieActionMenu
 */
function renderCardMenu(movie) {
    return renderMovieActionMenu(movie, 'card');
}

/**
 * Render menu for movie details context
 * Convenience wrapper for renderMovieActionMenu
 */
function renderDetailsMenu(movie, menuId = null) {
    return renderMovieActionMenu(movie, 'details', menuId);
}

