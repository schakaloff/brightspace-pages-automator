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
# is a read-only, code-signed directory.
#
# Playwright *injects* that 0 for us. playwright/_impl/_transport.py does:
#
#     if getattr(sys, "frozen", False) or globals().get("__compiled__"):
#         env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
#
# because its documented PyInstaller recipe assumes the browser was bundled
# next to the executable. This app deliberately downloads Chromium on first run
# instead, so that assumption is wrong here and has to be overridden.
#
# Note it is setdefault: the injection happens precisely *because* the key is
# absent, so unsetting the variable guarantees the bad value rather than
# avoiding it. Setting it explicitly is what makes the injection a no-op.
#
# Worse, the two code paths disagreed. playwright/__main__.py (which
# install_chromium runs) builds its env from get_driver_env() alone and never
# applies the frozen override, so the download landed in the per-user cache
# while the launch looked in .local-browsers — a first-run install that
# "worked", followed by "Executable doesn't exist at …".
_BROWSERS_PATH_VAR = "PLAYWRIGHT_BROWSERS_PATH"
# Aliases the node driver falls back to (see getFromENV in the driver bundle).
# Only ever cleared, never set: the primary variable above wins when present.
_BROWSERS_PATH_ALIASES = (
    "npm_config_playwright_browsers_path",
    "npm_package_config_playwright_browsers_path",
)


def _bundle_root() -> Path | None:
    """Directory the frozen app was unpacked into, or None when running from source."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def default_browsers_path() -> Path:
    """Where Playwright keeps browsers when nothing overrides the location.

    Mirrors defaultCacheDirectory in the driver's own registry so that what we
    set here is the directory Playwright would have chosen by itself — this is
    a restatement of its default, not a new location of ours.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ms-playwright"


def _is_unusable(value: str, bundle: Path | None) -> bool:
    """True for values that would put browsers inside the packaged app."""
    if value.strip() == "0":
        return True
    if bundle is None:
        return False
    try:
        resolved = Path(value).expanduser().resolve()
    except (OSError, ValueError):
        return True
    return resolved == bundle or bundle in resolved.parents


def sanitize_browser_env() -> None:
    """Pin the browser registry to the normal per-user cache.

    A deliberate PLAYWRIGHT_BROWSERS_PATH pointing somewhere outside the bundle
    is left alone — someone who sets it means it. Otherwise: a frozen build gets
    the default cache path written in explicitly (so Playwright's setdefault
    can't substitute "0"), and a source run just has any unusable value removed,
    since nothing injects anything there.
    """
    bundle = _bundle_root()
    frozen = bool(getattr(sys, "frozen", False))

    value = os.environ.get(_BROWSERS_PATH_VAR)
    if value is None or _is_unusable(value, bundle):
        if frozen:
            os.environ[_BROWSERS_PATH_VAR] = str(default_browsers_path())
        else:
            os.environ.pop(_BROWSERS_PATH_VAR, None)

    for alias in _BROWSERS_PATH_ALIASES:
        alias_value = os.environ.get(alias)
        if alias_value is not None and _is_unusable(alias_value, bundle):
            os.environ.pop(alias, None)


def effective_browsers_path() -> Path:
    """The registry directory Playwright will actually use, after sanitizing.

    Detection, installation and launch all read this, so they cannot drift onto
    different directories the way they did before.
    """
    value = os.environ.get(_BROWSERS_PATH_VAR)
    if value and value.strip() != "0":
        return Path(value)
    return default_browsers_path()


# Executable layout inside the registry, per platform. Kept in sync with
# Playwright's EXECUTABLE_PATHS table (chromium is a Chrome for Testing build
# since 1.49 — the old chrome-mac/Chromium.app layout is long gone).
_EXECUTABLE_PATTERNS = {
    "win32": "chromium-*/chrome-win64/chrome.exe",
    "darwin": "chromium-*/chrome-mac*/Google Chrome for Testing.app"
              "/Contents/MacOS/Google Chrome for Testing",
    "linux": "chromium-*/chrome-linux*/chrome",
}

_LEGACY_HOME_PATTERNS = (
    "AppData/Local/ms-playwright/chromium-*/chrome-win64/chrome.exe",
    ".cache/ms-playwright/chromium-*/chrome-linux*/chrome",
    "Library/Caches/ms-playwright/chromium-*/chrome-mac*/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
)


def _chromium_in_registry() -> bool:
    """Look for chromium in the registry Playwright is pointed at right now."""
    pattern = _EXECUTABLE_PATTERNS.get(
        sys.platform, _EXECUTABLE_PATTERNS["linux"]
    )
    if glob.glob(str(effective_browsers_path() / pattern)):
        return True
    # Older builds installed relative to the home directory before the registry
    # path was pinned; a browser already sitting there is still usable.
    home = Path.home()
    return any(glob.glob(str(home / legacy)) for legacy in _LEGACY_HOME_PATTERNS)


def is_chromium_installed() -> bool:
    """
    Fast path first — a glob hit avoids spawning the Node driver just to get a
    file path. On a miss, ask Playwright itself where it expects chromium, so a
    stale pattern here can only cost an extra check, never a false "missing"
    that re-downloads the browser on every launch.
    """
    sanitize_browser_env()
    if _chromium_in_registry():
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
