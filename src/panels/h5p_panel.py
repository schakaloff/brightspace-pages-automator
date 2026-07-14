import asyncio
import queue
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit,
)
from PySide6.QtCore import Signal, QTimer

from gui_log import LogWidget
from panels._shared import _form_label, _section_header, friendly_error


class H5PPanel(QWidget):
    step_success = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._moodle_ready_event = None
        self._h5p_ready_event = None
        self._h5p_skip_flag = [False]
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("H5P"))
        sub = QLabel(
            "Download H5P activities from Moodle and paste them into the matching "
            "Brightspace modules — no Moodle/Brightspace diff, just H5P."
        )
        sub.setProperty("role", "dim")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("BRIGHTSPACE COURSE URL"))
        layout.addSpacing(4)
        self._bs_entry = QLineEdit()
        self._bs_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/<id>/home")
        self._bs_entry.setFixedHeight(40)
        layout.addWidget(self._bs_entry)
        layout.addSpacing(12)

        layout.addWidget(_form_label("MOODLE COURSE URL"))
        layout.addSpacing(4)
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setFixedHeight(40)
        layout.addWidget(self._moodle_entry)
        layout.addSpacing(14)

        self._run_btn = QPushButton("Run H5P")
        self._run_btn.setFixedHeight(42)
        self._run_btn.clicked.connect(self._start_run)
        layout.addWidget(self._run_btn)
        layout.addSpacing(8)

        # Pause-point buttons (hidden until needed)
        self._ready_btn = QPushButton("Ready — Scrape Now")
        self._ready_btn.setProperty("variant", "success")
        self._ready_btn.setFixedHeight(38)
        self._ready_btn.hide()
        layout.addWidget(self._ready_btn)

        h5p_row = QHBoxLayout()
        h5p_row.setSpacing(8)
        self._h5p_ready_btn = QPushButton("Ready — Download H5P")
        self._h5p_ready_btn.setProperty("variant", "success")
        self._h5p_ready_btn.setFixedHeight(38)
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn = QPushButton("Skip H5P")
        self._h5p_skip_btn.setProperty("variant", "secondary")
        self._h5p_skip_btn.setFixedWidth(120)
        self._h5p_skip_btn.setFixedHeight(38)
        self._h5p_skip_btn.hide()
        h5p_row.addWidget(self._h5p_ready_btn, 1)
        h5p_row.addWidget(self._h5p_skip_btn)
        layout.addLayout(h5p_row)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)

        self._log = LogWidget()
        layout.addWidget(self._log, 1)

        # Load saved URLs
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("h5p_bs_url"):
            self._bs_entry.setText(cfg["h5p_bs_url"])
        if cfg.get("h5p_moodle_url"):
            self._moodle_entry.setText(cfg["h5p_moodle_url"])

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return

        bs_url = self._bs_entry.text().strip()
        moodle_url = self._moodle_entry.text().strip()
        if not bs_url or not moodle_url:
            self._log.append_log("Enter both a Brightspace and a Moodle course URL.", "warning")
            return

        self._mw.save_config({"h5p_bs_url": bs_url, "h5p_moodle_url": moodle_url})

        moodle_ev = threading.Event()
        h5p_ev = threading.Event()
        skip_flag = [False]
        self._moodle_ready_event = moodle_ev
        self._h5p_ready_event = h5p_ev
        self._h5p_skip_flag = skip_flag

        self._ready_btn.hide()
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()

        self._run_btn.setText("Running…")
        self._run_btn.setEnabled(False)
        self._log.clear_log()

        q = self._log_queue

        def worker():
            done_sent = [False]

            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))

            try:
                from h5p_runner import run_h5p_only
                asyncio.run(run_h5p_only(
                    bs_url=bs_url,
                    moodle_url=moodle_url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    moodle_ready_event=moodle_ev,
                    on_moodle_waiting=lambda: q.put(("__H5P_MOODLE_WAITING__", "")),
                    h5p_ready_event=h5p_ev,
                    on_h5p_waiting=lambda: q.put(("__H5P_WAITING__", "")),
                    h5p_skip_flag=skip_flag,
                    bs_username=self._mw.bs_username,
                    bs_password=self._mw.bs_password,
                    sso_email=self._mw.sso_email,
                    sso_password=self._mw.sso_password,
                    moodle_username=self._mw.moodle_username,
                    moodle_password=self._mw.moodle_password,
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
                    self._run_btn.setText("Run H5P")
                    self._run_btn.setEnabled(True)
                    self._ready_btn.hide()
                    self._h5p_ready_btn.hide()
                    self._h5p_skip_btn.hide()
                    self.step_success.emit()
                elif msg == "__H5P_MOODLE_WAITING__":
                    self._ready_btn.setText("Ready — Scrape Now")
                    try:
                        self._ready_btn.clicked.disconnect()
                    except RuntimeError:
                        pass
                    self._ready_btn.clicked.connect(self._moodle_ready)
                    self._ready_btn.show()
                elif msg == "__H5P_WAITING__":
                    self._h5p_ready_btn.show()
                    self._h5p_skip_btn.show()
                    try:
                        self._h5p_ready_btn.clicked.disconnect()
                    except RuntimeError:
                        pass
                    try:
                        self._h5p_skip_btn.clicked.disconnect()
                    except RuntimeError:
                        pass
                    self._h5p_ready_btn.clicked.connect(self._h5p_ready)
                    self._h5p_skip_btn.clicked.connect(self._h5p_skip)
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass

    def _moodle_ready(self):
        self._ready_btn.hide()
        if self._moodle_ready_event:
            self._moodle_ready_event.set()

    def _h5p_ready(self):
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()
        if self._h5p_ready_event:
            self._h5p_ready_event.set()

    def _h5p_skip(self):
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()
        self._h5p_skip_flag[0] = True
        if self._h5p_ready_event:
            self._h5p_ready_event.set()
