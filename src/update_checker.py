"""
Self-update check against GitHub Releases.

The app's VERSION constant isn't bumped on every CI run (releases are tagged
v<VERSION>-<run_number>, and a run can ship without a VERSION bump), so the
only reliable way to know "is a newer build available" is to compare our own
exact build tag (baked into the bundle at build time as BUILD_VERSION)
against the latest published release tag.

Tags are ordered rather than merely compared for equality: v0.8.5-98 parses to
(0, 8, 5, 98), so a release that is older than the installed build (which is
what /releases/latest returns once a newer release is deleted) can never be
offered as an update.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import ssl
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
# Written by the installer helper (a .cmd, so: plain "exit=<code>" text rather
# than JSON) and folded into the update state on the next launch.
UPDATE_RESULT_NAME = "BrightspacePagesAutomator-update-result.txt"
CHECKSUM_ASSET_NAME = "SHA256SUMS.txt"
UPDATE_DIR_PREFIX = "BrightspacePagesAutomator-update-"
# Carried across record_update_result() calls so the outcome of the last actual
# install survives the "Up to date" check that runs seconds after launch.
_STICKY_KEYS = (
    "last_install_result",
    "last_install_detail",
    "last_install_at",
    "setup_exit_code",
)


API_TIMEOUT_SECONDS = 10


class UpdateIntegrityError(Exception):
    """The downloaded installer could not be proven to match the release."""


class UpdateFetchError(Exception):
    """The release lookup failed, with the reason preserved.

    Every failure used to collapse into "check your internet connection",
    which is wrong for a rate limit, a certificate problem or a captive
    portal — and left nothing in the log to tell them apart. The status code
    and exception reason travel with the error so both the diagnostics and
    the message the user reads can name the real cause.
    """

    def __init__(self, kind: str, detail: str, user_message: str, status: int | None = None):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.user_message = user_message
        self.status = status
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


def update_result_path() -> Path:
    return Path(tempfile.gettempdir()) / UPDATE_RESULT_NAME


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


def get_my_build_commit() -> str | None:
    if not getattr(sys, "frozen", False):
        return get_source_commit()
    try:
        return _resource_path("BUILD_COMMIT").read_text(encoding="utf-8").strip()
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


_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-(\d+))?$")


def parse_build_tag(tag: str | None) -> tuple[int, int, int, int] | None:
    """v0.8.5-98 -> (0, 8, 5, 98). None for anything that isn't that shape.

    A missing patch or run number counts as 0, so v0.8 and v0.8.5 still order
    against v0.8.5-98. Legacy or hand-made tags (v1.0-beta, "nightly") return
    None and callers fall back to the old equality check rather than crashing.
    """
    if not tag:
        return None
    m = _TAG_RE.match(tag.strip())
    if not m:
        return None
    major, minor, patch, run = m.groups()
    return (int(major), int(minor), int(patch or 0), int(run or 0))


def is_newer_build(latest_tag: str | None, current_tag: str | None) -> bool | None:
    """True/False when both tags order, None when they can't be compared."""
    latest = parse_build_tag(latest_tag)
    current = parse_build_tag(current_tag)
    if latest is None or current is None:
        return None
    return latest > current


def _version_from_build_tag(build_tag: str | None) -> str:
    if not build_tag:
        return APP_VERSION
    m = re.match(r"^v?([0-9]+(?:\.[0-9]+){1,2})", build_tag)
    return m.group(1) if m else build_tag


