// Movie Lists Management (AI-generated saved searches)

let currentMovieListSlug = null;
let movieListsFilterText = '';
let showFavoritesOnly = false;

// Initialize movie lists page
async function loadMovieListsPage() {
    const pages = document.querySelectorAll('.page');
    pages.forEach(page => page.classList.remove('active'));
    const pageMovieLists = document.getElementById('pageMovieLists');
    if (pageMovieLists) {
        pageMovieLists.classList.add('active');
    }
    await loadMovieListsOverview();
}

// Load movie lists overview
async function loadMovieListsOverview() {
    const overview = document.getElementById('movieListsOverview');
    const detailView = document.getElementById('movieListDetailView');
    
    if (!overview) return;
    
    overview.style.display = 'block';
    if (detailView) detailView.style.display = 'none';
    
    currentMovieListSlug = null;
    
    try {
        const params = new URLSearchParams();
        if (showFavoritesOnly) params.append('favorites_only', 'true');
        if (movieListsFilterText) params.append('search', movieListsFilterText);
        
        const response = await fetch(`/api/movie-lists?${params}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        renderMovieListsOverview(data.lists, data.total);
        
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
    } catch (error) {
        console.error('Error loading movie lists:', error);
        overview.innerHTML = '<div class="empty-state">Error loading movie lists</div>';
    }
}

// Render movie lists overview
function renderMovieListsOverview(lists, total) {
    const overview = document.getElementById('movieListsOverview');
    if (!overview) return;
    
    let html = `
        <div class="movie-lists-header">
            <div class="movie-lists-filter">
                <input type="text" 
                       id="movieListsFilterInput" 
                       placeholder="Filter lists..." 
                       value="${escapeHtml(movieListsFilterText)}"
                       oninput="filterMovieLists(this.value)">
            </div>
            <div class="movie-lists-actions">
                <button class="btn ${showFavoritesOnly ? 'active' : ''}" onclick="toggleFavoritesFilter()">
                    ★ Favorites Only
                </button>
            </div>
        </div>
    `;
    
    if (!lists || lists.length === 0) {
        html += `
            <div class="empty-state">
                <h3>No movie lists yet</h3>
                <p>Use AI Search to ask questions about movies. Your search results will be automatically saved here!</p>
            </div>
        `;
    } else {
        html += `<div class="movie-lists-grid">`;
        lists.forEach(list => {
            html += createMovieListCard(list);
        });
        html += `</div>`;
        
        if (total > lists.length) {
            html += `<div class="movie-lists-more">Showing ${lists.length} of ${total} lists</div>`;
        }
    }
    
    overview.innerHTML = html;
}

// Create movie list card
function createMovieListCard(list) {
    const created = list.created ? formatRelativeTime(new Date(list.created)) : '';
    const providerIcon = list.provider === 'anthropic' ? '🤖' : '🧠';
    const favoriteClass = list.is_favorite ? 'favorite' : '';
    const favoriteIcon = list.is_favorite ? '★' : '☆';
    
    return `
        <div class="movie-list-card ${favoriteClass}" onclick="viewMovieList('${escapeHtml(list.slug)}')">
            <div class="movie-list-card-header">
                <div class="movie-list-card-title">${escapeHtml(list.title)}</div>
                <button class="movie-list-favorite-btn" 
                        onclick="event.stopPropagation(); toggleMovieListFavorite('${escapeHtml(list.slug)}', ${!list.is_favorite})"
                        title="${list.is_favorite ? 'Remove from favorites' : 'Add to favorites'}">
                    ${favoriteIcon}
                </button>
            </div>
            <div class="movie-list-card-query">"${escapeHtml(truncateText(list.query, 80))}"</div>
            <div class="movie-list-card-meta">
                <span>${list.movies_count} movies</span>
                <span>•</span>
                <span>${list.in_library_count} in library</span>
                <span>•</span>
                <span>${providerIcon}</span>
                <span>•</span>
                <span>${created}</span>
            </div>
            <div class="movie-list-card-actions">
                <button class="btn btn-small btn-danger" 
                        onclick="event.stopPropagation(); deleteMovieList('${escapeHtml(list.slug)}', '${escapeHtml(list.title)}')">
                    Delete
                </button>
            </div>
        </div>
    `;
}

// Filter movie lists
let filterDebounceTimer = null;
function filterMovieLists(text) {
    clearTimeout(filterDebounceTimer);
    filterDebounceTimer = setTimeout(() => {
        movieListsFilterText = text;
        loadMovieListsOverview();
    }, 300);
}

// Toggle favorites filter
function toggleFavoritesFilter() {
    showFavoritesOnly = !showFavoritesOnly;
    loadMovieListsOverview();
}

// View specific movie list
async function viewMovieList(slug) {
    currentMovieListSlug = slug;
    
    // Update URL without reloading
    if (window.history.pushState) {
        window.history.pushState(null, '', `#/lists/${slug}`);
    }
    
    const overview = document.getElementById('movieListsOverview');
    const detailView = document.getElementById('movieListDetailView');
    
    if (overview) overview.style.display = 'none';
    if (!detailView) return;
    
    detailView.style.display = 'block';
    detailView.innerHTML = '<div class="loading">Loading movie list...</div>';
    
    try {
        const response = await fetch(`/api/movie-lists/${slug}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        renderMovieListDetail(data);
        
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
    } catch (error) {
        console.error('Error loading movie list:', error);
        detailView.innerHTML = '<div class="empty-state">Error loading movie list</div>';
    }
}

// Render movie list detail view
function renderMovieListDetail(data) {
    const detailView = document.getElementById('movieListDetailView');
    if (!detailView) return;
    
    const created = data.created ? new Date(data.created).toLocaleDateString() : '';
    const providerLabel = data.provider === 'anthropic' ? 'Claude' : 'GPT';
    const costDisplay = data.cost_usd ? `$${data.cost_usd.toFixed(4)}` : '';
    const favoriteIcon = data.is_favorite ? '★' : '☆';
    const favoriteText = data.is_favorite ? 'Favorited' : 'Add to Favorites';
    
    let html = `
        <div class="movie-list-detail-header">
            <button class="btn btn-secondary" onclick="backToMovieListsOverview()">← Back to Lists</button>
            <div class="movie-list-detail-actions">
                <button class="btn ${data.is_favorite ? 'active' : ''}" 
                        onclick="toggleMovieListFavorite('${escapeHtml(data.slug)}', ${!data.is_favorite})">
                    ${favoriteIcon} ${favoriteText}
                </button>
                <button class="btn btn-danger" 
                        onclick="deleteMovieList('${escapeHtml(data.slug)}', '${escapeHtml(data.title)}')">
                    🗑️ Delete
                </button>
            </div>
        </div>
        
        <div class="movie-list-detail-info">
            <h2 class="movie-list-detail-title" 
                contenteditable="true" 
                onblur="updateMovieListTitle('${escapeHtml(data.slug)}', this.textContent)"
                title="Click to edit title">${escapeHtml(data.title)}</h2>
            <div class="movie-list-detail-query">Query: "${escapeHtml(data.query)}"</div>
            <div class="movie-list-detail-meta">
                ${providerLabel} • ${costDisplay} • ${created}
            </div>
        </div>
    `;
    
    // AI Comment
    if (data.comment) {
        html += `<div class="movie-list-detail-comment">${escapeHtml(data.comment)}</div>`;
    }
    
    // Found movies section
    if (data.found_movies && data.found_movies.length > 0) {
        html += `
            <div class="section-title">In Your Library (${data.found_movies.length})</div>
            <div class="movie-grid ai-results-grid">
        `;
        data.found_movies.forEach(movie => {
            const commentHtml = movie.ai_comment 
                ? `<div class="ai-movie-comment">${escapeHtml(movie.ai_comment)}</div>`
                : `<div class="ai-movie-comment muted">No specific comment.</div>`;
            html += `<div class="ai-card-suggestion">${createMovieCard(movie)}${commentHtml}</div>`;
        });
        html += `</div>`;
    } else {
        html += `<div class="empty-state">No matching movies found in your library.</div>`;
    }
    
    // Missing movies section
    if (data.missing_movies && data.missing_movies.length > 0) {
        // Store missing movies for copy button
        window._currentMissingMovies = data.missing_movies;
        html += `
            <div class="section-title-row" style="margin-top: 30px;">
                <div class="section-title">Not in Your Library (${data.missing_movies.length})</div>
                <button class="btn btn-small btn-copy-names" onclick="copyMissingMovieNames()" title="Copy all titles to clipboard">📋 Copy Names</button>
            </div>
            <div class="ai-missing-list">
        `;
        data.missing_movies.forEach(movie => {
            const title = escapeHtml(movie.name || 'Unknown title');
            const year = movie.year ? ` <span class="ai-missing-year">(${movie.year})</span>` : '';
            const comment = movie.ai_comment
                ? `<div class="ai-missing-comment">${escapeHtml(movie.ai_comment)}</div>`
                : '';
            html += `
                <div class="ai-missing-item">
                    <div class="ai-missing-card">
                        <div class="ai-missing-title"><strong>${title}</strong>${year}</div>
                    </div>
                    ${comment}
                </div>
            `;
        });
        html += `</div>`;
    } else if (data.found_movies && data.found_movies.length > 0) {
        html += `<div class="ai-missing-placeholder">All suggested movies are already in your library.</div>`;
    }
    
    detailView.innerHTML = html;
    
    // Initialize star ratings if available
    if (typeof initAllStarRatings === 'function') {
        initAllStarRatings();
    }
}

// Back to movie lists overview
function backToMovieListsOverview() {
    currentMovieListSlug = null;
    
    // Update URL
    if (window.history.pushState) {
        window.history.pushState(null, '', '#/lists');
    }
    
    loadMovieListsOverview();
}

// Toggle movie list favorite status
async function toggleMovieListFavorite(slug, isFavorite) {
    try {
        const response = await fetch(`/api/movie-lists/${slug}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_favorite: isFavorite })
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        // Refresh current view
        if (currentMovieListSlug === slug) {
            viewMovieList(slug);
        } else {
            loadMovieListsOverview();
        }
        
        showStatus(isFavorite ? 'Added to favorites' : 'Removed from favorites', 'success');
    } catch (error) {
        console.error('Error updating favorite status:', error);
        showStatus('Failed to update favorite status', 'error');
    }
}

