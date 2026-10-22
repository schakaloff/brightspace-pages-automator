"""Offline browser regression for login/edit-mode documents being replaced."""
import asyncio
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright, Error as PlaywrightError

sys.path.insert(0, 'src')
from content_checker import ContentChecker


@pytest_asyncio.fixture
async def moodle(monkeypatch):
    server = SimpleNamespace(enabled=False, clicks=0, refuse=False, login=False,
                             body_class=True, delayed=False, ajax=False, optimistic_only=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            server.clicks += 1
            time.sleep(.08)
            server.enabled = not server.refuse
            self.send_response(303)
            self.send_header('Location', '/login/index.php' if server.login else '/course/view.php?id=187689&edit=on')
            self.end_headers()

        def do_GET(self):
            if '/login/' in self.path:
                body = '<body>Sign in</body>'
            else:
                checked = 'checked' if server.enabled else ''
                body_class = 'editing' if server.enabled and server.body_class else ''
                change = "document.body.classList.add('editing')" if server.ajax else 'this.form.submit()'
                if server.optimistic_only:
                    change = ''
                form = (f'<form class="editmode-switch-form" action="/editmode.php" method="post">'
                        f'<input id="user-editing-switch" type="checkbox" name="setmode" {checked} '
                        f'onchange="{change}"></form>')
                if server.delayed:
                    import json
                    form = f'<script>setTimeout(()=>document.body.insertAdjacentHTML("beforeend", {json.dumps(form)}), 400)</script>'
                body = f'<body class="{body_class}"><h1>Test course</h1>{form}</body>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body.encode())

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    course_url = f'http://127.0.0.1:{http.server_port}/course/view.php?id=187689'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        page = await browser.new_page()
        checker = ContentChecker.__new__(ContentChecker)
        checker.moodle_url = course_url
        checker.stop_flag = [False]
        checker.log = MagicMock()
        # Shorten only the no-navigation grace period for the AJAX fixture.
        original_expect_navigation = page.expect_navigation
        monkeypatch.setattr(page, 'expect_navigation', lambda **kwargs:
            original_expect_navigation(**{**kwargs, 'timeout': 1000}))
        try:
            yield SimpleNamespace(page=page, server=server, checker=checker, course_url=course_url)
        finally:
            await browser.close()
            await asyncio.to_thread(http.shutdown)
            http.server_close()
            thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize('body_class', [True, False])
async def test_edit_switch_form_navigation_is_verified_without_switching_it_off(moodle, body_class):
    moodle.server.body_class = body_class
    await moodle.page.goto(moodle.course_url)
    assert await moodle.checker._enable_moodle_edit_mode(moodle.page), moodle.checker.log.call_args_list
    assert moodle.server.enabled and moodle.server.clicks == 1
    assert await moodle.page.locator('#user-editing-switch').is_checked()
    # Running discovery again is safe and never toggles editing off.
    assert await moodle.checker._enable_moodle_edit_mode(moodle.page)
    assert moodle.server.clicks == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('when', ['initial_read', 'verification'])
async def test_replaced_document_during_state_read_retries_on_the_current_page(moodle, monkeypatch, when):
    await moodle.page.goto(moodle.course_url)
    evaluate = moodle.page.evaluate
    injected = []

    async def interrupted_read(expression, *args, **kwargs):
        if 'hasToggle' in expression and not injected and (when == 'initial_read' or moodle.server.enabled):
            injected.append(True)
            await moodle.page.reload()
            raise PlaywrightError('Page.evaluate: Execution context was destroyed, most likely because of a navigation')
        return await evaluate(expression, *args, **kwargs)

    monkeypatch.setattr(moodle.page, 'evaluate', interrupted_read)
    assert await moodle.checker._enable_moodle_edit_mode(moodle.page)
    assert injected == [True]
    assert moodle.server.enabled and moodle.server.clicks == 1


@pytest.mark.asyncio
async def test_navigation_error_after_the_action_is_checked_before_any_second_click(moodle, monkeypatch):
    await moodle.page.goto(moodle.course_url)
    locator = moodle.page.locator
    actions = []

    class InterruptedCheckbox:
        first = property(lambda self: self)

        async def check(self, **kwargs):
            actions.append(True)
            await locator('#user-editing-switch').check(**kwargs)
            raise PlaywrightError('Execution context was destroyed, most likely because of a navigation')

    monkeypatch.setattr(moodle.page, 'locator', lambda selector: InterruptedCheckbox())
    assert await moodle.checker._enable_moodle_edit_mode(moodle.page)
    assert actions == [True] and moodle.server.clicks == 1


@pytest.mark.asyncio
async def test_waits_for_a_late_rendered_switch_and_supports_ajax_editing(moodle):
    moodle.server.delayed = True
    moodle.server.ajax = True
    await moodle.page.goto(moodle.course_url)
    assert await moodle.checker._enable_moodle_edit_mode(moodle.page)
    assert await moodle.page.locator('body').get_attribute('class') == 'editing'
    assert moodle.server.clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['refused', 'login'])
async def test_refused_editing_or_lost_login_does_not_confirm_edit_mode(moodle, failure):
    moodle.server.refuse = failure == 'refused'
    moodle.server.login = failure == 'login'
    await moodle.page.goto(moodle.course_url)
    assert not await moodle.checker._enable_moodle_edit_mode(moodle.page)
    assert moodle.server.clicks == 1
    assert any('aborting before Brightspace writes' in str(call) for call in moodle.checker.log.call_args_list)


@pytest.mark.asyncio
async def test_stop_before_edit_mode_does_not_touch_the_switch(moodle):
    await moodle.page.goto(moodle.course_url)
    moodle.checker.stop_flag[0] = True
    with pytest.raises(asyncio.CancelledError):
        await moodle.checker._enable_moodle_edit_mode(moodle.page)
    assert moodle.server.clicks == 0


@pytest.mark.asyncio
async def test_checked_appearance_without_page_update_is_not_confirmed(moodle):
    moodle.server.optimistic_only = True
    await moodle.page.goto(moodle.course_url)
    assert not await moodle.checker._enable_moodle_edit_mode(moodle.page)
    assert await moodle.page.locator('#user-editing-switch').is_checked()
    assert moodle.server.clicks == 0


@pytest.mark.asyncio
async def test_unrelated_script_errors_are_not_retried_or_treated_as_login_races(moodle, monkeypatch):
    await moodle.page.goto(moodle.course_url)
    calls = []
    async def broken_read(*args, **kwargs):
        calls.append(True)
        raise PlaywrightError('ReferenceError: unexpected script error')
    monkeypatch.setattr(moodle.page, 'evaluate', broken_read)
    assert not await moodle.checker._enable_moodle_edit_mode(moodle.page)
    assert len(calls) == 1 and moodle.server.clicks == 0
