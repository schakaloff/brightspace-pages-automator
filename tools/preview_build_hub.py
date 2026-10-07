"""Capture the native Build & style hub without credentials or course actions."""
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


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    if not QFontDatabase.families() and os.name == "nt":
        for font in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / font))
    app.setFont(QFont("Segoe UI", 10))
    for name in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                 "_start_update_check", "_show_welcome_if_needed"):
        setattr(MainWindow, name, lambda self: None)
    MainWindow.load_config = lambda self: {"welcomed": True}
    MainWindow.save_config = lambda self, data: None
    MainWindow.closeEvent = lambda self, event: event.accept()
    window = MainWindow()
    window._on_step(7)
    window.show()
    destination = ROOT / "test-results" / "build-hub-design"
    destination.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        window.set_theme(theme)
        for width, height in ((1586, 992), (1120, 800), (1120, 700), (720, 560)):
            window.resize(width, height)
            for _ in range(3):
                app.processEvents()
            hub = window._build_hub
            assert hub._scroll.horizontalScrollBar().maximum() == 0
            window.grab().save(str(destination / f"hub-{theme}-{width}x{height}.png"))
            if width == 720:
                hub._scroll.verticalScrollBar().setValue(hub._scroll.verticalScrollBar().maximum())
                app.processEvents()
                window.grab().save(str(destination / f"hub-{theme}-small-bottom.png"))
            hub._scroll.verticalScrollBar().setValue(0)
    window.close()
    print(destination)


if __name__ == "__main__":
    main()
