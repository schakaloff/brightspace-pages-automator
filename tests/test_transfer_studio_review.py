"""Real browser checks for Review gating; all course operations stay offline."""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import transfer_outline as outline
from transfer_runner import save_plan
from transfer_studio import TransferStudio
from test_transfer_structure import source_course
from test_transfer_outline import target_course


@pytest.mark.parametrize('width,height', [(1440, 1000), (390, 844)])
def test_review_distinguishes_blockers_from_notes_and_keeps_create_reachable(tmp_path, width, height):
    plan = outline.build_outline_plan(source_course(), target_course([
        {'Id': 500, 'Type': 0, 'Title': 'Week 1', 'children': []}]), 'mirror')
    for index in range(12):
        outline.add_page(plan, plan['outline'][0]['id'], 'introduction', f'Introduction {index + 1}')
    outline.unplace(plan, ['11'])
    path = tmp_path / (plan['id'] + '.json')
    save_plan(plan, path)
    server = TransferStudio(tmp_path, lambda request: None, saved_path=path, request_create=lambda request: None)
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel='chrome', headless=True)
            except Exception:
                browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page(viewport={'width': width, 'height': height})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(server.start())
                page.locator('#review-step').click()
                expect(page.locator('#review-issues .error')).to_have_count(2)
                expect(page.locator('#review-notes-summary')).to_contain_text('13 informational notes')
                assert not page.locator('#review-notes').evaluate('(e) => e.open')
                expect(page.locator('#review-note-list')).to_be_hidden()
                page.locator('#confirm-create').check()
                expect(page.locator('#workflow-next')).to_be_disabled()
                expect(page.locator('#create-blockers')).to_contain_text('Fix 2 blocking issues')
                page.get_by_role('button', name='Find unplaced items', exact=True).click()
                expect(page.locator('#arrange')).to_be_visible()
                expect(page.locator('#waiting-filter')).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('#source-tree .row.selected')).to_contain_text('Read chapter 2')
                page.locator('#selection-actions').click()
                page.get_by_role('menuitem', name='Leave out of transfer', exact=True).click()
                page.locator('#review-step').click()
                expect(page.locator('#review-issues .error')).to_have_count(1)
                page.get_by_role('button', name='Add into the existing module', exact=True).click()
                expect(page.locator('#review-issues .error')).to_have_count(0)
                expect(page.locator('#confirm-create')).not_to_be_checked()
                expect(page.locator('#create-blockers')).to_contain_text('Confirm the destination')
                page.locator('#confirm-create').check()
                expect(page.locator('#workflow-next')).to_be_enabled()
                expect(page.locator('#create-blockers')).to_have_text('Ready to create.')
                page.locator('#review-notes-summary').click()
                expect(page.locator('#review-note-list .issue-location').first).to_have_text('Week 1 / Introduction 1')
                expect(page.locator('#review-note-list')).to_contain_text('start hidden')
                expect(page.locator('#workflow-next')).to_be_enabled()
                assert page.locator('#workflow-next').evaluate('(e) => {const r=e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight;}')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors
            finally:
                browser.close()
    finally:
        server.close()


@pytest.mark.parametrize('width,height', [(1440, 1000), (390, 844)])
def test_review_resolves_a_skipped_assignment_link_and_can_undo(tmp_path, width, height):
    from test_transfer_activity_links import plan as linked_plan
    from transfer_runner import load_plan

    plan = linked_plan()
    path = tmp_path / (plan['id'] + '.json')
    save_plan(plan, path)
    server = TransferStudio(tmp_path, lambda request: None, saved_path=path, request_create=lambda request: None)
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel='chrome', headless=True)
            except Exception:
                browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page(viewport={'width': width, 'height': height})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(server.start())
                page.locator('#review-step').click()
                expect(page.locator('#review-issues .error')).to_have_count(1)
                expect(page.locator('#review-issues')).to_contain_text('Read chapter 2')
                expect(page.locator('#review-issues')).to_contain_text('Week 1 Essay')
                page.get_by_role('button', name='Use the existing destination assignment', exact=True).click()
                expect(page.locator('#review-issues .error')).to_have_count(0)
                assert load_plan(path)['activity_link_targets'] == {'70': '700'}
                assert '13' in load_plan(path)['skipped']
                expect(page.locator('#review-notes-summary')).to_contain_text('1 informational note')
                page.locator('#review-notes-summary').click()
                expect(page.locator('#review-note-list')).to_contain_text("keeping that assignment's settings and rubric")
                page.locator('#confirm-create').check()
                expect(page.locator('#workflow-next')).to_be_enabled()
                page.locator('#arrange-step').click()
                expect(page.locator('#undo')).to_be_enabled()
                page.locator('#undo').focus()
                page.keyboard.press('Control+z')
                expect(page.locator('#redo')).to_be_enabled()
                page.locator('#review-step').click()
                expect(page.locator('#review-issues .error')).to_have_count(1)
                assert not load_plan(path).get('activity_link_targets')
                expect(page.locator('#workflow-next')).to_be_disabled()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors
            finally:
                browser.close()
    finally:
        server.close()
