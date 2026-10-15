"""Skipped assignment links use an explicit destination choice, never a guess."""
import copy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_transfer_studio import studio, login, scan, edit_request, entry
from test_transfer_structure import source_course
from test_transfer_outline import target_course
from test_transfer_outline_runner import setup
from transfer_activity_links import assignment_url, issues, rewrites
from transfer_outline_runner import OutlineRunner, outline_signature
from transfer_runner import save_plan, load_plan
import transfer_outline as outline

MARKUP = '<p>Complete <a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=dropbox&amp;ou=1&amp;rcode=essay">the assignment</a>.</p>'


def courses():
    source = source_course()
    source['modules'][0]['children'][0]['html'] = MARKUP
    assignment = {**copy.deepcopy(source['assignments'][0]), 'Id': 700,
                  'ActivityId': 'https://ids.brightspace.com/activities/dropbox/destination-700'}
    assignment['Assessment']['ScoreDenominator'] = 42  # Different, and explicitly retained.
    target = target_course()
    target['assignments'] = [assignment]
    return source, target


def plan():
    source, target = courses()
    result = outline.build_outline_plan(source, target, 'mirror')
    outline.skip(result, ['13'])
    return result


def test_review_identifies_the_page_and_assignment_and_offers_explicit_reuse():
    problem = issues(plan())[0]
    assert problem['level'] == 'error' and problem['row'] == '11'
    assert 'Read chapter 2' in problem['text'] and 'Week 1 Essay' in problem['text']
    assert (problem['fix'], problem['link_row'], problem['target_id']) == ('use_existing_link', '13', '700')


def test_choice_rewrites_to_target_and_changes_signature_without_changing_old_empty_signatures():
    draft = plan()
    initial = outline_signature(draft)
    draft['activity_link_targets'] = {}
    assert outline_signature(draft) == initial
    draft['activity_link_targets']['70'] = '700'
    assert outline_signature(draft) != initial
    assert list(rewrites(draft).values()) == ['https://learn.okanagancollege.ca/d2l/common/dialogs/quickLink/quickLink.d2l?ou=2&type=dropbox&rcode=destination-700']
    assert issues(draft)[0]['level'] == 'note'


def test_included_activity_links_keep_using_the_copy_flow():
    source, target = courses()
    draft = outline.build_outline_plan(source, target, 'mirror')
    assert issues(draft) == []


def test_ambiguous_targets_are_not_guessed_and_missing_chosen_target_stops_preparation():
    draft = plan()
    draft['target']['assignments'].append({**copy.deepcopy(draft['target']['assignments'][0]), 'Id': 701})
    assert 'fix' not in issues(draft)[0]
    draft['activity_link_targets'] = {'70': 'missing'}
    with pytest.raises(ValueError, match='Week 1 Essay.*missing'):
        rewrites(draft)


def test_unsupported_activity_identity_cannot_be_used_as_a_link():
    _, target = courses()
    target['assignments'][0]['ActivityId'] = 'https://evil.example/activities/dropbox/not-brightspace'
    with pytest.raises(ValueError, match='link is unavailable'):
        assignment_url(target, target['assignments'][0])


def test_http_link_choice_is_saved_and_undoable_without_including_the_assignment(studio):
    source, target = courses()
    cookie = login(studio)
    scan(studio, cookie)
    studio.finish_scan((source, target))
    edit_request(studio, cookie, 'skip', [entry('13', 'source')])
    before = copy.deepcopy(studio.plan)
    assert edit_request(studio, cookie, 'use_existing_link', [entry('13', 'source')], {'target_id': '700'})[0] == 200
    assert load_plan(studio.path)['activity_link_targets'] == {'70': '700'}
    assert '13' in studio.plan['skipped'] and '13' not in outline.placements(studio.plan)
    assert edit_request(studio, cookie, 'undo')[0] == 200 and studio.plan == before


@pytest.mark.asyncio
async def test_real_runner_rewrites_page_links_without_copying_or_editing_existing_assignment(tmp_path, monkeypatch):
    source, target, draft, path, copies = await setup(tmp_path, monkeypatch, source_html=MARKUP)
    _, existing = courses()
    target.state['assignments'] = copy.deepcopy(existing['assignments'])
    draft['target'] = await target.inventory()
    outline.skip(draft, ['13'])
    draft['activity_link_targets'] = {'70': '700'}
    save_plan(draft, path)
    assignments_before = copy.deepcopy(target.state['assignments'])
    result = await OutlineRunner(source, target, draft, path).run('2', True)
    assert all(result['validation'].values())
    assert target.state['assignments'] == assignments_before
    assert not any(selection.get('dropbox') for selection in copies)
    assert not source.writes
    body = next(value for name, value in target.files.items() if b'the assignment' in value)
    assert b'ou=2' in body and b'destination-700' in body and b'ou=1' not in body
    assert 'keeping that assignment' in result['report']


@pytest.mark.asyncio
async def test_unknown_cross_course_link_reports_page_and_leaves_the_course_untouched(tmp_path, monkeypatch):
    source, target, draft, path, copies = await setup(tmp_path, monkeypatch, source_html='<a href="/d2l/le/lessons/1/units/999">Unknown unit</a>')
    with pytest.raises(ValueError, match='Read chapter 2.*Cross-course'):
        await OutlineRunner(source, target, draft, path).run('2', True)
    saved = load_plan(path)
    assert 'PREPARATION STOPPED' in saved['report'] and 'Read chapter 2' in saved['report']
    assert not saved['journal'] and not saved.get('run_signature')
    assert not source.writes and not target.writes and not copies
