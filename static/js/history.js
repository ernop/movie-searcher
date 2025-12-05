// History functions

function formatPlaybackTime(seconds) {
    if (!seconds || seconds <= 0) return null;
    
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    if (hours > 0) {
        return `${hours}h ${minutes}m ${secs}s`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

async function resumePlayback(movieId, startTime, movieName) {
    const formattedTime = formatPlaybackTime(startTime);
    console.log(`Resuming ${movieName} from ${formattedTime} (${startTime}s)`);
    
    try {
        const response = await fetch('/api/launch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                start_time: startTime,
                close_existing: true
            })
        });
        
        if (!response.ok) {
            const data = await response.json();
            alert(`Failed to resume: ${data.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Resume error:', error);
        alert(`Error resuming playback: ${error.message}`);
    }
}

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
            const stoppedAt = launch.stopped_at_seconds;
            
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
            
            // Format resume time if available
            const resumeTime = formatPlaybackTime(stoppedAt);
            const hasResumePoint = resumeTime && stoppedAt > 0; // Show for any captured position
            
            // Render movie card with timestamp and resume option
            const hasId = movie && movie.id != null && !Number.isNaN(movie.id);
            const cardHtml = createMovieCard(movie);
            
            // Build resume button HTML if we have a resume point
            let resumeHtml = '';
            if (hasResumePoint && hasId) {
                const movieName = movie.name || 'this movie';
                const escapedName = escapeHtml(movieName).replace(/'/g, "\\'");
                resumeHtml = `
                    <div class="history-resume">
                        <button class="resume-button" onclick="event.stopPropagation(); resumePlayback(${movie.id}, ${stoppedAt}, '${escapedName}')">
                            ▶ Resume from ${resumeTime}
                        </button>
                        <span class="resume-hint">Pick up where you left off</span>
                    </div>
                `;
            }
            
            html += `
                <div class="history-item${hasResumePoint ? ' has-resume' : ''}">
                    <div class="history-movie-card"${hasId ? '' : ' style="pointer-events: none;"'}>
                        ${cardHtml}
                    </div>
                    ${resumeHtml}
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

