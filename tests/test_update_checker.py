import sys
from pathlib import Path

sys.path.insert(0, "src")

import update_checker


def _workspace_temp() -> Path:
    path = Path("tmp_update_tests").resolve()
    path.mkdir(exist_ok=True)
    return path


def _release(tag="v0.8.0-99"):
    return {
        "tag_name": tag,
        "body": "notes",
        "html_url": "https://example.test/release",
        "assets": [
            {
                "name": "BrightspacePagesAutomator-Setup-0.8.0.exe",
                "browser_download_url": "https://example.test/versioned.exe",
            },
            {
                "name": update_checker.LATEST_WINDOWS_INSTALLER,
                "browser_download_url": "https://example.test/latest.exe",
            },
        ],
    }


def test_force_install_returns_latest_even_when_tag_matches(monkeypatch):
    monkeypatch.setattr(update_checker, "get_my_build_tag", lambda: "v0.8.0-99")
    monkeypatch.setattr(update_checker, "_fetch_latest_release", lambda: _release())

    release = update_checker.check_for_update(force_install=True)

    assert release["tag"] == "v0.8.0-99"
    assert release["force_install"] is True


def test_normal_check_skips_matching_tag(monkeypatch):
    monkeypatch.setattr(update_checker, "get_my_build_tag", lambda: "v0.8.0-99")
    monkeypatch.setattr(update_checker, "_fetch_latest_release", lambda: _release())

    assert update_checker.check_for_update() is None


def test_windows_asset_prefers_stable_latest_name(monkeypatch):
    monkeypatch.setattr(update_checker.sys, "platform", "win32")

    release = update_checker._release_info(_release())

    assert release["asset_name"] == update_checker.LATEST_WINDOWS_INSTALLER
    assert release["asset_url"] == "https://example.test/latest.exe"


def test_update_diagnostics_include_build_paths_and_last_result(monkeypatch):
    temp_dir = _workspace_temp()
    monkeypatch.setattr(update_checker.tempfile, "gettempdir", lambda: str(temp_dir))
    monkeypatch.setattr(update_checker, "get_my_build_tag", lambda: "v0.8.4-123")
    monkeypatch.setattr(update_checker, "get_my_build_commit", lambda: "abc1234")
    monkeypatch.setattr(update_checker, "get_install_path", lambda: temp_dir / "App")
    monkeypatch.setattr(update_checker, "update_branch_label", lambda: "main")

    update_checker.record_update_result(
        "Up to date",
        latest_build="v0.8.4-123",
        detail="Latest release matches this build.",
    )
    diagnostics = update_checker.get_update_diagnostics()

    assert diagnostics["current_version"] == "0.8.4"
    assert diagnostics["current_build"] == "v0.8.4-123 (abc1234)"
    assert diagnostics["current_commit"] == "abc1234"
    assert diagnostics["install_path"] == str(temp_dir / "App")
    assert diagnostics["update_channel"] == update_checker.UPDATE_CHANNEL
    assert diagnostics["update_branch"] == "main"
    assert diagnostics["last_update_result"] == "Up to date"
    assert diagnostics["last_update_detail"] == "Latest release matches this build."
    assert diagnostics["latest_build"] == "v0.8.4-123"
    assert diagnostics["updater_log_path"].endswith(update_checker.UPDATE_LOG_NAME)
    assert diagnostics["setup_log_path"].endswith(update_checker.SETUP_LOG_NAME)


def test_check_for_update_records_failed_fetch(monkeypatch):
    temp_dir = _workspace_temp()
    monkeypatch.setattr(update_checker.tempfile, "gettempdir", lambda: str(temp_dir))
    monkeypatch.setattr(update_checker, "get_my_build_tag", lambda: "v0.8.4-123")
    monkeypatch.setattr(update_checker, "_fetch_latest_release", lambda: None)

    assert update_checker.check_for_update() is None

    diagnostics = update_checker.get_update_diagnostics()
    assert diagnostics["last_update_result"] == "Failed"
    assert "Could not fetch latest GitHub release" in diagnostics["last_update_detail"]
