"""Exercise the production insertion flow with nested Brightspace dialogs."""
import sys
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, 'src')
from content_checker import ContentChecker
from h5p_handler import H5PHandler


def handler():
    checker = ContentChecker.__new__(ContentChecker)
    checker.log = lambda *args: None
    return H5PHandler(checker.log, checker._eval_in_any_frame,
                      checker._auto_dismiss, None, checker._diagnose, None, {})


@pytest.mark.asyncio
@pytest.mark.parametrize('early', [False, True])
@pytest.mark.parametrize('shadow,graded', [(False, False), (True, False), (False, True), (True, True)])
async def test_insertion_reaches_editor_through_iframe_footer_and_grade_choice(shadow, graded, early):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            outer = '<iframe title="Library" src="https://fixture.h5p.com/content"></iframe><iframe title="Insert Stuff" src="https://bs.example/dialog"></iframe>'
            library = '<button class="d2l-button" onclick="window.wrongClick=true">Insert</button>'
            dialog = '''<div id="host"></div><script>
            const root = SHADOW ? document.querySelector('#host').attachShadow({mode:'open'}) : document.querySelector('#host');
            root.innerHTML = '<button class="d2l-button" disabled>Insert</button>';
            const button = root.querySelector('button');
            setTimeout(() => button.disabled=false, 650);
            button.onclick = () => {
                root.innerHTML = '<button id="grade">GRADE</button>';
                root.querySelector('#grade').onclick = () => {
                    root.innerHTML = '<d2l-button data-dialog-action="insert">Insert</d2l-button>';
                    const final = root.querySelector('d2l-button');
                    final.style.display = 'inline-block';
                    final.onclick = () => {root.innerHTML='<div id="embedded">Activity embedded in editor</div>';};
                };
            };
            if (EARLY) button.onclick();
            </script>'''.replace('SHADOW', str(shadow).lower()).replace('EARLY', str(early).lower()).replace('GRADE', 'Add Grade Item' if graded else 'Proceed Without Grade Item')

            async def route(request):
                url = request.request.url
                body = library if 'h5p.com' in url else dialog if url.endswith('/dialog') else outer
                await request.fulfill(body=body, content_type='text/html')

            await page.route('https://**/*', route)
            await page.goto('https://bs.example/page')
            navigation = handler()
            navigation._click_insert_for_title = AsyncMock(return_value='Practice')
            assert await navigation.insert_from_list(page, object(), 'Practice', graded)
            frame = page.frame(url='https://bs.example/dialog')
            assert await frame.locator('#embedded').is_visible()
            assert navigation._grade_choice_completed
            assert not await page.frame(url='https://fixture.h5p.com/content').evaluate('!!window.wrongClick')
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_disabled_or_hidden_footer_is_not_reported_as_clicked():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<button class="d2l-button" disabled>Insert</button>
                <d2l-button data-dialog-action="insert" hidden>Insert</d2l-button>''')
            assert not await handler()._click_insert_dialog_footer(page, attempts=1)
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('completes', [True, False])
async def test_finalize_waits_for_save_to_leave_the_editor(completes):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            target = 'https://bs.example/d2l/le/lessons/1/units/2'
            body = '''<input class="d2l-input" maxlength="150">
                <button onclick="SAVE">Save and Close</button>'''.replace(
                    'SAVE', f"setTimeout(() => location.href='{target}', 3000)" if completes else 'void 0')
            await page.route('https://bs.example/**', lambda route: route.fulfill(body=body, content_type='text/html'))
            await page.route('**/content/modules/2/structure/', lambda route: route.fulfill(body='[]', content_type='application/json'))
            await page.goto('https://bs.example/d2l/le/lessons/1/edit/2/loadActivity/file/2?isNew=true')
            navigation = handler()
            navigation._grade_choice_completed = True
            navigation._auto_dismiss = AsyncMock(return_value=False)
            if not completes:
                page.wait_for_url = AsyncMock(side_effect=TimeoutError('Save did not complete'))
            assert await navigation.finalize(page, 'Practice', is_quiz=False) is completes
            if completes:
                assert page.url == target
            else:
                page.wait_for_url.assert_awaited_once()
        finally:
            await browser.close()
