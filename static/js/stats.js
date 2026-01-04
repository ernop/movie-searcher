// Stats page functionality

// Load VLC flags status on page load
async function loadVlcFlagsStatus() {
    const statusDiv = document.getElementById('vlcFlagsStatus');
    if (!statusDiv) return;
    
    try {
        const response = await fetch('/api/vlc/optimization/flags');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        let html = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <span><strong>${data.safe_count}</strong> of ${data.total_available} flags tested safe</span>
                <span style="color: ${data.safe_count > 0 ? '#4caf50' : '#888'};">
                    ${data.safe_count > 0 ? '✓ Optimizations active' : 'No optimizations (run test)'}
                </span>
            </div>
        `;
        
        if (data.available_flags && data.available_flags.length > 0) {
            html += '<div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px;">';
            for (const flag of data.available_flags) {
                const color = flag.safe ? '#4caf50' : '#666';
                const icon = flag.safe ? '✓' : '○';
                html += `
                    <span style="background: #2a2a2a; padding: 4px 8px; border-radius: 4px; font-size: 11px; color: ${color};" title="${flag.description}">
                        ${icon} ${flag.flag.replace('--', '')}
                    </span>
                `;
            }
            html += '</div>';
        }
        
        // Hardware acceleration status
        if (data.hw_acceleration) {
            const hw = data.hw_acceleration;
            html += `
                <div style="margin-top: 15px; padding-top: 10px; border-top: 1px solid #3a3a3a;">
                    <strong>Hardware Acceleration:</strong> 
                    <span style="color: ${hw.enabled ? (hw.tested_safe ? '#4caf50' : '#ff9800') : '#888'};">
                        ${hw.enabled ? (hw.tested_safe ? '✓ Enabled & tested safe' : '⚠ Enabled but not tested') : 'Disabled'}
                    </span>
                </div>
            `;
        }
        
        statusDiv.innerHTML = html;
    } catch (error) {
        statusDiv.innerHTML = `<div style="color: #f44336;">Error loading flags: ${error.message}</div>`;
    }
}

async function testVlcFlags() {
    const btn = document.getElementById('testFlagsBtn');
    const statusDiv = document.getElementById('vlcFlagsStatus');
    
    if (btn) btn.disabled = true;
    if (statusDiv) statusDiv.innerHTML = '<div class="loading">Testing VLC flags... This may take 15-30 seconds.</div>';
    
    try {
        const response = await fetch('/api/vlc/optimization/flags/test', { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        showStatus(`Tested ${data.total_tested} flags: ${data.safe_count} safe`, 'success');
        
        // Reload status
        await loadVlcFlagsStatus();
    } catch (error) {
        showStatus('Error testing flags: ' + error.message, 'error');
        if (statusDiv) statusDiv.innerHTML = `<div style="color: #f44336;">Test failed: ${error.message}</div>`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function testHwAcceleration() {
    const btn = document.getElementById('testHwBtn');
    
    if (btn) btn.disabled = true;
    showStatus('Testing hardware acceleration...', 'info');
    
    try {
        const response = await fetch('/api/vlc/optimization/flags/test-hw', { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        if (data.safe) {
            showStatus('Hardware acceleration is working!', 'success');
        } else {
            showStatus('Hardware acceleration not supported: ' + (data.error || 'Unknown error'), 'error');
        }
        
        await loadVlcFlagsStatus();
    } catch (error) {
        showStatus('Error testing HW accel: ' + error.message, 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function clearVlcFlags() {
    if (!confirm('Clear all tested VLC flags and start fresh?')) return;
    
    try {
        const response = await fetch('/api/vlc/optimization/flags/clear', { method: 'POST' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        showStatus('Cleared all VLC flags', 'success');
        await loadVlcFlagsStatus();
    } catch (error) {
        showStatus('Error clearing flags: ' + error.message, 'error');
    }
}

async function loadLaunchStats() {
    // Also load VLC flags status
    loadVlcFlagsStatus();
    const summaryDiv = document.getElementById('launchStatsSummary');
    const tableDiv = document.getElementById('launchStatsTable');
    
    if (!summaryDiv || !tableDiv) return;
    
    tableDiv.innerHTML = '<div class="loading">Loading launch stats...</div>';
    
    try {
        const response = await fetch('/api/stats/launches?limit=50');
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        const { launches, summary } = data;
        
        // Update summary cards
        document.getElementById('statsAvgTime').textContent = summary.avg_ms.toFixed(0);
        document.getElementById('statsMinTime').textContent = summary.min_ms.toFixed(0);
        document.getElementById('statsMaxTime').textContent = summary.max_ms.toFixed(0);
        document.getElementById('statsCount').textContent = summary.count;
        
        // Color code avg based on target
        const avgEl = document.getElementById('statsAvgTime');
        if (summary.avg_ms <= 50) {
            avgEl.style.color = '#4caf50'; // Green - excellent
        } else if (summary.avg_ms <= 200) {
            avgEl.style.color = '#ff9800'; // Orange - okay
        } else {
            avgEl.style.color = '#f44336'; // Red - needs work
        }
        
        // Build table
        if (launches.length === 0) {
            tableDiv.innerHTML = '<div class="empty-state">No launch data yet. Launch some movies to see stats!</div>';
            return;
        }
        
        let html = `
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                <thead>
                    <tr style="border-bottom: 1px solid #3a3a3a;">
                        <th style="text-align: left; padding: 8px; color: #888;">Time</th>
                        <th style="text-align: left; padding: 8px; color: #888;">Movie</th>
                        <th style="text-align: right; padding: 8px; color: #888;">Total</th>
                        <th style="text-align: right; padding: 8px; color: #666; font-size: 11px;">Prep</th>
                        <th style="text-align: right; padding: 8px; color: #666; font-size: 11px;">Popen</th>
                        <th style="text-align: right; padding: 8px; color: #666; font-size: 11px;">Health</th>
                        <th style="text-align: right; padding: 8px; color: #666; font-size: 11px;">Focus</th>
                    </tr>
                </thead>
                <tbody>
        `;
        
        for (const launch of launches) {
            const timeColor = launch.time_ms <= 50 ? '#4caf50' : 
                              launch.time_ms <= 200 ? '#ff9800' : '#f44336';
            
            const date = launch.created ? new Date(launch.created) : null;
            const timeStr = date ? date.toLocaleTimeString() : 'Unknown';
            
            const t = launch.timing || {};
            
            html += `
                <tr style="border-bottom: 1px solid #2a2a2a;">
                    <td style="padding: 6px 8px; color: #888; font-size: 11px;">${timeStr}</td>
                    <td style="padding: 6px 8px; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(launch.movie_name)}">
                        ${escapeHtml(launch.movie_name)}
                    </td>
                    <td style="padding: 6px 8px; text-align: right; font-weight: bold; color: ${timeColor};">
                        ${launch.time_ms.toFixed(0)}ms
                    </td>
                    <td style="padding: 6px 8px; text-align: right; color: #666; font-size: 11px;">
                        ${t.prep ? t.prep.toFixed(0) : '-'}
                    </td>
                    <td style="padding: 6px 8px; text-align: right; color: #666; font-size: 11px;">
                        ${t.popen ? t.popen.toFixed(0) : '-'}
                    </td>
                    <td style="padding: 6px 8px; text-align: right; color: #888; font-size: 11px;" title="500ms health check sleep">
                        ${t.health_check ? t.health_check.toFixed(0) : '-'}
                    </td>
                    <td style="padding: 6px 8px; text-align: right; color: #666; font-size: 11px;">
                        ${t.window_focus ? t.window_focus.toFixed(0) : '-'}
                    </td>
                </tr>
            `;
        }
        
        html += '</tbody></table>';
        html += '<p style="color: #666; font-size: 11px; margin-top: 10px;">Prep = file checks, VLC lookup | Popen = create process | Health = 500ms safety wait | Focus = find window</p>';
        tableDiv.innerHTML = html;
        
    } catch (error) {
        console.error('Error loading launch stats:', error);
        tableDiv.innerHTML = `<div class="empty-state">Error loading stats: ${error.message}</div>`;
    }
}

