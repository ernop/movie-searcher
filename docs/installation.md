# Installation Guide

## System Requirements

### Python Version
- **Python 3.8 or higher** (Python 3.9+ recommended)
- The application has been tested with Python 3.8, 3.9, 3.10, 3.11, 3.12, and 3.13

### Operating System
- **Windows** (primary target platform)
- **Linux/macOS** (should work with minor adjustments - see below)

### Additional Software
- **ffmpeg** (required for screenshot extraction)
  - **Automatically installed and configured** - no manual setup needed!
  - The application will automatically detect, install (via winget on Windows), and configure ffmpeg on first startup
  - Required for extracting video screenshots and determining video length
- **VLC Media Player** (optional, for playing movies)
  - Download from: https://www.videolan.org/
  - The application will auto-detect VLC in common Windows installation locations

## Quick Start (Windows)

**The easiest way to get started on Windows:**

1. **Install Python 3.8+** (if not already installed)
   - Download from: https://www.python.org/downloads/
   - During installation, **check the box** "Add Python to PATH"
   - Verify: Open Command Prompt and run `python --version`

2. **Download/Extract the Project**
   - Extract all project files to a folder (e.g., `D:\movie-searcher`)

3. **Run the Application**
   - Double-click `start.bat`
   - The script will automatically:
     - Check for Python
     - Create a virtual environment if needed
     - Install all Python dependencies
     - **Automatically detect and install ffmpeg** (if not found)
     - Configure ffmpeg and verify it's working
     - Start the server
     - Open your browser

4. **First Time Setup**
   - Create a `movies` folder in the project directory
   - Place your movie files in the `movies` folder
   - Click "Scan Movies Folder" in the web interface
   - Wait for indexing to complete

That's it! The application is now running. **ffmpeg is automatically installed and configured** - no manual setup needed!

## Manual Installation (Windows)

If you prefer to set up manually:

### 1. Install Python

1. Download Python from https://www.python.org/downloads/
2. During installation, **check the box** "Add Python to PATH"
3. Verify installation by opening Command Prompt and running:
   ```cmd
   python --version
   ```
   You should see Python 3.8 or higher.

### 2. Download/Extract the Project

Extract the project files to your desired location (e.g., `D:\movie-searcher`)

### 4. Create Virtual Environment

Open Command Prompt in the project directory:

```cmd
cd D:\movie-searcher
python -m venv venv
venv\Scripts\activate
```

### 5. Install Dependencies

With the virtual environment activated:

```cmd
pip install --upgrade pip
pip install -r requirements.txt
```

This installs:
- **FastAPI** (0.104.1) - Web framework for the API
- **Uvicorn** (0.24.0) with standard extras - ASGI server
- **Mutagen** (1.47.0) - For extracting video metadata (length, etc.)
- **SQLAlchemy** (2.0.44) - Database ORM for SQLite

### 6. Start the Server

```cmd
python main.py
```

The server will start on `http://localhost:8002` and your browser should open automatically.

**Note:** On first startup, the application will automatically:
- Detect if ffmpeg is installed
- Install ffmpeg via winget if not found (Windows only)
- Configure ffmpeg paths and verify everything works
- Save configuration to the database

### 7. Create Movies Folder

Create a folder named `movies` in the project directory and place your movie files there.

## Installation for Linux/macOS

The application should work on Linux and macOS with minor adjustments:

### 1. Install Python

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

**Linux (Fedora):**
```bash
sudo dnf install python3 python3-pip
```

**macOS:**
```bash
# Using Homebrew
brew install python3

# Or download from python.org
```

Verify: `python3 --version`

### 2. Setup Project

```bash
# Navigate to project directory
cd /path/to/movie-searcher

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Start Server

```bash
python3 main.py
```

Access at: `http://localhost:8002`

**Note:** The `start.bat` script is Windows-specific. On Linux/macOS, you'll need to start the server manually or create a shell script equivalent.

## Usage

### First Time Setup

1. **Create Movies Folder**
   - Create a folder named `movies` in the project directory
   - Place your movie files in this folder

