// Media Gallery and Overlay Functions

let currentMediaArray = [];
let currentMediaIndex = -1;
let currentVideoPath = null;
let currentMovieId = null;

function renderMediaGallery(images, screenshots, itemPath, movieId) {
    const allMedia = [];
    
    // Helper to extract filename from path
    function getFilename(path) {
        if (!path) return null;
        // Handle both Windows and Unix paths
        const parts = path.replace(/\\/g, '/').split('/');
        return parts[parts.length - 1];
    }
    
    // Add folder images first - images are now objects with {id, path, url_path}
    // Images are in movies folder, served as static files
    if (images && images.length > 0) {
        images.forEach((img, idx) => {
            const imgObj = typeof img === 'object' ? img : {id: null, path: img, url_path: null}; // Handle both old (path string) and new (object) formats
            allMedia.push({
                id: imgObj.id,
                path: imgObj.path,
                url_path: imgObj.url_path || null,
                type: 'image',
                label: idx === 0 ? 'Poster' : `Image ${idx + 1}`,
                timestamp: null
            });
        });
    }
    
    // Add screenshots - screenshots are now objects with {id, path, timestamp_seconds}
    // Screenshots are in controlled /screenshots directory, use path directly
    if (screenshots && screenshots.length > 0) {
        screenshots.forEach((screenshot, idx) => {
            const screenshotObj = typeof screenshot === 'object' ? screenshot : {id: null, path: screenshot, timestamp_seconds: null}; // Handle both old (path string) and new (object) formats
            allMedia.push({
                id: screenshotObj.id,
                path: screenshotObj.path,
                filename: getFilename(screenshotObj.path), // Extract filename for static serving
                type: 'screenshot',
                label: `Screenshot ${idx + 1}`,
                timestamp: screenshotObj.timestamp_seconds
            });
        });
    }
    
    if (allMedia.length === 0) {
        currentMediaArray = [];
        return '';
    }
    
    // Store media array globally for navigation
    currentMediaArray = allMedia;
    currentVideoPath = itemPath;
    currentMovieId = movieId;
    
    let galleryHtml = '<div class="media-gallery">';
    
    // Track if we've seen images and screenshots to add break between them
    let hasSeenImages = false;
    let hasSeenScreenshots = false;
    
    allMedia.forEach((media, idx) => {
        const isScreenshot = media.type === 'screenshot';
        const timestamp = media.timestamp;
        const timestampLabel = timestamp !== null && timestamp !== undefined 
            ? formatTimestamp(timestamp) 
            : '';
        
        // Add break when transitioning from images to screenshots
        if (isScreenshot && !hasSeenScreenshots && hasSeenImages) {
            galleryHtml += '<div style="width: 100%; flex-basis: 100%;"></div>';
            hasSeenScreenshots = true;
        }
        if (!isScreenshot) {
            hasSeenImages = true;
        } else {
            hasSeenScreenshots = true;
        }
        
        if (idx === 0) {
            // First item gets the "Get more" button
            let launchBtnHtml = '';
            if (isScreenshot && movieId) {
                if (timestamp !== null && timestamp !== undefined) {
                    launchBtnHtml = `<button class="media-item-launch-btn" onclick="event.stopPropagation(); jumpToVideo(${movieId}, ${timestamp});">Launch</button>`;
                } else {
                    launchBtnHtml = `<button class="media-item-launch-btn" onclick="event.stopPropagation(); launchMovie(${movieId});">Launch</button>`;
                }
            }
            // Use API endpoints when IDs available for reliability, fallback to static files
            const imageSrc = isScreenshot 
                ? (media.id ? `/api/screenshot/${media.id}` : (media.filename ? `/screenshots/${encodeURIComponent(media.filename)}` : ''))
                : (media.path ? (media.path.includes('screenshots') ? `/screenshots/${encodeURIComponent(getFilename(media.path))}` : `/movies/${encodeURIComponent(media.path)}`) : '');
            galleryHtml += `
                <div class="media-item" onclick="showMediaOverlay(${media.id || 'null'}, ${isScreenshot ? 'true' : 'false'}, ${timestamp !== null ? timestamp : 'null'}, ${movieId || 'null'}, ${idx})" style="position: relative;">
                    <button 
                        class="get-more-btn" 
                        title="Get more screenshots" 
                        onclick="event.stopPropagation(); showScreenshotConfig(${movieId || 'null'});" 
                        style="position: absolute; top: 8px; right: 8px; font-size: 11px; padding: 4px 8px; opacity: 0.85; z-index: 10;">
                        Get more
                    </button>
                    ${launchBtnHtml}
                    ${isScreenshot && timestampLabel ? `<div class="screenshot-timestamp" style="position: absolute; bottom: 8px; left: 8px; background: rgba(0,0,0,0.7); color: #fff; padding: 4px 8px; border-radius: 4px; font-size: 12px;">${escapeHtml(timestampLabel)}</div>` : ''}
                    <img src="${imageSrc}" 
                         alt="" 
                         loading="lazy"
                         onerror="console.error('Failed to load image:', '${escapeJsString(imageSrc)}'); this.style.display='none'"
                         onload="console.log('Loaded image:', '${escapeJsString(imageSrc)}')">
                </div>
            `;
        } else {
            // All other items
            let launchBtnHtml = '';
            if (isScreenshot && movieId) {
                if (timestamp !== null && timestamp !== undefined) {
                    launchBtnHtml = `<button class="media-item-launch-btn" onclick="event.stopPropagation(); jumpToVideo(${movieId}, ${timestamp});">Launch</button>`;
                } else {
                    launchBtnHtml = `<button class="media-item-launch-btn" onclick="event.stopPropagation(); launchMovie(${movieId});">Launch</button>`;
                }
            }
            // Use API endpoints when IDs available for reliability, fallback to static files
            const imageSrc = isScreenshot 
                ? (media.id ? `/api/screenshot/${media.id}` : (media.filename ? `/screenshots/${encodeURIComponent(media.filename)}` : ''))
                : (media.path ? (media.path.includes('screenshots') ? `/screenshots/${encodeURIComponent(getFilename(media.path))}` : `/movies/${encodeURIComponent(media.path)}`) : '');
            galleryHtml += `
                <div class="media-item" onclick="showMediaOverlay(${media.id || 'null'}, ${isScreenshot ? 'true' : 'false'}, ${timestamp !== null ? timestamp : 'null'}, ${movieId || 'null'}, ${idx})" style="position: relative;">
                    ${launchBtnHtml}
                    ${isScreenshot && timestampLabel ? `<div class="screenshot-timestamp" style="position: absolute; bottom: 8px; left: 8px; background: rgba(0,0,0,0.7); color: #fff; padding: 4px 8px; border-radius: 4px; font-size: 12px;">${escapeHtml(timestampLabel)}</div>` : ''}
                    <img src="${imageSrc}" 
                         alt="" 
                         loading="lazy"
                         onerror="console.error('Failed to load image:', '${escapeJsString(imageSrc)}'); this.style.display='none'"
                         onload="console.log('Loaded image:', '${escapeJsString(imageSrc)}')">
                </div>
            `;
        }
    });
    
    galleryHtml += '</div>';
    return galleryHtml;
}

