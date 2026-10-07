import sys
sys.path.insert(0, "src")

from gui_dialogs import PagesDialog
from panels.restyle_panel import RestylePanel

PAGES = [{"label": f"Lesson {i + 1}", "url": f"https://example.test/topics/{i + 1}"} for i in range(20)]


def test_picker_accepts_individual_non_consecutive_pages(qtbot):
    dialog = PagesDialog(PAGES)
    qtbot.addWidget(dialog)
    assert not dialog._run_btn.isEnabled()
    for i in (0, 3, 19):
        dialog._checks[i].setChecked(True)
    dialog._on_run()
    assert dialog.result_value() == [0, 3, 19]


def test_cancel_returns_empty_even_when_items_are_checked(qtbot):
    dialog = PagesDialog(PAGES)
    qtbot.addWidget(dialog)
    dialog._checks[0].setChecked(True)
    dialog.reject()
    assert dialog.result_value() == []


def test_search_retains_selection_and_selects_only_visible_items(qtbot):
    dialog = PagesDialog(PAGES)
    qtbot.addWidget(dialog)
    dialog._checks[19].setChecked(True)
    dialog._search.setText("Lesson 3")
    dialog._select(True)
    assert "hidden by search" in dialog._count_label.text()
    dialog._on_run()
    assert dialog.result_value() == [2, 19]
    dialog._select(False)
    assert not any(check.isChecked() for check in dialog._checks)


class WindowStub:
    chromium_ready = True
    def load_config(self): return {}
    def save_config(self, data): pass


def test_panel_reports_independent_page_results_and_restores_inputs(qtbot):
    panel = RestylePanel(WindowStub())
    qtbot.addWidget(panel)
    panel._busy = True
    panel._set_inputs_enabled(False)
    panel._results = {p["url"]: "pending" for p in PAGES[:3]}
    for page, state in zip(PAGES, ["changed", "failed", "skipped"]):
        panel._log_queue.put(("__RESULT__", (page, state)))
    panel._log_queue.put(("__DONE__", ""))
    panel._poll_log()
    assert panel._progress.value() == 3
    assert "1 saved · 1 failed · 1 skipped" in panel._status.text()
    assert not panel._busy
    assert panel._url_entry.isEnabled()
    assert not panel._move_unit_content_chk.isChecked()


def test_picker_cancel_is_passed_to_worker_as_empty_selection(qtbot, monkeypatch):
    panel = RestylePanel(WindowStub())
    qtbot.addWidget(panel)
    monkeypatch.setattr(PagesDialog, "exec", lambda self: 0)
    panel._log_queue.put(("__PAGES__", PAGES))
    panel._poll_log()
    assert panel._response_queue.get_nowait() == []
