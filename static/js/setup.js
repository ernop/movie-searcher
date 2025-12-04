// Setup Page Functions

async function restartServer() {
    const btn = document.getElementById('restartServerBtn');
    const statusEl = document.getElementById('restartStatus');
    
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⟳ Restarting...';
    }
    if (statusEl) {
        statusEl.textContent = 'Sending restart signal...';
        statusEl.style.color = '#f0ad4e';
    }
    
    try {
        const response = await fetch('/api/server/restart', { method: 'POST' });
        const data = await response.json();
        
        if (response.ok) {
            if (statusEl) {
                statusEl.textContent = 'Server is restarting, waiting for reconnect...';
            }
            
            // Wait for server to come back up
            await waitForServerRestart();
            
            if (statusEl) {
                statusEl.textContent = 'Server restarted successfully!';
                statusEl.style.color = '#4caf50';
            }
            
            // Reload the page after a brief moment
            setTimeout(() => {
                window.location.reload();
            }, 500);
        } else {
            throw new Error(data.detail || 'Failed to restart server');
        }
    } catch (error) {
        if (statusEl) {
            // If we get a fetch error, the server may have already restarted
            if (error.name === 'TypeError' || error.message.includes('fetch')) {
                statusEl.textContent = 'Server restarting, waiting for reconnect...';
                await waitForServerRestart();
                statusEl.textContent = 'Server restarted successfully!';
                statusEl.style.color = '#4caf50';
                setTimeout(() => {
                    window.location.reload();
                }, 500);
            } else {
                statusEl.textContent = 'Error: ' + error.message;
                statusEl.style.color = '#f44336';
            }
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '⟳ Restart Server';
        }
    }
}

async function waitForServerRestart(maxAttempts = 30, interval = 500) {
    for (let i = 0; i < maxAttempts; i++) {
        try {
            const response = await fetch('/api/stats', { 
                method: 'GET',
                cache: 'no-store'
            });
            if (response.ok) {
                return true;  // Server is back up
            }
        } catch (e) {
            // Server not yet available, continue waiting
        }
        await new Promise(resolve => setTimeout(resolve, interval));
    }
    throw new Error('Server did not restart within expected time');
}

async function loadCurrentFolder() {
    const setupCurrentFolderEl = document.getElementById('setupCurrentFolder');
    const setupLocalTargetFolderEl = document.getElementById('setupLocalTargetFolder');

    if (setupCurrentFolderEl) {
        setupCurrentFolderEl.textContent = 'Loading...';
    }
    if (setupLocalTargetFolderEl) {
        setupLocalTargetFolderEl.textContent = 'Loading...';
    }

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})  // Empty body to get current config
        });

        const data = await response.json();

        if (response.ok) {
            const folderPath = data.movies_folder || 'Not set';
            const localTargetPath = data.local_target_folder || 'Not set';

            if (setupCurrentFolderEl) {
                setupCurrentFolderEl.textContent = folderPath;
            }
            if (setupLocalTargetFolderEl) {
                setupLocalTargetFolderEl.textContent = localTargetPath;
            }

            // Also update setup checkboxes if present
            if (data.settings) {
                const closeVlcEl = document.getElementById('setupCloseExistingVlc');
                const launchSubsEl = document.getElementById('setupLaunchWithSubtitlesOn');
                const showAllMoviesEl = document.getElementById('setupShowAllMoviesTab');

                if (closeVlcEl && data.settings.close_existing_vlc !== undefined) {
                    closeVlcEl.checked = data.settings.close_existing_vlc;
                }
                if (launchSubsEl && data.settings.launch_with_subtitles_on !== undefined) {
                    launchSubsEl.checked = data.settings.launch_with_subtitles_on;
                }
                if (showAllMoviesEl) {
                    // Default to false if not set
                    showAllMoviesEl.checked = data.settings.show_all_movies_tab === true;
                }

                // Update nav visibility
                updateAllMoviesNavVisibility(data.settings.show_all_movies_tab === true);
            }

            return folderPath;
        } else {
            if (setupCurrentFolderEl) {
                setupCurrentFolderEl.textContent = 'Error loading';
            }
            if (setupLocalTargetFolderEl) {
                setupLocalTargetFolderEl.textContent = 'Error loading';
            }
            return null;
        }
    } catch (error) {
        if (setupCurrentFolderEl) {
            setupCurrentFolderEl.textContent = 'Error loading';
        }
        if (setupLocalTargetFolderEl) {
            setupLocalTargetFolderEl.textContent = 'Error loading';
        }
        return null;
    }
}

