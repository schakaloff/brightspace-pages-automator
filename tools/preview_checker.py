"""Capture Check & fix with local sample data; no scans or course writes."""
import argparse
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

URLS = ("https://learn.okanagancollege.ca/d2l/le/lessons/123",
        "https://mymoodle.okanagan.bc.ca/course/view.php?id=456")
REPORT = dict(missing=3, books=1, files=2, h5p=1, broken_links=2, activities=1,
              moodle_links=2, findings=[
                  dict(status="missing", name="Welcome and course overview", type="Page", section="Getting started"),
                  dict(status="missing", name="Practice activity", type="Assignment", section="Week 1"),
                  dict(status="review", name="Required readings", type="Book", section="Week 2")],
              broken_pages=["Lesson 1 resources", "Before you begin"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="after")
    args = parser.parse_args()
    if not args.label.isalnum():
        parser.error("Use a short label containing letters and numbers")
    app = QApplication([])
    app.setStyle("Fusion")
    if not QFontDatabase.families() and os.name == "nt":
        for font in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / font))
    app.setFont(QFont("Segoe UI", 10))
    for method in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                   "_start_update_check", "_show_welcome_if_needed"):
        setattr(MainWindow, method, lambda self: None)
    MainWindow.load_config = lambda self: dict(welcomed=True, chk_bs_url=URLS[0], chk_moodle_url=URLS[1])
    MainWindow.save_config = lambda self, data: None
    MainWindow.closeEvent = lambda self, event: event.accept()
    window = MainWindow()
    panel = window._checker
    window.show()
    destination = ROOT / "test-results" / "checker-design"
    destination.mkdir(parents=True, exist_ok=True)
    def capture(name):
        for _ in range(3): app.processEvents()
        window.grab().save(str(destination / f"{args.label}-{name}.png"))
    for theme in ("light", "dark"):
        window.set_theme(theme)
        for width, height in ((1120, 800), (720, 560)):
            window.resize(width, height)
            panel._invalidate_scan()
            panel._summary.setText("No scan yet. Start here to see what needs attention.")
            panel.show_review()
            panel._details_btn.setChecked(False)
            for box in panel._fix_boxes.values(): box.setChecked(False)
            if hasattr(panel, "_scroll"): panel._scroll.verticalScrollBar().setValue(0)
            capture(f"{theme}-{width}x{height}-empty")
            panel._show_scan_report(REPORT, URLS)
            capture(f"{theme}-{width}x{height}-results")
            panel.show_transfer()
            panel._fix_boxes["repair_files"].setChecked(True)
            panel._fix_boxes["content"].setChecked(True)
            capture(f"{theme}-{width}x{height}-fix")
            if hasattr(panel, "_scroll"):
                panel._scroll.ensureWidgetVisible(panel._apply_btn)
                capture(f"{theme}-{width}x{height}-apply")
        window.resize(1120, 800)
        panel.show_review()
        panel._log.clear_log()
        panel._details_btn.setChecked(True)
        panel._log.append_log("Sample warning: a file link needs review.", "warning")
        if hasattr(panel, "_ensure_visible"): panel._ensure_visible(panel._log)
        capture(f"{theme}-activity")
        panel._details_btn.setChecked(False)
    window.close()
    print(destination)


if __name__ == "__main__": main()
