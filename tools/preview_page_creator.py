"""Render the bulk page creator with sample data, without login or course writes.

Run with the app's Python environment:
    python tools/preview_page_creator.py --label after
Screenshots are saved beneath test-results/page-creator-design/.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from bulk_page_creator import Section
from gui import MainWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="preview")
    args = parser.parse_args()
    if not args.label.replace("-", "").isalnum():
        parser.error("Use letters, numbers and hyphens for the label")
    destination = ROOT / "test-results" / "page-creator-design"
    destination.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    app.setStyle("Fusion")
    # Offscreen Qt in a restricted environment may not enumerate Windows fonts.
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
    window._chromium_ready = True
    window._on_step(9)
    panel = window._page_creator
    panel._url.setText("https://learn.okanagancollege.ca/d2l/le/lessons/123/units/20")
    panel._queue.put(("loaded", ("123", [Section("20", "Week 1 / Getting started"),
                                        Section("21", "Week 2 / Practice")])))
    panel._poll()
    panel._append_titles(["Welcome", "Learning objectives", "Before you begin"] +
                         [f"Lesson {number}" for number in range(1, 18)])
    panel._content.setPlainText("Welcome to the course!\n\nBy the end of this section, you will be ready to begin the first lesson.\n\nRead the overview and complete the practice activity.")
    window.show()
    for theme in ("light", "dark"):
        window.set_theme(theme)
        for size in ((1120, 800), (720, 560)):
            window.resize(*size)
            app.processEvents()
            name = f"{args.label}-{theme}-{size[0]}x{size[1]}.png"
            window.grab().save(str(destination / name))
            print(destination / name)

    # Capture secondary states without sending anything to Brightspace.
    saved_rows = [dict(row) for row in panel._rows]
    window.resize(1120, 800)
    for theme in ("light", "dark"):
        window.set_theme(theme)
        panel._rows = [dict(row) for row in saved_rows]
        panel._refresh_table(0)
        panel._activity_toggle.setChecked(False)
        panel._append_log("Loaded 2 sections. Choose where to put the pages.", "success")
        panel._scroll.verticalScrollBar().setValue(0)
        app.processEvents()
        panel._format.showPopup()
        app.processEvents()
        panel._format.view().window().grab().save(str(destination / f"{args.label}-{theme}-dropdown.png"))
        panel._format.hidePopup()

        def capture_dialog(name):
            dialog = app.activeModalWidget()
            if dialog is None:
                raise RuntimeError("Expected a modal preview dialog")
            dialog.grab().save(str(destination / f"{args.label}-{theme}-{name}.png"))
            dialog.reject()

        QTimer.singleShot(30, lambda: capture_dialog("paste-titles"))
        panel._paste_titles()
        QTimer.singleShot(30, lambda: capture_dialog("content-preview"))
        panel._preview_clicked()

        panel._rows = []
        panel._refresh_table()
        app.processEvents()
        window.grab().save(str(destination / f"{args.label}-{theme}-empty.png"))
        panel._rows = [dict(row) for row in saved_rows]
        panel._rows[0].update(state="created", url="https://example.test/sample-page")
        panel._rows[1]["state"] = "review"
        panel._refresh_table(1)
        panel._append_log("The second page needs review. Check whether it exists in Brightspace before retrying. Later pages have not been sent.", "warning")
        app.processEvents()
        window.grab().save(str(destination / f"{args.label}-{theme}-partial-result.png"))
    window.close()


if __name__ == "__main__":
    main()
