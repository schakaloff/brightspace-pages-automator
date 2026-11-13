import asyncio
import queue
import threading

from PySide6.QtCore import Signal, QTimer

from panels.h5p_workspace import H5PWorkspace
from panels._shared import friendly_error
from h5p_progress import H5PRun, saved_runs, clean_url
from panels.h5p_manual_card import parse_unit_url



class Msg:
    """Tokens the worker thread puts on the log queue instead of a log line.

    _poll_log reads (token, payload) pairs; anything else is (message, tag) to log.
    """
    DONE = "__DONE__"                          # payload: bool, run succeeded
    COMPLETE = "__COMPLETE__"                  # run finished its work, cleanup follows
    STATE = "__H5P_STATE__"                    # payload: dict with 'stage'
    REVIEW = "__H5P_REVIEW__"                  # payload: (plan, result, event)
    GRADE_RECOVERY = "__H5P_GRADE_RECOVERY__"  # payload: (item_name, result, event)
    FILE_RECOVERY = "__H5P_FILE_RECOVERY__"    # payload: (failures, result, event)
    MOODLE_WAITING = "__H5P_MOODLE_WAITING__"
    WAITING = "__H5P_WAITING__"
    WAITS_FOR_ANSWER = (GRADE_RECOVERY, FILE_RECOVERY, REVIEW)  # a worker thread blocks until answered
    H5P_PREFIX = "__H5P_"                      # every token above except DONE and COMPLETE


