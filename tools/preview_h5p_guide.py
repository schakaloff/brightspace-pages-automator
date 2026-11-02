"""Record a local guide preview. No Moodle, Brightspace, or saved sessions used.

Run: .venv/Scripts/python.exe tools/preview_h5p_guide.py
Outputs: test-results/h5p-modern-cursor-preview.png/.webm and h5p-modern-cursor-styles.png
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from playwright.async_api import async_playwright
from h5p_browser_guide import H5PBrowserGuide


HTML = '''<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:#203149;font:16px/1.5 system-ui}
header{padding:24px 42px;background:#fff;border-bottom:1px solid #dbe3ee;display:flex;justify-content:space-between}
header span{color:#61748d;font-size:13px}main{max-width:940px;margin:42px auto}
.eyebrow{font-size:12px;letter-spacing:2px;color:#51708d;font-weight:700}h1{font-size:32px;margin:8px 0}
p{color:#62748a}.card{background:#fff;border:1px solid #dce5ee;border-radius:18px;padding:28px;margin-top:26px}
.row{display:flex;align-items:center;justify-content:space-between;padding:16px 0;border-bottom:1px solid #eaf0f5}
.row:last-child{border:0}small{display:block;color:#6d8095}button{border:0;background:#21587a;color:#fff;
border-radius:10px;padding:13px 20px;font:600 14px system-ui;cursor:pointer}button:focus{outline:3px solid #85d8c8}
.result{color:#22836b;font-weight:600}footer{position:fixed;right:24px;bottom:20px;font-size:12px;color:#6d8095}
</style></head><body><header><strong>Brightspace · H5P transfer</strong><span>LOCAL PREVIEW · NO COURSES CONNECTED</span></header>
<main><div class="eyebrow">YOUR ACTIVITIES</div><h1>A closer look at your transfer</h1>
<p>The guide follows real clicks. Background work gets a short, clear message.</p>
<div class="card"><div class="row"><div><strong>Practice: Cell structure</strong><small>Moodle → Week 3 · Biology</small></div>
<button id="library" onclick="this.textContent='Library opened'">Open H5P library</button></div>
<div class="row"><div><strong>Cell structure</strong><small>Already uploaded · ready to reuse</small></div>
<button id="insert" onclick="this.textContent='Activity selected'">Insert activity</button></div>
<div class="row"><div><strong>Week 3 · Biology</strong><small>Brightspace destination page</small></div>
<button id="save" onclick="this.textContent='Page saved'">Save and Close</button></div>
<div class="row"><span>Saved page verification</span><span class="result" id="result">Waiting for transfer</span></div></div>
</main><footer>Preview fixture · transfer controls and messages are illustrative</footer></body></html>'''


async def main():
    output = Path(__file__).resolve().parents[1] / 'test-results'
    output.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport=dict(width=1200, height=800),
            record_video_dir=str(output / 'h5p-guide-recordings'), record_video_size=dict(width=1200, height=800))
        guide = H5PBrowserGuide()
        await guide.install(context)
        await context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=HTML))
        page = await context.new_page()
        await page.goto('http://h5p-preview.test/')
        await page.wait_for_timeout(300)
        for selector, message, label in (
            ('#library', 'Opening the H5P library…', 'Open H5P library'),
            ('#insert', 'Already uploaded · reusing activity', 'Insert activity'),
            ('#save', 'Saving the Brightspace page…', 'Save and Close'),
        ):
            await guide.status(message, activity='Practice: Cell structure', current=3, total=12)
            await guide.point(page.locator(selector), label)
            await page.wait_for_timeout(600)
            await page.locator(selector).click()
            await page.wait_for_timeout(700)
        await guide.status('Checking the saved page and its title…', activity='Practice: Cell structure', current=3, total=12)
        await page.wait_for_timeout(1000)
        await page.locator('#result').evaluate("el => el.textContent = 'Page saved and verified'")
        await guide.status('Brightspace page saved and verified', activity='Practice: Cell structure',
            current=3, total=12, kind='success')
        await guide.point(page.locator('#save'), 'Save and Close')
        await page.screenshot(path=str(output / 'h5p-modern-cursor-preview.png'))
        shapes = await page.evaluate('''() => {
            const root = document.querySelector('#__h5p_browser_guide').shadowRoot;
            return [root.querySelector('.cursor-arrow').outerHTML, root.querySelector('.cursor-hand').outerHTML];
        }''')
        await page.wait_for_timeout(1200)
        video = page.video
        await context.close()
        await video.save_as(str(output / 'h5p-modern-cursor-preview.webm'))
        # Show enlarged copies of the actual guide artwork for a clear style review.
        samples = await browser.new_context(viewport=dict(width=700, height=260))
        sample_page = await samples.new_page()
        await sample_page.set_content('''<style>
            *{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:#203149;font:14px system-ui}
            main{display:flex;gap:20px;padding:24px}section{flex:1;background:white;border:1px solid #dce5ee;
            border-radius:14px;text-align:center;padding:18px}figure{height:110px;margin:0;display:grid;place-items:center}
            .arrow svg{width:60px;height:84px}.hand svg{width:72px;height:90px}
            p{margin:8px 0 0;color:#62748a;font-size:12px}
            </style><main><section><figure class="arrow">''' + shapes[0] + '''</figure>
            <strong>Modern navy arrow</strong><p>Between controls</p></section>
            <section><figure class="hand">''' + shapes[1] + '''</figure>
            <strong>Matching hand</strong><p>At clickable controls</p></section></main>''')
        await sample_page.screenshot(path=str(output / 'h5p-modern-cursor-styles.png'))
        await samples.close()
        await browser.close()
        print(output / 'h5p-modern-cursor-preview.png')
        print(output / 'h5p-modern-cursor-preview.webm')
        print(output / 'h5p-modern-cursor-styles.png')


if __name__ == '__main__':
    asyncio.run(main())
