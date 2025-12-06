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
    
    // Show progress UI
    resultsContainer.innerHTML = `
        <div class="ai-progress-container">
            <div class="ai-progress-step" data-step="1">
                <span class="ai-progress-indicator">○</span>
                <span class="ai-progress-text">Preparing query for AI...</span>
            </div>
            <div class="ai-progress-step" data-step="2">
                <span class="ai-progress-indicator">○</span>
                <span class="ai-progress-text">Waiting for AI response...</span>
            </div>
            <div class="ai-progress-step" data-step="3">
                <span class="ai-progress-indicator">○</span>
                <span class="ai-progress-text">Matching movies in your library...</span>
            </div>
            <div class="ai-progress-step" data-step="4">
                <span class="ai-progress-indicator">○</span>
                <span class="ai-progress-text">Building results...</span>
            </div>
        </div>
    `;
    
    function updateProgress(step, message) {
        // Mark previous steps as completed
        for (let i = 1; i < step; i++) {
            const prevStep = resultsContainer.querySelector(`.ai-progress-step[data-step="${i}"]`);
            if (prevStep) {
                prevStep.classList.add('completed');
                prevStep.classList.remove('active');
                prevStep.querySelector('.ai-progress-indicator').textContent = '✓';
            }
        }
        // Mark current step as active
        const currentStep = resultsContainer.querySelector(`.ai-progress-step[data-step="${step}"]`);
        if (currentStep) {
            currentStep.classList.add('active');
            currentStep.classList.remove('completed');
            currentStep.querySelector('.ai-progress-indicator').textContent = '●';
            currentStep.querySelector('.ai-progress-text').textContent = message;
        }
    }
    
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
        
        // Read SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let resultData = null;
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            
            // Parse SSE events from buffer
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep incomplete line in buffer
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const eventData = JSON.parse(line.slice(6));
                        
                        if (eventData.type === 'progress') {
                            updateProgress(eventData.step, eventData.message);
                        } else if (eventData.type === 'result') {
                            resultData = eventData;
                        } else if (eventData.type === 'error') {
                            throw new Error(eventData.detail);
                        }
                    } catch (parseErr) {
                        console.warn('Failed to parse SSE event:', line, parseErr);
                    }
                }
            }
        }
        
        if (resultData) {
            renderAiResults(resultData);
        } else {
            throw new Error('No result received from AI search');
        }
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
    const listId = data.movie_list_id;
    const listTitle = data.title || '';
    
    // Build header with title and link to saved list
    let headerBlock = '';
    if (listTitle || listId) {
        const slug = (listTitle || '').toString().toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '') || 'list';
        headerBlock = `
            <div class="ai-results-header">
                <div class="ai-results-title">${escapeHtml(listTitle)}</div>
                ${listId ? `<a href="#/lists/${listId}/${escapeHtml(slug)}" class="ai-results-link">View saved list →</a>` : ''}
            </div>
        `;
    }
    
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
        // Store missing movies for copy button
        window._currentMissingMovies = missingMovies;
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
            <div class="section-title-row" style="margin-top: 30px;">
                <div class="section-title">Not in your library</div>
                <button class="btn btn-small btn-copy-names" onclick="copyMissingMovieNames()" title="Copy all titles to clipboard">📋 Copy Names</button>
            </div>
            <div class="ai-missing-list">
                ${rows}
            </div>
        `;
    } else if (foundMovies.length > 0) {
        missingHtml = '<div class="ai-missing-placeholder">All suggested movies are already in your library.</div>';
    }
    
    resultsContainer.innerHTML = `
        <div class="ai-results-wrapper">
            ${headerBlock}
            ${commentBlock}
            ${foundHtml}
            ${missingHtml}
        </div>
    `;
    
    // Reload suggestions to show this new list in recent
    if (typeof loadMovieListSuggestions === 'function') {
        loadMovieListSuggestions('');
    }
    
    if (typeof initAllStarRatings === 'function') {
        initAllStarRatings();
    }
}

// Load suggestions as user types in AI search
let aiSuggestionsDebounce = null;
function initAiSearchSuggestions() {
    const input = document.getElementById('aiSearchInput');
    if (!input) return;
    
    input.addEventListener('input', (e) => {
        clearTimeout(aiSuggestionsDebounce);
        aiSuggestionsDebounce = setTimeout(() => {
            if (typeof loadMovieListSuggestions === 'function') {
                loadMovieListSuggestions(e.target.value);
            }
        }, 300);
    });
    
    // Load initial suggestions
    if (typeof loadMovieListSuggestions === 'function') {
        loadMovieListSuggestions('');
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', initAiSearchSuggestions);

