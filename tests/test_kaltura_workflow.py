"""Recovery contracts with isolated journals and no live course requests."""
import asyncio
import copy
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, 'src')
from kaltura_progress import KalturaRun, saved_runs, set_run_removed, describe_run
from kaltura_verification import entry_ids, find_video, reconcile_row, apply_page
from kaltura_runner import run_kaltura, resolve_units
from transfer_runner import PlanLock

BS = 'https://learn.example/d2l/home/42'
MOODLE = 'https://mymoodle.okanagan.bc.ca/course/view.php?id=7'


def make_run(tmp_path):
    run = KalturaRun.create(BS, MOODLE, tmp_path)
    run.capture_scan([dict(entry_id='1_test', name='Video', moodle_url=MOODLE,
        section_name='Week 1', sources=[dict(section_name='Week 1', section_key='section-1', moodle_url=MOODLE)])], [])
    run.data['modules'] = [dict(id='10', title='Week 1')]
    run.data['rows'][0].update(selected=True, destination={'id': '10'})
    run.save()
    return run


def choices(run, **overrides):
    return {r['key']: dict(selected=r['selected'], destination=r['destination'], **overrides) for r in run.data['rows']}


@pytest.mark.parametrize('markup', [
    '<iframe src="https://kaltura.test/entryId/1_test"></iframe>',
    '<script>kWidget.embed({"entry_id":"1_test"})</script>',
    '<iframe src="https://kaltura.test/entryid%2F1_test"></iframe>',
    '<iframe src="https://kaltura.test/entry_id=1_test&amp;foo=1"></iframe>',
])
def test_embed_identity_is_provider_identity_not_title(markup):
    assert entry_ids(markup) == {'1_test'}
    assert entry_ids('1_test Video') == set()


def test_capture_retains_distinct_source_sections_and_validates_every_destination(tmp_path):
    run = make_run(tmp_path)
    run.capture_scan([dict(entry_id='1_test', name='Video', sources=[
        dict(section_name='Same name', section_key='section-1', moodle_url=MOODLE),
        dict(section_name='Same name', section_key='section-2', moodle_url=MOODLE)])], [])
    assert len(run.data['rows']) == 2
    review = {r['key']: dict(selected=True, destination=None) for r in run.data['rows']}
    review[run.data['rows'][0]['key']]['destination'] = {'id': '10'}
    with pytest.raises(ValueError, match='Choose a destination'):
        run.approve(review)
    for row in review.values():
        row['destination'] = {'id': '10'}
    with pytest.raises(ValueError, match='same video'):
        run.approve(review)


def test_partial_scan_and_unknown_write_need_explicit_review(tmp_path):
    run = make_run(tmp_path)
    run.data.update(scan_complete=False, scan_issues=[dict(url=MOODLE, reason='Unread chapter')])
    with pytest.raises(ValueError, match='incomplete'):
        run.approve(choices(run))
    run.approve(choices(run), partial_ack=True)
    run.data['rows'][0]['state'] = 'pending'
    with pytest.raises(ValueError, match='Check the saved outcome'):
        run.approve(choices(run), partial_ack=True)


def test_removed_journal_survives_and_busy_transfer_cannot_be_removed(tmp_path):
    run = make_run(tmp_path)
    stamp = run.data['updated']
    with PlanLock(run.path), pytest.raises(RuntimeError, match='already running'):
        set_run_removed(run.data['id'], True, tmp_path)
    set_run_removed(run.data['id'], True, tmp_path)
    assert not saved_runs(tmp_path)
    assert saved_runs(tmp_path, include_removed=True)[0].data['rows']
    set_run_removed(run.data['id'], False, tmp_path)
    assert saved_runs(tmp_path)[0].data['updated'] == stamp


def test_saved_draft_survives_without_implying_approval(tmp_path):
    run = make_run(tmp_path)
    review = choices(run)
    review[run.data['rows'][0]['key']]['destination'] = None
    run.save_draft(review)
    loaded = KalturaRun.load(run.data['id'], tmp_path)
    assert loaded.data['rows'][0]['selected']
    assert loaded.data['rows'][0]['destination'] is None
    assert loaded.data['approval'] is None


def test_saved_source_locations_keep_chapter_identity_and_strip_login_tokens(tmp_path):
    from kaltura_progress import source_url, safe_problem
    url = 'https://mymoodle.okanagan.bc.ca/mod/book/view.php?id=5&chapterid=9&token=secret#heading'
    assert source_url(url).endswith('id=5&chapterid=9')
    assert 'secret' not in safe_problem('Failed navigation to ' + url)
    assert 'password' not in safe_problem('Failed navigation to https://user:password@host.test/login?token=secret')
    run = make_run(tmp_path)
    run.data['rows'][0].update(state='pending', attempted=True)
    run.capture_scan([], [dict(url=MOODLE, reason='Scan stopped')])
    assert run.data['rows'][0]['state'] == 'pending'


