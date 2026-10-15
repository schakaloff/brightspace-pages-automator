"""Whole-unit Skip removes linked draft modules, never live course objects."""
import copy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_transfer_studio import studio, ready, login, scan, edit_request, entry
from test_transfer_structure import source_course
from test_transfer_outline import target_course
from transfer_plan import digest
from transfer_runner import load_plan
import transfer_outline as outline


def patterned(studio, source=None, target=None, start='pattern'):
    cookie = login(studio)
    assert scan(studio, cookie, start=start, pattern='EA105 workshop layout')[0] == 202
    studio.finish_scan((source or source_course(), target or target_course()))
    assert not studio.error
    return cookie


@pytest.mark.parametrize('legacy', [False, True])
def test_whole_unit_skip_removes_automatic_pages_and_restores_everything_with_undo(studio, legacy):
    cookie = patterned(studio)
    if legacy:
        for node, _, _ in outline.walk(studio.plan['outline']): node.pop('auto_generated', None)
    before = copy.deepcopy(studio.plan)
    status, result = edit_request(studio, cookie, 'skip', [entry('10', 'source')])
    assert status == 200, result
    assert not studio.plan['outline'] and set(studio.plan['skipped']) == {'11', '12', '13', '15'}
    assert load_plan(studio.path)['outline'] == []
    assert studio.snapshot()['plan']['counts']['modules'] == 0
    assert studio.snapshot()['plan']['source_nodes'][0]['unit_skipped']
    assert edit_request(studio, cookie, 'undo')[0] == 200 and studio.plan == before
    assert edit_request(studio, cookie, 'redo')[0] == 200 and not studio.plan['outline']


def test_skipping_individual_items_does_not_remove_the_unit(studio):
    cookie = patterned(studio)
    for id_ in ('11', '12', '13', '15'):
        assert edit_request(studio, cookie, 'skip', [entry(id_, 'source')])[0] == 200
    assert len(studio.plan['outline']) == 1
    assert not studio.snapshot()['plan']['source_nodes'][0]['unit_skipped']
    assert any(n['kind'] == 'template' for n, _, _ in outline.walk(studio.plan['outline']))


@pytest.mark.parametrize('kind', ['placeholder', 'introduction'])
def test_added_pages_require_confirmation_before_any_change(studio, kind):
    cookie = patterned(studio)
    root = studio.plan['outline'][0]['id']
    assert edit_request(studio, cookie, 'add_page', [entry(root)], {'kind': kind, 'title': 'My notes'})[0] == 200
    before, revision = copy.deepcopy(studio.plan), studio.revision
    status, result = edit_request(studio, cookie, 'skip', [entry('10', 'source')])
    assert status == 409 and any(n['title'] == 'My notes' for n in result['confirmation']['protected'])
    assert studio.plan == before and studio.revision == revision and load_plan(studio.path) == before
    assert edit_request(studio, cookie, 'skip', [entry('10', 'source')], {'confirmed_custom': True})[0] == 200
    assert not studio.plan['outline']
    assert edit_request(studio, cookie, 'undo')[0] == 200 and studio.plan == before


def test_written_text_in_an_automatic_page_requires_confirmation(studio):
    cookie = patterned(studio)
    intro = next(n for n, _, _ in outline.walk(studio.plan['outline']) if n.get('template') == 'introduction')
    assert edit_request(studio, cookie, 'set_text', [entry(intro['id'])], {'introduction': 'My authored introduction.'})[0] == 200
    status, result = edit_request(studio, cookie, 'skip', [entry('10', 'source')])
    assert status == 409 and result['confirmation']['protected']
    assert intro['id'] in {n['id'] for n, _, _ in outline.walk(studio.plan['outline'])}


def test_foreign_content_returns_to_unplaced_after_confirmation_and_undo_restores_it(studio):
    source = source_course()
    source['modules'].append({'Id': 20, 'Type': 0, 'Title': 'Another unit', 'children': [
        {'Id': 21, 'Type': 1, 'Title': 'Another page', 'ActivityType': 1, 'TopicType': 1,
         'Url': '/content/enforced/1-src/another.html', 'html': '<p>Keep me.</p>'}]})
    cookie = patterned(studio, source)
    root = studio.plan['outline'][0]['id']
    assert edit_request(studio, cookie, 'place', [entry('21', 'source')], {'parent': root})[0] == 200
    before = copy.deepcopy(studio.plan)
    status, result = edit_request(studio, cookie, 'skip', [entry('10', 'source')])
    assert status == 409 and any(n['title'] == 'Another page' for n in result['confirmation']['protected'])
    assert studio.plan == before
    assert edit_request(studio, cookie, 'skip', [entry('10', 'source')], {'confirmed_custom': True})[0] == 200
    assert [n['title'] for n in studio.plan['outline']] == ['Another unit']
    assert '21' not in studio.plan['skipped'] and '21' not in outline.placements(studio.plan)
    assert studio.plan['source_hash'] == digest(source)
    assert edit_request(studio, cookie, 'undo')[0] == 200 and studio.plan == before


