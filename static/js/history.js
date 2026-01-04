// History functions

let currentHistoryPage = 1;
const HISTORY_PER_PAGE = 20;

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

async function loadHistory(page = null) {
    const historyList = document.getElementById('historyList');
    const historyPagination = document.getElementById('historyPagination');
    if (!historyList) return;
    
    // Read params from URL if not provided
    const urlParams = getRouteParams();
    if (page === null) {
        page = urlParams.page ? parseInt(urlParams.page) : 1;
    }
    currentHistoryPage = page;
    
    const search = urlParams.search || '';
    const dateFilter = urlParams.date_filter || 'all';
    
    // Update UI to reflect current filters
    updateHistoryFiltersUI(search, dateFilter);
    
    historyList.innerHTML = '<div class="loading">Loading history...</div>';
    
    try {
        // Build query string
        let queryParams = `page=${page}&per_page=${HISTORY_PER_PAGE}`;
        if (search && search.trim()) {
            queryParams += `&search=${encodeURIComponent(search.trim())}`;
        }
        if (dateFilter && dateFilter !== 'all') {
            queryParams += `&date_filter=${encodeURIComponent(dateFilter)}`;
        }
        
        const response = await fetch(`/api/launch-history?${queryParams}`);
        const data = await response.json();
        
        if (!response.ok) {
            historyList.innerHTML = '<div class="empty-state">Error loading history: ' + (data.detail || 'Unknown error') + '</div>';
            return;
        }
        
        const launches = data.launches || [];
        const pagination = data.pagination || { page: 1, pages: 1, total: 0 };
        
        // Update URL with all params
        updateHistoryUrl(page, search, dateFilter);
        
        if (launches.length === 0) {
            const emptyMessage = search || dateFilter !== 'all' 
                ? 'No launch history found matching your filters' 
                : 'No launch history yet';
            historyList.innerHTML = `<div class="empty-state">${emptyMessage}</div>`;
            if (historyPagination) historyPagination.innerHTML = '';
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
        
        // Render pagination
        if (historyPagination) {
            renderHistoryPagination(pagination, historyPagination);
        }

        // Restore scroll position if available
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
    } catch (error) {
        historyList.innerHTML = '<div class="empty-state">Error loading history: ' + error.message + '</div>';
        console.error('History error:', error);
    }
}

// Update URL to reflect current history filters
function updateHistoryUrl(page, search, dateFilter) {
    const urlParams = {};
    if (page > 1) urlParams.page = page;
    if (search && search.trim()) urlParams.search = search.trim();
    if (dateFilter && dateFilter !== 'all') urlParams.date_filter = dateFilter;
    updateRouteParams(urlParams);
}

// Update filter UI to reflect current state
function updateHistoryFiltersUI(search, dateFilter) {
    const searchInput = document.getElementById('historySearchInput');
    if (searchInput) {
        searchInput.value = search || '';
    }
    
    // Update active state of date filter buttons
    const filterMap = {
        'today': 'historyFilterToday',
        'yesterday': 'historyFilterYesterday',
        'this_week': 'historyFilterThisWeek',
        'this_month': 'historyFilterThisMonth',
        'all': 'historyFilterAll'
    };
    
    Object.keys(filterMap).forEach(filter => {
        const button = document.getElementById(filterMap[filter]);
        if (button) {
            if (filter === dateFilter || (filter === 'all' && (!dateFilter || dateFilter === 'all'))) {
                button.classList.add('active');
            } else {
                button.classList.remove('active');
            }
        }
    });
}

// Handle search input
function handleHistorySearch() {
    const searchInput = document.getElementById('historySearchInput');
    const search = searchInput ? searchInput.value : '';
    const urlParams = getRouteParams();
    const dateFilter = urlParams.date_filter || 'all';
    
    // Reset to page 1 when searching
    updateHistoryUrl(1, search, dateFilter);
    loadHistory(1);
}

// Handle date filter selection
function setHistoryDateFilter(filter) {
    const urlParams = getRouteParams();
    const search = urlParams.search || '';
    
    // Reset to page 1 when changing date filter
    updateHistoryUrl(1, search, filter);
    loadHistory(1);
}

// Go to specific history page
function goToHistoryPage(page) {
    loadHistory(page);
    // Scroll to top of history list
    const historyList = document.getElementById('historyList');
    if (historyList) {
        historyList.scrollIntoView({ behavior: 'smooth' });
    }
}

// Render history pagination
function renderHistoryPagination(pagination, container) {
    if (!pagination || pagination.pages <= 1) {
        container.innerHTML = '';
        return;
    }
    
    let html = '';
    const maxPages = 10;
    
    // Previous button
    const prevPage = pagination.page - 1;
    html += `<button class="pagination-btn" ${pagination.page === 1 ? 'disabled' : ''} onclick="goToHistoryPage(${prevPage})">Previous</button>`;
    
    // Page numbers
    let startPage = Math.max(1, pagination.page - Math.floor(maxPages / 2));
    let endPage = Math.min(pagination.pages, startPage + maxPages - 1);
    
    if (endPage - startPage < maxPages - 1) {
        startPage = Math.max(1, endPage - maxPages + 1);
    }
    
    if (startPage > 1) {
        html += `<button class="pagination-btn" onclick="goToHistoryPage(1)">1</button>`;
        if (startPage > 2) {
            html += `<span class="pagination-info">...</span>`;
        }
    }
    
    for (let i = startPage; i <= endPage; i++) {
        const isActive = i === pagination.page;
        html += `<button class="pagination-btn ${isActive ? 'active' : ''}" onclick="goToHistoryPage(${i})">${i}</button>`;
    }
    
    if (endPage < pagination.pages) {
        if (endPage < pagination.pages - 1) {
            html += `<span class="pagination-info">...</span>`;
        }
        html += `<button class="pagination-btn" onclick="goToHistoryPage(${pagination.pages})">${pagination.pages}</button>`;
    }
    
    // Next button
    const nextPage = pagination.page + 1;
    html += `<button class="pagination-btn" ${pagination.page === pagination.pages ? 'disabled' : ''} onclick="goToHistoryPage(${nextPage})">Next</button>`;
    
    // Page info
    html += `<span class="pagination-info">Page ${pagination.page} of ${pagination.pages} (${pagination.total} total)</span>`;
    
    container.innerHTML = html;
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

