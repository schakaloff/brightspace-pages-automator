import asyncio
import queue
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit,
)
from PySide6.QtCore import Signal, QTimer

from gui_log import LogWidget
from panels._shared import (
    _divider, _form_label, _section_header, PAGE_THEMES, _build_theme_swatches, friendly_error,
)


class RestylePanel(QWidget):
    step_success = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._response_queue: queue.Queue = queue.Queue()
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

        layout.addWidget(_section_header("Restyle"))
        sub = QLabel("Pick an OC brand colour theme, paste a Brightspace page or section URL, and let Claude restyle it.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("PAGE THEME"))
        layout.addSpacing(6)
        self._swatch_frames, self._selected_theme = _build_theme_swatches(layout)
        layout.addSpacing(14)

        layout.addWidget(_form_label("BRIGHTSPACE PAGE URL"))
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

        self._run_btn = QPushButton("Start")
        self._run_btn.setFixedSize(110, 42)
        self._run_btn.setToolTip(
            "Opens a browser, extracts the page HTML, sends it to Claude AI for restyling,\n"
            "and writes the styled HTML back to Brightspace."
        )
        self._run_btn.clicked.connect(self._start_run)
        url_row.addWidget(self._run_btn)
        layout.addLayout(url_row)

        url_hint = QLabel("Paste a section URL to restyle multiple pages at once — you'll pick which ones to include.")
        url_hint.setProperty("role", "dim")
        url_hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(url_hint)
        layout.addSpacing(12)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)

        # Load saved URL
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("automator_url"):
            self._url_entry.setText(cfg["automator_url"])

    def save_state(self):
        if not hasattr(self._mw, "save_config"):
            return
        self._mw.save_config({
            "automator_url": self._url_entry.text().strip(),
        })

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning"); return
        url = self._url_entry.text().strip()
        if not url:
            self._log.append_log("Paste a Brightspace URL first.", "warning"); return

        style_ref_path = Path(__file__).parent.parent.parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._log.clear_log()

        q  = self._log_queue
        rq = self._response_queue

        def on_pages_found(pages):
            q.put(("__PAGES__", pages))
            return rq.get(timeout=300)

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
                    claude_api_key=self._mw.claude_api_key,
                    claude_model=self._mw.claude_model,
                    style_reference_html=style_reference_html,
                    theme_name=self._selected_theme[0],
                    on_pages_found=on_pages_found,
                    bs_username=self._mw.bs_username,
                    bs_password=self._mw.bs_password,
                    sso_email=self._mw.sso_email,
                    sso_password=self._mw.sso_password,
                ))
            except Exception as e:
                msg, detail = friendly_error(e)
                q.put((f"Error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Start"); self._run_btn.setEnabled(True)
                    self.save_state()
                elif msg == "__PAGES__":
                    from gui_dialogs import PagesDialog
                    dlg = PagesDialog(tag, self)
                    if dlg.exec():
                        self._response_queue.put(dlg.result_value())
                    else:
                        self._response_queue.put((0, len(tag)))
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass
