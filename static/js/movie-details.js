// Movie Navigation

let savedHistoryScroll = null;

function openMovieHash(id, slug) {
    const safeSlug = (slug || '').toString();
    const current = getRoute();
    if (current === '/history') {
        const list = document.getElementById('historyList');
        if (list) savedHistoryScroll = list.scrollTop;
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

    // Show loading state
    const button = document.querySelector(`[data-movie-id="${movieId}"] .movie-card-btn`);
    if (button) {
        button.style.opacity = '0.6';
        button.disabled = true;
    }

    try {
        const response = await fetch('/api/change-status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                movieStatus: nextStatus
            })
        });

        if (response.ok) {
            // Update UI immediately without page reload
            updateMovieStatusUI(movieId, nextStatus);

            // Add brief success highlight
            if (button) {
                button.style.transition = 'background-color 0.3s ease';
                button.style.backgroundColor = '#e8f5e8';
                setTimeout(() => {
                    button.style.backgroundColor = '';
                }, 500);
            }
        } else {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        console.error('Error updating watched status:', error);
        showStatus('Failed to update watch status', 'error');
    } finally {
        // Restore button state
        if (button) {
            button.style.opacity = '';
            button.disabled = false;
        }
    }
}

// Update just the status UI elements without reloading the page
function updateMovieStatusUI(movieId, newStatus) {
    // Handle movie cards in explore/search views
    const movieCard = document.querySelector(`[data-movie-id="${movieId}"]`);
    if (movieCard) {
        // Update movie card button
        const button = movieCard.querySelector('.movie-card-btn');
        const checkbox = movieCard.querySelector('.watched-checkbox');

        if (button && checkbox) {
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
        const selectedSubtitle = currentSubtitles[movieId] || null;
        
        const response = await fetch('/api/launch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                subtitle_path: selectedSubtitle
            })
        });
        
        if (response.ok) {
            showStatus('Movie launched', 'success');
            updateCurrentlyPlaying();
        } else {
            const data = await response.json();
            showStatus('Failed to launch: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (error) {
        showStatus('Error launching movie: ' + error.message, 'error');
    }
}

let currentSubtitles = {};

async function loadSubtitles(movieId) {
    try {
        const response = await fetch(`/api/subtitles?movie_id=${movieId}`);
        if (!response.ok) {
            console.error('Failed to load subtitles:', response.status);
            currentSubtitles[movieId] = [];
            return [];
        }
        const data = await response.json();
        currentSubtitles[movieId] = data.subtitles || [];
        return currentSubtitles[movieId];
    } catch (error) {
        console.error('Error loading subtitles:', error);
        currentSubtitles[movieId] = [];
        return [];
    }
}

function updateSubtitle(movieId, subtitlePath) {
    currentSubtitles[movieId] = subtitlePath || null;
}

async function loadMovieDetailsById(id) {
    const container = document.getElementById('movieDetailsContainer');
    container.innerHTML = '<div class="loading">Loading movie details...</div>';

    try {
        const response = await fetch(`/api/movie/${encodeURIComponent(id)}`);
        const movie = await response.json();

        if (!response.ok) {
            container.innerHTML = `<div class="empty-state">Error: ${movie.detail || 'Failed to load movie'}</div>`;
            return;
        }

        // Prefer API endpoint by screenshot_id for reliability, fallback to static files
        let imageUrl = '';
        if (movie.screenshot_id) {
            // Use API endpoint - most reliable, handles path issues correctly
            imageUrl = `/api/screenshot/${movie.screenshot_id}`;
        } else if (movie.screenshot_path) {
            // Fallback: use static files endpoint (handles URL encoding properly)
            const filename = movie.screenshot_path.split(/[/\\]/).pop();
            imageUrl = filename ? `/screenshots/${encodeURIComponent(filename)}` : '';
        } else if (movie.image_path) {
            const filename = getFilename(movie.image_path);
            if (movie.image_path.includes('screenshots')) {
                imageUrl = filename ? `/screenshots/${encodeURIComponent(filename)}` : '';
            } else {
                // Extract relative path from movies folder
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
        const subtitles = currentSubtitles[movie.id] || [];
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

        container.innerHTML = `
            <div class="movie-details" data-movie-id="${movie.id}">
                <div class="movie-details-header">
                <div class="movie-details-poster">
                    ${imageUrl ? `<img src="${imageUrl}" alt="${escapeHtml(movie.name)}" onload="const img = this; const container = img.parentElement; if (img.naturalWidth && img.naturalHeight) { const ar = img.naturalWidth / img.naturalHeight; container.style.aspectRatio = ar + ' / 1'; container.style.height = 'auto'; }">` : 'No Image'}
                </div>
                <div class="movie-details-info">
                    <h1 class="movie-details-title">${escapeHtml(movie.name)}</h1>
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
                        <div style="position: relative; display: inline-block;">
                            <button class="btn btn-secondary" onclick="event.stopPropagation(); toggleCardMenu(this, 'menu-details-${movie.id}')">...</button>
                            <div class="movie-card-menu-dropdown" id="menu-details-${movie.id}" style="right: auto; left: 0;">
                                <button class="movie-card-menu-item" onclick="event.stopPropagation(); hideMovie(${movie.id})">Don't show this anymore</button>
                            </div>
                        </div>
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
    } catch (error) {
        container.innerHTML = `<div class="empty-state">Error loading movie: ${error.message}</div>`;
    }
}