async function loadStats() {
    const setupStatsEl = document.getElementById('setupStats');
    const statsEl = document.getElementById('stats');

    try {
        const response = await fetch('/api/stats');
        const data = await response.json();

        if (!response.ok) {
            console.error('Failed to load stats');
            return;
        }

        // Format numbers
        const totalMovies = data.total_movies || 0;
        const watchedCount = data.watched_count || 0;
        const watchedPercent = totalMovies > 0 ? Math.round((watchedCount / totalMovies) * 100) : 0;

        const statsHtml = `
            <div class="stats-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px;">
                <div class="stat-card" style="background: #1a1a1a; padding: 15px; border-radius: 6px; border: 1px solid #3a3a3a;">
                    <div style="font-size: 12px; color: #999; margin-bottom: 5px;">Total Movies</div>
                    <div style="font-size: 24px; font-weight: 500; color: #fff;">${totalMovies}</div>
                </div>
                <div class="stat-card" style="background: #1a1a1a; padding: 15px; border-radius: 6px; border: 1px solid #3a3a3a;">
                    <div style="font-size: 12px; color: #999; margin-bottom: 5px;">Watched</div>
                    <div style="font-size: 24px; font-weight: 500; color: #4caf50;">${watchedCount} <span style="font-size: 14px; color: #666;">(${watchedPercent}%)</span></div>
                </div>
            </div>
        `;

        if (setupStatsEl) {
            setupStatsEl.innerHTML = statsHtml;
        }

        if (statsEl) {
            statsEl.innerHTML = `
                <div>Total Movies: <span style="color: #fff;">${totalMovies}</span></div>
                <div>Watched: <span style="color: #4caf50;">${watchedCount}</span> (${watchedPercent}%)</div>
            `;
        }

    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

async function saveFolderPath() {
    const input = document.getElementById('folderPathInput');
    let folderPath = input.value.trim();

    if (!folderPath) {
        showStatus('Please enter a folder path', 'error');
        return;
    }

    // Validate absolute path before normalizing
    if (typeof isValidAbsolutePath === 'function' && !isValidAbsolutePath(folderPath)) {
        showStatus('Path must be absolute (e.g., D:\\movies or C:\\Movies)', 'error');
        return;
    }

    // Normalize the path (handle /, \, \\)
    folderPath = normalizePath(folderPath);

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ movies_folder: folderPath })
        });

        const data = await response.json();

        if (response.ok) {
            showStatus('Movies folder updated successfully', 'success');
            loadCurrentFolder();
            loadStats();
            hideFolderDialog();
        } else {
            showStatus('Failed to update folder: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (error) {
        showStatus('Failed to update folder: ' + error.message, 'error');
    }
}

async function loadHiddenMovies() {
    const container = document.getElementById('hiddenMoviesList');
    container.style.display = 'block';
    container.innerHTML = '<div class="loading">Loading hidden movies...</div>';

    try {
        const response = await fetch('/api/hidden-movies');
        if (!response.ok) throw new Error('Failed to load hidden movies');

        const data = await response.json();
        const movies = data.movies || [];

        if (movies.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding: 20px;">No hidden movies</div>';
            return;
        }

        let html = '<div class="movie-grid">';
        movies.forEach(movie => {
            html += createMovieCard(movie, {
                showMenu: false,
                showRating: true,
                watchStatusControl: false, // Don't show watch status for hidden movies
                customButtons: `<button class="btn btn-success" style="width:100%" onclick="event.stopPropagation(); unhideMovie(${movie.id})">Unhide</button>`
            });
        });
        html += '</div>';
        container.innerHTML = html;

        // Initialize star ratings
        initAllStarRatings();

        // Restore scroll position if available
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }
    } catch (error) {
        container.innerHTML = `<div class="status-message error">Error: ${error.message}</div>`;
    }
}

