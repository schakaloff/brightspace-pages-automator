"""Where a frozen build tells Playwright to look for Chromium.

Playwright's _transport.py injects PLAYWRIGHT_BROWSERS_PATH=0 for frozen apps,
which sends the driver into the read-only copy inside the .app. Since this app
downloads Chromium on first run instead of bundling it, the variable has to be
set explicitly so that injection can never apply.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "src")

import chromium_setup

VAR = "PLAYWRIGHT_BROWSERS_PATH"


@pytest.fixture
def frozen(monkeypatch):
    """Simulate a PyInstaller build, exactly as Playwright detects one."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\Apps\BPA\_internal", raising=False)
    monkeypatch.delenv(VAR, raising=False)
    return chromium_setup


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    for alias in chromium_setup._BROWSERS_PATH_ALIASES:
        monkeypatch.delenv(alias, raising=False)


# ── the default location ────────────────────────────────────────────────────

@pytest.mark.parametrize("platform,expected", [
    ("darwin", Path.home() / "Library" / "Caches" / "ms-playwright"),
    ("linux", Path.home() / ".cache" / "ms-playwright"),
])
def test_default_path_matches_playwrights_own_convention(platform, expected, monkeypatch):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    assert chromium_setup.default_browsers_path() == expected


def test_windows_default_follows_localappdata(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Someone\AppData\Local")

    assert chromium_setup.default_browsers_path() == Path(
        r"C:\Users\Someone\AppData\Local\ms-playwright"
    )


def test_linux_default_honours_xdg_cache_home(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", "/custom/cache")

    assert chromium_setup.default_browsers_path() == Path("/custom/cache/ms-playwright")


def test_macos_default_is_the_user_library_cache(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    path = chromium_setup.default_browsers_path()

    assert path.as_posix().endswith("Library/Caches/ms-playwright")
    assert ".local-browsers" not in str(path)


# ── frozen builds ───────────────────────────────────────────────────────────

def test_frozen_build_sets_the_cache_path_explicitly(frozen):
    frozen.sanitize_browser_env()

    assert os.environ[VAR] == str(frozen.default_browsers_path())
    assert os.environ[VAR] != "0"


def test_frozen_build_replaces_an_injected_zero(frozen, monkeypatch):
    monkeypatch.setenv(VAR, "0")

    frozen.sanitize_browser_env()

    assert os.environ[VAR] == str(frozen.default_browsers_path())


def test_frozen_build_replaces_a_path_inside_the_bundle(frozen, monkeypatch):
    monkeypatch.setenv(VAR, r"C:\Apps\BPA\_internal\playwright\driver\package\.local-browsers")

    frozen.sanitize_browser_env()

    assert ".local-browsers" not in os.environ[VAR]
    assert os.environ[VAR] == str(frozen.default_browsers_path())


def test_a_deliberate_override_is_left_alone(frozen, monkeypatch):
    monkeypatch.setenv(VAR, r"D:\shared\ms-playwright")

    frozen.sanitize_browser_env()

    assert os.environ[VAR] == r"D:\shared\ms-playwright"


def test_sanitize_is_idempotent(frozen):
    frozen.sanitize_browser_env()
    first = os.environ[VAR]
    frozen.sanitize_browser_env()

    assert os.environ[VAR] == first


def test_alias_variables_are_cleared_when_unusable(frozen, monkeypatch):
    for alias in chromium_setup._BROWSERS_PATH_ALIASES:
        monkeypatch.setenv(alias, "0")

    frozen.sanitize_browser_env()

    for alias in chromium_setup._BROWSERS_PATH_ALIASES:
        assert alias not in os.environ


# ── source runs keep the old behaviour ──────────────────────────────────────

def test_source_run_drops_zero_without_pinning_anything(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv(VAR, "0")

    chromium_setup.sanitize_browser_env()

    # Nothing injects a value outside a frozen build, so absence is correct.
    assert VAR not in os.environ


def test_source_run_keeps_a_deliberate_override(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv(VAR, "/opt/ms-playwright")

    chromium_setup.sanitize_browser_env()

    assert os.environ[VAR] == "/opt/ms-playwright"


# ── what the Node driver actually receives ──────────────────────────────────

def _driver_env_for_frozen_app() -> dict:
    """Reproduce playwright/_impl/_transport.py's env construction verbatim."""
    from playwright._impl._driver import get_driver_env

    env = get_driver_env()
    if getattr(sys, "frozen", False) or globals().get("__compiled__"):
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    return env


def test_driver_env_is_not_zero_for_a_frozen_app(frozen):
    """The regression itself: this is the value the driver process sees."""
    frozen.sanitize_browser_env()

    env = _driver_env_for_frozen_app()

    assert env["PLAYWRIGHT_BROWSERS_PATH"] != "0"
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(frozen.default_browsers_path())
    assert ".local-browsers" not in env["PLAYWRIGHT_BROWSERS_PATH"]


def test_without_the_fix_the_driver_env_would_be_zero(frozen):
    """Guards the guard: proves the assertion above is actually load-bearing."""
    os.environ.pop(VAR, None)  # what unsetting the variable used to leave behind

    assert _driver_env_for_frozen_app()["PLAYWRIGHT_BROWSERS_PATH"] == "0"


def test_playwright_still_injects_via_setdefault():
    """Canary: if upstream stops using setdefault, the fix needs revisiting."""
    from playwright._impl import _transport

    source = Path(_transport.__file__).read_text(encoding="utf-8")

    assert 'env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")' in source
    assert 'getattr(sys, "frozen", False)' in source


# ── detection agrees with the pinned location ───────────────────────────────

def test_detection_looks_in_the_pinned_registry(frozen, tmp_path, monkeypatch):
    registry = tmp_path / "ms-playwright"
    exe = registry / "chromium-1234" / "chrome-mac-arm64" / \
        "Google Chrome for Testing.app" / "Contents" / "MacOS" / \
        "Google Chrome for Testing"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"chrome")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(chromium_setup, "default_browsers_path", lambda: registry)

    frozen.sanitize_browser_env()

    assert chromium_setup.effective_browsers_path() == registry
    assert chromium_setup._chromium_in_registry() is True


def test_detection_reports_missing_when_the_registry_is_empty(frozen, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(chromium_setup, "default_browsers_path", lambda: tmp_path / "empty")
    monkeypatch.setattr(chromium_setup, "_LEGACY_HOME_PATTERNS", ())

    frozen.sanitize_browser_env()

    assert chromium_setup._chromium_in_registry() is False
