"""The Checker's Stop button must end a running worker, including at prompts."""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("mode", ["browser_wait", "moodle_prompt", "confirmation"])
def test_checker_stop_cancels_worker_and_restores_button(
    qtbot, monkeypatch, mode
):
    import content_checker
    from panels.checker_panel import CheckerPanel

    started = threading.Event()
    cancelled = threading.Event()

    class FakeChecker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def run(self):
            try:
                if mode == "moodle_prompt":
                    self.kwargs["on_moodle_waiting"]()
                    started.set()
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.kwargs["moodle_ready_event"].wait)
                elif mode == "confirmation":
                    started.set()
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.kwargs["confirm_fn"], "Continue?")
                else:
                    started.set()
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(content_checker, "ContentChecker", FakeChecker)
    mw = MagicMock()
    mw.chromium_ready = True
    mw.load_config.return_value = {}
    panel = CheckerPanel(mw)
    qtbot.addWidget(panel)
    panel._bs_entry.setText("https://example.org/d2l/le/content/123/home")
    panel._start_run()

    qtbot.waitUntil(started.is_set, timeout=3000)
    if mode != "browser_wait":
        qtbot.waitUntil(lambda: bool(panel._active_dialogs), timeout=3000)
        assert panel._run_btn.isEnabled()

    panel._run_btn.click()
    qtbot.waitUntil(cancelled.is_set, timeout=3000)
    qtbot.waitUntil(lambda: panel._run_btn.text() == "Run Check", timeout=3000)
    assert not panel._active_dialogs
    qtbot.waitUntil(lambda: not panel._worker_thread.is_alive(), timeout=3000)
