"""Moodle names must survive package import, editor events and saved-page checks."""
import json
import sys
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, 'src')
from content_checker import ContentChecker
from h5p_handler import H5PHandler
from h5p_titles import named_upload_package, saved_page_id, ensure_owned_page_title
from h5p_runner import _verify_topic_in_module_strict


MOODLE_NAME = 'Hard & Soft Deposits: Flash Cards — Révision (Week 2)'


def handler():
    result = H5PHandler(lambda *args: None, None, None, None, None, None, {})
    result._handle_grade_prompt = AsyncMock(return_value='absent')
    return result


def package(tmp_path, title):
    file = tmp_path / 'filename-without-punctuation.h5p'
    with zipfile.ZipFile(file, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('h5p.json', json.dumps(dict(title=title, mainLibrary='H5P.Flashcards', language='en')))
        archive.writestr('content/content.json', b'{"cards":[{"text":"Original question"}]}')
        archive.writestr('content/images/photo.png', b'original image bytes')
    return file


def test_upload_copy_uses_exact_moodle_name_without_changing_original_or_questions(tmp_path):
    source = package(tmp_path, 'Package title unrelated to Moodle')
    before = source.read_bytes()
    with named_upload_package(source, MOODLE_NAME) as upload:
        assert upload != source and upload.name == source.name
        with zipfile.ZipFile(upload) as result, zipfile.ZipFile(source) as original:
            metadata = json.loads(result.read('h5p.json'))
            assert metadata['title'] == MOODLE_NAME
            assert metadata['mainLibrary'] == 'H5P.Flashcards' and metadata['language'] == 'en'
            for name in ('content/content.json', 'content/images/photo.png'):
                assert result.read(name) == original.read(name)
        temporary = upload
    assert not temporary.exists()
    assert source.read_bytes() == before


def test_already_named_package_is_reused(tmp_path):
    source = package(tmp_path, MOODLE_NAME)
    with named_upload_package(source, MOODLE_NAME) as upload:
        assert upload == source


@pytest.mark.asyncio
async def test_h5p_title_targets_metadata_not_outer_page_or_question_and_commits_change():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('<input name="title" value="Outer page"><iframe></iframe>')
            frame = page.frames[1]
            await frame.set_content('''<div class="h5peditor-form"><div class="tree">
                <div class="field-name-question"><input class="h5peditor-text" maxlength="255" value="Keep this question"></div>
                <div class="field-name-extraTitle"><input id="metadata-title" class="h5peditor-text" maxlength="255" value="Imported package title"></div>
                </div></div><button>Save</button>
                <script>
                window.metadata = {title:'Imported package title'};
                document.querySelector('#metadata-title').addEventListener('change', e => { window.metadata.title=e.target.value; });
                </script>''')
            assert await handler()._set_content_title(page, MOODLE_NAME, attempts=1)
            assert await frame.evaluate('window.metadata.title') == MOODLE_NAME
            assert await page.locator('input[name="title"]').input_value() == 'Outer page'
            assert await frame.locator('.field-name-question input').input_value() == 'Keep this question'
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_question_text_is_never_used_as_a_fallback_title():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('<input name="title" value="Outer page"><input class="h5peditor-text" maxlength="255" value="Question">')
            assert not await handler()._set_content_title(page, MOODLE_NAME, attempts=1)
            assert await page.locator('input.h5peditor-text').input_value() == 'Question'
            assert await page.locator('input[name="title"]').input_value() == 'Outer page'
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reverts', [False, True])
async def test_page_title_is_committed_before_save_and_a_reverted_title_is_not_saved(reverts):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<label for="title">Page Title *</label>
                <input id="title" class="d2l-input" maxlength="150" value="Untitled">
                <button id="save">Save and Close</button><script>
                window.model='Untitled'; window.saved=null;
                const input=document.querySelector('input');
                input.addEventListener('keyup',()=>window.model=input.value);
                if (REVERT) input.addEventListener('blur',()=>{input.value='Untitled';window.model='Untitled';});
                document.querySelector('#save').onclick=()=>window.saved=window.model;
                </script>'''.replace('REVERT', str(reverts).lower()))
            assert await handler().finalize(page, MOODLE_NAME, is_quiz=False) is not reverts
            assert await page.evaluate('window.saved') == (None if reverts else MOODLE_NAME)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_new_page_verification_does_not_accept_punctuation_stripped_or_wrong_case_names():
    page = SimpleNamespace(evaluate=AsyncMock(return_value=[dict(Id=1,
        Title='Hard  Soft Deposits Flash Cards', Url='https://test.h5p.com/content/1')]))
    expected = 'Hard & Soft Deposits Flash Cards'
    assert await _verify_topic_in_module_strict(page, '1', '2', expected)
    assert not await _verify_topic_in_module_strict(page, '1', '2', expected, exact_title=True)
    checker = ContentChecker.__new__(ContentChecker)
    assert not await checker._verify_topic_in_module(page, '1', '2', expected, require_h5p=True, exact_title=True)


@pytest.mark.asyncio
async def test_page_title_waits_for_the_editor_to_reload_after_h5p_insertion():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<h1>New Page</h1><script>
                window.saved=null;
                setTimeout(()=>{
                    const input=document.createElement('input');
                    input.className='d2l-input'; input.maxLength=150; input.value='Imported package title';
                    let title=input.value;
                    input.onchange=()=>title=input.value;
                    const save=document.createElement('button');save.textContent='Save and Close';
                    save.onclick=()=>window.saved=title;
                    document.body.append(input,save);
                },650);
                </script>''')
            assert await handler().finalize(page, MOODLE_NAME, is_quiz=False)
            assert await page.evaluate('window.saved') == MOODLE_NAME
        finally:
            await browser.close()


@pytest.mark.parametrize('saved_url,existing,expected', [
    ('https://bs.example/d2l/le/lessons/1/topics/9', set(), '9'),
    ('https://bs.example/d2l/le/lessons/1/topics/9', {'9'}, None),
    ('https://other.example/d2l/le/lessons/1/topics/9', set(), None),
    ('https://bs.example/d2l/le/lessons/3/topics/9', set(), None),
    ('https://bs.example/d2l/le/lessons/1/units/2', set(), None),
])
def test_only_a_new_page_in_the_current_course_can_be_renamed(saved_url, existing, expected):
    editor = 'https://bs.example/d2l/le/lessons/1/edit/2/loadActivity/file/2?isNew=true'
    assert saved_page_id(editor, saved_url, existing) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('put_succeeds', [True, False])
async def test_finalize_repairs_the_stored_untitled_name_and_records_page_identity_before_renaming(put_succeeds):
    """Reproduce the live failure: the input looks right but the server saves Untitled."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            state = dict(created=False, Title='Untitled')
            events, payloads = [], []
            original = dict(Id=9, Title='Untitled', ShortTitle='Original short title', Type=1, TopicType=1,
                Url='/content/enforced/1/Untitled.html', IsHidden=True, IsLocked=True,
                StartDate='2026-10-01T00:00:00Z', EndDate=None, DueDate='2026-10-30T00:00:00Z',
                Description=dict(Html='<p>Keep this description</p>'), OpenAsExternalResource=False)
            async def route(request):
                url = request.request.url
                if '/content/modules/2/structure/' in url:
                    data = [dict(Id=9, Title=state['Title'])] if state['created'] else []
                    await request.fulfill(json=data)
                elif '/content/topics/9/file' in url:
                    await request.fulfill(body='<iframe src="https://test.h5p.com/content/77"></iframe>', content_type='text/html')
                elif '/content/topics/9' in url:
                    if request.request.method == 'PUT':
                        payload = request.request.post_data_json
                        payloads.append(payload)
                        events.append(('rename', '9'))
                        if not put_succeeds:
                            await request.fulfill(status=500, body='Rename failed')
                            return
                        state['Title'] = payload['Title']
                    await request.fulfill(json={**original, 'Title':state['Title']})
                elif '/topics/9' in url:
                    state['created'] = True
                    await request.fulfill(body='<h1>Saved page</h1>', content_type='text/html')
                else:
                    await request.fulfill(body='''<input class="d2l-input" maxlength="150" value="Untitled">
                        <button onclick="location.href='/d2l/le/lessons/1/topics/9'">Save and Close</button>''', content_type='text/html')
            await page.route('https://bs.example/**', route)
            await page.goto('https://bs.example/d2l/le/lessons/1/edit/2/loadActivity/file/2?isNew=true')
            await page.evaluate("localStorage.setItem('XSRF.Token','fixture-token')")
            transfer = handler()
            transfer._on_page_saved = lambda topic_id: events.append(('record', topic_id))
            assert await transfer.finalize(page, MOODLE_NAME, is_quiz=False) is put_succeeds
            assert events == [('record', '9'), ('rename', '9')]
            assert state['Title'] == (MOODLE_NAME if put_succeeds else 'Untitled')
            assert payloads[0]['Url'] == original['Url']
            assert payloads[0]['IsHidden'] is True and payloads[0]['IsLocked'] is True
            assert payloads[0]['StartDate'] == original['StartDate'] and payloads[0]['DueDate'] == original['DueDate']
            assert payloads[0]['ShortTitle'] == original['ShortTitle']
            assert payloads[0]['Description'] == dict(Content='<p>Keep this description</p>', Type='Html')
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_owned_title_repair_never_renames_a_page_outside_the_recorded_unit():
    api = SimpleNamespace(list_structure=AsyncMock(return_value=[dict(Id=88)]), rename_topic=AsyncMock())
    with pytest.raises(RuntimeError, match='recorded destination unit'):
        await ensure_owned_page_title(api, '9', MOODLE_NAME)
    api.rename_topic.assert_not_called()
