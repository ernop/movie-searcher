
// Navigate to explore view with year filter and optionally scroll to specific movie
let currentExplorePage = 1;
const EXPLORE_PER_PAGE = 15;

async function navigateToExploreWithYear(year, movieId) {
    if (!year) return;
    
    // Navigate to explore view
    navigateTo('/explore');
    
    // Wait for explore page to be active
    await new Promise(resolve => setTimeout(resolve, 100));
    
    // Set the year filter, preserving other filters
    const { filterType, letter, language } = getCurrentExploreFilters();
    await fetchExploreMovies(1, filterType, letter, null, year, language, false);
    
    // Year chip will be rendered by renderYearFilter
    
    // Wait for movies to render, then scroll to the specific movie if movieId provided
    if (movieId) {
        setTimeout(() => {
            const movieCard = document.querySelector(`.movie-card[data-movie-id="${movieId}"]`);
            if (movieCard) {
                movieCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
                // Add a highlight effect
                movieCard.style.transition = 'box-shadow 0.3s';
                movieCard.style.boxShadow = '0 0 20px rgba(74, 158, 255, 0.8)';
                setTimeout(() => {
                    movieCard.style.boxShadow = '';
                }, 2000);
            }
        }, 500);
    }
}

// Standard AJAX pattern: read UI state, build request, fetch, update UI
function getCurrentExploreFilters() {
    // The URL changes immediately; rendered controls may still belong to an older response.
    const params = getRouteParams();
    return {
        includeTv: params.include_tv === 'true', filterType: params.filter_type || 'all', language: params.language || 'all',
        letter: params.letter || null, decade: params.decade ? Number(params.decade) : null,
        year: params.year ? Number(params.year) : null, noYear: params.no_year === 'true'
    };
}

let lastFetchedUrl = '';
let pendingExploreUrl = '';
let exploreAbortController = null;
let exploreRequestId = 0;
let exploreLanguageCountsReady = false;

// Update URL to reflect current explore state (for shareable links)
function updateExploreUrl(page, filterType, letter, decade, year, language, noYear, includeTv) {
    // Explicitly set all explore params (null clears them from URL)
    const urlParams = {
        filter_type: (filterType && filterType !== 'all') ? filterType : null,
        language: (language && language !== 'all') ? language : null,
        letter: letter || null,
        decade: decade || null,
        year: year || null,
        no_year: noYear ? 'true' : null,
        include_tv: includeTv ? 'true' : null,
        page: (page && page > 1) ? page : null
    };
    
    updateRouteParams(urlParams);
}

async function fetchExploreMovies(page, filterType, letter, decade, year, language = null, noYear = false, includeTv = getCurrentExploreFilters().includeTv) {
    let requestId;
    try {
        // Use passed language or fall back to UI state
        const effectiveLanguage = language !== null ? language : getCurrentExploreFilters().language;
        
        const params = new URLSearchParams({
            page: page.toString(),
            per_page: EXPLORE_PER_PAGE.toString(),
            filter_type: filterType,
            include_tv: String(Boolean(includeTv))
        });
        
        if (letter) {
            params.append('letter', letter);
        }
        
        if (decade !== null && decade !== undefined) {
            params.append('decade', decade.toString());
        }
        
        if (year !== null && year !== undefined) {
            params.append('year', year.toString());
        }
        
        params.append('language', effectiveLanguage || 'all');
        
        if (noYear) {
            params.append('no_year', 'true');
        }
        
        const url = `/api/explore?${params}`;
        
        // Update browser URL to match current state
        updateExploreUrl(page, filterType, letter, decade, year, effectiveLanguage, noYear, includeTv);
        const tvToggle = document.getElementById('includeTvExplore');
        if (tvToggle) tvToggle.checked = Boolean(includeTv);
        
        if (url === pendingExploreUrl) return;
        requestId = ++exploreRequestId;
        if (exploreAbortController) exploreAbortController.abort();
        pendingExploreUrl = '';

        // Optimization: If URL is same as last fetched, and grid has content, skip fetch and just restore scroll
        // This preserves scroll position perfectly when navigating back
        const movieGrid = document.getElementById('movieGrid');
        if (url === lastFetchedUrl && movieGrid && movieGrid.children.length > 0) {
            if (typeof restoreScrollPosition === 'function') {
                restoreScrollPosition();
            }
            return;
        }
        
        pendingExploreUrl = url;
        exploreAbortController = new AbortController();
        const response = await fetch(url, { signal: exploreAbortController.signal });
        if (requestId !== exploreRequestId) return;
        
        if (!response.ok) {
            let errorMessage = 'Unknown error';
            try {
                const errorData = await response.json();
                errorMessage = errorData.detail || errorMessage;
            } catch (e) {
                errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            }
            if (requestId !== exploreRequestId) return;
            showStatus('Failed to load movies: ' + errorMessage, 'error');
            return;
        }
        
        const data = await response.json();
        if (requestId !== exploreRequestId) return;
        lastFetchedUrl = url;
        
        // Update current page
        currentExplorePage = page;
        
        // Render navigation with current filter state
        if (data.language_counts) {
            exploreLanguageCountsReady = true;
            renderLanguageFilters(data.language_counts, effectiveLanguage);
        }
        renderLetterNav(data.letter_counts || {}, letter);
        renderDecadeNav(data.decade_counts || {}, decade, data.no_year_count || 0, noYear);
        renderYearFilter(data.year_counts || {}, year);
        
        // Render movie grid
        renderMovieGrid(data.movies || []);
        
        // Render pagination
        renderPagination(data.pagination, filterType, letter, decade, year);
        
        // Restore scroll position
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
        
    } catch (error) {
        if (error.name === 'AbortError' || requestId !== exploreRequestId) return;
        showStatus('Error loading movies: ' + error.message, 'error');
        console.error('Explore error:', error);
    } finally {
        if (requestId === exploreRequestId) pendingExploreUrl = '';
    }
}