2. **Index Your Movies**
   - Open the web interface at `http://localhost:8002`
   - Click "Scan Movies Folder" or "Change Movies Folder" to set your movies directory
   - Wait for the scan to complete
   - Start searching and watching!

### Running the Application

**Windows (Easiest):**
- Double-click `start.bat` - it handles everything automatically

**Windows (Manual):**
```cmd
venv\Scripts\activate
python main.py
```

**Linux/macOS:**
```bash
source venv/bin/activate
python3 main.py
```

The server runs on `http://localhost:8002` by default.

## Dependencies Explained

### Core Dependencies

- **fastapi** - Modern, fast web framework for building APIs
- **uvicorn[standard]** - ASGI server with additional features:
  - `watchfiles` - For auto-reload during development
  - `websockets` - For WebSocket support
  - `httptools` - For improved HTTP parsing
- **mutagen** - Audio/video metadata library for extracting video length
- **sqlalchemy** - SQL toolkit and ORM for database operations

### Automatic Setup Features

The application automatically handles setup of external tools:

- **ffmpeg installation**: Automatically detects if ffmpeg is missing and installs it via winget (Windows)
- **ffmpeg configuration**: Automatically finds ffmpeg and ffprobe, tests both, and saves paths to database
- **Startup verification**: Tests ffmpeg and ffprobe on every startup to ensure everything works
- **Retry logic**: Retries installation and configuration up to 3 times if initial attempts fail
- **Status reporting**: Settings page shows system status with clear indicators if anything needs attention

### Required External Tools

- **ffmpeg** - Required for extracting video screenshots and determining video length
  - **Automatically installed and configured** on first startup
  - The application will detect, install (via winget on Windows), and configure ffmpeg automatically
  - If automatic installation fails, you can install manually:
    - Windows: `winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements`
    - Other platforms: Download from https://ffmpeg.org/
  - Configuration is saved to the database and verified on each startup

## Troubleshooting

### Python Not Found (Windows)

**Symptoms:** Error message "Python is not installed or not in PATH"

**Solutions:**
1. **Install Python:**
   - Download from https://www.python.org/downloads/
   - During installation, **check the box** "Add Python to PATH"
   - Restart Command Prompt after installation

