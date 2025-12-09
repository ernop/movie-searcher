// Star Rating Component
function createStarRating(movieId, currentRating, containerClass = '') {
    const rating = currentRating || 0;
    let html = `<div class="star-rating ${containerClass}" data-movie-id="${movieId}" data-rating="${rating}" onclick="event.stopPropagation();">`;
    for (let i = 1; i <= 5; i++) {
        const filled = i <= rating ? 'filled' : '';
        html += `<span class="star ${filled}" data-value="${i}">★</span>`;
    }
    html += '</div>';
    return html;
}

// Initialize star rating interactions
function initStarRating(element) {
    const movieId = parseInt(element.getAttribute('data-movie-id'), 10);
    const stars = element.querySelectorAll('.star');
    let currentRating = parseInt(element.getAttribute('data-rating') || '0', 10);
    
    function updateDisplay(rating) {
        stars.forEach((star, index) => {
            if (index + 1 <= rating) {
                star.classList.add('filled');
            } else {
                star.classList.remove('filled');
            }
        });
    }
    
    function setRating(rating) {
        if (rating < 1 || rating > 5) return;
        
        fetch('/api/rating', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                movie_id: movieId,
                rating: rating
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'updated') {
                currentRating = rating;
                element.setAttribute('data-rating', rating);
                updateDisplay(rating);
            }
        })
        .catch(error => {
            console.error('Error setting rating:', error);
        });
    }
    
    stars.forEach((star, index) => {
        const value = index + 1;
        
        star.addEventListener('mouseenter', () => {
            updateDisplay(value);
        });
        
        star.addEventListener('click', (e) => {
            e.stopPropagation();
            setRating(value);
        });
    });
    
    element.addEventListener('mouseleave', () => {
        updateDisplay(currentRating);
    });
}

// Initialize all star ratings on the page
function initAllStarRatings() {
    document.querySelectorAll('.star-rating').forEach(initStarRating);
}

// Global click listener to close menus
document.addEventListener('click', (e) => {
    if (!e.target.closest('.movie-card-menu-btn') && !e.target.closest('.movie-card-menu-dropdown') && !e.target.closest('.btn-secondary')) {
        document.querySelectorAll('.movie-card-menu-dropdown.active').forEach(el => {
            el.classList.remove('active');
        });
    }
});

function toggleCardMenu(btn, menuId) {
    // If menuId is a number, assume it's a movie ID and construct the ID
    // If it's a string, use it as is
    const id = typeof menuId === 'number' ? `menu-${menuId}` : menuId;
    const menu = document.getElementById(id);
    if (!menu) return;

    // Close other menus
    document.querySelectorAll('.movie-card-menu-dropdown.active').forEach(el => {
        if (el !== menu) el.classList.remove('active');
    });
    menu.classList.toggle('active');
}

function hideMovie(movieId) {
    fetch(`/api/movie/${movieId}/hide`, {
        method: 'POST'
    })
    .then(response => {
        if (response.ok) {
            showStatus('Movie hidden', 'success');
            // Remove card from UI if present
            const card = document.querySelector(`.movie-card[data-movie-id="${movieId}"]`);
            if (card) {
                card.remove();
            }
            // If in details view, go back
            if (window.location.hash.includes(`/movie/${movieId}`)) {
                historyBack();
            }
        } else {
            showStatus('Failed to hide movie', 'error');
        }
    })
    .catch(error => {
        console.error('Error hiding movie:', error);
        showStatus('Error hiding movie', 'error');
    });
}

