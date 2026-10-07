"""Render startup states and a mocked interface initialization without network."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from gui import MainWindow
from gui_splash import StartupSplash
import gui_styles


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    if not QFontDatabase.families() and os.name == "nt":
        for font in ("segoeui.ttf", "segoeuib.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / font))
    app.setFont(QFont("Segoe UI", 10))
    destination = ROOT / "test-results" / "splash-design"
    destination.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        gui_styles.set_theme(theme)
        splash = StartupSplash()
        splash.show()
        for name, message, progress in (("starting", "Starting…", 0),
                                         ("loading", "Loading panels…", .4),
                                         ("ready", "Ready", 1)):
            splash.set_stage(message, progress)
            app.processEvents()
            splash.grab().save(str(destination / f"after-{theme}-{name}.png"))
        for method in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                       "_start_update_check", "_show_welcome_if_needed"):
            setattr(MainWindow, method, lambda self: None)
        MainWindow.load_config = lambda self: {"welcomed": True, "theme": theme}
        MainWindow.save_config = lambda self, data: None
        MainWindow.closeEvent = lambda self, event: event.accept()
        window = MainWindow(splash=splash)
        assert splash.status == "Ready" and splash.progress == 1
        window.show()
        splash.finish(window)
        assert not splash.isVisible() and window.isVisible()
        window.close()
    print(destination)


if __name__ == "__main__":
    main()
