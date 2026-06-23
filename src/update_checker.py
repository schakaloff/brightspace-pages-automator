"""
Self-update check against GitHub Releases.

The app's VERSION constant isn't bumped on every CI run (releases are tagged
v<VERSION>-<run_number>, and a run can ship without a VERSION bump), so the
only reliable way to know "is a newer build available" is to compare our own
exact build tag (baked into the bundle at build time as BUILD_VERSION)
against the latest published release tag — not a semver comparison.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "schakaloff/brightspace-pages-automator"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"


def _resource_path(*parts) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    return base.joinpath(*parts)


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
    for asset in assets:
        if asset.get("name", "").endswith(suffix):
            return asset
    return None


def check_for_update() -> dict | None:
    """Returns a dict with tag/body/html_url/asset info if a newer build is
    published, or None if we're up to date / running from source / offline."""
    my_tag = get_my_build_tag()
    if not my_tag:
        return None

    try:
        req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            release = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None

    latest_tag = release.get("tag_name", "")
    if not latest_tag or latest_tag == my_tag:
        return None

    asset = _pick_asset(release.get("assets", []))
    return {
        "tag": latest_tag,
        "body": release.get("body") or "(no changelog provided)",
        "html_url": release.get("html_url", ""),
        "asset_url": asset.get("browser_download_url") if asset else None,
        "asset_name": asset.get("name") if asset else None,
    }


def download_asset(url: str, dest_path: Path, progress_cb=None) -> None:
    req = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", 0))
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
