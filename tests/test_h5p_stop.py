"""Stop must interrupt browser waits and prompts without leaving workers alive."""

import asyncio
import threading
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "src")


@pytest.fixture
def panel(qtbot):
    from panels.h5p_panel import H5PPanel

    mw = MagicMock()
    mw.chromium_ready = True
    mw.load_config.return_value = {}
    result = H5PPanel(mw)
    qtbot.addWidget(result)
    result._bs_entry.setText("https://example.org/d2l/le/content/123/home")
    result._moodle_entry.setText("https://example.org/course/view.php?id=123")
    result.show()
    return result


@pytest.mark.parametrize("mode", [
    "browser_wait", "moodle_prompt", "h5p_prompt", "grade_recovery", "file_recovery",
])
def test_stop_interrupts_waits_and_allows_a_fresh_run(panel, qtbot, monkeypatch, mode):
    import h5p_runner

    started = threading.Event()
    cancelled = threading.Event()
    cleanup_allowed = threading.Event()
    successes = []
    panel.step_success.connect(lambda: successes.append(True))

    async def fake_run(**kwargs):
        try:
            started.set()
            loop = asyncio.get_running_loop()
            if mode in ("moodle_prompt", "h5p_prompt"):
                prefix = "moodle" if mode == "moodle_prompt" else "h5p"
                kwargs[f"on_{prefix}_waiting"]()
                await loop.run_in_executor(None, kwargs[f"{prefix}_ready_event"].wait)
            elif mode == "grade_recovery":
                await loop.run_in_executor(None, kwargs["h5p_grade_recovery"], "Activity", True)
            elif mode == "file_recovery":
                await loop.run_in_executor(None, kwargs["h5p_recovery"], [{
                    "activity_key": "activity-1", "name": "Activity", "safe_name": "Activity",
                }])
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finally:
            while not cleanup_allowed.is_set():
                await asyncio.sleep(0.01)

    monkeypatch.setattr(h5p_runner, "run_h5p_only", fake_run)
    panel._start_run()
    qtbot.waitUntil(started.is_set, timeout=3000)
    if mode in ("grade_recovery", "file_recovery"):
        qtbot.waitUntil(lambda: bool(panel._active_dialogs), timeout=3000)
        assert not panel._active_dialogs[0].isModal()
    elif mode in ("moodle_prompt", "h5p_prompt"):
        button = panel._ready_btn if mode == "moodle_prompt" else panel._h5p_ready_btn
        qtbot.waitUntil(lambda: not button.isHidden(), timeout=3000)

    try:
        panel._stop_btn.click()
        qtbot.waitUntil(cancelled.is_set, timeout=3000)
        assert not panel._run_btn.isEnabled()  # Cleanup must finish before restart.
        assert not panel._stop_btn.isEnabled()
        panel._stop_run()  # Repeated requests must not cancel cleanup.
        assert not panel._active_dialogs
    finally:
        cleanup_allowed.set()
    qtbot.waitUntil(lambda: panel._run_btn.isEnabled(), timeout=3000)
    qtbot.waitUntil(lambda: not panel._worker_thread.is_alive(), timeout=3000)
    assert panel._stop_btn.isHidden()
    assert panel._ready_btn.isHidden()
    assert panel._h5p_ready_btn.isHidden()
    assert panel._h5p_skip_btn.isHidden()
    assert successes == []

    async def successful_run(**kwargs):
        assert not kwargs["stop_flag"][0]
        kwargs["on_complete"]()

    monkeypatch.setattr(h5p_runner, "run_h5p_only", successful_run)
    panel._start_run()
    qtbot.waitUntil(lambda: panel._run_btn.isEnabled(), timeout=3000)
    assert successes == [True]


def test_grade_dialog_stop_button_cancels_whole_run(panel, qtbot, monkeypatch):
    import h5p_runner

    async def fake_run(**kwargs):
        await asyncio.get_running_loop().run_in_executor(
            None, kwargs["h5p_grade_recovery"], "Activity", True
        )
        await asyncio.sleep(3600)

    monkeypatch.setattr(h5p_runner, "run_h5p_only", fake_run)
    panel._start_run()
    qtbot.waitUntil(lambda: bool(panel._active_dialogs), timeout=3000)
    dialog = panel._active_dialogs[0]
    next(button for button in dialog.buttons() if button.text() == "Stop Run").click()
    qtbot.waitUntil(lambda: panel._run_btn.isEnabled(), timeout=3000)
    qtbot.waitUntil(lambda: not panel._worker_thread.is_alive(), timeout=3000)
    assert panel._stop_flag[0]


def test_stop_before_worker_starts_does_not_launch_browser(panel, qtbot, monkeypatch):
    import h5p_runner

    launch = MagicMock()
    monkeypatch.setattr(h5p_runner, "run_h5p_only", launch)
    real_thread = threading.Thread
    deferred = []

    class DeferredThread:
        def __init__(self, target, **kwargs):
            deferred.append(target)

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", DeferredThread)
    panel._start_run()
    panel._stop_btn.click()
    thread = real_thread(target=deferred[0])
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=3000)
    qtbot.waitUntil(lambda: panel._run_btn.isEnabled(), timeout=3000)
    launch.assert_not_called()


def test_stop_discards_queued_prompt_and_releases_its_wait(panel):
    event = threading.Event()
    panel._task_active = True
    panel._log_queue.put(("__H5P_GRADE_RECOVERY__", ("Activity", ["skip"], event)))
    panel._log_queue.put(("__H5P_WAITING__", ""))
    panel._stop_run()
    panel._poll_log()
    assert event.is_set()
    assert not panel._active_dialogs
    assert panel._h5p_ready_btn.isHidden()
