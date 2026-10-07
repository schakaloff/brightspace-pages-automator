"""Navigation and reachability checks for the grouped tool hub."""
import sys
sys.path.insert(0, "src")

import pytest
from panels.workflow_hub import BuildStyleHub


@pytest.mark.parametrize("tool,panel", [
    ("create_pages", "_page_creator"), ("collect", "_collector"),
    ("restyle", "_restyle"), ("cleanup", "_cleanup"),
])
def test_grouped_hub_opens_the_existing_tool(qtbot, monkeypatch, tool, panel):
    from gui import MainWindow
    for name in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                 "_start_update_check", "_show_welcome_if_needed"):
        monkeypatch.setattr(MainWindow, name, lambda self: None)
    monkeypatch.setattr(MainWindow, "closeEvent", lambda self, event: event.accept())
    monkeypatch.setattr(MainWindow, "load_config", lambda self: {"welcomed": True})
    monkeypatch.setattr(MainWindow, "save_config", lambda self, data: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_step(7)
    window._build_hub.buttons[tool].click()
    assert window._stack.currentWidget() is getattr(window, panel)


def test_narrow_hub_keeps_all_actions_reachable(qtbot):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase
    if not QFontDatabase.families():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QApplication.instance().setFont(QFont("Segoe UI", 10))
    hub = BuildStyleHub()
    qtbot.addWidget(hub)
    hub.resize(560, 560)
    hub.show()
    QApplication.processEvents()
    assert hub._scroll.horizontalScrollBar().maximum() == 0
    opened = []
    hub.tool_selected.connect(opened.append)
    for key, button in hub.buttons.items():
        hub._scroll.ensureWidgetVisible(button)
        QApplication.processEvents()
        assert hub._scroll.viewport().rect().contains(button.mapTo(hub._scroll.viewport(), button.rect().center()))
        button.click()
        assert opened[-1] == key
    hub.resize(960, 800)
    QApplication.processEvents()
    assert hub._scroll.horizontalScrollBar().maximum() == 0
