// Movie Navigation

// Parse resolution from filename (e.g., 1080p, 720p, 4K, 2160p)
function parseResolution(path) {
    if (!path) return null;
    const filename = path.split(/[/\\]/).pop() || '';
    const lower = filename.toLowerCase();
    
    // Check for common resolution patterns
    if (/2160p|4k|uhd/i.test(lower)) return '4K';
    if (/1080p|1080i|fullhd|full.?hd/i.test(lower)) return '1080p';
    if (/720p|hd/i.test(lower)) return '720p';
    if (/480p|sd/i.test(lower)) return '480p';
    if (/576p|pal/i.test(lower)) return '576p';
    if (/360p/i.test(lower)) return '360p';
    
    return null;
}

function openMovieHash(id, slug) {
    const safeSlug = (slug || '').toString();
    // Save scroll position of current route before navigating
    if (typeof saveScrollPosition === 'function') {
        saveScrollPosition();
    }
    navigateTo(`/movie/${id}/${safeSlug}`);
}

async function goToRandomMovie() {
    try {
        const response = await fetch('/api/random-movie');
        if (!response.ok) {
            let errorMessage = 'Unknown error';
            try {
                const errorData = await response.json();
                errorMessage = errorData.detail || errorMessage;
            } catch (e) {
                errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            }
            showStatus('Failed to load random movie: ' + errorMessage, 'error');
            return;
        }
        
        const data = await response.json();
        const movieId = data.id;
        
        if (!movieId) {
            showStatus('Invalid response from server', 'error');
            return;
        }
        
        navigateTo(`/movie/${movieId}`);
    } catch (error) {
        showStatus('Error fetching random movie: ' + error.message, 'error');
    }
}

async function showRandomMovies() {
    try {
        const results = document.getElementById('results');
        if (!results) {
            showStatus('Results container not found', 'error');
            return;
        }
        
        results.innerHTML = '<div class="loading">Loading random movies...</div>';
        
        const response = await fetch('/api/random-movies?count=10');
        if (!response.ok) {
            let errorMessage = 'Unknown error';
            try {
                const errorData = await response.json();
                errorMessage = errorData.detail || errorMessage;
            } catch (e) {
                errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            }
            results.innerHTML = '<div class="empty-state">Failed to load random movies: ' + errorMessage + '</div>';
            showStatus('Failed to load random movies: ' + errorMessage, 'error');
            return;
        }
        
        const data = await response.json();
        const movies = data.results || [];
        
        if (movies.length === 0) {
            results.innerHTML = '<div class="empty-state">No movies found</div>';
            return;
        }
        
        displayResults(movies);
        showStatus(`Showing ${movies.length} random movies`, 'success');
    } catch (error) {
        const results = document.getElementById('results');
        if (results) {
            results.innerHTML = '<div class="empty-state">Error loading random movies: ' + error.message + '</div>';
        }
        showStatus('Error fetching random movies: ' + error.message, 'error');
    }
}

// Movie Actions

