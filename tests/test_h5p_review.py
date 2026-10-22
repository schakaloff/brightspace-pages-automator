"""The review gate must describe and constrain the writes that follow it."""
import asyncio
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, 'src')
from h5p_progress import H5PRun
from h5p_handler import H5PHandler
from h5p_review import build_plan, validate_choices, review_and_prepare


@pytest.fixture
def course(tmp_path, monkeypatch):
    import h5p_handler
    monkeypatch.setattr(h5p_handler, '__file__', str(tmp_path / 'src' / 'h5p_handler.py'))
    run = H5PRun.create('https://bs.example/d2l/home/1',
        'https://moodle.example/course/view.php?id=10', '1', directory=tmp_path / 'runs')
    items = [dict(type='SECTION', name='Week 1'),
             dict(type='EXTERNAL', hint='h5p', name='Practice A', href='https://moodle.example/mod/h5pactivity/view.php?id=11'),
             dict(type='EXTERNAL', hint='h5p', name='Practice B', href='https://moodle.example/mod/h5pactivity/view.php?id=12')]
    modules = [dict(kind='MODULE', id='2', title='Week 1'), dict(kind='MODULE', id='3', title='Practice')]
    handler = H5PHandler(MagicMock(), None, None, AsyncMock(return_value=True), None,
                         AsyncMock(return_value=False), {})
    checker = SimpleNamespace(stop_flag=[False], _h5p=handler, h5p_recovery=None,
        _create_missing_units=AsyncMock(), _fetch_bs_toc=AsyncMock(return_value=modules),
        _switch_to_instructor_role=AsyncMock())
    handler.enable_downloads = AsyncMock(return_value=[])
    context = SimpleNamespace(new_page=AsyncMock(return_value=SimpleNamespace(goto=AsyncMock(), close=AsyncMock())))
    return SimpleNamespace(run=run, items=items, modules=modules, checker=checker, context=context, root=tmp_path)


def choices_for(plan):
    return [dict(key=r['key'], selected=True, module_id=r['bs_module_id'], should_grade=False)
            for r in plan['activities']]


def test_plan_discovery_does_not_create_downloads_or_saved_assignments(course):
    plan = build_plan(course.run, course.items, course.modules)
    assert [r['name'] for r in plan['activities']] == ['Practice A', 'Practice B']
    assert course.run.data['activities'] == []
    assert not (course.root / 'downloads').exists()


def test_choices_keep_identity_and_honor_selection_destination_and_grading(course):
    plan = build_plan(course.run, course.items, course.modules)
    choices = choices_for(plan)
    choices[0].update(module_id='3', should_grade=True)
    choices[1]['selected'] = False
    rows = validate_choices(plan, choices)
    assert rows[0]['name'] == 'Practice A'
    assert rows[0]['bs_module_title'] == 'Practice' and rows[0]['should_grade']
    assert rows[1]['selected'] is False
    assert plan['activities'][0]['bs_module_id'] == '2'


@pytest.mark.parametrize('change', ['unknown_unit', 'duplicate_key', 'no_selection', 'invented_new_unit'])
def test_invalid_choices_cannot_authorize_a_transfer(course, change):
    plan = build_plan(course.run, course.items, course.modules)
    choices = choices_for(plan)
    if change == 'unknown_unit': choices[0]['module_id'] = '999'
    if change == 'duplicate_key': choices[1]['key'] = choices[0]['key']
    if change == 'no_selection':
        for row in choices: row['selected'] = False
    if change == 'invented_new_unit': choices[0]['module_id'] = 'new:Other unit'
    with pytest.raises(ValueError): validate_choices(plan, choices)


def test_duplicate_titles_cannot_be_transferred_even_one_at_a_time(course):
    course.items[2]['name'] = 'Practice A!'
    plan = build_plan(course.run, course.items, course.modules)
    choices = choices_for(plan)
    choices[1]['selected'] = False
    with pytest.raises(ValueError, match='Rename them in Moodle'):
        validate_choices(plan, choices)


