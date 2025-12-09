// Movie Navigation

// Load and render playlists that contain a movie
async function loadMoviePlaylists(movieId) {
    try {
        const response = await fetch(`/api/movies/${movieId}/playlists`);
        if (!response.ok) return '';

        const data = await response.json();
        const playlists = data.playlists || [];

        if (playlists.length === 0) return '';

        const playlistLinks = playlists.map(p => {
            const slug = createPlaylistSlug(p.name);
            const icon = p.is_system ? (p.name === 'Favorites' ? '★' : '♡') : '📋';
            return `<a href="#/playlist/${p.id}/${slug}" class="movie-playlist-pill">${icon} ${escapeHtml(p.name)}</a>`;
        }).join('');

        return `
            <div class="movie-playlists-section">
                <span class="movie-playlists-label">In playlists:</span>
                ${playlistLinks}
            </div>
        `;
    } catch (error) {
        console.error('Error loading movie playlists:', error);
        return '';
    }
}

// Load and render AI-generated movie lists that contain a movie
async function loadMovieLists(movieId) {
    try {
        const response = await fetch(`/api/movies/${movieId}/lists`);
        if (!response.ok) return '';

        const data = await response.json();
        const lists = data.lists || [];

        if (lists.length === 0) return '';

        // Use makeMovieListCard from movie-lists.js with focusMovieId for context
        const listCards = lists.map(lst => makeMovieListCard(lst, movieId)).join('');

        return `
            <div class="movie-lists-section">
                <span class="movie-lists-label">In Movie Lists (${lists.length})</span>
                <div class="movie-lists-cards">${listCards}</div>
            </div>
        `;
    } catch (error) {
        console.error('Error loading movie lists:', error);
        return '';
    }
}

// Load and render AI reviews for a movie
async function loadMovieReviews(movieId) {
    try {
        const response = await fetch(`/api/movie/${movieId}/reviews`);
        if (!response.ok) return '';

        const data = await response.json();
        const reviews = data.reviews || [];

        if (reviews.length === 0) return '';

        const reviewsHtml = reviews.map(review => {
            const modelDisplay = review.model_provider === 'openai' 
                ? (review.model_name === 'gpt-5.1' ? 'GPT-5.1' : review.model_name)
                : (review.model_name.includes('opus') ? 'Claude Opus 4.5' : review.model_name);
            const createdDate = review.created ? formatDate(new Date(review.created)) : '';
            const costText = review.cost_usd ? `$${review.cost_usd.toFixed(6)}` : '';
            
            // Render Markdown to HTML
            const formattedText = renderMarkdown(review.response_text);
            
            return `
                <div class="ai-review-card" data-review-id="${review.id}">
                    <div class="ai-review-header">
                        <div class="ai-review-meta">
                            <span class="ai-review-model">${escapeHtml(modelDisplay)}</span>
                            <span class="ai-review-date">${createdDate}</span>
                            ${costText ? `<span class="ai-review-cost">${costText}</span>` : ''}
                            <span class="ai-review-type-badge">${escapeHtml(review.prompt_type)}</span>
                        </div>
                        <button class="btn btn-small btn-danger" onclick="deleteReview(${movieId}, ${review.id})" title="Delete review">Delete</button>
                    </div>
                    <div class="ai-review-content">${formattedText}</div>
                </div>
            `;
        }).join('');

        return `
            <div class="movie-reviews-section">
                <span class="movie-reviews-label">AI Reviews (${reviews.length})</span>
                <div class="movie-reviews-list">${reviewsHtml}</div>
            </div>
        `;
    } catch (error) {
        console.error('Error loading reviews:', error);
        return '';
    }
}

