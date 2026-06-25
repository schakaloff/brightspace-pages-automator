import asyncio
import queue
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QToolButton, QMenu, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer

from gui_log import LogWidget
from panels._shared import _divider, _form_label, _section_header


class CheckerPanel(QWidget):
    step_success = Signal()
    continue_next = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._moodle_ready_event = None
        self._h5p_ready_event    = None
        self._file_checklist_event = None
        self._h5p_skip_flag      = [False]
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Content Checker"))
        sub = QLabel("Verify that Moodle content exists in Brightspace — leave either URL blank to test just that side.")
        sub.setProperty("role", "dim"); sub.setWordWrap(True)
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

        # Split run button + dropdown menu
        self._run_btn = QToolButton()
        self._run_btn.setText("Run Check")
        self._run_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._run_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._run_btn.setFixedHeight(42)
        self._run_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._run_btn.clicked.connect(self._start_run)

        run_menu = QMenu(self._run_btn)

        full_run_act = run_menu.addAction("Full Run")
        full_run_act.triggered.connect(self._start_run)

        h5p_act = run_menu.addAction("H5P Upload Only")
        h5p_act.triggered.connect(self._start_phase_b)

        run_menu.addSeparator()

        self._relink_act = run_menu.addAction("Re-link Moodle files")
        self._relink_act.setCheckable(True); self._relink_act.setChecked(True)

        self._pdf_act = run_menu.addAction("Upload missing files")
        self._pdf_act.setCheckable(True); self._pdf_act.setChecked(True)

        self._h5p_act = run_menu.addAction("Upload H5P")
        self._h5p_act.setCheckable(True); self._h5p_act.setChecked(False)

        self._run_btn.setMenu(run_menu)
        layout.addWidget(self._run_btn)
        layout.addSpacing(8)

        # Pause-point buttons (hidden until needed)
        self._ready_btn = QPushButton("Ready — Scrape Now")
        self._ready_btn.setProperty("variant", "success")
        self._ready_btn.setFixedHeight(38)
        self._ready_btn.hide()
        layout.addWidget(self._ready_btn)

        h5p_row = QHBoxLayout(); h5p_row.setSpacing(8)
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
        self._continue_btn.hide()
        self._continue_btn.clicked.connect(self.continue_next)
        layout.addWidget(self._continue_btn)

        # Load saved URLs
        cfg = self._mw.load_config() if hasattr(self._mw, "load_config") else {}
        if cfg.get("chk_bs_url"):
            self._bs_entry.setText(cfg["chk_bs_url"])
        if cfg.get("chk_moodle_url"):
            self._moodle_entry.setText(cfg["chk_moodle_url"])

    def _run_worker(self, phase_b: bool = False):
        bs_url     = self._bs_entry.text().strip()
        moodle_url = self._moodle_entry.text().strip()
        if not bs_url and not moodle_url:
            self._log.append_log("Paste at least one URL.", "warning")
            return
        if phase_b and not bs_url:
            self._log.append_log("Paste a Brightspace URL first.", "warning")
            return

        self._mw.save_config({"chk_bs_url": bs_url, "chk_moodle_url": moodle_url})

        import threading as _t
        moodle_ev = _t.Event(); h5p_ev = _t.Event(); file_ev = _t.Event()
        file_result = []
        skip_flag   = [False]
        self._moodle_ready_event   = moodle_ev
        self._h5p_ready_event      = h5p_ev
        self._file_checklist_event = file_ev
        self._h5p_skip_flag        = skip_flag

        self._ready_btn.hide()
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()
        self._continue_btn.hide()
        self._dl_label.hide()

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._log.clear_log()

        q = self._log_queue

        def confirm(msg: str) -> bool:
            from PySide6.QtWidgets import QMessageBox
            result = [False]; ev = _t.Event()
            def ask():
                result[0] = QMessageBox.question(
                    self, "Continue?", msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) == QMessageBox.StandardButton.Yes
                ev.set()
            QTimer.singleShot(0, ask)
            ev.wait()
            return result[0]

        def worker():
            done_sent = [False]
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
                    file_checklist_event=file_ev,
                    on_file_checklist=lambda d: q.put(("__CHK_FILE_CHECKLIST__", (d, file_result, file_ev))),
                    confirm_fn=confirm,
                    bs_username=self._mw.bs_username,
                    bs_password=self._mw.bs_password,
                    sso_email=self._mw.sso_email,
                    sso_password=self._mw.sso_password,
                    moodle_username=self._mw.moodle_username,
                    moodle_password=self._mw.moodle_password,
                )
                checker.do_relink     = self._relink_act.isChecked()
                checker.do_pdf_upload = self._pdf_act.isChecked()
                checker.do_h5p_embed  = self._h5p_act.isChecked()
                checker.file_checklist_result = file_result
                checker.h5p_skip_flag = skip_flag
                if phase_b:
                    checker.do_relink = False
                    checker.do_h5p_embed = True
                    checker.h5p_phase_b_only = True
                asyncio.run(checker.run())
            except Exception as e:
                q.put((f"Error: {e}", "error"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()

    def _start_run(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(phase_b=False)

    def _start_phase_b(self):
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return
        self._run_worker(phase_b=True)

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Run Check"); self._run_btn.setEnabled(True)
                    self._ready_btn.hide()
                    self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
                    dl = Path(__file__).parent.parent.parent / "downloads"
                    self._dl_label.setText(f"Downloads: {dl}")
                    self._dl_label.show()
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                elif msg == "__CHK_MOODLE_WAITING__":
                    self._ready_btn.setText("Ready — Scrape Now")
                    try:
                        self._ready_btn.clicked.disconnect()
                    except RuntimeError:
                        pass
                    self._ready_btn.clicked.connect(self._moodle_ready)
                    self._ready_btn.show()
                elif msg == "__CHK_H5P_WAITING__":
                    self._h5p_ready_btn.show(); self._h5p_skip_btn.show()
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
                elif msg == "__CHK_FILE_CHECKLIST__":
                    data_json, result_list, event = tag
                    from gui_dialogs import FileChecklistDialog
                    dlg = FileChecklistDialog(data_json, result_list, event, self)
                    dlg.exec()
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass

    def _moodle_ready(self):
        self._ready_btn.hide()
        if self._moodle_ready_event:
            self._moodle_ready_event.set()

    def _h5p_ready(self):
        self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
        if self._h5p_ready_event:
            self._h5p_ready_event.set()

    def _h5p_skip(self):
        self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
        self._h5p_skip_flag[0] = True
        if self._h5p_ready_event:
            self._h5p_ready_event.set()
