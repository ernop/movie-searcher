const searchInput = document.getElementById('searchInput');
const autocomplete = document.getElementById('autocomplete');
const results = document.getElementById('results');
const stats = document.getElementById('stats');
const viewWatchedBtn = document.getElementById('viewWatchedBtn');
const watchedSection = document.getElementById('watchedSection');
const watchedList = document.getElementById('watchedList');

let selectedIndex = -1;
let currentResults = [];
let searchAbortController = null;
let currentSearchRequestId = 0;

let searchDebounceTimer = null;

function scheduleSearch(query) {
    // Clear any pending debounce
    if (searchDebounceTimer) {
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = null;
    }

    // If query is empty, clear results
    if (!query || query.trim().length === 0) {
        // Abort any pending search
        if (searchAbortController) {
            searchAbortController.abort();
            searchAbortController = null;
        }
        // Increment request ID to invalidate any pending completions
        currentSearchRequestId++;
        
        if (autocomplete) autocomplete.style.display = 'none';
        if (results) results.innerHTML = '';
        currentResults = [];
        return;
    }
    
    // Debounce search requests
    searchDebounceTimer = setTimeout(() => {
        performSearch(query);
    }, 300);
}

async function performSearch(query, showResultsImmediately = false) {
    // Increment request ID for this new search
    const requestId = ++currentSearchRequestId;
    
    // Abort previous request
    if (searchAbortController) {
        searchAbortController.abort();
    }
    searchAbortController = new AbortController();
    
    try {
        const watchFilter = getWatchFilter();
        const params = new URLSearchParams({
            q: query,
            filter_type: watchFilter,
            language: 'all',
            offset: '0',
            limit: '50'
        });
        
        const response = await fetch(`/api/search?${params}`, {
            signal: searchAbortController.signal
        });
        
        // Note: We don't null out searchAbortController here because
        // we want to keep it active until the next request replaces it,
        // or if we want to support manual cancellation later.
        // But mainly, the requestId check below is the ultimate guard.
        
        if (!response.ok) {
            if (requestId !== currentSearchRequestId) return;
            showStatus('Search failed. Please try again.', 'error');
            console.error('Search failed:', response.status);
            return;
        }
        
        const data = await response.json();
        
        // CRITICAL: Check if this is still the latest request
        if (requestId !== currentSearchRequestId) {
            return;
        }
        
        currentResults = data.results || [];
        
        if (currentResults.length === 0) {
            if (autocomplete) autocomplete.style.display = 'none';
            if (results) results.innerHTML = '<div class="empty-state">No results found</div>';
            return;
        }

        if (showResultsImmediately) {
            displayResults(currentResults);
            if (autocomplete) autocomplete.style.display = 'none';
            updateClearButtonVisibility();
            return;
        }
        
        // Show autocomplete with top 10 results
        const autocompleteItems = currentResults.slice(0, 10);
        // Only show autocomplete if input is focused
        if (autocomplete && document.activeElement === searchInput) {
            autocomplete.innerHTML = autocompleteItems.map((item, index) => `
                <div class="autocomplete-item" data-index="${index}">
                    <strong>${escapeHtml(item.name || 'Unknown')}</strong>
                    ${item.year ? `<span style="color: #999; margin-left: 8px;">(${item.year})</span>` : ''}
                </div>
            `).join('');
            autocomplete.style.display = 'block';
        }
        selectedIndex = -1;
    } catch (error) {
        if (error.name === 'AbortError') {
            return;
        }
        if (requestId !== currentSearchRequestId) return;
        showStatus('Search failed. Please try again.', 'error');
        console.error('Search error:', error);
    }
}

function updateClearButtonVisibility() {
    const clearBtn = document.querySelector('.clear-search-btn');
    if (clearBtn) {
        if (searchInput && searchInput.value.trim().length > 0) {
            clearBtn.style.display = 'block';
        } else {
            clearBtn.style.display = 'none';
        }
    }
}

