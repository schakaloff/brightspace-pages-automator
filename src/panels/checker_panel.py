import asyncio
import queue
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QToolButton, QSizePolicy, QCheckBox,
    QTabWidget, QFrame, QListWidget, QMessageBox, QScrollArea, QGridLayout,
)
from PySide6.QtCore import Qt, Signal, QTimer, QPoint

import gui_styles
from gui_icons import make_pixmap
from gui_log import LogWidget
from panels._shared import friendly_error


class CheckerPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()
    open_tool = Signal(str)

    FIXES = (
        ("repair_files", "Repair broken file links", "Store missing files in Manage Files and update existing pages."),
        ("content", "Add missing pages and units", "Create Moodle pages, links and units that are missing in Brightspace."),
        ("files", "Upload missing course files", "Choose which missing files to upload after the scan."),
        ("activities", "Connect assignments and quizzes", "Link existing Brightspace activities into Content."),
        ("books", "Split Moodle Books", "Create one Brightspace page per chapter, in Moodle order."),
        ("order", "Match Moodle page order", "Reorder existing Brightspace pages within each unit."),
    )

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._moodle_ready_event = None
        self._h5p_ready_event    = None
        self._file_checklist_event = None
        self._h5p_skip_flag      = [False]
        self._stop_flag          = [False]
        self._worker_thread      = None
        self._worker_loop        = None
        self._worker_task        = None
        self._task_active        = False
        self._active_dialogs     = []
        self._moodle_ready_bound = False
        self._h5p_ready_bound    = False
        self._h5p_skip_bound     = False
        self._scan_report        = None
        self._scan_urls          = None
        self._build()
        self.refresh_theme()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        self.setObjectName("checker_panel")
        self.setProperty("design", "modern")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)
        body = QWidget()
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(26, 26, 26, 22)
        layout.setSpacing(0)
        self._scroll.setWidget(body)

        heading = QLabel("Check & fix")
        heading.setProperty("role", "page-heading")
        layout.addWidget(heading)
        layout.addSpacing(4)
        sub = QLabel("Compare your courses, then choose the changes to apply.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        courses = QFrame()
        courses.setProperty("role", "surface")
        course_layout = QVBoxLayout(courses)
        course_layout.setContentsMargins(20, 18, 20, 20)
        course_layout.setSpacing(14)
        courses_heading = QLabel("Your courses")
        courses_heading.setProperty("role", "section-heading")
        course_layout.addWidget(courses_heading)
        self._course_grid = QGridLayout()
        self._course_grid.setSpacing(16)
        self._course_fields = []
        course_layout.addLayout(self._course_grid)
        self._bs_entry = QLineEdit()
        self._bs_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/<id>/home")
        self._bs_entry.setMinimumWidth(0)
        self._bs_entry.setToolTip("Paste the Brightspace course home URL.\nExample: .../d2l/le/content/<id>/home")
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setMinimumWidth(0)
        self._moodle_entry.setToolTip(
            "Paste a Moodle course home or section URL. A section URL checks only that section.\n"
            "Requires Teacher-level access to download files and H5P."
        )
        for label, entry in (("Brightspace course URL", self._bs_entry),
                             ("Moodle course or section URL", self._moodle_entry)):
            field = QWidget()
            field.setMinimumWidth(0)
            column = QVBoxLayout(field)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(6)
            name = QLabel(label)
            name.setProperty("role", "field-label")
            column.addWidget(name)
            column.addWidget(entry)
            self._course_fields.append(field)
        self._compact_courses = None
        self._reflow_courses()
        layout.addWidget(courses)
        layout.addSpacing(20)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("course_workflow_tabs")
        self._tabs.tabBar().setDrawBase(False)
        self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._tabs.setFixedHeight(380)
        scan_page = QFrame()
        scan_page.setProperty("role", "surface")
        scan_layout = QVBoxLayout(scan_page)
        scan_layout.setContentsMargins(20, 20, 20, 18)
        scan_layout.setSpacing(12)
        scan_header = QHBoxLayout()
        scan_header.setSpacing(18)
        scan_text = QVBoxLayout()
        scan_text.setSpacing(4)
        scan_title = QLabel("Compare your courses")
        scan_title.setProperty("role", "section-heading")
        scan_text.addWidget(scan_title)
        scan_intro = QLabel("Find missing content and broken file links. Scanning leaves Brightspace unchanged.")
        scan_intro.setWordWrap(True)
        scan_intro.setProperty("role", "dim")
        scan_text.addWidget(scan_intro)
        scan_header.addLayout(scan_text, 1)
        self._run_btn = QPushButton("Scan course")
        self._run_btn.setFixedHeight(42)
        self._run_btn.setMinimumWidth(130)
        self._run_btn.clicked.connect(self._start_run)
        scan_header.addWidget(self._run_btn, 0, Qt.AlignmentFlag.AlignTop)
        scan_layout.addLayout(scan_header)
        results_heading = QLabel("Scan results")
        results_heading.setProperty("role", "field-label")
        scan_layout.addWidget(results_heading)
        self._summary = QLabel("No scan yet. Start here to see what needs attention.")
        self._summary.setObjectName("checker_scan_summary")
        self._summary.setWordWrap(True)
        self._summary.setProperty("role", "dim")
        scan_layout.addWidget(self._summary)
        self._empty_state = QWidget()
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setContentsMargins(12, 8, 12, 8)
        empty_layout.setSpacing(8)
        empty_layout.addStretch()
        self._empty_icon = QLabel()
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self._empty_icon)
        self._empty_title = QLabel("Ready to compare")
        self._empty_title.setProperty("role", "section-heading")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self._empty_title)
        self._empty_hint = QLabel("Your findings will appear here after a scan.")
        self._empty_hint.setWordWrap(True)
        self._empty_hint.setProperty("role", "dim")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self._empty_hint)
        empty_layout.addStretch()
        scan_layout.addWidget(self._empty_state, 1)
        self._findings = QListWidget()
        self._findings.setObjectName("checker_findings")
        self._findings.setMinimumHeight(135)
        self._findings.setMaximumHeight(260)
        self._findings.setWordWrap(True)
        self._findings.hide()
        scan_layout.addWidget(self._findings, 1)
        self._review_btn = QPushButton("Choose fixes →")
        self._review_btn.setProperty("variant", "secondary")
        self._review_btn.setEnabled(False)
        self._review_btn.clicked.connect(lambda: self._tabs.setCurrentIndex(1))
        review_row = QHBoxLayout()
        review_row.addStretch()
        self._review_btn.setMinimumHeight(40)
        review_row.addWidget(self._review_btn)
        scan_layout.addLayout(review_row)
        self._tabs.addTab(scan_page, "1 · Scan")

        # Scrolls so the fix list stays readable at any window height and can
        # take more fixes later without squeezing the cards.
        transfer_scroll = QScrollArea()
        transfer_scroll.setWidgetResizable(True)
        transfer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        transfer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        transfer_page = QFrame()
        transfer_page.setProperty("role", "surface")
        transfer_page.setObjectName("checker_fix_page")
        transfer_scroll.setWidget(transfer_page)
        transfer_layout = QVBoxLayout(transfer_page)
        transfer_layout.setContentsMargins(20, 18, 20, 18)
        transfer_layout.setSpacing(8)
        fix_heading = QLabel("Choose what to fix")
        fix_heading.setProperty("role", "section-heading")
        transfer_layout.addWidget(fix_heading)
        transfer_intro = QLabel("Tick the fixes to apply. You will see a summary and confirm before anything changes.")
        transfer_intro.setWordWrap(True)
        transfer_intro.setProperty("role", "dim")
        transfer_layout.addWidget(transfer_intro)
        self._fix_boxes = {}
        self._fix_labels = {}
        for key, title, description in self.FIXES:
            row = QFrame()
            row.setProperty("role", "checker-fix-row")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 10, 0, 12)
            row_layout.setSpacing(5)
            box = QCheckBox(title)
            box.setObjectName(f"fix_{key}")
            box.toggled.connect(self._update_apply_enabled)
            detail = QLabel(description)
            detail.setWordWrap(True)
            detail.setProperty("role", "dim")
            row_layout.addWidget(box)
            detail_row = QHBoxLayout()
            detail_row.setContentsMargins(28, 0, 0, 0)
            detail_row.addWidget(detail)
            row_layout.addLayout(detail_row)
            transfer_layout.addWidget(row)
            self._fix_boxes[key] = box
            self._fix_labels[key] = detail
        other = QLabel("H5P and Kaltura have their own tools under Media:")
        other.setProperty("role", "dim")
        transfer_layout.addWidget(other)
        tool_row = QHBoxLayout()
        for label, tool in (("Open H5P tool", "h5p"), ("Open Kaltura tool", "kaltura")):
            button = QPushButton(label)
            button.setProperty("variant", "secondary")
            button.clicked.connect(lambda _checked=False, name=tool: self.open_tool.emit(name))
            tool_row.addWidget(button)
        transfer_layout.addLayout(tool_row)
        transfer_layout.addStretch()
        # The apply button stays pinned below the list so it never scrolls
        # out of sight.
        fix_tab = QWidget()
        fix_tab_layout = QVBoxLayout(fix_tab)
        fix_tab_layout.setSpacing(8)
        fix_tab_layout.setContentsMargins(0, 0, 0, 0)
        fix_tab_layout.addWidget(transfer_scroll, 1)
        self._apply_btn = QPushButton("Preview selected changes")
        self._apply_btn.setFixedHeight(42)
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._preview_selected)
        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(4, 6, 4, 0)
        self._selection_summary = QLabel("Select fixes to preview.")
        self._selection_summary.setProperty("role", "dim")
        self._selection_summary.setWordWrap(True)
        apply_row.addWidget(self._selection_summary, 1)
        apply_row.addWidget(self._apply_btn)
        fix_tab_layout.addLayout(apply_row)
        self._tabs.addTab(fix_tab, "2 · Fix")
        self._tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self._tabs)
        layout.addSpacing(8)

        # Pause-point buttons (hidden until needed)
        self._moodle_hint = QLabel(
            "A browser window has opened your Moodle course. "
            "Verify you are logged in and the correct course is shown, then click below."
        )
        self._moodle_hint.setProperty("role", "dim")
        self._moodle_hint.setWordWrap(True)
        self._moodle_hint.hide()
        layout.addWidget(self._moodle_hint)
        layout.addSpacing(4)

        self._ready_btn = QPushButton("Ready — Scrape Now")
        self._ready_btn.setProperty("variant", "success")
        self._ready_btn.setFixedHeight(38)
        self._ready_btn.setToolTip("Click once you can see your Moodle course in the browser and you are logged in.")
        self._ready_btn.hide()
        layout.addWidget(self._ready_btn)

        self._h5p_hint = QLabel(
            "The tool needs Teacher-level access to download H5P files. "
            "Switch your Moodle role to Teacher in the browser, then click Ready — or click Skip if H5P is not needed."
        )
        self._h5p_hint.setProperty("role", "dim")
        self._h5p_hint.setWordWrap(True)
        self._h5p_hint.hide()
        layout.addWidget(self._h5p_hint)
        layout.addSpacing(4)

        h5p_row = QHBoxLayout(); h5p_row.setSpacing(8)
        self._h5p_ready_btn = QPushButton("Ready — Download H5P")
        self._h5p_ready_btn.setProperty("variant", "success")
        self._h5p_ready_btn.setFixedHeight(38)
        self._h5p_ready_btn.setToolTip("Click after switching your Moodle role to Teacher. The tool will then download H5P packages.")
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn = QPushButton("Skip H5P")
        self._h5p_skip_btn.setProperty("variant", "secondary")
        self._h5p_skip_btn.setFixedWidth(120)
        self._h5p_skip_btn.setFixedHeight(38)
        self._h5p_skip_btn.setToolTip("Skip H5P download — these activities will need to be embedded manually later.")
        self._h5p_skip_btn.hide()
        h5p_row.addWidget(self._h5p_ready_btn, 1)
        h5p_row.addWidget(self._h5p_skip_btn)
        layout.addLayout(h5p_row)
        layout.addSpacing(8)

        self._stop_btn = QPushButton("Stop current task")
        self._stop_btn.setProperty("variant", "secondary")
        self._stop_btn.clicked.connect(self._stop_run)
        self._stop_btn.hide()
        layout.addWidget(self._stop_btn)
        self._status_line = QLabel("")
        self._status_line.setProperty("role", "dim")
        self._status_line.setWordWrap(True)
        self._status_line.hide()
        layout.addWidget(self._status_line)
        self._details_btn = QToolButton()
        self._details_btn.setObjectName("checker_details_toggle")
        self._details_btn.setProperty("variant", "ghost")
        self._details_btn.setText("Show activity details")
        self._details_btn.setCheckable(True)
        self._details_btn.toggled.connect(self._toggle_details)
        layout.addWidget(self._details_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._log = LogWidget()
        self._log.setFixedHeight(210)
        self._log.hide()
        layout.addWidget(self._log)
        layout.addSpacing(8)

        # Downloads path (hidden until run completes)
        self._dl_label = QLabel()
        self._dl_label.setStyleSheet("font-family:Consolas,monospace;font-size:11px;")
        self._dl_label.setProperty("role", "dim")
        self._dl_label.hide()
        layout.addWidget(self._dl_label)

        # Continue button (hidden until success)
        self._continue_btn = QPushButton("Continue to Unit Collector")
        self._continue_btn.setProperty("variant", "next-step")
        self._continue_btn.setFixedHeight(38)
        self._continue_btn.setToolTip("Proceed to Step 2: scrape and combine unit topic pages.")
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self.continue_next)
        layout.addWidget(self._continue_btn)

        # Load saved URLs
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("chk_bs_url"):
            self._bs_entry.setText(cfg["chk_bs_url"])
        if cfg.get("chk_moodle_url"):
            self._moodle_entry.setText(cfg["chk_moodle_url"])
        self._bs_entry.textChanged.connect(self._invalidate_scan)
        self._moodle_entry.textChanged.connect(self._invalidate_scan)
        layout.addStretch(1)

    def _reflow_courses(self):
        compact = self.width() < 760
        if compact == self._compact_courses:
            return
        self._compact_courses = compact
        for field in self._course_fields:
            self._course_grid.removeWidget(field)
        for index, field in enumerate(self._course_fields):
            self._course_grid.addWidget(field, index if compact else 0, 0 if compact else index)
        self._course_grid.setColumnStretch(0, 1)
        self._course_grid.setColumnStretch(1, 0 if compact else 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_courses()
        self._size_workspace()

    def _size_workspace(self):
        # QTabWidget otherwise uses the taller, hidden Fix page's size hint
        # for Scan too. Bound the workspace; long lists scroll inside it.
        self._tabs.setFixedHeight(430 if self._compact_courses or self._tabs.currentIndex() == 1 else 380)

    def _tab_changed(self, index):
        self._size_workspace()
        if index == 1:
            QTimer.singleShot(0, lambda: self._ensure_visible(self._apply_btn))

    def _ensure_visible(self, widget):
        # ensureWidgetVisible uses a text edit's cursor rectangle. Use the
        # widget bounds so opening activity details reveals the whole log.
        position = widget.mapTo(self._scroll.widget(), QPoint(0, 0))
        self._scroll.ensureVisible(
            0, position.y() + widget.height() // 2, 0, widget.height() // 2 + 16
        )

    def refresh_theme(self):
        colors = gui_styles.modern_colors()
        self._empty_icon.setPixmap(make_pixmap("checker", colors["muted"], 36))
        self._log.set_log_colors({"info": colors["text"], "success": colors["success"],
                                  "warning": colors["warning"], "error": colors["error"],
                                  "dim": colors["muted"], "detail": colors["muted"],
                                  "step": colors["focus"]})

    def show_review(self):
        self._tabs.setCurrentIndex(0)

    def show_transfer(self):
        self._tabs.setCurrentIndex(1)

    def _toggle_details(self, shown: bool):
        self._log.setVisible(shown)
        self._details_btn.setText("Hide activity details" if shown else "Show activity details")
        if shown:
            QTimer.singleShot(0, lambda: self._ensure_visible(self._log))

    def _invalidate_scan(self, *_args):
        self._scan_report = None
        self._scan_urls = None
        self._summary.setText("Course URLs changed. Scan again before applying fixes.")
        self._findings.clear()
        self._findings.hide()
        self._empty_state.show()
        self._empty_title.setText("Ready to compare")
        self._empty_hint.setText("Your findings will appear here after a scan.")
        self._review_btn.setEnabled(False)
        self._update_apply_enabled()

    def _update_apply_enabled(self, *_args):
        count = sum(box.isChecked() for box in self._fix_boxes.values())
        self._selection_summary.setText(
            f"{count} {'fix' if count == 1 else 'fixes'} selected" if count else "Select fixes to preview."
        )
        self._apply_btn.setEnabled(
            self._scan_report is not None
            and any(box.isChecked() for box in self._fix_boxes.values())
            and not self._task_active
        )

    def _show_scan_report(self, report: dict, scanned_urls: tuple[str, str]):
        if scanned_urls != (self._bs_entry.text().strip(), self._moodle_entry.text().strip()):
            self._invalidate_scan()
            return
        self._scan_report = report
        self._scan_urls = scanned_urls
        self._summary.setText(
            f"Scan finished: {report['missing']} missing item(s), "
            f"{report['broken_links']} broken file link(s), "
            f"{report['books']} Moodle Book(s), "
            f"{report['h5p']} H5P item(s)."
        )
        self._findings.clear()
        for item in report.get("findings", []):
            label = "Missing" if item["status"] == "missing" else "Review"
            section = f" · {item['section']}" if item["section"] else ""
            self._findings.addItem(f"{label}: {item['name']} ({item['type']}){section}")
        for title in report.get("broken_pages", []):
            self._findings.addItem(f"Broken file link: {title}")
        self._findings.setVisible(self._findings.count() > 0)
        self._empty_state.setVisible(self._findings.count() == 0)
        self._empty_title.setText("Scan complete")
        self._empty_hint.setText("Review the summary, then choose any fixes you need.")
        self._review_btn.setEnabled(True)
        counts = {
            "repair_files": report["broken_links"], "files": report["files"],
            "activities": report["activities"], "books": report["books"],
        }
        for key, title, _description in self.FIXES:
            count = counts.get(key)
            box = self._fix_boxes[key]
            if count is not None:
                box.setText(f"{title} ({count})")
                box.setEnabled(count > 0)
                if count == 0:
                    box.setChecked(False)
            else:
                box.setText(title)
                box.setEnabled(True)
        self._update_apply_enabled()

    def _preview_selected(self):
        if self._scan_report is None or self._scan_urls != (
            self._bs_entry.text().strip(), self._moodle_entry.text().strip()
        ):
            self._invalidate_scan()
            return
        selected = {key: box.isChecked() for key, box in self._fix_boxes.items()}
        names = [title for key, title, _desc in self.FIXES if selected[key]]
        if not names:
            return
        preview = "\n".join(f"• {name}" for name in names)
        answer = QMessageBox.question(
            self, "Preview selected changes",
            "The app will scan again, then attempt these changes:\n\n"
            + preview + "\n\nYou will review individual files or uncertain matches before they are changed."
            + "\n\nApply the selected changes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.show_review()
        if selected == {key: key == "books" for key in selected}:
            self._start_books_only()
        elif selected == {key: key == "order" for key in selected}:
            self._start_order_only()
        else:
            self._run_worker(full_run=True, operations=selected)

    def save_state(self):
        if not hasattr(self._mw, "save_config"):
            return
        self._mw.save_config({
            "chk_bs_url": self._bs_entry.text().strip(),
            "chk_moodle_url": self._moodle_entry.text().strip(),
        })

    def _run_worker(self, phase_b: bool = False, full_run: bool = False,
                    order_only: bool = False, books_only: bool = False,
                    scan_only: bool = False, operations: dict | None = None):
        if self._task_active:
            return
        bs_url     = self._bs_entry.text().strip()
        moodle_url = self._moodle_entry.text().strip()
        if not bs_url and not moodle_url:
            self._log.append_log("Paste at least one URL.", "warning")
            return
        if phase_b and not bs_url:
            self._log.append_log("Paste a Brightspace URL first.", "warning")
            return
        if (order_only or books_only) and (not bs_url or not moodle_url):
            self._log.append_log("Paste both course URLs first.", "warning")
            return
        if (scan_only or operations is not None) and (not bs_url or not moodle_url):
            self._summary.setText("Paste both course URLs before scanning or applying fixes.")
            return

        self.save_state()

        import threading as _t
        moodle_ev = _t.Event(); h5p_ev = _t.Event(); file_ev = _t.Event()
        file_result = []
        skip_flag   = [False]
        grade_all = False  # Gradebook choice belongs to the dedicated H5P tool.
        self._moodle_ready_event   = moodle_ev
        self._h5p_ready_event      = h5p_ev
        self._file_checklist_event = file_ev
        self._h5p_skip_flag        = skip_flag

        self._ready_btn.hide()
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()
        self._continue_btn.hide()
        self._dl_label.hide()

        self._stop_flag[0] = False
        self._task_active = True
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._stop_btn.show()
        self._status_line.setText("Scanning courses…" if scan_only else "Applying selected changes…")
        self._status_line.show()
        QTimer.singleShot(0, lambda: self._ensure_visible(self._stop_btn))
        self._apply_btn.setEnabled(False)
        self._log.clear_log()

        q = self._log_queue

        def confirm(msg: str) -> bool:
            # Called from a worker thread — QTimer.singleShot never fires there
            # (no Qt event loop), so route through the log queue like the
            # file-checklist dialog and let _poll_log show it on the GUI thread.
            result = [False]; ev = _t.Event()
            q.put(("__CHK_CONFIRM__", (msg, result, ev)))
            while not ev.wait(0.1):
                if self._stop_flag[0]:
                    return False
            return result[0]

        def notify(title: str, text: str) -> None:
            # Fire-and-forget popup; worker does not wait for an answer.
            q.put(("__CHK_NOTIFY__", (title, text)))

        def grade_recovery(item_name: str, should_grade: bool) -> str:
            result = ["skip"]
            event = _t.Event()
            q.put(("__CHK_H5P_GRADE_RECOVERY__", (item_name, result, event)))
            while not event.wait(0.1):
                if self._stop_flag[0]:
                    return "stop"
            return result[0]

        def h5p_recovery(failures: list[dict]) -> list[dict]:
            result = []
            event = _t.Event()
            q.put(("__CHK_H5P_FILE_RECOVERY__", (failures, result, event)))
            while not event.wait(0.1):
                if self._stop_flag[0]:
                    return []
            return result

        def worker():
            done_sent = [False]
            owned_task = [None]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                from content_checker import ContentChecker
                checker = ContentChecker(
                    bs_url=bs_url,
                    moodle_url=moodle_url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    moodle_ready_event=moodle_ev,
                    on_moodle_waiting=lambda: q.put(("__CHK_MOODLE_WAITING__", "")),
                    h5p_ready_event=h5p_ev,
                    on_h5p_waiting=lambda: q.put(("__CHK_H5P_WAITING__", skip_flag)),
                    h5p_recovery=h5p_recovery,
                    h5p_grade_all=grade_all,
                    h5p_grade_recovery=grade_recovery,
                    file_checklist_event=file_ev,
                    on_file_checklist=lambda d: q.put(("__CHK_FILE_CHECKLIST__", (d, file_result, file_ev))),
                    confirm_fn=confirm,
                    notify_fn=notify,
                    bs_username=self._mw.bs_username,
                    bs_password=self._mw.bs_password,
                    sso_email=self._mw.sso_email,
                    sso_password=self._mw.sso_password,
                    moodle_username=self._mw.moodle_username,
                    moodle_password=self._mw.moodle_password,
                    full_run=full_run,
                    scan_only=scan_only,
                    operations=operations,
                    on_report=(lambda report: q.put(("__CHK_REPORT__", (report, (bs_url, moodle_url))))
                               if scan_only else None),
                )
                checker.do_relink     = False
                checker.do_pdf_upload = full_run and bool((operations or {}).get("files"))
                checker.do_h5p_embed  = False
                checker.file_checklist_result = file_result
                checker.h5p_skip_flag = skip_flag
                checker.stop_flag = self._stop_flag
                checker.order_only = order_only
                checker.books_only = books_only
                checker.keep_browser_open = False
                if phase_b:
                    checker.do_relink = False
                    checker.do_h5p_embed = True
                    checker.h5p_phase_b_only = True
                async def run_checker():
                    self._worker_loop = asyncio.get_running_loop()
                    self._worker_task = asyncio.current_task()
                    owned_task[0] = self._worker_task
                    if self._stop_flag[0]:
                        raise asyncio.CancelledError()
                    await checker.run()

                asyncio.run(run_checker())
            except asyncio.CancelledError:
                q.put(("⏹ Checker stopped by user.", "warning"))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
            finally:
                if self._worker_task is owned_task[0]:
                    self._worker_task = None
                    self._worker_loop = None
                on_done()

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(scan_only=True)

    def _start_full_run(self):
        """Legacy entry point now uses the visible selected-fix preview."""
        self._preview_selected()

    def _start_phase_b(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(phase_b=True, full_run=False)

    def _start_order_only(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(order_only=True)

    def _start_books_only(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(books_only=True)

    def _stop_run(self):
        """Cancel the active browser task and release any user prompt waits."""
        if self._stop_flag[0]:
            return
        self._stop_flag[0] = True
        self._stop_btn.setEnabled(False)
        self._log.append_log("Stopping Checker…", "warning")
        for event in (self._moodle_ready_event, self._h5p_ready_event,
                      self._file_checklist_event):
            if event:
                event.set()
        for dialog in list(self._active_dialogs):
            dialog.close()
        loop, task = self._worker_loop, self._worker_task
        if loop and task and loop.is_running():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The worker finished while Stop was being clicked.

    def _show_prompt(self, dialog, on_finished=None):
        """Keep Checker controls responsive while a user prompt is open."""
        self._active_dialogs.append(dialog)

        def finished(_result):
            if dialog in self._active_dialogs:
                self._active_dialogs.remove(dialog)
            try:
                if on_finished:
                    on_finished()
            finally:
                dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.setModal(False)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _close_prompt(self, title):
        for dialog in list(self._active_dialogs):
            if dialog.windowTitle() == title:
                dialog.close()

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if self._stop_flag[0] and msg.startswith("__CHK_"):
                    if msg in ("__CHK_CONFIRM__", "__CHK_H5P_GRADE_RECOVERY__",
                               "__CHK_FILE_CHECKLIST__", "__CHK_H5P_FILE_RECOVERY__"):
                        tag[-1].set()
                    continue
                if msg == "__DONE__":
                    for dialog in list(self._active_dialogs):
                        dialog.close()
                    self._stop_flag[0] = False
                    self._task_active = False
                    self._run_btn.setEnabled(True)
                    self._stop_btn.hide()
                    self._status_line.hide()
                    self._update_apply_enabled()
                    self._moodle_hint.hide()
                    self._ready_btn.hide()
                    self._h5p_hint.hide()
                    self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
                    dl = Path(__file__).parent.parent.parent / "downloads"
                    self._dl_label.setText(f"Downloads: {dl}")
                    self._dl_label.show()
                elif msg == "__CHK_REPORT__":
                    report, scanned_urls = tag
                    self._show_scan_report(report, scanned_urls)
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                elif msg == "__CHK_MOODLE_WAITING__":
                    self._ready_btn.setText("Ready — Scrape Now")
                    if self._moodle_ready_bound:
                        self._ready_btn.clicked.disconnect(self._moodle_ready)
                    self._ready_btn.clicked.connect(self._moodle_ready)
                    self._moodle_ready_bound = True
                    self._moodle_hint.show()
                    self._ready_btn.show()
                    # Front-most popup too — in-app buttons are easy to miss
                    from PySide6.QtWidgets import QMessageBox
                    dlg = QMessageBox(self)
                    dlg.setWindowTitle("Action needed — Moodle")
                    dlg.setText(
                        "A browser window has opened your Moodle course.\n\n"
                        "Verify you are logged in and the correct course is "
                        "shown, then click Ready."
                    )
                    ready = dlg.addButton("Ready — Scrape Now", QMessageBox.ButtonRole.AcceptRole)
                    dlg.addButton("I'll use the app buttons", QMessageBox.ButtonRole.RejectRole)
                    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                    self._show_prompt(dlg, lambda: self._moodle_ready()
                                      if not self._stop_flag[0] and dlg.clickedButton() is ready else None)
                elif msg == "__CHK_H5P_WAITING__":
                    self._h5p_hint.show()
                    self._h5p_ready_btn.show(); self._h5p_skip_btn.show()
                    if self._h5p_ready_bound:
                        self._h5p_ready_btn.clicked.disconnect(self._h5p_ready)
                    if self._h5p_skip_bound:
                        self._h5p_skip_btn.clicked.disconnect(self._h5p_skip)
                    self._h5p_ready_btn.clicked.connect(self._h5p_ready)
                    self._h5p_skip_btn.clicked.connect(self._h5p_skip)
                    self._h5p_ready_bound = True
                    self._h5p_skip_bound = True
                    from PySide6.QtWidgets import QMessageBox
                    dlg = QMessageBox(self)
                    dlg.setWindowTitle("Action needed — H5P download")
                    dlg.setText(
                        "The tool needs Teacher-level access to download H5P "
                        "files.\n\nSwitch your Moodle role to Teacher in the "
                        "browser, then click Ready — or Skip if H5P is not needed."
                    )
                    ready = dlg.addButton("Ready — Download H5P", QMessageBox.ButtonRole.AcceptRole)
                    skip  = dlg.addButton("Skip H5P", QMessageBox.ButtonRole.DestructiveRole)
                    dlg.addButton("I'll use the app buttons", QMessageBox.ButtonRole.RejectRole)
                    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                    def h5p_choice():
                        if not self._stop_flag[0]:
                            if dlg.clickedButton() is ready:
                                self._h5p_ready()
                            elif dlg.clickedButton() is skip:
                                self._h5p_skip()
                    self._show_prompt(dlg, h5p_choice)
                elif msg == "__CHK_NOTIFY__":
                    title, text = tag
                    from PySide6.QtWidgets import QMessageBox
                    dlg = QMessageBox(self)
                    dlg.setWindowTitle(title)
                    dlg.setText(text)
                    dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
                    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                    self._show_prompt(dlg)
                elif msg == "__CHK_CONFIRM__":
                    conf_msg, result_ref, event = tag
                    from PySide6.QtWidgets import QMessageBox
                    dlg = QMessageBox(self)
                    dlg.setWindowTitle("Continue?")
                    dlg.setText(conf_msg)
                    dlg.setStandardButtons(
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    dlg.setDefaultButton(QMessageBox.StandardButton.No)
                    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                    def confirm_choice():
                        result_ref[0] = (
                            not self._stop_flag[0]
                            and dlg.clickedButton() is dlg.button(QMessageBox.StandardButton.Yes)
                        )
                        event.set()
                    self._show_prompt(dlg, confirm_choice)
                elif msg == "__CHK_FILE_CHECKLIST__":
                    data_json, result_list, event = tag
                    from gui_dialogs import FileChecklistDialog
                    dlg = FileChecklistDialog(data_json, result_list, event, self)
                    self._show_prompt(dlg)
                elif msg == "__CHK_H5P_GRADE_RECOVERY__":
                    item_name, result_ref, event = tag
                    from PySide6.QtWidgets import QMessageBox
                    dlg = QMessageBox(self)
                    dlg.setWindowTitle("H5P gradebook action needed")
                    dlg.setText(
                        f"The app could not choose Add Grade Item for:\n\n{item_name}\n\n"
                        "You can fix the open Brightspace dialog manually, retry, skip this item, or stop."
                    )
                    fixed = dlg.addButton("I fixed it — Continue", QMessageBox.ButtonRole.AcceptRole)
                    retry = dlg.addButton("Retry Automatically", QMessageBox.ButtonRole.ActionRole)
                    skip = dlg.addButton("Skip This Item", QMessageBox.ButtonRole.DestructiveRole)
                    stop = dlg.addButton("Stop Run", QMessageBox.ButtonRole.RejectRole)
                    def grade_choice():
                        clicked = dlg.clickedButton()
                        result_ref[0] = ("stop" if self._stop_flag[0] or clicked is stop
                                         else "continue" if clicked is fixed else "retry" if clicked is retry
                                         else "skip")
                        event.set()
                        if result_ref[0] == "stop":
                            self._stop_run()
                    self._show_prompt(dlg, grade_choice)
                elif msg == "__CHK_H5P_FILE_RECOVERY__":
                    failures, result_ref, event = tag
                    from gui_dialogs import H5PRecoveryDialog
                    self._show_prompt(H5PRecoveryDialog(failures, result_ref, event, self))
                else:
                    self._log.append_log(msg, tag)
                    if tag in {"step", "warning", "error", "success"}:
                        self._status_line.setText(msg)
        except queue.Empty:
            pass

    def _moodle_ready(self):
        self._close_prompt("Action needed — Moodle")
        self._moodle_hint.hide()
        self._ready_btn.hide()
        if self._moodle_ready_event:
            self._moodle_ready_event.set()

    def _h5p_ready(self):
        self._close_prompt("Action needed — H5P download")
        self._h5p_hint.hide()
        self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
        if self._h5p_ready_event:
            self._h5p_ready_event.set()

    def _h5p_skip(self):
        self._close_prompt("Action needed — H5P download")
        self._h5p_hint.hide()
        self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
        self._h5p_skip_flag[0] = True
        if self._h5p_ready_event:
            self._h5p_ready_event.set()
