"""Run one saved H5P activity through the production pipeline and verify it.

Requires an explicit saved run and activity name. Uses the app's saved browser
session; uploads are declined because this checks an already uploaded activity.
The original run retains all activities and receives normal progress checkpoints.
"""
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from browser import launch_browser
from content_checker import ContentChecker
from h5p_progress import H5PRun
from h5p_runner import _verify_topic_in_module_strict
from transfer_runner import PlanLock


async def main(args):
    run = H5PRun.load(args.run)
    selected = [row for row in run.data['activities'] if row['name'] == args.activity]
    if len(selected) != 1 or selected[0]['upload'] != 'confirmed':
        raise ValueError('Choose one activity whose upload is confirmed in the saved run.')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    def log(text, tag='info'):
        print(f'[{tag}] {text}', flush=True)
    checker = ContentChecker(run.data['bs_url'], run.data['moodle_url'], log,
                             confirm_fn=lambda text: text.startswith('Phase A done.'))
    handler = checker._h5p
    handler._verify_topic_in_module = _verify_topic_in_module_strict
    handler.grade_all = run.data['grade_all']
    # This command only accepts confirmed cloud uploads. Phase B still locates
    # the exact cloud title; there is no reason to rescan the entire library.
    handler.h5p_phase_b_only = True
    class SelectedRun:
        data = run.data
        def assignments(self, flat):
            return [item for item in run.assignments(flat) if item['name'] == args.activity]
        def checkpoint(self, *values, **details):
            return run.checkpoint(*values, **details)
    handler.progress = SelectedRun()
    if args.trace_navigation:
        chooser = handler._tile_chooser_open
        async def trace_chooser(tab):
            ready = await chooser(tab)
            if ready:
                await tab.screenshot(path=str(output / 'chooser-detected.png'))
                log('CHOOSER MATCH: live Page target captured')
            return ready
        handler._tile_chooser_open = trace_chooser
        native_click = handler._native_click_in_frames
        page_attempt = 0
        async def trace_click(tab, selector, label):
            nonlocal page_attempt
            if label == 'Page tile':
                page_attempt += 1
                if page_attempt == 1:
                    await tab.screenshot(path=str(output / 'page-tile-before.png'))
                for frame in tab.frames:
                    try:
                        candidates = frame.locator(selector + ':visible')
                        log(f'PAGE TARGET {frame.url}: {await candidates.count()} match(es)')
                        if await candidates.count():
                            log(str(await candidates.first.evaluate('(e) => ({html:e.outerHTML,rect:e.getBoundingClientRect().toJSON()})')))
                    except Exception as exc:
                        log(f'PAGE TARGET ERROR: {exc}')
            return await native_click(tab, selector, label)
        handler._native_click_in_frames = trace_click
    # Capture exactly what the production steps leave on screen, including failures.
    for step in ('open_editor_and_get_frame', 'insert_from_list', 'finalize'):
        original = getattr(handler, step)
        async def watched(tab, *values, _step=step, _original=original, **kwargs):
            log(f'LIVE STEP: {_step}')
            result = await _original(tab, *values, **kwargs)
            await tab.screenshot(path=str(output / f'{_step}.png'))
            if not result and _step == 'insert_from_list':
                for frame in tab.frames:
                    try:
                        details = await frame.evaluate('''() => {
                            let hits=[];
                            function walk(root) {
                                for(const e of root.querySelectorAll('*')) {
                                    if (['BUTTON','D2L-BUTTON'].includes(e.tagName) && /grade item|^insert$/i.test(e.textContent.trim()))
                                        hits.push(e.outerHTML.slice(0, 1400));
                                    if(e.shadowRoot) walk(e.shadowRoot);
                                }
                            }
                            walk(document); return hits.slice(-12);
                        }''')
                        log(f'INSERT DIAGNOSTICS {frame.url}: {details}')
                    except Exception:
                        pass
            log(f'LIVE RESULT: {_step}: {bool(result)}')
            return result
        setattr(handler, step, watched)
    with PlanLock(run.path):
        p, browser, context, page = await launch_browser(log_fn=log)
        try:
            await page.goto(run.data['bs_url'], wait_until='domcontentloaded', timeout=30000)
            if '/login' in page.url or 'learn.okanagancollege.ca' not in page.url:
                raise RuntimeError('The saved app session needs a Brightspace login.')
            flat = await checker._fetch_bs_toc(page, run.data['course_id'])
            if not flat:
                raise RuntimeError('Could not read the course content.')
            if args.inspect_destination:
                from unit_overview import BrowserContentAPI
                item = selected[0]
                topics = await page.evaluate('''async ([course, module]) => {
                    const r = await fetch(`/d2l/api/le/1.0/${course}/content/modules/${module}/structure/`);
                    if (!r.ok) throw Error(`Cannot inspect destination: ${r.status}`);
                    return r.json();
                }''', [run.data['course_id'], str(item['bs_module_id'])])
                for topic in topics:
                    log(f"DESTINATION: id={topic.get('Id')} title={topic.get('Title')!r} type={topic.get('TypeIdentifier')!r} url={topic.get('Url')!r}")
                    if 'deposits' in topic.get('Title', '').lower():
                        html = await BrowserContentAPI(page, run.data['course_id'], str(item['bs_module_id'])).get_topic_html(topic['Id'])
                        log(f"DESTINATION EMBED: {ContentChecker._html_contains_h5p_embed(html)}")
                return
            if args.verify_only:
                log('Read-only verification: insertion and progress updates are disabled.')
            else:
                await handler.embed_in_brightspace(context, page, run.data['moodle_items'], flat,
                                                  'https://learn.okanagancollege.ca', run.data['course_id'])
            item = selected[0]
            if not await _verify_topic_in_module_strict(page, run.data['course_id'], item['bs_module_id'], item['name']):
                raise RuntimeError('The activity was not verified in its destination unit.')
            topics = await page.evaluate('''async ([course, module]) => {
                const r = await fetch(`/d2l/api/le/1.0/${course}/content/modules/${module}/structure/`);
                if (!r.ok) throw Error(`Cannot read saved topic: ${r.status}`);
                return r.json();
            }''', [run.data['course_id'], str(item['bs_module_id'])])
            topic = next(row for row in topics if row.get('Title') == item['name'])
            topic_url = f"https://learn.okanagancollege.ca/d2l/le/lessons/{run.data['course_id']}/topics/{topic['Id']}"
            await page.goto(topic_url, wait_until='domcontentloaded')
            loaded = False
            for attempt in range(30):
                for frame in page.frames:
                    try:
                        if await frame.locator('.h5p-content, .h5p-container').first.is_visible():
                            loaded = True
                            break
                    except Exception:
                        continue
                if loaded:
                    break
                await page.wait_for_timeout(1000)
            await page.screenshot(path=str(output / 'verified-topic.png'))
            if not loaded:
                raise RuntimeError('The page is saved, but the H5P player did not finish loading within 30 seconds.')
            log('The saved H5P player is visible.', 'success')
            log(f'Saved topic: {topic_url}')
            log(f"VERIFIED: {item['name']} in {item['bs_module_title']}", 'success')
            if not args.verify_only and run.counts()[2] == 0:
                run.data['status'] = 'complete'
                run.save()
            log(run.label())
        finally:
            await browser.close()
            await p.stop()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--activity', required=True)
    parser.add_argument('--output', default='test-results/h5p-one-live')
    parser.add_argument('--trace-navigation', action='store_true')
    parser.add_argument('--verify-only', action='store_true', help='Only open and verify the existing saved page; do not insert or update course progress.')
    parser.add_argument('--inspect-destination', action='store_true', help='Read destination topic metadata and embed markers only; never insert.')
    asyncio.run(main(parser.parse_args()))
