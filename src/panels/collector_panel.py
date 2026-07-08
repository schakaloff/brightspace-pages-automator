import asyncio
import queue
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QSpinBox,
)
from PySide6.QtCore import Signal, QTimer

from gui_log import LogWidget
from panels._shared import _divider, _form_label, _section_header, PAGE_THEMES, _build_theme_swatches


class CollectorPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._swatch_frames: dict = {}
        self._selected_theme: list = ["lake"]
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Unit Collector"))
        sub = QLabel("Scrapes all topic pages from a unit and combines them into one collapsible HTML file.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("PAGE THEME"))
        layout.addSpacing(6)
        self._swatch_frames, self._selected_theme = _build_theme_swatches(layout)
        layout.addSpacing(14)

        layout.addWidget(_form_label("BRIGHTSPACE UNIT URL"))
        layout.addSpacing(4)
        self._unit_entry = QLineEdit()
        self._unit_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/…/lessons/…")
        self._unit_entry.setFixedHeight(40)
        layout.addWidget(self._unit_entry)
        layout.addSpacing(12)

        layout.addWidget(_form_label("TARGET PAGE URL  (empty Brightspace page you created)"))
        layout.addSpacing(4)
        self._target_entry = QLineEdit()
        self._target_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/…/topics/…/View")
        self._target_entry.setFixedHeight(40)
        layout.addWidget(self._target_entry)
        layout.addSpacing(12)

        layout.addWidget(_form_label("MOODLE COURSE URL  (optional — fixes weird file/link names)"))
        layout.addSpacing(4)
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setFixedHeight(40)
        layout.addWidget(self._moodle_entry)
        layout.addSpacing(12)

        par_row = QHBoxLayout()
        par_row.addWidget(_form_label("PARALLEL PAGES"))
        self._parallel_spin = QSpinBox()
        self._parallel_spin.setRange(1, 10)
        self._parallel_spin.setValue(3)
        self._parallel_spin.setFixedWidth(60)
        par_row.addWidget(self._parallel_spin)
        par_row.addStretch()
        layout.addLayout(par_row)
        layout.addSpacing(14)

        self._run_btn = QPushButton("Collect & Assemble")
        self._run_btn.setFixedHeight(42)
        self._run_btn.clicked.connect(self._start_run)
        layout.addWidget(self._run_btn)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)
        layout.addSpacing(8)

        self._continue_btn = QPushButton("Continue to Page Changer")
        self._continue_btn.setProperty("variant", "next-step")
        self._continue_btn.setFixedHeight(38)
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self.continue_next)
        layout.addWidget(self._continue_btn)

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        unit_url   = self._unit_entry.text().strip()
        target_url = self._target_entry.text().strip()
        moodle_url = self._moodle_entry.text().strip()
        if not unit_url:
            self._log.append_log("Paste a Brightspace unit URL first.", "warning"); return
        if not target_url:
            self._log.append_log("Paste the target page URL first.", "warning"); return

        theme_name   = self._selected_theme[0]
        theme_colors = PAGE_THEMES[theme_name]
        parallel     = self._parallel_spin.value()

        style_ref_path = Path(__file__).parent.parent.parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._continue_btn.hide()
        self._log.clear_log()

        q = self._log_queue

        def worker():
            done_sent = [False]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                from unit_collector import run as collector_run
                asyncio.run(collector_run(
                    unit_url=unit_url,
                    target_url=target_url,
                    theme_name=theme_name,
                    theme_colors=theme_colors,
                    claude_api_key=self._mw.claude_api_key,
                    claude_model=self._mw.claude_model,
                    style_reference_html=style_reference_html,
                    parallel_pages=parallel,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    bs_username=self._mw.bs_username,
                    bs_password=self._mw.bs_password,
                    sso_email=self._mw.sso_email,
                    sso_password=self._mw.sso_password,
                    moodle_url=moodle_url,
                    moodle_username=self._mw.moodle_username,
                    moodle_password=self._mw.moodle_password,
                ))
            except Exception as e:
                q.put((f"Error: {e}", "error"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Collect & Assemble")
                    self._run_btn.setEnabled(True)
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass
