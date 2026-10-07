"""Check that the redesign keeps scan and fix actions reachable."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "src")


@pytest.fixture(params=["light", "dark"])
def checker(qtbot, monkeypatch, request):
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    import gui_styles
    from panels.checker_panel import CheckerPanel

    for name in ("segoeui.ttf", "segoeuib.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    monkeypatch.setattr(gui_styles, "current", dict(
        gui_styles.LIGHT if request.param == "light" else gui_styles.DARK
    ))
    mw = MagicMock()
    mw.load_config.return_value = {}
    panel = CheckerPanel(mw)
    panel.setFont(QFont("Segoe UI", 10))
    panel.setStyleSheet(gui_styles.get_stylesheet())
    qtbot.addWidget(panel)
    panel.show()
    return panel


def settled():
    from PySide6.QtWidgets import QApplication
    for _ in range(4):
        QApplication.processEvents()


def fully_visible(widget, viewport):
    from PySide6.QtCore import QPoint, QRect
    bounds = QRect(widget.mapTo(viewport, QPoint(0, 0)), widget.size())
    return viewport.rect().contains(bounds)


def report(panel):
    panel._bs_entry.setText("https://brightspace.example/course/123")
    panel._moodle_entry.setText("https://moodle.example/course/456")
    panel._show_scan_report(dict(
        missing=1, broken_links=0, books=0, h5p=0, files=0, activities=0,
        findings=[dict(status="missing", name="Introduction", type="Page", section="Week 1")],
    ), (panel._bs_entry.text(), panel._moodle_entry.text()))
    settled()


def test_standard_window_keeps_main_actions_visible(checker):
    panel = checker
    panel.resize(960, 800)
    settled()
    viewport = panel._scroll.viewport()
    assert fully_visible(panel._run_btn, viewport)
    assert fully_visible(panel._review_btn, viewport)
    assert fully_visible(panel._details_btn, viewport)
    assert panel._scroll.verticalScrollBar().maximum() == 0
    report(panel)
    assert panel._findings.isVisible()
    assert not panel._empty_state.isVisible()
    assert fully_visible(panel._review_btn, viewport)
    panel._review_btn.click()
    panel._fix_boxes["content"].setChecked(True)
    settled()
    assert panel._apply_btn.isEnabled()
    assert fully_visible(panel._apply_btn, viewport)
    assert panel._scroll.verticalScrollBar().maximum() == 0


def test_small_window_keeps_fixes_and_activity_reachable(checker):
    from PySide6.QtWidgets import QScrollArea
    panel = checker
    panel.resize(560, 560)
    settled()
    assert panel._compact_courses
    assert panel._scroll.horizontalScrollBar().maximum() == 0
    report(panel)
    panel._review_btn.click()
    settled()
    viewport = panel._scroll.viewport()
    assert fully_visible(panel._apply_btn, viewport)
    fix_scroll = panel._tabs.widget(1).findChild(QScrollArea)
    for box in panel._fix_boxes.values():
        fix_scroll.ensureWidgetVisible(box)
        settled()
        assert fully_visible(box, fix_scroll.viewport())
    panel._fix_boxes["content"].setChecked(True)
    panel.resize(960, 800)
    settled()
    assert not panel._compact_courses
    assert panel._fix_boxes["content"].isChecked()
    assert panel._apply_btn.isEnabled()
    panel._details_btn.setChecked(True)
    settled()
    assert fully_visible(panel._log, viewport)
    assert panel._scroll.horizontalScrollBar().maximum() == 0