@pytest.mark.asyncio
async def test_declined_review_has_no_remote_writes_or_downloads(course):
    seen = []
    def decline(plan):
        seen.append(plan)
        course.checker._h5p.enable_downloads.assert_not_called()
        course.checker._create_missing_units.assert_not_called()
        return None
    result = await review_and_prepare(course.checker, course.context, object(), course.items,
        course.modules, 'https://bs.example', '1', course.run, decline, None)
    assert result is None and len(seen) == 1
    course.checker._h5p.enable_downloads.assert_not_called()
    course.checker._create_missing_units.assert_not_called()
    assert course.run.data['activities'] == []


@pytest.mark.asyncio
async def test_uncertain_destination_read_stops_before_review_or_writes(course):
    course.checker._h5p._verify_topic_in_module.side_effect = RuntimeError('Cannot verify destination')
    review = MagicMock()
    with pytest.raises(RuntimeError, match='Cannot verify'):
        await review_and_prepare(course.checker, course.context, object(), course.items,
            course.modules, 'https://bs.example', '1', course.run, review, None)
    review.assert_not_called()
    course.checker._h5p.enable_downloads.assert_not_called()


@pytest.mark.asyncio
async def test_only_selected_activity_is_downloaded_and_missing_files_stay_recoverable(course):
    def approve(plan):
        choices = choices_for(plan)
        choices[1]['selected'] = False
        return choices
    await review_and_prepare(course.checker, course.context, object(), course.items,
        course.modules, 'https://bs.example', '1', course.run, approve, None)
    assert course.checker._h5p.enable_downloads.call_args.kwargs['activity_keys'] == {
        H5PHandler.activity_key(course.items[1])}
    restored = H5PRun.load(course.run.data['id'], course.run.path.parent)
    assignments = restored.assignments(course.modules)
    assert len(assignments) == 1 and assignments[0]['name'] == 'Practice A'
    assert restored.data['activities'][0]['download'] == 'failed'
    assert 'Resume' in restored.data['activities'][0]['problem']


@pytest.mark.asyncio
async def test_all_existing_pages_finish_without_downloading_or_changing_grade_items(course):
    course.checker._h5p._verify_topic_in_module.return_value = True
    def approve(plan):
        assert all(r['already_present'] and not r['selected'] for r in plan['activities'])
        return [dict(key=r['key'], selected=False) for r in plan['activities']]
    await review_and_prepare(course.checker, course.context, object(), course.items,
        course.modules, 'https://bs.example', '1', course.run, approve, None)
    course.checker._h5p.enable_downloads.assert_not_called()
    assert course.run.counts()[2] == 0


@pytest.mark.asyncio
async def test_new_unit_is_created_only_after_explicit_review_choice(course):
    course.modules[:] = [course.modules[1]]
    refreshed = course.modules + [dict(kind='MODULE', id='8', title='Week 1')]
    course.checker._fetch_bs_toc.return_value = refreshed
    def approve(plan):
        course.checker._create_missing_units.assert_not_called()
        choices = choices_for(plan)
        for choice in choices: choice['module_id'] = 'new:Week 1'
        return choices
    await review_and_prepare(course.checker, course.context, object(), course.items,
        course.modules, 'https://bs.example', '1', course.run, approve, None)
    course.checker._create_missing_units.assert_awaited_once()
    assert course.checker._create_missing_units.call_args.args[-1] == ['Week 1']
    assert all(r['bs_module_id'] == '8' for r in course.run.data['activities'])


@pytest.mark.asyncio
async def test_stopping_review_does_not_start_downloads(course):
    def stop(plan):
        course.checker.stop_flag[0] = True
        return choices_for(plan)
    with pytest.raises(asyncio.CancelledError):
        await review_and_prepare(course.checker, course.context, object(), course.items,
            course.modules, 'https://bs.example', '1', course.run, stop, None)
    course.checker._h5p.enable_downloads.assert_not_called()


