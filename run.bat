@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [Setup] Creating virtual environment...

    rem Try 'python' first, then 'py' (Windows Launcher), then common install paths
    python -m venv .venv 2>nul
    if errorlevel 1 (
        py -m venv .venv 2>nul
    )
    if errorlevel 1 (
        "%LOCALAPPDATA%\Python\bin\python.exe" -m venv .venv 2>nul
    )
    if errorlevel 1 (
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" -m venv .venv 2>nul
    )
    if errorlevel 1 (
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m venv .venv 2>nul
    )
    if errorlevel 1 (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m venv .venv 2>nul
    )

    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo ERROR: Could not find Python. Tried: python, py, and common install paths.
        echo        Make sure Python 3.11+ is installed and try again.
        echo        Download: https://python.org
        echo.
        echo To diagnose, run:  where python
        pause
        exit /b 1
    )

    echo [Setup] Installing dependencies...
    .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
    echo [Setup] Installing Playwright browser...
    .venv\Scripts\playwright install chromium
    echo [Setup] Done.
    echo.
)

.venv\Scripts\python gui.py
if errorlevel 1 (
    echo.
    echo The app exited with an error. See above for details.
    pause
)