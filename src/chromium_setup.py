"""
First-run Chromium presence check + installer.
Installers ship without the ~300MB Chromium binary; this module detects a
missing install and fetches it on first launch instead.
"""
import glob
import io
import os
import sys
from pathlib import Path

# Env vars Playwright's node driver consults when resolving its browser
# registry. PLAYWRIGHT_BROWSERS_PATH=0 makes it look inside the shipped driver
# package (…/driver/package/.local-browsers) — which, in a frozen macOS .app,
# is a read-only, code-signed directory. Writing there fails or invalidates the
# signature, so a stray value from the user's shell must never reach the driver.
_BROWSERS_PATH_VARS = (
    "PLAYWRIGHT_BROWSERS_PATH",
    "npm_config_playwright_browsers_path",
    "npm_package_config_playwright_browsers_path",
)


def _bundle_root() -> Path | None:
    """Directory the frozen app was unpacked into, or None when running from source."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def sanitize_browser_env() -> None:
    """
    Drop PLAYWRIGHT_BROWSERS_PATH values that would send the browser registry
    inside the packaged app. "0" is always rejected; an explicit path is kept
    unless it points into the bundle. Anything else the user set is honoured.
    """
    bundle = _bundle_root()
    for var in _BROWSERS_PATH_VARS:
        value = os.environ.get(var)
        if value is None:
            continue
        if value.strip() == "0":
            os.environ.pop(var, None)
            continue
        if bundle is None:
            continue
        try:
            resolved = Path(value).expanduser().resolve()
        except (OSError, ValueError):
            os.environ.pop(var, None)
            continue
        if resolved == bundle or bundle in resolved.parents:
            os.environ.pop(var, None)


# Fallback globs, used only if Playwright itself can't be asked. Kept in sync
# with Playwright's EXECUTABLE_PATHS table (chromium is a Chrome for Testing
# build since 1.49 — the old chrome-mac/Chromium.app layout is long gone).
_FALLBACK_PATTERNS = (
    "AppData/Local/ms-playwright/chromium-*/chrome-win64/chrome.exe",
    ".cache/ms-playwright/chromium-*/chrome-linux*/chrome",
    "Library/Caches/ms-playwright/chromium-*/chrome-mac*/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
)


def _fallback_chromium_installed() -> bool:
    home = Path.home()
    return any(glob.glob(str(home / pattern)) for pattern in _FALLBACK_PATTERNS)


def is_chromium_installed() -> bool:
    """
    Fast path first — a glob hit avoids spawning the Node driver just to get a
    file path. On a miss, ask Playwright itself where it expects chromium, so a
    stale pattern here can only cost an extra check, never a false "missing"
    that re-downloads the browser on every launch.
    """
    sanitize_browser_env()
    if _fallback_chromium_installed():
        return True
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def install_chromium(progress_cb) -> tuple[bool, str]:
    """
    Runs Playwright's own installer in-process (not via subprocess: a frozen
    build's sys.executable is the app itself, not a python interpreter, so
    `sys.executable -m playwright` would not work once packaged).
    """
    sanitize_browser_env()
    from playwright.__main__ import main as playwright_main

    old_argv = sys.argv
    old_stdout = sys.stdout
    buf = io.StringIO()

    class _Tee:
        def write(self, text):
            buf.write(text)
            for line in text.splitlines():
                if line.strip():
                    progress_cb(line)
            return len(text)

        def flush(self):
            pass

    sys.argv = ["playwright", "install", "chromium"]
    sys.stdout = _Tee()
    try:
        playwright_main()
        return True, ""
    except SystemExit as e:
        if e.code in (0, None):
            return True, ""
        return False, buf.getvalue()[-2000:]
    except Exception as e:
        return False, str(e)
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
