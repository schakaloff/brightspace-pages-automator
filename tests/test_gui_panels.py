# tests/test_gui_panels.py
import sys
import threading
sys.path.insert(0, "src")


def test_settings_api_key_signal(qtbot):
    from PySide6.QtWidgets import QMainWindow
    from gui_panels import SettingsPanel
    mw = QMainWindow()
    panel = SettingsPanel(mw)
    qtbot.addWidget(panel)
    received = []
    panel.api_key_changed.connect(received.append)
    panel.set_api_key("test-key-123")
    assert panel._key_field.text() == "test-key-123"


def test_set_api_key_does_not_emit_signal(qtbot):
    from PySide6.QtWidgets import QMainWindow
    from gui_panels import SettingsPanel
    mw = QMainWindow()
    panel = SettingsPanel(mw)
    qtbot.addWidget(panel)
    received = []
    panel.api_key_changed.connect(received.append)
    panel.set_api_key("silent-key")
    # set_api_key blocks signals — nothing should be emitted
    assert received == []
    assert panel._key_field.text() == "silent-key"


def test_key_field_text_changed_emits_signal(qtbot):
    from PySide6.QtWidgets import QMainWindow
    from gui_panels import SettingsPanel
    mw = QMainWindow()
    panel = SettingsPanel(mw)
    qtbot.addWidget(panel)
    received = []
    panel.api_key_changed.connect(received.append)
    panel._key_field.setText("new-value")
    assert "new-value" in received


