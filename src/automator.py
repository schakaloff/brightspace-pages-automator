"""
Page automator — class-based with AI styling pipeline:
  1. Navigate to the topic URL
  2. Find the smart-curriculum iframe → click Options → click Edit
  3. Wait for the edit page to load
  4. Find d2l-htmleditor-button[cmd="d2l-source-code"] → click it
  5. Extract HTML from the source-code dialog
  6. Call Gemini AI to restyle the HTML
  7. Write styled HTML back and click Update
  8. Signal on_complete() so the GUI re-enables
  9. Keep browser open until the user closes it
"""
import asyncio
from typing import Callable, Optional

from playwright.async_api import Page


async def _find_locator_any_frame(page: Page, selector: str, retries: int = 6, delay_ms: int = 700):
    """
    Search for a CSS selector across the main page and all iframes.
    Returns (frame_or_page, locator) or (None, None).
    """
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
        selected_color: str = "#2D8CFF",
    ):
        self.url = url
        self.log = log
        self.on_complete = on_complete
        self.gemini_api_key = gemini_api_key
        self.style_reference_html = style_reference_html
        self.selected_color = selected_color

    async def extract_html_from_editor(self, page: Page) -> Optional[str]:
        """Read HTML from the open source-code dialog textarea (shadow DOM aware)."""
        self.log("Extracting HTML from source editor...", "info")
        await page.wait_for_timeout(1500)

        # Traverse shadow DOM to find the source-code textarea
        _JS_EXTRACT = """() => {
            function deepFind(root, selector) {
                const el = root.querySelector(selector);
                if (el) return el;
                for (const child of root.querySelectorAll('*')) {
                    if (child.shadowRoot) {
                        const found = deepFind(child.shadowRoot, selector);
                        if (found) return found;
                    }
                }
                return null;
            }
            for (const sel of [
                'd2l-htmleditor-source-editor textarea',
                '.d2l-htmleditor-source-code textarea',
                'textarea[class*="source"]',
            ]) {
                const el = deepFind(document, sel);
                if (el) return el.value;
            }
            // Fallback: first textarea containing HTML tags
            for (const t of document.querySelectorAll('textarea')) {
                if (t.value && t.value.includes('<')) return t.value;
            }
            return null;
        }"""

        result = await page.evaluate(_JS_EXTRACT)

        # Also check inside iframes
        if result is None:
            for frame in page.frames:
                try:
                    r = await frame.evaluate(_JS_EXTRACT)
                    if r and '<' in r:
                        result = r
                        break
                except Exception:
                    pass

        if result:
            self.log(f"✓ Extracted {len(result):,} chars of HTML", "success")
        else:
            self.log("⚠ Could not locate source editor textarea", "warning")
        return result

    async def replace_html_in_editor(self, page: Page, html: str) -> bool:
        """Set textarea content to new HTML, fire change events, and click Update."""
        self.log("Writing styled HTML back to editor...", "info")

        _JS_SET = """(newHtml) => {
            function deepFind(root, selector) {
                const el = root.querySelector(selector);
                if (el) return el;
                for (const child of root.querySelectorAll('*')) {
                    if (child.shadowRoot) {
                        const found = deepFind(child.shadowRoot, selector);
                        if (found) return found;
                    }
                }
                return null;
            }
            for (const sel of [
                'd2l-htmleditor-source-editor textarea',
                '.d2l-htmleditor-source-code textarea',
                'textarea[class*="source"]',
            ]) {
                const el = deepFind(document, sel);
                if (el) {
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    setter.call(el, newHtml);
                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }"""

        set_ok = await page.evaluate(_JS_SET, html)
        if not set_ok:
            self.log("⚠ Could not write to source editor textarea", "warning")
            return False

        await page.wait_for_timeout(500)

        # Click the Update / Save button in the dialog (light DOM first, then shadow DOM)
        for selector in [
            'd2l-button:has-text("Update")',
            'button:has-text("Update")',
            'd2l-button:has-text("Save")',
            'button:has-text("Save")',
        ]:
            _, btn = await _find_locator_any_frame(page, selector, retries=3, delay_ms=400)
            if btn:
                await btn.first.click()
                self.log("✓ HTML updated in editor", "success")
                return True

        # Shadow DOM fallback
        clicked = await page.evaluate("""() => {
            function deepFindText(root, text) {
                for (const el of root.querySelectorAll('button, d2l-button')) {
                    if (el.textContent && el.textContent.trim().toLowerCase().includes(text)) {
                        el.click();
                        return true;
                    }
                }
                for (const child of root.querySelectorAll('*')) {
                    if (child.shadowRoot && deepFindText(child.shadowRoot, text))
                        return true;
                }
                return false;
            }
            return deepFindText(document, 'update') || deepFindText(document, 'save');
        }""")

        if clicked:
            self.log("✓ HTML updated in editor (shadow DOM fallback)", "success")
            return True

        self.log("⚠ Update button not found — HTML written but dialog not closed", "warning")
        return False

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login
        from ai_styler import apply_style

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context)

            # ── Navigate ──────────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Navigating to:", "info")
            self.log(f"  {self.url}", "step")
            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.log("✓ Page loaded", "success")

            # ── Step 1: Click Options ─────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Looking for Options button...", "info")
            try:
                await page.wait_for_selector('iframe', timeout=5000)
            except Exception:
                pass

            _, btn = await _find_locator_any_frame(
                page, 'd2l-button-icon.content-options-btn', retries=7
            )
            if btn is None:
                self.log("✗ Options button not found", "error")
                self.log("  Frames on page:", "dim")
                for f in page.frames:
                    self.log(f"    {f.url[:90]}", "dim")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            await btn.first.scroll_into_view_if_needed()
            await btn.first.click()
            self.log("✓ Options menu opened", "success")

            # ── Step 2: Click Edit ────────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Waiting for Edit menu item...", "info")

            _, edit_btn = await _find_locator_any_frame(
                page, 'd2l-menu-item#optEdit', retries=8, delay_ms=500
            )
            if edit_btn is None:
                self.log("✗ Edit menu item not found", "error")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            await edit_btn.first.wait_for(state="visible", timeout=4000)
            await edit_btn.first.click()
            self.log("✓ Edit clicked — waiting for edit page...", "success")

            # ── Step 3: Wait for edit page ────────────────────────────────────
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(800)

            # ── Step 4: Click Source Code ─────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Looking for Source Code button...", "info")

            _, src_btn = await _find_locator_any_frame(
                page, 'd2l-htmleditor-button[cmd="d2l-source-code"]', retries=8, delay_ms=700
            )
            if src_btn is None:
                self.log("✗ Source Code button not found", "error")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            await src_btn.first.scroll_into_view_if_needed()
            await src_btn.first.click()
            self.log("✓ Source Code dialog opened", "success")

            # ── Step 5: AI styling pipeline ───────────────────────────────────
            if self.gemini_api_key and self.style_reference_html:
                source_html = await self.extract_html_from_editor(page)
                if source_html:
                    styled_html = apply_style(
                        source_html=source_html,
                        style_reference_html=self.style_reference_html,
                        primary_color=self.selected_color,
                        api_key=self.gemini_api_key,
                        log_callback=self.log,
                    )
                    if styled_html:
                        await self.replace_html_in_editor(page, styled_html)
                    else:
                        self.log("⚠ AI styling failed — original HTML preserved", "warning")
                else:
                    self.log("⚠ Could not extract HTML — skipping AI styling", "warning")
            else:
                self.log("ℹ No AI config — source code dialog left open", "dim")

            # ── Done ──────────────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("✓  Done — enter a new URL and click Start to run again.", "success")
            self.log("  Close the Chromium window when you are finished.", "dim")
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
    selected_color: str = "#2D8CFF",
) -> None:
    await PageAutomator(
        url=url,
        log=log,
        on_complete=on_complete,
        gemini_api_key=gemini_api_key,
        style_reference_html=style_reference_html,
        selected_color=selected_color,
    ).run()