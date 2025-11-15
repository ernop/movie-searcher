@echo off
setlocal enabledelayedexpansion
echo Stopping Movie Searcher Server...
echo.

set FOUND=0

REM Find process using port 8002 (most reliable method)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8002" ^| findstr "LISTENING"') do (
    set PID=%%a
    set FOUND=1
    echo Found process %%a using port 8002
    echo Stopping process %%a...
    taskkill /PID %%a /F >nul 2>&1
    if errorlevel 1 (
        echo Failed to stop process %%a
    ) else (
        echo Successfully stopped process %%a
    )
)

REM Also kill any parent uvicorn processes (for reload mode)
REM Find processes with main.py in command line
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST ^| findstr /I "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /I "main.py" >nul
    if not errorlevel 1 (
        echo Stopping main.py process %%a...
        taskkill /PID %%a /F >nul 2>&1
        set FOUND=1
    )
)

REM Also check for uvicorn processes
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST ^| findstr /I "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /I "uvicorn" >nul
    if not errorlevel 1 (
        echo Stopping uvicorn process %%a...
        taskkill /PID %%a /F >nul 2>&1
        set FOUND=1
    )
)

echo.
if %FOUND%==1 (
    echo Server stopped.
) else (
    echo No server process found running on port 8002.
)