function clearSearch() {
    scheduleSearch('');
    selectedIndex = -1;
    if (searchInput) {
        searchInput.value = '';
        updateClearButtonVisibility();
    }
    if (results) {
        results.innerHTML = '';
    }
    if (autocomplete) {
        autocomplete.style.display = 'none';
    }
}

async function displayResults(items) {
    if (items.length === 0) {
        results.innerHTML = '<div class="empty-state">No results found</div>';
        return;
    }
    results.innerHTML = '<div class="movie-grid">' + items.map(item => createMovieCard(item)).join('') + '</div>';
    initAllStarRatings();
}

function getWatchFilter() {
    // Get watched filter from the first button group (not language filter)
    const watchedGroup = document.querySelector('.filter-options .btn-group-toggle:not(.language-filter-group)');
    if (watchedGroup) {
        const activeBtn = watchedGroup.querySelector('.btn.active');
        if (activeBtn) {
            return activeBtn.getAttribute('data-filter') || 'all';
        }
    }
    return 'all';
}

// Language filter now on Explore only
function getExploreLanguageFilter() {
    // Get language filter from language filter group
    const languageGroup = document.querySelector('.language-filter-group');
    if (languageGroup) {
        const activeBtn = languageGroup.querySelector('.btn.active');
        if (activeBtn) {
            return activeBtn.getAttribute('data-language') || 'all';
        }
    }
    return 'all';
}

function setWatchFilter(filterValue, clickedBtn) {
    // Remove active class from all watched filter buttons (not language)
    const watchedGroup = document.querySelector('.filter-options .btn-group-toggle:not(.language-filter-group)');
    if (watchedGroup) {
        watchedGroup.querySelectorAll('.btn').forEach(btn => btn.classList.remove('active'));
    }
    
    // Add active class to clicked button
    clickedBtn.classList.add('active');
    
    // Apply filters if there's a search query
    applyFilters();
}

function applyFilters() {
    // Re-run search with current query and updated filters
    const query = searchInput ? searchInput.value.trim() : '';
    if (query) {
        performSearch(query, true);
    }
}

function setExploreLanguageFilter(languageValue, clickedBtn) {
    const languageGroup = document.getElementById('exploreLanguageFilterGroup');
    if (languageGroup) {
        languageGroup.querySelectorAll('.btn').forEach(btn => btn.classList.remove('active'));
    }
    clickedBtn.classList.add('active');
    
    const { filterType, letter, decade, year, noYear } = getCurrentExploreFilters();
    fetchExploreMovies(1, filterType, letter, decade, year, languageValue, noYear);
}

async function loadLanguageFilters() {
    try {
        const response = await fetch('/api/language-counts');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!exploreLanguageCountsReady) {
            renderLanguageFilters(data.counts || {}, getCurrentExploreFilters().language);
        }
    } catch (error) {
        console.error('Error loading language filters:', error);
    }
}

function renderLanguageFilters(counts, activeLanguage = 'all') {
    const group = document.getElementById('exploreLanguageFilterGroup');
    if (!group) return;
    const names = {
        all: 'All', en: 'English', es: 'Spanish', fr: 'French', de: 'German',
        it: 'Italian', pt: 'Portuguese', ru: 'Russian', ja: 'Japanese', ko: 'Korean',
        zh: 'Chinese', hi: 'Hindi', sv: 'Swedish', da: 'Danish', ar: 'Arabic',
        pl: 'Polish', is: 'Icelandic', cs: 'Czech', fi: 'Finnish', no: 'Norwegian',
        nl: 'Dutch', uk: 'Ukrainian', new: 'Newari', unknown: 'Unknown', zxx: 'No language'
    };
    const codes = Object.keys(counts).sort((a, b) => {
        if (a === 'all') return -1;
        if (b === 'all') return 1;
        return counts[b] - counts[a] || (names[a] || a).localeCompare(names[b] || b);
    });
    group.innerHTML = '<span class="language-label">Audio language:</span>';
    for (const code of codes) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn' + (code === activeLanguage ? ' active' : '');
        button.dataset.language = code;
        button.textContent = `${names[code] || code} (${counts[code]})`;
        button.setAttribute('aria-pressed', String(code === activeLanguage));
        button.disabled = counts[code] === 0 && code !== activeLanguage && code !== 'all';
        button.onclick = () => setExploreLanguageFilter(code, button);
        group.appendChild(button);
    }
}