def test_same_named_unlinked_module_is_kept_and_renamed_linked_module_is_still_found(studio):
    cookie = ready(studio)
    linked = studio.plan['outline'][0]['id']
    edit_request(studio, cookie, 'add_module', args={'title': 'Week 1'})
    unrelated = studio.plan['outline'][-1]['id']
    edit_request(studio, cookie, 'rename', [entry(linked)], {'title': 'Renamed unit'})
    assert edit_request(studio, cookie, 'skip', [entry('10', 'source')], {'confirmed_custom': True})[0] == 200
    assert [n['id'] for n in studio.plan['outline']] == [unrelated]


def test_nested_source_folder_skip_removes_only_its_linked_draft_folder(studio):
    cookie = ready(studio)
    root = studio.plan['outline'][0]['id']
    assert edit_request(studio, cookie, 'skip', [entry('14', 'source')])[0] == 200
    assert studio.plan['outline'][0]['id'] == root
    assert not any(n.get('source_module') == '14' for n, _, _ in outline.walk(studio.plan['outline']))
    assert studio.plan['skipped'] == ['15']


def test_editing_a_mirrored_units_source_mapping_requires_confirmation(studio):
    cookie = ready(studio)
    root = studio.plan['outline'][0]['id']
    assert edit_request(studio, cookie, 'set_sources', [entry(root)], {'sources': []})[0] == 200
    before = copy.deepcopy(studio.plan)
    status, result = edit_request(studio, cookie, 'skip', [entry('10', 'source')])
    assert status == 409 and result['confirmation']['protected']
    assert studio.plan == before


def test_existing_destination_module_is_only_removed_from_the_draft(studio):
    target = target_course([{'Id': 500, 'Type': 0, 'Title': 'Week 1', 'children': [
        {'Id': 501, 'Type': 0, 'Title': 'Existing section', 'children': []}]}])
    cookie = patterned(studio, target=target, start='destination')
    before = copy.deepcopy(studio.plan['target'])
    assert edit_request(studio, cookie, 'skip', [entry('10', 'source')])[0] == 200
    assert not studio.plan['outline'] and studio.plan['target'] == before


def test_confirmation_cannot_apply_to_a_newer_draft_revision(studio):
    cookie = patterned(studio)
    root = studio.plan['outline'][0]['id']
    edit_request(studio, cookie, 'add_page', [entry(root)], {'kind': 'placeholder', 'title': 'My notes'})
    revision = studio.revision
    assert edit_request(studio, cookie, 'skip', [entry('10', 'source')])[0] == 409
    edit_request(studio, cookie, 'add_module', args={'title': 'Later change'})
    before = copy.deepcopy(studio.plan)
    assert edit_request(studio, cookie, 'skip', [entry('10', 'source')], {'confirmed_custom': True}, revision=revision)[0] == 409
    assert studio.plan == before


def test_empty_unit_skip_is_marked_and_undo_restores_its_draft_module(studio):
    source = source_course()
    source['modules'].append({'Id': 30, 'Type': 0, 'Title': 'Empty unit', 'children': []})
    cookie = patterned(studio, source, start='mirror')
    before = copy.deepcopy(studio.plan)
    assert edit_request(studio, cookie, 'skip', [entry('30', 'source')])[0] == 200
    assert [n['title'] for n in studio.plan['outline']] == ['Week 1']
    assert studio.snapshot()['plan']['source_nodes'][-1]['status'] == 'Skipped'
    assert edit_request(studio, cookie, 'include', [entry('30', 'source')])[0] == 200
    assert studio.snapshot()['plan']['source_nodes'][-1]['status'] == ''
    edit_request(studio, cookie, 'undo')
    assert edit_request(studio, cookie, 'undo')[0] == 200 and studio.plan == before
