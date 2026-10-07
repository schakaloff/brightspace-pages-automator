"""Render the page checklist with sample data; no browser, AI or course writes."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from gui_dialogs import PagesDialog
import gui_styles


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    if not QFontDatabase.families() and os.name == "nt":
        for font in ("segoeui.ttf", "segoeuib.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / font))
    app.setFont(QFont("Segoe UI", 10))
    destination = ROOT / "test-results" / "restyle-design"
    destination.mkdir(parents=True, exist_ok=True)
    pages = [{"label": label, "url": f"https://example.test/topics/{i}"} for i, label in enumerate(
        ["Welcome to the course", "Learning objectives", "Before you begin", "Lesson 1: Introduction"]
        + [f"Lesson {i}: Practice and review" for i in range(2, 18)])]
    dialog = PagesDialog(pages)
    for i in (0, 3, 12):
        dialog._checks[i].setChecked(True)
    dialog.show()
    for theme in ("light", "dark"):
        gui_styles.set_theme(theme)
        app.setStyleSheet(gui_styles.get_stylesheet())
        dialog._search.clear()
        app.processEvents()
        dialog.grab().save(str(destination / f"picker-{theme}.png"))
        dialog._search.setText("Lesson 1")
        app.processEvents()
        dialog.grab().save(str(destination / f"picker-{theme}-filtered.png"))
    dialog.close()
    print(destination)


if __name__ == "__main__":
    main()
