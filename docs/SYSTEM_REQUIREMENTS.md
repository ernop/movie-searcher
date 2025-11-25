# System Requirements and Validation

## Critical Rule: Required Programs Must Be Validated

**The Movie Searcher server CANNOT start until all required programs are installed, exist, and are proven to work correctly.**

### Required Programs

1. **ffmpeg** - Required for screenshot extraction
2. **ffprobe** - Required for video metadata extraction (part of ffmpeg package)
3. **VLC Media Player** - Required for launching and playing movies

### Validation Requirements

For each required program, the following must pass **before** the server starts:

#### 1. Existence Check
- The executable file must exist on the filesystem
- The path must be accessible and readable

#### 2. Version Check
- **ffmpeg/ffprobe**: Must respond to `--version` command successfully
- **VLC**: Version check is **NOT** performed because `vlc --version` can pop up GUI dialogs on Windows requiring user interaction. Instead, we simply verify the executable exists and is accessible.

#### 3. Functionality Check
- For ffmpeg/ffprobe: Must be able to process test operations
- For VLC: File must exist and have execute permissions (no version test needed)

### Automatic Installation

On Windows, the startup script will automatically attempt to install missing programs via winget:

```powershell
# ffmpeg
winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --silent

# VLC
winget install --id=VideoLAN.VLC -e --accept-source-agreements --accept-package-agreements --silent
```

### Startup Sequence

1. **Check if server is already running** - If yes, open browser and exit
2. **Check ffmpeg in PATH** - If not found, attempt winget installation
3. **Run setup_ffmpeg.py** - Detect and validate ffmpeg/ffprobe, save to config
   - Exit with error if validation fails
4. **Check VLC in PATH** - If not found, attempt winget installation  
5. **Run setup_vlc.py** - Detect and validate VLC, save to config
   - Exit with error if validation fails
6. **Start server** - Only if all validations passed
7. **During server startup (lifespan):**
   - Re-validate ffmpeg/ffprobe configuration
   - Re-validate VLC configuration
   - **Raise RuntimeError if any validation fails** - This prevents server from starting

### Validation Failure Handling

If any required program fails validation:

1. **Pre-startup failures** (in start.py):
   - Display error message with installation instructions
   - Wait for user to press Enter
   - Exit with code 1

2. **Startup failures** (in lifespan):
   - Log error with full details
   - Raise RuntimeError with descriptive message
   - Server will not accept connections

### Configuration Storage

Validated program paths are stored in `settings.json`:

```json
{
  "ffmpeg_path": "C:\\path\\to\\ffmpeg.exe",
  "ffprobe_path": "C:\\path\\to\\ffprobe.exe",
  "vlc_path": "C:\\Program Files\\VideoLAN\\VLC\\vlc.exe"
}
```

### Re-validation on Startup

Every time the server starts, all programs are re-validated:
- This ensures programs haven't been uninstalled or moved
- This catches corrupted installations
- This verifies programs still respond correctly

### Manual Installation

If automatic installation fails, users must install manually:

**ffmpeg:**
- Download from: https://ffmpeg.org/download.html
- Or use: `winget install --id=Gyan.FFmpeg`

**VLC:**
- Download from: https://www.videolan.org/vlc/
- Or use: `winget install --id=VideoLAN.VLC`

### Troubleshooting

If the server won't start due to validation failures:

1. Check the console output for specific error messages
2. Verify the programs are installed correctly
3. Try running the commands manually:
   ```bash
   ffmpeg -version
   vlc --version
   ```
4. If manual commands fail, reinstall the program
5. On Windows, try uninstalling and using winget to reinstall:
   ```powershell
   winget uninstall --id=VideoLAN.VLC
   winget install --id=VideoLAN.VLC -e --accept-source-agreements --accept-package-agreements
   ```

### System Status Page

The Settings page includes a "System Status" section that shows:
- Real-time status of all required programs
- Version numbers for validated programs
- Error messages if validation fails
- "Re-check System Status" button to rerun validation

This allows users to verify their installation without restarting the server.

