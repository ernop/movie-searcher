# Installation Guide

Everything you need to get Movie Searcher running on your machine.

## System Requirements

### Python Version
- **Python 3.8 or higher** (Python 3.9+ recommended)
- The application has been tested with Python 3.8, 3.9, 3.10, 3.11, 3.12, and 3.13

### Operating System
- **Windows** (primary target platform)
- **Linux/macOS** (fully supported)

### Additional Software
- **ffmpeg** (required for screenshot extraction)
  - **Automatically installed and configured** on Windows via winget
  - On Linux/macOS: install via package manager
- **VLC Media Player** (required for playing movies)
  - **Automatically installed** on Windows via winget if missing
  - On Linux/macOS: install via package manager

## Quick Start (Windows)

**The easiest way to get started:**

1. **Install Python 3.8+** (if not already installed)
   - Download from: https://www.python.org/downloads/
   - During installation, **check the box** "Add Python to PATH"

2. **Double-click `run.bat`**

That's it! The launcher will:
- Create a virtual environment if needed
- Install all Python dependencies
- Detect/install ffmpeg and VLC automatically
- Start the server
- Open your browser

3. **First Time Setup**
   - Create a `movies` folder in the project directory
   - Or configure a custom path in `settings.json`
   - Click "Scan Movies Folder" in the web interface

## Quick Start (Linux/macOS)

```bash
# Navigate to project directory
cd /path/to/movie-searcher

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Start the server
python3 start.py
```

Access at: `http://localhost:8002`

## Per-Machine Configuration

Each installation can have its own configuration in `settings.json`:

```json
{
  "movies_folder": "D:\\Movies",
  "local_target_folder": "C:\\LocalMovies"
}
```

This file is gitignored so each machine can have different paths.

**Common settings:**
- `movies_folder`: Path to your movie collection
- `local_target_folder`: Path for local copies (optional)

### AI Search Configuration (Optional)

To enable AI-powered movie discovery (asking questions like "What were Hitchcock's best thrillers?"), add your API key:

```json
{
  "movies_folder": "D:\\Movies",
  "AnthropicApiKey": "sk-ant-api03-..."
}
```

**Or** for OpenAI:

```json
{
  "movies_folder": "D:\\Movies",
  "OpenAIApiKey": "sk-..."
}
```

You can get API keys from:
- **Anthropic (Claude)**: https://console.anthropic.com/
- **OpenAI (GPT)**: https://platform.openai.com/

AI search works without any API key—you just won't be able to ask natural language questions about movies.

## Manual Installation (Windows)

If you prefer to set up manually:

### 1. Install Python

1. Download Python from https://www.python.org/downloads/
2. During installation, **check the box** "Add Python to PATH"
3. Verify installation:
   ```cmd
   python --version
   ```

### 2. Create Virtual Environment

```cmd
cd D:\movie-searcher
python -m venv venv
venv\Scripts\activate
```

### 3. Install Dependencies

```cmd
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Start the Server

```cmd
python start.py
```

The server starts on `http://localhost:8002` and opens your browser automatically.

On first startup, the application will:
- Detect/install ffmpeg via winget
- Detect/install VLC via winget
- Configure paths and verify everything works

## Stopping the Server

**Option 1:** Press `Ctrl+C` in the server window

**Option 2:** Run the stop script:
```bash
python stop.py
```

## Dependencies Explained

### Core Dependencies

- **fastapi** - Modern, fast web framework for building APIs
- **uvicorn[standard]** - ASGI server with websockets, auto-reload
- **sqlalchemy** - SQL toolkit and ORM for database operations
- **mutagen** - Audio/video metadata library for extracting video length

### Automatic Setup Features

The application automatically handles setup of external tools:

- **ffmpeg installation**: Automatically installs via winget (Windows) if missing
- **VLC installation**: Automatically installs via winget (Windows) if missing
- **Path detection**: Finds executables in common installation locations
- **Startup verification**: Tests tools on every startup

### Required External Tools

- **ffmpeg** - For extracting video screenshots and determining video length
  - Windows: Auto-installed via winget, or `winget install --id=Gyan.FFmpeg`
  - Linux: `sudo apt install ffmpeg` or equivalent
  - macOS: `brew install ffmpeg`

- **VLC** - For playing movies
  - Windows: Auto-installed via winget, or download from https://videolan.org
  - Linux: `sudo apt install vlc` or equivalent
  - macOS: `brew install --cask vlc`

## Troubleshooting

### Python Not Found

**Windows:**
1. Install Python from https://python.org/downloads/
2. Check "Add Python to PATH" during installation
3. Restart your terminal
4. Verify: `python --version`

**Linux/macOS:**
- Use `python3` instead of `python`
- Install: `sudo apt install python3 python3-pip python3-venv`

### Import Errors / Missing Modules

1. Make sure virtual environment is activated:
   - Windows: `venv\Scripts\activate`
   - Linux/macOS: `source venv/bin/activate`
2. Reinstall dependencies:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

### Port Already in Use

1. Stop any existing server:
   ```bash
   python stop.py
   ```
2. Or change port in `server.py` (search for `port=8002`)

### ffmpeg Not Found

**Windows:** The application auto-installs via winget. If that fails:
```cmd
winget install --id=Gyan.FFmpeg -e --accept-source-agreements
```

**Linux:**
```bash
sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

### VLC Not Found

**Windows:** The application auto-installs via winget. If that fails:
```cmd
winget install --id=VideoLAN.VLC -e --accept-source-agreements
```

Or download from https://videolan.org

## Updating

To update dependencies:

```bash
# Activate venv first
pip install --upgrade -r requirements.txt
```

## Uninstallation

1. Stop the server: `python stop.py`
2. Delete the project folder

All data (database, screenshots, config) is stored in the project folder.
