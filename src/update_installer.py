"""Helpers for launching the Windows self-update installer.

The installer must not start while the app is still holding AppMutex. In silent
mode Inno can treat that as "app is still running" and exit before replacing
files, which looks like a successful update that did nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from config import RELAUNCH_SWITCH
from update_checker import log_update_event


def installer_args() -> list[str]:
    return [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        RELAUNCH_SWITCH,
    ]


def _cmd_value(value: str) -> str:
    return value.replace("^", "^^").replace("&", "^&").replace("<", "^<").replace(">", "^>").replace("|", "^|")


def wait_then_install_script(
    installer_path: Path,
    pid: int,
    relaunch_path: Path | None = None,
) -> str:
    app_path = str(relaunch_path or "")
    app_dir = str(relaunch_path.parent) if relaunch_path is not None else ""
    app_name = relaunch_path.name if relaunch_path is not None else ""
    args = " ".join(installer_args()) + ' /LOG="%SETUPLOG%"'
    lines = [
        "@echo off",
        "setlocal",
        f'set "INSTALLER={_cmd_value(str(installer_path))}"',
        f'set "APP={_cmd_value(app_path)}"',
        f'set "APPDIR={_cmd_value(app_dir)}"',
        f'set "APPNAME={_cmd_value(app_name)}"',
        'set "LOG=%TEMP%\\BrightspacePagesAutomator-update.log"',
        'set "SETUPLOG=%TEMP%\\BrightspacePagesAutomator-setup.log"',
        f'>> "%LOG%" echo [%DATE% %TIME%] Waiting for app PID {pid}',
        # Wait-Process blocks until the pid exits and returns at once if it is
        # already gone. The previous `tasklist | findstr` poll could hang
        # indefinitely on the pipe, leaving a stuck console and no install.
        # -Timeout caps the wait so a wedged app can never block the update
        # forever; we continue regardless, since Setup closes the app anyway.
        'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command '
        f'"try {{ Wait-Process -Id {pid} -Timeout 120 -ErrorAction Stop }} catch {{ }}" '
        '>NUL 2>&1',
        '>> "%LOG%" echo [%DATE% %TIME%] App exited, continuing',
        "timeout /t 1 /nobreak >NUL",
        '>> "%LOG%" echo [%DATE% %TIME%] Running installer "%INSTALLER%"',
        f'"%INSTALLER%" {args}',
        'set "SETUP_EXIT=%ERRORLEVEL%"',
        '>> "%LOG%" echo [%DATE% %TIME%] Installer exited %SETUP_EXIT%',
        "timeout /t 2 /nobreak >NUL",
    ]
    if relaunch_path is not None:
        lines += [
            'tasklist /FI "IMAGENAME eq %APPNAME%" 2>NUL | findstr /I /C:"%APPNAME%" >NUL',
            "if errorlevel 1 (",
            '  if exist "%APP%" (',
            '    >> "%LOG%" echo [%DATE% %TIME%] Restart command start "" /D "%APPDIR%" "%APP%"',
            '    >> "%LOG%" echo [%DATE% %TIME%] Relaunching "%APP%"',
            '    start "" /D "%APPDIR%" "%APP%"',
            "  ) else (",
            '    >> "%LOG%" echo [%DATE% %TIME%] App path missing "%APP%"',
            "  )",
            ") else (",
            '  >> "%LOG%" echo [%DATE% %TIME%] App already running',
            ")",
        ]
    lines += [
        "endlocal",
        'del "%~f0"',
        "",
    ]
    return "\r\n".join(lines)


def launch_after_current_process_exits(installer_path: Path) -> None:
    """Start a detached helper that waits for this app, then runs Setup."""
    if sys.platform != "win32":
        log_update_event(f"Launching installer directly: {installer_path}")
        subprocess.Popen([str(installer_path)])
        return

    relaunch_path = Path(sys.executable) if getattr(sys, "frozen", False) else None
    helper_path = Path(tempfile.gettempdir()) / f"BrightspacePagesAutomator-update-{os.getpid()}.cmd"
    # newline="" is required: the script already joins its lines with \r\n, and
    # the default translation would turn every one of those into \r\r\n. cmd.exe
    # mis-parses the multi-line if/goto block that results, so the helper spins
    # in its wait loop forever and Setup is never launched.
    helper_path.write_text(
        wait_then_install_script(installer_path, os.getpid(), relaunch_path),
        encoding="utf-8",
        newline="",
    )
    log_update_event(f"Installer helper written: {helper_path}")
    log_update_event(f"Installer path: {installer_path}")
    log_update_event(f"Restart target: {relaunch_path or '(none; source run)'}")
    log_update_event(f"Helper launch command: cmd.exe /d /c {helper_path}")

    # CREATE_NO_WINDOW only. It and DETACHED_PROCESS are mutually exclusive in
    # CreateProcess, and passing both surfaced a visible console window running
    # the wait loop. CREATE_NO_WINDOW still gives the helper a (hidden) console,
    # which timeout/tasklist need in order to work at all.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        ["cmd.exe", "/d", "/c", str(helper_path)],
        close_fds=True,
        creationflags=creationflags,
    )
    log_update_event(f"Installer helper launched: pid={proc.pid}")
