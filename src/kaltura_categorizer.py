import asyncio
import re
import os
import sys

from playwright.async_api import async_playwright

from config import USERDATA_DIR, SESSION_FILE

KMC_URL = "https://kmc.cap2.ovp.kaltura.com/index.php/kmcng/content/entries/list"
KMC_SESSION_FILE = str(USERDATA_DIR / "kmc_session.json")


class KalturaCategorizer:

    async def scan_moodle_course(self, moodle_course_url: str) -> list[dict]:
        """Scrape all kalvidres activity pages in a Moodle course.

        Returns list of {entry_id, name, moodle_url, section_name}.
        """
        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
                context = await browser.new_context(storage_state=storage)
                page = await context.new_page()

                await page.goto(moodle_course_url, wait_until="networkidle", timeout=30000)

                # Collect all kalvidres links on the course page
                link_items = await page.evaluate("""() => {
                    const results = [];
                    document.querySelectorAll('li.section, li.section.main').forEach(section => {
                        const heading = section.querySelector('.sectionname, h3, h4');
                        const sectionName = heading ? heading.textContent.trim() : '(unnamed section)';
                        section.querySelectorAll('a[href*="mod/kalvidres/view.php"]').forEach(a => {
                            results.push({href: a.href, section_name: sectionName});
                        });
                    });
                    return results;
                }""")
                # deduplicate by href, preserve order
                seen = set()
                deduped = []
                for item in link_items:
                    if item["href"] not in seen:
                        seen.add(item["href"])
                        deduped.append(item)
                link_items = deduped

                for item in link_items:
                    link = item["href"]
                    section_name = item["section_name"]
                    try:
                        await page.goto(link, wait_until="networkidle", timeout=20000)

                        iframe_src = await page.evaluate("""() => {
                            const f = document.querySelector('iframe#contentframe');
                            return f ? f.src : '';
                        }""")
                        m = re.search(r'entryid%2F([\w_]+)', iframe_src, re.IGNORECASE)
                        if not m:
                            continue
                        entry_id = m.group(1)

                        title = await page.title()
                        name = re.sub(r'\s*\|\s*OCmoodle\s*$', '', title).strip()

                        results.append({
                            "entry_id": entry_id,
                            "name": name,
                            "moodle_url": link,
                            "section_name": section_name,
                        })
                    except Exception as e:
                        print(f"[kaltura scanner] skipped {link}: {repr(e)}", file=sys.stderr)
                        continue
            finally:
                await browser.close()
        return results

    async def categorize_entries(
        self,
        entries: list[dict],
        brightspace_course_id: str,
        log_fn,
    ) -> None:
        """For each entry: search KMC by entry ID, select, add to category."""
        async with async_playwright() as p:
            context, browser = await self._get_kmc_context(p)
            try:
                page = await context.new_page()

                for entry in entries:
                    entry_id = entry["entry_id"]
                    name = entry["name"]
                    try:
                        await page.goto(KMC_URL, wait_until="networkidle", timeout=20000)

                        # Search by entry ID
                        search = page.locator("input[type='text']").first
                        await search.click()
                        await search.click(click_count=3)
                        await search.type(entry_id)
                        await page.keyboard.press("Enter")
                        await page.wait_for_timeout(2000)

                        # Select checkbox on first result row
                        checkbox = page.locator("p-tablecheckbox .p-checkbox-box").first
                        await checkbox.click()
                        await page.wait_for_timeout(500)

                        # Open Actions dropdown
                        actions_btn = page.locator("button:has-text('Actions')").first
                        await actions_btn.click()
                        await page.wait_for_timeout(500)

                        # Add / Remove Categories → Add To Categories
                        await page.locator(".p-menuitem-text:has-text('Add / Remove Categories')").click()
                        await page.wait_for_timeout(400)
                        await page.locator(".p-menuitem-text:has-text('Add To Categories')").first.click()
                        await page.wait_for_timeout(800)

                        # Type Brightspace course ID in category search
                        cat_input = page.locator("input[placeholder='Search Categories']")
                        await cat_input.click()
                        await cat_input.type(brightspace_course_id)
                        await page.wait_for_timeout(1000)

                        # Select first autocomplete result
                        await page.locator(".p-autocomplete-item, li[role='option']").first.click()
                        await page.wait_for_timeout(500)

                        # Confirm
                        await page.locator("button:has-text('Apply to all selected entries')").click()
                        await page.wait_for_timeout(1000)

                        log_fn(f"✓ {name}", "success")

                    except Exception as e:
                        log_fn(f"✗ {name}: {e}", "error")

                await context.storage_state(path=KMC_SESSION_FILE)
            finally:
                await browser.close()

    async def _get_kmc_context(self, playwright):
        """Return a logged-in KMC browser context.

        Loads kmc_session.json if it exists and is still valid.
        Otherwise opens a visible browser for manual SSO login, waits
        for the KMC entries list page to load, then saves the session.
        """
        browser = await playwright.chromium.launch(headless=False)
        try:
            storage = KMC_SESSION_FILE if os.path.exists(KMC_SESSION_FILE) else None
            context = await browser.new_context(storage_state=storage)
            page = await context.new_page()
            await page.goto(KMC_URL, timeout=30000)

            # If redirected away from KMC (SSO login), wait for user
            if "kmcng/content/entries/list" not in page.url:
                await page.wait_for_url("**/kmcng/content/entries/list**", timeout=120000)

            await context.storage_state(path=KMC_SESSION_FILE)
            await page.close()
            return context, browser
        except Exception:
            await browser.close()
            raise