// Movie Card Component
function createMovieCard(movie, options = {}) {
    // Default options
    const {
        showMenu = true,
        showRating = true,
        showMeta = true,
        showPath = false,
        primaryAction = 'launch', // 'launch', 'none'
        watchStatusControl = true,
        customButtons = null // HTML string for custom buttons
    } = options;

    // Use helper for image URL
    const imageUrl = getMovieImageUrl(movie);
    
    // Use watch_status if available (can be string enum or boolean for backward compat), fallback to watched boolean
    let watchStatus = movie.watch_status !== undefined ? movie.watch_status : (movie.watched ? 'watched' : null);
    // Normalize: convert boolean to string enum for backward compatibility
    if (watchStatus === true) watchStatus = 'watched';
    if (watchStatus === false) watchStatus = 'unwatched';
    
    const watchedClass = watchStatus === 'watched' ? 'watched' : '';
    const showSizes = shouldShowMovieSizes();
    const fileSize = showSizes && movie.size ? formatSize(movie.size) : '';
    const year = movie.year ? movie.year : '';
    const length = movie.length ? formatMinutes(movie.length) : '';
    const hasLaunched = movie.has_launched || false;
    
    // Determine checkbox class based on watch_status
    let checkboxClass = 'unset';
    if (watchStatus === 'watched') {
        checkboxClass = 'watched';
    } else if (watchStatus === 'unwatched') {
        checkboxClass = 'unwatched';
    } else if (watchStatus === 'want_to_watch') {
        checkboxClass = 'want-to-watch';
    }
    
    // Use helper for slug
    const slug = getMovieSlug(movie);
    // No longer using onclick for the card, using <a> overlay instead
    // const cardClick = `openMovieHash(${movie.id}, '${encodeURIComponent(slug)}')`;

    let metaHtml = '';
    if (showMeta) {
        // Added pointer-events logic to allow clicks on text to fall through to the card link, 
        // but kept year-link and checkbox interactive.
        const reviewCount = movie.review_count || 0;
        const reviewIndicator = reviewCount > 0 ? `
            <a href="#/movie/${movie.id}" class="review-indicator" style="pointer-events: auto; margin-left: auto;" 
               data-movie-id="${movie.id}" 
               data-review-count="${reviewCount}"
               onmouseenter="showReviewPreview(this, ${movie.id})"
               onmouseleave="hideReviewPreview(this)">
                📝 Review${reviewCount > 1 ? 's' : ''}
            </a>
        ` : '';
        
        metaHtml = `
            <div class="movie-card-meta" style="position: relative; z-index: 2; pointer-events: none; display: flex; align-items: center; gap: 8px;">
                ${year ? `<span class="year-link" onclick="event.stopPropagation(); navigateToExploreWithYear(${year}, ${movie.id || 'null'});" title="Filter by ${year}" style="pointer-events: auto;">${year}</span>` : ''}
                ${length ? `<span>${length}</span>` : ''}
                ${fileSize ? `<span class="movie-size">${fileSize}</span>` : ''}
                ${reviewIndicator}
                ${hasLaunched ? '<div class="launch-status-checkbox launched" onclick="event.stopPropagation();" style="pointer-events: auto;"></div>' : ''}
            </div>
        `;
    } else if (showPath && movie.path) {
        // Minimal meta showing path (e.g. for duplicates)
        metaHtml = `
            <div class="movie-card-meta" style="position: relative; z-index: 2; pointer-events: none;">
                ${year ? `<span class="year-link" onclick="event.stopPropagation();" style="pointer-events: auto;">${year}</span>` : ''}
                ${showSizes && movie.size ? `<span class="movie-size" style="margin-left: auto; font-size: 11px; color: #666;">${formatSize(movie.size)}</span>` : ''}
            </div>
            <div class="result-path" style="margin-bottom: 10px; font-size: 10px; color: #666; word-break: break-all; line-height: 1.2; position: relative; z-index: 2; pointer-events: auto; user-select: text;">
                ${escapeHtml(movie.path)}
            </div>
        `;
    }

    let menuHtml = '';
    if (showMenu) {
        // Use central menu renderer from movie-menu.js
        if (typeof renderCardMenu === 'function') {
            menuHtml = renderCardMenu(movie);
        } else {
            // Fallback if movie-menu.js not loaded yet
            console.warn('movie-menu.js not loaded, using fallback menu');
            menuHtml = `
                <div style="position: relative; z-index: 2;">
                    <button class="movie-card-menu-btn" onclick="event.stopPropagation(); toggleCardMenu(this, ${movie.id})">⋮</button>
                    <div class="movie-card-menu-dropdown" id="menu-${movie.id}">
                        <button class="movie-card-menu-item" onclick="event.stopPropagation(); openFolder('${escapeJsString(movie.path || '')}')">Open Folder</button>
                        <button class="movie-card-menu-item" onclick="event.stopPropagation(); showAddToPlaylistMenu(${movie.id})">Add to playlist</button>
                        <button class="movie-card-menu-item" onclick="event.stopPropagation(); hideMovie(${movie.id})">Don't show this anymore</button>
                    </div>
                </div>
            `;
        }
    }

    let buttonsHtml = '';
    if (customButtons) {
        buttonsHtml = customButtons;
    } else {
        const watchBtn = watchStatusControl ? `
            <button class="movie-card-btn" onclick="event.stopPropagation(); toggleWatched(${movie.id}, ${watchStatus === null ? 'null' : `'${watchStatus}'`})">
                <span class="watched-checkbox ${checkboxClass}"></span>${watchStatus === 'watched' ? 'watched' : watchStatus === 'unwatched' ? 'not watched' : watchStatus === 'want_to_watch' ? 'want to see' : '-'}
            </button>
        ` : '';
        
        const launchBtn = primaryAction === 'launch' ? `
            <button class="movie-card-launch" onclick="event.stopPropagation(); launchMovie(${movie.id})">▶</button>
        ` : '';
        
        buttonsHtml = watchBtn + launchBtn;
    }

    return `
        <div class="movie-card ${watchedClass}" data-movie-id="${movie.id || ''}" style="position: relative;">
            <a href="#/movie/${movie.id}/${slug}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; text-decoration: none; outline: none;" aria-label="${escapeHtml(movie.name)}"></a>
            
            <div class="movie-card-image">
                ${imageUrl ? `<img src="${imageUrl}" alt="${escapeHtml(movie.name)}" loading="lazy" onerror="this.parentElement.innerHTML='No Image'" onload="const img = this; const container = img.parentElement; if (img.naturalWidth && img.naturalHeight) { const ar = img.naturalWidth / img.naturalHeight; container.style.aspectRatio = ar + ' / 1'; }">` : 'No Image'}
            </div>
            <div class="movie-card-body">
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div class="movie-card-title">${escapeHtml(movie.name)}</div>
                    ${menuHtml}
                </div>
                ${metaHtml}
                <div style="position: relative; z-index: 2; pointer-events: none;">
                    ${showRating ? createStarRating(movie.id || 0, movie.rating || null, 'movie-card-rating').replace('class="star-rating', 'style="pointer-events: auto;" class="star-rating') : ''}
                </div>
                <div class="movie-card-buttons" style="position: relative; z-index: 2; pointer-events: none;">
                    <!-- Inject pointer-events: auto into buttons via replacement or assume they have it? -->
                    <!-- Buttons are interactive elements, they usually have pointer-events: auto by default, but if parent is none... -->
                    <!-- Inherited pointer-events: none makes children none unless overridden. -->
                    <!-- So I must override on children. -->
                    ${buttonsHtml.replace(/<button/g, '<button style="pointer-events: auto;"')}
                </div>
            </div>
        </div>
    `;
}

