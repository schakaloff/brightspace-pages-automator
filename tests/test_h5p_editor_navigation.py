"""Exercise the real navigation code against a delayed Brightspace-style chooser."""
import sys

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, 'src')
from content_checker import ContentChecker
from h5p_handler import H5PHandler


def handler(log=None):
    checker = ContentChecker.__new__(ContentChecker)
    checker.log = log or (lambda *args: None)
    return H5PHandler(checker.log, checker._eval_in_any_frame, None, None,
                      checker._diagnose, None, {})


CHOOSER = '''<d2l-button class="create-new-btn">Create New</d2l-button>
<div id="tiles" hidden><a class="add-material-tile" href="/loadActivity/file/2">Page</a></div>
<div id="editor" hidden>Page editor</div>
<script>
let timer;
document.querySelector('d2l-button').onclick = () => {
    clearTimeout(timer);
    if (!document.querySelector('#tiles').hidden) {
        document.querySelector('#tiles').hidden = true; return;
    }
    timer = setTimeout(() => document.querySelector('#tiles').hidden = false, 650);
};
document.querySelector('a').onclick = e => {
    e.preventDefault(); document.querySelector('#editor').hidden = false;
    document.querySelector('#tiles').hidden = true;
};
</script>'''


@pytest.mark.asyncio
async def test_hidden_tiles_do_not_count_as_an_open_chooser():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(CHOOSER)
            assert not await handler()._tile_chooser_open(page)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_delayed_chooser_is_not_toggled_closed_before_clicking_page():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            # Keep navigation real but serve a local fixture instead of Brightspace.
            delayed = CHOOSER.replace('let timer;', "let timer; const tiles = document.querySelector('#tiles'); tiles.remove();")
            delayed = delayed.replace("if (!document.querySelector('#tiles').hidden)", "if (tiles.isConnected)")
            delayed = delayed.replace("document.querySelector('#tiles').hidden = true; return;", "tiles.remove(); return;")
            delayed = delayed.replace("() => document.querySelector('#tiles').hidden = false", "() => {document.body.append(tiles); tiles.hidden = false;}")
            delayed = delayed.replace("document.querySelector('a').onclick", "tiles.querySelector('a').onclick")
            await page.route('https://bs.example/**', lambda route: route.fulfill(body=delayed, content_type='text/html'))
            navigation = handler()
            opened = []

            async def open_interactives(tab, **kwargs):
                opened.append(await tab.locator('#editor').is_visible())
                return opened[-1]

            marker = object()
            async def find_list(tab): return marker
            navigation.open_interactives = open_interactives
            navigation.find_list_frame = find_list
            assert await navigation.open_editor_and_get_frame(page, 'https://bs.example', '1', '2') is marker
            assert opened == [True]
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('narrow,tile', [
    (False, '<button class="add-material-tile">Page</button>'),
    (False, '<a class="add-material-tile" href="/new-page"><span class="material-tile-text">Page</span><span>Create an HTML page</span></a>'),
    (True, '<a class="add-material-tile" href="/loadActivity/file/2">Page</a>'),
    (True, '<button class="add-material-tile"><span class="material-tile-text">Page</span></button>'),
])
async def test_page_navigation_supports_tile_labels_and_add_menu(narrow, tile):
    """Both responsive layouts must reach the editor before opening H5P."""
    controls = '''<d2l-dropdown-button-subtle text="Add">Add</d2l-dropdown-button-subtle>
        <d2l-menu-item id="create-new-menu-item" hidden>Create New</d2l-menu-item>''' if narrow else '''<d2l-button class="create-new-btn">Create New</d2l-button>'''
    body = '''<style>d2l-button,d2l-dropdown-button-subtle,d2l-menu-item {display:block} [hidden] {display:none!important}</style>
        <div id="host"></div><div id="editor" hidden>Page editor</div>
        <script>
        const root = document.querySelector('#host').attachShadow({mode:'open'});
        root.innerHTML = CONTROLS;
        const tiles = document.createElement('div');
        tiles.id = 'tiles'; tiles.hidden = true; tiles.innerHTML = TILES;
        root.append(tiles);
        const create = root.querySelector('d2l-button, d2l-menu-item');
        const add = root.querySelector('d2l-dropdown-button-subtle');
        if (add) add.onclick = () => {create.hidden = false;};
        let clicks = 0;
        create.onclick = () => {
            clicks++;
            setTimeout(() => {tiles.hidden = false;}, 650);
        };
        tiles.onclick = e => {
            e.preventDefault();
            document.querySelector('#editor').hidden = false;
            tiles.hidden = true;
        };
        window.createClicks = () => clicks;
        </script>'''
    import json
    body = body.replace('CONTROLS', json.dumps(controls)).replace('TILES', json.dumps(tile))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.route('https://bs.example/**', lambda route: route.fulfill(body=body, content_type='text/html'))
            navigation = handler()
            opened = []

            async def open_interactives(tab, **kwargs):
                opened.append(await tab.locator('#editor').is_visible())
                return opened[-1]

            marker = object()
            async def find_list(tab): return marker
            navigation.open_interactives = open_interactives
            navigation.find_list_frame = find_list
            assert await navigation.open_editor_and_get_frame(page, 'https://bs.example', '1', '2') is marker
            assert opened == [True]
            assert await page.evaluate('window.createClicks()') == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_live_page_target_survives_chooser_frame_redraw():
    """The chooser may be removed after readiness but before the next click."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            target = 'https://bs.example/d2l/le/lessons/1/edit/2/loadActivity/file/2?isNew=true&workingCopyId=live-copy'
            body = CHOOSER.replace('/loadActivity/file/2', target)
            async def route(request):
                html = '<div id="editor">Page editor</div>' if '/edit/2/' in request.request.url else body
                await request.fulfill(body=html, content_type='text/html')
            await page.route('https://bs.example/**', route)
            navigation = handler()
            chooser = navigation._tile_chooser_open
            async def redraw_after_ready(tab):
                ready = await chooser(tab)
                if ready:
                    await tab.locator('#tiles').evaluate('(e) => e.remove()')
                return ready
            navigation._tile_chooser_open = redraw_after_ready
            async def open_interactives(tab, **kwargs):
                return await tab.locator('#editor').is_visible()
            marker = object()
            async def find_list(tab): return marker
            navigation.open_interactives = open_interactives
            navigation.find_list_frame = find_list
            assert await navigation.open_editor_and_get_frame(page, 'https://bs.example', '1', '2') is marker
            assert page.url == target
        finally:
            await browser.close()