def test_review_ui_releases_approved_choices_and_applies_destination_to_section(course, qtbot, monkeypatch):
    import panels.h5p_panel as panel_module
    monkeypatch.setattr(panel_module, 'saved_runs', lambda *args: [])
    mw = MagicMock()
    mw.load_config.return_value = {}
    panel = panel_module.H5PPanel(mw)
    qtbot.addWidget(panel)
    plan = build_plan(course.run, course.items, course.modules)
    event, result = threading.Event(), [None]
    panel._task_active = True
    panel._show_plan(plan, result, event)
    controls = list(panel._row_controls.values())
    controls[0]['unit'].setCurrentIndex(controls[0]['unit'].findData('3'))
    panel._table.setCurrentCell(controls[0]['index'], 1)
    panel._apply_section.click()
    assert controls[1]['unit'].currentData() == '3'
    controls[0]['grade'].setChecked(True)
    controls[1]['move'].setChecked(False)
    panel._run_btn.click()
    assert event.is_set()
    assert result[0][0]['should_grade'] and result[0][0]['module_id'] == '3'
    assert result[0][1]['selected'] is False
    assert panel._stage == 2 and not panel._run_btn.isEnabled()


def test_review_ui_stop_releases_worker_wait(course, qtbot, monkeypatch):
    import panels.h5p_panel as panel_module
    monkeypatch.setattr(panel_module, 'saved_runs', lambda *args: [])
    mw = MagicMock()
    mw.load_config.return_value = {}
    panel = panel_module.H5PPanel(mw)
    qtbot.addWidget(panel)
    event, result = threading.Event(), [None]
    panel._task_active = True
    panel._show_plan(build_plan(course.run, course.items, course.modules), result, event)
    panel._stop_run()
    assert event.is_set() and result[0] is None
    assert not panel._run_btn.isEnabled()


@pytest.mark.asyncio
async def test_reviewed_runner_uses_inventory_then_transfers_only_approved_rows(course, monkeypatch):
    import browser
    import h5p_runner
    checker = course.checker
    checker._fetch_bs_toc = AsyncMock(return_value=course.modules)
    checker._scrape_moodle = AsyncMock(return_value=course.items)
    checker.moodle_course_name = 'Moodle practice course'
    runtime = SimpleNamespace(stop=AsyncMock())
    browser_instance = SimpleNamespace(is_connected=lambda: True, close=AsyncMock())
    page = SimpleNamespace(evaluate=AsyncMock(return_value={'Name': 'Brightspace practice course'}))
    monkeypatch.setattr(browser, 'launch_browser', AsyncMock(return_value=(runtime, browser_instance, course.context, page)))
    monkeypatch.setattr(browser, 'wait_for_login', AsyncMock())
    monkeypatch.setattr(h5p_runner, 'ContentChecker', lambda **kwargs: checker)
    monkeypatch.setattr(h5p_runner, '_verify_topic_in_module_strict', AsyncMock(return_value=False))

    async def download(context, items, activity_keys):
        folder = course.root / 'downloads' / 'h5p'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'Practice A.h5p').write_bytes(b'activity A')
        return []
    checker._h5p.enable_downloads.side_effect = download

    async def embed(*args):
        run = checker._h5p.progress
        assignments = run.assignments(course.modules)
        assert len(assignments) == 1 and assignments[0]['name'] == 'Practice A'
        assert assignments[0]['should_grade'] and assignments[0]['bs_module_id'] == '3'
        assert assignments[0]['file'].read_bytes() == b'activity A'
        run.checkpoint(assignments[0], 'upload', 'confirmed')
        run.checkpoint(assignments[0], 'insertion', 'confirmed')
        checker._summary['h5p_inserted'].append(('Practice A', 'Practice'))
    checker._h5p.embed_in_brightspace = AsyncMock(side_effect=embed)
    def approve(plan):
        checker._h5p.embed_in_brightspace.assert_not_called()
        checker._h5p.enable_downloads.assert_not_called()
        result = choices_for(plan)
        result[0].update(module_id='3', should_grade=True)
        result[1]['selected'] = False
        return result
    states, complete = [], MagicMock()
    await h5p_runner.run_h5p_only(course.run.data['bs_url'], course.run.data['moodle_url'], MagicMock(),
        progress_directory=course.run.path.parent, review_fn=approve, on_state=states.append, on_complete=complete)
    checker._scrape_moodle.assert_awaited_once_with(course.context, inventory_only=True)
    assert states[-1]['stage'] == 'results'
    result = states[-1]['run']
    assert result['status'] == 'paused'  # One activity was deliberately left for later.
    assert result['activities'][0]['insertion'] == 'confirmed'
    assert result['activities'][1]['selected'] is False
    assert result['moodle_course_name'] == 'Moodle practice course'
    complete.assert_not_called()
    runtime.stop.assert_awaited_once()
    browser_instance.close.assert_awaited_once()