@pytest.mark.asyncio
async def test_session_preflights_kaltura_and_reuses_complete_read_only_inventory(tmp_path, monkeypatch):
    import browser
    import kaltura_runner as runner
    run = make_run(tmp_path)
    page = SimpleNamespace(evaluate=AsyncMock(return_value={'Name': 'Real course name'}), goto=AsyncMock())
    context = SimpleNamespace(expose_binding=AsyncMock(), add_init_script=AsyncMock(), pages=[page])
    bs_browser, kmc_browser = SimpleNamespace(close=AsyncMock()), SimpleNamespace(close=AsyncMock())
    p = SimpleNamespace(stop=AsyncMock())
    monkeypatch.setattr(browser, 'launch_browser', AsyncMock(return_value=(p, bs_browser, context, page)))
    monkeypatch.setattr(browser, 'wait_for_login', AsyncMock())
    monkeypatch.setattr(runner, 'read_modules', AsyncMock(return_value=run.data['modules']))
    index = AsyncMock(return_value={})
    monkeypatch.setattr(runner, 'read_video_index', index)
    events = []
    session = runner.KalturaSession(run, {}, threading.Event(), events.append, lambda *args: None, False)
    session.cat._get_kmc_context = AsyncMock(return_value=(context, kmc_browser, page))
    await session.connect()
    session.cat._get_kmc_context.assert_awaited_once()
    assert any(e.get('service') == 'Kaltura' and e.get('state') == 'Connected' for e in events)
    row = run.data['rows'][0]
    await session.reconcile(row)
    await session.reconcile(copy.deepcopy(row))
    assert index.await_count == 1
    assert run.data['course_name'] == 'Real course name'
    await session.close()
    bs_browser.close.assert_awaited_once()
    kmc_browser.close.assert_awaited_once()
    p.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_named_pdf_does_not_count_and_unreadable_html_blocks_creation():
    page = SimpleNamespace(evaluate=AsyncMock(side_effect=[
        [dict(Id=1, Title='Video', TopicType=1, Url='video.pdf'),
         dict(Id=2, Title='Renamed video', TopicType=1, Url='page.html')],
        '<iframe src="https://kaltura.test/entryId/1_test"></iframe>',
    ]))
    assert (await find_video(page, '42', '10', '1_test'))['Id'] == 2
    page.evaluate = AsyncMock(side_effect=[
        [dict(Id=2, Title='Different title', TopicType=1, Url='page.html')], RuntimeError('HTTP 403')])
    with pytest.raises(RuntimeError, match='403'):
        await find_video(page, '42', '10', '1_test')


@pytest.mark.asyncio
async def test_lost_save_response_reconciles_and_does_not_repeat(tmp_path, monkeypatch):
    import kaltura_verification as verification
    run = make_run(tmp_path)
    row = run.data['rows'][0]
    found = [None]
    async def lookup(*args): return found[0]
    monkeypatch.setattr(verification, 'find_video', lookup)
    monkeypatch.setattr(verification, 'module_topics', AsyncMock(return_value=[]))
    async def save(*args):
        assert KalturaRun.load(run.data['id'], tmp_path).data['rows'][0]['state'] == 'pending'
        found[0] = dict(Id=123, Title='Video')
        raise TimeoutError('response lost after save')
    cat = SimpleNamespace(_get_embed_code=AsyncMock(return_value='<iframe src="https://kaltura.test/entryId/1_test"></iframe>'),
                          _create_bs_page=AsyncMock(side_effect=save))
    await apply_page(run, row, object(), cat, object(), lambda *args: None)
    assert row['state'] == 'created' and row['topic_id'] == '123'
    await apply_page(run, row, object(), cat, object(), lambda *args: None)
    assert cat._create_bs_page.await_count == 1


@pytest.mark.asyncio
async def test_failed_read_or_duplicate_match_never_retries_uncertain_save(tmp_path, monkeypatch):
    import kaltura_verification as verification
    run = make_run(tmp_path)
    row = run.data['rows'][0]
    row.update(state='pending', attempted=True)
    monkeypatch.setattr(verification, 'find_video', AsyncMock(side_effect=RuntimeError('inventory unavailable')))
    cat = SimpleNamespace(_get_embed_code=AsyncMock(), _create_bs_page=AsyncMock())
    await apply_page(run, row, object(), cat, object(), lambda *args: None)
    assert row['state'] == 'needs_check'
    cat._create_bs_page.assert_not_awaited()
    cat._get_embed_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_embed_fails_before_mutation_and_stop_waits_for_verified_save(tmp_path, monkeypatch):
    import kaltura_verification as verification
    run = make_run(tmp_path)
    row = run.data['rows'][0]
    monkeypatch.setattr(verification, 'find_video', AsyncMock(return_value=None))
    monkeypatch.setattr(verification, 'module_topics', AsyncMock(return_value=[]))
    cat = SimpleNamespace(_get_embed_code=AsyncMock(return_value='<script>entryId="1_wrong"</script>'), _create_bs_page=AsyncMock())
    await apply_page(run, row, object(), cat, object(), lambda *args: None)
    assert row['state'] == 'failed'
    cat._create_bs_page.assert_not_awaited()
    stop = threading.Event()
    found = [None]
    async def lookup(*args): return found[0]
    monkeypatch.setattr(verification, 'find_video', lookup)
    async def save(*args):
        stop.set()
        found[0] = dict(Id=123, Title='Video')
        return True
    cat._get_embed_code.return_value = '<script>entryId="1_test"</script>'
    cat._create_bs_page.side_effect = save
    await apply_page(run, row, object(), cat, object(), lambda *args: None, stop=stop)
    assert row['state'] == 'created' and stop.is_set()


