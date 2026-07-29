"""Helpers for launching the Windows self-update installer.

The installer must not start while the app is still holding AppMutex. In silent
mode Inno can treat that as "app is still running" and exit before replacing
files, which looks like a successful update that did nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def installer_args() -> list[str]:
    return [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
    ]


def _ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def wait_then_install_script(
    installer_path: Path,
    pid: int,
    relaunch_path: Path | None = None,
) -> str:
    args = ", ".join(_ps_single_quote(arg) for arg in installer_args())
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; "
        "Start-Sleep -Milliseconds 400; "
        f"Start-Process -FilePath {_ps_single_quote(str(installer_path))} "
        f"-ArgumentList @({args}) -WindowStyle Hidden -Wait; "
    )
    if relaunch_path is not None:
        app_path = str(relaunch_path)
        app_dir = str(relaunch_path.parent)
        script += (
            "Start-Sleep -Milliseconds 700; "
            f"if (Test-Path -LiteralPath {_ps_single_quote(app_path)}) {{ "
            f"Start-Process -FilePath {_ps_single_quote(app_path)} "
            f"-WorkingDirectory {_ps_single_quote(app_dir)} "
            "}"
        )
    return script


def launch_after_current_process_exits(installer_path: Path) -> None:
    """Start a detached helper that waits for this app, then runs Setup."""
    if sys.platform != "win32":
        subprocess.Popen([str(installer_path)])
        return

    relaunch_path = Path(sys.executable) if getattr(sys, "frozen", False) else None
    creationflags = 0
    creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)

    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            wait_then_install_script(installer_path, os.getpid(), relaunch_path),
        ],
        close_fds=True,
        creationflags=creationflags,
    )
