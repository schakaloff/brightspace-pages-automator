import sys
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, 'src')
import scorm_handler
from scorm_handler import SCORMHandler


@pytest.mark.asyncio
async def test_picker_uses_the_correct_course_and_module_without_stacking_dialogs():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.route('https://learn.okanagancollege.ca/**', lambda route: route.fulfill(
                body='<button id="upload-button">Bulk Upload</button>', content_type='text/html'))
            frame = await SCORMHandler(page, '1', lambda *a: None).open_picker('2')
            assert '/addcontent/' in frame.url
            assert 'oId=2&ou=1' in frame.url
            assert frame is page.main_frame and len(page.frames) == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_original_moodle_zip_download_uses_file_details_and_never_saves_settings(tmp_path):
    from scorm_packages import SCORMRun, download_package
    from zipfile import ZipFile
    BS = 'https://learn.okanagancollege.ca/d2l/le/lessons/23840'
    MOODLE = 'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972'
    with ZipFile(tmp_path / 'original.zip', 'w') as package:
        package.writestr('imsmanifest.xml', '<manifest><metadata><schema>ADL SCORM</schema></metadata></manifest>')
    archive = (tmp_path / 'original.zip').read_bytes()
    run = SCORMRun.open(BS, MOODLE, tmp_path / 'runs')
    row = dict(key='42', href='https://mymoodle.okanagan.bc.ca/mod/scorm/view.php?id=42', name='Practice', section='Unit')
    run.data['activities'] = [row]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            async def route(request):
                url = request.request.url
                if '/mod/scorm/' in url:
                    body = '<a href="/course/modedit.php?update=42">Settings</a>'
                elif '/modedit.php' in url:
                    body = '''<div id="fitem_id_packagefile"></div>
                    <button id="save" onclick="window.saved=true">Save</button><script>
                    setTimeout(()=>document.querySelector('#fitem_id_packagefile').innerHTML=
                    `<div class="fp-file" onclick="document.querySelector('#details').hidden=false">original.zip</div>`, 200);
                    </script><div id="details" hidden><button class="fp-file-download" onclick="location.href='/original.zip'">Download</button></div>'''
                else:
                    await request.fulfill(body=archive, headers={'Content-Type':'application/zip',
                        'Content-Disposition':'attachment; filename="original.zip"'})
                    return
                await request.fulfill(body=body, content_type='text/html')
            await page.route('https://mymoodle.okanagan.bc.ca/**', route)
            result, cached = await download_package(page, run, row)
            assert not cached and result.read_bytes() == archive
            assert not await page.evaluate('!!window.saved')
            # A cached rerun must not reopen Settings or trigger another download.
            await page.goto('about:blank')
            assert (await download_package(page, run, row)) == (result, True)
            assert page.url == 'about:blank'
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('obstructed', [False, True])
async def test_existing_library_package_is_added_ungraded_and_verified(monkeypatch, obstructed):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<input type="search"><div class="search-result"><a class="title">dental</a>
                <input type="radio" name="selected-result"></div><button id="upload-button">Bulk Upload</button>
                <button onclick="document.querySelector('#settings').hidden=false">Next</button>
                <div id="settings" hidden><input type="radio" id="create-grade-item-yes" name="grade-item" checked>
                <input type="radio" id="create-grade-item-no" name="grade-item">
                <input type="radio" id="latest-version" name="version" checked>
                <input type="radio" id="this-version" name="version">
                <button onclick="window.added=document.querySelector('#create-grade-item-no').checked && document.querySelector('#this-version').checked">Add</button></div>''')
            if obstructed:
                await page.evaluate("()=>{const e=document.createElement('div');e.style='position:fixed;inset:0;z-index:99';document.body.append(e)}")
            handler = SCORMHandler(page, '1', lambda *a: None)
            handler.open_picker = AsyncMock(return_value=page.main_frame)
            async def topics(*args):
                return [dict(Id=7, Title='dental', Url='d2l:brightspace:content:tenant:scorm:id/latest')] if await page.evaluate('!!window.added') else []
            monkeypatch.setattr(scorm_handler, 'module_topics', topics)
            checkpoints = []
            result = await handler.transfer(dict(module_id='2', name='Practice', filename='dental.zip'), 'unused.zip',
                                            lambda **details: checkpoints.append(details))
            assert result['Id'] == 7
            assert checkpoints == [dict(upload='confirmed'), dict(insertion='pending'), dict(insertion='confirmed', topic_id='7')]
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_label', ['Save', 'Next'])
async def test_new_zip_upload_is_confirmed_before_adding_its_topic(monkeypatch, tmp_path, finish_label):
    file = tmp_path / 'dental.zip'
    file.write_bytes(b'fixture package')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<div id="library"><input type="search"><div id="results"></div>
            <button id="upload-button" onclick="document.querySelector('#library').hidden=true;document.querySelector('#uploader').hidden=false">Bulk Upload</button>
            <button onclick="document.querySelector('#library').hidden=true;document.querySelector('#settings').hidden=false">Next</button></div>
            <div id="uploader" hidden><input id="file-select" type="file" onchange="window.files=this.files.length;setTimeout(()=>document.querySelector('#finish').disabled=false,200)">
            <button id="finish" disabled onclick="document.querySelector('#uploader').hidden=true;document.querySelector('#library').hidden=false;
            document.querySelector('#results').innerHTML='<div class=search-result><a class=title>dental</a><input type=radio name=selected-result></div>'">FINISH</button></div>
            <div id="settings" hidden><input id="create-grade-item-no" type="radio"><input id="this-version" type="radio">
            <button onclick="window.added=document.querySelector('#create-grade-item-no').checked && document.querySelector('#this-version').checked">Add</button></div>'''.replace('FINISH', finish_label))
            handler = SCORMHandler(page, '1', lambda *a: None)
            handler.open_picker = AsyncMock(return_value=page.main_frame)
            async def topics(*args):
                return [dict(Id=77, Title='dental', Url='d2l:brightspace:content:tenant:scorm:id/version')] if await page.evaluate('!!window.added') else []
            monkeypatch.setattr(scorm_handler, 'module_topics', topics)
            checkpoints = []
            result = await handler.transfer(dict(module_id='2', name='Practice', filename='dental.zip'), file,
                                            lambda **details: checkpoints.append(details))
            assert await page.evaluate('window.files') == 1
            assert result['Id'] == 77
            assert checkpoints == [dict(upload='pending'), dict(upload='confirmed'), dict(insertion='pending'),
                                   dict(insertion='confirmed', topic_id='77')]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_unknown_upload_is_not_repeated():
    handler = SCORMHandler(None, '1', lambda *a: None)
    handler.open_picker = AsyncMock(return_value=object())
    handler.find_package = AsyncMock(return_value=None)
    handler._click = AsyncMock()
    with pytest.raises(RuntimeError, match='unknown result'):
        await handler.transfer(dict(module_id='2', filename='dental.zip', upload='pending'), 'dental.zip', lambda **kw: None)
    handler._click.assert_not_called()


