import asyncio
import queue
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QCheckBox,
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
        self._stop_flag = [False]
        self._task_active = False
        self._worker_loop = None
        self._worker_task = None
        self._active_dialogs = []
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
        layout.addSpacing(12)

        self._gradebook_cb = QCheckBox("Add H5P activities to gradebook")
        self._gradebook_cb.setChecked(False)
        self._gradebook_cb.setToolTip(
            "Checked: choose Add Grade Item for every H5P activity in this run. "
            "Unchecked: proceed without grade items."
        )
        layout.addWidget(self._gradebook_cb)
        layout.addSpacing(14)

        self._run_btn = QPushButton("Run H5P")
        self._run_btn.setFixedHeight(42)
        self._run_btn.clicked.connect(self._start_run)
        run_row = QHBoxLayout()
        run_row.addWidget(self._run_btn, 1)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setProperty("variant", "secondary")
        self._stop_btn.setFixedHeight(42)
        self._stop_btn.setToolTip(
            "Stop the current H5P run and close its browser. Already saved activities remain."
        )
        self._stop_btn.clicked.connect(self._stop_run)
        self._stop_btn.hide()
        run_row.addWidget(self._stop_btn)
        layout.addLayout(run_row)
        layout.addSpacing(8)

        # Pause-point buttons (hidden until needed)
        self._ready_btn = QPushButton("Ready — Scrape Now")
        self._ready_btn.setProperty("variant", "success")
        self._ready_btn.setFixedHeight(38)
        self._ready_btn.clicked.connect(self._moodle_ready)
        self._ready_btn.hide()
        layout.addWidget(self._ready_btn)

        h5p_row = QHBoxLayout()
        h5p_row.setSpacing(8)
        self._h5p_ready_btn = QPushButton("Ready — Download H5P")
        self._h5p_ready_btn.setProperty("variant", "success")
        self._h5p_ready_btn.setFixedHeight(38)
        self._h5p_ready_btn.clicked.connect(self._h5p_ready)
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn = QPushButton("Skip H5P")
        self._h5p_skip_btn.setProperty("variant", "secondary")
        self._h5p_skip_btn.setFixedWidth(120)
        self._h5p_skip_btn.setFixedHeight(38)
        self._h5p_skip_btn.clicked.connect(self._h5p_skip)
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

    def save_state(self):
        if not hasattr(self._mw, "save_config"):
            return
        self._mw.save_config({
            "h5p_bs_url": self._bs_entry.text().strip(),
            "h5p_moodle_url": self._moodle_entry.text().strip(),
        })

    def _start_run(self):
        if self._task_active:
            return
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            return

        bs_url = self._bs_entry.text().strip()
        moodle_url = self._moodle_entry.text().strip()
        grade_all = self._gradebook_cb.isChecked()
        if not bs_url or not moodle_url:
            self._log.append_log("Enter both a Brightspace and a Moodle course URL.", "warning")
            return

        self.save_state()

        moodle_ev = threading.Event()
        h5p_ev = threading.Event()
        skip_flag = [False]
        self._moodle_ready_event = moodle_ev
        self._h5p_ready_event = h5p_ev
        self._h5p_skip_flag = skip_flag
        stop_flag = [False]
        self._stop_flag = stop_flag
        self._task_active = True

        self._ready_btn.hide()
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()

        self._run_btn.setText("Running…")
        self._run_btn.setEnabled(False)
        self._stop_btn.setText("Stop")
        self._stop_btn.setEnabled(True)
        self._stop_btn.show()
        self._log.clear_log()

        q = self._log_queue

        def grade_recovery(item_name: str, should_grade: bool) -> str:
            result = ["skip"]
            event = threading.Event()
            q.put(("__H5P_GRADE_RECOVERY__", (item_name, result, event)))
            while not event.wait(0.1):
                if stop_flag[0]:
                    return "stop"
            return "stop" if stop_flag[0] else result[0]

        def h5p_recovery(failures: list[dict]) -> list[dict]:
            result = []
            event = threading.Event()
            q.put(("__H5P_FILE_RECOVERY__", (failures, result, event)))
            while not event.wait(0.1):
                if stop_flag[0]:
                    return []
            return [] if stop_flag[0] else result

        def worker():
            succeeded = [False]

            def on_done():
                succeeded[0] = True
                q.put(("__COMPLETE__", ""))

            async def run():
                self._worker_loop = asyncio.get_running_loop()
                self._worker_task = asyncio.current_task()
                if stop_flag[0]:
                    raise asyncio.CancelledError()
                await run_h5p_only(
                    bs_url=bs_url,
                    moodle_url=moodle_url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    moodle_ready_event=moodle_ev,
                    on_moodle_waiting=lambda: q.put(("__H5P_MOODLE_WAITING__", "")),
                    h5p_ready_event=h5p_ev,
                    on_h5p_waiting=lambda: q.put(("__H5P_WAITING__", "")),
                    h5p_recovery=h5p_recovery,
                    h5p_skip_flag=skip_flag,
                    stop_flag=stop_flag,
                    h5p_grade_all=grade_all,
                    h5p_grade_recovery=grade_recovery,
                    bs_username=self._mw.bs_username,
                    bs_password=self._mw.bs_password,
                    sso_email=self._mw.sso_email,
                    sso_password=self._mw.sso_password,
                    moodle_username=self._mw.moodle_username,
                    moodle_password=self._mw.moodle_password,
                )

            try:
                from h5p_runner import run_h5p_only
                asyncio.run(run())
            except asyncio.CancelledError:
                q.put(("H5P stopped by user. Already saved activities remain.", "warning"))
            except Exception as e:
                succeeded[0] = False
                msg, detail = friendly_error(e)
                q.put((f"Error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
            finally:
                self._worker_task = None
                self._worker_loop = None
                q.put(("__DONE__", succeeded[0] and not stop_flag[0]))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _stop_run(self):
        if not self._task_active or self._stop_flag[0]:
            return
        self._stop_flag[0] = True
        self._stop_btn.setText("Stopping…")
        self._stop_btn.setEnabled(False)
        self._log.append_log("Stopping H5P…", "warning")
        self._ready_btn.hide()
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()
        # Schedule cancellation before releasing waits, so the task cannot
        # start another browser action after a manual pause is released.
        loop, task = self._worker_loop, self._worker_task
        if loop and task and loop.is_running():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The worker finished while Stop was clicked.
        for event in (self._moodle_ready_event, self._h5p_ready_event):
            if event:
                event.set()
        for dialog in list(self._active_dialogs):
            dialog.close()

    def _show_prompt(self, dialog, on_finished=None):
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

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if self._stop_flag[0] and msg.startswith("__H5P_"):
                    if msg in ("__H5P_GRADE_RECOVERY__", "__H5P_FILE_RECOVERY__"):
                        tag[-1].set()
                    continue
                if msg == "__DONE__":
                    for dialog in list(self._active_dialogs):
                        dialog.close()
                    self._task_active = False
                    self._run_btn.setText("Run H5P")
                    self._run_btn.setEnabled(True)
                    self._stop_btn.hide()
                    self._ready_btn.hide()
                    self._h5p_ready_btn.hide()
                    self._h5p_skip_btn.hide()
                    if tag:
                        self.step_success.emit()
                elif msg == "__COMPLETE__":
                    if not self._stop_flag[0]:
                        self._run_btn.setText("Finished — close browser or Stop")
                elif msg == "__H5P_MOODLE_WAITING__":
                    self._ready_btn.setText("Ready — Scrape Now")
                    self._ready_btn.show()
                elif msg == "__H5P_WAITING__":
                    self._h5p_ready_btn.show()
                    self._h5p_skip_btn.show()
                elif msg == "__H5P_GRADE_RECOVERY__":
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
                    dlg.addButton("Skip This Item", QMessageBox.ButtonRole.DestructiveRole)
                    stop = dlg.addButton("Stop Run", QMessageBox.ButtonRole.RejectRole)

                    def grade_finished(dlg=dlg, result_ref=result_ref, event=event,
                                       fixed=fixed, retry=retry, stop=stop):
                        clicked = dlg.clickedButton()
                        result_ref[0] = (
                            "stop" if self._stop_flag[0] or clicked is stop else
                            "continue" if clicked is fixed else
                            "retry" if clicked is retry else "skip"
                        )
                        if result_ref[0] == "stop":
                            self._stop_run()
                        event.set()

                    self._show_prompt(dlg, grade_finished)
                elif msg == "__H5P_FILE_RECOVERY__":
                    failures, result_ref, event = tag
                    from gui_dialogs import H5PRecoveryDialog
                    self._show_prompt(
                        H5PRecoveryDialog(failures, result_ref, event, self), event.set
                    )
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