async function unhideMovie(movieId) {
    try {
        const response = await fetch(`/api/movie/${movieId}/unhide`, {
            method: 'POST'
        });

        if (response.ok) {
            showStatus('Movie unhidden', 'success');
            loadHiddenMovies(); // Reload list
        } else {
            showStatus('Failed to unhide movie', 'error');
        }
    } catch (error) {
        console.error('Error unhiding movie:', error);
        showStatus('Error unhiding movie', 'error');
    }
}

async function loadDuplicateMovies() {
    const container = document.getElementById('duplicateMoviesList');
    container.style.display = 'block';
    container.innerHTML = '<div class="loading">Searching for duplicates...</div>';

    try {
        const response = await fetch('/api/duplicates');
        if (!response.ok) throw new Error('Failed to load duplicates');

        const data = await response.json();
        const duplicates = data.duplicates || [];

        if (duplicates.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding: 20px;">No duplicate movies found</div>';
            return;
        }

        let html = '<div class="duplicates-list">';
        duplicates.forEach(group => {
            html += `
                <div class="duplicate-group" style="margin-bottom: 30px; border-bottom: 1px solid #3a3a3a; padding-bottom: 20px;">
                    <h4 style="color: #fff; margin-bottom: 15px;">${escapeHtml(group.name)} (${group.count})</h4>
                    <div class="movie-grid">
                        ${group.movies.map(movie => createMovieCard(movie)).join('')}
                    </div>
                </div>
            `;
        });
        html += '</div>';
        container.innerHTML = html;

        // Initialize star ratings for the new cards
        if (typeof initAllStarRatings === 'function') {
            initAllStarRatings();
        }

        // Restore scroll position if available
        if (typeof restoreScrollPosition === 'function') {
            restoreScrollPosition();
        }

    } catch (error) {
        container.innerHTML = `<div class="status-message error">Error: ${error.message}</div>`;
    }
}

