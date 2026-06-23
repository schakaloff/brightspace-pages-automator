import asyncio
from typing import Callable, List, Optional

from playwright.async_api import Page


async def _find_locator_any_frame(page: Page, selector: str, retries: int = 6, delay_ms: int = 700):
    for _ in range(max(retries, 1)):
        for ctx in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                loc = ctx.locator(selector)
                if await loc.count() > 0:
                    return ctx, loc
            except Exception:
                pass
        if _ < retries - 1:
            await page.wait_for_timeout(delay_ms)
    return None, None


class PageAutomator:
    def __init__(
        self,
        url: str,
        log: Callable[[str, str], None],
        on_complete: Callable = None,
        gemini_api_key: str = "",
        style_reference_html: str = "",
        theme_name: str = "blue",
        on_pages_found: Callable = None,
        bs_username: str = "",
        bs_password: str = "",
    ):
        self.url = url
        self.log = log
        self.on_complete = on_complete
        self.gemini_api_key = gemini_api_key
        self.style_reference_html = style_reference_html
        self.theme_name = theme_name
        self.on_pages_found = on_pages_found  # fn(pages) -> (start_idx, count)
        self.bs_username = bs_username
        self.bs_password = bs_password
        self._clipboard_lock = asyncio.Lock()  # one tab touches clipboard at a time

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _focus_codemirror(self, page: Page) -> bool:
        focused = await page.evaluate("""() => {
            function deepFind(root) {
                const el = root.querySelector('[contenteditable="true"].cm-content');
                if (el) return el;
                for (const child of root.querySelectorAll('*')) {
                    if (child.shadowRoot) {
                        const found = deepFind(child.shadowRoot);
                        if (found) return found;
                    }
                }
                return null;
            }
            const el = deepFind(document);
            if (el) { el.focus(); el.click(); return true; }
            return false;
        }""")
        return bool(focused)

    async def extract_html_from_editor(self, page: Page) -> Optional[str]:
        self.log("Extracting HTML (Ctrl+A, Ctrl+C)...", "info")

        result = None
        for attempt in range(6):
            await page.wait_for_timeout(1000)
            async with self._clipboard_lock:
                await page.evaluate("navigator.clipboard.writeText('')")
                if not await self._focus_codemirror(page):
                    continue
                await page.wait_for_timeout(300)
                await page.keyboard.press("Control+a")
                await page.wait_for_timeout(200)
                await page.keyboard.press("Control+c")
                await page.wait_for_timeout(400)
                result = await page.evaluate("navigator.clipboard.readText()")
            if result and "<" in result:
                break

        if result and "<" in result:
            self.log(f"✓ Extracted {len(result):,} chars", "success")
            return result

        self.log("⚠ Clipboard empty after copy", "warning")
        return None

    async def replace_html_in_editor(self, page: Page, html: str) -> bool:
        self.log("Pasting styled HTML (Ctrl+A, Ctrl+V)...", "info")

        async with self._clipboard_lock:
            await page.evaluate("(h) => navigator.clipboard.writeText(h)", html)
            await page.wait_for_timeout(300)

            await self._focus_codemirror(page)
            await page.wait_for_timeout(400)
            await page.keyboard.press("Control+a")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Control+v")
            await page.wait_for_timeout(600)

        self.log("✓ HTML pasted", "success")
        await page.wait_for_timeout(500)

        # Close source-code dialog
        for selector in ['[data-dialog-action="save"]', 'd2l-button:has-text("OK")', 'button:has-text("OK")', 'd2l-button:has-text("Update")', 'button:has-text("Update")']:
            _, btn = await _find_locator_any_frame(page, selector, retries=3, delay_ms=400)
            if btn:
                await btn.first.click()
                self.log("✓ Source dialog closed", "success")
                break

        await page.wait_for_timeout(1200)

        # Save and Close the editor page
        self.log("Saving page...", "info")
        for selector in ['d2l-button:has-text("Save and Close")', 'button:has-text("Save and Close")', 'd2l-button:has-text("Save")', 'button:has-text("Save")']:
            _, btn = await _find_locator_any_frame(page, selector, retries=6, delay_ms=600)
            if btn:
                await btn.first.click()
                self.log("✓ Page saved", "success")
                return True

        self.log("⚠ Save button not found — save manually", "warning")
        return False

    async def scrape_section_pages(self, page: Page) -> List[dict]:
        """Scrape all topic links from the section sidebar."""
        self.log("Scanning section for topic pages...", "info")

        # Wait for smart-curriculum SPA iframe to appear
        try:
            await page.wait_for_selector('iframe', timeout=8000)
        except Exception:
            pass

        base_url = "/".join(self.url.split("/")[:3])
        lesson_id = self.url.rstrip("/").split("/")[-1]

        # Non-page content types to exclude (matched against icon name or type/sub-title attrs)
        SKIP_TYPES = ['quiz', 'dropbox', 'link', 'video', 'youtube',
                      'discussion', 'survey', 'assignment', 'checklist', 'lti']

        _JS = """([baseUrl, lessonId, skipTypes]) => {
            function iconHint(el) {
                // icon attribute on d2l-icon children
                for (const ic of el.querySelectorAll('d2l-icon, d2l-icon-custom')) {
                    const n = ic.getAttribute('icon') || ic.getAttribute('name') || '';
                    if (n) return n.toLowerCase();
                }
                if (el.shadowRoot) {
                    for (const ic of el.shadowRoot.querySelectorAll('d2l-icon, d2l-icon-custom')) {
                        const n = ic.getAttribute('icon') || ic.getAttribute('name') || '';
                        if (n) return n.toLowerCase();
                    }
                }
                // direct type hint attributes
                return (el.getAttribute('sub-title-text') || '').toLowerCase();
            }

            function isHtmlPage(el) {
                const hint = iconHint(el);
                if (!hint) return true;
                return !skipTypes.some(t => hint.includes(t));
            }

            function topicsIn(root) {
                return Array.from(root.querySelectorAll('d2l-list-item-nav'))
                    .filter(el => (el.getAttribute('action-href') || '').includes('/topics/'))
                    .filter(el => isHtmlPage(el))
                    .map(el => ({
                        label: el.getAttribute('label') || el.getAttribute('drag-handle-text') || 'Untitled',
                        url: baseUrl + el.getAttribute('action-href'),
                        hint: iconHint(el),
                    }));
            }

            // Find the unit/lesson container matching our ID
            function findUnitEl(root) {
                for (const el of root.querySelectorAll('d2l-list-item-nav')) {
                    const href = el.getAttribute('action-href') || '';
                    const key  = el.getAttribute('key') || '';
                    if (key === lessonId || href.includes('/' + lessonId)) return el;
                }
                for (const child of root.querySelectorAll('*')) {
                    if (child.shadowRoot) {
                        const found = findUnitEl(child.shadowRoot);
                        if (found) return found;
                    }
                }
                return null;
            }

            const unitEl = findUnitEl(document);
            if (unitEl) {
                const topics = topicsIn(unitEl);
                if (topics.length > 0) return topics;
            }
            return topicsIn(document);
        }"""

        # Poll for topics — smart-curriculum SPA can take 5-15s to populate
        pages = []
        for attempt in range(10):
            await page.wait_for_timeout(2000)

            # Try main frame first
            try:
                pages = await page.evaluate(_JS, [base_url, lesson_id, SKIP_TYPES])
            except Exception:
                pass

            # Then every child frame (smart-curriculum loads in an iframe)
            if not pages:
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        pages = await frame.evaluate(_JS, [base_url, lesson_id, SKIP_TYPES])
                        if pages:
                            break
                    except Exception:
                        pass

            if pages:
                break
            self.log(f"  Waiting for SPA ({attempt + 1}/10)...", "dim")

        seen = set()
        unique = []
        for p in (pages or []):
            if p["url"] not in seen:
                seen.add(p["url"])
                # log the type hint so user can see what was detected
                hint = p.get("hint", "")
                suffix = f"  [{hint}]" if hint else ""
                self.log(f"  + {p['label']}{suffix}", "dim")
                unique.append({"label": p["label"], "url": p["url"]})

        if unique:
            self.log(f"✓ Found {len(unique)} HTML page(s)", "success")
        else:
            self.log("⚠ No HTML pages found — check: are you logged in? Is the unit expanded in the sidebar?", "warning")
        return unique

    async def _process_topic(self, page: Page, url: str, label: str = "") -> bool:
        """Navigate to a topic and run the full options → edit → AI → save pipeline."""
        self.log("─" * 52, "dim")
        if label:
            self.log(f"Processing: {label}", "step")
        self.log(f"  {url}", "dim")

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        _, btn = await _find_locator_any_frame(page, 'd2l-button-icon.content-options-btn', retries=15)
        if btn is None:
            self.log("✗ Options button not found — skipping", "error")
            return False
        await btn.first.scroll_into_view_if_needed()
        await btn.first.click()

        _, edit_btn = await _find_locator_any_frame(page, 'd2l-menu-item#optEdit', retries=8, delay_ms=500)
        if edit_btn is None:
            self.log("✗ Edit menu not found — skipping", "error")
            return False
        await edit_btn.first.wait_for(state="visible", timeout=4000)
        await edit_btn.first.click()

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(800)

        # All editor buttons are inside the shadow DOM of d2l-htmleditor.
        # Playwright locator() won't reach them, so we use JS with deep
        # shadow traversal. d2l web components also need their *inner*
        # <button> clicked, not the outer custom element.
        _JS_DEEP_CLICK = """(selector) => {
            function deepFind(root, sel) {
                const el = root.querySelector(sel);
                if (el) return el;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot, sel);
                        if (f) return f;
                    }
                }
                return null;
            }
            const el = deepFind(document, selector);
            if (!el) return false;
            // d2l web components render a real <button> inside their own shadow root
            if (el.shadowRoot) {
                const inner = el.shadowRoot.querySelector('button');
                if (inner) { inner.click(); return true; }
            }
            el.click();
            return true;
        }"""

        async def js_click(selector: str) -> bool:
            # Rebuild frame list each call — editor frame may load after page nav
            ctxs = [page, *[f for f in page.frames if f != page.main_frame]]
            for ctx in ctxs:
                try:
                    if await ctx.evaluate(_JS_DEEP_CLICK, selector):
                        return True
                except Exception:
                    pass
            return False

        # First: try Source Code button directly (visible when toolbar is wide enough)
        opened = False
        for _ in range(5):
            if await js_click('d2l-htmleditor-button[cmd="d2l-source-code"]'):
                opened = True
                break
            await page.wait_for_timeout(700)

        # Toolbar in "chomping" mode hides Source Code — click More Actions first
        if not opened:
            self.log("  Source Code chomped — clicking More Actions...", "dim")
            await js_click('d2l-htmleditor-button-toggle.d2l-htmleditor-toolbar-chomper')
            await page.wait_for_timeout(700)
            # Source Code may now appear as a direct button or inside a menu item
            for sel in (
                'd2l-htmleditor-button[cmd="d2l-source-code"]',
                'd2l-htmleditor-menu-item[cmd="d2l-source-code"]',
            ):
                for _ in range(4):
                    if await js_click(sel):
                        opened = True
                        break
                    await page.wait_for_timeout(500)
                if opened:
                    break

        if not opened:
            self.log("✗ Source Code button not found — skipping", "error")
            return False
        self.log("✓ Source Code dialog opened", "success")

        source_html = await self.extract_html_from_editor(page)
        if not source_html:
            self.log("✗ Could not extract HTML — skipping", "error")
            return False

        from ai_styler import apply_style
        styled_html = await asyncio.to_thread(
            apply_style,
            source_html=source_html,
            style_reference_html=self.style_reference_html,
            theme_name=self.theme_name,
            api_key=self.gemini_api_key,
            log_callback=self.log,
        )

        if not styled_html:
            self.log("✗ AI returned nothing — skipping", "error")
            return False

        await self.replace_html_in_editor(page, styled_html)
        await page.wait_for_timeout(1500)
        return True

    # ── Main run ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context, self.bs_username or None, self.bs_password or None)

            self.log("─" * 52, "dim")
            self.log(f"Navigating to: {self.url}", "info")
            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.log("✓ Page loaded", "success")

            if "/topics/" not in self.url:
                # Section URL: scrape all topic pages and let user pick
                pages = await self.scrape_section_pages(page)
                if not pages:
                    self.log("✗ No topic pages found in this section", "error")
                    if self.on_complete:
                        self.on_complete()
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                    return

                start_idx, count = 0, len(pages)
                if self.on_pages_found:
                    start_idx, count = await asyncio.to_thread(self.on_pages_found, pages)

                selected = pages[start_idx: start_idx + count]
                self.log(f"Processing {len(selected)} page(s) — up to 5 at a time", "info")

                sem = asyncio.Semaphore(5)

                async def process_one(topic: dict, idx: int) -> None:
                    async with sem:
                        tab = await context.new_page()
                        self.log(f"[{idx + 1}/{len(selected)}] {topic['label']}", "step")
                        await self._process_topic(tab, topic["url"], topic["label"])

                await asyncio.gather(*[process_one(t, i) for i, t in enumerate(selected)])
            else:
                # Single topic URL
                await self._process_topic(page, self.url)

            self.log("─" * 52, "dim")
            self.log("✓  All done! Close the browser when finished.", "success")
            if self.on_complete:
                self.on_complete()

            while browser.is_connected():
                await asyncio.sleep(0.5)
            self.log("Browser closed.", "dim")

        except Exception:
            if self.on_complete:
                self.on_complete()
            raise
        finally:
            if browser.is_connected():
                await browser.close()
            await p.stop()


async def run(
    url: str,
    log: Callable[[str, str], None],
    on_complete: Callable = None,
    gemini_api_key: str = "",
    style_reference_html: str = "",
    theme_name: str = "blue",
    on_pages_found: Callable = None,
    bs_username: str = "",
    bs_password: str = "",
) -> None:
    await PageAutomator(
        url=url,
        log=log,
        on_complete=on_complete,
        gemini_api_key=gemini_api_key,
        style_reference_html=style_reference_html,
        theme_name=theme_name,
        on_pages_found=on_pages_found,
        bs_username=bs_username,
        bs_password=bs_password,
    ).run()