// Filter change handlers - read UI state and make request
function applyExploreFilters() {
    const { filterType, letter, decade, year, language, noYear } = getCurrentExploreFilters();
    // Do not clear other filters; combine filters and reset to page 1
    fetchExploreMovies(1, filterType, letter, decade, year, language, noYear);
}

function jumpToLetter(letter) {
    const { filterType, decade, year, language, noYear } = getCurrentExploreFilters();
    fetchExploreMovies(1, filterType, letter, decade, year, language, noYear);
}

function clearLetterFilter() {
    document.querySelectorAll('.letter-btn').forEach(btn => btn.classList.remove('active'));
}

function jumpToDecade(decade) {
    const { filterType, letter, language } = getCurrentExploreFilters();
    // Replace the year selection with a decade, preserving letter and language.
    fetchExploreMovies(1, filterType, letter, decade, null, language, false);
}

function jumpToNoYear() {
    const { filterType, letter, language } = getCurrentExploreFilters();
    // Select films without a year, preserving letter and language.
    fetchExploreMovies(1, filterType, letter, null, null, language, true);
}

function clearDecadeFilter() {
    document.querySelectorAll('.decade-btn').forEach(btn => btn.classList.remove('active'));
}

function jumpToYear(year) {
    const { filterType, letter, language } = getCurrentExploreFilters();
    // Select a year, preserving letter and language.
    fetchExploreMovies(1, filterType, letter, null, year, language, false);
}

function navigateToAdjacentYear(year, direction) {
    if (!year || year === null) return;
    jumpToYear(year);
}

function clearYearFilterUI() {
    const yearChipContainer = document.getElementById('yearChipContainer');
    const yearInput = document.getElementById('yearInput');
    if (yearChipContainer) {
        yearChipContainer.innerHTML = '';
    }
    if (yearInput) {
        yearInput.value = '';
    }
}

function clearYearFilter() {
    // Preserve all other filters when clearing year
    const { filterType, letter, decade, language, noYear } = getCurrentExploreFilters();
    fetchExploreMovies(1, filterType, letter, decade, null, language, noYear);
}

function clearAllZoneFilters() {
    const { filterType, language } = getCurrentExploreFilters();
    fetchExploreMovies(1, filterType, null, null, null, language, false);
}

function goToExplorePage(page) {
    const { filterType, letter, decade, year, language, noYear } = getCurrentExploreFilters();
    fetchExploreMovies(page, filterType, letter, decade, year, language, noYear);
}