def test_settings_shows_update_diagnostics(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMainWindow
    import update_checker
    from gui_panels import SettingsPanel

    monkeypatch.setattr(update_checker, "get_update_diagnostics", lambda: {
        "current_version": "0.8.4",
        "current_build": "v0.8.4-123",
        "install_path": r"C:\Apps\BrightspacePagesAutomator",
        "update_channel": "stable",
        "update_branch": "main",
        "latest_build": "v0.8.4-123",
        "last_update_result": "Up to date",
        "last_update_detail": "",
        "last_update_at": "2026-07-30 12:00:00",
        "updater_log_path": r"C:\Temp\BrightspacePagesAutomator-update.log",
        "setup_log_path": r"C:\Temp\BrightspacePagesAutomator-setup.log",
    })
    mw = QMainWindow()
    panel = SettingsPanel(mw)
    qtbot.addWidget(panel)

    text = panel._update_diag_lbl.text()
    assert "Commit/build: v0.8.4-123" in text
    assert "Install path: C:\\Apps\\BrightspacePagesAutomator" in text
    assert "Update channel: stable" in text
    assert "Update branch: main" in text
    assert "Last update result: Up to date" in text
    assert "Updater log: C:\\Temp\\BrightspacePagesAutomator-update.log" in text


def test_divider_returns_frame(qtbot):
    from PySide6.QtWidgets import QFrame
    from gui_panels import _divider
    frame = _divider()
    qtbot.addWidget(frame)
    assert isinstance(frame, QFrame)
    assert frame.frameShape() == QFrame.Shape.HLine


def test_form_label_returns_label(qtbot):
    from PySide6.QtWidgets import QLabel
    from gui_panels import _form_label
    lbl = _form_label("MY LABEL")
    qtbot.addWidget(lbl)
    assert isinstance(lbl, QLabel)
    assert lbl.text() == "MY LABEL"
    assert lbl.property("role") == "form-label"


def test_checker_panel_builds(qtbot):
    from PySide6.QtWidgets import QMainWindow
    from unittest.mock import MagicMock
    from gui_panels import CheckerPanel
    mw = MagicMock()
    mw.chromium_ready = False
    mw.load_config.return_value = {}
    mw.save_config.return_value = None
    panel = CheckerPanel(mw)
    qtbot.addWidget(panel)
    assert panel._run_btn.text() == "Scan course"
    assert panel._apply_btn.text() == "Preview selected changes"
    assert panel._apply_btn.isEnabled() is False
    assert panel._stop_btn.isHidden()
    # assert panel._continue_btn.isHidden()


def test_h5p_panel_has_pre_run_gradebook_checkbox(qtbot):
    from unittest.mock import MagicMock
    from panels.h5p_panel import H5PPanel
    mw = MagicMock()
    mw.chromium_ready = False
    mw.load_config.return_value = {}
    panel = H5PPanel(mw)
    qtbot.addWidget(panel)
    assert panel._gradebook_cb.text() == "Add H5P activities to gradebook"
    assert panel._gradebook_cb.isChecked() is False
    panel._gradebook_cb.setChecked(True)
    assert panel._gradebook_cb.isChecked() is True


def test_h5p_recovery_dialog_matches_dropped_package(qtbot, tmp_path):
    from gui_dialogs import H5PRecoveryDialog

    event = threading.Event()
    result = []
    failures = [{
        "activity_key": "https://moodle/mod/h5pactivity/view.php?id=1",
        "name": "Introduction 2024",
        "safe_name": "Introduction 2024",
        "reason": "Moodle activity has no H5P package file attached",
    }]
    package = tmp_path / "Introduction_2024.h5p"
    package.write_bytes(b"placeholder")
    dialog = H5PRecoveryDialog(failures, result, event)
    qtbot.addWidget(dialog)

    dialog._add_dropped_files([str(package)])
    assert dialog._rows[0]["path"] == str(package)
    assert "Matched 1" in dialog._drop_status.text()

    dialog._continue()
    assert event.is_set()
    assert result == [{
        "activity_key": "https://moodle/mod/h5pactivity/view.php?id=1",
        "path": str(package),
    }]


def test_collector_panel_builds(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    assert panel._run_btn.text() == "Create Combined Unit Page"
    # assert panel._continue_btn.isHidden()


def test_collector_panel_has_moodle_url_field(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    assert panel._moodle_entry.text() == ""
    panel._moodle_entry.setText("https://mymoodle.okanagan.bc.ca/course/view.php?id=123")
    assert panel._moodle_entry.text() == "https://mymoodle.okanagan.bc.ca/course/view.php?id=123"


def test_restyle_panel_builds(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import RestylePanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = RestylePanel(mw); qtbot.addWidget(panel)
    assert panel._run_btn.text() == "Start restyle"
    assert panel._move_unit_content_chk.isChecked() is False


def test_restyle_unit_content_option_can_be_disabled_and_saved(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import RestylePanel
    mw = MagicMock(); mw.chromium_ready = False
    mw.load_config.return_value = {"restyle_move_unit_content": False}
    panel = RestylePanel(mw); qtbot.addWidget(panel)
    assert panel._move_unit_content_chk.isChecked() is False
    panel._move_unit_content_chk.setChecked(True)
    panel.save_state()
    assert mw.save_config.call_args.args[0]["restyle_move_unit_content"] is True


def test_collector_panel_has_multi_unit_checkboxes(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    assert panel._multi_unit_chk.isChecked() is False
    assert panel._auto_continue_chk.isChecked() is False
    assert panel._auto_continue_chk.isEnabled() is False


def test_collector_cleanup_mode_requires_an_existing_page(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel

    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    panel.show()
    panel._cleanup_only_chk.setChecked(True)

    assert panel._run_btn.text() == "Clear Section Duplicate"
    assert not panel._auto_create_chk.isChecked()
    assert not panel._auto_create_chk.isEnabled()
    assert panel._target_entry.isVisible()
    assert not panel._multi_unit_chk.isEnabled()


def test_multi_unit_toggle_enables_auto_continue_checkbox(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    panel._multi_unit_chk.setChecked(True)
    assert panel._auto_continue_chk.isEnabled() is True
    panel._multi_unit_chk.setChecked(False)
    assert panel._auto_continue_chk.isEnabled() is False
    assert panel._auto_continue_chk.isChecked() is False


def test_col_confirm_message_shows_dialog_and_sets_event(qtbot, monkeypatch):
    import threading
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    from PySide6.QtWidgets import QMessageBox

    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)

    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)

    result_ref = [False]
    event = threading.Event()
    panel._log_queue.put(("__COL_CONFIRM__", ("Continue to next unit?", result_ref, event)))
    panel._poll_log()

    assert event.is_set()
    assert result_ref[0] is True


def test_workflow_hub_cards_open_their_tool(qtbot):
    from panels.workflow_hub import WorkflowHub

    hub = WorkflowHub("Media", "Move media.", [
        ("h5p", "H5P activities", "Download H5P."),
        ("kaltura", "Kaltura videos", "Find videos."),
    ])
    qtbot.addWidget(hub)
    opened = []
    hub.tool_selected.connect(opened.append)

    hub.buttons["kaltura"].click()
    hub.buttons["h5p"].click()

    assert opened == ["kaltura", "h5p"]


def test_checker_fix_tab_needs_a_scan_and_a_choice(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CheckerPanel

    mw = MagicMock()
    mw.chromium_ready = False
    mw.load_config.return_value = {}
    panel = CheckerPanel(mw)
    qtbot.addWidget(panel)
    panel._bs_entry.setText("https://learn.test/d2l/home/42")
    panel._moodle_entry.setText("https://moodle.test/course/view.php?id=7")

    assert [panel._tabs.tabText(i) for i in range(panel._tabs.count())] == ["1 · Scan", "2 · Fix"]
    panel._fix_boxes["order"].setChecked(True)
    assert not panel._apply_btn.isEnabled()  # no scan yet

    urls = (panel._bs_entry.text(), panel._moodle_entry.text())
    panel._show_scan_report({
        "missing": 0, "books": 0, "files": 0, "h5p": 0, "broken_links": 0,
        "activities": 0, "moodle_links": 0, "findings": [], "broken_pages": [],
    }, urls)
    assert panel._apply_btn.isEnabled()
    assert not panel._fix_boxes["books"].isEnabled()  # nothing to split

    panel._moodle_entry.setText("https://moodle.test/course/view.php?id=8")
    assert not panel._apply_btn.isEnabled()  # a different course needs a new scan
