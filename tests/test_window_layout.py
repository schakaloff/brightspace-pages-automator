"""Half-screen sizing, automatic navigation and active-page scroll boundaries."""
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

sys.path.insert(0, 'src')


def settle():
    for _ in range(4):
        QApplication.processEvents()


def visible_bounds(widget, viewport):
    return viewport.rect().contains(QRect(widget.mapTo(viewport, QPoint()), widget.size()))


@pytest.fixture
def window(qtbot, monkeypatch):
    from gui import MainWindow
    import update_checker
    import panels.h5p_panel
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf'):
        QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts') / name))
    QApplication.instance().setFont(QFont('Segoe UI', 10))
    monkeypatch.setattr(update_checker, 'get_update_diagnostics', lambda: {})
    monkeypatch.setattr(panels.h5p_panel, 'saved_runs', lambda *args: [])
    for method in ('_set_window_icon', '_load_api_key', '_start_chromium_check',
                   '_start_update_check', '_show_welcome_if_needed'):
        monkeypatch.setattr(MainWindow, method, lambda self: None)
    monkeypatch.setattr(MainWindow, 'closeEvent', lambda self, event: event.accept())
    monkeypatch.setattr(MainWindow, 'load_config', lambda self: dict(welcomed=True))
    writes = []
    monkeypatch.setattr(MainWindow, 'save_config', lambda self, values: writes.append(values))
    widget = MainWindow()
    qtbot.addWidget(widget)
    widget.resize(1120, 800)
    widget.show()
    settle()
    widget._test_config_writes = writes
    return widget


@pytest.mark.parametrize('size', [(624, 640), (560, 480)])
def test_half_screen_keeps_media_rows_reachable_without_horizontal_scrolling(window, size):
    window._on_step(6)
    window.resize(*size)
    settle()
    assert window.width() == size[0] and window.height() == size[1]
    assert window._sidebar.is_collapsed()
    assert window._content_scroll.horizontalScrollBar().maximum() == 0
    hub = window._media_hub
    assert hub._scroll.horizontalScrollBar().maximum() == 0
    for row in hub.buttons.values():
        hub._scroll.ensureWidgetVisible(row)
        settle()
        assert visible_bounds(row, hub._scroll.viewport())


def test_automatic_sidebar_changes_do_not_overwrite_the_users_preference(window):
    writes = window._test_config_writes
    writes.clear()
    window.resize(624, 640)
    settle()
    assert window._sidebar.is_collapsed()
    window.resize(1120, 800)
    settle()
    assert not window._sidebar.is_collapsed()
    assert not any('sidebar_collapsed' in change for change in writes)
    window._sidebar.set_collapsed(True, animate=False)
    assert writes[-1] == dict(sidebar_collapsed=True)
    window.resize(624, 640)
    settle()
    window.resize(1120, 800)
    settle()
    assert window._sidebar.is_collapsed()


def test_user_can_reopen_navigation_while_narrow_without_resizing_the_window(window):
    window._on_step(6)
    window.resize(624, 640)
    settle()
    window._sidebar.set_collapsed(False, animate=False)
    settle()
    assert not window._sidebar.is_collapsed()
    assert window.width() == 624
    assert window._test_config_writes[-1] == dict(sidebar_collapsed=False)


def test_h5p_back_returns_from_results_to_courses_then_media_without_clearing_urls(window):
    window._open_tool('h5p')
    panel = window._h5p
    panel._moodle_entry.setText('https://moodle.example/course/view.php?id=10')
    panel._bs_entry.setText('https://bs.example/d2l/home/1')
    panel._show_results(False)
    panel._back_btn.click()
    assert window._stack.currentWidget() is panel
    assert panel._stage == 0 and panel._connection.isVisible()
    panel._back_btn.click()
    assert window._stack.currentWidget() is window._media_hub
    window._open_tool('h5p')
    assert panel._moodle_entry.text() == 'https://moodle.example/course/view.php?id=10'
    assert panel._bs_entry.text() == 'https://bs.example/d2l/home/1'


def test_wide_legacy_page_scrolls_without_widening_the_window_or_other_pages(window):
    window.resize(624, 640)
    window._stack.setCurrentWidget(window._transfer)
    settle()
    assert window.width() == 624
    assert window._content_scroll.horizontalScrollBar().maximum() > 0
    window._content_scroll.ensureWidgetVisible(window._transfer._primary)
    settle()
    assert visible_bounds(window._transfer._primary, window._content_scroll.viewport())
    window._on_step(6)
    settle()
    assert window._content_scroll.horizontalScrollBar().maximum() == 0
    assert window._content_scroll.verticalScrollBar().maximum() == 0
    assert window._media_hub.width() == window._content_scroll.viewport().width()


def test_resize_keeps_courses_and_primary_actions_available(window):
    bs_url = 'https://learn.example/d2l/le/lessons/123'
    window._checker._bs_entry.setText(bs_url)
    for size in ((624, 640), (560, 480), (1120, 800)):
        window.resize(*size)
        for panel in (window._checker, window._h5p):
            window._stack.setCurrentWidget(panel)
            settle()
            assert window._content_scroll.horizontalScrollBar().maximum() == 0
            assert visible_bounds(panel._run_btn, window._content_scroll.viewport())
    assert window._checker._bs_entry.text() == bs_url