def current_build_label() -> str:
    build_tag = get_my_build_tag()
    commit = get_my_build_commit()
    if build_tag:
        if commit:
            return f"{build_tag} ({commit})"
        return build_tag
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
    previous = read_update_state()
    state = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "result": result,
        "detail": detail,
        "current_version": current_version_label(),
        "current_build": current_build_label(),
        "current_commit": get_my_build_commit() or "",
        "latest_build": latest_build,
        "install_path": str(get_install_path()),
        "update_channel": UPDATE_CHANNEL,
        "update_branch": update_branch_label(),
        "updater_log_path": str(update_log_path()),
        "setup_log_path": str(setup_log_path()),
    }
    # The result of the last real install outlives the routine check that
    # follows it, so a failed update can't be papered over by "Up to date".
    for key in _STICKY_KEYS:
        if key in previous:
            state[key] = previous[key]
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
        "current_commit": get_my_build_commit() or "",
        "install_path": str(get_install_path()),
        "update_channel": UPDATE_CHANNEL,
        "update_branch": update_branch_label(),
        "last_update_result": state.get("result", "No update check has run yet"),
        "last_update_detail": state.get("detail", ""),
        "last_update_at": state.get("timestamp", ""),
        "latest_build": state.get("latest_build", ""),
        "updater_log_path": str(update_log_path()),
        "setup_log_path": str(setup_log_path()),
        "last_install_result": state.get("last_install_result", ""),
        "last_install_detail": state.get("last_install_detail", ""),
        "last_install_at": state.get("last_install_at", ""),
        "setup_exit_code": state.get("setup_exit_code", ""),
        # Present only when the last check failed; cleared by the next success,
        # since record_update_result rebuilds the state from scratch.
        "fetch_error_kind": state.get("fetch_error_kind", ""),
        "fetch_http_status": state.get("fetch_http_status", ""),
        "fetch_user_message": state.get("fetch_user_message", ""),
    }


def consume_installer_result() -> dict | None:
    """Fold the helper's Setup exit code into the update state, once.

    The helper runs after this process is gone, so the only way it can report
    back is a file. Reading it at the next launch (and deleting it) turns a
    silent failed install into something Settings and the badge can show.
    """
    path = update_result_path()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    code = None
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "exit":
            try:
                code = int(value.strip())
            except ValueError:
                code = None
            break

    try:
        path.unlink()
    except OSError:
        pass

    if code == 0:
        result = "Update installed"
        detail = "Installer finished successfully (exit code 0)."
    elif code is None:
        result = "Update failed"
        detail = (
            "The installer helper did not report an exit code. "
            f"See the setup log at {setup_log_path()}."
        )
    else:
        result = "Update failed"
        detail = (
            f"Installer exited with code {code} — the update was NOT applied. "
            f"See the setup log at {setup_log_path()}."
        )

    log_update_event(f"Installer result consumed: exit={code if code is not None else '(missing)'}")
    return record_update_result(
        result,
        detail=detail,
        extra={
            "last_install_result": result,
            "last_install_detail": detail,
            "last_install_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "setup_exit_code": "" if code is None else code,
        },
    )


def new_update_dir() -> Path:
    """A fresh private directory per update attempt.

    Downloading to a fixed %TEMP% name means any local process can predict —
    and replace — the file that is about to be executed with installer
    privileges. mkdtemp gives a name nothing can guess ahead of time.
    """
    return Path(tempfile.mkdtemp(prefix=UPDATE_DIR_PREFIX))


def cleanup_update_dir(path: Path | None) -> None:
    """Best-effort removal of an update directory whose installer never ran."""
    if path is None:
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
        log_update_event(f"Update directory removed: {path}")
    except Exception:
        pass


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums(text: str) -> dict:
    """Parse `sha256sum` output: "<64 hex>  <filename>" per line.

    Blank lines and comments are skipped; anything else that doesn't match the
    shape is ignored rather than fatal, so one stray line can't void the file.
    """
    sums = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip().lower(), parts[1].strip().lstrip("*")
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            continue
        sums[Path(name).name] = digest
    return sums


