"""Read-only template/API capability probe for the supplied sandbox pair."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from config import SESSION_FILE
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=SESSION_FILE)
        page = await context.new_page()
        await page.goto('https://learn.okanagancollege.ca/d2l/home')
        result = await page.evaluate("""async () => {
          const result = {};
          for (const [key,path] of Object.entries({
            versions:'/d2l/api/versions/',
            introduction:'/d2l/api/le/1.82/25707/content/topics/993983/file',
            session:'/d2l/api/le/1.82/25707/content/topics/994004/file',
            root:'/d2l/api/le/1.82/25708/content/modules/993840/structure/',
            categories98:'/d2l/api/le/1.98/25707/grades/categories/',
            outcomes:'/d2l/api/le/1.99/25708/lo/outcomeSets/',
            alignments:'/d2l/api/le/1.99/25708/lo/alignments/'
          })) {
            const r=await fetch(path,{credentials:'include'});
            result[key]={status:r.status,body:await r.text()};
          }
          return result;
        }""")
        out=Path('transfer-investigation')
        (out/'capabilities.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        for key,value in result.items():
            if key in ('introduction','session') and value['status']==200:
                (out/(key+'.html')).write_text(value['body'],encoding='utf-8')
                from bs4 import BeautifulSoup
                s=BeautifulSoup(value['body'],'html.parser')
                print(key,'headings',[(h.name,h.get_text(' ',strip=True)) for h in s.find_all(['h1','h2','h3','h4'])])
            else:
                print(key,value['status'],value['body'][:2200])
        await browser.close()
asyncio.run(main())
