"""Helpers for launching the Windows self-update installer.

The installer must not start while the app is still holding AppMutex. In silent
mode Inno can treat that as "app is still running" and exit before replacing
files, which looks like a successful update that did nothing.

The helper is also the single authoritative relaunch path after an update.
Setup used to reopen the app as well (a [Run] entry gated on /RELAUNCH), so two
processes could race to start and the user could end up with two windows. Setup
no longer relaunches anything; this script does, exactly once.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from update_checker import (
    UPDATE_DIR_PREFIX,
    log_update_event,
    record_update_result,
    setup_log_path,
    update_log_path,
    update_result_path,
)


def installer_args() -> list[str]:
    return [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
    ]


def _cmd_value(value: str) -> str:
    return value.replace("^", "^^").replace("&", "^&").replace("<", "^<").replace(">", "^>").replace("|", "^|")


def _removable_update_dir(installer_path: Path) -> Path | None:
    """Only ever delete a directory this app created for this update.

    The installer normally sits alone in a mkdtemp'd directory, but a caller
    could hand us a path somewhere else entirely, and wiping that recursively
    would be catastrophic — so the prefix check gates the cleanup.
    """
    parent = installer_path.parent
    return parent if parent.name.startswith(UPDATE_DIR_PREFIX) else None


def wait_then_install_script(
    installer_path: Path,
    pid: int,
    relaunch_path: Path | None = None,
    update_dir: Path | None = None,
) -> str:
    app_path = str(relaunch_path or "")
    app_dir = str(relaunch_path.parent) if relaunch_path is not None else ""
    app_name = relaunch_path.name if relaunch_path is not None else ""
    if update_dir is None:
        update_dir = _removable_update_dir(installer_path)
    args = " ".join(installer_args()) + ' /LOG="%SETUPLOG%"'
    lines = [
        "@echo off",
        "setlocal",
        f'set "INSTALLER={_cmd_value(str(installer_path))}"',
        f'set "UPDATEDIR={_cmd_value(str(update_dir) if update_dir else "")}"',
        f'set "APP={_cmd_value(app_path)}"',
        f'set "APPDIR={_cmd_value(app_dir)}"',
        f'set "APPNAME={_cmd_value(app_name)}"',
        f'set "LOG={_cmd_value(str(update_log_path()))}"',
        f'set "SETUPLOG={_cmd_value(str(setup_log_path()))}"',
        f'set "RESULT={_cmd_value(str(update_result_path()))}"',
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
        # The PID above is the window that requested the update, but another
        # app instance (or a slow child process retaining the application
        # mutex) can still make Inno Setup abort in silent mode. Ensure the
        # packaged executable is fully gone before launching Setup.
        'if not "%APPNAME%"=="" (',
        '  >> "%LOG%" echo [%DATE% %TIME%] Closing remaining %APPNAME% processes',
        '  taskkill /F /IM "%APPNAME%" >NUL 2>&1',
        ')',
        "timeout /t 1 /nobreak >NUL",
        '>> "%LOG%" echo [%DATE% %TIME%] Running installer "%INSTALLER%"',
        f'"%INSTALLER%" {args}',
        'set "SETUP_EXIT=%ERRORLEVEL%"',
        '>> "%LOG%" echo [%DATE% %TIME%] Installer exited %SETUP_EXIT%',
        # This process is gone by now, so a file is the only way to report back.
        # The next launch folds it into the update state (see
        # update_checker.consume_installer_result) — without it a Setup that
        # failed looked exactly like one that succeeded.
        '> "%RESULT%" echo exit=%SETUP_EXIT%',
        'if "%SETUP_EXIT%"=="0" (',
        '  >> "%LOG%" echo [%DATE% %TIME%] Last update result: Update installed; setup log "%SETUPLOG%"',
        ') else (',
        '  >> "%LOG%" echo [%DATE% %TIME%] Update FAILED: Setup did not apply the update',
        '  >> "%LOG%" echo [%DATE% %TIME%] Last update result: Installer exited %SETUP_EXIT%; setup log "%SETUPLOG%"',
        ')',
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
    # Cleanup last, and only once Setup has finished with the file. cd out of
    # the directory first so the shell isn't standing in it. The rd also takes
    # this script with it, which is why nothing may follow it: cmd reads the
    # batch file as it goes, and there must be no lines left to read.
    lines += [
        '>> "%LOG%" echo [%DATE% %TIME%] Cleaning up update files',
        'cd /d "%TEMP%"',
        'del /f /q "%INSTALLER%" >NUL 2>&1',
    ]
    if update_dir is not None:
        # `endlocal & set` on one line is the standard way to carry a value out
        # of the setlocal scope: the %UPDATEDIR% on the right is expanded when
        # the line is parsed, before endlocal takes effect.
        lines += [
            'endlocal & set "CLEANUPDIR=%UPDATEDIR%"',
            'rd /s /q "%CLEANUPDIR%"',
        ]
    else:
        lines += ["endlocal", 'del "%~f0"']
    lines.append("")
    return "\r\n".join(lines)


def launch_after_current_process_exits(installer_path: Path) -> None:
    """Start a detached helper that waits for this app, then runs Setup."""
    record_update_result(
        "Applying update",
        detail="Installer helper started; the app will close and relaunch after Setup finishes.",
        extra={"installer_path": str(installer_path)},
    )
    if sys.platform != "win32":
        log_update_event(f"Launching installer directly: {installer_path}")
        subprocess.Popen([str(installer_path)])
        return

    relaunch_path = Path(sys.executable) if getattr(sys, "frozen", False) else None
    update_dir = _removable_update_dir(installer_path)
    # The helper lives beside the installer in the per-update directory so one
    # rd takes both away. If the installer isn't in one of our directories, fall
    # back to %TEMP% and let the script delete only itself.
    helper_dir = update_dir or Path(tempfile.gettempdir())
    helper_path = helper_dir / f"BrightspacePagesAutomator-update-{os.getpid()}.cmd"
    # newline="" is required: the script already joins its lines with \r\n, and
    # the default translation would turn every one of those into \r\r\n. cmd.exe
    # mis-parses the multi-line if/goto block that results, so the helper spins
    # in its wait loop forever and Setup is never launched.
    helper_path.write_text(
        wait_then_install_script(
            installer_path, os.getpid(), relaunch_path, update_dir=update_dir
        ),
        encoding="utf-8",
        newline="",
    )
    log_update_event(f"Installer helper written: {helper_path}")
    log_update_event(f"Installer path: {installer_path}")
    log_update_event(f"Update directory: {update_dir or '(not a managed update directory)'}")
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