// Review preview cache
const reviewPreviewCache = {};

async function showReviewPreview(element, movieId) {
    // Check cache first
    if (reviewPreviewCache[movieId]) {
        displayReviewTooltip(element, reviewPreviewCache[movieId]);
        return;
    }
    
    // Fetch first review
    try {
        const response = await fetch(`/api/movie/${movieId}/reviews`);
        if (!response.ok) return;
        
        const data = await response.json();
        const reviews = data.reviews || [];
        
        if (reviews.length > 0) {
            const firstReview = reviews[0];
            const preview = firstReview.response_text.substring(0, 300);
            const fullText = firstReview.response_text;
            const cacheEntry = {
                preview: preview,
                fullText: fullText,
                model: firstReview.model_name,
                date: firstReview.created
            };
            reviewPreviewCache[movieId] = cacheEntry;
            displayReviewTooltip(element, cacheEntry);
        }
    } catch (error) {
        console.error('Error fetching review preview:', error);
    }
}

function displayReviewTooltip(element, reviewData) {
    // Remove existing tooltip if any
    const existing = element.querySelector('.review-indicator-tooltip');
    if (existing) existing.remove();
    
    const tooltip = document.createElement('div');
    tooltip.className = 'review-indicator-tooltip';
    tooltip.innerHTML = `
        <div style="font-weight: 500; margin-bottom: 6px; color: #e8d090;">${escapeHtml(reviewData.model || 'Review')}</div>
        <div style="max-height: 200px; overflow-y: auto;">${escapeHtml(reviewData.preview)}${reviewData.fullText.length > 300 ? '...' : ''}</div>
    `;
    element.style.position = 'relative';
    element.appendChild(tooltip);
}

function hideReviewPreview(element) {
    const tooltip = element.querySelector('.review-indicator-tooltip');
    if (tooltip) {
        tooltip.remove();
    }
}

// Render Markdown to HTML
function renderMarkdown(text) {
    if (!text) return '';
    
    let html = text;
    
    // Escape HTML first to prevent XSS
    html = escapeHtml(html);
    
    // Code blocks first (before other processing): ```code```
    const codeBlocks = [];
    html = html.replace(/```([\s\S]*?)```/g, (match, code) => {
        const id = `CODEBLOCK_${codeBlocks.length}`;
        codeBlocks.push(`<pre><code>${code}</code></pre>`);
        return id;
    });
    
    // Inline code: `code` (but not inside code blocks)
    html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    
    // Headers: # Header, ## Header, ### Header (must be at start of line)
    html = html.replace(/^### (.*)$/gim, '<h3>$1</h3>');
    html = html.replace(/^## (.*)$/gim, '<h2>$1</h2>');
    html = html.replace(/^# (.*)$/gim, '<h1>$1</h1>');
    
    // Bold: **text** or __text__ (process before italic)
    html = html.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+?)__/g, '<strong>$1</strong>');
    
    // Italic: *text* or _text_ (but not if it's part of **text**)
    // Simple approach: match single * or _ that aren't part of double
    html = html.replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, '<em>$1</em>');
    html = html.replace(/(?<!_)_([^_\n]+?)_(?!_)/g, '<em>$1</em>');
    
    // Restore code blocks
    codeBlocks.forEach((block, i) => {
        html = html.replace(`CODEBLOCK_${i}`, block);
    });
    
    // Line breaks: convert \n to <br> (but not inside code blocks)
    const parts = html.split(/(<pre><code>[\s\S]*?<\/code><\/pre>)/);
    for (let i = 0; i < parts.length; i += 2) {
        // Only process non-code parts
        parts[i] = parts[i].replace(/\n/g, '<br>');
    }
    html = parts.join('');
    
    // Paragraphs: double line breaks create paragraphs
    html = html.replace(/(<br>\s*){2,}/g, '</p><p>');
    
    // Wrap in paragraph tags if not already wrapped and doesn't start with block elements
    if (!html.trim().match(/^<(h[1-6]|p|pre|ul|ol)/i)) {
        html = '<p>' + html + '</p>';
    }
    
    return html;
}
