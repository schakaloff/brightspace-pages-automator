"""Windows single-instance guard.

The app has always created a named mutex so Inno Setup's AppMutex check can
tell it is running. Creating it was never enough on its own: CreateMutexW
succeeds for the second process too, and only GetLastError() reveals that
someone got there first. Without that check two windows can end up open — most
visibly right after an update, when the installer relaunches the app.

The handle must stay referenced for the lifetime of the process: Windows frees
the mutex when the last handle closes, and Setup's AppMutex probe is how it
knows to wait for us.
"""

from __future__ import annotations

import sys

from config import APP_MUTEX_NAME

ERROR_ALREADY_EXISTS = 183


def claim_single_instance(name: str = APP_MUTEX_NAME) -> tuple[object | None, bool]:
    """Returns (handle, already_running).

    already_running is True when another process already holds the mutex, in
    which case this process should exit instead of opening a second window.
    Off Windows, and on any failure to talk to the API, returns (None, False) —
    a broken guard must never stop the app from starting.
    """
    if sys.platform != "win32":
        return None, False
    try:
        import ctypes
        from ctypes import wintypes

        # use_last_error routes GetLastError through ctypes' own storage, so it
        # can't be clobbered between the call and reading it.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateMutexW
        # restype must be set: the default c_int truncates a 64-bit HANDLE,
        # which yields a handle that cannot be closed or checked.
        create.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE

        handle = create(None, False, name)
        already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if not handle:
            return None, False
        return handle, already_running
    except Exception:
        return None, False
