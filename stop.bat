@echo off
echo Stopping Movie Searcher Server...
echo.

REM Find and kill Python processes running main.py
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST ^| findstr /I "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /I "main.py" >nul
    if not errorlevel 1 (
        echo Stopping server process %%a...
        taskkill /PID %%a /F >nul 2>&1
    )
)

REM Also try to find uvicorn processes
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST ^| findstr /I "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /I "uvicorn" >nul
    if not errorlevel 1 (
        echo Stopping uvicorn process %%a...
        taskkill /PID %%a /F >nul 2>&1
    )
)

echo.
echo Server stopped (if it was running).
pause

