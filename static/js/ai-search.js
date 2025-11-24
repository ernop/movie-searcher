async function performAiSearch() {
    const input = document.getElementById('aiSearchInput');
    const providerSelect = document.getElementById('aiProviderSelect');
    const statusEl = document.getElementById('aiSearchStatus');
    const costDisplay = document.getElementById('aiCostDisplay');
    const askBtn = document.getElementById('aiSearchButton');
    const resultsContainer = document.getElementById('results');
    
    if (!input || !providerSelect || !resultsContainer) {
        console.warn('AI search controls are missing from the page.');
        return;
    }
    
    const query = input.value.trim();
    if (!query) {
        if (statusEl) statusEl.textContent = 'Type a question to ask the AI.';
        return;
    }
    
    if (statusEl) statusEl.textContent = '';
    if (costDisplay) {
        costDisplay.textContent = '';
        costDisplay.removeAttribute('title');
    }
    if (askBtn) {
        askBtn.disabled = true;
        if (!askBtn.dataset.originalText) {
            askBtn.dataset.originalText = askBtn.textContent;
        }
        askBtn.textContent = 'Searching...';
    }
    
    resultsContainer.innerHTML = '<div class="loading">Processing request...</div>';
    
    try {
        const response = await fetch('/api/ai_search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                query,
                provider: providerSelect.value
            })
        });
        
        if (!response.ok) {
            let detail = 'Search failed';
            try {
                const errorData = await response.json();
                detail = errorData.detail || detail;
            } catch (parseError) {
                // ignore parse errors and use default message
            }
            throw new Error(detail);
        }
        
        const data = await response.json();
        renderAiResults(data);
    } catch (error) {
        console.error('AI Search Error:', error);
        alert(`Error: ${error.message}`);
        if (statusEl) statusEl.textContent = 'AI search failed. Try again.';
        resultsContainer.innerHTML = '';
    } finally {
        if (askBtn) {
            askBtn.disabled = false;
            askBtn.textContent = askBtn.dataset.originalText || 'Ask AI';
        }
    }
}

function renderAiResults(data) {
    const resultsContainer = document.getElementById('results');
    const costDisplay = document.getElementById('aiCostDisplay');
    
    if (!resultsContainer) return;
    
    if (costDisplay) {
        costDisplay.textContent = '';
        costDisplay.removeAttribute('title');
        const usdValue = typeof data.cost_usd === 'number'
            ? data.cost_usd
            : (typeof data.cost_cents === 'number' ? data.cost_cents / 100 : null);
        if (typeof usdValue === 'number') {
            const usdText = usdValue < 0.01 ? usdValue.toFixed(5) : usdValue.toFixed(4);
            costDisplay.textContent = `Est. cost: $${usdText}`;
            if (data.cost_details) {
                costDisplay.title = data.cost_details;
            }
        } else if (data.cost_details) {
            costDisplay.textContent = data.cost_details;
            costDisplay.title = data.cost_details;
        }
    }
    
    const statusEl = document.getElementById('aiSearchStatus');
    if (statusEl) {
        statusEl.textContent = '';
    }
    
    const foundMovies = Array.isArray(data.found_movies) ? data.found_movies : [];
    const missingMovies = Array.isArray(data.missing_movies) ? data.missing_movies : [];
    const overallComment = (data.comment || '').trim();
    
    let commentBlock = '';
    if (overallComment) {
        commentBlock = `<div class="ai-overall-comment">${escapeHtml(overallComment)}</div>`;
    }
    
    let foundHtml = '';
    if (foundMovies.length > 0) {
        const cards = foundMovies.map(movie => {
            const commentText = movie.ai_comment
                ? `<div class="ai-movie-comment">${escapeHtml(movie.ai_comment)}</div>`
                : `<div class="ai-movie-comment muted">No specific comment.</div>`;
            return `<div class="ai-card-suggestion">${createMovieCard(movie)}${commentText}</div>`;
        }).join('');
        foundHtml = `
            <div class="section-title" style="margin-top: 5px;">In Your Library</div>
            <div class="movie-grid ai-results-grid">
                ${cards}
            </div>
        `;
    } else {
        foundHtml = '<div class="empty-state">No matching movies found in your library.</div>';
    }
    
    let missingHtml = '';
    if (missingMovies.length > 0) {
        const rows = missingMovies.map(movie => {
            const title = escapeHtml(movie.name || 'Unknown title');
            const year = movie.year ? ` <span class="ai-missing-year">(${movie.year})</span>` : '';
            const comment = movie.ai_comment
                ? `<div class="ai-missing-comment">${escapeHtml(movie.ai_comment)}</div>`
                : '';
            return `<div class="ai-missing-item">
                        <div class="ai-missing-card">
                            <div class="ai-missing-title"><strong>${title}</strong>${year}</div>
                        </div>
                        ${comment}
                    </div>`;
        }).join('');
        missingHtml = `
            <div class="section-title" style="margin-top: 30px;">Not in your library</div>
            <div class="ai-missing-list">
                ${rows}
            </div>
        `;
    } else if (foundMovies.length > 0) {
        missingHtml = '<div class="ai-missing-placeholder">All suggested movies are already in your library.</div>';
    }
    
    resultsContainer.innerHTML = `
        <div class="ai-results-wrapper">
            ${commentBlock}
            ${foundHtml}
            ${missingHtml}
        </div>
    `;
    
    if (typeof initAllStarRatings === 'function') {
        initAllStarRatings();
    }
}

