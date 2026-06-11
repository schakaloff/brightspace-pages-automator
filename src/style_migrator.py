"""
Style Migrator automation:
  1. Navigate to Brightspace URL → Options → Edit → Source Code button
  2. Extract page HTML from source-code textarea (shadow DOM aware)
  3. Open Moodle URL in a new tab → scrape main content → close tab
  4. Send both to Gemini 2.0 Flash: restyle Brightspace HTML to match Moodle,
     convert [fa-xxx] codes to emoji, inline CSS only
  5. Replace textarea content → click OK → click Save and Close
"""
import asyncio
from typing import Callable, Optional

from playwright.async_api import BrowserContext, Page

_MIGRATOR_PROMPT = """You are an expert front-end developer. Your task is to restyle a Brightspace LMS HTML page.

SOURCE PAGE (Brightspace): The content that MUST be fully preserved.
REFERENCE PAGE (Moodle): The visual design and layout to imitate.

Rules:
- Keep ALL text, links, headings, lists, and semantic meaning from SOURCE PAGE.
- Apply the layout, card design, spacing, typography, and colour scheme of REFERENCE PAGE.
- Use ONLY inline CSS (no <link>, no <style> blocks, no external class references).
- Convert Font Awesome shortcodes like [fa-book], [fa-check-circle], [fa-arrow-right]
  to equivalent Unicode emoji (e.g. 📖, ✅, →).
- Primary accent colour to use throughout: {primary_color}
- Output must be valid, self-contained HTML that renders inside a Brightspace HTML editor.
- Return ONLY the complete restyled HTML. No explanation, no markdown fences, no preamble.

=== SOURCE HTML (Brightspace) ===
{source_html}

=== REFERENCE HTML (Moodle) ===
{moodle_html}
"""

# Font Awesome → emoji map (used as a hint to the model; model handles conversion)
_FA_HINTS = {
    "fa-book": "📖", "fa-graduation-cap": "🎓", "fa-check": "✅",
    "fa-check-circle": "✅", "fa-times": "❌", "fa-times-circle": "❌",
    "fa-arrow-right": "→", "fa-arrow-left": "←", "fa-star": "⭐",
    "fa-info-circle": "ℹ️", "fa-warning": "⚠️", "fa-exclamation-triangle": "⚠️",
    "fa-question-circle": "❓", "fa-pencil": "✏️", "fa-edit": "✏️",
    "fa-file": "📄", "fa-folder": "📁", "fa-link": "🔗",
    "fa-download": "⬇️", "fa-search": "🔍", "fa-user": "👤",
    "fa-users": "👥", "fa-calendar": "📅", "fa-clock": "🕐",
    "fa-bell": "🔔", "fa-comment": "💬", "fa-envelope": "✉️",
    "fa-home": "🏠", "fa-cog": "⚙️", "fa-wrench": "🔧",
    "fa-trophy": "🏆", "fa-lightbulb-o": "💡", "fa-lock": "🔒",
    "fa-globe": "🌐", "fa-video": "🎥", "fa-image": "🖼️",
    "fa-bar-chart": "📊", "fa-list": "📋", "fa-plus": "➕",
    "fa-thumbs-up": "👍", "fa-rocket": "🚀", "fa-flask": "🧪",
    "fa-code": "💻", "fa-play": "▶️", "fa-forward": "⏩",
}


async def _find_locator_any_frame(
    page: Page, selector: str, retries: int = 6, delay_ms: int = 700
):
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


_JS_EXTRACT_TEXTAREA = """() => {
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
    for (const t of document.querySelectorAll('textarea')) {
        if (t.value && t.value.includes('<')) return t.value;
    }
    return null;
}"""

