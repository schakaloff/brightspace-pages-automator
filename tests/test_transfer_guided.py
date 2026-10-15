import asyncio
import copy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from transfer_guided import (automatic_recipe, build_guided_plan, discover_templates,
                             match_target_assignments, matching_assignment)
from transfer_plan import build_plan, digest, validate_plan, workshop_recipe
from transfer_runner import scan_transfer


def course(id_='1', modules=None):
    return {'id': id_, 'name': 'Sandbox ' + id_, 'root': f'/content/enforced/{id_}-sandbox/',
            'modules': modules or [], 'assignments': [], 'categories': [], 'grades': [],
            'setup': {'GradingSystem': 'Weighted'}, 'versions': ['1.99']}


def sample_plan():
    source = course(modules=[{'Id': 10, 'Type': 0, 'Title': 'Workshop', 'children': [
        {'Id': 11, 'Type': 1, 'Title': 'Slides', 'ActivityType': 1, 'TopicType': 1, 'Url': '/content/enforced/1-sandbox/slides.pdf'}]}])
    recipe = workshop_recipe()
    recipe['workshops'] = [{'title': 'Workshop', 'source_titles': ['Workshop'], 'hours': 6, 'outcomes': ['Learn'], 'introduction': 'Intro', 'session': 'Session'}]
    plan = build_plan(source, course('2'), recipe, {'introduction': {'html': 'template'}, 'session': {'html': 'template'}})
    plan['workshops'][0]['mapping_reviewed'] = True
    plan['workshops'][0]['items'][0]['reviewed'] = True
    plan['review_acknowledged'] = True
    return plan

STYLE = '<div class="courseware-layouts-content-wrapper"><h1>Template</h1><div class="d2l-element"><div class="d2l-callout-title">Learning Outcomes</div><div><ul><li>Example</li></ul></div></div></div>'


def templates(course_id='2'):
    return [{'Id': 500, 'Type': 0, 'Title': 'Development templates', 'children': [
        {'Id': 501, 'Type': 1, 'Title': 'Module Introduction template', 'html': STYLE, 'Url': f'/content/enforced/{course_id}-sandbox/intro.html'},
        {'Id': 502, 'Type': 1, 'Title': 'Session Overview template', 'html': STYLE, 'Url': f'/content/enforced/{course_id}-sandbox/session.html'}]}]


def known_source():
    titles = [t for w in workshop_recipe()['workshops'] for t in w['source_titles']]
    return course('1', [{'Id': i + 10, 'Type': 0, 'Title': title, 'children': [
        {'Id': i + 100, 'Type': 1, 'Title': 'Slides', 'ActivityType': 1, 'TopicType': 1,
         'Url': '/content/enforced/1-sandbox/slides.pdf'}]} for i, title in enumerate(titles)])


def test_two_links_discover_known_layout_without_marking_uncertainty_reviewed():
    source, target = known_source(), course('2', templates())
    before = digest(source)
    plan = build_guided_plan(source, target)
    assert len(plan['workshops']) == 9 and plan['recipe']['name'] == 'EA105 workshop layout'
    assert all(w['mapping_reviewed'] for w in plan['workshops'])
    # Every placement says why; only placements made by a stated rule count as decided.
    assert all(row['reviewed'] == row['reason'].startswith(('Rule:', 'Page label:', 'Inside source section'))
               for w in plan['workshops'] for row in w['items'])
    assert plan['templates']['introduction']['id'] == '501'
    assert not plan['review_acknowledged'] and digest(source) == before


def test_other_course_uses_actual_modules_and_preserves_gradebook():
    source = sample_plan()['source']
    plan = build_guided_plan(source, course('2', templates()))
    assert [w['title'] for w in plan['workshops']] == ['Workshop']
    assert plan['workshops'][0]['source_module_ids'] == ['10']
    assert plan['workshops'][0]['mapping_reviewed']
    assert not plan['recipe']['gradebook']['enabled']


def test_duplicate_source_titles_are_distinct_drafts_requiring_name_review():
    source = sample_plan()['source']
    extra = copy.deepcopy(source['modules'][0]); extra['Id'] = 20; extra['children'][0]['Id'] = 21
    source['modules'].append(extra)
    plan = build_guided_plan(source, course('2', templates()))
    assert [w['title'] for w in plan['workshops']] == ['Workshop', 'Workshop (2)']
    assert plan['workshops'][1]['source_module_ids'] == ['20']
    assert not plan['workshops'][1]['mapping_reviewed']


