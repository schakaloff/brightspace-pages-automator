@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

:: ── 1. Locate an existing Python installation ─────────────────────────────────
set "PYTHON_EXE="
set "USE_EMBED=0"

for %%C in (python py) do (
    if not defined PYTHON_EXE (
        %%C --version >nul 2>&1 && set "PYTHON_EXE=%%C"
    )
)
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe"
) do (
    if not defined PYTHON_EXE if exist %%P set "PYTHON_EXE=%%P"
)

if defined PYTHON_EXE goto :have_python

:: ── 2. No Python found — use or download portable Python (zero admin needed) ──
if exist "python-embed\python.exe" (
    echo [Setup] Using portable Python from python-embed\
    set "PYTHON_EXE=%~dp0python-embed\python.exe"
    set "USE_EMBED=1"
    goto :have_python
)

echo [Setup] Python not found on this machine.
echo [Setup] Downloading portable Python 3.12 — no installation or admin needed...
powershell -NoProfile -Command ^
    "Invoke-WebRequest https://www.python.org/ftp/python/3.12.9/python-3.12.9-embed-amd64.zip -OutFile python-embed.zip -UseBasicParsing"
if errorlevel 1 (
    echo.
    echo ERROR: Download failed. Check your internet connection and try again.
    pause
    exit /b 1
)
powershell -NoProfile -Command "Expand-Archive python-embed.zip python-embed -Force"
del python-embed.zip

:: Enable site-packages so pip-installed packages are importable
for %%F in ("python-embed\python3*._pth") do (
    powershell -NoProfile -Command ^
        "(Get-Content '%%F') -replace '#import site','import site' | Set-Content '%%F'"
    findstr /C:"import site" "%%F" >nul 2>&1 || echo import site>>"%%F"
)

echo [Setup] Bootstrapping pip into portable Python...
powershell -NoProfile -Command ^
    "Invoke-WebRequest https://bootstrap.pypa.io/get-pip.py -OutFile get-pip.py -UseBasicParsing"
"python-embed\python.exe" get-pip.py --quiet
del get-pip.py

set "PYTHON_EXE=%~dp0python-embed\python.exe"
set "USE_EMBED=1"

:have_python

:: ── 3. Install deps once ──────────────────────────────────────────────────────
if "!USE_EMBED!"=="1" (
    :: Embedded Python: install directly into it, no venv needed
    if not exist "python-embed\.deps_done" (
        echo [Setup] Installing dependencies into portable Python...
        "!PYTHON_EXE!" -m pip install --quiet --upgrade pip
        "!PYTHON_EXE!" -m pip install --quiet -r requirements.txt
        echo [Setup] Installing Playwright browser...
        "!PYTHON_EXE!" -m playwright install chromium
        echo. > "python-embed\.deps_done"
        echo [Setup] Done.
        echo.
    )
    set "RUN_PYTHON=!PYTHON_EXE!"
) else (
    :: System Python: use a virtualenv for isolation
    if not exist ".venv\Scripts\python.exe" (
        echo [Setup] Creating virtual environment...
        "!PYTHON_EXE!" -m venv .venv
        echo [Setup] Installing dependencies...
        .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
        .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
        echo [Setup] Installing Playwright browser...
        .venv\Scripts\playwright install chromium
        echo [Setup] Done.
        echo.
    )
    set "RUN_PYTHON=.venv\Scripts\python.exe"
)

:: ── 4. Launch ─────────────────────────────────────────────────────────────────
"!RUN_PYTHON!" gui.py
if errorlevel 1 (
    echo.
    echo The app exited with an error. See above for details.
    pause
)
