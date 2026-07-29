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

from config import RELAUNCH_SWITCH


def installer_args() -> list[str]:
    return [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        RELAUNCH_SWITCH,
    ]


def _ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def wait_then_install_script(installer_path: Path, pid: int) -> str:
    args = ", ".join(_ps_single_quote(arg) for arg in installer_args())
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; "
        "Start-Sleep -Milliseconds 400; "
        f"Start-Process -FilePath {_ps_single_quote(str(installer_path))} "
        f"-ArgumentList @({args}) -WindowStyle Hidden"
    )


def launch_after_current_process_exits(installer_path: Path) -> None:
    """Start a detached helper that waits for this app, then runs Setup."""
    if sys.platform != "win32":
        subprocess.Popen([str(installer_path)])
        return

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
            wait_then_install_script(installer_path, os.getpid()),
        ],
        close_fds=True,
        creationflags=creationflags,
    )