async function updateScreenshotsGallery(movieId, existingScreenshotIds) {
    // Incrementally update the screenshots gallery without reloading the entire page
    try {
        const response = await fetch(`/api/movie/${movieId}/screenshots`);
        if (!response.ok) return false;
        
        const data = await response.json();
        const allScreenshots = data.screenshots || [];
        
        // Find the media gallery container
        const container = document.getElementById('movieDetailsContainer');
        if (!container) return false;
        const gallery = container.querySelector('.media-gallery');
        if (!gallery) return false;
        
        // Get current screenshot IDs from DOM to detect deletions
        const domScreenshotIds = new Set();
        const domScreenshotElements = [];
        gallery.querySelectorAll('.media-item').forEach(item => {
            const onclick = item.getAttribute('onclick');
            if (onclick && onclick.includes(', true,')) {
                // Extract screenshot ID from onclick="showMediaOverlay(id, true, ...)"
                const match = onclick.match(/showMediaOverlay\((\d+),\s*true/);
                if (match) {
                    const id = parseInt(match[1]);
                    domScreenshotIds.add(id);
                    domScreenshotElements.push({element: item, id: id});
                }
            }
        });
        
        // Remove screenshots from DOM that are no longer in database
        const apiScreenshotIds = new Set(allScreenshots.map(s => s.id));
        domScreenshotElements.forEach(({element, id}) => {
            if (!apiScreenshotIds.has(id)) {
                element.remove();
                // Remove from currentMediaArray
                const idx = currentMediaArray.findIndex(m => m.id === id && m.type === 'screenshot');
                if (idx >= 0) {
                    currentMediaArray.splice(idx, 1);
                }
            }
        });
        
        // Find new screenshots (not in existing set)
        const newScreenshots = allScreenshots.filter(s => !existingScreenshotIds.has(s.id));
        if (newScreenshots.length === 0) {
            return false; // No new screenshots
        }
        
        // Helper to extract filename from path
        function getFilename(path) {
            if (!path) return null;
            const parts = path.replace(/\\/g, '/').split('/');
            return parts[parts.length - 1];
        }
        
        // Sort new screenshots by timestamp (nulls last, matching API order)
        newScreenshots.sort((a, b) => {
            const tsA = a.timestamp_seconds;
            const tsB = b.timestamp_seconds;
            if (tsA === null || tsA === undefined) return 1; // nulls last
            if (tsB === null || tsB === undefined) return -1;
            return tsA - tsB;
        });
        
        // Get DOM elements AFTER deletions (so we only see remaining screenshots)
        const existingItems = Array.from(gallery.querySelectorAll('.media-item'));
        
        // Helper to extract timestamp from DOM element
        function getTimestampFromElement(el) {
            const onclick = el.getAttribute('onclick');
            if (!onclick || !onclick.includes(', true,')) return null; // Not a screenshot
            const match = onclick.match(/showMediaOverlay\([^,]+,\s*true,\s*([^,]+)/);
            if (match && match[1] !== 'null') {
                const ts = parseFloat(match[1]);
                return isNaN(ts) ? null : ts;
            }
            return null;
        }
        
        // Insert each new screenshot in the correct position
        newScreenshots.forEach((screenshot) => {
            const filename = getFilename(screenshot.path);
            if (!filename) return;
            
            const timestamp = screenshot.timestamp_seconds;
            const timestampLabel = timestamp !== null && timestamp !== undefined 
                ? formatTimestamp(timestamp) 
                : '';
            
            // Prefer API endpoint by screenshot ID if available
            const imageSrc = screenshot.id 
                ? `/api/screenshot/${screenshot.id}` 
                : `/screenshots/${encodeURIComponent(filename)}`;
            
            // Find DOM insertion point: first screenshot element with timestamp > this timestamp
            // Also check if we need to add a break before the first screenshot (if images exist)
            let insertBeforeElement = null;
            let needsBreak = false;
            let foundFirstScreenshot = false;
            for (const el of existingItems) {
                const elOnclick = el.getAttribute('onclick');
                const elTs = getTimestampFromElement(el);
                const isElScreenshot = elTs !== null || (elOnclick && elOnclick.includes(', true,'));
                if (isElScreenshot) {
                    foundFirstScreenshot = true;
                    if (elTs !== null && timestamp !== null && timestamp !== undefined && elTs > timestamp) {
                        insertBeforeElement = el;
                        break;
                    }
                } else {
                    // This is an image - if we haven't seen a screenshot yet, we'll need a break
                    if (!foundFirstScreenshot) {
                        needsBreak = true;
                    }
                }
            }
            
            // Find insertion index in currentMediaArray (for navigation)
            let insertIndex = currentMediaArray.length;
            for (let i = 0; i < currentMediaArray.length; i++) {
                const item = currentMediaArray[i];
                if (item.type === 'screenshot') {
                    const itemTs = item.timestamp;
                    if (timestamp !== null && timestamp !== undefined) {
                        if (itemTs !== null && itemTs !== undefined && itemTs >= timestamp) {
                            insertIndex = i;
                            break;
                        }
                    } else if (itemTs === null || itemTs === undefined) {
                        insertIndex = i;
                        break;
                    }
                }
            }
            if (insertIndex === currentMediaArray.length) {
                // Find last screenshot index
                for (let i = currentMediaArray.length - 1; i >= 0; i--) {
                    if (currentMediaArray[i].type === 'screenshot') {
                        insertIndex = i + 1;
                        break;
                    }
                }
            }
            
            // Insert into currentMediaArray
            const newMediaItem = {
                id: screenshot.id,
                path: screenshot.path,
                filename: filename,
                type: 'screenshot',
                label: `Screenshot ${insertIndex + 1}`,
                timestamp: timestamp
            };
            currentMediaArray.splice(insertIndex, 0, newMediaItem);
            
            // Create launch button if needed
            let launchBtnHtml = '';
            if (movieId) {
                if (timestamp !== null && timestamp !== undefined) {
                    launchBtnHtml = `<button class="media-item-launch-btn" onclick="event.stopPropagation(); jumpToVideo(${movieId}, ${timestamp});">Launch</button>`;
                } else {
                    launchBtnHtml = `<button class="media-item-launch-btn" onclick="event.stopPropagation(); launchMovie(${movieId});">Launch</button>`;
                }
            }
            
            // Create the new gallery item HTML
            const newItemHtml = `
                <div class="media-item" onclick="showMediaOverlay(${screenshot.id}, true, ${timestamp !== null ? timestamp : 'null'}, ${movieId || 'null'}, ${insertIndex})" style="position: relative;">
                    ${launchBtnHtml}
                    ${timestampLabel ? `<div class="screenshot-timestamp" style="position: absolute; bottom: 8px; left: 8px; background: rgba(0,0,0,0.7); color: #fff; padding: 4px 8px; border-radius: 4px; font-size: 12px;">${escapeHtml(timestampLabel)}</div>` : ''}
                    <img src="${imageSrc}" 
                         alt="" 
                         loading="lazy"
                         onerror="console.error('Failed to load image:', '${escapeJsString(imageSrc)}'); this.style.display='none'"
                         onload="console.log('Loaded image:', '${escapeJsString(imageSrc)}')">
                </div>
            `;
            
            // Insert into DOM
            if (insertBeforeElement) {
                insertBeforeElement.insertAdjacentHTML('beforebegin', newItemHtml);
                // Add to existingItems so next iteration can find it
                const newEl = insertBeforeElement.previousElementSibling;
                if (newEl) {
                    const insertIdx = existingItems.indexOf(insertBeforeElement);
                    existingItems.splice(insertIdx, 0, newEl);
                }
            } else {
                // If this is the first screenshot and images exist, add break first
                if (needsBreak && !foundFirstScreenshot) {
                    gallery.insertAdjacentHTML('beforeend', '<div style="width: 100%; flex-basis: 100%;"></div>');
                }
                gallery.insertAdjacentHTML('beforeend', newItemHtml);
                // Add to existingItems
                const newEl = gallery.lastElementChild;
                if (newEl) existingItems.push(newEl);
            }
            
            // Track this screenshot as existing
            existingScreenshotIds.add(screenshot.id);
        });
        
        return true; // Had new screenshots
    } catch (error) {
        console.error('Error updating screenshots gallery:', error);
        return false;
    }
}

function formatTimestamp(seconds) {
    if (seconds === null || seconds === undefined) return '';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }
    return `${minutes}:${String(secs).padStart(2, '0')}`;
}

function showMediaOverlay(mediaId, isScreenshot, timestampSeconds, movieId, index) {
    const overlay = document.getElementById('mediaOverlay');
    const img = document.getElementById('mediaOverlayImage');
    let controls = document.getElementById('mediaOverlayControls');
    if (!controls) {
        controls = document.createElement('div');
        controls.id = 'mediaOverlayControls';
        controls.style.cssText = 'position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); z-index: 1000;';
        overlay.appendChild(controls);
    }
    
    // Store current index for navigation
    currentMediaIndex = index !== undefined ? index : -1;
    
    // Prefer API endpoints when IDs available for reliability, fallback to static files
    let imageUrl = '';
    if (isScreenshot) {
        // Screenshots: prefer API endpoint by ID, fallback to filename-based endpoint
        if (mediaId) {
            imageUrl = `/api/screenshot/${mediaId}`;
        } else if (currentMediaArray[currentMediaIndex] && currentMediaArray[currentMediaIndex].filename) {
            imageUrl = `/screenshots/${encodeURIComponent(currentMediaArray[currentMediaIndex].filename)}`;
        }
    } else {
        // Images: use path directly
        if (currentMediaArray[currentMediaIndex] && currentMediaArray[currentMediaIndex].path) {
            const path = currentMediaArray[currentMediaIndex].path;
            if (path.includes('screenshots')) {
                imageUrl = `/screenshots/${encodeURIComponent(getFilename(path))}`;
            } else {
                imageUrl = `/movies/${encodeURIComponent(path)}`;
            }
        }
    }
    img.src = imageUrl;
    
    // Show controls for screenshots with timestamps
    if (timestampSeconds !== null && timestampSeconds !== undefined && movieId) {
        controls.innerHTML = `
            <div style="display: flex; gap: 10px; align-items: center;">
                <span style="color: #fff; font-size: 14px; background: rgba(0,0,0,0.7); padding: 8px 12px; border-radius: 4px;">${formatTimestamp(timestampSeconds)}</span>
                <button onclick="event.stopPropagation(); jumpToVideo(${movieId}, ${timestampSeconds});" 
                        style="background: #4a9eff; color: #fff; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px;">
                    Jump to Video
                </button>
            </div>
        `;
        controls.style.display = 'block';
    } else {
        controls.style.display = 'none';
    }
    
    overlay.classList.add('active');
    
    // Add keyboard event listener if not already added
    if (!overlay.dataset.keyboardListener) {
        overlay.dataset.keyboardListener = 'true';
        document.addEventListener('keydown', handleMediaOverlayKeydown);
    }
}

function handleMediaOverlayKeydown(event) {
    const overlay = document.getElementById('mediaOverlay');
    if (!overlay.classList.contains('active')) {
        return;
    }
    
    // Only handle if overlay is active and we have media array
    if (currentMediaArray.length === 0 || currentMediaIndex < 0) {
        return;
    }
    
    if (event.key === 'ArrowLeft') {
        event.preventDefault();
        navigateMedia(-1);
    } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        navigateMedia(1);
    } else if (event.key === 'Escape') {
        event.preventDefault();
        closeMediaOverlay();
    }
}

