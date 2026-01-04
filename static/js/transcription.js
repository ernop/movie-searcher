// Transcription functionality for Movie Searcher
// Handles UI for Whisper-based dialogue transcription

// Track active polling intervals per movie
const transcriptionPollers = {};

async function checkTranscriptionStatus(movieId) {
  try {
    const response = await fetch(`/api/transcription/status/${movieId}`);
    if (!response.ok) {
      if (response.status === 404) {
        return { status: 'not_started', progress: 0 };
      }
      throw new Error(`HTTP ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error checking transcription status:', error);
    return { status: 'error', error_message: error.message };
  }
}

async function checkTranscriptionSetup() {
  try {
    const response = await fetch('/api/transcription/check-setup');
    if (!response.ok) {
      return { ready: false, errors: ['Failed to check setup'] };
    }
    return await response.json();
  } catch (error) {
    return { ready: false, errors: [error.message] };
  }
}

async function startTranscription(movieId, modelSize = 'large-v3') {
  try {
    const response = await fetch('/api/transcription/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ movie_id: movieId, model_size: modelSize })
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to start transcription');
    }

    const result = await response.json();

    // Start polling for progress
    startTranscriptionPolling(movieId);

    return result;
  } catch (error) {
    console.error('Error starting transcription:', error);
    showStatus('Failed to start transcription: ' + error.message, 'error');
    throw error;
  }
}

function startTranscriptionPolling(movieId) {
  // Clear any existing poller
  if (transcriptionPollers[movieId]) {
    clearInterval(transcriptionPollers[movieId]);
  }

  // Poll every 2 seconds
  transcriptionPollers[movieId] = setInterval(async () => {
    const status = await checkTranscriptionStatus(movieId);
    updateTranscriptionUI(movieId, status);

    // Stop polling when complete or failed
    if (status.status === 'completed' || status.status === 'failed' || status.status === 'not_started') {
      clearInterval(transcriptionPollers[movieId]);
      delete transcriptionPollers[movieId];

      // If completed, load the transcript
      if (status.status === 'completed') {
        await loadAndDisplayTranscript(movieId);
      }
    }
  }, 2000);
}

async function loadAndDisplayTranscript(movieId) {
  try {
    const response = await fetch(`/api/transcription/transcript/${movieId}`);
    if (!response.ok) {
      throw new Error('Failed to load transcript');
    }
    const data = await response.json();
    displayTranscript(movieId, data);
  } catch (error) {
    console.error('Error loading transcript:', error);
  }
}

async function deleteTranscript(movieId) {
  if (!confirm('Delete this transcript? This cannot be undone.')) {
    return;
  }

  try {
    const response = await fetch(`/api/transcription/transcript/${movieId}`, {
      method: 'DELETE'
    });

    if (!response.ok) {
      throw new Error('Failed to delete transcript');
    }

    showStatus('Transcript deleted', 'success');

    // Refresh the transcription section
    await renderTranscriptionSection(movieId);
  } catch (error) {
    console.error('Error deleting transcript:', error);
    showStatus('Failed to delete transcript: ' + error.message, 'error');
  }
}

function updateTranscriptionUI(movieId, status) {
  const container = document.getElementById(`transcription-section-${movieId}`);
  if (!container) return;

  const progressBar = container.querySelector('.transcription-progress-bar');
  const progressText = container.querySelector('.transcription-progress-text');
  const statusText = container.querySelector('.transcription-status');
  const startBtn = container.querySelector('.transcription-start-btn');
  const progressSection = container.querySelector('.transcription-progress');

  if (status.status === 'not_started') {
    if (startBtn) startBtn.style.display = 'inline-block';
    if (progressSection) progressSection.style.display = 'none';
    if (statusText) statusText.textContent = '';
  } else if (status.status === 'completed') {
    if (startBtn) startBtn.style.display = 'none';
    if (progressSection) progressSection.style.display = 'none';
    if (statusText) {
      statusText.innerHTML = `<span class="transcription-complete">✓ Transcribed</span> · ${status.segment_count || 0} segments · ${status.word_count || 0} words`;
    }
  } else if (status.status === 'failed') {
    if (startBtn) startBtn.style.display = 'inline-block';
    if (progressSection) progressSection.style.display = 'none';
    if (statusText) {
      statusText.innerHTML = `<span class="transcription-error">✗ Failed</span> ${status.error_message ? ': ' + escapeHtml(status.error_message.substring(0, 100)) : ''}`;
    }
  } else {
    // In progress
    if (startBtn) startBtn.style.display = 'none';
    if (progressSection) progressSection.style.display = 'block';
    if (progressBar) progressBar.style.width = `${status.progress || 0}%`;
    if (progressText) progressText.textContent = `${Math.round(status.progress || 0)}%`;
    if (statusText) statusText.textContent = status.current_step || 'Processing...';
  }
}

function displayTranscript(movieId, data) {
  const container = document.getElementById(`transcription-section-${movieId}`);
  if (!container) return;

  const transcriptContainer = container.querySelector('.transcript-content');
  if (!transcriptContainer) return;

  if (!data.segments || data.segments.length === 0) {
    transcriptContainer.innerHTML = '<p class="transcript-empty">No dialogue found in this video.</p>';
    return;
  }

  // Group segments and format them
  let html = '<div class="transcript-segments">';

  data.segments.forEach((seg, idx) => {
    const startTime = formatTranscriptTime(seg.start_time);
    const endTime = formatTranscriptTime(seg.end_time);
    const speaker = seg.speaker_id ? `<span class="transcript-speaker">${escapeHtml(seg.speaker_id)}</span>` : '';

    html += `
            <div class="transcript-segment" data-start="${seg.start_time}" data-end="${seg.end_time}">
                <div class="transcript-time" onclick="seekToTime(${movieId}, ${seg.start_time})" title="Jump to ${startTime}">
                    ${startTime}
                </div>
                <div class="transcript-text">
                    ${speaker}
                    ${escapeHtml(seg.text)}
                </div>
            </div>
        `;
  });

  html += '</div>';

  // Add search box
  html = `
        <div class="transcript-search">
            <input type="text" 
                   placeholder="Search transcript..." 
                   class="transcript-search-input"
                   oninput="filterTranscript(${movieId}, this.value)">
        </div>
    ` + html;

  transcriptContainer.innerHTML = html;
  transcriptContainer.style.display = 'block';
}

function formatTranscriptTime(seconds) {
  if (seconds === null || seconds === undefined) return '0:00';

  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function filterTranscript(movieId, searchTerm) {
  const container = document.getElementById(`transcription-section-${movieId}`);
  if (!container) return;

  const segments = container.querySelectorAll('.transcript-segment');
  const term = searchTerm.toLowerCase().trim();

  segments.forEach(seg => {
    const text = seg.querySelector('.transcript-text').textContent.toLowerCase();
    if (term === '' || text.includes(term)) {
      seg.style.display = 'flex';
      // Highlight matches
      if (term && term.length > 1) {
        const textEl = seg.querySelector('.transcript-text');
        const original = textEl.textContent;
        const regex = new RegExp(`(${escapeRegex(searchTerm)})`, 'gi');
        textEl.innerHTML = escapeHtml(original).replace(regex, '<mark>$1</mark>');
      }
    } else {
      seg.style.display = 'none';
    }
  });
}

function escapeRegex(str) {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function seekToTime(movieId, seconds) {
  // For now, just show a message - full implementation would require VLC control
  showStatus(`Timestamp: ${formatTranscriptTime(seconds)} - Launch movie to jump to this point`, 'info');
}

async function renderTranscriptionSection(movieId) {
  const status = await checkTranscriptionStatus(movieId);

  let html = `
        <div id="transcription-section-${movieId}" class="transcription-section">
            <div class="transcription-header">
                <h3>Transcript</h3>
                <div class="transcription-controls">
    `;

  if (status.status === 'not_started' || status.status === 'failed') {
    html += `
            <button class="btn transcription-start-btn" onclick="handleStartTranscription(${movieId})">
                Transcribe Audio
            </button>
        `;
  }

  if (status.status === 'completed') {
    html += `
            <button class="btn btn-secondary btn-small" onclick="deleteTranscript(${movieId})" title="Delete transcript">
                🗑️
            </button>
        `;
  }

  html += `
                </div>
            </div>
            <div class="transcription-status"></div>
            <div class="transcription-progress" style="display: none;">
                <div class="transcription-progress-bar-container">
                    <div class="transcription-progress-bar" style="width: 0%"></div>
                </div>
                <span class="transcription-progress-text">0%</span>
            </div>
            <div class="transcript-content" style="display: none;"></div>
        </div>
    `;

  // Update the UI with current status
  setTimeout(() => {
    updateTranscriptionUI(movieId, status);

    // If in progress, start polling
    if (status.status !== 'not_started' && status.status !== 'completed' && status.status !== 'failed') {
      startTranscriptionPolling(movieId);
    }

    // If completed, load transcript
    if (status.status === 'completed') {
      loadAndDisplayTranscript(movieId);
    }
  }, 50);

  return html;
}

async function handleStartTranscription(movieId) {
  const container = document.getElementById(`transcription-section-${movieId}`);
  if (!container) return;

  const startBtn = container.querySelector('.transcription-start-btn');
  if (startBtn) {
    startBtn.disabled = true;
    startBtn.textContent = 'Starting...';
  }

  try {
    // First check if setup is ready
    const setup = await checkTranscriptionSetup();
    if (!setup.ready) {
      let msg = 'Transcription is not ready. ';
      if (!setup.pytorch_installed) {
        msg += 'PyTorch is not installed. ';
      } else if (!setup.cuda_available) {
        msg += 'CUDA is not available. ';
      }
      if (!setup.faster_whisper_installed) {
        msg += 'faster-whisper is not installed. ';
      }
      if (setup.errors && setup.errors.length > 0) {
        msg += setup.errors.join(' ');
      }
      showStatus(msg, 'error');
      if (startBtn) {
        startBtn.disabled = false;
        startBtn.textContent = 'Transcribe Audio';
      }
      return;
    }

    await startTranscription(movieId);
    showStatus('Transcription started', 'success');
  } catch (error) {
    if (startBtn) {
      startBtn.disabled = false;
      startBtn.textContent = 'Transcribe Audio';
    }
  }
}

// Dialogue Search Page

async function loadDialogueSearchPage() {
  // Load transcription stats
  try {
    const response = await fetch('/api/transcription/stats');
    if (response.ok) {
      const stats = await response.json();
      const statsEl = document.getElementById('dialogueSearchStats');
      if (statsEl) {
        if (stats.movies_transcribed === 0) {
          statsEl.innerHTML = `
                        <div class="dialogue-stats-warning">
                            No movies have been transcribed yet. 
                            Visit a movie's detail page and click "Transcribe Audio" to get started.
                        </div>
                    `;
        } else {
          statsEl.innerHTML = `
                        <span class="dialogue-stat">${stats.movies_transcribed} movies transcribed</span>
                        <span class="dialogue-stat">${stats.total_words?.toLocaleString() || 0} words</span>
                        <span class="dialogue-stat">${stats.total_duration_hours || 0} hours of dialogue</span>
                    `;
        }
      }
    }
  } catch (error) {
    console.error('Failed to load transcription stats:', error);
  }
}

async function performDialogueSearch() {
  const input = document.getElementById('dialogueSearchInput');
  const resultsContainer = document.getElementById('dialogueSearchResults');

  if (!input || !resultsContainer) return;

  const query = input.value.trim();

  if (query.length < 2) {
    resultsContainer.innerHTML = '<div class="dialogue-search-empty">Enter at least 2 characters to search.</div>';
    return;
  }

  resultsContainer.innerHTML = '<div class="loading">Searching...</div>';

  try {
    const response = await fetch(`/api/transcription/search?q=${encodeURIComponent(query)}&limit=100`);

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Search failed');
    }

    const data = await response.json();

    if (data.results.length === 0) {
      resultsContainer.innerHTML = `
                <div class="dialogue-search-empty">
                    No dialogue found matching "${escapeHtml(query)}"
                </div>
            `;
      return;
    }

    // Group results by movie
    const movieGroups = {};
    data.results.forEach(result => {
      if (!movieGroups[result.movie_id]) {
        movieGroups[result.movie_id] = {
          movie_id: result.movie_id,
          movie_name: result.movie_name,
          results: []
        };
      }
      movieGroups[result.movie_id].results.push(result);
    });

    let html = `<div class="dialogue-results-header">${data.total_results} results for "${escapeHtml(query)}"</div>`;

    Object.values(movieGroups).forEach(group => {
      html += `
                <div class="dialogue-movie-group">
                    <div class="dialogue-movie-header">
                        <a href="#/movie/${group.movie_id}" class="dialogue-movie-name">${escapeHtml(group.movie_name)}</a>
                        <span class="dialogue-movie-count">${group.results.length} match${group.results.length !== 1 ? 'es' : ''}</span>
                    </div>
                    <div class="dialogue-movie-results">
            `;

      group.results.forEach(result => {
        const time = formatTranscriptTime(result.start_time);
        const highlightedText = highlightSearchTerm(result.text, query);

        html += `
                    <div class="dialogue-result-item">
                        <div class="dialogue-result-time" title="Jump to ${time}">
                            ${time}
                        </div>
                        <div class="dialogue-result-content">
                            ${result.context_before ? `<div class="dialogue-context-before">...${escapeHtml(result.context_before)}</div>` : ''}
                            <div class="dialogue-result-text">${highlightedText}</div>
                            ${result.context_after ? `<div class="dialogue-context-after">${escapeHtml(result.context_after)}...</div>` : ''}
                        </div>
                    </div>
                `;
      });

      html += `
                    </div>
                </div>
            `;
    });

    resultsContainer.innerHTML = html;

  } catch (error) {
    console.error('Dialogue search error:', error);
    resultsContainer.innerHTML = `
            <div class="dialogue-search-error">
                Search failed: ${escapeHtml(error.message)}
            </div>
        `;
  }
}

function highlightSearchTerm(text, term) {
  const escaped = escapeHtml(text);
  const termEscaped = escapeHtml(term);
  const regex = new RegExp(`(${escapeRegex(termEscaped)})`, 'gi');
  return escaped.replace(regex, '<mark>$1</mark>');
}

// Export for use in movie-details.js
window.renderTranscriptionSection = renderTranscriptionSection;
window.checkTranscriptionStatus = checkTranscriptionStatus;
window.startTranscription = startTranscription;
window.deleteTranscript = deleteTranscript;
window.handleStartTranscription = handleStartTranscription;
window.loadDialogueSearchPage = loadDialogueSearchPage;
window.performDialogueSearch = performDialogueSearch;

