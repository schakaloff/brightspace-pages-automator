"""Regression coverage for course context and saved-account management."""
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLineEdit, QMessageBox, QPushButton

sys.path.insert(0, "src")
from course_context import brightspace_course_key, moodle_course_id
from panels.settings_panel import SettingsPanel
from panels.collector_panel import CollectorPanel


class Window:
    def __init__(self, config=None):
        self.config = dict(config or {})
        self.saved = []
        self.update_checks = []
        self.chromium_ready = False

    def load_config(self):
        return dict(self.config)

    def save_config(self, values):
        self.config.update(values)
        self.saved.append(dict(values))

    def set_theme(self, name):
        pass

    def _run_update_check(self, force_install=False):
        self.update_checks.append(force_install)


@pytest.fixture
def settings(qtbot, monkeypatch):
    import update_checker
    monkeypatch.setattr(update_checker, "get_update_diagnostics", lambda: {})
    window = Window()
    panel = SettingsPanel(window)
    qtbot.addWidget(panel)
    return panel, window


def collector(qtbot, config=None):
    window = Window(config)
    panel = CollectorPanel(window)
    qtbot.addWidget(panel)
    return panel, window


def unit(course, number=1, host="learn.example.edu"):
    return f"https://{host}/d2l/le/lessons/{course}/units/{number}"


def enable_source(panel):
    panel._use_moodle_names.setChecked(True)
    panel._moodle_entry.setText("https://mymoodle.okanagan.bc.ca/course/view.php?id=99")


def test_startup_ignores_both_legacy_moodle_sources_and_does_not_persist_new_one(qtbot):
    panel, window = collector(qtbot, {"col_moodle_url": "old collector", "chk_moodle_url": "old checker", "col_unit_url": unit(1)})
    assert not panel._use_moodle_names.isChecked()
    assert panel._moodle_entry.text() == ""
    enable_source(panel)
    panel.save_state()
    assert window.config["col_moodle_url"] == ""
    assert panel._moodle_entry.text().endswith("id=99")
    fresh = CollectorPanel(window)
    qtbot.addWidget(fresh)
    assert fresh._moodle_entry.text() == ""
    assert not fresh._use_moodle_names.isChecked()


def test_source_survives_unit_changes_but_resets_across_courses_and_hosts(qtbot):
    panel, _ = collector(qtbot)
    panel._unit_entry.setText(unit(1))
    enable_source(panel)
    panel._unit_entry.setText(unit(1, 2))
    assert panel._use_moodle_names.isChecked()
    assert panel._moodle_entry.text()
    panel._unit_entry.setText(unit(2))
    assert not panel._use_moodle_names.isChecked()
    assert panel._moodle_entry.text() == ""
    enable_source(panel)
    panel._unit_entry.setText(unit(2, host="another.example.edu"))
    assert panel._moodle_entry.text() == ""


def test_checker_reuse_is_explicit_matches_course_and_clear_is_honoured(qtbot):
    panel, window = collector(qtbot, {"chk_moodle_url": "old"})
    window._checker = SimpleNamespace(_bs_entry=QLineEdit(unit(1)), _moodle_entry=QLineEdit("https://mymoodle.okanagan.bc.ca/course/view.php?id=99"))
    panel._unit_entry.setText(unit(1))
    panel.refresh_carryover()
    assert panel._moodle_entry.text() == ""
    panel._use_checker_source()
    assert panel._use_moodle_names.isChecked()
    assert panel._moodle_entry.text().endswith("id=99")
    panel._adv_btn.setChecked(False)
    assert not panel._moodle_summary_row.isHidden()
    assert "Source course 99" in panel._moodle_summary.text()
    panel._clear_moodle_source()
    panel.refresh_carryover()
    assert panel._moodle_entry.text() == ""
    assert panel._moodle_summary_row.isHidden()
    panel._unit_entry.setText(unit(2))
    panel._use_checker_source()
    assert panel._moodle_entry.text() == ""


@pytest.mark.parametrize("url", [
    "https://learn.example.edu/d2l/le/lessons/12/units/34",
    "https://learn.example.edu/d2l/le/content/12/Home",
    "https://learn.example.edu/d2l/home/12",
    "learn.example.edu/d2l/common/dialogs/quickLink/quickLink.d2l?ou=12&type=content",
])
def test_supported_course_url_forms_have_same_context(url):
    assert brightspace_course_key(url) == ("learn.example.edu", "12")


def test_login_selector_routes_only_selected_saved_account(settings, _never_touch_the_real_keyring):
    panel, window = settings
    window.config.update({"bs_username": "direct", "sso_email": "sso@example.edu"})
    _never_touch_the_real_keyring.update({("BrightspacePagesAutomator", "direct"): "direct-pass", ("BrightspacePagesAutomator_SSO", "sso@example.edu"): "sso-pass"})
    panel._load_credentials()
    assert panel.sso_email == "sso@example.edu"
    assert panel.bs_username == ""
    panel._direct_radio.setChecked(True)
    assert window.config["bs_login_method"] == "direct"
    assert panel.bs_password == "direct-pass"
    assert panel.sso_email == ""
    assert window.config["sso_email"] == "sso@example.edu"