// Generate a new review
async function generateReview(movieId) {
    const providerSelect = document.getElementById(`review-provider-${movieId}`);
    const instructionsTextarea = document.getElementById(`review-instructions-${movieId}`);
    const reviewContainer = document.getElementById(`review-generation-${movieId}`);
    const reviewsContainer = document.getElementById(`movie-reviews-${movieId}`);
    
    if (!providerSelect || !reviewContainer) return;
    
    const provider = providerSelect.value;
    const furtherInstructions = instructionsTextarea ? instructionsTextarea.value.trim() : null;
    
    // Disable button
    const generateBtn = document.getElementById(`generate-review-btn-${movieId}`);
    if (generateBtn) {
        generateBtn.disabled = true;
        generateBtn.textContent = 'Generating...';
    }
    
    // Show progress UI
    reviewContainer.innerHTML = `
        <div class="ai-review-progress-container">
            <div class="ai-review-progress-step" data-step="1">
                <span class="ai-review-progress-indicator">○</span>
                <span class="ai-review-progress-text">Preparing query for AI...</span>
            </div>
            <div class="ai-review-progress-step" data-step="2">
                <span class="ai-review-progress-indicator">○</span>
                <span class="ai-review-progress-text">Waiting for AI response...</span>
            </div>
            <div class="ai-review-progress-step" data-step="3">
                <span class="ai-review-progress-indicator">○</span>
                <span class="ai-review-progress-text">Saving review...</span>
            </div>
        </div>
    `;
    
    function updateProgress(step, message) {
        // Mark previous steps as completed
        for (let i = 1; i < step; i++) {
            const prevStep = reviewContainer.querySelector(`.ai-review-progress-step[data-step="${i}"]`);
            if (prevStep) {
                prevStep.classList.add('completed');
                prevStep.classList.remove('active');
                prevStep.querySelector('.ai-review-progress-indicator').textContent = '✓';
            }
        }
        // Mark current step as active
        const currentStep = reviewContainer.querySelector(`.ai-review-progress-step[data-step="${step}"]`);
        if (currentStep) {
            currentStep.classList.add('active');
            currentStep.classList.remove('completed');
            currentStep.querySelector('.ai-review-progress-indicator').textContent = '●';
            currentStep.querySelector('.ai-review-progress-text').textContent = message;
        }
    }
    
    try {
        const response = await fetch(`/api/movie/${movieId}/review`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                provider: provider,
                further_instructions: furtherInstructions || null
            })
        });
        
        if (!response.ok) {
            let detail = 'Review generation failed';
            try {
                const errorData = await response.json();
                detail = errorData.detail || detail;
            } catch (parseError) {
                // ignore parse errors
            }
            throw new Error(detail);
        }
        
        // Read SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let resultData = null;
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            
            // Parse SSE events from buffer
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep incomplete line in buffer
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const eventData = JSON.parse(line.slice(6));
                        
                        if (eventData.type === 'progress') {
                            updateProgress(eventData.step, eventData.message);
                        } else if (eventData.type === 'result') {
                            resultData = eventData;
                        } else if (eventData.type === 'error') {
                            throw new Error(eventData.detail);
                        }
                    } catch (parseErr) {
                        console.warn('Failed to parse SSE event:', line, parseErr);
                    }
                }
            }
        }
        
        if (resultData) {
            // Clear progress and reload reviews
            reviewContainer.innerHTML = '';
            if (reviewsContainer) {
                const html = await loadMovieReviews(movieId);
                reviewsContainer.innerHTML = html;
            }
            showStatus('Review generated successfully', 'success');
        } else {
            throw new Error('No result received from review generation');
        }
    } catch (error) {
        console.error('Review Generation Error:', error);
        showStatus(`Error: ${error.message}`, 'error');
        reviewContainer.innerHTML = '';
    } finally {
        if (generateBtn) {
            generateBtn.disabled = false;
            generateBtn.textContent = 'Get Review';
        }
    }
}