_JS_SET_TEXTAREA = """(newHtml) => {
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


class StyleMigrator:
    def __init__(
        self,
        brightspace_url: str,
        moodle_url: str,
        primary_color: str,
        gemini_api_key: str,
        log: Callable[[str, str], None],
        on_complete: Callable = None,
    ):
        self.brightspace_url = brightspace_url
        self.moodle_url      = moodle_url
        self.primary_color   = primary_color
        self.gemini_api_key  = gemini_api_key
        self.log             = log
        self.on_complete     = on_complete

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _extract_brightspace_html(self, page: Page) -> Optional[str]:
        self.log("Extracting HTML from source editor...", "info")
        await page.wait_for_timeout(1500)

        result = await page.evaluate(_JS_EXTRACT_TEXTAREA)

        if result is None:
            for frame in page.frames:
                try:
                    r = await frame.evaluate(_JS_EXTRACT_TEXTAREA)
                    if r and "<" in r:
                        result = r
                        break
                except Exception:
                    pass

        if result:
            self.log(f"✓ Extracted {len(result):,} chars from Brightspace", "success")
        else:
            self.log("⚠ Could not locate source editor textarea", "warning")
        return result

    async def _scrape_moodle(self, context: BrowserContext) -> Optional[str]:
        self.log("Opening Moodle page in new tab...", "info")
        tab = await context.new_page()
        try:
            await tab.goto(self.moodle_url, wait_until="domcontentloaded", timeout=30000)
            await tab.wait_for_timeout(1500)

            html = await tab.evaluate("""() => {
                for (const sel of [
                    '[role="main"]', '#page-content', '.course-content',
                    '#region-main', '.region-content', 'main', '#content',
                ]) {
                    const el = document.querySelector(sel);
                    if (el) return el.outerHTML;
                }
                return document.body.innerHTML;
            }""")

            if html:
                self.log(f"✓ Scraped {len(html):,} chars from Moodle", "success")
            else:
                self.log("⚠ Could not find Moodle main content", "warning")
            return html
        except Exception as e:
            self.log(f"✗ Moodle scrape error: {e}", "error")
            return None
        finally:
            await tab.close()
            self.log("Moodle tab closed", "dim")

    def _call_gemini(self, source_html: str, moodle_html: str) -> Optional[str]:
        import google.generativeai as genai

        self.log("🤖 Sending to Gemini AI for restyling...", "info")
        try:
            genai.configure(api_key=self.gemini_api_key)
            model = genai.GenerativeModel("gemini-2.0-flash")
            prompt = _MIGRATOR_PROMPT.format(
                source_html=source_html,
                moodle_html=moodle_html,
                primary_color=self.primary_color,
            )
            response = model.generate_content(prompt)
            result = response.text.strip()

            # Strip markdown fences if model added them
            if result.startswith("```"):
                lines = result.splitlines()
                start = 1 if lines[0].startswith("```") else 0
                end   = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                result = "\n".join(lines[start:end]).strip()

            self.log(f"✅ AI restyling complete ({len(result):,} chars)", "success")
            return result
        except Exception as e:
            self.log(f"❌ Gemini API error: {e}", "error")
            return None

    async def _replace_and_save(self, page: Page, html: str) -> None:
        self.log("Writing styled HTML back to editor...", "info")

        set_ok = await page.evaluate(_JS_SET_TEXTAREA, html)
        if not set_ok:
            self.log("⚠ Could not write to source editor textarea", "warning")
            return

        await page.wait_for_timeout(500)

        # Click OK / Update in the source-code dialog
        for sel in [
            'd2l-button:has-text("OK")',
            'button:has-text("OK")',
            'd2l-button:has-text("Update")',
            'button:has-text("Update")',
        ]:
            _, btn = await _find_locator_any_frame(page, sel, retries=3, delay_ms=400)
            if btn:
                await btn.first.click()
                self.log("✓ Source code dialog closed", "success")
                break

        await page.wait_for_timeout(1200)

        # Click Save and Close on the editor page
        self.log("Looking for Save and Close...", "info")
        for sel in [
            'd2l-button:has-text("Save and Close")',
            'button:has-text("Save and Close")',
            'd2l-button:has-text("Save")',
            'button:has-text("Save")',
        ]:
            _, btn = await _find_locator_any_frame(page, sel, retries=6, delay_ms=600)
            if btn:
                await btn.first.click()
                self.log("✓ Page saved and closed", "success")
                return

        self.log("⚠ Save and Close not found — please save manually", "warning")

    # ── Main flow ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context)

            # ── Navigate ──────────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Navigating to Brightspace page...", "info")
            try:
                await page.goto(
                    self.brightspace_url, wait_until="domcontentloaded", timeout=30000
                )
            except Exception:
                pass
            self.log("✓ Page loaded", "success")

            # ── Options ───────────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Looking for Options button...", "info")
            try:
                await page.wait_for_selector("iframe", timeout=5000)
            except Exception:
                pass

            _, btn = await _find_locator_any_frame(
                page, "d2l-button-icon.content-options-btn", retries=7
            )
            if btn is None:
                self.log("✗ Options button not found", "error")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            await btn.first.scroll_into_view_if_needed()
            await btn.first.click()
            self.log("✓ Options menu opened", "success")

            # ── Edit ──────────────────────────────────────────────────────────
            self.log("Waiting for Edit menu item...", "info")
            _, edit_btn = await _find_locator_any_frame(
                page, "d2l-menu-item#optEdit", retries=8, delay_ms=500
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
            self.log("✓ Edit clicked — waiting for editor...", "success")

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(800)

            # ── Source Code ───────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Looking for Source Code button...", "info")
            _, src_btn = await _find_locator_any_frame(
                page,
                'd2l-htmleditor-button[cmd="d2l-source-code"]',
                retries=8,
                delay_ms=700,
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

            # ── Extract HTML ──────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            source_html = await self._extract_brightspace_html(page)
            if not source_html:
                self.log("✗ Could not extract HTML — see notes above", "error")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            # ── Print preview to log ───────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log(f"Extracted HTML ({len(source_html):,} chars):", "info")
            preview = source_html[:2000]
            if len(source_html) > 2000:
                preview += f"\n… (truncated, {len(source_html) - 2000:,} more chars)"
            self.log(preview, "step")
            self.log("─" * 52, "dim")
            self.log("✓ Step 1 complete — HTML extracted successfully.", "success")
            self.log("  Browser left open. Close it when you are done.", "dim")
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