@pytest.mark.asyncio
async def test_saved_page_with_stripped_embed_is_not_duplicated_on_resume(tmp_path, monkeypatch):
    import kaltura_verification as verification
    run = make_run(tmp_path)
    row = run.data['rows'][0]
    topics = []
    async def read_topics(*args): return copy.deepcopy(topics)
    monkeypatch.setattr(verification, 'find_video', AsyncMock(return_value=None))
    monkeypatch.setattr(verification, 'module_topics', read_topics)
    async def save(*args):
        topics.append(dict(Id=456, Title='Video', Url='video.html', TopicType=1))
        return True  # Save succeeded, but the server stripped the player HTML.
    cat = SimpleNamespace(_get_embed_code=AsyncMock(return_value='<script>entryId="1_test"</script>'),
                          _create_bs_page=AsyncMock(side_effect=save))
    await apply_page(run, row, object(), cat, object(), lambda *args: None)
    assert row['state'] == 'needs_check'
    await apply_page(run, row, object(), cat, object(), lambda *args: None)
    assert cat._create_bs_page.await_count == 1


@pytest.mark.asyncio
async def test_unselected_new_units_never_reach_backend_and_failed_inventory_blocks(tmp_path):
    run = make_run(tmp_path)
    run.data['rows'][0].update(selected=False, destination={'new': 'Unselected unit'})
    session = SimpleNamespace(modules=AsyncMock(side_effect=RuntimeError('cannot read TOC')), create_unit=AsyncMock())
    await resolve_units(run, session, threading.Event())
    session.modules.assert_not_awaited()
    run.data['rows'][0]['selected'] = True
    with pytest.raises(RuntimeError, match='TOC'):
        await resolve_units(run, session, threading.Event())
    session.create_unit.assert_not_awaited()


class FakeSession:
    applied = []
    closed = False
    def __init__(self, run, credentials, stop, emit, log, watch):
        self.run = run
    async def connect(self):
        self.run.data['modules'] = [dict(id='10', title='Week 1')]
    async def scan(self):
        return [dict(entry_id='1_test', name='Video', section_name='Week 1', moodle_url=MOODLE)], []
    async def reconcile(self, row): pass
    async def status(self, *args, **kwargs): pass
    async def modules(self): return self.run.data['modules']
    async def apply(self, row):
        self.applied.append(row['key'])
        row.update(state='created', topic_id='123')
    async def close(self): self.closed = True


@pytest.mark.asyncio
async def test_runner_requires_review_and_retains_progress_on_stop(tmp_path):
    stop, states = threading.Event(), []
    FakeSession.applied = []
    async def approve(data, save_draft):
        assert not FakeSession.applied
        run = KalturaRun('', data)
        return dict(choices=choices(run), partial_ack=False)
    run = await run_kaltura(BS, MOODLE, stop=stop, review=approve, emit=states.append,
        log=lambda *args: None, directory=tmp_path, session_factory=FakeSession)
    assert run.data['status'] == 'complete'
    assert len(FakeSession.applied) == 1
    assert states[-1]['stage'] == 'results'
    FakeSession.applied = []
    async def pause(data, save_draft):
        save_draft(choices(KalturaRun('', data)))
        stop.set()
        return None
    run = await run_kaltura(BS, MOODLE, stop=stop, review=pause, emit=states.append,
        log=lambda *args: None, directory=tmp_path, session_factory=FakeSession)
    assert run.data['status'] == 'paused' and not FakeSession.applied
    assert KalturaRun.load(run.data['id'], tmp_path).data['approval'] is None


@pytest.mark.asyncio
async def test_runner_rejects_changed_course_pair_before_any_connection(tmp_path):
    run = make_run(tmp_path)
    factory = AsyncMock()
    with pytest.raises(ValueError, match='another course pair'):
        await run_kaltura(BS.replace('/42', '/99'), MOODLE, stop=threading.Event(), review=None,
            emit=lambda *args: None, log=lambda *args: None, resume_id=run.data['id'],
            directory=tmp_path, session_factory=factory)
    factory.assert_not_called()
