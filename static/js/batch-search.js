// Batch Search - Batch URL opener for movie searches

let batchSearchQueue = [];
let batchSearchCompleted = [];
let batchSearchTotal = 0;

function initBatchSearch() {
    // Restore host from localStorage if available
    const savedHost = localStorage.getItem('batchSearchHost');
    if (savedHost) {
        document.getElementById('batchHostInput').value = savedHost;
    }

    // Check if we have saved state
    const savedState = sessionStorage.getItem('batchSearchState');
    if (savedState) {
        try {
            const state = JSON.parse(savedState);
            batchSearchQueue = state.queue || [];
            batchSearchCompleted = state.completed || [];
            batchSearchTotal = state.total || 0;

            if (batchSearchQueue.length > 0 || batchSearchCompleted.length > 0) {
                showQueuePhase();
                updateQueueDisplay();
                updateProgress();
            }
        } catch (e) {
            console.error('Failed to restore batch search state:', e);
        }
    }
}

function saveState() {
    const state = {
        queue: batchSearchQueue,
        completed: batchSearchCompleted,
        total: batchSearchTotal
    };
    sessionStorage.setItem('batchSearchState', JSON.stringify(state));
}

function startBatchProcess() {
    const hostInput = document.getElementById('batchHostInput');
    const host = hostInput.value.trim();

    if (!host) {
        alert('Please enter a host first');
        hostInput.focus();
        return;
    }

    // Save host
    localStorage.setItem('batchSearchHost', host);

    const textarea = document.getElementById('batchMovieList');
    const text = textarea.value.trim();

    if (!text) {
        alert('Please paste your movie list');
        textarea.focus();
        return;
    }

    // Parse the list
    const lines = text.split('\n').filter(line => line.trim());
    batchSearchQueue = lines.map(line => line.trim());
    batchSearchCompleted = [];
    batchSearchTotal = batchSearchQueue.length;

    if (batchSearchTotal === 0) {
        alert('No valid entries found');
        return;
    }

    saveState();
    showQueuePhase();
    updateQueueDisplay();
    updateProgress();
}

function showQueuePhase() {
    document.getElementById('batchInputPhase').style.display = 'none';
    document.getElementById('batchQueuePhase').style.display = 'block';
}

function showInputPhase() {
    document.getElementById('batchInputPhase').style.display = 'block';
    document.getElementById('batchQueuePhase').style.display = 'none';
}

function cleanMovieLine(line) {
    // Remove possessive 's (e.g., "Schindler's" -> "Schindler")
    let cleaned = line.replace(/'s\b/g, '');

    // Remove all quotes (single, double, curly quotes)
    cleaned = cleaned.replace(/['"'""`´]/g, '');

    // Remove other problematic characters (keep letters, numbers, spaces, hyphens)
    cleaned = cleaned.replace(/[^\w\s\-]/g, ' ');

    // Collapse multiple spaces into one
    cleaned = cleaned.replace(/\s+/g, ' ').trim();

    return cleaned;
}

function composeBatchUrl(host, movieLine) {
    const cleaned = cleanMovieLine(movieLine);
    const encoded = encodeURIComponent(cleaned);
    return `https://${host}/search.php?q=${encoded}&all=on&search=Search`;
}

async function openBatchSearchTabs() {
    const hostInput = document.getElementById('batchHostInput');
    const host = hostInput.value.trim();

    if (!host) {
        alert('Please enter a host');
        hostInput.focus();
        return;
    }

    if (batchSearchQueue.length === 0) {
        return;
    }

    // Disable button while opening
    const btn = document.getElementById('batchOpenBtn');
    btn.disabled = true;
    btn.textContent = 'Opening...';

    // Get up to 5 items to open
    const toOpen = batchSearchQueue.splice(0, 5);
    const urls = toOpen.map(movieLine => composeBatchUrl(host, movieLine));

    try {
        // Call backend to open URLs (bypasses popup blocker)
        const response = await fetch('/api/open-urls', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls })
        });

        const data = await response.json();

        if (data.status === 'ok') {
            // Mark all as completed
            toOpen.forEach(movie => batchSearchCompleted.push(movie));

            // Hide any previous warning
            const warning = document.getElementById('batchPopupWarning');
            if (warning) warning.style.display = 'none';
        } else {
            // Put items back in queue if failed
            batchSearchQueue.unshift(...toOpen);
            alert('Failed to open URLs: ' + (data.error || 'Unknown error'));
        }
    } catch (error) {
        console.error('Error opening URLs:', error);
        // Put items back in queue
        batchSearchQueue.unshift(...toOpen);
        alert('Failed to open URLs: ' + error.message);
    }

    saveState();
    updateQueueDisplay();
    updateProgress();
}

function updateProgress() {
    const opened = batchSearchCompleted.length;
    const remaining = batchSearchQueue.length;
    const total = batchSearchTotal;
    const percent = total > 0 ? Math.round((opened / total) * 100) : 0;

    document.getElementById('batchProgressText').textContent = `${opened} of ${total} opened`;
    document.getElementById('batchRemainingText').textContent = `${remaining} remaining`;
    document.getElementById('batchProgressFill').style.width = `${percent}%`;

    // Update button text
    const btn = document.getElementById('batchOpenBtn');
    if (remaining === 0) {
        btn.textContent = 'All Done!';
        btn.disabled = true;
    } else if (remaining < 5) {
        btn.textContent = `Open Last ${remaining}`;
        btn.disabled = false;
    } else {
        btn.textContent = 'Open Next 5';
        btn.disabled = false;
    }
}

function updateQueueDisplay() {
    const queueContainer = document.getElementById('batchQueueItems');
    const completedContainer = document.getElementById('batchCompletedItems');
    const completedSection = document.getElementById('batchCompletedSection');

    // Show up to 15 items in queue
    const queuePreview = batchSearchQueue.slice(0, 15);
    queueContainer.innerHTML = queuePreview.map((item, i) => `
        <div class="batch-queue-item ${i < 5 ? 'next-batch' : ''}">${escapeHtml(item)}</div>
    `).join('');

    if (batchSearchQueue.length > 15) {
        queueContainer.innerHTML += `<div class="batch-queue-item more">...and ${batchSearchQueue.length - 15} more</div>`;
    }

    if (batchSearchQueue.length === 0) {
        queueContainer.innerHTML = '<div class="batch-queue-empty">Queue empty!</div>';
    }

    // Show all completed items (in order they were opened)
    if (batchSearchCompleted.length > 0) {
        completedSection.style.display = 'block';
        completedContainer.innerHTML = batchSearchCompleted.map(item => `
            <div class="batch-completed-item">${escapeHtml(item)}</div>
        `).join('');
    } else {
        completedSection.style.display = 'none';
    }
}

function resetBatchQueue() {
    batchSearchQueue = [];
    batchSearchCompleted = [];
    batchSearchTotal = 0;
    sessionStorage.removeItem('batchSearchState');

    // Clear textarea
    document.getElementById('batchMovieList').value = '';

    // Reset button
    const btn = document.getElementById('batchOpenBtn');
    btn.textContent = 'Open Next 5';
    btn.disabled = false;

    showInputPhase();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