def test_save_preserves_password_spaces_and_removal_uses_saved_identity(settings, monkeypatch, _never_touch_the_real_keyring):
    panel, window = settings
    panel._moodle_user_field.setText("teacher")
    panel._moodle_pass_field.setText(" leading and trailing ")
    panel._save_moodle_credentials()
    assert _never_touch_the_real_keyring[("BrightspacePagesAutomator_Moodle", "teacher")] == " leading and trailing "
    assert "Saved on this computer" in panel._moodle_status.text()
    panel._moodle_user_field.setText("unsaved edit")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    panel._remove_credentials("moodle")
    assert ("BrightspacePagesAutomator_Moodle", "teacher") not in _never_touch_the_real_keyring
    assert window.config["moodle_username"] == ""
    assert panel.moodle_password == ""
    assert panel._moodle_pass_field.text() == ""


def test_cancel_removal_and_keyring_failures_do_not_report_success(settings, monkeypatch):
    import keyring
    panel, window = settings
    window.config["moodle_username"] = "original"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    panel._remove_credentials("moodle")
    assert window.config["moodle_username"] == "original"
    panel._moodle_user_field.setText("new")
    panel._moodle_pass_field.setText("password")
    def unavailable(*args):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(keyring, "set_password", unavailable)
    panel._save_moodle_credentials()
    assert window.config["moodle_username"] == "original"
    assert "Could not save" in panel._moodle_status.text()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(keyring, "delete_password", unavailable)
    panel._remove_credentials("moodle")
    assert window.config["moodle_username"] == "original"
    assert "Could not remove" in panel._moodle_status.text()


def test_updates_and_reinstall_are_separate_actions(settings):
    panel, window = settings
    panel._check_for_updates()
    panel._check_latest_installer()
    assert window.update_checks == [False, True]
    assert panel._tabs.count() == 4
    assert panel._troubleshooting_btn.isChecked() is False


def test_failed_installation_remains_visible_when_routine_check_says_up_to_date(settings):
    panel, _ = settings
    panel.refresh_update_diagnostics({"current_version": "example", "last_update_result": "Up to date", "last_install_result": "Update failed", "setup_exit_code": 3})
    assert "Up to date" in panel._update_summary_lbl.text()
    assert "Update failed (Setup exit code 3)" in panel._update_summary_lbl.text()


def test_account_config_write_failure_is_not_reported_as_saved(settings):
    panel, window = settings
    window.save_config = lambda values: None
    panel._moodle_user_field.setText("teacher")
    panel._moodle_pass_field.setText("password")
    assert panel._save_credentials("moodle") is False
    assert "Could not save the account name" in panel._moodle_status.text()


def test_interactive_guide_opens_configured_url_and_invalid_url_has_no_dead_button(settings, qtbot, monkeypatch):
    import panels.settings_layout as layout
    opened = []
    monkeypatch.setattr(layout.webbrowser, "open", opened.append)
    window = Window({"interactive_guide_url": "https://example.edu/interactive-guide"})
    panel = SettingsPanel(window)
    qtbot.addWidget(panel)
    guide_button = next(btn for btn in panel.findChildren(QPushButton) if btn.text() == "Open interactive guide")
    guide_button.click()
    assert opened == ["https://example.edu/interactive-guide"]
    window.config["interactive_guide_url"] = "https://[unfinished"
    invalid = SettingsPanel(window)
    qtbot.addWidget(invalid)
    assert not any(btn.text() == "Open interactive guide" for btn in invalid.findChildren(QPushButton))


def test_malformed_urls_are_rejected_without_crashing():
    assert brightspace_course_key("https://[unfinished") is None
    assert moodle_course_id("https://[unfinished") is None
    assert moodle_course_id("https://user:secret@example.edu/course/view.php?id=1") is None


@pytest.mark.parametrize("enabled", [False, True])
def test_run_only_passes_moodle_source_when_explicitly_enabled(qtbot, monkeypatch, enabled):
    import panels.collector_panel as collector_module
    import unit_collector
    panel, window = collector(qtbot)
    window.chromium_ready = True
    for name in ("claude_api_key", "claude_model", "bs_username", "bs_password", "sso_email", "sso_password", "moodle_username", "moodle_password"):
        setattr(window, name, "")
    panel._unit_entry.setText(unit(1))
    panel._moodle_entry.setText("https://mymoodle.okanagan.bc.ca/course/view.php?id=99")
    panel._use_moodle_names.setChecked(enabled)
    received = []
    async def fake_run(**kwargs):
        received.append(kwargs)
        return True
    monkeypatch.setattr(unit_collector, "run", fake_run)
    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(collector_module.threading, "Thread", InlineThread)
    panel._start_run()
    assert len(received) == 1
    assert received[0]["moodle_url"] == ("https://mymoodle.okanagan.bc.ca/course/view.php?id=99" if enabled else "")