async function loadSystemStatus() {
    const statusEl = document.getElementById('systemStatus');
    if (!statusEl) return;

    try {
        const [ffmpegResponse, vlcResponse] = await Promise.all([
            fetch('/api/test-ffmpeg'),
            fetch('/api/test-vlc')
        ]);

        if (!ffmpegResponse.ok) {
            const statusText = ffmpegResponse.statusText || `HTTP ${ffmpegResponse.status}`;
            let errorDetail = '';
            try {
                const errorData = await ffmpegResponse.text();
                if (errorData) {
                    const parsed = JSON.parse(errorData);
                    errorDetail = parsed.detail || parsed.message || errorData.substring(0, 100);
                } else {
                    errorDetail = statusText;
                }
            } catch {
                errorDetail = statusText;
            }

            statusEl.innerHTML = `
                <div style="color: #f44336;">
                    <div style="font-weight: 500;">Server error checking system status</div>
                    <div style="font-size: 12px; color: #999; margin-top: 5px;">
                        ${escapeHtml(statusText)}: ${escapeHtml(errorDetail)}
                    </div>
                </div>
            `;
            return;
        }

        let ffmpegResult, vlcResult;
        try {
            ffmpegResult = await ffmpegResponse.json();
            vlcResult = await vlcResponse.json();
        } catch (jsonError) {
            statusEl.innerHTML = `
                <div style="color: #f44336;">
                    <div style="font-weight: 500;">Invalid response from server</div>
                    <div style="font-size: 12px; color: #999; margin-top: 5px;">
                        Server returned non-JSON response. ${escapeHtml(jsonError.message)}
                    </div>
                </div>
            `;
            return;
        }

        let statusHtml = '';
        const allOk = ffmpegResult.ok && vlcResult.ok;
        const allErrors = [...(ffmpegResult.errors || []), ...(vlcResult.errors || [])];

        if (allOk) {
            statusHtml = `
                <div style="display: flex; align-items: center; gap: 10px; color: #4caf50;">
                    <span style="font-size: 20px;">✓</span>
                    <div>
                        <div style="font-weight: 500; margin-bottom: 5px;">All systems operational</div>
                        <div style="font-size: 12px; color: #999;">
                            ffmpeg: ${ffmpegResult.ffmpeg_version || 'OK'} | 
                            ffprobe: ${ffmpegResult.ffprobe_version || 'OK'} | 
                            VLC: ${vlcResult.vlc_version || 'OK'}
                        </div>
                    </div>
                </div>
            `;
        } else {
            statusHtml = `
                <div style="display: flex; align-items: flex-start; gap: 10px; color: #f44336;">
                    <span style="font-size: 20px;">✗</span>
                    <div style="flex: 1;">
                        <div style="font-weight: 500; margin-bottom: 5px;">System issues detected</div>
                        <div style="font-size: 12px; color: #999; margin-top: 8px;">
                            ${allErrors.map(e => `<div>• ${escapeHtml(e)}</div>`).join('')}
                        </div>
                        ${ffmpegResult.ffmpeg_path ? `<div style="font-size: 11px; color: #666; margin-top: 8px;">ffmpeg: ${escapeHtml(ffmpegResult.ffmpeg_path)}</div>` : ''}
                        ${vlcResult.vlc_path ? `<div style="font-size: 11px; color: #666; margin-top: 8px;">VLC: ${escapeHtml(vlcResult.vlc_path)}</div>` : ''}
                        ${vlcResult.checked_locations ? `<div style="font-size: 11px; color: #666; margin-top: 4px;">Searched: ${vlcResult.checked_locations.length} locations</div>` : ''}
                    </div>
                </div>
            `;
        }

        statusEl.innerHTML = statusHtml;
    } catch (error) {
        let errorTitle = 'Error checking system status';
        let errorMessage = error.message;

        if (error.name === 'TypeError' && error.message.includes('fetch')) {
            errorTitle = 'Network error: Cannot reach server';
            errorMessage = 'Failed to connect to the server. Make sure the server is running and accessible.';
        } else if (error.name === 'AbortError') {
            errorTitle = 'Request cancelled';
            errorMessage = 'The request was cancelled or timed out.';
        } else if (error.name === 'NetworkError') {
            errorTitle = 'Network error';
            errorMessage = 'Network request failed. Check your connection.';
        }

        statusEl.innerHTML = `
            <div style="color: #f44336;">
                <div style="font-weight: 500;">${errorTitle}</div>
                <div style="font-size: 12px; color: #999; margin-top: 5px;">${escapeHtml(errorMessage)}</div>
            </div>
        `;
    }
}

function loadSetupPage() {
    loadCurrentFolder();
    loadStats();
    loadSystemStatus();
    loadVlcOptimizationStatus();
    loadVlcHardwareAccelSetting();

    // Clear dynamic lists to avoid staleness
    const hiddenContainer = document.getElementById('hiddenMoviesList');
    if (hiddenContainer) hiddenContainer.style.display = 'none';

    const recleanStatus = document.getElementById('recleanStatus');
    if (recleanStatus) recleanStatus.style.display = 'none';
}

async function recheckSystemStatus() {
    const statusEl = document.getElementById('systemStatus');
    if (statusEl) {
        statusEl.innerHTML = '<div class="loading">Re-checking system status...</div>';
    }
    await loadSystemStatus();
}

// Local Target Folder Functions

function showLocalTargetDialog() {
    const dialog = document.getElementById('localTargetDialog');
    if (dialog) {
        dialog.classList.add('active');
        const input = document.getElementById('localTargetPathInput');
        if (input) {
            input.focus();
        }
    }
}

