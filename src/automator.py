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
    ):
        self.url = url
        self.log = log
        self.on_complete = on_complete
        self.gemini_api_key = gemini_api_key
        self.style_reference_html = style_reference_html
        self.theme_name = theme_name
        self.on_pages_found = on_pages_found  # fn(pages) -> (start_idx, count)

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
        await page.wait_for_timeout(1500)
        await page.evaluate("navigator.clipboard.writeText('')")

        if not await self._focus_codemirror(page):
            self.log("⚠ Could not focus CodeMirror editor", "warning")
            return None

        await page.wait_for_timeout(400)
        await page.keyboard.press("Control+a")
        await page.wait_for_timeout(200)
        await page.keyboard.press("Control+c")
        await page.wait_for_timeout(500)

        result = await page.evaluate("navigator.clipboard.readText()")
        if result and "<" in result:
            self.log(f"✓ Extracted {len(result):,} chars", "success")
            return result

        self.log("⚠ Clipboard empty after copy", "warning")
        return None

    async def replace_html_in_editor(self, page: Page, html: str) -> bool:
        self.log("Pasting styled HTML (Ctrl+A, Ctrl+V)...", "info")
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

        # Wait for iframe and sidebar to render
        try:
            await page.wait_for_selector('iframe', timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        base_url = "/".join(self.url.split("/")[:3])  # https://domain.com
        # Extract the lesson ID from the URL (last path segment)
        lesson_id = self.url.rstrip("/").split("/")[-1]

        _JS = """([baseUrl, lessonId]) => {
            function topicsIn(root) {
                return Array.from(root.querySelectorAll('d2l-list-item-nav'))
                    .filter(el => (el.getAttribute('action-href') || '').includes('/topics/'))
                    .map(el => ({
                        label: el.getAttribute('label') || el.getAttribute('drag-handle-text') || 'Untitled',
                        url: baseUrl + el.getAttribute('action-href')
                    }));
            }

            // Find the lesson container that matches our lesson ID
            function findLessonEl(root) {
                for (const el of root.querySelectorAll('d2l-list-item-nav')) {
                    const href = el.getAttribute('action-href') || '';
                    const key  = el.getAttribute('key') || '';
                    if (key === lessonId || href.endsWith('/lessons/' + lessonId)) {
                        return el;
                    }
                }
                // Also check shadow roots
                for (const child of root.querySelectorAll('*')) {
                    if (child.shadowRoot) {
                        const found = findLessonEl(child.shadowRoot);
                        if (found) return found;
                    }
                }
                return null;
            }

            const lessonEl = findLessonEl(document);
            if (lessonEl) {
                // Topics are slotted children — they live in the light DOM under the lesson element
                const topics = topicsIn(lessonEl);
                if (topics.length > 0) return topics;
            }

            // Fallback: return all topics (old behaviour) if lesson container not found
            return topicsIn(document);
        }"""

        pages = await page.evaluate(_JS, [base_url, lesson_id])

        # Try every iframe if main doc came up empty
        if not pages:
            all_frames = [f for f in page.frames if f != page.main_frame]
            self.log(f"  Checking {len(all_frames)} frame(s)...", "dim")
            for frame in all_frames:
                try:
                    self.log(f"  frame: {frame.url[:80]}", "dim")
                    fp = await frame.evaluate(_JS, [base_url, lesson_id])
                    if fp:
                        self.log(f"  ✓ Topics found in above frame", "dim")
                        pages = fp
                        break
                except Exception:
                    pass

        seen = set()
        unique = []
        for p in (pages or []):
            if p["url"] not in seen:
                seen.add(p["url"])
                unique.append(p)

        if unique:
            self.log(f"✓ Found {len(unique)} topic pages", "success")
        else:
            self.log("⚠ No topics found — make sure the lesson is expanded in the sidebar", "warning")
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
            await page.wait_for_selector('iframe', timeout=5000)
        except Exception:
            pass

        _, btn = await _find_locator_any_frame(page, 'd2l-button-icon.content-options-btn', retries=7)
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

        _, src_btn = await _find_locator_any_frame(page, 'd2l-htmleditor-button[cmd="d2l-source-code"]', retries=8, delay_ms=700)
        if src_btn is None:
            self.log("✗ Source Code button not found — skipping", "error")
            return False
        await src_btn.first.scroll_into_view_if_needed()
        await src_btn.first.click()
        self.log("✓ Source Code dialog opened", "success")

        source_html = await self.extract_html_from_editor(page)
        if not source_html:
            self.log("✗ Could not extract HTML — skipping", "error")
            return False

        from ai_styler import apply_style
        styled_html = apply_style(
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
            await wait_for_login(page, context)

            self.log("─" * 52, "dim")
            self.log(f"Navigating to: {self.url}", "info")
            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.log("✓ Page loaded", "success")

            if not self.gemini_api_key:
                self.log("⚠ No Gemini API key — add it to .env", "warning")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            # ── Section URL: scrape topics first ─────────────────────────────
            if "/topics/" not in self.url:
                pages = await self.scrape_section_pages(page)
                if not pages:
                    self.log("✗ No topic pages found in this section", "error")
                    if self.on_complete:
                        self.on_complete()
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                    return

                # Ask user which pages to process (blocks until GUI responds)
                start_idx, count = 0, len(pages)
                if self.on_pages_found:
                    start_idx, count = await asyncio.to_thread(self.on_pages_found, pages)

                selected = pages[start_idx: start_idx + count]
                self.log(f"Processing {len(selected)} page(s) starting from #{start_idx + 1}", "info")

                for i, topic in enumerate(selected):
                    self.log(f"Page {i + 1} of {len(selected)}", "step")
                    await self._process_topic(page, topic["url"], topic["label"])

            # ── Single topic URL ──────────────────────────────────────────────
            else:
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
) -> None:
    await PageAutomator(
        url=url,
        log=log,
        on_complete=on_complete,
        gemini_api_key=gemini_api_key,
        style_reference_html=style_reference_html,
        theme_name=theme_name,
        on_pages_found=on_pages_found,
    ).run()