def test_template_fallback_uses_unique_source_template_and_refuses_duplicates():
    source = course('1', templates('1'))
    r = automatic_recipe(source)
    found = discover_templates(source, course('2'), r)
    assert found['introduction']['course'] == '1'
    duplicate = copy.deepcopy(source['modules'][0]['children'][0]); duplicate['Id'] = 503
    source['modules'][0]['children'].append(duplicate)
    assert discover_templates(source, course('2'), r)['introduction']['builtin']


def test_course_without_shell_templates_gets_self_contained_standard_styles():
    from transfer_files import render_template
    from transfer_api import choose_templates
    plan = build_guided_plan(sample_plan()['source'], course('2'))
    assert all(t['builtin'] for t in plan['templates'].values())
    assert choose_templates(plan['target'], plan['recipe'], plan['source']) == plan['templates']
    for kind in ('introduction', 'session'):
        page = render_template(plan['templates'][kind]['html'], kind, plan['workshops'][0], plan['recipe'])
        assert '<style>' in page
        if kind == 'introduction':
            assert 'Learning outcomes to be supplied' in page


def test_empty_source_gives_actionable_error():
    with pytest.raises(ValueError, match='no populated'): automatic_recipe(course())


def assignment_plan():
    plan = sample_plan()
    a = {'Id': 30, 'Name': 'Assignment', 'CustomInstructions': {'Html': '<p>Part 1</p>'},
         'Assessment': {'ScoreDenominator': 10, 'Rubrics': [{'Id': 31, 'Name': 'Rubric'}]}}
    plan['source']['assignments'] = [a]
    item = plan['source']['modules'][0]['children'][0]
    item.update(Title='Assignment', ActivityType=3, ToolItemId=30)
    plan['source_hash'] = digest(plan['source'])
    row = plan['workshops'][0]['items'][0]
    row.update(title='Assignment', source=copy.deepcopy(item), section='After Class', reviewed=True)
    return plan, a


def test_unique_copied_assignment_matches_without_ids_but_ambiguous_copies_do_not():
    plan, a = assignment_plan()
    target = copy.deepcopy(a); target['Id'] = 40; target['Assessment']['Rubrics'][0]['Id'] = 41
    plan['target']['assignments'] = [target]
    match_target_assignments(plan)
    assert plan['workshops'][0]['items'][0]['target_assignment_id'] == '40'
    row = plan['workshops'][0]['items'][0]; row['target_assignment_id'] = ''
    duplicate = copy.deepcopy(target); duplicate['Id'] = 42
    plan['target']['assignments'].append(duplicate)
    match_target_assignments(plan)
    assert row['target_assignment_id'] == ''
    duplicate['CustomInstructions']['Html'] = '<p>Different assignment</p>'
    assert not matching_assignment(a, duplicate)


def test_hidden_transfer_metadata_does_not_prevent_equivalent_assignment_match():
    plan, a = assignment_plan()
    target = copy.deepcopy(a)
    target['CustomInstructions']['Html'] += '<p style="display: none;">bpa-transfer:abc:assignment</p>'
    assert matching_assignment(a, target)
    target['CustomInstructions']['Html'] += '<p>Changed instructions</p>'
    assert not matching_assignment(a, target)


@pytest.mark.parametrize('change', ['points', 'submission'])
def test_matching_assignments_require_equal_assessment_and_submission_settings(change):
    _, source = assignment_plan()
    target = copy.deepcopy(source)
    if change == 'points': target['Assessment']['ScoreDenominator'] = 20
    else: target['SubmissionType'] = 2
    assert not matching_assignment(source, target)


@pytest.mark.asyncio
async def test_scan_defaults_to_automatic_plan_and_never_calls_write_api(monkeypatch):
    from transfer_api import TransferAPI
    source, target = known_source(), course('2', templates())
    async def inventory(api, *args): return copy.deepcopy(source if api.course_id == '1' else target)
    async def forbidden(*args, **kwargs): raise AssertionError('Scan attempted a write')
    monkeypatch.setattr(TransferAPI, 'inventory', inventory)
    monkeypatch.setattr(TransferAPI, 'create_object', forbidden)
    class Page: url = 'https://learn.okanagancollege.ca/d2l/home'
    plan = await scan_transfer(Page(), '1', '2')
    assert plan['guided'] and len(plan['workshops']) == 9 and not plan['journal']