def test_media_card_opens_scorm_and_carries_course_urls(qtbot, monkeypatch):
    from gui import MainWindow
    for method in ('_set_window_icon', '_load_api_key', '_start_chromium_check', '_start_update_check', '_show_welcome_if_needed'):
        monkeypatch.setattr(MainWindow, method, lambda self: None)
    monkeypatch.setattr(MainWindow, 'load_config', lambda self: {'welcomed': True})
    monkeypatch.setattr(MainWindow, 'save_config', lambda *a: None)
    monkeypatch.setattr(MainWindow, 'closeEvent', lambda self, event: event.accept())
    window = MainWindow()
    qtbot.addWidget(window)
    window._checker._bs_entry.setText('https://learn.okanagancollege.ca/d2l/le/lessons/23840')
    window._checker._moodle_entry.setText('https://mymoodle.okanagan.bc.ca/course/view.php?id=166972')
    window._media_hub.buttons['scorm'].click()
    assert window._stack.currentWidget() is window._scorm
    assert window._scorm._bs_entry.text() == window._checker._bs_entry.text()
    assert window._scorm._moodle_entry.text() == window._checker._moodle_entry.text()
    assert not window._scorm._transfer_all.isEnabled()


def test_transferring_one_preserves_the_remaining_review_choices(qtbot, monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from PySide6.QtCore import Qt
    from unittest.mock import AsyncMock
    from panels.scorm_panel import SCORMPanel
    from scorm_packages import SCORMRun
    import scorm_runner

    panel = SCORMPanel(SimpleNamespace(load_config=lambda: {}, chromium_ready=True))
    qtbot.addWidget(panel)
    run = SCORMRun.open('https://learn.okanagancollege.ca/d2l/le/lessons/23840',
        'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972', tmp_path)
    modules = [dict(id='1', title='Source unit'), dict(id='2', title='Chosen unit')]
    run.capture([dict(key=str(i), name=f'Activity {i}', section='Source unit', href='') for i in (10, 20)], modules)
    for row in run.data['activities']:
        row.update(filename='course.zip', sha256='fixture', state='Ready')
        run.folder(row).mkdir(parents=True)
        (run.folder(row) / 'course.zip').write_bytes(b'fixture')
    panel._run, panel._modules = run, modules
    panel._show_rows()
    panel._table.cellWidget(1, 2).setCurrentIndex(2)
    factories = []
    monkeypatch.setattr(panel, '_start', factories.append)
    backend = AsyncMock(return_value=run)
    monkeypatch.setattr(scorm_runner, 'transfer_scorm', backend)
    panel._transfer(one=True)
    asyncio.run(factories[0]())
    assert backend.call_args.args[2] == [('10', '1')]
    run.data['activities'][0]['state'] = 'Done + verified'
    panel._queue.put(('result', run))
    panel._poll()
    assert panel._table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    assert panel._table.item(1, 0).checkState() == Qt.CheckState.Checked
    assert panel._table.cellWidget(1, 2).currentData() == '2'