// Initial load - reads URL params and fetches movies
function loadExploreMovies() {
    const urlParams = getRouteParams();
    
    // Read all filter values from URL
    const filterType = urlParams.filter_type || 'all';
    const language = urlParams.language || 'all';
    const letter = urlParams.letter || null;
    const decade = urlParams.decade ? parseInt(urlParams.decade) : null;
    const year = urlParams.year ? parseInt(urlParams.year) : null;
    const noYear = urlParams.no_year === 'true';
    const page = urlParams.page ? parseInt(urlParams.page) : 1;
    
    // Set watch filter button state
    if (filterType) {
        const watchBtn = document.querySelector(`.explore-filters .btn[data-filter="${filterType}"]`);
        if (watchBtn) {
            const group = watchBtn.closest('.btn-group-toggle');
            group.querySelectorAll('.btn').forEach(btn => btn.classList.remove('active'));
            watchBtn.classList.add('active');
        }
    }
    
    const languageGroup = document.getElementById('exploreLanguageFilterGroup');
    if (languageGroup) {
        languageGroup.querySelectorAll('.btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.language === language);
        });
    }

    // Fetch with URL params - UI state will be set by render functions
    currentExplorePage = page;
    fetchExploreMovies(page, filterType, letter, decade, year, language, noYear);
}

function renderLetterNav(letterCounts, activeLetter) {
    const letterNav = document.getElementById('letterNav');
    const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
    
    let html = '';
    
    // Add "All" button
    const allCount = Object.values(letterCounts).reduce((sum, count) => sum + count, 0);
    html += `<button class="letter-btn ${!activeLetter ? 'active' : ''}" data-action="clear">All (${allCount})</button>`;
    
    // Add letter buttons
    for (const letter of letters) {
        const count = letterCounts[letter] || 0;
        const isActive = activeLetter === letter;
        html += `<button class="letter-btn ${isActive ? 'active' : ''}" ${count === 0 ? 'disabled' : ''} data-letter="${letter}">${letter} (${count})</button>`;
    }
    
    // Add "#" for non-alphabetic
    const hashCount = letterCounts['#'] || 0;
    const isHashActive = activeLetter === '#';
    html += `<button class="letter-btn ${isHashActive ? 'active' : ''}" ${hashCount === 0 ? 'disabled' : ''} data-letter="#"># (${hashCount})</button>`;
    
    letterNav.innerHTML = html;
    
    // Attach event listeners to all letter buttons
    letterNav.querySelectorAll('.letter-btn').forEach(btn => {
        if (btn.disabled) {
            return;
        }
        
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            if (btn.dataset.action === 'clear') {
                // Only clear letter filter, preserve decade, year, and noYear
                const { filterType, decade, year, language, noYear } = getCurrentExploreFilters();
                fetchExploreMovies(1, filterType, null, decade, year, language, noYear);
            } else if (btn.dataset.letter) {
                jumpToLetter(btn.dataset.letter);
            }
        });
    });
}

function renderDecadeNav(decadeCounts, activeDecade, noYearCount, activeNoYear) {
    const decadeNav = document.getElementById('decadeNav');
    if (!decadeNav) return;
    
    // Get all decades that have movies, sorted
    const decades = Object.keys(decadeCounts).map(d => parseInt(d)).filter(d => d >= 1900 && d <= 2030).sort((a, b) => b - a);
    
    let html = '';
    
    // Add "All" button (only active if no decade and no "no year" is selected)
    const allCount = Object.values(decadeCounts).reduce((sum, count) => sum + count, 0) + noYearCount;
    const { year } = getCurrentExploreFilters();
    const allActive = !activeDecade && !activeNoYear && !year;
    html += `<button class="decade-btn ${allActive ? 'active' : ''}" data-action="clear">All (${allCount})</button>`;
    
    // Add "No year" button
    html += `<button class="decade-btn ${activeNoYear ? 'active' : ''}" ${noYearCount === 0 ? 'disabled' : ''} data-action="no_year">No year (${noYearCount})</button>`;
    
    // Add decade buttons
    for (const decade of decades) {
        const count = decadeCounts[decade] || 0;
        const isActive = activeDecade === decade;
        const decadeLabel = `${decade}s`;
        html += `<button class="decade-btn ${isActive ? 'active' : ''}" ${count === 0 ? 'disabled' : ''} data-decade="${decade}">${decadeLabel} (${count})</button>`;
    }
    
    decadeNav.innerHTML = html;
    
    // Attach event listeners
    decadeNav.querySelectorAll('.decade-btn').forEach(btn => {
        if (btn.disabled) {
            return;
        }
        
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            if (btn.dataset.action === 'clear') {
                // Only clear decade/noYear filters, preserve letter and year
                const { filterType, letter, year, language } = getCurrentExploreFilters();
                fetchExploreMovies(1, filterType, letter, null, year, language, false);
            } else if (btn.dataset.action === 'no_year') {
                jumpToNoYear();
            } else if (btn.dataset.decade) {
                jumpToDecade(parseInt(btn.dataset.decade));
            }
        });
    });
}