function hideLocalTargetDialog() {
    const dialog = document.getElementById('localTargetDialog');
    if (dialog) {
        dialog.classList.remove('active');
    }
}

function browseLocalTargetFolder() {
    // Browser can't open native folder dialog, show instructions
    showStatus('Please type the folder path manually', 'info');
}

async function saveLocalTargetPath() {
    const input = document.getElementById('localTargetPathInput');
    let folderPath = input.value.trim();

    if (!folderPath) {
        showStatus('Please enter a folder path', 'error');
        return;
    }

    // Validate absolute path before normalizing
    if (typeof isValidAbsolutePath === 'function' && !isValidAbsolutePath(folderPath)) {
        showStatus('Path must be absolute (e.g., D:\\LocalMovies or C:\\Offline)', 'error');
        return;
    }

    // Normalize the path (handle /, \, \\)
    if (typeof normalizePath === 'function') {
        folderPath = normalizePath(folderPath);
    }

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ local_target_folder: folderPath })
        });

        const data = await response.json();

        if (response.ok) {
            showStatus('Local target folder updated successfully', 'success');
            loadCurrentFolder();
            hideLocalTargetDialog();
        } else {
            showStatus('Failed to update folder: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (error) {
        showStatus('Failed to update folder: ' + error.message, 'error');
    }
}

// Copy to Local Functions

let activeCopyMovieId = null;
let copyProgressPollInterval = null;

function showCopyProgress(movieName) {
    const toast = document.getElementById('copyProgressToast');
    const titleEl = document.getElementById('copyProgressTitle');
    const messageEl = document.getElementById('copyProgressMessage');
    const barEl = document.getElementById('copyProgressBar');
    const percentEl = document.getElementById('copyProgressPercent');

    if (toast) {
        toast.style.display = 'block';
        if (titleEl) titleEl.textContent = `Copying: ${movieName}`;
        if (messageEl) messageEl.textContent = 'Starting copy...';
        if (barEl) barEl.style.width = '0%';
        if (percentEl) percentEl.textContent = '0%';
    }
}

function updateCopyProgress(progress, message) {
    const barEl = document.getElementById('copyProgressBar');
    const percentEl = document.getElementById('copyProgressPercent');
    const messageEl = document.getElementById('copyProgressMessage');

    if (barEl) barEl.style.width = `${progress}%`;
    if (percentEl) percentEl.textContent = `${Math.round(progress)}%`;
    if (messageEl && message) messageEl.textContent = message;
}

function hideCopyProgress() {
    const toast = document.getElementById('copyProgressToast');
    if (toast) {
        toast.style.display = 'none';
    }
    if (copyProgressPollInterval) {
        clearInterval(copyProgressPollInterval);
        copyProgressPollInterval = null;
    }
    activeCopyMovieId = null;
}

function showCopyComplete(message, isError = false) {
    const titleEl = document.getElementById('copyProgressTitle');
    const messageEl = document.getElementById('copyProgressMessage');
    const barEl = document.getElementById('copyProgressBar');

    if (titleEl) titleEl.textContent = isError ? 'Copy Failed' : 'Copy Complete';
    if (messageEl) messageEl.textContent = message;
    if (barEl) barEl.style.width = isError ? '0%' : '100%';
    if (barEl) barEl.style.background = isError ? '#f44336' : '#4caf50';

    // Auto-hide after 5 seconds
    setTimeout(() => {
        const barEl = document.getElementById('copyProgressBar');
        if (barEl) barEl.style.background = '#4a9eff';  // Reset color
        hideCopyProgress();
    }, 5000);
}