def test_review_approval_is_cancelled_with_the_worker(course, qtbot, monkeypatch):
    import h5p_runner
    import panels.h5p_panel as panel_module
    monkeypatch.setattr(panel_module, 'saved_runs', lambda *args: [])
    mw = MagicMock()
    mw.chromium_ready = True
    mw.load_config.return_value = {}
    panel = panel_module.H5PPanel(mw)
    qtbot.addWidget(panel)
    panel._bs_entry.setText(course.run.data['bs_url'])
    panel._moodle_entry.setText(course.run.data['moodle_url'])
    cancelled = threading.Event()
    async def worker(**kwargs):
        try:
            await asyncio.get_running_loop().run_in_executor(None, kwargs['review_fn'],
                build_plan(course.run, course.items, course.modules))
        except asyncio.CancelledError:
            cancelled.set()
            raise
    monkeypatch.setattr(h5p_runner, 'run_h5p_only', worker)
    panel._start_run()
    qtbot.waitUntil(lambda: panel._review_wait is not None, timeout=3000)
    panel._stop_btn.click()
    qtbot.waitUntil(lambda: not panel._task_active, timeout=3000)
    qtbot.waitUntil(lambda: not panel._worker_thread.is_alive(), timeout=3000)
    assert cancelled.is_set() and panel._review_wait is None
    assert panel._run_btn.isEnabled()
    assert panel._stage == 3  # Stop & save still leaves the progress screen open.


@pytest.mark.parametrize('phase', ['connect', 'review', 'transfer'])
def test_back_waits_for_worker_cleanup_then_returns_to_courses_with_saved_progress(course, qtbot, monkeypatch, phase):
    import h5p_runner
    import panels.h5p_panel as panel_module
    monkeypatch.setattr(panel_module, 'saved_runs', lambda directory: [H5PRun.load(course.run.data['id'], course.run.path.parent)])
    mw = MagicMock()
    mw.chromium_ready = True
    mw.load_config.return_value = {}
    panel = panel_module.H5PPanel(mw)
    panel._progress_directory = course.run.path.parent
    qtbot.addWidget(panel)
    panel.show()
    panel._bs_entry.setText(course.run.data['bs_url'])
    panel._moodle_entry.setText(course.run.data['moodle_url'])
    cleanup_started, finish_cleanup = threading.Event(), threading.Event()
    entered_phase = threading.Event()
    navigated = MagicMock()
    panel.back_requested.connect(navigated)

    async def worker(**kwargs):
        try:
            if phase == 'connect':
                kwargs['on_moodle_waiting']()
                entered_phase.set()
                await asyncio.to_thread(kwargs['moodle_ready_event'].wait)
            elif phase == 'review':
                entered_phase.set()
                await asyncio.to_thread(kwargs['review_fn'], build_plan(course.run, course.items, course.modules))
            else:
                kwargs['on_state'](dict(stage='transfer', message='Transferring'))
                entered_phase.set()
                await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            while not finish_cleanup.is_set():
                await asyncio.sleep(0.01)
            course.run.data['status'] = 'paused'
            course.run.save()
            kwargs['on_state'](dict(stage='results', run=course.run.data, summary={}))

    monkeypatch.setattr(h5p_runner, 'run_h5p_only', worker)
    try:
        panel._start_run(course.run.data['id'])
        qtbot.waitUntil(entered_phase.is_set, timeout=3000)
        if phase == 'connect':
            qtbot.waitUntil(panel._ready_btn.isVisible, timeout=3000)
        elif phase == 'review':
            qtbot.waitUntil(lambda: panel._review_wait is not None, timeout=3000)
        else:
            qtbot.waitUntil(lambda: panel._stage == 2, timeout=3000)
        panel._back_btn.click()
        qtbot.waitUntil(cleanup_started.is_set, timeout=3000)
        assert panel._task_active
        assert not panel._run_btn.isEnabled() and not panel._back_btn.isEnabled()
        navigated.assert_not_called()
    finally:
        finish_cleanup.set()
        if panel._task_active and not panel._stop_flag[0]:
            panel._stop_run()
        qtbot.waitUntil(lambda: not panel._task_active, timeout=3000)
        qtbot.waitUntil(lambda: not panel._worker_thread.is_alive(), timeout=3000)

    assert panel._stage == 0 and panel._review_wait is None
    assert panel._connection.isVisible() and panel._moodle_entry.isEnabled()
    assert panel._run_btn.isEnabled() and panel._back_btn.isEnabled()
    assert not panel._ready_btn.isVisible() and not panel._stop_btn.isVisible()
    assert panel._bs_entry.text() == course.run.data['bs_url']
    assert panel._moodle_entry.text() == course.run.data['moodle_url']
    assert H5PRun.load(course.run.data['id'], course.run.path.parent).data['status'] == 'paused'
    assert panel._saved_courses.findData(course.run.data['id']) > 0
    navigated.assert_not_called()


