import asyncio
import re
import os
import sys

from playwright.async_api import async_playwright

from config import USERDATA_DIR, SESSION_FILE
from automator import _find_locator_any_frame

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

    async def get_bs_modules(self, bs_url: str) -> list[dict]:
        """Return [{id, title}] for all modules in the Brightspace course via D2L TOC API."""
        from content_checker import _extract_course_id
        course_id = _extract_course_id(bs_url)
        if not course_id:
            raise ValueError(f"Could not extract course ID from URL: {bs_url}")
        base_url = "/".join(bs_url.split("/")[:3])

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
                context = await browser.new_context(storage_state=storage)
                page = await context.new_page()
                await page.goto(
                    f"{base_url}/d2l/le/content/{course_id}/home",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                modules = await page.evaluate(
                    """async (courseId) => {
                        const resp = await fetch(
                            `/d2l/api/le/1.0/${courseId}/content/toc`,
                            {credentials: 'include'}
                        );
                        if (!resp.ok) return null;
                        const toc = await resp.json();
                        const result = [];
                        function collect(modules) {
                            for (const m of (modules || [])) {
                                result.push({id: String(m.ModuleId), title: m.Title || '(unnamed)'});
                                collect(m.Modules);
                            }
                        }
                        collect(toc.Modules || []);
                        return result;
                    }""",
                    course_id,
                )
                return modules or []
            finally:
                await browser.close()

    async def _get_embed_code(self, kmc_page, entry_id: str, log_fn) -> "str | None":
        """Navigate KMC to entry, open Share & Embed, return embed code from textarea."""
        log_fn(f"  KMC: searching {entry_id}…", "dim")
        await kmc_page.goto(KMC_URL, wait_until="networkidle", timeout=20000)

        search = kmc_page.locator("input[type='text']").first
        await search.click()
        await search.click(click_count=3)
        await search.type(entry_id)
        await kmc_page.keyboard.press("Enter")
        await kmc_page.wait_for_timeout(2000)

        rows = kmc_page.locator("p-table tbody tr, tr.kEntry")
        if await rows.count() == 0:
            log_fn(f"  ⚠ Entry {entry_id} not found in KMC", "warning")
            return None
        await rows.first.click()
        await kmc_page.wait_for_timeout(1500)

        try:
            share_link = kmc_page.locator(".kPreviewAndEmbedContainer a").first
            await share_link.wait_for(state="visible", timeout=5000)
            await share_link.click()
        except Exception:
            log_fn(f"  ⚠ Share & Embed link not found for {entry_id}", "warning")
            return None
        await kmc_page.wait_for_timeout(1500)

        try:
            code = await kmc_page.locator("textarea").first.input_value(timeout=5000)
            if code and code.strip():
                return code.strip()
        except Exception:
            pass

        log_fn(f"  ⚠ Embed textarea empty for {entry_id}", "warning")
        return None

    async def _create_bs_page(
        self,
        bs_page,
        base_url: str,
        course_id: str,
        module_id: str,
        title: str,
        html: str,
        log_fn,
    ) -> bool:
        """Navigate to a Brightspace module, create a new Page, paste embed HTML, save."""
        log_fn(f"  BS: opening module {module_id}…", "dim")

        # Navigate to module view (URL format verified against D2L; adjust if needed)
        module_url = f"{base_url}/d2l/le/content/{course_id}/modules/{module_id}/home"
        try:
            await bs_page.goto(module_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log_fn(f"  ⚠ Navigation to module failed: {e}", "warning")
        await bs_page.wait_for_timeout(2000)

        # ── Shadow-DOM helpers ─────────────────────────────────────────────────
        _JS_DEEP_CLICK = """(selector) => {
            function deepFind(root, sel, depth) {
                if (depth === 0) return null;
                const el = root.querySelector(sel);
                if (el) return el;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot, sel, depth - 1);
                        if (f) return f;
                    }
                }
                return null;
            }
            const el = deepFind(document, selector, 10);
            if (!el) return false;
            if (el.shadowRoot) {
                const inner = el.shadowRoot.querySelector('button');
                if (inner) { inner.click(); return true; }
            }
            el.click();
            return true;
        }"""

        async def js_click(selector: str) -> bool:
            frames = [bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]
            for ctx in frames:
                try:
                    if await ctx.evaluate(_JS_DEEP_CLICK, selector):
                        return True
                except Exception:
                    pass
            return False

        # ── Click "Create New" ─────────────────────────────────────────────────
        if not await js_click('button[aria-label="Create New"]'):
            log_fn("  ✗ 'Create New' button not found", "error")
            return False
        await bs_page.wait_for_timeout(1000)

        # ── Click "Page" tile ──────────────────────────────────────────────────
        _JS_PAGE_TILE = """() => {
            function deepFind(root, fn, depth) {
                if (depth === 0) return null;
                const found = fn(root);
                if (found) return found;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot, fn, depth - 1);
                        if (f) return f;
                    }
                }
                return null;
            }
            const tile = deepFind(document, r => {
                for (const d of r.querySelectorAll('div.add-material-tile-inner')) {
                    const t = d.querySelector('.material-tile-text');
                    if (t && t.textContent.trim() === 'Page') return d;
                    if (d.querySelector('svg#htmldoc')) return d;
                }
                return null;
            }, 10);
            if (!tile) return false;
            tile.click();
            return true;
        }"""

        page_tile_found = False
        for ctx in [bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]:
            try:
                if await ctx.evaluate(_JS_PAGE_TILE):
                    page_tile_found = True
                    break
            except Exception:
                pass

        if not page_tile_found:
            log_fn("  ✗ 'Page' tile not found", "error")
            return False
        await bs_page.wait_for_timeout(3000)

        # ── Fill title ─────────────────────────────────────────────────────────
        for title_sel in (
            'd2l-input-text input',
            'input[aria-label*="itle"]',
            'input[name="title"]',
            'input[id*="title"]',
        ):
            _, loc = await _find_locator_any_frame(bs_page, title_sel, retries=3, delay_ms=500)
            if loc:
                await loc.fill(title)
                log_fn(f"  ✓ Title: {title}", "dim")
                break
        else:
            log_fn("  ⚠ Title field not found — set manually", "warning")

        # ── Open Source Code editor ────────────────────────────────────────────
        source_opened = False
        for _ in range(5):
            if await js_click('d2l-htmleditor-button[cmd="d2l-source-code"]'):
                source_opened = True
                break
            await bs_page.wait_for_timeout(700)

        if not source_opened:
            await js_click('d2l-htmleditor-button-toggle.d2l-htmleditor-toolbar-chomper')
            await bs_page.wait_for_timeout(700)
            for sel in (
                'd2l-htmleditor-button[cmd="d2l-source-code"]',
                'd2l-htmleditor-menu-item[cmd="d2l-source-code"]',
            ):
                for _ in range(4):
                    if await js_click(sel):
                        source_opened = True
                        break
                    await bs_page.wait_for_timeout(500)
                if source_opened:
                    break

        if not source_opened:
            log_fn("  ✗ Source Code button not found", "error")
            return False

        await bs_page.wait_for_timeout(800)

        # ── Paste HTML ─────────────────────────────────────────────────────────
        await bs_page.evaluate("(h) => navigator.clipboard.writeText(h)", html)
        await bs_page.wait_for_timeout(300)

        await bs_page.evaluate("""() => {
            function deepFind(root) {
                const el = root.querySelector('[contenteditable="true"].cm-content');
                if (el) return el;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot);
                        if (f) return f;
                    }
                }
                return null;
            }
            const el = deepFind(document);
            if (el) { el.focus(); el.click(); }
        }""")
        await bs_page.wait_for_timeout(400)
        await bs_page.keyboard.press("Control+a")
        await bs_page.wait_for_timeout(200)
        await bs_page.keyboard.press("Control+v")
        await bs_page.wait_for_timeout(600)

        # ── Close Source Code dialog ───────────────────────────────────────────
        for selector in (
            '[data-dialog-action="save"]',
            'd2l-button:has-text("OK")',
            'button:has-text("OK")',
            'd2l-button:has-text("Update")',
            'button:has-text("Update")',
        ):
            _, btn = await _find_locator_any_frame(bs_page, selector, retries=3, delay_ms=400)
            if btn:
                await btn.first.click()
                break
        await bs_page.wait_for_timeout(1200)

        # ── Save and Close ─────────────────────────────────────────────────────
        for selector in (
            'd2l-button:has-text("Save and Close")',
            'button:has-text("Save and Close")',
            'd2l-button:has-text("Save")',
            'button:has-text("Save")',
        ):
            _, btn = await _find_locator_any_frame(bs_page, selector, retries=6, delay_ms=600)
            if btn:
                await btn.first.click()
                log_fn(f"  ✓ Saved: {title}", "success")
                return True

        log_fn("  ⚠ Save button not found", "warning")
        return False

    async def embed_entries(
        self,
        entries: list[dict],
        section_map: dict[str, str],
        bs_url: str,
        log_fn,
    ) -> None:
        """For each entry: get KMC embed code → create Brightspace page."""
        from content_checker import _extract_course_id
        course_id = _extract_course_id(bs_url)
        if not course_id:
            raise ValueError(f"Could not extract course ID from URL: {bs_url}")
        base_url = "/".join(bs_url.split("/")[:3])

        async with async_playwright() as p:
            kmc_context, kmc_browser = await self._get_kmc_context(p)
            bs_browser = await p.chromium.launch(headless=False)
            try:
                storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
                bs_context = await bs_browser.new_context(
                    storage_state=storage,
                    permissions=["clipboard-read", "clipboard-write"],
                )
                kmc_page = await kmc_context.new_page()
                bs_page = await bs_context.new_page()

                for entry in entries:
                    entry_id = entry["entry_id"]
                    name = entry["name"]
                    section_name = entry.get("section_name", "")
                    module_id = section_map.get(section_name)

                    if not module_id:
                        log_fn(
                            f"⚠ No module mapped for section '{section_name}', skipping {name}",
                            "warning",
                        )
                        continue

                    try:
                        embed_code = await self._get_embed_code(kmc_page, entry_id, log_fn)
                        if not embed_code:
                            continue
                        ok = await self._create_bs_page(
                            bs_page, base_url, course_id, module_id, name, embed_code, log_fn
                        )
                        if not ok:
                            log_fn(f"✗ Failed to create page: {name}", "error")
                    except Exception as e:
                        log_fn(f"✗ {name}: {e}", "error")

            finally:
                try:
                    await kmc_context.storage_state(path=KMC_SESSION_FILE)
                except Exception:
                    pass
                await kmc_browser.close()
                await bs_browser.close()

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