// Store available years for autocomplete
let availableYears = []
// Store year counts for autocomplete
let currentYearCounts = {};

function renderYearFilter(yearCounts, activeYear) {
    const yearInput = document.getElementById('yearInput');
    const yearChipContainer = document.getElementById('yearChipContainer');
    if (!yearInput || !yearChipContainer) return;
    
    // Get all years that have movies, sorted descending
    availableYears = Object.keys(yearCounts).map(y => parseInt(y)).filter(y => y >= 1900 && y <= 2035).sort((a, b) => b - a);
    
    // Render chip if year is active
    if (activeYear) {
        const count = yearCounts[activeYear] || 0;
        
        // Find adjacent years in available years (sorted descending)
        const currentIndex = availableYears.indexOf(activeYear);
        const laterYear = currentIndex > 0 ? availableYears[currentIndex - 1] : null; // Later year (left button, +1, smaller index = larger year)
        const earlierYear = currentIndex < availableYears.length - 1 ? availableYears[currentIndex + 1] : null; // Earlier year (right button, -1, larger index = smaller year)
        
        yearChipContainer.innerHTML = `
            <button class="year-nav-btn" ${!earlierYear ? 'disabled' : ''} onclick="navigateToAdjacentYear(${earlierYear || 'null'}, 'earlier')" title="Earlier year (${earlierYear || 'N/A'})">◀</button>
            <div class="year-chip" data-year="${activeYear}" onclick="clearYearFilter()" title="Click to clear year filter" style="cursor: pointer;">
                <span>${activeYear}</span>
                <span class="year-chip-close" onclick="event.stopPropagation(); clearYearFilter()" title="Clear year filter">×</span>
            </div>
            <button class="year-nav-btn" ${!laterYear ? 'disabled' : ''} onclick="navigateToAdjacentYear(${laterYear || 'null'}, 'later')" title="Later year (${laterYear || 'N/A'})">▶</button>
        `;
        yearInput.value = '';
    } else {
        yearChipContainer.innerHTML = '';
    }
    
    // Setup autocomplete if not already set up, or update year counts
    if (!yearInput.dataset.autocompleteSetup) {
        setupYearAutocomplete(yearInput, yearCounts);
        yearInput.dataset.autocompleteSetup = 'true';
    } else {
        // Update year counts for existing autocomplete
        currentYearCounts = yearCounts;
    }
}

function setupYearAutocomplete(input, yearCounts) {
    const autocomplete = document.getElementById('yearAutocomplete');
    let selectedIndex = -1;
    
    // Update stored year counts
    currentYearCounts = yearCounts;
    
    input.addEventListener('input', (e) => {
        const query = e.target.value.trim();
        
        if (!query) {
            autocomplete.style.display = 'none';
            return;
        }
        
        // Filter years that match the query
        const queryNum = parseInt(query);
        const matches = availableYears.filter(year => {
            if (!isNaN(queryNum)) {
                return year.toString().startsWith(query);
            }
            return false;
        }).slice(0, 10); // Limit to 10 results
        
        if (matches.length === 0) {
            autocomplete.style.display = 'none';
            return;
        }
        
        // Render autocomplete items
        autocomplete.innerHTML = matches.map((year, index) => {
            const count = currentYearCounts[year] || 0;
            return `<div class="year-autocomplete-item" data-year="${year}" data-index="${index}">${year} (${count})</div>`;
        }).join('');
        
        autocomplete.style.display = 'block';
        selectedIndex = -1;
    });
    
    input.addEventListener('keydown', (e) => {
        const items = autocomplete.querySelectorAll('.year-autocomplete-item');
        
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
            updateAutocompleteSelection(items, selectedIndex);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            selectedIndex = Math.max(selectedIndex - 1, -1);
            updateAutocompleteSelection(items, selectedIndex);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (selectedIndex >= 0 && items[selectedIndex]) {
                const year = parseInt(items[selectedIndex].dataset.year);
                selectYear(year);
            } else if (items.length === 1) {
                const year = parseInt(items[0].dataset.year);
                selectYear(year);
            } else {
                // Try to parse input as year
                const year = parseInt(input.value.trim());
                if (!isNaN(year) && availableYears.includes(year)) {
                    selectYear(year);
                }
            }
        } else if (e.key === 'Escape') {
            autocomplete.style.display = 'none';
            input.blur();
        }
    });
    
    // Click on autocomplete item
    autocomplete.addEventListener('click', (e) => {
        const item = e.target.closest('.year-autocomplete-item');
        if (item) {
            const year = parseInt(item.dataset.year);
            selectYear(year);
        }
    });
    
    // Close autocomplete when clicking outside
    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !autocomplete.contains(e.target)) {
            autocomplete.style.display = 'none';
        }
    });
}

