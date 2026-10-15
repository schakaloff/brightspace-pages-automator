"""Exercise the real Kaltura scanner against Moodle's summary/full course views."""
from types import SimpleNamespace
import sys
from urllib.parse import urlsplit, parse_qs

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, 'src')
import kaltura_categorizer as kaltura

COURSE = 'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972'


@pytest.mark.parametrize('url', [
    'https://www.youtube.com/watch?v=Il3T7plDnSQ&feature=share',
    'https://www.youtube-nocookie.com/embed/Il3T7plDnSQ?rel=0',
    'https://youtu.be/Il3T7plDnSQ',
])
def test_youtube_sources_have_a_provider_specific_id(url):
    assert kaltura._youtube_watch_url(url) == 'https://www.youtube.com/watch?v=Il3T7plDnSQ'


@pytest.mark.parametrize('url', [None, '', 'https://youtube.com.example/embed/Il3T7plDnSQ', 'https://www.youtube.com/channel/Il3T7plDnSQ'])
def test_unrelated_urls_are_not_reported_as_youtube_videos(url):
    assert kaltura._youtube_watch_url(url) is None


def test_selected_section_url_is_normalized_to_the_whole_course():
    assert kaltura._whole_moodle_course_url(COURSE + '&section=4#section-4') == COURSE
    assert kaltura._whole_moodle_course_url(COURSE.replace('/course/view.php', '/enrol/index.php')) == COURSE


def test_section_ids_cannot_be_mistaken_for_course_ids():
    with pytest.raises(ValueError, match='whole Moodle course URL'):
        kaltura._whole_moodle_course_url(COURSE.replace('/course/view.php', '/course/section.php'))


@pytest.mark.asyncio
async def test_summary_without_edit_permissions_reports_incomplete_instead_of_empty():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('<li class="section section-summary"><h3>Videos</h3>Activities: 3</li>')
            with pytest.raises(RuntimeError, match='scan is incomplete'):
                await kaltura.KalturaCategorizer()._load_full_course_listing(page, COURSE, lambda *a: None)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_full_scan_finds_hidden_subsection_videos_and_book_chapters(monkeypatch, tmp_path):
    editing = [False]
    logs, visited = [], []
    async with async_playwright() as pw:
        async def route(request):
            url = urlsplit(request.request.url)
            query = parse_qs(url.query)
            visited.append(url.path)
            if 'kaltura.example' in url.hostname:
                body = '<title>Safety briefing</title>'
            elif url.path == '/course/view.php':
                if query.get('edit') == ['1']: editing[0] = True
                if not editing[0]:
                    body = '''<input type="checkbox" name="setmode" onclick="location.href='?id=166972&edit=1'">
                        <li class="section"><h3 class="sectionname">Welcome</h3>
                        <li class="activity"><a href="/mod/page/view.php?id=10">Start Here</a></li></li>
                        <li class="section section-summary"><h3>Radiography</h3>Activities: 3</li>'''
                else:
                    body = '''<body class="editing"><ul><li class="section"><h3 class="sectionname">Radiography</h3>
                      <ul><li class="section"><h3 class="sectionname">Demo videos</h3><ul>
                      <li class="activity"><a href="/mod/kalvidres/view.php?id=11">Demonstration</a></li>
                      <li class="activity"><a href="/mod/kalvidassign/view.php?id=13">Kaltura submission video</a></li>
                      <li class="activity"><a href="/mod/kalvidassign/view.php?id=14">Perio Dressing &amp; Suture Removal Demo</a></li>
                      <li class="activity"><a href="/mod/book/view.php?id=12">Video book</a></li></ul></li></ul></li>
                      <li class="section"><h3 class="sectionname">Safety</h3><ul><li class="activity">
                      <iframe src="https://kaltura.example/embed/entryid/1_label"></iframe></li></ul></li></ul></body>'''
            elif url.path == '/mod/kalvidres/view.php':
                body = '<title>BW Demo</title><iframe id="contentframe" src="https://kaltura.example/embed/entryid%2F0_demo"></iframe>'
            elif url.path == '/mod/kalvidassign/view.php':
                if query.get('id') == ['13']:
                    body = '''<h1>Kaltura assignment demo</h1><div id="intro">
                        <iframe src="https://kaltura.example/embed/entryId=1_assignment"></iframe></div>
                        <div id="submissions"><iframe src="https://kaltura.example/embed/entryId=1_student_submission"></iframe></div>'''
                else:
                    body = '''<h1>Perio Dressing &amp; Suture Removal Demo</h1><main id="intro">
                    <div data-setup-lazy='{"sources":[{"type":"video/youtube","src":"https://www.youtube.com/watch?v=Il3T7plDnSQ"}]}'></div>
                    <iframe src=""></iframe></main>'''
            elif url.path == '/mod/book/view.php':
                if query.get('chapterid') == ['2']:
                    body = '<h1>Chapter two</h1><script>const entryId="1_second";</script>'
                else:
                    body = '''<h1>Chapter one</h1><iframe src="https://kaltura.example/embed/entryId=1_first"></iframe>
                              <div class="book_toc"><a href="?id=12&chapterid=2">Next chapter</a></div>'''
            else:
                body = '<h1>Start Here</h1>'
            await request.fulfill(body=body, content_type='text/html')

        async def launch(**kwargs):
            browser = await pw.chromium.launch(channel='msedge', headless=True)
            original_context = browser.new_context
            async def new_context(**options):
                context = await original_context(**options)
                await context.route('**/*', route)
                return context
            monkeypatch.setattr(browser, 'new_context', new_context)
            return browser

        class Runtime:
            async def __aenter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch=launch))
            async def __aexit__(self, *args): pass

        monkeypatch.setattr(kaltura, 'async_playwright', Runtime)
        monkeypatch.setattr(kaltura, 'MOODLE_SESSION_FILE', str(tmp_path / 'no-session.json'))
        results = await kaltura.KalturaCategorizer().scan_moodle_course(
            COURSE, log_fn=lambda message, tag='dim': logs.append(message))

    by_id = {r['entry_id']: r for r in results}
    assert set(by_id) == {'1_label', '0_demo', '1_first', '1_second', '1_assignment'}
    assert by_id['0_demo']['section_name'] == 'Demo videos'
    assert by_id['1_second']['section_name'] == 'Demo videos'
    assert by_id['1_assignment']['section_name'] == 'Demo videos'
    assert by_id['1_label']['section_name'] == 'Safety'
    assert by_id['1_label']['name'] == 'Safety briefing'
    assert editing[0] and '/mod/book/view.php' in visited
    assert any('Course coverage:' in line for line in logs)
    assert any('YouTube video: Perio Dressing & Suture Removal Demo' in line for line in logs)
    assert any('https://www.youtube.com/watch?v=Il3T7plDnSQ' in line for line in logs)
    assert any('Also identified 1 YouTube video(s)' in line for line in logs)


@pytest.mark.asyncio
async def test_genuinely_empty_full_course_is_a_successful_empty_scan():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('<li class="section"><h3 class="sectionname">Welcome</h3></li>')
            logs = []
            await kaltura.KalturaCategorizer()._load_full_course_listing(page, COURSE, lambda *args: logs.append(args))
            assert '1 sections, 0 activity rows' in logs[-1][0]
        finally:
            await browser.close()