// Delete a review
async function deleteReview(movieId, reviewId) {
    if (!confirm('Are you sure you want to delete this review?')) {
        return;
    }
    
    try {
        const response = await fetch(`/api/movie/${movieId}/review/${reviewId}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            throw new Error('Failed to delete review');
        }
        
        // Reload reviews
        const reviewsContainer = document.getElementById(`movie-reviews-${movieId}`);
        if (reviewsContainer) {
            const html = await loadMovieReviews(movieId);
            reviewsContainer.innerHTML = html;
        }
        
        showStatus('Review deleted', 'success');
    } catch (error) {
        console.error('Error deleting review:', error);
        showStatus('Error deleting review', 'error');
    }
}

// Generate related movies
async function generateRelatedMovies(movieId) {
    const providerSelect = document.getElementById(`review-provider-${movieId}`);
    const relatedContainer = document.getElementById(`related-movies-${movieId}`);
    const relatedBtn = document.getElementById(`related-movies-btn-${movieId}`);
    
    if (!providerSelect || !relatedContainer) return;
    
    const provider = providerSelect.value;
    
    // Disable button
    if (relatedBtn) {
        relatedBtn.disabled = true;
        relatedBtn.textContent = 'Finding...';
    }
    
    // Show progress UI
    relatedContainer.innerHTML = `
        <div class="related-movies-progress">
            <div class="related-movies-progress-step" data-step="1">
                <span class="related-movies-progress-indicator">○</span>
                <span class="related-movies-progress-text">Preparing query...</span>
            </div>
            <div class="related-movies-progress-step" data-step="2">
                <span class="related-movies-progress-indicator">○</span>
                <span class="related-movies-progress-text">Waiting for AI response...</span>
            </div>
            <div class="related-movies-progress-step" data-step="3">
                <span class="related-movies-progress-indicator">○</span>
                <span class="related-movies-progress-text">Matching movies...</span>
            </div>
        </div>
    `;
    
    function updateProgress(step, message) {
        for (let i = 1; i < step; i++) {
            const prevStep = relatedContainer.querySelector(`.related-movies-progress-step[data-step="${i}"]`);
            if (prevStep) {
                prevStep.classList.add('completed');
                prevStep.classList.remove('active');
                prevStep.querySelector('.related-movies-progress-indicator').textContent = '✓';
            }
        }
        const currentStep = relatedContainer.querySelector(`.related-movies-progress-step[data-step="${step}"]`);
        if (currentStep) {
            currentStep.classList.add('active');
            currentStep.classList.remove('completed');
            currentStep.querySelector('.related-movies-progress-indicator').textContent = '●';
            currentStep.querySelector('.related-movies-progress-text').textContent = message;
        }
    }
    
    try {
        const response = await fetch(`/api/movie/${movieId}/related-movies`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                provider: provider
            })
        });
        
        if (!response.ok) {
            let detail = 'Related movies search failed';
            try {
                const errorData = await response.json();
                detail = errorData.detail || detail;
            } catch (parseError) {
                // ignore parse errors
            }
            throw new Error(detail);
        }
        
        // Read SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let resultData = null;
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            
            const lines = buffer.split('\n');
            buffer = lines.pop();
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const eventData = JSON.parse(line.slice(6));
                        
                        if (eventData.type === 'progress') {
                            updateProgress(eventData.step, eventData.message);
                        } else if (eventData.type === 'result') {
                            resultData = eventData;
                        } else if (eventData.type === 'error') {
                            throw new Error(eventData.detail);
                        }
                    } catch (parseErr) {
                        console.warn('Failed to parse SSE event:', line, parseErr);
                    }
                }
            }
        }
        
        if (resultData) {
            renderRelatedMovies(movieId, resultData);
            showStatus('Related movies found', 'success');
        } else {
            throw new Error('No result received');
        }
    } catch (error) {
        console.error('Related Movies Error:', error);
        showStatus(`Error: ${error.message}`, 'error');
        relatedContainer.innerHTML = '';
    } finally {
        if (relatedBtn) {
            relatedBtn.disabled = false;
            relatedBtn.textContent = 'Related Movies';
        }
    }
}

// Render related movies
function renderRelatedMovies(movieId, data) {
    const container = document.getElementById(`related-movies-${movieId}`);
    if (!container) return;
    
    const foundMovies = data.found_movies || [];
    
    if (foundMovies.length === 0) {
        container.innerHTML = '<div class="related-movies-empty">No related movies found in your library.</div>';
        return;
    }
    
    const cardsHtml = foundMovies.map(movie => {
        const relationship = movie.relationship || 'Related';
        const cardHtml = createMovieCard(movie, {
            showMenu: true,
            showRating: true,
            showMeta: true
        });
        // Badge below the card
        return `<div class="related-movie-wrapper">
            ${cardHtml}
            <div class="related-movie-badge">${escapeHtml(relationship)}</div>
        </div>`;
    }).join('');
    
    container.innerHTML = `
        <div class="related-movies-section">
            <div class="related-movies-label">Related Movies (${foundMovies.length})</div>
            <div class="related-movies-grid">${cardsHtml}</div>
        </div>
    `;
    
    // Initialize star ratings
    if (typeof initAllStarRatings === 'function') {
        initAllStarRatings();
    }
}

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
            headers: { 'Content-Type': 'application/json' },
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

        // Validate subtitle path if one is selected
        if (selectedSubtitle && typeof selectedSubtitle !== 'string') {
            showStatus('Invalid subtitle selection', 'error');
            return;
        }

        const response = await fetch('/api/launch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                movie_id: movieId,
                subtitle_path: selectedSubtitle,
                close_existing_vlc: true
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
        const showSizes = shouldShowMovieSizes();

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
                const sizeStr = showSizes && m.size ? formatSize(m.size) : '';
                const resolution = parseResolution(m.path || '');
                const resolutionBadge = resolution ? `<span class="same-title-resolution">${resolution}</span>` : '';
                const hiddenBadge = m.hidden ? '<span class="same-title-hidden-badge">hidden</span>' : '';
                return `<a href="#/movie/${m.id}" class="same-title-item" title="${escapeHtml(m.path)}">
                    ${sizeStr ? `<span class="same-title-size movie-size">${sizeStr}</span>` : ''}
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

        // Render the transcription section (async, placeholder first)
        const transcriptionPlaceholder = `<div id="transcription-placeholder-${movie.id}"><div class="loading" style="padding: 20px;">Loading transcription status...</div></div>`;

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
                        ${showSizes && movie.size ? `<span class="movie-size">${formatSize(movie.size)}</span>` : ''}
                        ${movie.watched_date ? `<span>Watched: ${formatDate(movie.watched_date)}</span>` : ''}
                    </div>
                    <div id="movie-playlists-${movie.id}" class="movie-playlists-container"></div>
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
                    <div class="review-controls" style="margin-top: 20px; display: inline-flex; flex-direction: column; gap: 10px; width: auto;">
                        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                            <button id="related-movies-btn-${movie.id}" class="btn btn-small" onclick="generateRelatedMovies(${movie.id})">Related Movies</button>
                            <button id="generate-review-btn-${movie.id}" class="btn btn-small" onclick="generateReview(${movie.id})">Get AI Review</button>
                            <select id="review-provider-${movie.id}" style="padding: 4px 8px; background: #1a1a1a; border: 1px solid #333; color: #888; border-radius: 4px; font-size: 12px;">
                                <option value="anthropic" selected>Claude Opus 4.5</option>
                                <option value="openai">GPT-5.1</option>
                            </select>
                        </div>
                        <details style="margin-top: 0;">
                            <summary style="cursor: pointer; color: #888; font-size: 12px;">Further instructions (optional)</summary>
                            <textarea id="review-instructions-${movie.id}" placeholder="Add any additional instructions for the review..." style="width: 100%; min-height: 60px; margin-top: 8px; padding: 8px; background: #1a1a1a; border: 1px solid #333; color: white; border-radius: 4px; font-family: inherit; font-size: 13px;"></textarea>
                        </details>
                    </div>
                    <div id="related-movies-${movie.id}" class="related-movies-container"></div>
                    <div id="review-generation-${movie.id}"></div>
                    <div style="margin-top: 20px; color: #999; font-size: 12px;">
                        <div>Path: ${escapeHtml(movie.path)}</div>
                        ${subtitleIndicator}
                        ${movie.created ? `<div>Created: ${formatDate(movie.created)}</div>` : ''}
                    </div>
                </div>
            </div>
            <div id="movie-reviews-${movie.id}" class="movie-reviews-container"></div>
            <div id="movie-lists-${movie.id}" class="movie-lists-container"></div>
            ${mediaGallery}
            ${transcriptionPlaceholder}
            </div>
        `;
        initAllStarRatings();

        // Load playlists section asynchronously
        loadMoviePlaylists(movie.id).then(html => {
            const playlistsContainer = document.getElementById(`movie-playlists-${movie.id}`);
            if (playlistsContainer && html) {
                playlistsContainer.innerHTML = html;
            }
        });

        // Load AI reviews section asynchronously
        loadMovieReviews(movie.id).then(html => {
            const reviewsContainer = document.getElementById(`movie-reviews-${movie.id}`);
            if (reviewsContainer && html) {
                reviewsContainer.innerHTML = html;
            }
        });

        // Load AI-generated movie lists section asynchronously
        loadMovieLists(movie.id).then(html => {
            const listsContainer = document.getElementById(`movie-lists-${movie.id}`);
            if (listsContainer && html) {
                listsContainer.innerHTML = html;
            }
        });

        // Load transcription section asynchronously
        if (typeof renderTranscriptionSection === 'function') {
            renderTranscriptionSection(movie.id).then(html => {
                const placeholder = document.getElementById(`transcription-placeholder-${movie.id}`);
                if (placeholder) {
                    placeholder.outerHTML = html;
                }
            }).catch(err => {
                console.error('Failed to load transcription section:', err);
                const placeholder = document.getElementById(`transcription-placeholder-${movie.id}`);
                if (placeholder) {
                    placeholder.innerHTML = '';
                }
            });
        }
        // Menu state is pre-computed server-side, no need for additional API calls
    } catch (error) {
        container.innerHTML = `<div class="empty-state">Error loading movie: ${error.message}</div>`;
    }
}

// Close same-title dropdown when clicking outside
document.addEventListener('click', function (event) {
    const indicator = document.querySelector('.same-title-indicator.expanded');
    if (indicator && !indicator.contains(event.target)) {
        indicator.classList.remove('expanded');
    }
});