@pytest.mark.asyncio
async def test_missing_download_does_not_block_available_activities(course):
    folder = course.root / 'downloads' / 'h5p'
    folder.mkdir(parents=True)
    available = folder / 'Practice B.h5p'
    available.write_bytes(b'available package')
    assignments = H5PHandler._build_assignments(
        [folder / 'Practice A.h5p', available], course.items, course.modules, False)
    course.run.capture_assignments(assignments)
    handler = course.checker._h5p
    handler.progress = course.run
    handler.open_editor_and_get_frame = AsyncMock(return_value=object())
    handler.find_list_frame = AsyncMock(return_value=object())
    handler._collect_cloud_titles = AsyncMock(side_effect=[[], ['Practice B']])
    handler.upload_one = AsyncMock(return_value=True)
    handler.insert_from_list = AsyncMock(return_value=True)
    pages = set()
    async def verify(page, course_id, module_id, name): return name in pages
    async def finalize(tab, name, **kwargs):
        pages.add(name)
        return True
    handler._verify_topic_in_module = verify
    handler.finalize = finalize
    await handler.embed_in_brightspace(course.context, object(), course.items, course.modules, 'https://bs.example', '1')
    assert handler.upload_one.await_count == 1
    assert handler.upload_one.call_args.args[3] == 'Practice B'
    assert course.run.data['activities'][1]['insertion'] == 'confirmed'
    assert 'Download missing' in course.run.data['activities'][0]['problem']


def test_results_filter_keeps_gradebook_issues_visible(course, qtbot, monkeypatch):
    import panels.h5p_panel as panel_module
    monkeypatch.setattr(panel_module, 'saved_runs', lambda *args: [])
    mw = MagicMock()
    mw.load_config.return_value = {}
    panel = panel_module.H5PPanel(mw)
    qtbot.addWidget(panel)
    row = build_plan(course.run, course.items, course.modules)['activities'][0]
    row.update(already_present=True, insertion='confirmed', grade_attention=True)
    panel._set_stage(3)
    panel._render_rows([row])
    panel._only_attention.setChecked(True)
    index = panel._display_rows[0][0]
    assert not panel._table.isRowHidden(index)
    assert 'gradebook' in panel._table.item(index, 4).text()


