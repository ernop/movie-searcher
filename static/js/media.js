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
    
    if (allMedia.length === 0 && !movieId) {
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
    let screenshotButtonRendered = false;
    
    const renderScreenshotButton = () => {
        return `
            <div class="media-item" style="display: flex; align-items: center; justify-content: center; background: #2a2a2a; border: 2px dashed #444; min-width: 120px; min-height: 100px; cursor: pointer;" onclick="showScreenshotConfig(${movieId})">
                <div style="text-align: center; padding: 15px;">
                    <div style="font-size: 20px; color: #666; margin-bottom: 5px;">⚙️</div>
                    <div style="font-size: 12px; color: #999;">Generate<br>Screenshots</div>
                </div>
            </div>
        `;
    };
    
    allMedia.forEach((media, idx) => {
        const isScreenshot = media.type === 'screenshot';
        const timestamp = media.timestamp;
        const timestampLabel = timestamp !== null && timestamp !== undefined 
            ? formatTimestamp(timestamp) 
            : '';
        
        // Add break and screenshot button when transitioning from images to screenshots (or at first screenshot)
        if (isScreenshot && !hasSeenScreenshots) {
            if (hasSeenImages) {
                galleryHtml += '<div style="width: 100%; flex-basis: 100%;"></div>';
            }
            // Add screenshot button on its own line before all screenshots
            if (!screenshotButtonRendered && movieId) {
                galleryHtml += renderScreenshotButton();
                galleryHtml += '<div style="width: 100%; flex-basis: 100%;"></div>';
                screenshotButtonRendered = true;
            }
            hasSeenScreenshots = true;
        }
        if (!isScreenshot) {
            hasSeenImages = true;
        } else {
            hasSeenScreenshots = true;
        }
        
        // Render the media item
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
                     onerror="console.error('Failed to load image:', '${escapeJsString(imageSrc)}'); this.style.display='none'">
            </div>
        `;
    });
    
    // If no screenshots were found (or media array was empty), add button at the end
    if (!screenshotButtonRendered && movieId) {
        // Add break if we have images but no screenshots yet
        if (hasSeenImages) {
            galleryHtml += '<div style="width: 100%; flex-basis: 100%;"></div>';
        }
        galleryHtml += renderScreenshotButton();
    }
    
    galleryHtml += '</div>';
    return galleryHtml;
}

async function updateScreenshotsGallery(movieId, existingScreenshotIds, updateProgress = null) {
    // Incrementally update the screenshots gallery without reloading the entire page
    try {
        const response = await fetch(`/api/movie/${movieId}/screenshots`);
        if (!response.ok) return false;
        
        const data = await response.json();
        const allScreenshots = data.screenshots || [];
        
        // If we have a progress callback, call it with the current count
        if (updateProgress && typeof updateProgress === 'function') {
            updateProgress(allScreenshots.length);
        }
        
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
            
            // Find DOM insertion point: screenshots go AFTER the button/progress card and its break
            // New layout: Images -> Break -> Button/Progress -> Break -> Screenshots
            let insertBeforeElement = null;
            let insertAfterElement = null;
            let foundFirstScreenshot = false;
            let hasImages = false;
            
            // Find the button/progress card and existing screenshots
            let buttonOrProgress = null;
            let buttonBreak = null;
            
            for (const el of existingItems) {
                const elOnclick = el.getAttribute('onclick');
                const elTs = getTimestampFromElement(el);
                const isElScreenshot = elTs !== null || (elOnclick && elOnclick.includes(', true,'));
                
                // Check if it's the screenshot generator button or progress card
                const isButton = (elOnclick && elOnclick.includes('showScreenshotConfig')) || 
                               el.id === 'screenshotProgressCard';
                
                if (isButton) {
                    buttonOrProgress = el;
                    // Find the break element after the button (next sibling with flex-basis: 100%)
                    let next = el.nextElementSibling;
                    if (next && next.style.flexBasis === '100%') {
                        buttonBreak = next;
                    }
                } else if (isElScreenshot) {
                    foundFirstScreenshot = true;
                    // Find insertion point: first screenshot with timestamp > this timestamp
                    if (elTs !== null && timestamp !== null && timestamp !== undefined && elTs > timestamp) {
                        insertBeforeElement = el;
                        break;
                    }
                    // Track last screenshot for appending after
                    insertAfterElement = el;
                } else {
                    // This is an image
                    hasImages = true;
                }
            }
            
            // If we didn't find a specific insertion point, append after the break following the button
            if (!insertBeforeElement && !foundFirstScreenshot && buttonBreak) {
                insertAfterElement = buttonBreak;
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
                         onerror="console.error('Failed to load image:', '${escapeJsString(imageSrc)}'); this.style.display='none'">
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
            } else if (insertAfterElement) {
                // Insert after the break following the button, or after the last screenshot
                insertAfterElement.insertAdjacentHTML('afterend', newItemHtml);
                // Add to existingItems
                const newEl = insertAfterElement.nextElementSibling;
                if (newEl) {
                    const insertIdx = existingItems.indexOf(insertAfterElement);
                    existingItems.splice(insertIdx + 1, 0, newEl);
                }
            } else {
                // Fallback: append to end of gallery
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
        
        const response = await fetch('/api/launch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie_id: movieId,
                subtitle_path: subtitlePath || null,
                close_existing_vlc: true,
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

function setScreenshotInterval(val) {
    const input = document.getElementById('screenshotConfigInterval');
    if (input) {
        input.value = val;
        // Visual feedback
        input.style.borderColor = '#4a9eff';
        setTimeout(() => input.style.borderColor = '#3a3a3a', 300);
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
            const queuedCount = data.queued || 0;
            showStatus('Queued ' + queuedCount + ' screenshots', 'success');
            closeScreenshotConfig();
            
            // CLEAR ALL EXISTING SCREENSHOTS FROM DOM - regeneration means fresh start
            const container = document.getElementById('movieDetailsContainer');
            if (container) {
                const gallery = container.querySelector('.media-gallery');
                if (gallery) {
                    // Find the generator button before removing it (so we know where to insert progress)
                    let generatorButton = null;
                    let hasImages = false;
                    
                    // Remove all screenshot elements (keep images)
                    const allItems = Array.from(gallery.querySelectorAll('.media-item'));
                    allItems.forEach(item => {
                        const onclick = item.getAttribute('onclick');
                        // Check if it's a screenshot (onclick contains ", true," for screenshots)
                        if (onclick && onclick.includes(', true,')) {
                            item.remove();
                        } else if (onclick && onclick.includes('showScreenshotConfig')) {
                            generatorButton = item;
                        } else {
                            hasImages = true;
                        }
                    });
                    
                    // Replace generator button with progress card
                    const progressCard = document.createElement('div');
                    progressCard.id = 'screenshotProgressCard';
                    progressCard.className = 'media-item';
                    progressCard.style.cssText = 'display: flex; align-items: center; justify-content: center; background: #2a2a2a; border: 2px solid #4a9eff; min-width: 120px; min-height: 100px;';
                    progressCard.innerHTML = `
                        <div style="text-align: center; padding: 15px;">
                            <div style="font-size: 14px; color: #4a9eff; margin-bottom: 5px; font-weight: bold;">Generating...</div>
                            <div style="font-size: 12px; color: #ccc;"><span id="gen-count">0</span> / ${queuedCount}</div>
                        </div>
                    `;
                    
                    if (generatorButton) {
                        generatorButton.replaceWith(progressCard);
                    } else {
                        // If no button found (e.g. maybe hidden?), append to gallery
                        // If images exist but no button, we might need a break
                        if (hasImages && !gallery.querySelector('div[style*="flex-basis: 100%"]')) {
                            gallery.insertAdjacentHTML('beforeend', '<div style="width: 100%; flex-basis: 100%;"></div>');
                        }
                        gallery.appendChild(progressCard);
                    }
                    
                    // Update currentMediaArray - remove screenshots, keep images
                    currentMediaArray = currentMediaArray.filter(media => media.type !== 'screenshot');
                }
            }
            
            // Track existing screenshot IDs from API response (should be empty after deletion)
            const existingScreenshotIds = new Set((data.screenshots || []).map(s => s.id));
            
            // Poll for new screenshots (lightweight endpoint, incremental updates)
            let polls = 0;
            const maxPolls = 60; // Poll for up to 120 seconds (60 * 2s)
            const pollIntervalMs = 2000;
            
            // Function to restore button
            const restoreButton = () => {
                const progressCard = document.getElementById('screenshotProgressCard');
                if (progressCard) {
                    const btnDiv = document.createElement('div');
                    btnDiv.className = 'media-item';
                    btnDiv.style.cssText = 'display: flex; align-items: center; justify-content: center; background: #2a2a2a; border: 2px dashed #444; min-width: 120px; min-height: 100px; cursor: pointer;';
                    btnDiv.setAttribute('onclick', `showScreenshotConfig(${movieId})`);
                    btnDiv.innerHTML = `
                        <div style="text-align: center; padding: 15px;">
                            <div style="font-size: 20px; color: #666; margin-bottom: 5px;">⚙️</div>
                            <div style="font-size: 12px; color: #999;">Generate<br>Screenshots</div>
                        </div>
                    `;
                    progressCard.replaceWith(btnDiv);
                }
            };
            
            const iv = setInterval(async () => {
                // Stop polling if user navigated away from movie details page
                const pageMovieDetails = document.getElementById('pageMovieDetails');
                if (!pageMovieDetails || !pageMovieDetails.classList.contains('active')) {
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
                
                const container = document.getElementById('movieDetailsContainer');
                if (!container || !container.innerHTML) {
                    clearInterval(iv);
                    return;
                }

                // Update gallery incrementally
                const hadNewScreenshots = await updateScreenshotsGallery(movieId, existingScreenshotIds, (count) => {
                    const countEl = document.getElementById('gen-count');
                    if (countEl) countEl.textContent = count;
                });
                
                polls += 1;
                
                // Stop logic: if we have >= queued count, we are done
                // Or timeout
                const currentCount = parseInt(document.getElementById('gen-count')?.textContent || '0');
                if (currentCount >= queuedCount && queuedCount > 0) {
                     // Give it one more poll to ensure everything is settled, then stop
                     if (polls > 2) { // Minimum polls
                        clearInterval(iv);
                        setTimeout(restoreButton, 1000);
                     }
                } else if (polls >= maxPolls) {
                    clearInterval(iv);
                    restoreButton();
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
