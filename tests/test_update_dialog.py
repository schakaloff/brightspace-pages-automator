"""Reinstalling the build you already run must not be dressed up as an update."""
import sys

sys.path.insert(0, "src")

BASE = {
    "tag": "v0.8.5-99",
    "body": "notes",
    "html_url": "https://example.test/release",
    "asset_url": "https://example.test/latest.exe",
    "asset_name": "BrightspacePagesAutomator-Setup-Latest.exe",
    "checksum_url": "https://example.test/SHA256SUMS.txt",
    "checksum_name": "SHA256SUMS.txt",
}


def _dialog(qtbot, **overrides):
    from gui_dialogs import UpdateDialog
    dlg = UpdateDialog({**BASE, **overrides})
    qtbot.addWidget(dlg)
    return dlg


def _texts(dlg):
    from PySide6.QtWidgets import QLabel
    return [w.text() for w in dlg.findChildren(QLabel)]


def test_newer_build_offers_an_update(qtbot):
    dlg = _dialog(qtbot, tag="v0.8.6-1", force_install=False, same_build=False)

    assert dlg._button_text() == "Restart && Update"
    assert any("New version available: v0.8.6-1" in t for t in _texts(dlg))


def test_same_build_offers_a_reinstall(qtbot):
    dlg = _dialog(qtbot, force_install=True, same_build=True)

    assert dlg._button_text() == "Restart && Reinstall"
    texts = _texts(dlg)
    assert any("You already have this build installed" in t for t in texts)
    assert any("Reinstall latest version?" in t for t in texts)


def test_same_build_never_claims_a_new_version_exists(qtbot):
    dlg = _dialog(qtbot, force_install=True, same_build=True)

    joined = " ".join(_texts(dlg))
    assert "New version available" not in joined
    assert "Update available" not in joined


def test_forced_install_of_a_different_build_still_says_install(qtbot):
    dlg = _dialog(qtbot, tag="v0.8.4-90", force_install=True, same_build=False)

    assert dlg._button_text() == "Restart && Install"
    assert any("Install latest version: v0.8.4-90" in t for t in _texts(dlg))


def test_button_text_is_restored_after_a_failure(qtbot):
    dlg = _dialog(qtbot, force_install=True, same_build=True)
    dlg._on_install_failed("checksum mismatch")

    assert dlg._update_btn.text() == "Restart && Reinstall"
    assert dlg._update_btn.isEnabled()
    assert "checksum mismatch" in dlg._status_lbl.text()