class H5PPanel(H5PWorkspace):
    step_success = Signal()
    back_requested = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._moodle_ready_event = None
        self._h5p_ready_event = None
        self._h5p_skip_flag = [False]
        self._stop_flag = [False]
        self._task_active = False
        self._back_to_courses_pending = False
        self._worker_loop = None
        self._worker_task = None
        self._active_dialogs = []
        self._progress_directory = None
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _refresh_saved_runs(self):
        runs = saved_runs(self._progress_directory, include_removed=True)
        active = sum(not run.data.get('removed_at') for run in runs)
        self._saved_count.setText(f'{active} saved')
        self._saved_hint.setText('Find a course, resume a transfer, or remove old scans.' if active else
            'Your saved list is clear. You can restore removed entries in Browse saved transfers.')
        self._resume_btn.setEnabled(bool(runs) and not self._task_active)
        self._saved_card.setVisible(bool(runs) and not self._task_active and getattr(self, '_stage', 0) == 0)

    def _browse_saved_transfers(self):
        if self._task_active:
            return
        from panels.h5p_saved_transfers import SavedH5PTransfersDialog
        dialog = SavedH5PTransfersDialog(self._progress_directory, self)
        accepted = dialog.exec()
        run_id = dialog.selected_run_id
        dialog.deleteLater()
        self._refresh_saved_runs()
        if accepted and run_id:
            self._resume_run(run_id)

    def _resume_run(self, run_id=None):
        if self._task_active or not run_id: return
        try:
            run = H5PRun.load(run_id, self._progress_directory)
            if run.data.get('removed_at'):
                raise ValueError('Restore this transfer to the saved list before continuing it.')
        except (OSError, ValueError) as exc:
            self._log.append_log(str(exc), 'warning')
            self._status_hint.setText(str(exc))
            self._refresh_saved_runs()
            return
        self._bs_entry.setText(run.data['bs_url'])
        self._moodle_entry.setText(run.data['moodle_url'])
        self._gradebook_cb.setChecked(run.data['grade_all'])
        self._start_run(run.data['id'])

    def save_state(self):
        if not hasattr(self._mw, "save_config"):
            return
        self._mw.save_config({
            "h5p_bs_url": self._bs_entry.text().strip(),
            "h5p_moodle_url": self._moodle_entry.text().strip(),
            "h5p_watch_browser": self._watch_browser_cb.isChecked(),
        })

    def _update_back_navigation(self):
        returning = self._back_to_courses_pending
        to_courses = self._task_active or self._stage != 0
        destination = 'course setup' if to_courses else 'Media'
        self._back_btn.setText('Going back…' if returning else '← Back')
        self._back_btn.setEnabled(not returning)
        self._back_btn.setAccessibleName('Back to ' + destination)
        self._back_btn.setToolTip(
            'Stop the current work, keep saved progress, and return to course setup.'
            if self._task_active else 'Return to ' + destination + '.')

    def _go_back(self):
        if self._back_to_courses_pending:
            return
        if self._task_active:
            # Keep the current run on screen until its worker has saved and
            # closed the browser. Resetting sooner could allow overlapping runs.
            self._back_to_courses_pending = True
            self._stop_run()
            self._update_back_navigation()
        elif self._stage != 0:
            self._new_transfer()
            self._moodle_entry.setFocus()
        else:
            self.save_state()
            self.back_requested.emit()

    def _start_manual(self, unit_url, files, grade):
        self._start_run(manual={'unit_url': unit_url, 'files': files, 'grade': grade})

    def _start_run(self, resume_id=None, manual=None):
        if self._task_active:
            return
        if not self._mw.chromium_ready:
            self._log.append_log("Browser engine still installing — please wait.", "warning")
            self._status_hint.setText('The browser engine is still installing. Please wait a moment, then try again.')
            return

        # A manual run uploads local files to one unit, so it has no Moodle course.
        bs_url = manual['unit_url'] if manual else self._bs_entry.text().strip()
        moodle_url = '' if manual else self._moodle_entry.text().strip()
        grade_all = manual['grade'] if manual else self._gradebook_cb.isChecked()
        watch_browser = self._watch_browser_cb.isChecked()
        if not manual:
            if not bs_url or not moodle_url:
                self._log.append_log("Enter both a Brightspace and a Moodle course URL.", "warning")
                self._status_hint.setText('Enter both a Moodle source and a Brightspace destination course URL.')
                return

            try:
                from content_checker import _extract_course_id
                clean_url(bs_url)
                clean_url(moodle_url)
                if not _extract_course_id(bs_url):
                    raise ValueError('Enter a Brightspace course URL containing its course ID.')
            except ValueError as exc:
                self._status_hint.setText(str(exc))
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
        self._back_to_courses_pending = False
        self._resume_id = resume_id or None
        self._run_data = None
        self._plan = None
        self._result_summary = {}
        self._last_error = None
        self._review_wait = None
        self._furthest_stage = 0
        self._set_stage(0)
        self._workspace.hide()
        self._saved_card.hide()
        self._change_btn.hide()
        self._new_btn.hide()
        self._progress_bar.hide()
        self._status_line.setText('Adding your H5P files' if manual else 'Finding your H5P activities')
        self._status_hint.setText('The browser will open and sign in to Brightspace. Your files are then uploaded and placed on new pages.' if manual else
            'The browser will open to connect your courses. Course content changes wait until you approve the plan.')
        self._resume_btn.setEnabled(False)
        self._manual_card.set_busy(True)
        for field in (self._bs_entry, self._moodle_entry, self._gradebook_cb, self._watch_browser_cb): field.setEnabled(False)

        self._ready_btn.hide()
        self._h5p_ready_btn.hide()
        self._h5p_skip_btn.hide()

        self._run_btn.setText("Finding activities…")
        self._run_btn.setEnabled(False)
        self._stop_btn.setText("Stop && save")
        self._stop_btn.setEnabled(True)
        self._stop_btn.show()
        self._log.clear_log()

        q = self._log_queue
        credentials = {key: getattr(self._mw, key, '') for key in (
            'bs_username', 'bs_password', 'sso_email', 'sso_password', 'moodle_username', 'moodle_password')}

        def review(plan):
            result = [None]
            event = threading.Event()
            q.put((Msg.REVIEW, (plan, result, event)))
            while not event.wait(0.1):
                if stop_flag[0]:
                    return None
            return None if stop_flag[0] else result[0]

        def grade_recovery(item_name: str, should_grade: bool) -> str:
            result = ["skip"]
            event = threading.Event()
            q.put((Msg.GRADE_RECOVERY, (item_name, result, event)))
            while not event.wait(0.1):
                if stop_flag[0]:
                    return "stop"
            return "stop" if stop_flag[0] else result[0]

        def h5p_recovery(failures: list[dict]) -> list[dict]:
            result = []
            event = threading.Event()
            q.put((Msg.FILE_RECOVERY, (failures, result, event)))
            while not event.wait(0.1):
                if stop_flag[0]:
                    return []
            return [] if stop_flag[0] else result

        def worker():
            succeeded = [False]

            def on_done():
                succeeded[0] = True
                q.put((Msg.COMPLETE, ""))

            async def run():
                self._worker_loop = asyncio.get_running_loop()
                self._worker_task = asyncio.current_task()
                if stop_flag[0]:
                    raise asyncio.CancelledError()
                if manual:
                    from h5p_runner import run_h5p_manual
                    _, unit_id = parse_unit_url(bs_url)
                    await run_h5p_manual(
                        bs_url=bs_url, unit_id=unit_id, files=manual['files'], grade=grade_all,
                        log=lambda msg, tag="info": q.put((msg, tag)),
                        on_complete=on_done, h5p_grade_recovery=grade_recovery,
                        stop_flag=stop_flag, **credentials,
                        on_state=lambda state: q.put((Msg.STATE, state)),
                        watch_browser=watch_browser,
                    )
                    return
                await run_h5p_only(
                    bs_url=bs_url,
                    moodle_url=moodle_url,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    moodle_ready_event=moodle_ev,
                    on_moodle_waiting=lambda: q.put((Msg.MOODLE_WAITING, "")),
                    h5p_ready_event=h5p_ev,
                    on_h5p_waiting=lambda: q.put((Msg.WAITING, "")),
                    h5p_recovery=h5p_recovery,
                    h5p_skip_flag=skip_flag,
                    stop_flag=stop_flag,
                    h5p_grade_all=grade_all,
                    h5p_grade_recovery=grade_recovery,
                    **credentials,
                    resume_id=resume_id or None,
                    progress_directory=self._progress_directory,
                    review_fn=review,
                    on_state=lambda state: q.put((Msg.STATE, state)),
                    watch_browser=watch_browser,
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
                q.put((Msg.DONE, succeeded[0] and not stop_flag[0]))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _stop_run(self):
        if not self._task_active or self._stop_flag[0]:
            return
        self._stop_flag[0] = True
        self._stop_btn.setText("Stopping…")
        self._stop_btn.setEnabled(False)
        self._run_btn.setEnabled(False)
        self._status_line.setText('Stopping and saving progress…')
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
        if self._review_wait:
            self._review_wait[1].set()
            self._review_wait = None
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
                if not self._stop_flag[0] and self._stage == 2:
                    self._status_line.setText('Transferring your activities')
                    self._status_hint.setText('Continuing with the available activities. Your progress is saved automatically.')

        dialog.finished.connect(finished)
        dialog.setModal(False)
        dialog.show()

    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if self._stop_flag[0] and msg.startswith(Msg.H5P_PREFIX):
                    if msg in Msg.WAITS_FOR_ANSWER:
                        tag[-1].set()
                    elif msg == Msg.STATE and tag.get('run'):
                        self._run_data = tag['run']
                        self._resume_id = self._run_data['id']
                    continue
                if msg == Msg.DONE:
                    for dialog in list(self._active_dialogs):
                        dialog.close()
                    self._task_active = False
                    self._manual_card.set_busy(False)
                    for field in (self._bs_entry, self._moodle_entry, self._gradebook_cb, self._watch_browser_cb): field.setEnabled(True)
                    self._refresh_saved_runs()
                    self._run_btn.setEnabled(True)
                    self._stop_btn.hide()
                    self._ready_btn.hide()
                    self._h5p_ready_btn.hide()
                    self._h5p_skip_btn.hide()
                    self._show_results(bool(tag))
                    if self._last_error:
                        self._status_hint.setText(self._last_error)
                    if tag:
                        self.step_success.emit()
                    if self._back_to_courses_pending:
                        self._back_to_courses_pending = False
                        self._new_transfer()
                        self._status_hint.setText('Your progress is saved. Open a saved transfer to continue, or change the courses to start again.')
                        self._moodle_entry.setFocus()
                    else:
                        self._update_back_navigation()
                elif msg == Msg.COMPLETE:
                    if not self._stop_flag[0]:
                        self._run_btn.setText("Finishing…")
                elif msg == Msg.REVIEW:
                    self._show_plan(*tag)
                elif msg == Msg.STATE:
                    stage = tag['stage']
                    if stage in ('progress', 'results'):
                        self._show_progress(tag['run'])
                    if stage == 'transfer':
                        self._set_stage(2)
                        self._status_line.setText('Transferring your activities')
                        self._status_hint.setText(tag['message'])
                        self._run_btn.setText('Transferring…')
                        self._run_btn.setEnabled(False)
                    elif stage == 'watch':
                        count = f"Activity {tag['current']} of {tag['total']} · " if tag.get('total') else ''
                        activity = f"\n{tag['activity']}" if tag.get('activity') else ''
                        self._status_hint.setText(count + tag['message'] + activity)
                    elif stage == 'connect':
                        self._status_line.setText('Connecting your courses')
                        self._status_hint.setText('Sign in to Brightspace in the browser if asked. Next, confirm the Moodle course here.')
                    elif stage == 'empty':
                        self._status_hint.setText('No H5P activities were found in this Moodle course.')
                    elif stage == 'results':
                        self._result_summary = tag.get('summary', {})
                        issues = tag.get('summary', {}).get('h5p_grade_failed', [])
                        if issues:
                            self._last_error = f'{len(issues)} activities need a gradebook check. Review their pages and gradebook settings in Brightspace.'
                        elif tag['run'].get('status') == 'complete':
                            self._last_error = None
                elif msg == Msg.MOODLE_WAITING:
                    self._status_line.setText('Check the Moodle course in the browser')
                    self._status_hint.setText('Sign in if needed and open the correct course or section. Then continue here to find its activities.')
                    self._ready_btn.setText('I’m on the correct course — continue')
                    self._ready_btn.show()
                elif msg == Msg.WAITING:
                    self._status_line.setText('Ready to download from Moodle')
                    self._status_hint.setText('Check that the browser shows the correct course and that editing controls are available.')
                    self._h5p_ready_btn.show()
                    self._h5p_skip_btn.show()
                elif msg == Msg.GRADE_RECOVERY:
                    item_name, result_ref, event = tag
                    self._status_line.setText('A gradebook choice needs your help')
                    self._status_hint.setText(f'Check the open Brightspace dialog for {item_name}. Use the recovery window to retry or continue.')
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
                elif msg == Msg.FILE_RECOVERY:
                    failures, result_ref, event = tag
                    self._status_line.setText('Some downloads need your help')
                    self._status_hint.setText('Choose replacement .h5p files in the recovery window, or continue with the available activities.')
                    from gui_dialogs import H5PRecoveryDialog
                    self._show_prompt(
                        H5PRecoveryDialog(failures, result_ref, event, self), event.set
                    )
                else:
                    self._log.append_log(msg, tag)
                    if tag == 'error':
                        self._last_error = msg
                        self._status_hint.setText(msg)
        except queue.Empty:
            pass

    def _moodle_ready(self):
        self._ready_btn.hide()
        self._status_line.setText('Reading the Moodle activity list')
        self._status_hint.setText('Preparing your transfer plan. Downloads and course content changes have not started.')
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
