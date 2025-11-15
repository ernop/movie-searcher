// Shared Star Rating Component
// This component provides a consistent star rating UI across the application
// All parts of the app should use this component for star ratings

function renderStarRating(path, isWatched, rating = null) {
    // If watched but no rating, show 0 stars (user can click to rate). If not watched, show 0 stars.
    const starsToShow = (isWatched && rating !== null) ? rating : 0;
    const fullStars = Math.floor(starsToShow);
    const hasHalfStar = (starsToShow % 1) >= 0.5;
    
    let starsHtml = '';
    for (let i = 0; i < 5; i++) {
        let starClass = '';
        if (i < fullStars) {
            starClass = 'active';
        } else if (i === fullStars && hasHalfStar) {
            starClass = 'half';
        }
        // Calculate rating value: clicking left half = i + 0.5, right half = i + 1
        const leftHalfRating = i + 0.5;
        const rightHalfRating = i + 1;
        // Escape path for JavaScript string literal (backslashes need to be escaped)
        const escapedPath = path.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
        starsHtml += `
            <span class="star ${starClass}" 
                  data-rating-left="${leftHalfRating}" 
                  data-rating-right="${rightHalfRating}"
                  onclick="event.stopPropagation(); handleStarClick(event, '${escapedPath}', ${leftHalfRating}, ${rightHalfRating})">★</span>`;
    }
    
    // Escape path for JavaScript string literal
    const escapedPath = path.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
    return `
        <div class="star-rating" onclick="event.stopPropagation(); toggleWatched('${escapedPath}', ${isWatched})" title="${isWatched ? 'Click stars to rate, or click empty area to toggle watched' : 'Click stars to mark as watched'}">
            ${starsHtml}
        </div>
    `;
}

function handleStarClick(event, path, leftRating, rightRating) {
    event.stopPropagation();
    const star = event.currentTarget;
    const rect = star.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const width = rect.width;
    
    // Determine if click was on left or right half
    const rating = clickX < width / 2 ? leftRating : rightRating;
    setRating(path, rating);
}

