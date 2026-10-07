import asyncio
import queue
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QCheckBox, QProgressBar,
)
from PySide6.QtCore import Signal, QTimer

from gui_log import LogWidget
from panels._shared import (
    _divider, _form_label, _section_header, PAGE_THEMES, _build_theme_swatches, friendly_error,
    NoScrollComboBox,
)
from style_presets import STYLE_PRESETS, load_style_reference


class RestylePanel(QWidget):
    step_success = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._response_queue: queue.Queue = queue.Queue()
        self._swatch_frames: dict = {}
        self._selected_theme: list = ["lake"]
        self._busy = False
        self._stop_event = threading.Event()
        self._results = {}
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Restyle"))
        sub = QLabel("Choose a theme and design, then restyle one page or pick specific pages from a section.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("PAGE THEME"))
        layout.addSpacing(6)
        self._swatch_frames, self._selected_theme = _build_theme_swatches(layout)
        layout.addSpacing(14)

        layout.addWidget(_form_label("PAGE DESIGN"))
        layout.addSpacing(6)
        self._style_preset = NoScrollComboBox()
        for key, label in STYLE_PRESETS.items():
            self._style_preset.addItem(label, key)
        self._style_preset.setToolTip(
            "Calm resources uses clear file rows and prominent actions. "
            "Classic cards restores the previous card-and-gradient design."
        )
        layout.addWidget(self._style_preset)
        layout.addSpacing(14)

        layout.addWidget(_form_label("BRIGHTSPACE PAGE OR SECTION URL"))
        layout.addSpacing(4)

        url_row = QHBoxLayout(); url_row.setSpacing(8)
        self._url_entry = QLineEdit()
        self._url_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/home/…")
        self._url_entry.setFixedHeight(42)
        self._url_entry.setToolTip(
            "Paste a single Brightspace page URL, or a section URL to restyle multiple pages at once.\n"
            "If a section URL is used, a dialog will let you select which pages to include."
        )
        url_row.addWidget(self._url_entry, 1)

        self._run_btn = QPushButton("Start restyle")
        self._run_btn.setFixedSize(110, 42)
        self._run_btn.setToolTip(
            "Opens a browser, extracts the page HTML, sends it to Claude AI for restyling,\n"
            "and writes the styled HTML back to Brightspace."
        )
        self._run_btn.clicked.connect(self._start_run)
        url_row.addWidget(self._run_btn)
        layout.addLayout(url_row)
        self._stop_btn = QPushButton("Stop after active pages")
        self._stop_btn.setProperty("variant", "secondary")
        self._stop_btn.clicked.connect(self._stop)
        self._stop_btn.hide()
        layout.addWidget(self._stop_btn)

        url_hint = QLabel("Paste a section URL to restyle multiple pages at once — you'll pick which ones to include.")
        url_hint.setProperty("role", "dim")
        url_hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(url_hint)
        layout.addSpacing(12)

        self._move_unit_content_chk = QCheckBox("Move unit content into an Overview page")
        self._move_unit_content_chk.setChecked(False)
        self._move_unit_content_chk.setToolTip(
            "Add the unit description to the checklist as a separate item. "
            "It is moved only if you check it when choosing pages."
        )
        layout.addWidget(self._move_unit_content_chk)
        layout.addSpacing(12)
        self._status = QLabel("Ready. Section URLs open a page checklist before any changes.")
        self._status.setWordWrap(True)
        self._status.setProperty("role", "dim")
        layout.addWidget(self._status)
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.hide()
        layout.addWidget(self._progress)
        layout.addSpacing(12)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)

        # Load saved URL
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("automator_url"):
            self._url_entry.setText(cfg["automator_url"])
        self._move_unit_content_chk.setChecked(cfg.get("restyle_move_unit_content", False))
        preset = cfg.get("restyle_style_preset", "calm")
        index = self._style_preset.findData(preset)
        self._style_preset.setCurrentIndex(max(index, 0))

    def save_state(self):
        if not hasattr(self._mw, "save_config"):
            return
        self._mw.save_config({
            "automator_url": self._url_entry.text().strip(),
            "restyle_move_unit_content": self._move_unit_content_chk.isChecked(),
            "restyle_style_preset": self._style_preset.currentData(),
        })

    def _start_run(self):
        if self._busy:
            return
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning"); return
        url = self._url_entry.text().strip()
        if not url:
            self._log.append_log("Paste a Brightspace URL first.", "warning"); return

        try:
            style_reference_html = load_style_reference(self._style_preset.currentData())
        except OSError:
            self._log.append_log(
                "The selected page design could not be loaded. Restore the app's templates or reinstall it, then try again.",
                "error",
            )
            return
        # Capture all inputs on the GUI thread before starting the worker.
        options = dict(
            claude_api_key=self._mw.claude_api_key, claude_model=self._mw.claude_model,
            style_reference_html=style_reference_html, theme_name=self._selected_theme[0],
            bs_username=self._mw.bs_username, bs_password=self._mw.bs_password,
            sso_email=self._mw.sso_email, sso_password=self._mw.sso_password,
            move_unit_content=self._move_unit_content_chk.isChecked(),
        )
        self._busy = True
        self._stop_event.clear()
        self._results = {}
        self._set_inputs_enabled(False)
        self._stop_btn.setEnabled(True)
        self._stop_btn.show()
        self._progress.hide()
        self._status.setText("Opening Brightspace and finding pages…")

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._log.clear_log()

        q  = self._log_queue
        rq = self._response_queue
        while not rq.empty():
            rq.get_nowait()

        def on_pages_found(pages):
            q.put(("__PAGES__", pages))
            while not self._stop_event.is_set():
                try:
                    return rq.get(timeout=0.2)
                except queue.Empty:
                    pass
            return []

        def worker():
            done_sent = [False]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                import sys as _sys
                _sys.modules.pop("automator", None)
                from automator import run as automator_run
                asyncio.run(automator_run(
                    url=url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    **options,
                    on_pages_found=on_pages_found,
                    stop_event=self._stop_event,
                    on_page_result=lambda index, page, state: q.put(("__RESULT__", (page, state))),
                ))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    def _set_inputs_enabled(self, enabled):
        for control in (self._url_entry, self._style_preset, self._move_unit_content_chk,
                        *self._swatch_frames.values()):
            control.setEnabled(enabled)

    def _stop(self):
        self._stop_event.set()
        self._stop_btn.setEnabled(False)
        self._status.setText("Stopping. Active pages will finish; queued pages will be skipped.")

    def _update_results(self):
        states = list(self._results.values())
        completed = sum(state != "pending" for state in states)
        self._progress.setRange(0, max(len(states), 1))
        self._progress.setValue(completed)
        self._progress.show()
        self._status.setText(
            f"{completed} of {len(states)} finished · "
            f"{states.count('changed')} saved · {states.count('failed')} failed · "
            f"{states.count('skipped')} skipped"
        )

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._busy = False
                    self._run_btn.setText("Start restyle"); self._run_btn.setEnabled(True)
                    self._set_inputs_enabled(True)
                    self._stop_btn.hide()
                    if self._status.text().startswith("Opening Brightspace"):
                        self._status.setText("Run finished. See the log for details.")
                    self.save_state()
                elif msg == "__PAGES__":
                    from gui_dialogs import PagesDialog
                    if self._stop_event.is_set():
                        self._response_queue.put([])
                        continue
                    dlg = PagesDialog(tag, self)
                    if dlg.exec():
                        chosen = dlg.result_value()
                        self._results = {tag[i]["url"]: "pending" for i in chosen}
                        self._update_results()
                        self._response_queue.put(chosen)
                    else:
                        self._status.setText("Cancelled. No pages changed.")
                        self._response_queue.put([])
                elif msg == "__RESULT__":
                    page, state = tag
                    self._results[page["url"]] = state
                    self._update_results()
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass
