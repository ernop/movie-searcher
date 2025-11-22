// History functions
async function loadHistory() {
    const historyList = document.getElementById('historyList');
    if (!historyList) return;
    
    historyList.innerHTML = '<div class="loading">Loading history...</div>';
    
    try {
        const response = await fetch('/api/launch-history');
        const data = await response.json();
        
        if (!response.ok) {
            historyList.innerHTML = '<div class="empty-state">Error loading history: ' + (data.detail || 'Unknown error') + '</div>';
            return;
        }
        
        const launches = data.launches || [];
        
        if (launches.length === 0) {
            historyList.innerHTML = '<div class="empty-state">No launch history yet</div>';
            return;
        }
        
        let html = '';
        for (const launch of launches) {
            const movie = launch.movie;
            const timestamp = launch.timestamp;
            
            // Format timestamp with day of week
            let formattedTime = 'Unknown time';
            if (timestamp) {
                try {
                    const date = new Date(timestamp);
                    const daysOfWeek = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
                    const dayOfWeek = daysOfWeek[date.getDay()];
                    formattedTime = `${dayOfWeek}, ${date.toLocaleString()}`;
                } catch (e) {
                    formattedTime = timestamp;
                }
            }
            
        // Render movie card with a small timestamp below
        const hasId = movie && movie.id != null && !Number.isNaN(movie.id);
        const cardHtml = createMovieCard(movie);
        html += `
            <div class="history-item">
                <div class="history-movie-card"${hasId ? '' : ' style="pointer-events: none;"'}>
                    ${cardHtml}
                </div>
                <div class="history-timestamp">${escapeHtml(formattedTime)}</div>
                ${hasId ? '' : '<div class="history-timestamp" style="color:#ff9a9a;">Missing movie id; re-index to enable navigation</div>'}
            </div>
        `;
        }
        
        historyList.innerHTML = html;
        initAllStarRatings();

        // Restore scroll position if available
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
    } catch (error) {
        historyList.innerHTML = '<div class="empty-state">Error loading history: ' + error.message + '</div>';
        console.error('History error:', error);
    }
}

async function recleanAllNames() {
    const status = document.getElementById('recleanStatus');
    if (!status) return;
    
    status.style.display = 'block';
    status.style.background = '#2a2a2a';
    status.style.color = '#aaa';
    status.textContent = 'Re-cleaning all movie names...';
    
    try {
        const response = await fetch('/api/admin/reclean-names', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });
        const data = await response.json();
        
        if (response.ok) {
            status.style.background = '#2d5a2d';
            status.style.color = '#aaffaa';
            status.textContent = data.message || `Re-cleaned ${data.updated} of ${data.total} movie names`;
        } else {
            status.style.background = '#5a2d2d';
            status.style.color = '#ffaaaa';
            status.textContent = `Error: ${data.detail || 'Failed to re-clean names'}`;
        }
    } catch (error) {
        status.style.background = '#5a2d2d';
        status.style.color = '#ffaaaa';
        status.textContent = `Error: ${error.message || 'Unknown error'}`;
    }
}

