"""Launch or capture the link-review preview, without credentials or requests."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    if not args.interactive:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from gui import MainWindow
    app = QApplication([])
    app.setStyle("Fusion")
    for filename in ("segoeui.ttf", "segoeuib.ttf"):
        font = Path("C:/Windows/Fonts") / filename
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))
    for method in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                   "_start_update_check", "_show_welcome_if_needed"):
        setattr(MainWindow, method, lambda self: None)
    MainWindow.load_config = lambda self: {"welcomed": True}
    MainWindow.save_config = lambda self, data: None
    MainWindow.closeEvent = lambda self, event: event.accept()
    window = MainWindow()
    window.setWindowTitle("Brightspace Automator — Link review preview")
    window._on_step(10)
    window.set_theme("light")
    window.show()
    if args.interactive:
        window._link_review._existing_btn.hide()
        # The standalone preview cannot route into operational panels.
        window._sidebar.step_clicked.disconnect()
        window._sidebar.settings_clicked.disconnect()
        from PySide6.QtWidgets import QMessageBox
        def preview_only(*args):
            QMessageBox.information(window, "Preview", "This window previews link review only. Open the regular app for other tools.")
        window._sidebar.step_clicked.connect(preview_only)
        window._sidebar.settings_clicked.connect(preview_only)
        return app.exec()
    destination = ROOT / "test-results" / "link-review-design"
    destination.mkdir(parents=True, exist_ok=True)
    panel = window._link_review
    def capture(name):
        for _ in range(4):
            app.processEvents()
        window.grab().save(str(destination / name))
    for theme in ("light", "dark"):
        window.set_theme(theme)
        for width, height in ((1487, 1058), (1120, 800), (720, 560)):
            window.resize(width, height)
            panel._scroll.verticalScrollBar().setValue(0)
            capture(f"{theme}-{width}x{height}-choose.png")
            panel._next()
            capture(f"{theme}-{width}x{height}-review.png")
            panel._back()
        window.resize(1120, 800)
        panel._edit_link("notes")
        for _ in range(3):
            app.processEvents()
        panel._active_dialog.grab().save(str(destination / f"{theme}-edit.png"))
        panel._active_dialog.reject()
    window.close()
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