function navigateMedia(direction) {
    if (currentMediaArray.length === 0 || currentMediaIndex < 0) {
        return;
    }
    
    const newIndex = currentMediaIndex + direction;
    if (newIndex < 0 || newIndex >= currentMediaArray.length) {
        return;
    }
    
    const media = currentMediaArray[newIndex];
    showMediaOverlay(media.id, media.type === 'screenshot', media.timestamp, currentMovieId, newIndex);
}

function closeMediaOverlay() {
    const overlay = document.getElementById('mediaOverlay');
    overlay.classList.remove('active');
    currentMediaIndex = -1;
}

async function jumpToVideo(movieId, timestampSeconds) {
    try {
        const subtitleSelect = document.getElementById(`subtitle-${movieId}`);
        const subtitlePath = subtitleSelect ? subtitleSelect.value : null;
        const closeExistingVlc = document.getElementById('setupCloseExistingVlc').checked;
        
        const response = await fetch('/api/launch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                subtitle_path: subtitlePath || null,
                close_existing_vlc: closeExistingVlc,
                start_time: timestampSeconds
            })
        });
        
        const data = await response.json();
        if (response.ok && data.status === 'launched') {
            showStatus('Video launched at ' + formatTimestamp(timestampSeconds), 'success');
            closeMediaOverlay();
        } else {
            showStatus('Failed to launch video: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (error) {
        showStatus('Failed to launch video: ' + error.message, 'error');
    }
}

