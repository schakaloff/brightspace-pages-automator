"""Real Chromium checks against intercepted local fixtures; no LMS connection."""
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

sys.path.insert(0, 'src')
from h5p_browser_guide import H5PBrowserGuide


@pytest_asyncio.fixture
async def guided_browser():
    async with async_playwright() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip('Playwright Chromium is not installed')
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=dict(width=1100, height=750))
        guide = H5PBrowserGuide()
        await guide.install(context)
        async def fixture(route):
            if 'embedded.test' in route.request.url:
                html = '<button style="margin:30px" onclick="this.dataset.clicked=1">Insert activity</button>'
            else:
                html = '''<style>body{margin:0;font:16px system-ui}button{margin:80px;padding:15px}
                    dialog button{margin:20px}</style><button id="save" onclick="this.dataset.clicked=1">Save page</button>
                    <input aria-label="Password" type="password"><div id="shadow"></div><dialog>
                    <button id="modal" onclick="this.dataset.clicked=1">Confirm</button></dialog>
                    <script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML =
                    '<iframe style="position:absolute;left:420px;top:180px;width:480px;height:250px;border:4px solid gray" src="http://embedded.test/activity"></iframe>';</script>'''
            await route.fulfill(content_type='text/html', body=html)
        await context.route('**/*', fixture)
        page = await context.new_page()
        await page.goto('http://guide.test/course')
        await page.wait_for_function("!!document.querySelector('#__h5p_browser_guide')?.shadowRoot")
        yield guide, context, page
        await browser.close()


def overlay(selector):
    return f"document.querySelector('#__h5p_browser_guide').shadowRoot.querySelector('{selector}')"


@pytest.mark.asyncio
async def test_cursor_is_visible_before_clicks_and_during_background_waits(guided_browser):
    guide, context, page = guided_browser
    assert await page.evaluate(f"getComputedStyle({overlay('.cursor')}).opacity") == '1'
    await guide.point(page.locator('#save'), 'Save page')
    await page.wait_for_timeout(2700)  # The transient action label may fade; the cursor must stay.
    assert await page.evaluate(f"getComputedStyle({overlay('.bubble')}).opacity") == '0'
    assert await page.evaluate(f"getComputedStyle({overlay('.cursor')}).opacity") == '1'
    await guide.status('Checking existing pages…')
    assert await page.evaluate(f"getComputedStyle({overlay('.cursor')}).opacity") == '1'


@pytest.mark.asyncio
async def test_overlay_does_not_block_real_clicks_or_capture_passwords(guided_browser):
    guide, context, page = guided_browser
    button = page.locator('#save')
    await guide.point(button, 'Save page')
    await button.click()
    assert await button.get_attribute('data-clicked') == '1'
    assert 'Save page' in await page.evaluate(f"{overlay('.bubble')}.textContent")
    await page.evaluate("document.body.insertAdjacentHTML('beforeend', '<label id=cloud-save>Save H5P upload</label>')")
    await page.locator('#cloud-save').evaluate('(label) => label.click()')
    assert await page.evaluate(f"{overlay('.bubble')}.textContent") == 'Clicking Save H5P upload'
    await page.get_by_label('Password').fill('secret-test-value')
    assert 'secret-test-value' not in await page.evaluate(
        "document.querySelector('#__h5p_browser_guide').shadowRoot.textContent")
    # Native modal dialogs remain usable, too.
    await page.locator('dialog').evaluate('(d) => d.showModal()')
    await page.locator('#modal').click()
    assert await page.locator('#modal').get_attribute('data-clicked') == '1'


@pytest.mark.asyncio
async def test_cross_origin_frame_inside_shadow_root_translates_cursor(guided_browser):
    guide, context, page = guided_browser
    frame = page.frames[1]
    button = frame.get_by_role('button', name='Insert activity')
    box = await button.bounding_box()
    await button.evaluate('(b) => b.click()')
    await page.wait_for_function(f"{overlay('.bubble')}.textContent === 'Clicking Insert activity'")
    # Wait for the movement to finish, then compare with Playwright's top-level box.
    await page.wait_for_function(f"{overlay('.cursor')}.getAnimations().length === 0")
    position = await page.evaluate(f"(() => {{ const r={overlay('.cursor')}.getBoundingClientRect(); return {{x:r.x,y:r.y}}; }})()")
    assert position['x'] == pytest.approx(box['x'] + box['width']/2, abs=1)
    assert position['y'] == pytest.approx(box['y'] + box['height']/2, abs=1)
    assert await page.evaluate(f"{overlay('.cursor')}.dataset.kind") == 'hand'
    assert await button.get_attribute('data-clicked') == '1'


