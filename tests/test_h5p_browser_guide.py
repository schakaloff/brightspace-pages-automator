"""The optional guide must survive browser changes without changing real actions."""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, 'src')
from h5p_browser_guide import GUIDE_SCRIPT, H5PBrowserGuide


@pytest.mark.asyncio
async def test_disabled_guide_still_sends_app_status_without_touching_browser():
    events = []
    guide = H5PBrowserGuide(events.append, enabled=False)
    await guide.install(object())
    await guide.status('Checking saved pages…', activity='Practice', current=2, total=4)
    await guide.point(object(), 'Save')
    assert events[-1] == dict(stage='watch', message='Checking saved pages…',
        activity='Practice', current=2, total=4, kind='working')
    assert guide.context is None


@pytest.mark.asyncio
async def test_navigation_and_new_tabs_get_latest_state():
    page = SimpleNamespace(evaluate=AsyncMock())
    context = SimpleNamespace(expose_binding=AsyncMock(), add_init_script=AsyncMock(), pages=[page])
    guide = H5PBrowserGuide()
    await guide.install(context)
    context.add_init_script.assert_awaited_once_with(script=GUIDE_SCRIPT)
    await guide.status('Checking saved page…', activity='Practice')
    binding = context.expose_binding.call_args.args[1]
    assert binding(None)['message'] == 'Checking saved page…'
    assert binding(None)['activity'] == 'Practice'
    state = binding(None)
    state['message'] = 'changed'
    assert guide.state['message'] == 'Checking saved page…'


@pytest.mark.asyncio
async def test_closed_page_and_missing_target_cannot_fail_transfer():
    events = []
    page = SimpleNamespace(evaluate=AsyncMock(side_effect=RuntimeError('Page closed')))
    guide = H5PBrowserGuide(events.append)
    guide.context = SimpleNamespace(pages=[page])
    await guide.status('Uploading…')
    locator = SimpleNamespace(bounding_box=AsyncMock(side_effect=RuntimeError('Detached')))
    await guide.point(locator, 'Save')
    assert events[-1]['message'] == 'Uploading…'


@pytest.mark.asyncio
async def test_failed_installation_keeps_app_messages_working():
    events = []
    guide = H5PBrowserGuide(events.append)
    await guide.install(SimpleNamespace(expose_binding=AsyncMock(side_effect=RuntimeError('Unavailable'))))
    await guide.status('Checking saved pages…')
    assert guide.context is None
    assert events[-1]['message'] == 'Checking saved pages…'


@pytest.mark.asyncio
async def test_visual_wait_does_not_swallow_stop_cancellation():
    entered = asyncio.Event()
    async def slow_evaluate(*args):
        entered.set()
        await asyncio.Event().wait()
    guide = H5PBrowserGuide()
    guide.context = SimpleNamespace(pages=[SimpleNamespace(evaluate=slow_evaluate)])
    task = asyncio.create_task(guide.status('Uploading…'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cursor_uses_locator_coordinates_without_clicking():
    page = SimpleNamespace(evaluate=AsyncMock())
    locator = SimpleNamespace(page=page, bounding_box=AsyncMock(
        return_value=dict(x=100, y=200, width=80, height=40)),
        evaluate=AsyncMock(return_value='hand'), click=AsyncMock())
    guide = H5PBrowserGuide()
    guide.context = SimpleNamespace(pages=[page])
    await guide.point(locator, 'Save and Close')
    assert page.evaluate.call_args.args[1] == dict(x=140, y=220, label='Save and Close', kind='hand')
    locator.click.assert_not_called()


def test_watch_setting_and_status_in_h5p_panel(qtbot, monkeypatch):
    from unittest.mock import MagicMock
    import panels.h5p_panel as panel_module
    monkeypatch.setattr(panel_module, 'saved_runs', lambda *args, **kwargs: [])
    window = MagicMock()
    window.load_config.return_value = {'h5p_watch_browser': False}
    panel = panel_module.H5PPanel(window)
    qtbot.addWidget(panel)
    assert not panel._watch_browser_cb.isChecked()
    panel._watch_browser_cb.setChecked(True)
    panel.save_state()
    assert window.save_config.call_args.args[0]['h5p_watch_browser'] is True
    panel._log_queue.put(('__H5P_STATE__', dict(stage='watch', message='Uploading…',
        activity='Practice A', current=2, total=5)))
    panel._poll_log()
    assert panel._status_hint.text() == 'Activity 2 of 5 · Uploading…\nPractice A'
    assert panel._stage == 0  # Display updates must not advance/approve the workflow.