function setExploreWatchFilter(filterValue, clickedBtn) {
    // Remove active class from all watch filter buttons in the same group
    const group = clickedBtn.closest('.btn-group-toggle');
    group.querySelectorAll('.btn').forEach(btn => btn.classList.remove('active'));
    
    // Add active class to clicked button
    clickedBtn.classList.add('active');
    
    const { letter, decade, year, language, noYear } = getCurrentExploreFilters();
    fetchExploreMovies(1, filterValue, letter, decade, year, language, noYear);
}

let progressInterval = null;
		let progressPollInFlight = false;
		let progressAbortController = null;

// showStatus is now defined globally in utils.js as a toast notification

function formatTime(seconds) {
    if (!seconds) return 'Unknown';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m ${s}s`;
}

function formatMinutes(seconds) {
    if (!seconds && seconds !== 0) return '';
    const minutes = Math.round(Number(seconds) / 60);
    if (!isFinite(minutes) || minutes <= 0) return '';
    return `${minutes} min`;
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    return date.toLocaleDateString();
}

// formatSize is defined in utils.js (loaded first)

async function scanFolder() {
    const btn = document.getElementById('setupScanBtn') || document.getElementById('scanBtn');
    const progressContainer = document.getElementById('setupProgressContainer') || document.getElementById('progressContainer');
    const progressBar = document.getElementById('setupProgressBar') || document.getElementById('progressBar');
    const progressStatus = document.getElementById('setupProgressStatus') || document.getElementById('progressStatus');
    const progressCount = document.getElementById('setupProgressCount') || document.getElementById('progressCount');
    const progressFile = document.getElementById('setupProgressFile') || document.getElementById('progressFile');
    
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Scanning...';
    }
    if (progressContainer) {
        progressContainer.classList.add('active');
    }
    showStatus('Starting scan...', 'info');
    
    try {
        const response = await fetch('/api/index', { method: 'POST' });
        const data = await response.json();
        
        if (response.ok) {
            // Start polling for progress
            startProgressPolling(progressContainer, progressBar, progressStatus, progressCount, progressFile, btn);
        } else {
            showStatus('Scan failed: ' + (data.detail || 'Unknown error'), 'error');
            if (progressContainer) progressContainer.classList.remove('active');
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Scan Movies Folder';
            }
        }
    } catch (error) {
        showStatus('Scan failed: ' + error.message, 'error');
        if (progressContainer) progressContainer.classList.remove('active');
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Scan Movies Folder';
        }
    }
}

let lastLogCount = 0;

function renderLogs(logWindow, logs) {
    if (!logWindow || !logs) return;
    
    // Only append new logs
    if (logs.length > lastLogCount) {
        const newLogs = logs.slice(lastLogCount);
        newLogs.forEach(log => {
            const logEntry = document.createElement('div');
            logEntry.className = `log-entry ${log.level}`;
            
            const timestamp = document.createElement('span');
            timestamp.className = 'log-timestamp';
            timestamp.textContent = log.timestamp;
            
            const level = document.createElement('span');
            level.className = 'log-level';
            level.textContent = `[${log.level.toUpperCase()}]`;
            
            const message = document.createElement('span');
            message.textContent = log.message;
            
            logEntry.appendChild(timestamp);
            logEntry.appendChild(level);
            logEntry.appendChild(message);
            logWindow.appendChild(logEntry);
        });
        
        // Auto-scroll to bottom
        logWindow.scrollTop = logWindow.scrollHeight;
        lastLogCount = logs.length;
    }
}

function startProgressPolling(progressContainer, progressBar, progressStatus, progressCount, progressFile, scanBtn) {
    if (progressInterval) {
        clearInterval(progressInterval);
				progressInterval = null;
			}
			// Abort any in-flight poll from a previous interval
			if (progressAbortController) {
				try { progressAbortController.abort(); } catch (e) {}
				progressAbortController = null;
				progressPollInFlight = false;
    }
    
    // Reset log counter
    lastLogCount = 0;
    
    // Find log window
    const logWindow = document.getElementById('setupScanLogWindow') || document.getElementById('scanLogWindow');
    if (logWindow) {
        logWindow.innerHTML = '';
        logWindow.style.display = 'block';
    }
    
    progressInterval = setInterval(async () => {
				// Prevent overlapping polls if the previous request hasn't finished
				if (progressPollInFlight) {
					return;
				}
        try {
					// Create a new controller per tick so we can cancel on stop
					progressAbortController = new AbortController();
					progressPollInFlight = true;
					const response = await fetch('/api/scan-progress', { signal: progressAbortController.signal });
            const data = await response.json();
            
            if (data.is_scanning) {
                const percent = Math.round(data.progress_percent);
                if (progressBar) {
                    progressBar.style.width = percent + '%';
                    progressBar.textContent = percent + '%';
                }
                
                // Show file count and add/update/remove counts
                if (progressCount) {
                    let countText = `${data.current} / ${data.total}`;
                    const statsParts = [];
                    if (data.movies_added > 0) statsParts.push(`+${data.movies_added}`);
                    if (data.movies_updated > 0) statsParts.push(`~${data.movies_updated}`);
                    if (data.movies_removed > 0) statsParts.push(`-${data.movies_removed}`);
                    if (statsParts.length > 0) {
                        countText += ` (${statsParts.join(', ')})`;
                    }
                    progressCount.textContent = countText;
                }
                
                if (progressFile) progressFile.textContent = data.current_file ? `Scanning: ${data.current_file}` : '';
                
                if (progressStatus) {
                    if (data.status === 'counting') {
                        progressStatus.textContent = 'Counting files...';
                    } else if (data.status === 'scanning') {
                        progressStatus.textContent = 'Scanning movies...';
                    } else {
                        progressStatus.textContent = data.status;
                    }
                }
                
                // Render logs
                if (data.logs && logWindow) {
                    renderLogs(logWindow, data.logs);
                }
            } else {
                // Scan complete
                clearInterval(progressInterval);
                progressInterval = null;
						// Abort any pending fetch
						if (progressAbortController) {
							try { progressAbortController.abort(); } catch (e) {}
							progressAbortController = null;
						}
						progressPollInFlight = false;
                // Keep progress container visible to show scan logs
                // Don't remove 'active' class so log window remains visible
                if (scanBtn) {
                    scanBtn.disabled = false;
                    scanBtn.textContent = 'Scan Movies Folder';
                }
                
                // Final log render
                if (data.logs && logWindow) {
                    renderLogs(logWindow, data.logs);
                }
                
                // Update progress status to show completion
                if (progressStatus) {
                    if (data.status === 'complete') {
                        // Build completion message with stats
                        const statsParts = [];
                        if (data.movies_added > 0) statsParts.push(`${data.movies_added} added`);
                        if (data.movies_updated > 0) statsParts.push(`${data.movies_updated} updated`);
                        if (data.movies_removed > 0) statsParts.push(`${data.movies_removed} removed`);
                        
                        let statusText = `Scan complete: ${data.current} movies`;
                        if (statsParts.length > 0) {
                            statusText += ` (${statsParts.join(', ')})`;
                        }
                        progressStatus.textContent = statusText;
                        
                        if (progressBar) {
                            progressBar.style.width = '100%';
                            progressBar.textContent = '100%';
                        }
                        if (progressFile) progressFile.textContent = '';
                    } else if (data.status.startsWith('error')) {
                        progressStatus.textContent = 'Scan error: ' + data.status;
                    }
                }
                
                if (data.status === 'complete') {
                    // Build completion message with stats for toast
                    const statsParts = [];
                    if (data.movies_added > 0) statsParts.push(`${data.movies_added} added`);
                    if (data.movies_updated > 0) statsParts.push(`${data.movies_updated} updated`);
                    if (data.movies_removed > 0) statsParts.push(`${data.movies_removed} removed`);
                    
                    let toastMsg = `Scan complete: ${data.current} movies`;
                    if (statsParts.length > 0) {
                        toastMsg += ` (${statsParts.join(', ')})`;
                    }
                    showStatus(toastMsg, 'success');
                    loadStats();
                    // Reload setup stats if on setup page
                    const route = getRoute();
                    if (route === '/setup') {
                        loadSetupPage();
                    }
                } else if (data.status.startsWith('error')) {
                    showStatus('Scan error: ' + data.status, 'error');
                }
            }
				} catch (error) {
					// Ignore AbortError noise when we intentionally cancel
            console.error('Progress polling error:', error);
				} finally {
					progressPollInFlight = false;
					// Do not reuse old controllers
					progressAbortController = null;
        }
    }, 500); // Poll every 500ms
}

// Search Input Event Listeners

searchInput.addEventListener('input', (e) => {
    const route = getRoute();
    updateClearButtonVisibility();
    if (route === '/home' || route === '/') {
        scheduleSearch(e.target.value);
    }
});

searchInput.addEventListener('keydown', (e) => {
    const route = getRoute();
    if (route !== '/home' && route !== '/') return;
    
    const items = autocomplete.querySelectorAll('.autocomplete-item');
    
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
        updateSelection(items);
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectedIndex = Math.max(selectedIndex - 1, -1);
        updateSelection(items);
    } else if (e.key === 'Enter') {
        e.preventDefault();
        if (selectedIndex >= 0 && items[selectedIndex]) {
            selectSearchSuggestion(selectedIndex);
        } else {
            // Force a search with the current input and show results
            if (searchDebounceTimer) {
                clearTimeout(searchDebounceTimer);
                searchDebounceTimer = null;
            }
            performSearch(searchInput.value, true);
        }
    } else if (e.key === 'Escape') {
        autocomplete.style.display = 'none';
    }
});

function updateSelection(items) {
    items.forEach((item, index) => {
        if (index === selectedIndex) {
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });
}

function selectSearchSuggestion(index) {
    const movie = currentResults[index];
    if (!movie) return;
    if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
    if (searchAbortController) searchAbortController.abort();
    currentSearchRequestId++;
    searchInput.value = movie.name;
    autocomplete.style.display = 'none';
    updateClearButtonVisibility();
    openMovieHash(movie.id, getMovieSlug(movie));
}

autocomplete.addEventListener('click', (e) => {
    const item = e.target.closest('.autocomplete-item');
    if (item) {
        const index = parseInt(item.dataset.index);
        selectSearchSuggestion(index);
    }
});

// Hide autocomplete when clicking outside
document.addEventListener('click', (e) => {
    if (autocomplete && autocomplete.style.display === 'block') {
        if (!searchInput.contains(e.target) && !autocomplete.contains(e.target)) {
            autocomplete.style.display = 'none';
        }
    }
});

// Hide autocomplete when tabbing out (blur)
searchInput.addEventListener('blur', () => {
    // Small delay to allow click events on autocomplete items to register
    setTimeout(() => {
        if (autocomplete) {
            autocomplete.style.display = 'none';
        }
    }, 200);
});
