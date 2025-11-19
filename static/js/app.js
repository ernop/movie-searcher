// Application initialization

window.addEventListener('load', startCurrentlyPlayingPolling);
window.addEventListener('load', updateScreenshotProcessorStatus);

// Load stats and folder path on page load
loadStats();
loadLanguageFilters();
loadCurrentFolder();
updateCurrentlyPlaying();

// Save launch with subtitles setting when checkbox changes
document.addEventListener('DOMContentLoaded', () => {
    const launchWithSubtitlesOnEl = document.getElementById('setupLaunchWithSubtitlesOn');
    if (launchWithSubtitlesOnEl) {
        launchWithSubtitlesOnEl.addEventListener('change', async () => {
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        settings: {
                            launch_with_subtitles_on: launchWithSubtitlesOnEl.checked
                        }
                    })
                });
                if (response.ok) {
                    showStatus('Setting saved successfully', 'success');
                } else {
                    const data = await response.json();
                    showStatus('Failed to save setting: ' + (data.detail || 'Unknown error'), 'error');
                }
            } catch (error) {
                showStatus('Failed to save setting: ' + error.message, 'error');
            }
        });
    }
});

