import asyncio
import queue
import threading
from copy import deepcopy
from pathlib import Path

from PySide6.QtWidgets import QWidget, QMessageBox
from PySide6.QtCore import Qt, Signal, QTimer

from panels._shared import friendly_error
from panels.checker_review import CheckerReview


class CheckerPanel(CheckerReview, QWidget):
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
        self._scan_report        = None
        self._scan_urls          = None
        self._build()
        self.refresh_theme()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

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
        if (scan_only or operations is not None or order_only or books_only) and not self._validate_courses():
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
        self._run_mode = "scan" if scan_only else "apply"
        self._subtitle.setText("Scanning your course pair." if scan_only else "Applying the selected fix types.")
        self._last_run_result = None
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._stop_btn.show()
        self._status_line.setText("Scanning courses…" if scan_only else "Applying selected changes…")
        self._status_line.show()
        self._workspace.hide()
        self._course_form.hide()
        self._course_summary.show()
        self._refresh_course_context()
        self._progress.show()
        self._update_apply_enabled()
        self._scroll.verticalScrollBar().setValue(0)
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
            owned_task = [None]
            checker = None
            status = "incomplete"
            errors = []
            def log(message, tag="info"):
                if tag == "error":
                    errors.append(str(message))
                q.put((message, tag))
            try:
                from content_checker import ContentChecker
                checker = ContentChecker(
                    bs_url=bs_url,
                    moodle_url=moodle_url,
                    log=log,
                    # Completion belongs to the worker finally block, after
                    # browser cleanup and the structured result are ready.
                    on_complete=lambda: None,
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
                status = "finished" if getattr(checker, "run_finished", False) else "incomplete"
            except asyncio.CancelledError:
                status = "stopped"
                q.put(("⏹ Checker stopped by user.", "warning"))
            except Exception as e:
                status = "failed"
                msg, detail = friendly_error(e)
                errors.append(msg)
                q.put((f"Error: {msg}", "error"))
                if detail != msg:
                    q.put((detail, "detail"))
            finally:
                if self._worker_task is owned_task[0]:
                    self._worker_task = None
                    self._worker_loop = None
                result = {"mode": "scan" if scan_only else "apply",
                          "status": "stopped" if self._stop_flag[0] else status,
                          "summary": deepcopy(getattr(checker, "_summary", {})),
                          "errors": errors}
                q.put(("__RUN_RESULT__", result))
                q.put(("__DONE__", ""))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _start_run(self):
        if self._task_active or not self._validate_courses():
            return
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            self._selection_summary.setText("Browser engine is still installing. Try again when it is ready.")
            return
        self._run_worker(scan_only=True)

    def _stop_run(self):
        """Cancel the active browser task and release any user prompt waits."""
        if self._stop_flag[0]:
            return
        self._stop_flag[0] = True
        self._stop_btn.setEnabled(False)
        self._status_line.setText("Stopping…")
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
                    self._progress.hide()
                    self._moodle_hint.hide()
                    self._ready_btn.hide()
                    self._h5p_hint.hide()
                    self._h5p_ready_btn.hide(); self._h5p_skip_btn.hide()
                    dl = Path(__file__).parent.parent.parent / "downloads"
                    self._dl_label.setText(f"Downloads: {dl}")
                    self._dl_label.show()
                    result = getattr(self, "_last_run_result", None)
                    if result:
                        self._show_run_result(result)
                    else:
                        self.show_review()
                    self._update_apply_enabled()
                elif msg == "__RUN_RESULT__":
                    self._last_run_result = tag
                elif msg == "__CHK_REPORT__":
                    report, scanned_urls = tag
                    self._show_scan_report(report, scanned_urls)
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                elif msg == "__CHK_MOODLE_WAITING__":
                    self._ready_btn.setText("I'm signed in — continue")
                    self._status_line.setText("Waiting for Moodle login")
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
                    ready = dlg.addButton("I'm signed in — continue", QMessageBox.ButtonRole.AcceptRole)
                    dlg.addButton("I'll use the app buttons", QMessageBox.ButtonRole.RejectRole)
                    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                    self._show_prompt(dlg, lambda: self._moodle_ready()
                                      if not self._stop_flag[0] and dlg.clickedButton() is ready else None)
                elif msg == "__CHK_H5P_WAITING__":
                    self._status_line.setText("Waiting for H5P download access")
                    self._h5p_hint.show()
                    self._h5p_ready_btn.show(); self._h5p_skip_btn.show()
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
        self._status_line.setText("Reading Moodle content…")
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