function updateAutocompleteSelection(items, index) {
    items.forEach((item, i) => {
        if (i === index) {
            item.classList.add('active');
            item.scrollIntoView({ block: 'nearest' });
        } else {
            item.classList.remove('active');
        }
    });
}

function selectYear(year) {
    const yearInput = document.getElementById('yearInput');
    const autocomplete = document.getElementById('yearAutocomplete');
    
    if (yearInput) {
        yearInput.value = '';
    }
    if (autocomplete) {
        autocomplete.style.display = 'none';
    }
    
    jumpToYear(year);
}

function renderMovieGrid(movies) {
    const movieGrid = document.getElementById('movieGrid');
    
    if (!movieGrid) {
        console.error('movieGrid element not found');
        return;
    }
    
    // Always clear the grid first
    movieGrid.innerHTML = '';
    
    if (!movies || movies.length === 0) {
        console.log('No movies to render, showing empty state');
        movieGrid.innerHTML = '<div class="empty-state">No movies found</div>';
        return;
    }
    
    
    movieGrid.innerHTML = movies.map(movie => createMovieCard(movie)).join('');
    initAllStarRatings();
}


function renderPagination(pagination, filterType, letter, decade, year) {
    const paginationEl = document.getElementById('explorePagination');
    
    if (pagination.pages <= 1) {
        paginationEl.innerHTML = '';
        return;
    }
    
    let html = '';
    
    // Previous button
    const prevPage = pagination.page - 1;
    html += `<button class="pagination-btn" ${pagination.page === 1 ? 'disabled' : ''} onclick="goToExplorePage(${prevPage})">Previous</button>`;
    
    // Page numbers
    const maxPages = 10;
    let startPage = Math.max(1, pagination.page - Math.floor(maxPages / 2));
    let endPage = Math.min(pagination.pages, startPage + maxPages - 1);
    
    if (endPage - startPage < maxPages - 1) {
        startPage = Math.max(1, endPage - maxPages + 1);
    }
    
    if (startPage > 1) {
        html += `<button class="pagination-btn" onclick="goToExplorePage(1)">1</button>`;
        if (startPage > 2) {
            html += `<span class="pagination-info">...</span>`;
        }
    }
    
    for (let i = startPage; i <= endPage; i++) {
        const isActive = i === pagination.page;
        html += `<button class="pagination-btn ${isActive ? 'active' : ''}" onclick="goToExplorePage(${i})">${i}</button>`;
    }
    
    if (endPage < pagination.pages) {
        if (endPage < pagination.pages - 1) {
            html += `<span class="pagination-info">...</span>`;
        }
        html += `<button class="pagination-btn" onclick="goToExplorePage(${pagination.pages})">${pagination.pages}</button>`;
    }
    
    // Next button
    const nextPage = pagination.page + 1;
    html += `<button class="pagination-btn" ${pagination.page === pagination.pages ? 'disabled' : ''} onclick="goToExplorePage(${nextPage})">Next</button>`;
    
    // Page info
    html += `<span class="pagination-info">Page ${pagination.page} of ${pagination.pages} (${pagination.total} total)</span>`;
    
    paginationEl.innerHTML = html;
}


function setExploreTvFilter(includeTv) {
    const f = getCurrentExploreFilters();
    fetchExploreMovies(1, f.filterType, f.letter, f.decade, f.year, f.language, f.noYear, includeTv);
}
