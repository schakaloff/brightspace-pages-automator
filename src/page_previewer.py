"""
Style Preview:
  1. Navigate to a Brightspace topic page (view mode)
  2. Open Options → Edit → Source Code → extract source HTML
  3. Navigate back to the view page
  4. Send source HTML to Claude AI for styling
  5. Inject styled HTML into the live Brightspace page DOM (preview in real browser)
  6. Wait for user: Apply / Regenerate (with feedback) / Skip
  7. Apply → go through Options → Edit → Source Code → write back → Save and Close
"""
import asyncio
import time
from typing import Callable, Optional

from playwright.async_api import Page, BrowserContext


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
    if (el.shadowRoot) {
        const inner = el.shadowRoot.querySelector('button, a');
        if (inner) { inner.click(); return true; }
    }
    el.click();
    return true;
}"""

# Inject styled HTML into the Brightspace topic view page.
# Tries several strategies because D2L component structure varies by version.
_JS_INJECT_CONTENT = """(styledHtml) => {
    // Strategy 1: d2l-html-block web component (D2L 20.x+)
    // Try the .html JS property first, then the attribute, then innerHTML
    const block = document.querySelector('d2l-html-block');
    if (block) {
        try { block.html = styledHtml; return 'd2l-html-block.html'; } catch(e) {}
        try { block.setAttribute('html', styledHtml); return 'd2l-html-block[html]'; } catch(e) {}
        block.innerHTML = styledHtml;
        return 'd2l-html-block innerHTML';
    }

    // Strategy 2: known D2L content container selectors
    for (const sel of [
        '.d2l-lessontype-content',
        '#d2l-lessontype-htmlfile',
        '.d2l-page-content',
        '.d2l-content',
        '.d2l-htmlblock-rendered',
        '[data-type="html"]',
    ]) {
        const el = document.querySelector(sel);
        if (el) { el.innerHTML = styledHtml; return sel; }
    }

    // Strategy 3: largest text-containing div inside [role="main"]
    const main = document.querySelector('[role="main"], main, #content');
    if (main) {
        const divs = Array.from(main.querySelectorAll('div'));
        const big = divs.find(d =>
            d.textContent.trim().length > 30 &&
            !d.querySelector('nav, header, [role="navigation"]')
        );
        if (big) { big.innerHTML = styledHtml; return 'main>div (heuristic)'; }
        main.innerHTML = styledHtml;
        return '[role="main"] innerHTML';
    }
    return null;
}"""


def _call_claude_feedback(
    styled_html: str,
    feedback: str,
    api_key: str,
    log: Callable,
    model: str = "claude-sonnet-5",
) -> Optional[str]:
    """Re-run AI on already-styled HTML applying user feedback."""
    import anthropic

    prompt = (
        "You are an expert front-end developer. The HTML below was already styled.\n"
        "Apply the user's feedback to adjust it. Keep the same theme, colors, and overall layout.\n"
        "Only make the specific changes the user requested.\n"
        "Return ONLY the complete adjusted HTML. No explanation, no markdown fences.\n\n"
        f"User feedback: {feedback}\n\n"
        "CURRENT HTML:\n"
        f"{styled_html}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    MAX_RETRIES = 3
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"🤖 Applying feedback — attempt {attempt}/{MAX_RETRIES}...", "info")
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            result = next(b.text for b in response.content if b.type == "text").strip()
            if result.startswith("```"):
                lines = result.splitlines()
                start = 1 if lines[0].startswith("```") else 0
                end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                result = "\n".join(lines[start:end]).strip()
            log(f"✅ Feedback applied ({len(result):,} chars)", "success")
            return result
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 529) and attempt < MAX_RETRIES:
                log("⚠ Server busy — retrying in 8s...", "warning")
                time.sleep(8)
            else:
                log("❌ AI unavailable after retries", "error")
                return None
        except Exception as e:
            log(f"❌ AI error: {e}", "error")
            return None
    return None


class PagePreviewer:
    def __init__(
        self,
        url: str,
        log: Callable[[str, str], None],
        on_complete: Callable = None,
        claude_api_key: str = "",
        claude_model: str = "",
        theme_name: str = "blue",
        on_user_action: Callable = None,
    ):
        self.url             = url
        self.log             = log
        self.on_complete     = on_complete
        self.claude_api_key  = claude_api_key
        self.claude_model    = claude_model
        self.theme_name      = theme_name
        # on_user_action() blocks the worker thread until user chooses.
        # Returns ("apply" | "regenerate" | "skip", feedback_text)
        self.on_user_action  = on_user_action
        self._clipboard_lock = asyncio.Lock()

    # ── Editor helpers (same pattern as PageAutomator) ────────────────────────

    async def _focus_codemirror(self, page: Page) -> bool:
        return bool(await page.evaluate("""() => {
            function deepFind(root) {
                const el = root.querySelector('[contenteditable="true"].cm-content');
                if (el) return el;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) { const f = deepFind(c.shadowRoot); if (f) return f; }
                }
                return null;
            }
            const el = deepFind(document);
            if (el) { el.focus(); el.click(); return true; }
            return false;
        }"""))

    async def _open_source_editor(self, page: Page) -> bool:
        """Click Options → Edit → Source Code. Returns True if dialog opened."""
        _, btn = await _find_locator_any_frame(page, 'd2l-button-icon.content-options-btn', retries=15)
        if btn is None:
            self.log("✗ Options button not found", "error")
            return False
        await btn.first.scroll_into_view_if_needed()
        await btn.first.click()

        _, edit_btn = await _find_locator_any_frame(page, 'd2l-menu-item#optEdit', retries=8, delay_ms=500)
        if edit_btn is None:
            self.log("✗ Edit menu not found", "error")
            return False
        await edit_btn.first.wait_for(state="visible", timeout=4000)
        await edit_btn.first.click()

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(800)

        async def js_click(sel: str) -> bool:
            for ctx in [page, *[f for f in page.frames if f != page.main_frame]]:
                try:
                    if await ctx.evaluate(_JS_DEEP_CLICK, sel):
                        return True
                except Exception:
                    pass
            return False

        opened = False
        for _ in range(5):
            if await js_click('d2l-htmleditor-button[cmd="d2l-source-code"]'):
                opened = True
                break
            await page.wait_for_timeout(700)

        if not opened:
            self.log("  Source Code chomped — clicking More Actions...", "dim")
            await js_click('d2l-htmleditor-button-toggle.d2l-htmleditor-toolbar-chomper')
            await page.wait_for_timeout(700)
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

        if opened:
            self.log("✓ Source Code dialog opened", "success")
        else:
            self.log("✗ Source Code button not found", "error")
        return opened

    async def _extract_source_html(self, page: Page) -> Optional[str]:
        """Extract HTML from the open CodeMirror source editor via clipboard."""
        self.log("Extracting HTML from editor...", "info")
        result = None
        for _ in range(6):
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
        self.log("⚠ Could not extract HTML from editor", "warning")
        return None

    async def _write_back_and_save(self, page: Page, styled_html: str) -> bool:
        """Navigate to topic, open source editor, paste styled HTML, and save."""
        self.log("Navigating to topic for apply...", "info")
        try:
            await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        if not await self._open_source_editor(page):
            return False

        self.log("Writing styled HTML back...", "info")
        async with self._clipboard_lock:
            await page.evaluate("(h) => navigator.clipboard.writeText(h)", styled_html)
            await page.wait_for_timeout(300)
            await self._focus_codemirror(page)
            await page.wait_for_timeout(400)
            await page.keyboard.press("Control+a")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Control+v")
            await page.wait_for_timeout(600)
        self.log("✓ HTML pasted", "success")
        await page.wait_for_timeout(500)

        for sel in ['[data-dialog-action="save"]', 'd2l-button:has-text("OK")', 'button:has-text("OK")',
                    'd2l-button:has-text("Update")', 'button:has-text("Update")']:
            _, btn = await _find_locator_any_frame(page, sel, retries=3, delay_ms=400)
            if btn:
                await btn.first.click()
                self.log("✓ Source dialog closed", "success")
                break

        await page.wait_for_timeout(1200)

        for sel in ['d2l-button:has-text("Save and Close")', 'button:has-text("Save and Close")',
                    'd2l-button:has-text("Save")', 'button:has-text("Save")']:
            _, btn = await _find_locator_any_frame(page, sel, retries=6, delay_ms=600)
            if btn:
                await btn.first.click()
                self.log("✅ Page saved!", "success")
                return True

        self.log("⚠ Save button not found — save manually", "warning")
        return False

    # ── Main flow ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login
        from ai_styler import apply_style, DEFAULT_MODEL
        model = self.claude_model or DEFAULT_MODEL

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context)

            # ── Step 1: load the topic view page ─────────────────────────────
            self.log("─" * 52, "dim")
            self.log(f"Navigating to: {self.url}", "info")
            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            self.log("✓ Page loaded", "success")

            # ── Step 2: extract source HTML via the editor ────────────────────
            self.log("─" * 52, "dim")
            self.log("Opening editor to extract source HTML...", "info")
            if not await self._open_source_editor(page):
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            source_html = await self._extract_source_html(page)
            if not source_html:
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            # ── Step 3: navigate back to view page for preview ────────────────
            self.log("─" * 52, "dim")
            self.log("Returning to view page for preview...", "info")
            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)

            # ── Step 4: AI style → inject → user review loop ──────────────────
            styled_html: Optional[str] = None
            feedback = ""
            first_run = True

            while True:
                self.log("─" * 52, "dim")

                if first_run:
                    # Initial styling via ai_styler (uses theme prompt file)
                    styled_html = await asyncio.to_thread(
                        apply_style,
                        source_html=source_html,
                        style_reference_html="",
                        theme_name=self.theme_name,
                        api_key=self.claude_api_key,
                        model=model,
                        log_callback=self.log,
                    )
                    first_run = False
                else:
                    # Regenerate: apply feedback on top of the previous styled result
                    styled_html = await asyncio.to_thread(
                        _call_claude_feedback,
                        styled_html,
                        feedback,
                        self.claude_api_key,
                        self.log,
                        model,
                    )

                if not styled_html:
                    self.log("✗ AI returned nothing — aborting", "error")
                    if self.on_complete:
                        self.on_complete()
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                    return

                # Inject preview into the live Brightspace page
                self.log("─" * 52, "dim")
                self.log("Injecting preview into page...", "info")
                inject_result = await page.evaluate(_JS_INJECT_CONTENT, styled_html)
                if inject_result:
                    self.log(f"✓ Preview live (via {inject_result})", "success")
                    self.log("👁  Check the browser — then choose Apply, Regenerate, or Skip.", "info")
                else:
                    self.log("⚠ Could not auto-inject preview", "warning")
                    self.log("  The styled HTML is ready — use Apply to save it anyway.", "info")

                # Signal GUI that preview is ready, wait for user choice
                if self.on_user_action:
                    action, feedback = await asyncio.to_thread(self.on_user_action)
                else:
                    action, feedback = "skip", ""

                if action == "apply":
                    self.log("─" * 52, "dim")
                    await self._write_back_and_save(page, styled_html)
                    break
                elif action == "regenerate":
                    self.log(f"Regenerating with feedback: {feedback!r}", "info")
                    # Navigate back to the clean view page so injection is fresh
                    try:
                        await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(1500)
                    continue
                else:
                    self.log("⏭ Skipped", "dim")
                    break

            self.log("─" * 52, "dim")
            self.log("✓ Done! Close the browser when finished.", "success")
            if self.on_complete:
                self.on_complete()

            while browser.is_connected():
                await asyncio.sleep(0.5)
            self.log("Browser closed.", "dim")

        except Exception as e:
            self.log(f"✗ Unexpected error: {e}", "error")
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
    claude_api_key: str = "",
    claude_model: str = "",
    theme_name: str = "blue",
    on_user_action: Callable = None,
) -> None:
    await PagePreviewer(
        url=url,
        log=log,
        on_complete=on_complete,
        claude_api_key=claude_api_key,
        claude_model=claude_model,
        theme_name=theme_name,
        on_user_action=on_user_action,
    ).run()