def fetch_expected_sha256(checksum_url: str | None, asset_name: str | None) -> str:
    """Expected digest for asset_name, or UpdateIntegrityError explaining why not."""
    if not checksum_url:
        raise UpdateIntegrityError(
            f"This release does not publish {CHECKSUM_ASSET_NAME}, so the installer "
            "cannot be verified."
        )
    if not asset_name:
        raise UpdateIntegrityError("This release has no installer to verify.")
    try:
        req = urllib.request.Request(
            checksum_url, headers={"Accept": "application/octet-stream"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise UpdateIntegrityError(f"Could not download {CHECKSUM_ASSET_NAME}: {e}") from e

    sums = parse_checksums(text)
    if not sums:
        raise UpdateIntegrityError(
            f"{CHECKSUM_ASSET_NAME} is malformed — no usable SHA-256 lines found."
        )
    digest = sums.get(Path(asset_name).name)
    if not digest:
        raise UpdateIntegrityError(
            f"{CHECKSUM_ASSET_NAME} does not list {asset_name}."
        )
    return digest


def verify_installer_checksum(installer_path: Path, expected_sha256: str) -> str:
    """Raise UpdateIntegrityError unless the file on disk matches expected."""
    actual = sha256_of_file(installer_path)
    expected = (expected_sha256 or "").strip().lower()
    log_update_event(
        f"Checksum check: file={installer_path} expected={expected or '(none)'} actual={actual}"
    )
    if actual != expected:
        raise UpdateIntegrityError(
            "The downloaded installer does not match the checksum published with "
            f"the release (expected {expected or '(none)'}, got {actual})."
        )
    return actual


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


def _rate_limit_reset_label(headers) -> str:
    """"14:20:54" from X-RateLimit-Reset, or "" if the header is unusable."""
    try:
        reset = int(headers.get("X-RateLimit-Reset", ""))
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.fromtimestamp(reset).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def _http_error(e: urllib.error.HTTPError) -> UpdateFetchError:
    headers = e.headers or {}
    remaining = headers.get("X-RateLimit-Remaining")
    # GitHub answers an exhausted unauthenticated quota with 403 (older) or
    # 429 (newer) and Remaining: 0. Nothing is wrong with the connection, so
    # saying "check your internet" sends people chasing the wrong thing.
    if e.code in (403, 429) and remaining == "0":
        reset_at = _rate_limit_reset_label(headers)
        detail = (
            f"HTTP {e.code}: GitHub API rate limit exceeded "
            f"(X-RateLimit-Remaining=0"
            + (f", resets at {reset_at}" if reset_at else "")
            + ")"
        )
        message = (
            "GitHub is limiting how often update checks can run from this "
            "network, so the check was refused"
            + (f". Updates should work again after {reset_at}." if reset_at
               else ". Try again later.")
            + "\n\nYour internet connection is fine — this limit is shared by "
            "everyone on the same network."
        )
        return UpdateFetchError("rate_limit", detail, message, status=e.code)

    reason = e.reason or ""
    detail = f"HTTP {e.code}: {reason}"
    message = (
        f"GitHub refused the update check with HTTP {e.code} ({reason}).\n\n"
        "This is a problem at GitHub's end or with how this network reaches "
        "it, not with the app."
    )
    return UpdateFetchError("http", detail, message, status=e.code)


def _url_error(e: urllib.error.URLError) -> UpdateFetchError:
    reason = e.reason
    if isinstance(reason, ssl.SSLError):
        code = getattr(reason, "reason", "") or type(reason).__name__
        detail = f"SSL failure: {code}: {reason}"
        message = (
            "The secure connection to GitHub could not be verified "
            f"({code}).\n\n"
            "This is a certificate problem on this computer or network, not a "
            "connection problem — the app can reach GitHub but cannot confirm "
            "it is really GitHub."
        )
        return UpdateFetchError("ssl", detail, message)

    if isinstance(reason, (socket.timeout, TimeoutError)):
        return _timeout_error()

    detail = f"{type(reason).__name__ if reason is not None else 'URLError'}: {reason}"
    message = (
        f"Could not reach github.com ({reason}).\n\n"
        "Check your internet connection and try again."
    )
    return UpdateFetchError("network", detail, message)


def _timeout_error() -> UpdateFetchError:
    detail = f"Timed out after {API_TIMEOUT_SECONDS}s"
    message = (
        f"The update check timed out after {API_TIMEOUT_SECONDS} seconds.\n\n"
        "The network may be slow, or something on it may be blocking "
        "github.com."
    )
    return UpdateFetchError("timeout", detail, message)


def _malformed_error(e: Exception) -> UpdateFetchError:
    detail = f"Malformed response: {type(e).__name__}: {e}"
    message = (
        "GitHub's reply could not be read, so the update check was abandoned."
        "\n\nA sign-in page or web filter may be intercepting the connection."
    )
    return UpdateFetchError("malformed", detail, message)


def _fetch_latest_release() -> dict | None:
    """The latest release, or UpdateFetchError naming why not.

    Returning None is still honoured by the caller as an unexplained failure,
    which keeps the contract for anything that stubs this out.
    """
    try:
        req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Subclass of URLError, so it has to be caught first or a status code
        # is silently downgraded to a generic network failure.
        raise _http_error(e) from e
    except urllib.error.URLError as e:
        raise _url_error(e) from e
    except (socket.timeout, TimeoutError) as e:
        raise _timeout_error() from e
    except ValueError as e:
        # json.JSONDecodeError and UnicodeDecodeError are both ValueErrors.
        raise _malformed_error(e) from e
    except OSError as e:
        # Anything urllib let through unwrapped (connection reset, no route).
        raise UpdateFetchError(
            "network",
            f"{type(e).__name__}: {e}",
            f"Could not reach github.com ({e}).\n\n"
            "Check your internet connection and try again.",
        ) from e


def _pick_checksum_asset(assets: list) -> dict | None:
    for asset in assets:
        if asset.get("name") == CHECKSUM_ASSET_NAME:
            return asset
    return None


def _release_info(
    release: dict,
    force_install: bool = False,
    same_build: bool = False,
) -> dict | None:
    latest_tag = release.get("tag_name", "")
    if not latest_tag:
        return None

    assets = release.get("assets", [])
    asset = _pick_asset(assets)
    checksum = _pick_checksum_asset(assets)
    return {
        "tag": latest_tag,
        "body": release.get("body") or "(no changelog provided)",
        "html_url": release.get("html_url", ""),
        "asset_url": asset.get("browser_download_url") if asset else None,
        "asset_name": asset.get("name") if asset else None,
        "checksum_url": checksum.get("browser_download_url") if checksum else None,
        "checksum_name": checksum.get("name") if checksum else None,
        "force_install": force_install,
        # True only when the user deliberately asked to reinstall the build they
        # are already running — the dialog says "reinstall", not "update".
        "same_build": same_build,
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
        f"current_commit={get_my_build_commit() or '(unknown)'} "
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

    try:
        release = _fetch_latest_release()
    except UpdateFetchError as e:
        record_update_result(
            "Failed",
            e.detail,
            extra={
                "fetch_error_kind": e.kind,
                "fetch_http_status": e.status if e.status is not None else "",
                "fetch_user_message": e.user_message,
            },
        )
        log_update_event(
            "Update check failed: "
            f"kind={e.kind} "
            f"status={e.status if e.status is not None else '(none)'} "
            f"url={API_URL} "
            f"detail={e.detail}"
        )
        return None

    if not release:
        record_update_result("Failed", "Could not fetch latest GitHub release.")
        log_update_event("Update check failed: could not fetch latest release")
        return None

    latest_tag = release.get("tag_name", "")
    same_build = bool(latest_tag) and latest_tag == my_tag
    newer = is_newer_build(latest_tag, my_tag)
    info = _release_info(
        release, force_install=force_install, same_build=same_build
    )
    log_update_event(
        "Update check latest: "
        f"latest_build={latest_tag or '(missing)'} "
        f"latest_order={parse_build_tag(latest_tag)} "
        f"current_order={parse_build_tag(my_tag)} "
        f"newer={newer if newer is not None else '(not comparable)'} "
        f"asset_name={(info or {}).get('asset_name')} "
        f"asset_url={(info or {}).get('asset_url')} "
        f"checksum_url={(info or {}).get('checksum_url')}"
    )

    if not force_install:
        if not latest_tag or same_build:
            record_update_result("Up to date", latest_build=latest_tag)
            log_update_event("Update check result: up to date")
            return None
        if newer is False:
            # /releases/latest returns whatever is newest *now*, which can be
            # older than this build once a release is deleted or unpublished.
            # Offering it would silently downgrade the user.
            record_update_result(
                "Up to date",
                detail=(
                    f"Latest release {latest_tag} is not newer than the installed "
                    f"build {my_tag}."
                ),
                latest_build=latest_tag,
            )
            log_update_event("Update check result: latest release is not newer; ignoring")
            return None
        if newer is None:
            log_update_event(
                "Update check: tags are not orderable "
                f"(current={my_tag!r} latest={latest_tag!r}); "
                "falling back to an inequality comparison"
            )

    if force_install and same_build:
        result_label = "Reinstall available"
    elif force_install:
        result_label = "Manual reinstall available"
    else:
        result_label = "Update available"

    record_update_result(
        result_label,
        latest_build=latest_tag,
        extra={
            "asset_name": (info or {}).get("asset_name"),
            "asset_url": (info or {}).get("asset_url"),
            "checksum_url": (info or {}).get("checksum_url"),
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