async function toggleWatched(movieId, currentStatus) {
    const cycleOrder = [null, 'want_to_watch', 'unwatched', 'watched'];
    const currentIdx = cycleOrder.indexOf(currentStatus === 'null' ? null : currentStatus);
    const nextIdx = (currentIdx + 1) % cycleOrder.length;
    const nextStatus = cycleOrder[nextIdx];

    // Optimistic update: Update UI immediately
    updateMovieStatusUI(movieId, nextStatus);

    try {
        const response = await fetch('/api/change-status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                movieStatus: nextStatus
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        // Success - UI already updated. 
        // Optionally add a subtle success indicator if needed, but usually the state change is enough.
    } catch (error) {
        console.error('Error updating watched status:', error);
        showStatus('Failed to update watch status', 'error');
        
        // Revert UI on error
        updateMovieStatusUI(movieId, currentStatus === 'null' ? null : currentStatus);
    }
}

// Update just the status UI elements without reloading the page
function updateMovieStatusUI(movieId, newStatus) {
    const nextClickStatus = newStatus === null ? 'null' : `'${newStatus}'`;

    // Handle movie cards in explore/search views
    const movieCard = document.querySelector(`[data-movie-id="${movieId}"]`);
    if (movieCard) {
        // Update movie card button
        const button = movieCard.querySelector('.movie-card-btn');
        const checkbox = movieCard.querySelector('.watched-checkbox');

        if (button) {
            // Update onclick handler immediately for next interaction
            button.setAttribute('onclick', `event.stopPropagation(); toggleWatched(${movieId}, ${nextClickStatus})`);

            if (checkbox) {
                // Update checkbox class
                let checkboxClass = 'unset';
                if (newStatus === 'watched') {
                    checkboxClass = 'watched';
                } else if (newStatus === 'unwatched') {
                    checkboxClass = 'unwatched';
                } else if (newStatus === 'want_to_watch') {
                    checkboxClass = 'want-to-watch';
                }

                checkbox.className = `watched-checkbox ${checkboxClass}`;

                // Update button text
                let buttonText = '-';
                if (newStatus === 'watched') {
                    buttonText = 'watched';
                } else if (newStatus === 'unwatched') {
                    buttonText = 'not watched';
                } else if (newStatus === 'want_to_watch') {
                    buttonText = 'want to see';
                }

                // Update the text content (preserve the checkbox span)
                const textSpan = button.querySelector('span.watched-checkbox + *') || button.lastChild;
                if (textSpan && textSpan.nodeType === Node.TEXT_NODE) {
                    textSpan.textContent = buttonText;
                } else {
                    // Fallback: recreate the button content
                    button.innerHTML = `<span class="watched-checkbox ${checkboxClass}"></span>${buttonText}`;
                }
            }
        }

        // Update movie card class (add/remove 'watched' class)
        if (newStatus === 'watched') {
            movieCard.classList.add('watched');
        } else {
            movieCard.classList.remove('watched');
        }
    }

    // Handle movie detail view
    const detailView = document.querySelector('.movie-details');
    if (detailView && detailView.dataset.movieId == movieId) {
        // Update detail view button
        const button = detailView.querySelector('.watched-btn');

        if (button) {
            // Update onclick handler
            button.setAttribute('onclick', `toggleWatched(${movieId}, ${nextClickStatus})`);

            // Update button class
            let buttonClass = '';
            if (newStatus === 'watched') {
                buttonClass = 'watched';
            } else if (newStatus === 'unwatched') {
                buttonClass = 'unwatched';
            } else if (newStatus === 'want_to_watch') {
                buttonClass = 'want-to-watch';
            }

            button.className = `watched-btn ${buttonClass}`;

            // Update button text
            let buttonText = '-';
            if (newStatus === 'watched') {
                buttonText = 'watched';
            } else if (newStatus === 'unwatched') {
                buttonText = 'not watched';
            } else if (newStatus === 'want_to_watch') {
                buttonText = 'want to see';
            }

            button.textContent = buttonText;
        }

        // Update the meta information (watched date)
        const metaDiv = detailView.querySelector('.movie-details-meta');
        if (metaDiv) {
            // Remove existing watched date spans
            const spans = metaDiv.querySelectorAll('span');
            spans.forEach(span => {
                if (span.textContent && span.textContent.startsWith('Watched:')) {
                    span.remove();
                }
            });

            // Add new watched date if applicable
            if (newStatus === 'watched') {
                const watchedSpan = document.createElement('span');
                watchedSpan.textContent = `Watched: ${formatDate(new Date().toISOString())}`;
                metaDiv.appendChild(watchedSpan);
            }
        }
    }

    // Update playlists display if present
    const playlistsContainer = document.querySelector('.movie-playlists');
    if (playlistsContainer) {
        // If "Want to Watch" status changed, we might need to update playlists
        // But for now, we'll handle this in the playlist functions
    }
}

async function launchMovie(movieId) {
    try {
        const selectedSubtitle = selectedSubtitles[movieId] || null;
        const closeExistingVlc = document.getElementById('setupCloseExistingVlc').checked;

        // Validate subtitle path if one is selected
        if (selectedSubtitle && typeof selectedSubtitle !== 'string') {
            showStatus('Invalid subtitle selection', 'error');
            return;
        }

        const response = await fetch('/api/launch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                subtitle_path: selectedSubtitle,
                close_existing_vlc: closeExistingVlc
            })
        });

        const data = await response.json();
        
        // Check for failed status even on 200 response (VLC can start but exit immediately)
        if (response.ok && data.status === 'launched') {
            showStatus('Movie launched', 'success');
            updateCurrentlyPlaying();
        } else {
            let errorMessage = 'Unknown error';
            
            if (data.detail) {
                errorMessage = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
            } else if (data.error) {
                errorMessage = data.error;
            } else if (data.message) {
                errorMessage = data.message;
            }
            
            // Include VLC stderr if available (helps diagnose VLC-specific failures)
            if (data.vlc_stderr) {
                errorMessage += '\n\nVLC error: ' + data.vlc_stderr.substring(0, 200);
                console.error('VLC stderr:', data.vlc_stderr);
            }
            
            // Log full response for debugging
            console.error('Launch failed with response:', response.status, data);
            showStatus('Failed to launch: ' + errorMessage, 'error');
        }
    } catch (error) {
        console.error('Launch error:', error);
        showStatus('Error launching movie: ' + error.message, 'error');
    }
}

