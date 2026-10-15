"""Capture Check & fix with local sample data; no scans or course writes."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication
import keyring
import update_checker
from gui import MainWindow

URLS = ("https://learn.okanagancollege.ca/d2l/le/lessons/123",
        "https://mymoodle.okanagan.bc.ca/course/view.php?id=456")
REPORT = dict(missing=3, books=1, files=2, h5p=1, broken_links=2, activities=1,
              moodle_links=2, findings=[
                  dict(status="missing", name="Welcome and course overview", type="Page", section="Getting started"),
                  dict(status="missing", name="Practice activity", type="Assignment", section="Week 1"),
                  dict(status="fuzzy", name="Required readings", type="Book", section="Week 2")],
              file_link_check="complete",
              book_findings=[dict(name="Required readings", section="Week 2")],
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
    keyring.get_password = lambda *args: None
    keyring.set_password = lambda *args: None
    keyring.delete_password = lambda *args: None
    update_checker.get_update_diagnostics = lambda: {}
    for method in ("_set_window_icon", "_load_api_key", "_start_chromium_check",
                   "_start_update_check", "_show_welcome_if_needed", "_refresh_update_diagnostics"):
        setattr(MainWindow, method, lambda self: None)
    MainWindow.load_config = lambda self: dict(welcomed=True, chk_bs_url=URLS[0], chk_moodle_url=URLS[1])
    MainWindow.save_config = lambda self, data: None
    MainWindow.closeEvent = lambda self, event: event.accept()
    window = MainWindow()
    panel = window._checker
    panel._run_worker = lambda *args, **kwargs: None
    panel._poll_timer.stop()
    window.show()
    destination = ROOT / "test-results" / "checker-design"
    destination.mkdir(parents=True, exist_ok=True)
    def capture(name):
        for _ in range(3): app.processEvents()
        window.grab().save(str(destination / f"{args.label}-{name}.png"))
    for theme in ("dark", "light"):
        window.set_theme(theme)
        for width, height in ((1487, 1058), (900, 720), (720, 560)):
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
            for group in ("links", "content", "books", "other"):
                panel._open_group(group)
                capture(f"{theme}-{width}x{height}-{group}")
            panel._open_group("content")
            panel._fix_boxes["repair_files"].setChecked(True)
            panel._fix_boxes["content"].setChecked(True)
            capture(f"{theme}-{width}x{height}-fix")
            panel._preview_selected()
            capture(f"{theme}-{width}x{height}-confirm")
        window.resize(900, 720)
        panel._show_scan_report(dict(REPORT, file_link_check="failed", broken_links=0, broken_pages=[]), URLS)
        capture(f"{theme}-partial-scan")
        panel._task_active = True
        panel._run_mode = "scan"
        panel._subtitle.setText("Scanning your course pair.")
        panel._workspace.hide()
        panel._progress.show()
        panel._status_line.setText("Waiting for Moodle login")
        panel._moodle_hint.show()
        panel._ready_btn.show()
        panel._update_apply_enabled()
        capture(f"{theme}-login-pause")
        panel._task_active = False
        panel._progress.hide()
        panel._moodle_hint.hide()
        panel._ready_btn.hide()
        panel._show_run_result(dict(mode="apply", status="stopped", summary={"files_uploaded": [("Example.pdf", "Week 1")]}, errors=[]))
        capture(f"{theme}-stopped")
        panel._show_run_result(dict(mode="apply", status="finished", summary={"files_uploaded": [], "files_failed": [("Example.pdf", "Week 1")], "broken_links_repaired": 1}, errors=["A file upload needs attention."]))
        capture(f"{theme}-outcome")
        panel._show_scan_report(REPORT, URLS)
        panel.show_review()
        panel._log.clear_log()
        panel._details_btn.setChecked(True)
        panel._log.append_log("Sample warning: a file link needs review.", "warning")
        if hasattr(panel, "_ensure_visible"): panel._ensure_visible(panel._log)
        capture(f"{theme}-activity")
        panel._details_btn.setChecked(False)
    source = QImage(str(ROOT.parent / "checker-ux-review" / "checker-concept-v2.png"))
    implementation = QImage(str(destination / f"{args.label}-dark-1487x1058-results.png"))
    if not source.isNull():
        comparison = QImage(source.width() + implementation.width(), max(source.height(), implementation.height()), QImage.Format.Format_RGB32)
        comparison.fill(0x10161f)
        painter = QPainter(comparison)
        painter.drawImage(0, 0, source)
        painter.drawImage(source.width(), 0, implementation)
        painter.end()
        comparison.save(str(destination / "comparison-overview.png"))
        source_region = source.copy(292, 290, 1148, 464)
        implementation_region = implementation.copy(204, 205, 1239, 480)
        comparison = QImage(source_region.width() + implementation_region.width(), 480, QImage.Format.Format_RGB32)
        comparison.fill(0x10161f)
        painter = QPainter(comparison)
        painter.drawImage(0, 0, source_region)
        painter.drawImage(source_region.width(), 0, implementation_region)
        painter.end()
        comparison.save(str(destination / "comparison-findings.png"))
    window.close()
    print(destination)


if __name__ == "__main__": main()
