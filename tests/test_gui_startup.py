"""Check startup stage reporting and the handoff to the main window."""
import sys
sys.path.insert(0, "src")

from gui_splash import StartupSplash


def test_startup_reports_ready_and_hands_off_to_window(qtbot, monkeypatch):
    from gui import MainWindow
    for method in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                   "_start_update_check", "_show_welcome_if_needed"):
        monkeypatch.setattr(MainWindow, method, lambda self: None)
    monkeypatch.setattr(MainWindow, "closeEvent", lambda self, event: event.accept())
    monkeypatch.setattr(MainWindow, "load_config", lambda self: {"welcomed": True})
    monkeypatch.setattr(MainWindow, "save_config", lambda self, data: None)
    splash = StartupSplash()
    qtbot.addWidget(splash)
    stages = []
    original = splash.set_stage
    def record(message, progress):
        stages.append(message)
        original(message, progress)
    splash.set_stage = record
    splash.show()
    window = MainWindow(splash=splash)
    qtbot.addWidget(window)
    assert stages == ["Loading interface…", "Loading panels…", "Loading credentials…", "Ready"]
    assert splash.progress == 1.0
    window.show()
    splash.finish(window)
    assert window.isVisible() and not splash.isVisible()
