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
    event.preventDefault();
    // For inline onclick handlers, event.currentTarget might not work correctly
    // Find the star element (could be event.target or its parent if clicking on text node)
    let star = event.target;
    while (star && !star.classList.contains('star')) {
        star = star.parentElement;
    }
    if (!star) {
        // Fallback: use the element that has the onclick handler
        star = event.target.closest('.star');
    }
    if (!star) {
        console.error('Could not find star element');
        return;
    }
    
    // Use data attributes from the actual clicked star element to ensure we get the correct rating values
    // This is more reliable than using the function parameters, which might be from a different star
    const actualLeftRating = parseFloat(star.getAttribute('data-rating-left'));
    const actualRightRating = parseFloat(star.getAttribute('data-rating-right'));
    
    // Validate that we got valid ratings
    if (isNaN(actualLeftRating) || isNaN(actualRightRating)) {
        console.error('Invalid rating values from star element', {actualLeftRating, actualRightRating});
        return;
    }
    
    const rect = star.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const width = rect.width;
    
    // Determine if click was on left or right half using the actual star's rating values
    const rating = clickX < width / 2 ? actualLeftRating : actualRightRating;
    
    // Ensure rating is a number, not a string
    const ratingValue = parseFloat(rating);
    if (isNaN(ratingValue)) {
        console.error('Invalid rating calculated', {rating, clickX, width, actualLeftRating, actualRightRating});
        return;
    }
    
    console.log('Star clicked:', {path, rating: ratingValue, clickX, width, leftHalf: clickX < width / 2});
    setRating(path, ratingValue);
}

