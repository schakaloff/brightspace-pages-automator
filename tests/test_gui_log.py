# tests/test_gui_log.py
import sys
sys.path.insert(0, "src")
import pytest


def test_append_adds_text(qtbot):
    from gui_log import LogWidget
    w = LogWidget()
    qtbot.addWidget(w)
    w.append_log("hello world", "info")
    assert "hello world" in w.toPlainText()


def test_clear_removes_text(qtbot):
    from gui_log import LogWidget
    w = LogWidget()
    qtbot.addWidget(w)
    w.append_log("some text", "info")
    w.clear_log()
    assert w.toPlainText().strip() == ""


def test_unknown_tag_does_not_raise(qtbot):
    from gui_log import LogWidget
    w = LogWidget()
    qtbot.addWidget(w)
    w.append_log("msg", "unknown_tag")  # should not raise


def test_zoom_badge_hidden_initially(qtbot):
    from gui_log import LogWidget
    w = LogWidget()
    qtbot.addWidget(w)
    assert not w._zoom_badge.isVisible()