let availableSubtitles = {};
let selectedSubtitles = {};

// Note: Copy to Local and menu functionality is now handled by movie-menu.js
// The handleCopyToLocal function is called via handleMovieMenuAction in movie-menu.js

function closeAllMenus() {
    document.querySelectorAll('.movie-card-menu-dropdown').forEach(menu => {
        menu.classList.remove('show');
        menu.classList.remove('active');
    });
}

async function loadSubtitles(movieId) {
    try {
        const response = await fetch(`/api/subtitles?movie_id=${movieId}`);
        if (!response.ok) {
            console.error('Failed to load subtitles:', response.status);
            availableSubtitles[movieId] = [];
            return [];
        }
        const data = await response.json();
        availableSubtitles[movieId] = data.subtitles || [];
        return availableSubtitles[movieId];
    } catch (error) {
        console.error('Error loading subtitles:', error);
        availableSubtitles[movieId] = [];
        return [];
    }
}

function updateSubtitle(movieId, subtitlePath) {
    selectedSubtitles[movieId] = subtitlePath || null;
}

async function loadMovieDetailsById(id) {
    const container = document.getElementById('movieDetailsContainer');
    container.innerHTML = '<div class="loading">Loading movie details...</div>';

    try {
        // Fetch movie details and same-title movies in parallel
        const [response, sameTitleResponse] = await Promise.all([
            fetch(`/api/movie/${encodeURIComponent(id)}`),
            fetch(`/api/movie/${encodeURIComponent(id)}/same-title`)
        ]);
        const movie = await response.json();

        if (!response.ok) {
            container.innerHTML = `
                <div class="movie-not-found">
                    <div class="not-found-icon">🎬</div>
                    <h2>Movie Not Found</h2>
                    <p>The movie with ID <strong>${id}</strong> doesn't exist in the database.</p>
                    <p class="not-found-hint">It may have been removed, or the link might be incorrect.</p>
                    <div class="not-found-actions">
                        <a href="#/explore" class="btn">Browse Movies</a>
                        <a href="#/home" class="btn btn-secondary">Go Home</a>
                    </div>
                </div>`;
            return;
        }

        // Parse same-title movies
        let sameTitleMovies = [];
        if (sameTitleResponse.ok) {
            const sameTitleData = await sameTitleResponse.json();
            sameTitleMovies = sameTitleData.movies || [];
        }

        // Use ID-based endpoint - no disk paths exposed in URLs
        const imageUrl = `/api/movie/${movie.id}/image`;
        const externalLinks = generateExternalLinks(movie.name);
        const mediaGallery = renderMediaGallery(movie.images || [], movie.screenshots || [], movie.path, movie.id);

        // Use watch_status if available (can be string enum or boolean for backward compat), fallback to watched boolean
        let watchStatus = movie.watch_status !== undefined ? movie.watch_status : (movie.watched ? 'watched' : null);
        // Normalize: convert boolean to string enum for backward compatibility
        if (watchStatus === true) watchStatus = 'watched';
        if (watchStatus === false) watchStatus = 'unwatched';
        const isWatched = watchStatus === 'watched';

        // Load subtitles
        await loadSubtitles(movie.id);
        const subtitles = availableSubtitles[movie.id] || [];
        let subtitleSelect = '';
        if (subtitles.length > 0) {
            subtitleSelect = `
                <select class="subtitle-select" id="subtitle-${movie.id}" onchange="updateSubtitle(${movie.id}, this.value)">
                    <option value="">No subtitle</option>
                    ${subtitles.map(sub => `<option value="${escapeJsString(sub.path)}">${escapeHtml(sub.name)}</option>`).join('')}
                </select>
            `;
        }
        
        // Build subtitle indicator - show actual filenames
        let subtitleIndicator = '';
        if (subtitles.length > 0) {
            const subtitleNames = subtitles.map(sub => escapeHtml(sub.name)).join(', ');
            const locations = Array.from(new Set(subtitles.map(sub => sub.location).filter(Boolean))).map(loc => loc === 'subs' ? 'subs folder' : 'current folder').join(', ');
            subtitleIndicator = `<div style="font-size: 11px; color: #888; margin-top: 4px;">Subtitles: ${subtitleNames}${locations ? ` (${locations})` : ''}</div>`;
        }

        // Build same-title movies indicator
        let sameTitleIndicator = '';
        if (sameTitleMovies.length > 0) {
            const otherVersionsHtml = sameTitleMovies.map(m => {
                const sizeStr = m.size ? formatSize(m.size) : 'unknown size';
                const resolution = parseResolution(m.path || '');
                const resolutionBadge = resolution ? `<span class="same-title-resolution">${resolution}</span>` : '';
                const hiddenBadge = m.hidden ? '<span class="same-title-hidden-badge">hidden</span>' : '';
                return `<a href="#/movie/${m.id}" class="same-title-item" title="${escapeHtml(m.path)}">
                    <span class="same-title-size">${sizeStr}</span>
                    ${resolutionBadge}
                    ${hiddenBadge}
                </a>`;
            }).join('');
            
            const countLabel = sameTitleMovies.length === 1 ? '1 other version' : `${sameTitleMovies.length} other versions`;
            sameTitleIndicator = `
                <div class="same-title-indicator">
                    <button class="same-title-toggle" onclick="this.parentElement.classList.toggle('expanded')" title="Show other versions of this movie">
                        ${countLabel}
                    </button>
                    <div class="same-title-dropdown">
                        ${otherVersionsHtml}
                    </div>
                </div>
            `;
        }

        container.innerHTML = `
            <div class="movie-details" data-movie-id="${movie.id}">
                <div class="movie-details-header">
                <div style="display: flex; flex-direction: column; gap: 10px; width: 300px; flex-shrink: 0;">
                    <div class="movie-details-poster" style="width: 100%;">
                        ${imageUrl ? `<img src="${imageUrl}" alt="${escapeHtml(movie.name)}" onload="const img = this; const container = img.parentElement; if (img.naturalWidth && img.naturalHeight) { const ar = img.naturalWidth / img.naturalHeight; container.style.aspectRatio = ar + ' / 1'; container.style.height = 'auto'; }">` : 'No Image'}
                    </div>
                </div>
                <div class="movie-details-info">
                    <div class="movie-details-title-row">
                        <h1 class="movie-details-title">${escapeHtml(movie.name)}</h1>
                        ${sameTitleIndicator}
                    </div>
                    <div class="movie-details-meta">
                        ${movie.year ? `<span class="year-link" onclick="navigateToExploreWithYear(${movie.year}, ${movie.id || 'null'});" title="Filter by ${movie.year}">${movie.year}</span>` : ''}
                        ${movie.length ? `<span>${formatTime(movie.length)}</span>` : ''}
                        ${movie.size ? `<span>${formatSize(movie.size)}</span>` : ''}
                        ${movie.watched_date ? `<span>Watched: ${formatDate(movie.watched_date)}</span>` : ''}
                    </div>
                    ${createStarRating(movie.id || 0, movie.rating || null, 'movie-details-rating')}
                    <div class="movie-details-actions">
                        <button class="watched-btn ${watchStatus === 'watched' ? 'watched' : watchStatus === 'unwatched' ? 'unwatched' : watchStatus === 'want_to_watch' ? 'want-to-watch' : ''}" onclick="toggleWatched(${movie.id}, ${watchStatus === null ? 'null' : `'${watchStatus}'`})">
                            ${watchStatus === 'watched' ? 'watched' : watchStatus === 'unwatched' ? 'not watched' : watchStatus === 'want_to_watch' ? 'want to see' : '-'}
                        </button>
                        ${subtitleSelect}
                        <button class="launch-btn" onclick="launchMovie(${movie.id})">Launch</button>
                        ${typeof renderDetailsMenu === 'function' ? renderDetailsMenu(movie, 'menu-details-' + movie.id) : `
                            <div style="position: relative; display: inline-block;">
                                <button class="btn btn-secondary" onclick="event.stopPropagation(); toggleCardMenu(this, 'menu-details-${movie.id}')">...</button>
                                <div class="movie-card-menu-dropdown" id="menu-details-${movie.id}" style="right: auto; left: 0;">
                                    <button class="movie-card-menu-item" onclick="event.stopPropagation(); showAddToPlaylistMenu(${movie.id})">Add to playlist</button>
                                    <button class="movie-card-menu-item" onclick="event.stopPropagation(); hideMovie(${movie.id})">Don't show this anymore</button>
                                </div>
                            </div>
                        `}
                    </div>
                    <div class="external-links" style="margin-top: 20px;">
                        <a href="${externalLinks.letterboxd}" target="_blank" class="external-link">Letterboxd</a>
                        <a href="${externalLinks.google}" target="_blank" class="external-link">Google</a>
                        <a href="${externalLinks.douban}" target="_blank" class="external-link">Douban</a>
                        <a href="#" onclick="event.preventDefault(); openFolder('${escapeJsString(movie.path)}'); return false;" class="external-link">Open Folder</a>
                    </div>
                    <div style="margin-top: 20px; color: #999; font-size: 12px;">
                        <div>Path: ${escapeHtml(movie.path)}</div>
                        ${subtitleIndicator}
                        ${movie.created ? `<div>Created: ${formatDate(movie.created)}</div>` : ''}
                    </div>
                </div>
            </div>
            ${mediaGallery}
            </div>
        `;
        initAllStarRatings();
        // Menu state is pre-computed server-side, no need for additional API calls
    } catch (error) {
        container.innerHTML = `<div class="empty-state">Error loading movie: ${error.message}</div>`;
    }
}

// Close same-title dropdown when clicking outside
document.addEventListener('click', function(event) {
    const indicator = document.querySelector('.same-title-indicator.expanded');
    if (indicator && !indicator.contains(event.target)) {
        indicator.classList.remove('expanded');
    }
});
