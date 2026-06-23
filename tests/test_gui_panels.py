# tests/test_gui_panels.py
import sys
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
    assert panel._run_btn.text() == "Run Check"
    assert panel._continue_btn.isHidden()