async function copyMovieToLocal(movieId, movieName) {
    if (activeCopyMovieId === movieId) {
        showStatus('Copy already in progress', 'info');
        return;
    }

    activeCopyMovieId = movieId;
    showCopyProgress(movieName);

    try {
        // Start the copy
        const response = await fetch(`/api/movie/${movieId}/copy-to-local`, {
            method: 'POST'
        });

        const data = await response.json();

        if (data.status === 'already_copied') {
            showCopyComplete(data.message);
            return;
        }

        if (data.status === 'complete') {
            showCopyComplete(data.message);
            return;
        }

        if (data.status === 'in_progress') {
            // Start polling for progress
            startCopyProgressPolling(movieId);
            return;
        }

        if (!response.ok) {
            showCopyComplete(data.detail || 'Copy failed', true);
            return;
        }

        showCopyComplete(data.message || 'Copy complete');

    } catch (error) {
        showCopyComplete('Error: ' + error.message, true);
    }
}

function startCopyProgressPolling(movieId) {
    if (copyProgressPollInterval) {
        clearInterval(copyProgressPollInterval);
    }

    copyProgressPollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/movie/${movieId}/copy-status`);
            const data = await response.json();

            if (data.status === 'in_progress') {
                updateCopyProgress(data.progress || 0, data.message);
            } else if (data.status === 'complete' || data.status === 'already_copied') {
                clearInterval(copyProgressPollInterval);
                copyProgressPollInterval = null;
                showCopyComplete(data.message);
            } else if (data.status === 'error') {
                clearInterval(copyProgressPollInterval);
                copyProgressPollInterval = null;
                showCopyComplete(data.message, true);
            }
        } catch (error) {
            console.error('Error polling copy status:', error);
        }
    }, 500);  // Poll every 500ms
}

async function checkCopyStatus(movieId) {
    try {
        const response = await fetch(`/api/movie/${movieId}/copy-status`);
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('Error checking copy status:', error);
        return { status: 'error', message: error.message };
    }
}

// =============================================================================
// VLC Optimization Functions
// =============================================================================

async function loadVlcOptimizationStatus() {
    const statusEl = document.getElementById('vlcOptimizationStatus');
    const applyBtn = document.getElementById('btnApplyVlcOptimization');
    const removeBtn = document.getElementById('btnRemoveVlcOptimization');

    if (!statusEl) return;

    statusEl.innerHTML = '<div class="loading">Checking VLC optimization status...</div>';

    try {
        const response = await fetch('/api/vlc/optimization/status');
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to load status');
        }

        const status = data.status;
        let statusHtml = '';

        if (status.is_optimized) {
            statusHtml = `
                <div style="display: flex; align-items: center; gap: 10px; color: #4caf50;">
                    <span style="font-size: 20px;">✓</span>
                    <div>
                        <div style="font-weight: 500;">System-wide optimization active</div>
                        <div style="font-size: 12px; color: #999; margin-top: 3px;">
                            VLC config optimized: ${status.path ? escapeHtml(status.path) : 'Unknown path'}
                        </div>
                        ${status.backup_exists ? '<div style="font-size: 12px; color: #4caf50; margin-top: 3px;">✓ Backup available for restore</div>' : ''}
                    </div>
                </div>
            `;
            if (applyBtn) applyBtn.style.display = 'none';
            if (removeBtn) removeBtn.style.display = 'inline-block';
        } else if (status.exists) {
            statusHtml = `
                <div style="display: flex; align-items: center; gap: 10px; color: #f0ad4e;">
                    <span style="font-size: 20px;">○</span>
                    <div>
                        <div style="font-weight: 500;">VLC config found - not optimized</div>
                        <div style="font-size: 12px; color: #999; margin-top: 3px;">
                            Path: ${status.path ? escapeHtml(status.path) : 'Unknown'}
                        </div>
                        <div style="font-size: 12px; color: #888; margin-top: 3px;">
                            Command-line optimizations are active for Movie Searcher launches.
                            Click "Apply System-Wide Optimization" to optimize all VLC usage.
                        </div>
                    </div>
                </div>
            `;
            if (applyBtn) applyBtn.style.display = 'inline-block';
            if (removeBtn) removeBtn.style.display = 'none';
        } else {
            statusHtml = `
                <div style="display: flex; align-items: center; gap: 10px; color: #888;">
                    <span style="font-size: 20px;">○</span>
                    <div>
                        <div style="font-weight: 500;">VLC config file not found</div>
                        <div style="font-size: 12px; color: #999; margin-top: 3px;">
                            VLC may not have been run yet. Run VLC once to create its config file, then return here.
                        </div>
                        <div style="font-size: 12px; color: #888; margin-top: 3px;">
                            Command-line optimizations are still active for Movie Searcher launches.
                        </div>
                    </div>
                </div>
            `;
            if (applyBtn) applyBtn.style.display = 'inline-block';
            if (removeBtn) removeBtn.style.display = 'none';
        }

        statusEl.innerHTML = statusHtml;

    } catch (error) {
        statusEl.innerHTML = `
            <div style="color: #f44336;">
                <div style="font-weight: 500;">Error checking VLC optimization status</div>
                <div style="font-size: 12px; color: #999; margin-top: 5px;">${escapeHtml(error.message)}</div>
            </div>
        `;
        if (applyBtn) applyBtn.style.display = 'none';
        if (removeBtn) removeBtn.style.display = 'none';
    }
}

async function applyVlcOptimization() {
    const statusEl = document.getElementById('vlcOptimizationStatus');

    // Confirm with user
    if (!confirm('This will modify VLC\'s global configuration file to optimize startup speed.\n\nChanges will affect ALL VLC usage on your system, not just Movie Searcher.\n\nA backup of your current settings will be created.\n\nContinue?')) {
        return;
    }

    statusEl.innerHTML = '<div class="loading">Applying VLC optimizations...</div>';

    try {
        const response = await fetch('/api/vlc/optimization/apply', {
            method: 'POST'
        });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to apply optimizations');
        }

        if (data.success) {
            showStatus(data.message || 'VLC optimizations applied successfully', 'success');
            loadVlcOptimizationStatus();
        } else {
            throw new Error(data.message || 'Unknown error');
        }

    } catch (error) {
        showStatus('Failed to apply VLC optimizations: ' + error.message, 'error');
        loadVlcOptimizationStatus();
    }
}

async function removeVlcOptimization() {
    const statusEl = document.getElementById('vlcOptimizationStatus');

    if (!confirm('This will restore VLC\'s original configuration.\n\nContinue?')) {
        return;
    }

    statusEl.innerHTML = '<div class="loading">Restoring VLC settings...</div>';

    try {
        const response = await fetch('/api/vlc/optimization/remove', {
            method: 'POST'
        });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to restore settings');
        }

        if (data.success) {
            showStatus(data.message || 'VLC settings restored successfully', 'success');
            loadVlcOptimizationStatus();
        } else {
            throw new Error(data.message || 'Unknown error');
        }

    } catch (error) {
        showStatus('Failed to restore VLC settings: ' + error.message, 'error');
        loadVlcOptimizationStatus();
    }
}

async function saveVlcHardwareAccelSetting(enabled) {
    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                settings: {
                    vlc_hardware_acceleration: enabled
                }
            })
        });

        if (response.ok) {
            showStatus(enabled ? 'Hardware acceleration enabled' : 'Hardware acceleration disabled', 'success');
        } else {
            const data = await response.json();
            showStatus('Failed to save setting: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (error) {
        showStatus('Failed to save setting: ' + error.message, 'error');
    }
}

async function loadVlcHardwareAccelSetting() {
    try {
        const response = await fetch('/api/config');
        const data = await response.json();

        if (response.ok && data.settings) {
            const checkbox = document.getElementById('setupVlcHardwareAccel');
            if (checkbox && data.settings.vlc_hardware_acceleration !== undefined) {
                checkbox.checked = data.settings.vlc_hardware_acceleration;
            }
        }
    } catch (error) {
        console.error('Error loading VLC hardware acceleration setting:', error);
    }
}