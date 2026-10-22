"""Saved H5P runs survive interruption, course switches and application restarts."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, 'src')
from h5p_progress import H5PRun, saved_runs


def activity(file, name='Practice', module='2'):
    return dict(file=file, name=name, belongs_to_course=True, should_grade=False,
                moodle_section='Week 1', bs_module_title='Week 1', bs_module_id=module)


def test_saved_courses_have_independent_files_and_progress_after_restart(tmp_path):
    file = tmp_path / 'Practice.h5p'
    file.write_bytes(b'course A')
    first = H5PRun.create('https://bs.example/d2l/home/1', 'https://moodle.example/course/view.php?id=10', '1', directory=tmp_path)
    first.capture_assignments([activity(file)])
    first.checkpoint(activity(file), 'upload', 'confirmed')
    file.write_bytes(b'course B')
    second = H5PRun.create('https://bs.example/d2l/home/3', 'https://moodle.example/course/view.php?id=30', '3', directory=tmp_path)
    second.capture_assignments([activity(file)])
    restored = H5PRun.load(first.data['id'], tmp_path)
    modules = [dict(kind='MODULE', id='2', title='Week 1')]
    assert restored.assignments(modules)[0]['file'].read_bytes() == b'course A'
    assert second.assignments(modules)[0]['file'].read_bytes() == b'course B'
    assert restored.counts() == (1, 0, 1) and second.counts() == (0, 0, 1)
    assert len(saved_runs(tmp_path)) == 2
    with pytest.raises(ValueError, match='unit is missing'): restored.assignments([])


def test_progress_keeps_only_resource_urls_and_h5p_activity_fields(tmp_path):
    run = H5PRun.create('https://bs.example/d2l/home/1?token=secret', 'https://moodle.example/course/view.php?id=10&sesskey=secret', '1', directory=tmp_path)
    run.capture_moodle([
        {'type': 'SECTION', 'name': 'Week 1', 'password': 'secret'},
        {'type': 'EXTERNAL', 'name': 'Practice', 'hint': 'h5p', 'href': 'https://moodle.example/mod/h5pactivity/view.php?id=5&sesskey=secret', 'html': 'secret'},
        {'type': 'PAGE', 'name': 'Other content', 'html': 'secret'},
    ])
    assert 'secret' not in run.path.read_text(encoding='utf-8')
    assert len(run.data['moodle_items']) == 2
    with pytest.raises(ValueError): H5PRun.load('../bad', tmp_path)


def test_bad_saved_file_is_ignored_and_ambiguous_activity_names_stop_before_copying(tmp_path):
    (tmp_path / ('a' * 32 + '.json')).write_text('{broken', encoding='utf-8')
    assert saved_runs(tmp_path) == []
    file = tmp_path / 'Practice.h5p'
    file.write_bytes(b'cache')
    run = H5PRun.create('https://bs.example/d2l/home/1', 'https://moodle.example/course/view.php?id=10', '1', directory=tmp_path)
    with pytest.raises(ValueError, match='distinct names'):
        run.capture_assignments([activity(file), activity(file, 'Practice!', '3')])
    assert run.data['activities'] == []


@pytest.mark.asyncio
async def test_empty_course_does_not_open_the_cloud_editor(tmp_path):
    from h5p_handler import H5PHandler
    run = H5PRun.create('https://bs.example/d2l/home/1', 'https://moodle.example/course/view.php?id=10', '1', directory=tmp_path)
    items = [{'type': 'SECTION', 'name': 'Week 1'}]
    run.capture_moodle(items)
    handler = H5PHandler(lambda *args: None, None, None, None, None, None, {})
    handler.progress = run
    context = SimpleNamespace(new_page=AsyncMock())
    await handler.embed_in_brightspace(context, object(), items, [], 'https://bs', '1')
    context.new_page.assert_not_awaited()
    assert '0 H5P activities' in run.label()


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupted_stage', ['upload', 'insertion'])
async def test_resume_after_course_switch_rechecks_lost_save_and_skips_moodle(tmp_path, monkeypatch, interrupted_stage):
    import browser as browser_module
    import h5p_handler
    import h5p_runner

    downloads = tmp_path / 'downloads' / 'h5p'
    downloads.mkdir(parents=True)
    for name in ('Activity A', 'Activity B'): (downloads / (name + '.h5p')).write_bytes(name.encode())
    monkeypatch.setattr(h5p_handler, '__file__', str(tmp_path / 'src' / 'h5p_handler.py'))
    cloud, pages = set(), set()
    uploads, saves, scrapes = [], [], []
    disconnected = [False]
    should_interrupt = [True]

    class Checker:
        def __init__(self, **kwargs):
            self.stop_flag = [False]
            self.course_id = h5p_runner._extract_course_id(kwargs['bs_url'])
            self.name = 'Activity A' if self.course_id == '1' else 'Activity B'
            self._h5p = h5p_handler.H5PHandler(kwargs['log'], None, None, self.confirm, None, None, {})
            self._h5p.open_editor_and_get_frame = AsyncMock(return_value=object())
            self._h5p.find_list_frame = AsyncMock(return_value=object())
            self._h5p._collect_cloud_titles = self.scan_cloud
            self._h5p.upload_one = self.upload
            self._h5p.insert_from_list = AsyncMock(return_value=True)
            self._h5p.finalize = self.save_page

        async def confirm(self, message): return True
        async def _fetch_bs_toc(self, *args): return [dict(kind='MODULE', id='2', title='Week 1')]
        async def _scrape_moodle(self, *args):
            scrapes.append(self.course_id)
            return [{'type': 'SECTION', 'name': 'Week 1'},
                    {'type': 'EXTERNAL', 'name': self.name, 'hint': 'h5p', 'href': f'https://moodle.example/mod/h5pactivity/view.php?id={self.course_id}'}]
        async def _ensure_h5p_destination_units(self, *args): return args[-1]
        async def scan_cloud(self, *args):
            self._h5p._last_cloud_scan_complete = True
            return list(cloud)
        async def upload(self, tab, frame, file, name, **kwargs):
            uploads.append(name)
            cloud.add(name)
            if name == 'Activity A' and should_interrupt[0] and interrupted_stage == 'upload':
                should_interrupt[0] = False
                raise asyncio.CancelledError()  # Save reached the server; response was lost.
            return True
        async def save_page(self, tab, name, **kwargs):
            saves.append((self.course_id, name))
            pages.add((self.course_id, name))
            if name == 'Activity A' and should_interrupt[0] and interrupted_stage == 'insertion':
                should_interrupt[0] = False
                raise asyncio.CancelledError()
            return True

    async def verify(page, course_id, module_id, name, *, exact_title=False):
        if disconnected[0]: raise RuntimeError('Could not verify destination')
        return (course_id, name) in pages

    browser = MagicMock()
    browser.close = AsyncMock()
    driver = MagicMock(stop=AsyncMock())
    page = SimpleNamespace(evaluate=AsyncMock(return_value={'Name': 'Saved course'}))
    context = SimpleNamespace(new_page=AsyncMock(return_value=SimpleNamespace(close=AsyncMock(), wait_for_timeout=AsyncMock())))
    monkeypatch.setattr(h5p_runner, 'ContentChecker', Checker)
    monkeypatch.setattr(h5p_runner, '_verify_topic_in_module_strict', verify)
    monkeypatch.setattr(browser_module, 'launch_browser', AsyncMock(return_value=(driver, browser, context, page)))
    monkeypatch.setattr(browser_module, 'wait_for_login', AsyncMock())

    async def run(course, resume=None):
        await h5p_runner.run_h5p_only(f'https://bs.example/d2l/home/{course}', f'https://moodle.example/course/view.php?id={course}',
                                    lambda *args: None, resume_id=resume, progress_directory=tmp_path / 'runs')

    with pytest.raises(asyncio.CancelledError): await run('1')
    first = saved_runs(tmp_path / 'runs')[0]
    assert first.data['status'] == 'paused'
    assert first.data['activities'][0][interrupted_stage] == 'pending'
    await run('3')  # Work on another course before returning to A.
    assert len(saved_runs(tmp_path / 'runs')) == 2
    disconnected[0] = True
    before = (list(uploads), list(saves))
    with pytest.raises(RuntimeError, match='verify destination'): await run('1', first.data['id'])
    assert (uploads, saves) == before  # A failed read cannot authorize another write.
    disconnected[0] = False
    await run('1', first.data['id'])
    restored = H5PRun.load(first.data['id'], tmp_path / 'runs')
    assert restored.data['status'] == 'complete' and restored.counts() == (1, 1, 0)
    assert scrapes == ['1', '3'] and uploads.count('Activity A') == 1
    assert saves.count(('1', 'Activity A')) == 1


@pytest.mark.asyncio
async def test_resume_verifies_html_h5p_pages_and_stops_when_the_read_is_uncertain():
    from h5p_runner import _verify_topic_in_module_strict
    class Page:
        broken = False
        async def evaluate(self, script, args):
            if '/structure/' in script:
                return [{'Id': 9, 'Title': 'Practice', 'Url': '/content/enforced/1/page.html'}]
            if self.broken: raise RuntimeError('Network unavailable')
            return '<iframe src="https://ocedtech.h5p.com/content/9/embed"></iframe>'
    page = Page()
    assert await _verify_topic_in_module_strict(page, '1', '2', 'Practice')
    page.broken = True
    with pytest.raises(RuntimeError, match='No new page will be created'):
        await _verify_topic_in_module_strict(page, '1', '2', 'Practice')
    missing = SimpleNamespace(evaluate=AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match='No new page will be created'):
        await _verify_topic_in_module_strict(missing, '1', '2', 'Practice')


def test_resume_selector_restores_each_course_and_its_settings(qtbot, tmp_path, monkeypatch):
    import h5p_progress
    from panels.h5p_panel import H5PPanel
    monkeypatch.setattr(h5p_progress, 'RUNS_DIR', tmp_path)
    first = H5PRun.create('https://bs.example/d2l/home/1', 'https://moodle.example/course/view.php?id=10', '1', True, tmp_path)
    second = H5PRun.create('https://bs.example/d2l/home/3', 'https://moodle.example/course/view.php?id=30', '3', False, tmp_path)
    mw = MagicMock()
    mw.chromium_ready = True
    mw.load_config.return_value = {}
    panel = H5PPanel(mw)
    qtbot.addWidget(panel)
    panel.show()
    started = []
    panel._start_run = lambda id_: started.append(id_)
    for run in (first, second, first):
        panel._saved_courses.setCurrentIndex(panel._saved_courses.findData(run.data['id']))
        panel._resume_btn.click()
        assert panel._bs_entry.text() == run.data['bs_url']
        assert panel._moodle_entry.text() == run.data['moodle_url']
        assert panel._gradebook_cb.isChecked() == run.data['grade_all']
    assert started == [first.data['id'], second.data['id'], first.data['id']]
    panel._poll_timer.stop()
    recreated = H5PPanel(mw)
    qtbot.addWidget(recreated)
    assert recreated._saved_courses.count() == 3  # Blank choice plus both runs.
    assert recreated._saved_courses.currentData() is None
    assert not recreated._resume_btn.isEnabled()
    recreated._resume_run()
    assert started == [first.data['id'], second.data['id'], first.data['id']]
    recreated._poll_timer.stop()