// Update movie list title
async function updateMovieListTitle(slug, newTitle) {
    newTitle = newTitle.trim();
    if (!newTitle) {
        viewMovieList(slug); // Reload to reset
        return;
    }
    
    try {
        const response = await fetch(`/api/movie-lists/${slug}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: newTitle })
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        showStatus('Title updated', 'success');
    } catch (error) {
        console.error('Error updating title:', error);
        showStatus('Failed to update title', 'error');
        viewMovieList(slug); // Reload to reset
    }
}

// Delete movie list
async function deleteMovieList(slug, title) {
    if (!confirm(`Delete movie list "${title}"? This cannot be undone.`)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/movie-lists/${slug}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        showStatus(`Deleted "${title}"`, 'success');
        
        // Navigate back to overview
        if (currentMovieListSlug === slug) {
            backToMovieListsOverview();
        } else {
            loadMovieListsOverview();
        }
    } catch (error) {
        console.error('Error deleting movie list:', error);
        showStatus('Failed to delete movie list', 'error');
    }
}

// Format relative time (e.g., "2 hours ago")
function formatRelativeTime(date) {
    const now = new Date();
    const diffMs = now - date;
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);
    
    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return date.toLocaleDateString();
}

// Truncate text with ellipsis
function truncateText(text, maxLength) {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength - 3) + '...';
}

// Handle direct movie list routes
function handleMovieListRoute(slug) {
    const pages = document.querySelectorAll('.page');
    pages.forEach(page => page.classList.remove('active'));
    const pageMovieLists = document.getElementById('pageMovieLists');
    if (pageMovieLists) {
        pageMovieLists.classList.add('active');
    }
    
    viewMovieList(slug);
}

// Load movie list suggestions for the search area
async function loadMovieListSuggestions(queryText = '') {
    const suggestionsContainer = document.getElementById('movieListSuggestions');
    if (!suggestionsContainer) return;
    
    try {
        const response = await fetch(`/api/movie-lists/suggestions?q=${encodeURIComponent(queryText)}`);
        if (!response.ok) return;
        
        const data = await response.json();
        renderMovieListSuggestions(data.suggestions, data.recent_lists);
    } catch (error) {
        console.error('Error loading suggestions:', error);
    }
}

// Render movie list suggestions
function renderMovieListSuggestions(suggestions, recentLists) {
    const container = document.getElementById('movieListSuggestions');
    if (!container) return;
    
    let html = '';
    
    // Similar searches
    if (suggestions && suggestions.length > 0) {
        html += `<div class="suggestions-section">
            <div class="suggestions-label">💡 Similar searches:</div>
            <div class="suggestions-items">`;
        suggestions.forEach(s => {
            html += `
                <a href="#/lists/${escapeHtml(s.slug)}" class="suggestion-item">
                    "${escapeHtml(truncateText(s.title, 40))}" (${s.movies_count} movies)
                </a>
            `;
        });
        html += `</div></div>`;
    }
    
    // Recent lists
    if (recentLists && recentLists.length > 0) {
        html += `<div class="suggestions-section">
            <div class="suggestions-label">📋 Recent lists:</div>
            <div class="suggestions-items">`;
        recentLists.forEach(s => {
            html += `
                <a href="#/lists/${escapeHtml(s.slug)}" class="suggestion-item">
                    ${escapeHtml(truncateText(s.title, 40))} (${s.movies_count})
                </a>
            `;
        });
        html += `</div></div>`;
    }
    
    container.innerHTML = html;
    container.style.display = html ? 'block' : 'none';
}

// Initialize - load suggestions on page load
document.addEventListener('DOMContentLoaded', () => {
    // Load initial suggestions (recent lists)
    loadMovieListSuggestions('');
});

