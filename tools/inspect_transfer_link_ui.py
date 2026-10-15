"""Read rendered native controls for the saved hidden assignment smoke test."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from config import SESSION_FILE
from transfer_runner import load_plan
from playwright.async_api import async_playwright
from content_checker import ContentChecker

async def main():
    plan = load_plan('transfer-investigation/smoke-assignment-plan.json')
    ident = plan['journal']['w0:section:After Class']['result']['Id']
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=SESSION_FILE)
        page = await context.new_page()
        await page.goto(f'https://learn.okanagancollege.ca/d2l/le/lessons/25707/units/{ident}', wait_until='domcontentloaded')
        await page.wait_for_timeout(12000)
        for frame in page.frames:
            button = frame.locator('d2l-button[aria-label="Add Existing"]')
            if await button.count() and await button.first.is_visible():
                await button.first.click()
                await page.wait_for_timeout(6000)
                break
        for frame in page.frames:
            item = frame.get_by_text('Assignment', exact=True)
            if await item.count():
                print('Assignment matches', frame.url, await item.count(), flush=True)
                for i in range(await item.count()):
                    candidate = item.nth(i)
                    print(await candidate.evaluate('e => e.outerHTML.slice(0,800)'), 'visible', await candidate.is_visible(), flush=True)
        checker = object.__new__(ContentChecker)
        print('Shared helper result', await checker._click_visible_exact_text(page, 'Assignment'), flush=True)
        await page.wait_for_timeout(5000)
        for frame in page.frames:
            items = frame.get_by_text('Resumes and Cover Letters Assignment', exact=True)
            for i in range(await items.count()):
                print('Picker candidate', await items.nth(i).evaluate('e => ({tag:e.tagName,html:e.outerHTML,parent:e.parentElement.outerHTML.slice(0,3000)})'), flush=True)
        for frame in page.frames:
            print('FRAME', frame.url, flush=True)
            print(await frame.locator('body').inner_text(), flush=True)
            print(await frame.evaluate("""() => {
                const result=[];
                function walk(root){for(const e of root.querySelectorAll('*')){
                    if(e.shadowRoot) walk(e.shadowRoot);
                    if(/button|menu-item|list-item/i.test(e.tagName) && /assign|select|add|insert/i.test(e.textContent + e.getAttribute('aria-label'))) result.push({tag:e.tagName,id:e.id,label:e.getAttribute('aria-label'),text:e.textContent.trim().slice(0,100)});
                }} walk(document); return result;
            }"""), flush=True)
        await page.screenshot(path='transfer-investigation/link-ui.png', full_page=True)
        await browser.close()

if __name__ == '__main__': asyncio.run(main())
