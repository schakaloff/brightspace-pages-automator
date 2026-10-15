"""Read-only Kaltura scan check for the supplied Moodle course."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from kaltura_categorizer import KalturaCategorizer, MOODLE_SESSION_FILE
from playwright.async_api import async_playwright
from config import SESSION_FILE

COURSE = 'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972'


async def main():
    if '--inspect-video' in sys.argv:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(storage_state=MOODLE_SESSION_FILE)
                page = await context.new_page()
                await page.goto(COURSE, wait_until='domcontentloaded')
                await page.wait_for_timeout(1500)
                await KalturaCategorizer()._load_full_course_listing(page, COURSE, lambda *a: print(*a,flush=True))
                rows = await page.locator('li.activity').evaluate_all('''els=>els.filter(e=>/perio|suture/i.test(e.textContent)).map(e=>({
                    name:e.querySelector('.activityname')?.textContent.trim(),cls:e.className,
                    links:[...e.querySelectorAll('a[href]')].filter(a=>/demo/i.test(a.textContent)).map(a=>({text:a.textContent.trim(),href:a.href}))
                }))''')
                print('ACTIVITIES:',rows,flush=True)
                for row in rows:
                    for link in row['links']:
                        await page.goto(link['href'],wait_until='domcontentloaded')
                        await page.wait_for_timeout(2500)
                        print('VIDEO PAGE:',page.url.split('?')[0],await page.title(),flush=True)
                        print('PLAYER DOM:',await page.locator('#region-main').evaluate('e=>e.innerHTML.slice(0,16000)'),flush=True)
            finally:
                await browser.close()
        return
    if '--inspect' not in sys.argv:
        results = await KalturaCategorizer().scan_moodle_course(COURSE)
        print('RESULT:', results, flush=True)
        return
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(storage_state=MOODLE_SESSION_FILE)
            page = await context.new_page()
            await page.goto(COURSE, wait_until='domcontentloaded')
            await page.wait_for_timeout(2500)
            print('COURSE:',page.url.split('?')[0],flush=True)
            print(await page.evaluate('''()=>({
                bodyClass:document.body.className,
                title:document.title,
                sections:[...document.querySelectorAll('li.section')].map(s=>({id:s.id,
                    name:s.querySelector('.sectionname,h3,h4')?.textContent.trim(), activities:s.querySelectorAll('li.activity').length})),
                sectionsLinks:[...document.querySelectorAll('a[href*="section="]')].map(a=>({name:a.textContent.trim(),href:a.href})),
                activityLinks:[...document.querySelectorAll('#page-content a[href*="/mod/"]')].map(a=>({name:a.textContent.trim().slice(0,100),href:a.href})),
                editToggle:document.querySelector('#user-editing-switch')?.checked
            })'''),flush=True)
            print('SECTION DOM:',await page.locator('#section-2').evaluate('(e)=>e.outerHTML.slice(0,7000)'),flush=True)
            print('CONTROLS:',await page.locator('#page-content [aria-expanded],input[type="checkbox"],a[href*="switchrole"]').evaluate_all('(els)=>els.map(e=>({tag:e.tagName,id:e.id,text:e.textContent.trim().slice(0,100),action:e.getAttribute("data-action"),expanded:e.getAttribute("aria-expanded"),name:e.name,checked:e.checked}))'),flush=True)
            shared = await browser.new_context(storage_state=SESSION_FILE)
            shared_page = await shared.new_page()
            await shared_page.goto(COURSE, wait_until='domcontentloaded')
            await shared_page.wait_for_timeout(2000)
            print('SHARED SESSION:', await shared_page.evaluate('''()=>({path:location.pathname,editing:document.body.classList.contains('editing'),sections:document.querySelectorAll('li.section').length,activities:document.querySelectorAll('li.activity').length})'''),flush=True)
        finally:
            await browser.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    asyncio.run(main())
