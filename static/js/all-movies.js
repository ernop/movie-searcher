// All Movies List

async function loadAllMovies() {
    const listContainer = document.getElementById('allMoviesList');
    const statsContainer = document.getElementById('allMoviesStats');
    
    listContainer.innerHTML = '<div class="loading">Loading movies...</div>';
    statsContainer.innerHTML = '';
    
    try {
        const response = await fetch('/api/all-movies');
        if (!response.ok) {
            throw new Error('Failed to load movies');
        }
        
        const data = await response.json();
        const movies = data.movies;
        const total = data.total;
        
        if (movies.length === 0) {
            listContainer.innerHTML = '<div class="empty-state">No movies found</div>';
            restoreScrollPosition();
            return;
        }
        
        // Display stats
        statsContainer.innerHTML = `Showing ${total} movies`;
        
        // Display movies as list
        const listHTML = '<div class="all-movies-list">' +
            movies.map(movie => `
                <div class="all-movies-item" onclick="navigateTo('/movie/${movie.id}')">
                    <span class="all-movies-item-name">${escapeHtml(movie.name)}</span>
                    <span class="all-movies-item-year">${movie.year || 'Unknown'}</span>
                </div>
            `).join('') +
            '</div>';
        
        listContainer.innerHTML = listHTML;
        
        // Restore scroll position after render
        restoreScrollPosition();
    } catch (error) {
        console.error('Error loading all movies:', error);
        listContainer.innerHTML = `<div class="empty-state">Error loading movies: ${error.message}</div>`;
        restoreScrollPosition();
    }
}

