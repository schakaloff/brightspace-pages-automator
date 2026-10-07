import sys

sys.path.insert(0, "src")

from content_cleanup import ContentTopic


class MainWindowStub:
    def load_config(self):
        return {}


def test_filter_and_selection_keep_hidden_checked_topics(qtbot):
    from PySide6.QtCore import QPoint, Qt
    from panels.cleanup_panel import CleanupPanel

    panel = CleanupPanel(MainWindowStub())
    qtbot.addWidget(panel)
    panel._display_topics([
        ContentTopic("1", "Video A", "10", "Unit 1", "2"),
        ContentTopic("2", "Assignment B", "11", "Unit 2", "3"),
    ])
    panel._loaded_course_id = "123"
    panel.resize(900, 600)
    panel.show()
    qtbot.wait(50)
    check = panel._table.cellWidget(0, 0)
    qtbot.mouseClick(
        check, Qt.MouseButton.LeftButton, pos=QPoint(8, check.height() // 2),
    )
    assert [item.id for item in panel._selected_topics()] == ["1"]
    title_rect = panel._table.visualItemRect(panel._table.item(1, 1))
    qtbot.mouseClick(
        panel._table.viewport(), Qt.MouseButton.LeftButton, pos=title_rect.center()
    )
    assert [item.id for item in panel._selected_topics()] == ["1", "2"]
    panel._clear.click()
    panel._filter.setText("Video")
    panel._select_visible.click()
    assert [item.id for item in panel._selected_topics()] == ["1"]
    panel._filter.setText("Assignment")
    panel._select_visible.click()
    assert [item.id for item in panel._selected_topics()] == ["1", "2"]
    panel._clear.click()
    assert panel._selected_topics() == []
    assert not panel._remove.isEnabled()


def test_build_hub_opens_cleanup_without_moving_settings(qtbot, monkeypatch):
    from gui import MainWindow

    monkeypatch.setattr(MainWindow, "_set_window_icon", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_api_key", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_chromium_check", lambda self: None)
    monkeypatch.setattr(MainWindow, "_start_update_check", lambda self: None)
    monkeypatch.setattr(MainWindow, "_show_welcome_if_needed", lambda self: None)
    monkeypatch.setattr(MainWindow, "closeEvent", lambda self, event: event.accept())
    monkeypatch.setattr(MainWindow, "load_config", lambda self: {"welcomed": True})
    monkeypatch.setattr(MainWindow, "save_config", lambda self, value: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_step(7)
    window._build_hub.buttons["cleanup"].click()
    assert window._stack.currentWidget() is window._cleanup
    window._on_settings()
    assert window._stack.currentWidget() is window._settings
