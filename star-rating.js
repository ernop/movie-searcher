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
                  data-star-index="${i}"
                  data-rating-left="${leftHalfRating}" 
                  data-rating-right="${rightHalfRating}"
                  onclick="event.stopPropagation(); handleStarClick(event, '${escapedPath}')">★</span>`;
    }
    
    // Escape path for JavaScript string literal
    const escapedPath = path.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
    return `
        <div class="star-rating" onclick="event.stopPropagation(); toggleWatched('${escapedPath}', ${isWatched})" title="${isWatched ? 'Click stars to rate, or click empty area to toggle watched' : 'Click stars to mark as watched'}">
            ${starsHtml}
        </div>
    `;
}

function handleStarClick(event, path) {
    event.stopPropagation();
    event.preventDefault();
    
    console.log('=== STAR CLICK DETECTION ===');
    console.log('event.target:', event.target);
    console.log('event.target.nodeType:', event.target.nodeType);
    console.log('event.target.nodeName:', event.target.nodeName);
    console.log('event.target.className:', event.target.className);
    console.log('event.target.textContent:', event.target.textContent);
    
    // Get the star element that was actually clicked
    // event.target might be the span itself or the text node "★" inside it
    let star = event.target;
    if (star.nodeType === Node.TEXT_NODE) {
        console.log('Detected text node, getting parent element');
        // If clicking on the text node, get its parent (the span)
        star = star.parentElement;
        console.log('Parent element:', star);
    }
    
    // Verify it's actually a star element
    if (!star || !star.classList.contains('star')) {
        console.error('Could not find star element', {target: event.target, star});
        return;
    }
    
    console.log('Star element found:', star);
    console.log('Star classes:', star.className);
    console.log('Star index (data-star-index):', star.getAttribute('data-star-index'));
    
    // Get rating values from data attributes
    const leftRating = parseFloat(star.getAttribute('data-rating-left'));
    const rightRating = parseFloat(star.getAttribute('data-rating-right'));
    
    console.log('Star rating values:', {
        leftRating: leftRating,
        rightRating: rightRating,
        'data-rating-left': star.getAttribute('data-rating-left'),
        'data-rating-right': star.getAttribute('data-rating-right')
    });
    
    // Validate that we got valid ratings
    if (isNaN(leftRating) || isNaN(rightRating)) {
        console.error('Invalid rating values from star element', {leftRating, rightRating, star});
        return;
    }
    
    // Calculate which half of the star was clicked
    const rect = star.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const width = rect.width;
    
    console.log('Click position:', {
        clickX: clickX,
        width: width,
        clickXPercent: ((clickX / width) * 100).toFixed(1) + '%',
        isLeftHalf: clickX < width / 2
    });
    
    // Determine rating: left half = leftRating, right half = rightRating
    const ratingValue = clickX < width / 2 ? leftRating : rightRating;
    
    console.log('=== FINAL RESULT ===');
    console.log('Selected rating:', ratingValue);
    console.log('Star index:', star.getAttribute('data-star-index'));
    console.log('Path:', path);
    console.log('========================');
    
    setRating(path, ratingValue);
}

