@echo off
echo Movie Searcher Launcher
echo.

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8 or higher
    pause
    exit /b 1
)

REM Check if server is already running
echo Checking if server is already running...
netstat -an | findstr ":8002" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo Server is already running!
    echo Opening browser...
    start http://localhost:8002
    echo.
    echo Server URL: http://localhost:8002
    echo.
    pause
    exit /b 0
)

REM Check if virtual environment exists, if not create it
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install/upgrade dependencies
echo Installing dependencies...
pip install -q --upgrade pip
pip install -q -r requirements.txt

REM Start the server in a separate window (stays running)
echo Starting server in background with auto-reload enabled...
start "Movie Searcher Server" cmd /k "cd /d %SCRIPT_DIR% && call venv\Scripts\activate.bat && python main.py"

REM Wait a moment for server to start
timeout /t 3 /nobreak >nul

REM Open browser
echo Opening browser...
start http://localhost:8002

echo.
echo Server is running at http://localhost:8002
echo The server window can be minimized - it will keep running.
echo To stop the server, close the "Movie Searcher Server" window.
echo.
pause

