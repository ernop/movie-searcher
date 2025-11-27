# VLC Fast Startup Optimization Guide

This document outlines strategies to minimize time-to-first-frame when launching videos with VLC.

## Root Causes of Slow Startup (5-6+ seconds)

### 1. **Hardware/Storage Related**
- **HDD spin-up**: If the drive is in sleep mode, mechanical disks need 3-5 seconds to spin up
- **USB selective suspend**: Windows may power-manage USB ports, requiring wake-up time
- **Antivirus scanning**: Windows Defender or other AV may scan files before allowing access

### 2. **VLC Behavior Related**
- **Metadata preparsing**: VLC scans files for metadata before playback
- **Network lookups**: VLC attempts to fetch online metadata (album art, etc.)
- **File indexing**: VLC may build an index for seeking
- **Subtitle auto-detection**: Scanning for subtitle files
- **Media library**: VLC's built-in library system
- **Lua extensions**: Scripts that run on startup
- **Inaccurate seeking**: When using --start-time, VLC may seek slowly for accuracy

### 3. **Codec/Decoding Related**
- **Software decoding**: CPU-based decoding is slower than GPU hardware acceleration
- **High file caching**: Default 1200ms cache can delay initial playback
- **Multiple audio tracks**: Scanning for all audio streams

---

## VLC Command-Line Optimizations

### Critical Fast-Startup Options

```bash
# PERFORMANCE: Reduce file caching (default 1200ms → 300ms for local files)
--file-caching=300

# PERFORMANCE: Use fast seeking (less accurate but much faster)
--input-fast-seek

# PERFORMANCE: Disable network metadata lookups
--no-metadata-network-access

# PERFORMANCE: Disable automatic media preparsing
--no-auto-preparse

# PERFORMANCE: Disable media library scanning
--no-media-library

# PERFORMANCE: Disable Lua extensions (can slow startup)
--no-lua

# UI: Disable on-screen title display (small savings)
--no-video-title-show

# UI: Disable Qt update notifications
--no-qt-updates-notif

# UI: Skip Qt privacy dialog
--no-qt-privacy-ask

# UI: Disable album art fetching
--no-album-art
```

### Hardware Acceleration Options (Windows)

```bash
# Use Direct3D11 hardware decoding (Windows 8+, best for modern systems)
--avcodec-hw=d3d11va

# Alternative: DXVA2 (Windows Vista+, broader compatibility)
--avcodec-hw=dxva2

# Use Direct3D11 video output (fastest on Windows)
--vout=direct3d11

# Alternative video output options
--vout=direct3d9
--vout=opengl
```

### Hardware Acceleration Options (Linux)

```bash
# VAAPI (Intel/AMD integrated graphics)
--avcodec-hw=vaapi

# VDPAU (NVIDIA, older)
--avcodec-hw=vdpau

# CUDA/NVDEC (NVIDIA modern)
--avcodec-hw=cuda
```

### Complete Optimized Command Example

```bash
vlc "movie.mkv" \
    --file-caching=300 \
    --input-fast-seek \
    --no-metadata-network-access \
    --no-auto-preparse \
    --no-media-library \
    --no-lua \
    --no-video-title-show \
    --no-qt-updates-notif \
    --no-qt-privacy-ask \
    --avcodec-hw=d3d11va \
    --start-time=1234
```

---

## VLC Configuration File (vlcrc)

### Location
- **Windows**: `%APPDATA%\vlc\vlcrc`
- **Linux**: `~/.config/vlc/vlcrc`
- **macOS**: `~/Library/Preferences/org.videolan.vlc/vlcrc`

### Key Settings to Modify

```ini
# File caching (milliseconds)
file-caching=300

# Use fast seeking
input-fast-seek=1

# Disable metadata network access
metadata-network-access=0

# Disable auto preparse
auto-preparse=0

# Disable media library
media-library=0

# Hardware acceleration
avcodec-hw=d3d11va

# Disable video title display
video-title-show=0

# Disable Qt privacy dialog
qt-privacy-ask=0

# Disable update notifications
qt-updates-notif=0

# Disable album art
album-art=0
```

---

## Implementation Strategy for Movie Searcher

### Option A: Command-Line Only (Recommended - No User Configuration Needed)

Add optimization flags directly to the VLC launch command. This is the safest approach as it:
- Doesn't modify user's VLC settings
- Only affects launches from Movie Searcher
- Is reversible (user can still launch VLC normally)
- Works immediately without any setup

### Option B: vlcrc Modification (Opt-In)

Provide a setup option that modifies the user's vlcrc file. This:
- Affects ALL VLC usage (not just Movie Searcher)
- Requires user consent
- Creates a backup of original settings
- Can be reversed

### Option C: Hybrid (Recommended for Movie Searcher)

1. **Always use command-line optimizations** (no user action needed)
2. **Offer optional vlcrc optimization** in settings with:
   - Clear explanation of what will change
   - Backup creation
   - One-click restore option

---

## System-Level Optimizations (Windows)

### Disable USB Selective Suspend

**Registry path**: `HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\USB\DisableSelectiveSuspend`

```reg
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\USB]
"DisableSelectiveSuspend"=dword:00000001
```

Or via Power Options:
- Power Options → Change plan settings → Change advanced → USB settings → USB selective suspend → Disabled

### Add Antivirus Exclusions

Recommend users add their media folders to antivirus exclusions:
- Windows Defender: Settings → Virus & threat protection → Exclusions

---

## Testing Methodology

To measure improvement:

1. **Cold start**: Close VLC, wait 10 seconds, launch video
2. **Warm start**: Launch video while VLC is already running
3. **With seeking**: Launch video with --start-time parameter
4. **Measure**: Time from command execution to first frame visible

Expected improvements:
- Without optimization: 3-6 seconds
- With optimization: 0.5-1.5 seconds

---

## VLC Version Compatibility

These optimizations are tested with VLC 3.x. Some options may differ in VLC 4.x:

- `--avcodec-hw` syntax may change
- `--no-lua` may become `--no-plugins` or similar
- Always test with user's installed VLC version
