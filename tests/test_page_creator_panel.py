import sys

sys.path.insert(0, "src")

from bulk_page_creator import BatchResult, CreatedPage, PageDraft, Section
from panels.page_creator_panel import PageCreatorPanel


class MainWindowStub:
    chromium_ready = True

    def __init__(self, cfg=None):
        self.cfg = cfg or {}

    def load_config(self):
        return self.cfg

    def save_config(self, data):
        self.cfg.update(data)


def make_panel(qtbot, cfg=None):
    panel = PageCreatorPanel(MainWindowStub(cfg))
    qtbot.addWidget(panel)
    panel._url.setText("https://learn.okanagancollege.ca/d2l/le/lessons/123/units/20")
    panel._queue.put(("loaded", ("123", [Section("20", "Unit 1 / Lessons")])) )
    panel._poll()
    return panel


def test_editing_and_reordering_keep_content_attached_to_titles(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles(["One", "Two", "Three"])
    panel._content.setPlainText("Content of One")
    panel._table.setCurrentCell(1, 0)
    panel._format.setCurrentIndex(1)
    panel._content.setPlainText("<h2>Content of Two</h2>")
    panel._move_row(-1)
    assert panel._rows[0]["title"] == "Two"
    assert panel._rows[0]["format"] == "html"
    assert panel._rows[0]["content"] == "<h2>Content of Two</h2>"
    panel._table.setCurrentCell(1, 0)
    assert panel._content.toPlainText() == "Content of One"
    panel._remove_row()
    assert [row["title"] for row in panel._rows] == ["Two", "Three"]


def test_editor_title_and_table_title_stay_in_sync_after_selection(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles(["One", "Two"])
    panel._title.setText("Renamed One")
    assert panel._rows[0]["title"] == panel._table.item(0, 0).text() == "Renamed One"
    panel._table.setCurrentCell(1, 0)
    panel._table.item(1, 0).setText("Renamed Two")
    assert panel._title.text() == "Renamed Two"
    panel._table.setCurrentCell(0, 0)
    assert panel._title.text() == "Renamed One"
    panel._rows[0]["state"] = "created"
    panel._refresh_table()
    assert panel._title.isReadOnly()


def test_clear_all_empties_twenty_completed_pages_and_saved_draft(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles([f"Page {number}" for number in range(20)])
    panel._content.setPlainText("Old content")
    for row in panel._rows:
        row["state"] = "created"
    panel._refresh_table()
    panel.save_state()
    assert panel._clear_all.isEnabled()
    panel._clear_all.click()
    assert panel._rows == [] and panel._table.rowCount() == 0
    assert panel._title.text() == panel._content.toPlainText() == ""
    assert not panel._clear_all.isEnabled() and not panel._create.isEnabled()
    assert panel._loaded_course_id == "123"
    assert panel._section.currentData() == Section("20", "Unit 1 / Lessons")
    restored = PageCreatorPanel(panel._mw)
    qtbot.addWidget(restored)
    assert restored._rows == []


def test_clear_all_cannot_discard_an_active_batch(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles(["One", "Two"])
    panel._set_busy(True, creating=True)
    assert not panel._clear_all.isEnabled()
    panel._clear_all_rows()
    assert [row["title"] for row in panel._rows] == ["One", "Two"]
    panel._set_busy(False)
    assert panel._clear_all.isEnabled()


def test_activity_can_collapse_and_errors_are_revealed(qtbot):
    panel = make_panel(qtbot)
    assert panel._log.isHidden()
    panel._activity_toggle.setChecked(True)
    assert not panel._log.isHidden()
    panel._activity_toggle.setChecked(False)
    panel._queue.put(("error", ValueError("Choose a different page title")))
    panel._poll()
    assert panel._activity_toggle.isChecked()
    assert not panel._log.isHidden()
    assert panel._activity_summary.text() == "Choose a different page title"
    assert any("Choose a different page title" in text for text, _tag in panel._log._entries)


def test_narrow_layout_stacks_and_all_actions_remain_reachable(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    import gui_styles

    # Offscreen Windows Qt may not discover the host's system fonts.
    if sys.platform == "win32" and not QFontDatabase.families():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        QApplication.instance().setFont(QFont("Segoe UI", 10))
    QApplication.instance().setStyleSheet(gui_styles.get_stylesheet())
    panel = make_panel(qtbot)
    panel._append_titles(["One", "Two"])
    panel.resize(560, 560)
    panel.show()
    qtbot.wait(20)
    assert panel._splitter.orientation() == Qt.Orientation.Vertical
    assert panel._scroll.horizontalScrollBar().maximum() == 0
    panel._scroll.ensureWidgetVisible(panel._create)
    qtbot.wait(20)
    button_point = panel._create.mapTo(panel._scroll.viewport(), panel._create.rect().center())
    assert panel._scroll.viewport().rect().contains(button_point)
    panel.resize(960, 800)
    qtbot.wait(20)
    assert panel._splitter.orientation() == Qt.Orientation.Horizontal


def test_progress_updates_on_verified_creation_only(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles(["One", "Two"])
    panel._set_busy(True, creating=True)
    assert not panel._progress.isHidden()
    assert panel._progress.maximum() == 2 and panel._progress.value() == 0
    first = CreatedPage(PageDraft("One"), "101", "https://example.test/101")
    panel._queue.put(("progress", (0, first)))
    panel._poll()
    assert panel._progress.value() == 1
    panel._queue.put(("done", ([0, 1], BatchResult((first,), stopped=True))))
    panel._poll()
    assert panel._progress.isHidden()
    assert panel._rows[1]["state"] == "pending"


def test_changing_url_invalidates_loaded_section(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles(["One"])
    assert panel._create.isEnabled()
    panel._url.setText("https://learn.okanagancollege.ca/d2l/le/lessons/456")
    assert panel._loaded_course_id == ""
    assert not panel._create.isEnabled()


def test_course_url_requires_explicit_section_selection(qtbot):
    panel = make_panel(qtbot)
    panel._url.setText("https://learn.okanagancollege.ca/d2l/le/lessons/123")
    panel._append_titles(["One"])
    panel._queue.put(("loaded", ("123", [Section("20", "Unit 1"), Section("21", "Unit 2")])))
    panel._poll()
    assert panel._section.currentData() is None
    assert not panel._create.isEnabled()
    panel._section.setCurrentIndex(1)
    assert panel._create.isEnabled()


def test_open_section_uses_loaded_course_and_selected_module(qtbot, monkeypatch):
    panel = make_panel(qtbot)
    opened = []
    monkeypatch.setattr("panels.page_creator_panel.webbrowser.open", opened.append)
    panel._open_section.click()
    assert opened == ["https://learn.okanagancollege.ca/d2l/le/lessons/123/units/20"]


def test_partial_failure_locks_uncertain_row_and_skips_completed_pages_on_next_run(qtbot, monkeypatch):
    panel = make_panel(qtbot)
    panel._append_titles(["One", "Two", "Three"])
    first = CreatedPage(PageDraft("One"), "101", "https://example.test/101")
    panel._set_busy(True, creating=True)
    panel._queue.put(("progress", (0, first)))
    panel._queue.put(("done", ([0, 1, 2], BatchResult((first,), 1, "response lost"))))
    panel._poll()
    assert [row["state"] for row in panel._rows] == ["created", "review", "pending"]
    assert not panel._create.isEnabled()
    panel._table.setCurrentCell(1, 0)
    assert panel._content.isReadOnly()
    panel._remove_row()
    assert panel._create.isEnabled()
    captured = []
    async def create(url, section, drafts, *args):
        captured.extend(drafts)
        return BatchResult(())
    import asyncio
    monkeypatch.setattr("panels.page_creator_panel.create_course_pages", create)
    monkeypatch.setattr(panel, "_run_worker", lambda action, kind: asyncio.run(action()))
    panel._create_clicked()
    assert [draft.title for draft in captured] == ["Three"]


def test_draft_restores_content_and_marks_interrupted_pending_rows_for_review(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles(["One", "Two"])
    panel._content.setPlainText("Saved body")
    panel._rows[0]["state"] = "created"
    panel._rows[0]["url"] = "https://example.test/101"
    panel._set_busy(True, creating=True)
    panel.save_state()
    restored = PageCreatorPanel(panel._mw)
    qtbot.addWidget(restored)
    assert restored._rows[0]["content"] == "Saved body"
    assert restored._rows[0]["state"] == "created"
    assert restored._rows[1]["state"] == "review"
    assert not restored._create.isEnabled()


def test_busy_controls_and_stop_keep_pending_content(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles(["One"])
    panel._content.setPlainText("Keep me")
    panel._set_busy(True, creating=True)
    assert not panel._url.isEnabled() and not panel._paste.isEnabled()
    assert panel._content.isReadOnly()
    panel._stop.click()
    assert panel._stop_event.is_set()
    assert not panel._stop.isEnabled()
    panel._queue.put(("done", ([0], BatchResult((), stopped=True))))
    panel._poll()
    assert panel._rows[0]["state"] == "pending"
    assert panel._rows[0]["content"] == "Keep me"
    assert panel._create.isEnabled()


def test_preflight_error_keeps_draft_editable_and_persisted(qtbot):
    panel = make_panel(qtbot)
    panel._append_titles(["One"])
    panel._set_busy(True, creating=True)
    panel._queue.put(("error", ValueError("This section already contains these titles: One")))
    panel._poll()
    assert panel._rows[0]["state"] == "pending"
    assert not panel._busy and not panel._creating
    assert not panel._content.isReadOnly()
    assert not panel._mw.cfg["page_creator_draft"]["active"]


def test_build_hub_routes_creator_and_settings(qtbot, monkeypatch):
    from gui import MainWindow

    for name in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                 "_start_update_check", "_show_welcome_if_needed"):
        monkeypatch.setattr(MainWindow, name, lambda self: None)
    monkeypatch.setattr(MainWindow, "closeEvent", lambda self, event: event.accept())
    monkeypatch.setattr(MainWindow, "load_config", lambda self: {"welcomed": True})
    monkeypatch.setattr(MainWindow, "save_config", lambda self, data: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window._checker._bs_entry.setText("https://learn.okanagancollege.ca/d2l/le/lessons/123")
    window._on_step(7)
    window._build_hub.buttons["create_pages"].click()
    assert window._stack.currentWidget() is window._page_creator
    assert window._page_creator._url.text().endswith("/123")
    window._on_settings()
    assert window._stack.currentWidget() is window._settings


def test_close_during_creation_waits_for_saved_results(qtbot, monkeypatch):
    from gui import MainWindow

    close_event = MainWindow.closeEvent
    for name in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                 "_start_update_check", "_show_welcome_if_needed"):
        monkeypatch.setattr(MainWindow, name, lambda self: None)
    monkeypatch.setattr(MainWindow, "closeEvent", lambda self, event: event.accept())
    monkeypatch.setattr(MainWindow, "load_config", lambda self: {"welcomed": True})
    monkeypatch.setattr(MainWindow, "save_config", lambda self, data: None)
    messages = []
    monkeypatch.setattr("gui.QMessageBox.information", lambda *args: messages.append(args[2]))
    window = MainWindow()
    qtbot.addWidget(window)
    window._page_creator._creating = True
    from PySide6.QtGui import QCloseEvent
    event = QCloseEvent()
    close_event(window, event)
    assert not event.isAccepted()
    assert messages and "Stop" in messages[0]
