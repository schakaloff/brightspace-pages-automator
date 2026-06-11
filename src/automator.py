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
        theme_name: str = "blue",
    ):
        self.url = url
        self.log = log
        self.on_complete = on_complete
        self.gemini_api_key = gemini_api_key
        self.style_reference_html = style_reference_html
        self.theme_name = theme_name

    async def _focus_codemirror(self, page: Page) -> bool:
        """Find and focus the CodeMirror editor inside the source-code dialog."""
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
        """Copy HTML from the CodeMirror source editor using Ctrl+A / Ctrl+C."""
        self.log("Extracting HTML from source editor (Ctrl+A, Ctrl+C)...", "info")
        await page.wait_for_timeout(1500)

        await page.evaluate("navigator.clipboard.writeText('')")

        ok = await self._focus_codemirror(page)
        if not ok:
            self.log("⚠ Could not focus CodeMirror editor", "warning")
            return None

        await page.wait_for_timeout(400)
        await page.keyboard.press("Control+a")
        await page.wait_for_timeout(200)
        await page.keyboard.press("Control+c")
        await page.wait_for_timeout(500)

        result = await page.evaluate("navigator.clipboard.readText()")

        if result and "<" in result:
            self.log(f"✓ Extracted {len(result):,} chars of HTML", "success")
            return result

        self.log("⚠ Clipboard empty after copy — editor may not have focused", "warning")
        return None

    async def replace_html_in_editor(self, page: Page, html: str) -> bool:
        """Select all in CodeMirror and paste AI-generated HTML, then save."""
        self.log("Pasting styled HTML into editor (Ctrl+A, Ctrl+V)...", "info")

        await page.evaluate("(h) => navigator.clipboard.writeText(h)", html)
        await page.wait_for_timeout(300)

        await self._focus_codemirror(page)
        await page.wait_for_timeout(400)
        await page.keyboard.press("Control+a")
        await page.wait_for_timeout(200)
        await page.keyboard.press("Control+v")
        await page.wait_for_timeout(600)

        self.log("✓ HTML pasted into editor", "success")
        await page.wait_for_timeout(500)

        # Click the Save button inside the source-code dialog to apply & close it
        for selector in [
            '[data-dialog-action="save"]',
            'd2l-button:has-text("OK")',
            'button:has-text("OK")',
            'd2l-button:has-text("Update")',
            'button:has-text("Update")',
        ]:
            _, btn = await _find_locator_any_frame(page, selector, retries=3, delay_ms=400)
            if btn:
                await btn.first.click()
                self.log("✓ Source code dialog saved", "success")
                break
        else:
            # Shadow DOM fallback
            await page.evaluate("""() => {
                function deepFindText(root, text) {
                    for (const el of root.querySelectorAll('button, d2l-button')) {
                        if (el.textContent && el.textContent.trim().toLowerCase().includes(text)) {
                            el.click(); return true;
                        }
                    }
                    for (const child of root.querySelectorAll('*')) {
                        if (child.shadowRoot && deepFindText(child.shadowRoot, text)) return true;
                    }
                    return false;
                }
                return deepFindText(document, 'ok') || deepFindText(document, 'update');
            }""")

        await page.wait_for_timeout(1200)

        # Click Save and Close on the editor page to commit to Brightspace
        self.log("Looking for Save and Close...", "info")
        for selector in [
            'd2l-button:has-text("Save and Close")',
            'button:has-text("Save and Close")',
            'd2l-button:has-text("Save")',
            'button:has-text("Save")',
        ]:
            _, btn = await _find_locator_any_frame(page, selector, retries=6, delay_ms=600)
            if btn:
                await btn.first.click()
                self.log("✓ Page saved and closed", "success")
                return True

        self.log("⚠ Save and Close not found — please save manually", "warning")
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
            if not self.gemini_api_key:
                self.log("⚠ No Gemini API key set — add it to .env", "warning")
            else:
                self.log(f"✓ API key loaded ({len(self.gemini_api_key)} chars)", "success")
                source_html = await self.extract_html_from_editor(page)
                if source_html:
                    self.log("─" * 52, "dim")
                    styled_html = apply_style(
                        source_html=source_html,
                        style_reference_html=self.style_reference_html,
                        theme_name=self.theme_name,
                        api_key=self.gemini_api_key,
                        log_callback=self.log,
                    )
                    self.log("─" * 52, "dim")
                    if styled_html:
                        await self.replace_html_in_editor(page, styled_html)
                    else:
                        self.log("✗ AI returned nothing — original HTML preserved", "error")
                else:
                    self.log("✗ Could not extract HTML — skipping AI styling", "error")

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
    theme_name: str = "blue",
) -> None:
    await PageAutomator(
        url=url,
        log=log,
        on_complete=on_complete,
        gemini_api_key=gemini_api_key,
        style_reference_html=style_reference_html,
        theme_name=theme_name,
    ).run()