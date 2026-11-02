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

URLS = ("https://learn.okanagancollege.ca/d2l/le/lessons/25707",
        "https://mymoodle.okanagan.bc.ca/course/view.php?id=187689")
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
    destination = ROOT / "test-results" / "checker-design" / "flow"
    destination.mkdir(parents=True, exist_ok=True)
    def capture(name):
        for _ in range(3): app.processEvents()
        if app.focusWidget(): app.focusWidget().clearFocus()
        app.processEvents()
        window.grab().save(str(destination / f"{args.label}-{name}.png"))
    for theme in ("light", "dark"):
        window.set_theme(theme)
        for width, height in ((1120, 800), (624, 640), (560, 480)):
            window.resize(width, height)
            panel._task_active = False
            panel._progress.hide()
            panel._moodle_hint.hide()
            panel._ready_btn.hide()
            panel._connection_steps.hide()
            panel._attention_title.hide()
            panel._details_btn.setChecked(False)
            panel._invalidate_scan()
            capture(f"{theme}-{width}-connect")

            panel._task_active = True
            panel._run_mode = "scan"
            panel._set_stage(0)
            panel._course_form.hide()
            panel._course_summary.show()
            panel._workspace.hide()
            panel._progress.show()
            panel._log_queue.put(("__CHK_MOODLE_WAITING__", ""))
            panel._poll_log()
            panel._update_apply_enabled()
            capture(f"{theme}-{width}-login")

            panel._task_active = False
            panel._progress.hide()
            panel._moodle_hint.hide()
            panel._ready_btn.hide()
            panel._connection_steps.hide()
            panel._attention_title.hide()
            panel._show_scan_report(REPORT, URLS)
            panel._fix_boxes["repair_files"].setChecked(True)
            panel._fix_boxes["content"].setChecked(True)
            capture(f"{theme}-{width}-review")
            panel._open_group("content")
            capture(f"{theme}-{width}-findings")
            panel._go_back()
            panel._preview_selected()
            capture(f"{theme}-{width}-confirm")

            panel._task_active = True
            panel._run_mode = "apply"
            panel._completed_stages = {0, 1}
            panel._set_stage(2)
            panel._workspace.hide()
            panel._course_summary.show()
            panel._progress.show()
            panel._progress_bar.show()
            panel._status_line.setText("Updating file links in Lesson 1 resources")
            panel._render_running_choices(["repair_files", "content"])
            panel._progress_hint.setText("Selected fix types: Repair broken file links; Add missing pages and units")
            panel._update_apply_enabled()
            capture(f"{theme}-{width}-apply")

            panel._task_active = False
            panel._progress.hide()
            panel._show_run_result(dict(mode="apply", status="finished", summary={
                "files_uploaded": [("Example.pdf", "Week 1")],
                "files_failed": [("Missing.pdf", "Before you begin")],
                "broken_links_found": 2, "broken_links_repaired": 1,
            }, errors=["Before you begin: the source file was unavailable."]))
            capture(f"{theme}-{width}-results")
            panel._show_scan_report(dict(REPORT, file_link_check="failed", broken_links=0, broken_pages=[]), URLS)
            capture(f"{theme}-{width}-partial")
        panel._details_btn.setChecked(False)
    window.close()
    print(destination)


if __name__ == "__main__": main()
