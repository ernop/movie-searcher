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

// Movie Card Component
function createMovieCard(movie) {
    // Helper to extract filename from path
    function getFilename(path) {
        if (!path) return null;
        const parts = path.replace(/\\/g, '/').split('/');
        return parts[parts.length - 1];
    }
    
    // Prefer API endpoint by screenshot_id for reliability, fallback to image_path
    let imageUrl = '';
    if (movie.screenshot_id) {
        // Use API endpoint - most reliable, handles path issues correctly
        imageUrl = `/api/screenshot/${movie.screenshot_id}`;
    } else if (movie.image_path) {
        // Use movie.image_path directly - check if it's a screenshot or movie image
        const filename = getFilename(movie.image_path);
        if (filename && movie.image_path.includes('screenshots')) {
            // Screenshot: use /screenshots/ endpoint
            imageUrl = `/screenshots/${encodeURIComponent(filename)}`;
        } else {
            // Movie image: use image_path_url if available (relative path from backend), otherwise extract from absolute path
            if (movie.image_path_url) {
                imageUrl = `/movies/${encodeURIComponent(movie.image_path_url)}`;
            } else {
                // Fallback: extract relative path manually (shouldn't happen if backend is correct)
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
    }
    // Use watch_status if available (can be string enum or boolean for backward compat), fallback to watched boolean
    let watchStatus = movie.watch_status !== undefined ? movie.watch_status : (movie.watched ? 'watched' : null);
    // Normalize: convert boolean to string enum for backward compatibility
    if (watchStatus === true) watchStatus = 'watched';
    if (watchStatus === false) watchStatus = 'unwatched';
    
    const watchedClass = watchStatus === 'watched' ? 'watched' : '';
    const fileSize = movie.size ? formatSize(movie.size) : '';
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
        checkboxClass = 'want-to-watch';  // Add CSS class for want_to_watch if needed
    }
    
    // Prefer hash-based ID route if we have an id
    const slug = (movie.name || '').toString()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
    const cardClick = `openMovieHash(${movie.id}, '${encodeURIComponent(slug)}')`;

    return `
        <div class="movie-card ${watchedClass}" data-movie-id="${movie.id || ''}" onclick="${cardClick}">
            <div class="movie-card-image">
                ${imageUrl ? `<img src="${imageUrl}" alt="${escapeHtml(movie.name)}" loading="lazy" onerror="this.parentElement.innerHTML='No Image'" onload="const img = this; const container = img.parentElement; if (img.naturalWidth && img.naturalHeight) { const ar = img.naturalWidth / img.naturalHeight; container.style.aspectRatio = ar + ' / 1'; }">` : 'No Image'}
            </div>
            <div class="movie-card-body">
                <div class="movie-card-title">${escapeHtml(movie.name)}</div>
                <div class="movie-card-meta">
                    ${year ? `<span class="year-link" onclick="event.stopPropagation(); navigateToExploreWithYear(${year}, ${movie.id || 'null'});" title="Filter by ${year}">${year}</span>` : ''}
                    ${length ? `<span>${length}</span>` : ''}
                    ${fileSize ? `<span>${fileSize}</span>` : ''}
                    ${hasLaunched ? '<div class="launch-status-checkbox launched" onclick="event.stopPropagation();"></div>' : ''}
                </div>
                ${createStarRating(movie.id || 0, movie.rating || null, 'movie-card-rating')}
                <div class="movie-card-buttons">
                    <button class="movie-card-btn" onclick="event.stopPropagation(); toggleWatched(${movie.id}, ${watchStatus === null ? 'null' : `'${watchStatus}'`})">
                        <span class="watched-checkbox ${checkboxClass}"></span>${watchStatus === 'watched' ? 'watched' : watchStatus === 'unwatched' ? 'not watched' : watchStatus === 'want_to_watch' ? 'want to see' : '-'}
                    </button>
                    <button class="movie-card-launch" onclick="event.stopPropagation(); launchMovie(${movie.id})">▶</button>
                </div>
            </div>
        </div>
    `;
}