2. **Add Python to PATH manually:**
   - Find Python installation (usually `C:\Users\YourName\AppData\Local\Programs\Python\Python3XX\`)
   - Add to System Environment Variables:
     - Right-click "This PC" → Properties → Advanced System Settings
     - Click "Environment Variables"
     - Under "System Variables", find "Path" and click "Edit"
     - Add Python installation path and `Scripts` folder
   - Restart Command Prompt

3. **Verify installation:**
   ```cmd
   python --version
   ```
   Should show Python 3.8 or higher

### Python Not Found (Linux/macOS)

- Use `python3` instead of `python`
- Install Python 3 if not already installed
- Verify: `python3 --version`

### Import Errors (Windows)

**Symptoms:** ModuleNotFoundError or ImportError when running the application

**Solutions:**

1. **Activate virtual environment:**
   ```cmd
   venv\Scripts\activate
   ```
   You should see `(venv)` in your command prompt

2. **Reinstall dependencies:**
   ```cmd
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Verify installation:**
   ```cmd
   pip list
   ```
   Should show fastapi, uvicorn, mutagen, and sqlalchemy

4. **If using start.bat:**
   - Delete the `venv` folder
   - Run `start.bat` again - it will recreate the virtual environment

### Port Already in Use (Windows)

**Symptoms:** Error about port 8002 being in use

**Solutions:**

1. **Stop existing server:**
   - Close any "Movie Searcher Server" windows
   - Or double-click `stop.bat` to stop the server
   - Check Task Manager for any `python.exe` processes running `main.py`

2. **Use a different port:**
   - Open `main.py` in a text editor
   - Find the line with `port=8002` (around line 2119)
   - Change to a different port (e.g., `port=8003`)
   - Access the application at `http://localhost:8003`

### Database Migration Issues (Windows)

**Symptoms:** Database errors or missing data after migration

**Solutions:**

1. **Start fresh:**
   - Close the server
   - Delete `movie_searcher.db` file
   - Restart the server - it will recreate the database
   - If you had JSON files, they will be automatically migrated on first run

2. **Manual migration:**
   - Ensure JSON files exist (`movie_index.json`, `watched_movies.json`, etc.)
   - Start the server - migration happens automatically if database is empty

### ffmpeg Not Found (Windows)

**Symptoms:** Screenshots not being generated, warnings about ffmpeg not found

**Solutions:**

1. **Automatic installation should handle this:**
   - The application automatically installs ffmpeg on first startup
   - If you see warnings, restart the server - it will retry installation
   - Check the server logs for installation progress

2. **If automatic installation fails:**
   - Open PowerShell or Command Prompt as Administrator
   - Run: `winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements`
   - Restart the server - it will detect and configure the installation

3. **Manual installation (if winget is not available):**
   - Download from: https://ffmpeg.org/download.html
   - Extract to a folder (e.g., `C:\ffmpeg`)
   - Restart the server - it will detect and configure the installation
   - The application searches these locations automatically:
     - System PATH
     - `C:\ffmpeg\bin\ffmpeg.exe`
     - `C:\Program Files\ffmpeg\bin\ffmpeg.exe`
     - `C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe`
     - Winget installation directory

4. **Check system status:**
   - Go to Settings page in the web interface
   - Check the "System Status" section at the top
   - It will show if ffmpeg and ffprobe are working correctly

5. **Note:** The application will automatically retry installation up to 3 times on startup. If it still fails, check the logs for specific error messages.

### VLC Not Found (Windows)

**Symptoms:** Error when trying to launch movies

**Solutions:**

1. **Install VLC Media Player:**
   - Download from https://www.videolan.org/
   - Install to default location (`C:\Program Files\VideoLAN\VLC\`)

2. **The application auto-detects VLC in these locations:**
   - `C:\Program Files\VideoLAN\VLC\vlc.exe`
   - `C:\Program Files (x86)\VideoLAN\VLC\vlc.exe`
   - `%LOCALAPPDATA%\Programs\VideoLAN\vlc.exe`
   - Or if VLC is in your system PATH

3. **Note:** The application will work without VLC, but you won't be able to play movies directly from the interface

## Updating Dependencies (Windows)

To update all dependencies to their latest compatible versions:

```cmd
venv\Scripts\activate
pip install --upgrade -r requirements.txt
```

To update a specific package:

```cmd
pip install --upgrade fastapi
```

## Uninstallation (Windows)

To remove the application:

1. **Stop the server:**
   - Close any "Movie Searcher Server" windows
   - Or double-click `stop.bat`

2. **Delete the project folder:**
   - Simply delete the entire project folder
   - All data (database, config, etc.) is stored in the project folder

3. **Optional - Remove virtual environment:**
   ```cmd
   rmdir /s venv
   ```

## Development Setup (Windows)

For development, you may want additional tools:

```cmd
venv\Scripts\activate
pip install pytest  # For testing (optional)
pip install black   # For code formatting (optional)
pip install flake8  # For linting (optional)
```

## Getting Help (Windows)

If you encounter issues:

1. **Check the log file:**
   - Open `movie_searcher.log` in a text editor
   - Look for error messages at the end of the file

2. **Verify Python installation:**
   ```cmd
   python --version
   ```
   Should show Python 3.8 or higher

3. **Verify dependencies:**
   ```cmd
   venv\Scripts\activate
   pip list
   ```
   Should show fastapi, uvicorn, mutagen, and sqlalchemy

4. **Check movies folder:**
   - Ensure the `movies` folder exists in the project directory
   - Contains video files (`.mp4`, `.avi`, `.mkv`, etc.)

5. **Check port availability:**
   - Ensure port 8002 is not in use by another application
   - Close any other instances of the Movie Searcher

6. **Common Windows-specific issues:**
   - **Antivirus blocking:** Add the project folder to antivirus exclusions
   - **Firewall:** Allow Python through Windows Firewall if prompted
   - **Permissions:** Run Command Prompt as Administrator if needed
   - **Path issues:** Use full paths if you encounter path-related errors

