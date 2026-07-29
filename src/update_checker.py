"""
Self-update check against GitHub Releases.

The app's VERSION constant isn't bumped on every CI run (releases are tagged
v<VERSION>-<run_number>, and a run can ship without a VERSION bump), so the
only reliable way to know "is a newer build available" is to compare our own
exact build tag (baked into the bundle at build time as BUILD_VERSION)
against the latest published release tag — not a semver comparison.
"""
import json
import tempfile
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = "schakaloff/brightspace-pages-automator"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
LATEST_WINDOWS_INSTALLER = "BrightspacePagesAutomator-Setup-Latest.exe"
UPDATE_LOG_NAME = "BrightspacePagesAutomator-update.log"


def _resource_path(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    return base.joinpath(*parts)


def update_log_path() -> Path:
    return Path(tempfile.gettempdir()) / UPDATE_LOG_NAME


def log_update_event(message: str) -> None:
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with update_log_path().open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def get_my_build_tag() -> str:
    """Returns None when running from source (not a packaged build) — in that
    case there's nothing meaningful to compare against, so callers should skip
    the update check entirely."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        return _resource_path("BUILD_VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _pick_asset(assets: list) -> dict | None:
    suffix = ".exe" if sys.platform == "win32" else ".dmg"
    if sys.platform == "win32":
        for asset in assets:
            if asset.get("name") == LATEST_WINDOWS_INSTALLER:
                return asset
    for asset in assets:
        if asset.get("name", "").endswith(suffix):
            return asset
    return None


def _fetch_latest_release() -> dict | None:
    try:
        req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def _release_info(release: dict, force_install: bool = False) -> dict | None:
    latest_tag = release.get("tag_name", "")
    if not latest_tag:
        return None

    asset = _pick_asset(release.get("assets", []))
    return {
        "tag": latest_tag,
        "body": release.get("body") or "(no changelog provided)",
        "html_url": release.get("html_url", ""),
        "asset_url": asset.get("browser_download_url") if asset else None,
        "asset_name": asset.get("name") if asset else None,
        "force_install": force_install,
    }


def check_for_update(force_install: bool = False) -> dict | None:
    """Returns a dict with tag/body/html_url/asset info if a newer build is
    published, or None if we're up to date / running from source / offline.

    force_install=True deliberately returns the latest release even when the
    current build tag matches, so users can repair a stale or broken install by
    reinstalling the newest installer.
    """
    my_tag = get_my_build_tag()
    log_update_event(
        "Update check: "
        f"frozen={getattr(sys, 'frozen', False)} "
        f"platform={sys.platform} "
        f"executable={sys.executable} "
        f"current_build={my_tag or '(source/unversioned)'} "
        f"force_install={force_install}"
    )
    if not my_tag and not force_install:
        log_update_event("Update check skipped: running from source/unversioned build")
        return None

    release = _fetch_latest_release()
    if not release:
        log_update_event("Update check failed: could not fetch latest release")
        return None

    latest_tag = release.get("tag_name", "")
    info = _release_info(release, force_install=force_install)
    log_update_event(
        "Update check latest: "
        f"latest_build={latest_tag or '(missing)'} "
        f"asset_name={(info or {}).get('asset_name')} "
        f"asset_url={(info or {}).get('asset_url')}"
    )
    if not force_install and (not latest_tag or latest_tag == my_tag):
        log_update_event("Update check result: up to date")
        return None

    log_update_event("Update check result: update available")
    return info


def download_asset(url: str, dest_path: Path, progress_cb=None) -> None:
    log_update_event(f"Download started: url={url} dest={dest_path}")
    req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        log_update_event(f"Download response: content_length={total or 'unknown'}")
        read = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if progress_cb and total:
                    progress_cb(int(read * 100 / total))
    log_update_event(f"Download finished: dest={dest_path} bytes={dest_path.stat().st_size}")