async function showScreenshotConfig(movieId) {
    if (!movieId) {
        showStatus('Movie ID not available', 'error');
        return;
    }
    const dialog = document.getElementById('screenshotConfigDialog');
    const movieIdInput = document.getElementById('screenshotConfigMovieId');
    movieIdInput.value = movieId;
    
    // Load available subtitles
    try {
        const subtitles = await loadSubtitles(movieId);
        const subtitleSelect = document.getElementById('screenshotConfigSubtitle');
        
        // Clear existing options except "No subtitle"
        subtitleSelect.innerHTML = '<option value="">No subtitle</option>';
        
        // Add subtitle options
        subtitles.forEach(sub => {
            const option = document.createElement('option');
            option.value = sub.path;
            option.textContent = sub.name;
            subtitleSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading subtitles:', error);
        // Still show dialog even if subtitle loading fails
    }
    
    dialog.style.display = 'flex';
}

function closeScreenshotConfig() {
    const dialog = document.getElementById('screenshotConfigDialog');
    dialog.style.display = 'none';
}

async function generateScreenshots() {
    const movieId = parseInt(document.getElementById('screenshotConfigMovieId').value);
    if (!movieId) {
        showStatus('Movie ID is required', 'error');
        return;
    }
    const interval = parseFloat(document.getElementById('screenshotConfigInterval').value) || 3;
    const subtitleSelect = document.getElementById('screenshotConfigSubtitle');
    const subtitlePath = subtitleSelect && subtitleSelect.value ? subtitleSelect.value : null;
    
    try {
        const resp = await fetch('/api/movie/screenshots/interval', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                movie_id: movieId, 
                every_minutes: interval, 
                subtitle_path: subtitlePath
            })
        });
        const data = await resp.json();
        if (!resp.ok) {
            showStatus('Failed to queue screenshots: ' + (data.detail || 'error'), 'error');
        } else {
            showStatus('Queued ' + (data.queued || 0) + ' screenshots', 'success');
            closeScreenshotConfig();
            
            // CLEAR ALL EXISTING SCREENSHOTS FROM DOM - regeneration means fresh start
            const container = document.getElementById('movieDetailsContainer');
            if (container) {
                const gallery = container.querySelector('.media-gallery');
                if (gallery) {
                    // Remove all screenshot elements (keep images)
                    const allItems = Array.from(gallery.querySelectorAll('.media-item'));
                    allItems.forEach(item => {
                        const onclick = item.getAttribute('onclick');
                        // Check if it's a screenshot (onclick contains ", true," for screenshots)
                        if (onclick && onclick.includes(', true,')) {
                            item.remove();
                        }
                    });
                    
                    // Update currentMediaArray - remove screenshots, keep images
                    currentMediaArray = currentMediaArray.filter(media => media.type !== 'screenshot');
                }
            }
            
            // Track existing screenshot IDs from API response (should be empty after deletion)
            const existingScreenshotIds = new Set((data.screenshots || []).map(s => s.id));
            
            // Initial update after a short delay
            setTimeout(() => {
                updateScreenshotsGallery(movieId, existingScreenshotIds);
            }, 1500);
            
            // Poll for new screenshots (lightweight endpoint, incremental updates)
            let polls = 0;
            const maxPolls = 30; // Poll for up to 60 seconds (30 * 2s)
            const pollIntervalMs = 2000;
            const iv = setInterval(async () => {
                // Stop polling if user navigated away from movie details page
                const pageMovieDetails = document.getElementById('pageMovieDetails');
                if (!pageMovieDetails || !pageMovieDetails.classList.contains('active')) {
                    clearInterval(iv);
                    return;
                }
                const container = document.getElementById('movieDetailsContainer');
                if (!container || !container.innerHTML) {
                    clearInterval(iv);
                    return;
                }
                // Verify we're still on the same movie by checking route
                const route = getRoute();
                const expectedRoute = `/movie/${movieId}`;
                if (!route.startsWith(expectedRoute)) {
                    clearInterval(iv);
                    return;
                }
                
                // Update gallery incrementally
                const hadNewScreenshots = await updateScreenshotsGallery(movieId, existingScreenshotIds);
                
                polls += 1;
                // Stop early if no new screenshots for several polls (likely done)
                if (!hadNewScreenshots && polls >= 5) {
                    clearInterval(iv);
                } else if (polls >= maxPolls) {
                    clearInterval(iv);
                }
            }, pollIntervalMs);
        }
    } catch (e) {
        showStatus('Failed to queue screenshots: ' + e.message, 'error');
    }
}


async function displayResults(items) {
    // Backward-compatible full replace render
    if (items.length === 0) {
        results.innerHTML = '<div class="empty-state">No results found</div>';
        return;
    }
    results.innerHTML = '<div class="movie-grid">' + items.map(item => createMovieCard(item)).join('') + '</div>';
    initAllStarRatings();
}

