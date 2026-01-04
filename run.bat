@echo off
title Movie Searcher
cd /d "%~dp0"

echo ============================================================
echo  Movie Searcher Launcher
echo ============================================================
echo.

:: Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.8+ from https://python.org
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Create venv if it doesn't exist
if not exist "venv\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Virtual environment created.
    echo.
)

:: Activate venv
echo Activating virtual environment...
call venv\Scripts\activate.bat

:: Check if dependencies are installed (quick check for fastapi)
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
    echo Dependencies installed.
    echo.
)

:: Run the startup script (shows live logs)
echo.
echo ============================================================
echo  Starting server... (Ctrl+C to stop)
echo ============================================================
echo.
python start.py

:: If we get here, server stopped
echo.
echo Server stopped.

