import sys

sys.path.insert(0, "src")

import update_checker


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
