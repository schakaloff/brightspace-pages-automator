"""Run exactly one prepared SCORM transfer; capture its saved Brightspace topic."""
import argparse
import asyncio
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from browser import launch_browser
from scorm_packages import SCORMRun, RUNS_DIR
from scorm_runner import transfer_scorm

BS = 'https://learn.okanagancollege.ca/d2l/le/lessons/23840/units/994179'
MOODLE = 'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972'


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--activity', required=True)
    parser.add_argument('--verify-only', action='store_true')
    options = parser.parse_args()
    directory = RUNS_DIR
    run = SCORMRun.open(BS, MOODLE, directory)
    row = next(row for row in run.data['activities'] if row['key'] == options.activity)
    if not options.verify_only:
        run = await transfer_scorm(BS, MOODLE, [(row['key'], row['module_id'])],
                                  lambda message, tag='info': print(message, flush=True), directory=directory)
        row = next(row for row in run.data['activities'] if row['key'] == options.activity)
    if not row.get('topic_id'):
        raise RuntimeError('No saved topic ID is available for verification.')
    p, browser, context, page = await launch_browser()
    try:
        await page.goto(f"https://learn.okanagancollege.ca/d2l/le/lessons/{run.data['course_id']}/topics/{row['topic_id']}", wait_until='domcontentloaded')
        loaded = False
        for _ in range(120):
            for frame in page.frames:
                if '/scormcontent/index.html' not in frame.url: continue
                title = frame.get_by_text(re.compile(re.escape(row.get('package_title', row['name'])), re.I)).first
                if await title.count() and await title.is_visible():
                    print('PLAYER CONTENT:', (await frame.locator('body').inner_text())[:1500], flush=True)
                    loaded = True
                    break
            if loaded: break
            await page.wait_for_timeout(1000)
        if not loaded:
            await page.screenshot(path='test-results/scorm-player-not-ready.png')
            raise RuntimeError('Saved topic exists, but its package title never became visible in the SCORM player.')
        for frame in page.frames:
            print('FRAME:', frame.url.split('?')[0], flush=True)
            if 'smart-curriculum' in frame.url:
                print('TOPIC TEXT:', (await frame.locator('body').inner_text())[-1800:], flush=True)
        await page.screenshot(path=f"test-results/scorm-verified-topic-{row['key']}.png")
        print('VERIFIED TOPIC:', row['topic_id'], flush=True)
    finally:
        await browser.close()
        await p.stop()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    asyncio.run(main())
