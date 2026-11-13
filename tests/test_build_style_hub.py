"""Navigation and reachability checks for the grouped tool hub."""
import sys
sys.path.insert(0, "src")

import pytest
from panels.workflow_hub import BuildStyleHub


@pytest.mark.parametrize("tool,panel", [
    ("create_pages", "_page_creator"), ("collect", "_collector"),
    ("restyle", "_restyle"), ("cleanup", "_cleanup"),
    ("duplicate_content", "_duplicate"),
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


def test_plan_transfer_launches_browser_from_hub(qtbot, monkeypatch):
    from gui import MainWindow
    for name in ("_set_window_icon", "_load_api_key", "_start_chromium_check", "_start_update_check", "_show_welcome_if_needed"):
        monkeypatch.setattr(MainWindow, name, lambda self: None)
    monkeypatch.setattr(MainWindow, "closeEvent", lambda self, event: event.accept())
    monkeypatch.setattr(MainWindow, "load_config", lambda self: {"welcomed": True})
    monkeypatch.setattr(MainWindow, "save_config", lambda self, data: None)
    window = MainWindow(); qtbot.addWidget(window)
    opened = []
    monkeypatch.setattr(window._transfer, "open_studio", lambda: opened.append(True))
    window._on_step(7)
    window._build_hub.buttons["structured_transfer"].click()
    assert opened == [True] and window._stack.currentWidget() is window._build_hub


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


@pytest.mark.parametrize('region', ['text', 'icon', 'whitespace'])
def test_entire_rows_navigate_once_including_labels_and_icons(qtbot, region):
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QFrame
    import gui_styles
    hub = BuildStyleHub()
    qtbot.addWidget(hub)
    hub.setStyleSheet(gui_styles.get_stylesheet())
    hub.resize(960, 800)
    hub.show()
    opened = []
    hub.tool_selected.connect(opened.append)
    for key, row in hub.buttons.items():
        hub._scroll.ensureWidgetVisible(row)
        QApplication.processEvents()
        if region == 'text':
            target = next(label for label in row.findChildren(QLabel)
                          if label.property('role') == 'hub-tool-heading')
            point = target.mapTo(hub, target.rect().center())
        elif region == 'icon':
            target = row.findChild(QFrame)
            point = target.mapTo(hub, target.rect().center())
        else:
            point = row.mapTo(hub, QPoint(row.width() // 2, 6))
        QTest.mouseClick(hub.windowHandle(), Qt.MouseButton.LeftButton, pos=point)
        assert opened[-1] == key
    assert opened == ['create_pages', 'collect', 'duplicate_content', 'structured_transfer', 'quiz_repair', 'restyle', 'cleanup']


def test_keyboard_navigation_crosses_groups_without_extra_buttons(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    hub = BuildStyleHub()
    qtbot.addWidget(hub)
    hub.resize(900, 800)
    hub.show()
    QApplication.processEvents()
    opened = []
    hub.tool_selected.connect(opened.append)
    transfer = hub.buttons['structured_transfer']
    transfer.setFocus()
    qtbot.keyClick(transfer, Qt.Key.Key_Return)
    qtbot.keyClick(transfer, Qt.Key.Key_Tab)
    quiz = hub.buttons['quiz_repair']
    assert quiz.hasFocus()
    qtbot.keyClick(quiz, Qt.Key.Key_Space)
    qtbot.keyClick(quiz, Qt.Key.Key_Tab)
    restyle = hub.buttons['restyle']
    assert restyle.hasFocus()
    qtbot.keyClick(restyle, Qt.Key.Key_Space)
    assert opened == ['structured_transfer', 'quiz_repair', 'restyle']
