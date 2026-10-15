"""Read-only preview or explicit additive smoke test for the supplied pair.

Use scan or guided-scan for read-only proposals. smoke creates a separately
named, hidden verification workshop, skips rubric assignments, and exercises
file copying and resumability without deciding actual workshop timing.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from config import SESSION_FILE
from playwright.async_api import async_playwright
from transfer_plan import preview_text, workshop_recipe
from transfer_runner import apply_transfer, load_plan, save_plan, scan_transfer
from transfer_api import TransferAPI

async def main(mode, trace_link=False):
    out = Path('transfer-investigation'); out.mkdir(exist_ok=True)
    if trace_link:
        from content_checker import ContentChecker
        original_click = ContentChecker._click_visible_exact_text
        async def traced_click(self, tab, text):
            candidates = [(frame.url, await frame.get_by_text(text, exact=True).count()) for frame in tab.frames]
            result = await original_click(self, tab, text)
            print('Picker check', text, result, candidates, flush=True)
            return result
        ContentChecker._click_visible_exact_text = traced_click
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=SESSION_FILE)
        page = await context.new_page()
        await page.goto('https://learn.okanagancollege.ca/d2l/home')
        log = lambda message, level='info': print(f'[{level}] {message}', flush=True)
        smoke = mode in ('smoke', 'smoke-assignment', 'smoke-custom', 'smoke-split', 'verify-rerun')
        guided = mode == 'guided-scan'
        rerun = mode == 'verify-rerun'
        path = out / ('guided-workshop-plan.json' if guided else 'smoke-assignment-plan.json' if rerun else mode + '-plan.json' if smoke else 'workshop-plan.json')
        if smoke and path.exists() and load_plan(path).get('journal'):
            plan = load_plan(path)
        else:
            recipe = workshop_recipe()
            if smoke:
                recipe['workshops'] = [{
                    'title': 'Structured Transfer verification — ' + ('Employment assignment' if mode == 'smoke-assignment' else 'Observation files'), 'hours': 6,
                    'source_titles': ['Resumes and Cover Letters'] if mode == 'smoke-assignment' else ['Observation Day'],
                    'introduction': 'Sandbox verification of structured transfer and independent course files.',
                    'outcomes': ['Verify that copied resources resolve within this sandbox course.'],
                    'session': 'Technical verification only. Workshop teaching sequence still requires instructor review.'}]
                recipe['gradebook']['enabled'] = mode == 'smoke-assignment'
                if mode == 'smoke-split':
                    # EA105 rules: pages split at their own labels and copied as plain content.
                    recipe['workshops'][0].update(title='Structured Transfer verification — Physical Health split',
                                                  source_titles=['Physical Health and Safety'])
                if mode == 'smoke-custom':
                    # A non-EA105 structure: no Before/After folders, items placed
                    # directly in the unit, Session Overview first among them.
                    recipe['sections'] = [
                        {'title': 'Overview', 'kind': 'introduction'},
                        {'title': 'Workshop Files', 'kind': 'inline', 'default': True, 'session_overview': True},
                        {'title': 'Assessment', 'kind': 'folder', 'assignments': True}]
                    recipe['workshops'][0]['title'] = 'Structured Transfer verification — Custom structure'
            if not smoke:
                original_json = TransferAPI._json
                async def read_only(self, route, method='GET', payload=None):
                    if method != 'GET': raise AssertionError('A preview attempted a course mutation')
                    return await original_json(self, route, method, payload)
                TransferAPI._json = read_only
            plan = await scan_transfer(page, '25708', '25707', None if guided else recipe, log)
            if smoke:
                workshop = plan['workshops'][0]; workshop['mapping_reviewed'] = True
                for row in workshop['items']:
                    if mode == 'smoke-split':
                        # Keep the rules' placements; the rubric assignment needs Brightspace's native copy.
                        if row['source'].get('ActivityType') == 3: row['section'] = 'Skip'
                        row['reviewed'] = True
                        continue
                    if mode == 'smoke-custom':
                        row['section'] = 'Workshop Files' if row['id'] in ('993920', '993921') else 'Skip'
                    elif mode == 'smoke-assignment':
                        row['section'] = 'After Class' if row['source'].get('ActivityType') == 3 else 'Class Materials'
                    else:
                        row['section'] = 'Class Materials' if row['id'] in ('993920','993921') else 'Skip'
                    row['reviewed'] = True
                    row['reason'] = 'Technical sandbox smoke test; does not decide production workshop timing.'
                plan['review_acknowledged'] = True
            save_plan(plan, path)
        (out / ('guided-workshop-preview.txt' if guided else mode + '-preview.txt' if smoke else 'workshop-preview.txt')).write_text(preview_text(plan), encoding='utf-8')
        print(preview_text(plan), flush=True)
        if smoke:
            if rerun:
                assert plan.get('validation') and all(plan['validation'].values()), 'First complete the assignment smoke test.'
                original_json = TransferAPI._json
                async def get_only(self, route, method='GET', payload=None):
                    if method != 'GET': raise AssertionError('Completed-plan rerun attempted a course mutation: ' + method + ' ' + route)
                    return await original_json(self, route, method, payload)
                async def no_upload(*args, **kwargs): raise AssertionError('Completed-plan rerun attempted an upload')
                TransferAPI._json = get_only
                TransferAPI.upload_file = no_upload
                before = {key:entry.get('result', {}).get('Id') for key,entry in plan['journal'].items()}
            result = await apply_transfer(page, plan, path, '25707', True, log)
            print(result['report'], flush=True)
            if not all(result.get('validation', {}).values()): raise RuntimeError('Smoke validation failed')
            if rerun:
                assert before == {key:entry.get('result', {}).get('Id') for key,entry in result['journal'].items()}
                print('PASS completed-plan rerun: identical IDs, zero API writes and zero uploads.', flush=True)
        await browser.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('mode', choices=['scan', 'guided-scan', 'smoke', 'smoke-assignment', 'smoke-custom', 'smoke-split', 'verify-rerun'])
    parser.add_argument('--trace-link', action='store_true')
    args = parser.parse_args()
    try: asyncio.run(main(args.mode, args.trace_link))
    except Exception as exc:
        print(str(exc).split('Call log:')[0].strip(), flush=True)
        raise SystemExit(1)