@pytest.mark.asyncio
async def test_status_survives_navigation_new_tabs_and_closed_tabs(guided_browser):
    guide, context, page = guided_browser
    await guide.status('Checking the saved page…', activity='Practice A', current=3, total=8)
    await page.goto('http://guide.test/next')
    await page.wait_for_function(f"{overlay('.detail')}?.textContent === 'Practice A'")
    assert await page.evaluate(f"getComputedStyle({overlay('.cursor')}).opacity") == '1'
    assert await page.evaluate(f"{overlay('.count')}.textContent") == 'Activity 3 of 8'
    other = await context.new_page()
    await other.goto('http://guide.test/new-tab')
    await other.wait_for_function(f"{overlay('.message')}?.textContent === 'Checking the saved page…'")
    assert await other.evaluate(f"getComputedStyle({overlay('.cursor')}).opacity") == '1'
    await other.close()
    await guide.status('Brightspace page saved and verified', kind='success')
    assert await page.evaluate(f"{overlay('.message')}.textContent") == 'Brightspace page saved and verified'


@pytest.mark.asyncio
async def test_reduced_motion_and_offscreen_targets(guided_browser):
    guide, context, page = guided_browser
    await page.emulate_media(reduced_motion='reduce')
    await page.reload()
    await guide.point(page.locator('#save'), 'Save page')
    assert await page.evaluate(f"{overlay('.cursor')}.getAnimations().length") == 0
    before = await page.evaluate(f"{overlay('.cursor')}.style.transform")
    await page.evaluate("window.__h5pGuide.point({x:99999,y:99999,label:'Hidden control'})")
    assert await page.evaluate(f"{overlay('.cursor')}.style.transform") == before


@pytest.mark.asyncio
async def test_status_card_moves_away_from_action_target(guided_browser):
    guide, context, page = guided_browser
    await page.locator('#save').evaluate("b => b.style.cssText='position:fixed;left:30px;bottom:35px;margin:0'")
    await guide.point(page.locator('#save'), 'Save page')
    card = await page.evaluate(f"{overlay('.card')}.getBoundingClientRect().top")
    assert card == 20
    await page.locator('#save').click()
    assert await page.locator('#save').get_attribute('data-clicked') == '1'


@pytest.mark.asyncio
async def test_arrow_travels_then_hand_points_at_clickable_targets(guided_browser):
    guide, context, page = guided_browser
    button = page.locator('#save')
    await guide.point(button, 'Save page')
    assert await page.evaluate(f"{overlay('.cursor')}.dataset.kind") == 'hand'
    assert await page.evaluate(f"getComputedStyle({overlay('.cursor-hand')}).display") == 'block'
    # Both shapes keep the same logical hotspot; changing shape cannot shift a click.
    position = await page.evaluate(f"{overlay('.cursor')}.style.transform")
    box = await button.bounding_box()
    await page.evaluate('(p) => window.__h5pGuide.point(p)', dict(
        x=box['x']+box['width']/2, y=box['y']+box['height']/2, kind='arrow', label='Moving'))
    assert await page.evaluate(f"{overlay('.cursor')}.style.transform") == position
    assert await page.evaluate(f"{overlay('.cursor')}.dataset.kind") == 'arrow'
    await guide.point(page.get_by_label('Password'), 'Editing field')
    assert await page.evaluate(f"{overlay('.cursor')}.dataset.kind") == 'arrow'
    await button.evaluate('(el) => el.disabled = true')
    await guide.point(button, 'Disabled control')
    assert await page.evaluate(f"{overlay('.cursor')}.dataset.kind") == 'arrow'
