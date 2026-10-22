"""Exercise full-row navigation through the real Qt window event routing."""
import sys
sys.path.insert(0, 'src')

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QFrame
import gui_styles
from panels.workflow_hub import WorkflowHub


@pytest.fixture
def hub(qtbot):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    QApplication.instance().setFont(QFont('Segoe UI', 10))
    widget = WorkflowHub('Media', 'Move media into Brightspace.', [
        ('h5p', 'H5P activities', 'Download H5P activities from Moodle and place them in the matching Brightspace units.'),
        ('scorm', 'SCORM packages', 'Download original Moodle SCORM ZIPs, review their destination units, and transfer them to Brightspace.'),
        ('kaltura', 'Kaltura videos', "Find Moodle's Kaltura videos and create matching Brightspace pages."),
    ])
    qtbot.addWidget(widget)
    widget.setStyleSheet(gui_styles.get_stylesheet())
    widget.resize(960, 600)
    widget.show()
    QApplication.processEvents()
    return widget


@pytest.mark.parametrize('region', ['text', 'icon', 'whitespace'])
def test_clicks_anywhere_on_each_row_open_exactly_its_tool(hub, region):
    opened = []
    hub.tool_selected.connect(opened.append)
    for index, (key, button) in enumerate(hub.buttons.items()):
        hub._scroll.ensureWidgetVisible(button)
        QApplication.processEvents()
        if region == 'text':
            target = next(label for label in button.findChildren(QLabel)
                          if label.property('role') == 'media-tool-heading')
            position = hub.mapFromGlobal(target.mapToGlobal(target.rect().center()))
        elif region == 'icon':
            target = button.findChild(QFrame)
            position = hub.mapFromGlobal(target.mapToGlobal(target.rect().center()))
        else:
            position = hub.mapFromGlobal(button.mapToGlobal(QPoint(button.width() // 2, 6)))
        # Send through QWindow, so Qt hit-tests labels and icon containers too.
        QTest.mouseClick(hub.windowHandle(), Qt.MouseButton.LeftButton, pos=position)
        assert opened == list(hub.buttons)[:index + 1]
    assert opened == ['h5p', 'scorm', 'kaltura']


def test_rows_support_tab_enter_and_space_without_nested_focus_targets(hub, qtbot):
    opened = []
    hub.tool_selected.connect(opened.append)
    button = hub.buttons['h5p']
    button.setFocus()
    qtbot.keyClick(button, Qt.Key.Key_Return)
    qtbot.keyClick(button, Qt.Key.Key_Tab)
    assert hub.buttons['scorm'].hasFocus()
    qtbot.keyClick(hub.buttons['scorm'], Qt.Key.Key_Space)
    assert opened == ['h5p', 'scorm']


def test_narrow_rows_wrap_descriptions_and_keep_navigation_reachable(hub):
    hub.resize(430, 440)
    QApplication.processEvents()
    assert hub._scroll.horizontalScrollBar().maximum() == 0
    for button in hub.buttons.values():
        hub._scroll.ensureWidgetVisible(button)
        QApplication.processEvents()
        assert hub._scroll.viewport().rect().contains(
            button.mapTo(hub._scroll.viewport(), button.rect().center()))
        assert button.height() >= button.heightForWidth(button.width())
