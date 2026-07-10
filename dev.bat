@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=py"
)

"%PYTHON_EXE%" dev.py
if errorlevel 1 (
    echo.
    echo The dev launcher exited with an error. See above for details.
    pause
)
