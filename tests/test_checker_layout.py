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
    QApplication.instance().setStyleSheet(gui_styles.get_stylesheet())
    panel.refresh_theme()
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
    panel._bs_entry.setText("https://brightspace.example/d2l/le/lessons/123")
    panel._moodle_entry.setText("https://moodle.example/course/view.php?id=456")
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
    assert fully_visible(panel._run_btn, panel)
    assert fully_visible(panel._details_btn, viewport)
    assert panel._scroll.verticalScrollBar().maximum() == 0
    report(panel)
    assert panel._group_rows["content"].isVisible()
    assert not panel._findings.isVisible()  # detail stays out of the overview
    assert fully_visible(panel._review_btn, panel)
    panel._group_rows["content"].click()
    panel._fix_boxes["content"].setChecked(True)
    settled()
    assert panel._apply_btn.isEnabled()
    assert fully_visible(panel._apply_btn, panel)
    assert panel._scroll.verticalScrollBar().maximum() == 0


def test_small_window_keeps_fixes_and_activity_reachable(checker):
    panel = checker
    panel.resize(560, 560)
    settled()
    assert panel._compact_courses
    assert panel._scroll.horizontalScrollBar().maximum() == 0
    report(panel)
    panel._review_btn.click()
    settled()
    viewport = panel._scroll.viewport()
    assert fully_visible(panel._apply_btn, panel)
    for group, keys in (("links", ("repair_files",)), ("content", ("content", "files", "activities")), ("books", ("books",)), ("other", ("order",))):
        panel._open_group(group)
        for key in keys:
            box = panel._fix_boxes[key]
            panel._scroll.ensureWidgetVisible(box)
            settled()
            assert fully_visible(box, viewport)
        assert fully_visible(panel._apply_btn, panel)
    panel._open_group("content")
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


def test_long_result_text_is_not_compressed_or_clipped(checker):
    checker.resize(560, 560)
    report(checker)
    checker._show_run_result(dict(mode="apply", status="stopped", summary={"files_uploaded": [("Outline", "Week 1")]}, errors=[]))
    settled()
    checker._show_run_result(dict(mode="apply", status="finished", summary={"files_uploaded": [], "files_failed": [("Outline", "Week 1")], "broken_links_repaired": 1}, errors=["A file upload needs attention."]))
    settled()
    body = checker._result_body
    assert body.height() >= body.heightForWidth(body.width())
    assert fully_visible(checker._run_btn, checker)
    assert checker._scroll.horizontalScrollBar().maximum() == 0
