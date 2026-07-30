"""
Self-update check against GitHub Releases.

The app's VERSION constant isn't bumped on every CI run (releases are tagged
v<VERSION>-<run_number>, and a run can ship without a VERSION bump), so the
only reliable way to know "is a newer build available" is to compare our own
exact build tag (baked into the bundle at build time as BUILD_VERSION)
against the latest published release tag — not a semver comparison.
"""
import json
import os
import re
import subprocess
import tempfile
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    from app_version import APP_VERSION
except Exception:
    APP_VERSION = "unknown"

REPO = "schakaloff/brightspace-pages-automator"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
LATEST_WINDOWS_INSTALLER = "BrightspacePagesAutomator-Setup-Latest.exe"
UPDATE_LOG_NAME = "BrightspacePagesAutomator-update.log"
SETUP_LOG_NAME = "BrightspacePagesAutomator-setup.log"
UPDATE_STATE_NAME = "BrightspacePagesAutomator-update-state.json"
UPDATE_CHANNEL = os.environ.get("BPA_UPDATE_CHANNEL", "stable")
UPDATE_BRANCH = os.environ.get("BPA_UPDATE_BRANCH", "main")


def _resource_path(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    return base.joinpath(*parts)


def update_log_path() -> Path:
    return Path(tempfile.gettempdir()) / UPDATE_LOG_NAME


def setup_log_path() -> Path:
    return Path(tempfile.gettempdir()) / SETUP_LOG_NAME


def update_state_path() -> Path:
    return Path(tempfile.gettempdir()) / UPDATE_STATE_NAME


def log_update_event(message: str) -> None:
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with update_log_path().open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def get_my_build_tag() -> str | None:
    """Returns None when running from source (not a packaged build) — in that
    case there's nothing meaningful to compare against, so callers should skip
    the update check entirely."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        return _resource_path("BUILD_VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return None


def get_install_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _resource_path().resolve()


def _git_value(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=str(get_install_path()),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except Exception:
        return None


def get_source_commit() -> str | None:
    if getattr(sys, "frozen", False):
        return None
    return _git_value("rev-parse", "--short", "HEAD")


def get_source_branch() -> str | None:
    if getattr(sys, "frozen", False):
        return None
    return _git_value("branch", "--show-current")


def _version_from_build_tag(build_tag: str | None) -> str:
    if not build_tag:
        return APP_VERSION
    m = re.match(r"^v?([0-9]+(?:\.[0-9]+){1,2})", build_tag)
    return m.group(1) if m else build_tag


def current_build_label() -> str:
    build_tag = get_my_build_tag()
    if build_tag:
        return build_tag
    commit = get_source_commit()
    return f"source ({commit})" if commit else "source/unversioned"


def current_version_label() -> str:
    return _version_from_build_tag(get_my_build_tag())


def update_branch_label() -> str:
    source_branch = get_source_branch()
    if source_branch:
        return f"{UPDATE_BRANCH} (source checkout: {source_branch})"
    return UPDATE_BRANCH


def read_update_state() -> dict:
    try:
        return json.loads(update_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def record_update_result(
    result: str,
    detail: str = "",
    latest_build: str = "",
    extra: dict | None = None,
) -> dict:
    state = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "result": result,
        "detail": detail,
        "current_version": current_version_label(),
        "current_build": current_build_label(),
        "latest_build": latest_build,
        "install_path": str(get_install_path()),
        "update_channel": UPDATE_CHANNEL,
        "update_branch": update_branch_label(),
        "updater_log_path": str(update_log_path()),
        "setup_log_path": str(setup_log_path()),
    }
    if extra:
        state.update(extra)
    try:
        update_state_path().write_text(
            json.dumps(state, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    log_update_event(
        "Update state: "
        f"result={result} "
        f"detail={detail or '(none)'} "
        f"current_build={state['current_build']} "
        f"latest_build={latest_build or '(unknown)'} "
        f"install_path={state['install_path']} "
        f"channel={state['update_channel']} "
        f"branch={state['update_branch']} "
        f"updater_log={state['updater_log_path']} "
        f"setup_log={state['setup_log_path']}"
    )
    return state


def get_update_diagnostics() -> dict:
    state = read_update_state()
    return {
        "current_version": current_version_label(),
        "current_build": current_build_label(),
        "install_path": str(get_install_path()),
        "update_channel": UPDATE_CHANNEL,
        "update_branch": update_branch_label(),
        "last_update_result": state.get("result", "No update check has run yet"),
        "last_update_detail": state.get("detail", ""),
        "last_update_at": state.get("timestamp", ""),
        "latest_build": state.get("latest_build", ""),
        "updater_log_path": str(update_log_path()),
        "setup_log_path": str(setup_log_path()),
    }


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
        f"install_path={get_install_path()} "
        f"channel={UPDATE_CHANNEL} "
        f"branch={update_branch_label()} "
        f"current_build={my_tag or '(source/unversioned)'} "
        f"updater_log={update_log_path()} "
        f"setup_log={setup_log_path()} "
        f"force_install={force_install}"
    )
    if not my_tag and not force_install:
        record_update_result(
            "Skipped",
            "Running from source/unversioned build; automatic update check not applicable.",
        )
        log_update_event("Update check skipped: running from source/unversioned build")
        return None

    release = _fetch_latest_release()
    if not release:
        record_update_result("Failed", "Could not fetch latest GitHub release.")
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
        record_update_result("Up to date", latest_build=latest_tag)
        log_update_event("Update check result: up to date")
        return None

    record_update_result(
        "Update available" if not force_install else "Manual reinstall available",
        latest_build=latest_tag,
        extra={
            "asset_name": (info or {}).get("asset_name"),
            "asset_url": (info or {}).get("asset_url"),
            "release_url": (info or {}).get("html_url"),
        },
    )
    log_update_event("Update check result: update available")
    return info


def download_asset(url: str, dest_path: Path, progress_cb=None) -> None:
    record_update_result(
        "Downloading",
        detail=f"Downloading installer to {dest_path}",
        extra={"download_path": str(dest_path)},
    )
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
    record_update_result(
        "Downloaded",
        detail=f"Installer downloaded to {dest_path}",
        extra={"download_path": str(dest_path)},
    )