@pytest.mark.asyncio
async def test_uncertain_saved_title_requires_exact_name_before_resume(course):
    files = [course.root / 'Practice A.h5p', course.root / 'Practice B.h5p']
    course.run.capture_assignments(H5PHandler._build_assignments(files, course.items, course.modules, False))
    course.run.data['activities'][0]['name_check_required'] = True
    course.checker._h5p._verify_saved_name = AsyncMock(return_value=False)
    course.checker._h5p._verify_topic_in_module.return_value = True
    def review(plan):
        first = plan['activities'][0]
        assert first['name_check_required'] and not first['selected'] and not first['already_present']
        return None
    await review_and_prepare(course.checker, course.context, object(), course.items, course.modules,
        'https://bs.example', '1', course.run, review, None)
    course.checker._h5p._verify_saved_name.assert_awaited_once()
    course.checker._h5p.enable_downloads.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_resume_does_not_create_another_page_after_a_title_verification_failure(course):
    assignments = H5PHandler._build_assignments([course.root / 'Practice A.h5p'], course.items, course.modules, False)
    course.run.capture_assignments(assignments)
    course.run.data['activities'][0]['name_check_required'] = True
    handler = course.checker._h5p
    handler.progress = course.run
    handler._verify_saved_name = AsyncMock(return_value=False)
    handler._verify_topic_in_module.return_value = True
    handler.open_editor_and_get_frame = AsyncMock()
    with pytest.raises(RuntimeError, match='second page will not be created'):
        await handler.embed_in_brightspace(course.context, object(), course.items, course.modules, 'https://bs.example', '1')
    handler.open_editor_and_get_frame.assert_not_called()


def test_saved_page_rename_keeps_destination_and_grading(course):
    plan = build_plan(course.run, course.items, course.modules)
    plan['activities'][0].update(name_check_required=True, saved_topic_id='9', should_grade=True)
    choices = choices_for(plan)
    rows = validate_choices(plan, choices)
    assert rows[0]['selected'] and rows[0]['saved_topic_id'] == '9'
    assert rows[0]['should_grade'] is True
    choices[0]['module_id'] = '3'
    with pytest.raises(ValueError, match='original destination unit'):
        validate_choices(plan, choices)


@pytest.mark.asyncio
@pytest.mark.parametrize('verified', [True, False])
async def test_resume_repairs_the_recorded_page_without_opening_an_editor_or_uploading(course, monkeypatch, verified):
    import h5p_titles
    assignments = H5PHandler._build_assignments([course.root / 'Practice A.h5p'], course.items, course.modules, False)
    course.run.capture_assignments(assignments)
    row = course.run.data['activities'][0]
    row.update(name_check_required=True, saved_topic_id='9', upload='confirmed')
    handler = course.checker._h5p
    handler.progress = course.run
    repair = AsyncMock(return_value=dict(Id=9, Title='Practice A'))
    monkeypatch.setattr(h5p_titles, 'ensure_owned_page_title', repair)
    handler._verify_topic_in_module = AsyncMock(return_value=True)
    handler._verify_saved_name = AsyncMock(return_value=verified)
    handler.open_editor_and_get_frame = AsyncMock()
    handler.upload_one = AsyncMock()
    if verified:
        await handler.embed_in_brightspace(course.context, object(), course.items, course.modules, 'https://bs.example', '1')
    else:
        with pytest.raises(RuntimeError, match='second page will not be created'):
            await handler.embed_in_brightspace(course.context, object(), course.items, course.modules, 'https://bs.example', '1')
    assert repair.call_args.args[1:] == ('9', 'Practice A')
    assert repair.call_args.args[0].module_id == '2'
    assert (row['insertion'] == 'confirmed') is verified
    assert row['name_check_required'] is not verified
    handler.open_editor_and_get_frame.assert_not_called()
    handler.upload_one.assert_not_called()


@pytest.mark.asyncio
async def test_pending_owned_page_rename_is_reviewable_without_renaming_during_discovery(course, monkeypatch):
    import h5p_titles
    assignments = H5PHandler._build_assignments([course.root / 'Practice A.h5p'], course.items, course.modules, False)
    course.run.capture_assignments(assignments)
    course.run.data['activities'][0].update(name_check_required=True, saved_topic_id='9')
    course.checker._h5p._verify_saved_name = AsyncMock(return_value=False)
    repair = AsyncMock()
    monkeypatch.setattr(h5p_titles, 'ensure_owned_page_title', repair)
    def review(plan):
        row = plan['activities'][0]
        assert row['selected'] and row['name_check_required'] and row['saved_topic_id'] == '9'
        return None
    await review_and_prepare(course.checker, course.context, object(), course.items, course.modules,
        'https://bs.example', '1', course.run, review, None)
    repair.assert_not_called()
    course.checker._h5p.enable_downloads.assert_not_called()
