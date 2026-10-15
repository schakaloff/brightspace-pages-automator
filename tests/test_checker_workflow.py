"""Review never writes until confirmed, and course/run state stays truthful."""
import asyncio
import threading
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QLabel

from panels.checker_workflow import course_context

URLS = ("https://learn.example/d2l/le/lessons/123", "https://moodle.example/course/view.php?id=456")
REPORT = dict(missing=2, files=1, activities=1, books=1, h5p=1, moodle_links=1,
              broken_links=2, file_link_check="complete", broken_pages=["Resource page"],
              findings=[dict(status="missing", name="Welcome", section="Week 1", type="PAGE")],
              book_findings=[dict(name="Readings", section="Week 2")])


@pytest.fixture
def panel(qtbot):
    from panels.checker_panel import CheckerPanel
    for font in ("segoeui.ttf", "segoeuib.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    mw = MagicMock()
    mw.chromium_ready = True
    mw.load_config.return_value = {}
    widget = CheckerPanel(mw)
    widget.setFont(QFont("Segoe UI", 10))
    qtbot.addWidget(widget)
    widget.resize(900, 720)
    widget.show()
    widget._bs_entry.setText(URLS[0])
    widget._moodle_entry.setText(URLS[1])
    return widget


def scan(panel, report=REPORT):
    panel._show_scan_report(report, panel._current_urls())


def test_review_preserves_choices_between_groups_and_requires_explicit_apply(panel, monkeypatch):
    worker = MagicMock()
    monkeypatch.setattr(panel, "_run_worker", worker)
    scan(panel)
    panel._group_rows["links"].click()
    panel._fix_boxes["repair_files"].click()
    panel._back_btn.click()
    panel._group_rows["content"].click()
    panel._fix_boxes["files"].click()
    panel._apply_btn.click()
    assert panel._view == "confirm"
    assert URLS[0] in panel._confirm_courses.text()
    assert URLS[1] in panel._confirm_courses.text()
    worker.assert_not_called()
    panel._back_btn.click()
    assert panel._fix_boxes["repair_files"].isChecked()
    assert panel._fix_boxes["files"].isChecked()
    panel._apply_btn.click()
    panel._confirm_btn.click()
    worker.assert_called_once_with(full_run=True, operations=dict(repair_files=True, content=False, files=True, activities=False, books=False, order=False))


@pytest.mark.parametrize("key,group,expected", [("books", "books", {"books_only": True}), ("order", "other", {"order_only": True})])
def test_single_special_operation_keeps_its_existing_backend_route(panel, monkeypatch, key, group, expected):
    worker = MagicMock()
    monkeypatch.setattr(panel, "_run_worker", worker)
    scan(panel)
    panel._open_group(group)
    panel._fix_boxes[key].click()
    panel._apply_btn.click()
    worker.assert_not_called()
    panel._confirm_btn.click()
    worker.assert_called_once_with(**expected)


def test_changed_course_invalidates_confirmation_and_clears_selections(panel, monkeypatch):
    worker = MagicMock()
    monkeypatch.setattr(panel, "_run_worker", worker)
    scan(panel)
    panel._open_group("links")
    panel._fix_boxes["repair_files"].click()
    panel._apply_btn.click()
    panel._bs_entry.setText("https://other.example/d2l/le/lessons/123")
    panel._apply_selected()
    worker.assert_not_called()
    assert panel._scan_report is None
    assert not any(box.isChecked() for box in panel._fix_boxes.values())
    assert not panel._confirm_btn.isEnabled()


def test_late_report_for_previous_course_is_discarded(panel):
    old = panel._current_urls()
    panel._moodle_entry.setText("https://moodle.example/course/view.php?id=789")
    panel._show_scan_report(REPORT, old)
    assert panel._scan_report is None
    assert panel._course_form.isVisible()


def test_failed_link_check_is_not_reported_as_zero_or_offered_as_repair(panel):
    scan(panel, dict(REPORT, file_link_check="failed", broken_links=0, broken_pages=[]))
    assert panel._group_counts["links"].text() == "Not checked"
    assert panel._group_rows["links"].isVisible()
    assert not panel._fix_boxes["repair_files"].isEnabled()
    assert panel._fix_boxes["files"].isEnabled()
    assert "could not be checked" in panel._summary.text()


def test_rescan_drops_choices_that_are_no_longer_available(panel):
    scan(panel)
    panel._fix_boxes["repair_files"].setChecked(True)
    panel._fix_boxes["content"].setChecked(True)
    scan(panel, dict(REPORT, broken_links=0, broken_pages=[]))
    assert not panel._fix_boxes["repair_files"].isChecked()
    assert panel._fix_boxes["content"].isChecked()


def test_switching_groups_does_not_leave_old_evidence_visible(panel):
    scan(panel)
    panel._open_group("links")
    old_labels = panel._findings.findChildren(QLabel)
    panel._open_group("content")
    assert all(label.isHidden() for label in old_labels)
    text = " ".join(label.text() for label in panel._findings.findChildren(QLabel))
    assert "Welcome" in text
    assert "Resource page" not in text


def test_section_scope_stays_visible_and_invalid_input_does_not_start_worker(panel, monkeypatch):
    worker = MagicMock()
    monkeypatch.setattr(panel, "_run_worker", worker)
    panel._moodle_entry.setText("https://moodle.example/course/section.php?id=99")
    assert panel._validate_courses()
    scan(panel)
    assert "Section 99" in panel._course_context_label.text()
    panel._bs_entry.setText("https://learn.example/not-a-course")
    panel._start_run()
    worker.assert_not_called()
    assert panel._course_errors["bs"].isVisible()


@pytest.mark.parametrize("url", ["https://user:pass@learn.example/d2l/home/123", "https://learn.example:bad/d2l/home/123", "https://bad host/d2l/home/123", "file:///d2l/home/123"])
def test_malformed_or_credential_urls_are_not_valid_course_inputs(url):
    assert course_context(url, "Brightspace") is None


def test_keyboard_can_open_a_finding_group(panel, qtbot):
    scan(panel)
    panel._group_rows["links"].setFocus()
    qtbot.keyClick(panel._group_rows["links"], Qt.Key.Key_Space)
    assert panel._view == "group"
    assert panel._active_group == "links"


def test_stopped_result_keeps_recorded_changes_and_requires_new_scan(panel):
    scan(panel)
    panel._show_run_result(dict(mode="apply", status="stopped", summary={"files_uploaded": [("Outline.pdf", "Week 1")]}, errors=[]))
    assert panel._result_title.text() == "Run stopped"
    assert "Files uploaded: 1" in panel._result_body.text()
    assert "does not undo" in panel._result_body.text()
    assert panel._scan_report is None
    assert not panel._apply_btn.isEnabled()


def test_worker_callback_cannot_unlock_ui_before_worker_finishes(panel, qtbot, monkeypatch):
    import content_checker
    started = threading.Event()
    release = threading.Event()
    class FakeChecker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.run_finished = False
            self._summary = {}
        async def run(self):
            self.kwargs["on_complete"]()
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            self.kwargs["on_report"](REPORT)
            self.run_finished = True
    monkeypatch.setattr(content_checker, "ContentChecker", FakeChecker)
    panel._start_run()
    qtbot.waitUntil(started.is_set)
    assert panel._task_active
    assert not panel._bs_entry.isEnabled()
    assert not panel._change_btn.isEnabled()
    assert panel._stop_btn.isVisible()
    release.set()
    qtbot.waitUntil(lambda: not panel._task_active)
    assert panel._bs_entry.isEnabled()
    assert panel._change_btn.isEnabled()
    assert panel._view == "overview"
    assert panel._scan_current()
