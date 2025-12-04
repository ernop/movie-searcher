
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
    const { filterType, letter, decade, language, noYear } = getCurrentExploreFilters();
    await fetchExploreMovies(1, filterType, letter, decade, year);
    
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
    // Read filter type from watch filter button group (first group)
    const activeBtn = document.querySelector('.explore-filters .btn-group-toggle:first-child .btn.active[data-filter]');
    const filterType = activeBtn ? activeBtn.dataset.filter || 'all' : 'all';
    
    // Read letter from active letter button
    const activeLetterBtn = document.querySelector('.letter-btn.active[data-letter]');
    const letter = activeLetterBtn ? activeLetterBtn.dataset.letter : null;
    
    // Read decade from active decade button
    const activeDecadeBtn = document.querySelector('.decade-btn.active[data-decade]');
    const decade = activeDecadeBtn ? parseInt(activeDecadeBtn.dataset.decade) : null;
    
    // Read year from active year chip
    const yearChip = document.querySelector('.year-chip[data-year]');
    const year = yearChip ? parseInt(yearChip.dataset.year) : null;
    
    // Read language from explore language filter group
    const langBtn = document.querySelector('#exploreLanguageFilterGroup .btn.active[data-language]');
    const language = langBtn ? (langBtn.getAttribute('data-language') || 'all') : 'all';
    
    // Read no year filter state from decade nav
    const noYearBtn = document.querySelector('.decade-btn[data-action="no_year"]');
    const noYear = noYearBtn && noYearBtn.classList.contains('active');
    
    return { filterType, letter, decade, year, language, noYear };
}

let lastFetchedUrl = '';

async function fetchExploreMovies(page, filterType, letter, decade, year) {
    try {
        const params = new URLSearchParams({
            page: page.toString(),
            per_page: EXPLORE_PER_PAGE.toString(),
            filter_type: filterType
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
        
        // Always include language from current explore state (even 'all' for clarity)
        const { language, noYear } = getCurrentExploreFilters();
        params.append('language', language || 'all');
        
        if (noYear) {
            params.append('no_year', 'true');
        }
        
        const url = `/api/explore?${params}`;
        
        // Optimization: If URL is same as last fetched, and grid has content, skip fetch and just restore scroll
        // This preserves scroll position perfectly when navigating back
        const movieGrid = document.getElementById('movieGrid');
        if (url === lastFetchedUrl && movieGrid && movieGrid.children.length > 0) {
            if (typeof restoreScrollPosition === 'function') {
                restoreScrollPosition();
            }
            return;
        }
        
        lastFetchedUrl = url;
        
        const response = await fetch(url);
        
        if (!response.ok) {
            let errorMessage = 'Unknown error';
            try {
                const errorData = await response.json();
                errorMessage = errorData.detail || errorMessage;
            } catch (e) {
                errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            }
            showStatus('Failed to load movies: ' + errorMessage, 'error');
            return;
        }
        
        const data = await response.json();
        
        // Update current page
        currentExplorePage = page;
        
        // Render navigation with current filter state
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
        showStatus('Error loading movies: ' + error.message, 'error');
        console.error('Explore error:', error);
    }
}

// Filter change handlers - read UI state and make request
function applyExploreFilters() {
    const { filterType, letter, decade, year } = getCurrentExploreFilters();
    // Do not clear other filters; combine filters and reset to page 1
    fetchExploreMovies(1, filterType, letter, decade, year);
}

function jumpToLetter(letter) {
    const { filterType, decade, year, language, noYear } = getCurrentExploreFilters();
    fetchExploreMovies(1, filterType, letter, decade, year);
}

function clearLetterFilter() {
    document.querySelectorAll('.letter-btn').forEach(btn => btn.classList.remove('active'));
}

function jumpToDecade(decade) {
    const { filterType, letter, language } = getCurrentExploreFilters();
    // Clear year and no_year UI (mutually exclusive with decade), but preserve letter and language
    clearYearFilterUI();
    clearDecadeFilter();
    fetchExploreMovies(1, filterType, letter, decade, null);
}

function jumpToNoYear() {
    const { filterType, letter, language } = getCurrentExploreFilters();
    // Clear year and decade UI (mutually exclusive with no_year), but preserve letter and language
    clearYearFilterUI();
    clearDecadeFilter();
    fetchExploreMovies(1, filterType, letter, null, null);
}

function clearDecadeFilter() {
    document.querySelectorAll('.decade-btn').forEach(btn => btn.classList.remove('active'));
}

function jumpToYear(year) {
    const { filterType, letter, language } = getCurrentExploreFilters();
    // Clear decade and no_year UI (mutually exclusive with year), but preserve letter and language
    clearDecadeFilter();
    fetchExploreMovies(1, filterType, letter, null, year);
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
    clearYearFilterUI();
    // Preserve all other filters when clearing year
    const { filterType, letter, decade, language, noYear } = getCurrentExploreFilters();
    fetchExploreMovies(1, filterType, letter, decade, null);
}

function clearAllZoneFilters() {
    clearLetterFilter();
    clearDecadeFilter();
    clearYearFilter();
}

function goToExplorePage(page) {
    const { filterType, letter, decade, year } = getCurrentExploreFilters();
    fetchExploreMovies(page, filterType, letter, decade, year);
}

// Initial load - reads UI state
function loadExploreMovies() {
    // Initialize filter buttons from URL params if present
    const urlParams = getRouteParams();
    let filtersInitialized = false;
    
    // Set watch filter from URL
    if (urlParams.filter_type) {
        const watchBtn = document.querySelector(`.explore-filters .btn[data-filter="${urlParams.filter_type}"]`);
        if (watchBtn) {
            // Set button state without triggering applyExploreFilters
            const group = watchBtn.closest('.btn-group-toggle');
            group.querySelectorAll('.btn').forEach(btn => btn.classList.remove('active'));
            watchBtn.classList.add('active');
            filtersInitialized = true;
        }
    }
    
    
    // Set language filter from URL (need to wait for language buttons to load)
    // This will be handled after language filters are loaded
    if (urlParams.language) {
        // Wait a bit for language buttons to be populated
        setTimeout(() => {
            const langBtn = document.querySelector(`#exploreLanguageFilterGroup .btn[data-language="${urlParams.language}"]`);
            if (langBtn) {
                const languageGroup = document.getElementById('exploreLanguageFilterGroup');
                if (languageGroup) {
                    languageGroup.querySelectorAll('.btn').forEach(btn => btn.classList.remove('active'));
                }
                langBtn.classList.add('active');
                applyExploreFilters();
            }
        }, 100);
    }
    
    // Only fetch if we didn't initialize filters (they will trigger their own fetch)
    if (!filtersInitialized) {
        const { filterType, letter, decade, year } = getCurrentExploreFilters();
        fetchExploreMovies(currentExplorePage || 1, filterType, letter, decade, year);
    } else {
        // Filters were initialized, they will trigger applyExploreFilters
        // But we need to make sure applyExploreFilters is called
        applyExploreFilters();
    }
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
                clearLetterFilter();
                const { filterType, decade, year, language, noYear } = getCurrentExploreFilters();
                fetchExploreMovies(1, filterType, null, decade, year);
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
                clearDecadeFilter();
                const { filterType, letter, year, language, noYear } = getCurrentExploreFilters();
                fetchExploreMovies(1, filterType, letter, null, year);
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

