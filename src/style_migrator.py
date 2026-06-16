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
import re
import threading
from typing import Callable, List, Optional

from playwright.async_api import BrowserContext, Page

from config import SESSION_FILE

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


def _clean_moodle_html(raw: str) -> str:
    """Strip Moodle/Bootstrap noise from scraped HTML, keeping layout structure for Gemini."""
    try:
        from bs4 import BeautifulSoup, Comment
    except ImportError:
        return raw  # beautifulsoup4 not installed — fall back to raw

    soup = BeautifulSoup(raw, "html.parser")

    # Remove non-visual tags entirely
    for tag in soup.find_all(["script", "style", "svg", "noscript", "meta", "link"]):
        tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Strip noisy attributes; keep class (layout hint), style (inline design), href, src
    _KEEP_ATTRS = {"class", "style", "href", "src", "alt", "role", "aria-label"}
    for tag in soup.find_all(True):
        attrs_to_remove = [a for a in list(tag.attrs) if a not in _KEEP_ATTRS]
        for a in attrs_to_remove:
            del tag.attrs[a]
        # Trim very long class lists to first 4 tokens (Moodle adds many utility classes)
        if tag.get("class"):
            tag.attrs["class"] = tag.attrs["class"][:4]
        # Drop blank style attributes
        if tag.get("style", "").strip() == "":
            tag.attrs.pop("style", None)

    # Collapse long text nodes (Moodle sometimes embeds base64 or huge paragraphs)
    for text_node in soup.find_all(string=True):
        if len(text_node) > 500:
            text_node.replace_with(text_node[:500] + "…")

    return str(soup)


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


# The D2L source-code editor uses CodeMirror 6 (not a <textarea>).
# Content lives in .cm-content[data-language] > .cm-line divs (contenteditable).
# textContent of each .cm-line gives the raw HTML source for that line.
_JS_EXTRACT_TEXTAREA = """() => {
    function walkShadow(root) {
        const cmContent = root.querySelector('.cm-content[data-language]');
        if (cmContent) {
            const lines = Array.from(cmContent.querySelectorAll('.cm-line'));
            if (lines.length > 0) {
                return lines.map(l => l.textContent).join('\\n');
            }
        }
        for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) {
                const r = walkShadow(el.shadowRoot);
                if (r != null) return r;
            }
        }
        return null;
    }
    return walkShadow(document);
}"""

# Diagnostic version — returns a status object so Python can log what was found.
_JS_DIAGNOSE = """() => {
    const info = { textareas: 0, cmEditors: 0, cmLines: 0, snippet: null };
    function walk(root) {
        for (const el of root.querySelectorAll('*')) {
            if (el.tagName === 'TEXTAREA') info.textareas++;
            if (el.classList && el.classList.contains('cm-editor')) info.cmEditors++;
            if (el.classList && el.classList.contains('cm-line')) {
                info.cmLines++;
                if (!info.snippet) info.snippet = el.textContent.slice(0, 80);
            }
            if (el.shadowRoot) walk(el.shadowRoot);
        }
    }
    walk(document);
    return info;
}"""

_JS_SET_TEXTAREA = """(newHtml) => {
    // Strategy 1: find the d2l-htmleditor-source-editor element and use its CM6 view
    function walkShadow(root) {
        for (const el of root.querySelectorAll('*')) {
            if (el.tagName && el.tagName.toLowerCase() === 'd2l-htmleditor-source-editor') {
                // Try common CM6 view property names on the LitElement instance
                for (const prop of ['_editor', 'editor', '_view', 'view', '_cm', 'cm']) {
                    const v = el[prop];
                    if (v && v.state && typeof v.dispatch === 'function') {
                        v.dispatch(v.state.update({
                            changes: { from: 0, to: v.state.doc.length, insert: newHtml }
                        }));
                        return 'cm6:' + prop;
                    }
                }
            }
            if (el.shadowRoot) {
                const r = walkShadow(el.shadowRoot);
                if (r) return r;
            }
        }
        return null;
    }
    const cm6Result = walkShadow(document);
    if (cm6Result) return cm6Result;

    // Strategy 2: focus the CM6 contenteditable and replace via execCommand
    function findCmContent(root) {
        const el = root.querySelector('.cm-content[data-language]');
        if (el) return el;
        for (const child of root.querySelectorAll('*')) {
            if (child.shadowRoot) {
                const r = findCmContent(child.shadowRoot);
                if (r) return r;
            }
        }
        return null;
    }
    const cmContent = findCmContent(document);
    if (cmContent) {
        cmContent.focus();
        const range = document.createRange();
        range.selectNodeContents(cmContent);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        const ok = document.execCommand('insertText', false, newHtml);
        return ok ? 'execCommand' : 'execCommand-failed';
    }
    return false;
}"""


def _find_moodle_links(html: str) -> List[str]:
    """Return deduplicated moodle.okanagan.bc.ca URLs found in html."""
    urls = re.findall(r'https?://moodle\.okanagan\.bc\.ca[^\s"\'<>]*', html)
    return list(dict.fromkeys(urls))


class StyleMigrator:
    def __init__(
        self,
        brightspace_url: str,
        moodle_url: str,
        primary_color: str,
        gemini_api_key: str,
        log: Callable[[str, str], None],
        on_complete: Callable = None,
        moodle_ready_event: threading.Event = None,
        on_moodle_waiting: Callable = None,
        on_links_found: Callable = None,
    ):
        self.brightspace_url    = brightspace_url
        self.moodle_url         = moodle_url
        self.primary_color      = primary_color
        self.gemini_api_key     = gemini_api_key
        self.log                = log
        self.on_complete        = on_complete
        self.moodle_ready_event = moodle_ready_event
        self.on_moodle_waiting  = on_moodle_waiting
        self.on_links_found     = on_links_found

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
            try:
                info = await page.evaluate(_JS_DIAGNOSE)
                self.log(
                    f"✗ Editor not found — textareas={info['textareas']} "
                    f"cm-editors={info['cmEditors']} cm-lines={info['cmLines']} "
                    f"snippet={info['snippet']!r}",
                    "error",
                )
            except Exception as diag_err:
                self.log(f"✗ Could not locate source editor (diag failed: {diag_err})", "error")
        return result

    async def _scrape_moodle(self, context: BrowserContext, structured: bool = False):
        self.log("Opening Moodle page in new tab...", "info")
        tab = await context.new_page()
        try:
            try:
                await tab.goto(self.moodle_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await tab.wait_for_timeout(1500)

            # Detect if Moodle redirected to a login page and wait for manual login
            if "login" in tab.url.lower():
                self.log("─" * 52, "dim")
                self.log("  Moodle login required — log in in the browser.", "info")
                self.log("─" * 52, "dim")
                for i in range(120):
                    await tab.wait_for_timeout(3000)
                    if i % 10 == 9:
                        self.log(f"  Waiting for Moodle login... ({(i+1)*3}s)", "dim")
                    if "login" not in tab.url.lower():
                        self.log("✓ Moodle login detected", "success")
                        await tab.wait_for_timeout(1500)
                        break
                else:
                    self.log("✗ Moodle login timed out after 6 minutes", "error")
                    return None

            # Save session now that Moodle is open — captures Moodle cookies too
            try:
                await context.storage_state(path=SESSION_FILE)
                self.log("✓ Moodle session saved", "dim")
            except Exception:
                pass

            # Ask the user to confirm they're on the right page before scraping
            self.log("─" * 52, "dim")
            self.log(f"  Moodle loaded at: {tab.url}", "dim")
            self.log("  Make sure the course page is visible, then click", "info")
            self.log("  ✅ Ready — Scrape Now  in the app.", "info")
            self.log("─" * 52, "dim")
            if self.on_moodle_waiting:
                self.on_moodle_waiting()
            if self.moodle_ready_event:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.moodle_ready_event.wait)

            # If still on dashboard/wrong page, navigate to the original URL
            if "course/view.php" not in tab.url:
                self.log(f"  Not on a course page — navigating to provided URL...", "dim")
                try:
                    await tab.goto(self.moodle_url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass

            # Wait for the page to settle
            try:
                await tab.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            await tab.wait_for_timeout(1000)

            current_url = tab.url
            self.log(f"  Scraping: {current_url}", "dim")

            if "course/view.php" not in current_url:
                self.log(f"⚠ Still not on a course page — scrape may return nothing", "warning")
                self.log(f"  Got: {current_url}", "dim")

            if structured:
                try:
                    items = await tab.evaluate("""() => {
                        const TYPES = {
                            modtype_resource:     'FILE',
                            modtype_assign:       'ASSIGN',
                            modtype_quiz:         'QUIZ',
                            modtype_url:          'URL',
                            modtype_page:         'PAGE',
                            modtype_forum:        'FORUM',
                            modtype_label:        'LABEL',
                            modtype_folder:       'FOLDER',
                            modtype_kalturamedia: 'VIDEO',
                            modtype_lti:          'VIDEO',
                        };

                        function labelInfo(activity) {
                            const body = activity.querySelector(
                                '.contentafterlink, .description, .no-overflow, .labelcontent, .activitybody'
                            );
                            if (!body) return { type: 'LABEL', name: '(empty label)' };

                            // Detect Kaltura via video.js player in DOM or iframe
                            const vjsEl  = body.querySelector('.video-js, [id*="videojs_"]');
                            const kframe = body.querySelector('iframe[src*="kaltura"]');
                            const hasVideo = !!(vjsEl || kframe);

                            let entryId = '';
                            if (kframe) {
                                const m = (kframe.src || '').match(/entryid\\/([^\\/]+)/);
                                if (m) entryId = m[1];
                            }
                            if (!entryId && vjsEl) {
                                // entry ID sometimes in a data attribute or child source
                                const src = vjsEl.querySelector('source[src*="entryid"], source[src*="kaltura"]');
                                if (src) {
                                    const m = (src.getAttribute('src') || '').match(/entryid[=\\/]([^&\\/]+)/i);
                                    if (m) entryId = m[1];
                                }
                                // or in the player's id attribute: id_videojs_<hash>_<n>
                                const idAttr = vjsEl.id || '';
                                if (!entryId && idAttr) entryId = idAttr;
                            }

                            // Get text content stripping video.js control noise
                            const rawText = body.textContent.trim().replace(/\\s+/g, ' ');
                            const isOnlyVideoNoise = /^Video Player is loading/.test(rawText);

                            if (isOnlyVideoNoise || (hasVideo && rawText.replace(/Video Player.*/, '').trim().length < 5)) {
                                return {
                                    type: 'VIDEO',
                                    name: entryId ? 'Kaltura video [' + entryId + ']' : '(embedded video)',
                                };
                            }

                            // Build a label name from the text, excluding video player noise
                            const cleanText = rawText.replace(/Video Player is loading.*?(Current Time \\d|$)/g, '').trim();

                            let name = null;
                            const heading = body.querySelector('h1,h2,h3,h4,h5,h6');
                            if (heading) { const t = heading.textContent.trim(); if (t.length > 2) name = t; }
                            if (!name) {
                                const bold = body.querySelector('strong, b');
                                if (bold) { const t = bold.textContent.trim(); if (t.length > 2 && t.length < 120) name = t; }
                            }
                            if (!name) {
                                const link = body.querySelector('a');
                                if (link) { const t = link.textContent.trim(); name = t.length > 2 ? t : link.href || null; }
                            }
                            if (!name && cleanText.length > 2) {
                                name = cleanText.slice(0, 80) + (cleanText.length > 80 ? '…' : '');
                            }
                            if (!name) {
                                if (body.querySelector('img')) name = '(image)';
                                else name = '(empty label)';
                            }

                            // Annotate if a video is also present in this label
                            if (hasVideo) name += entryId ? ' [🎥 ' + entryId + ']' : ' [🎥]';

                            return { type: 'LABEL', name };
                        }

                        const result = [];
                        document.querySelectorAll('li.section, li.section.main').forEach(section => {
                            const heading = section.querySelector('.sectionname, h3, h4');
                            result.push({
                                type: 'SECTION',
                                name: heading ? heading.textContent.trim() : '(unnamed section)',
                                href: '',
                            });
                            section.querySelectorAll('li.activity').forEach(activity => {
                                const cls = Array.from(activity.classList);
                                const matched = cls.find(c => TYPES[c]);
                                if (!matched) return;
                                let type = TYPES[matched];
                                const anchor = activity.querySelector('a');
                                let name, href = anchor ? anchor.href : '';

                                if (matched === 'modtype_label') {
                                    const info = labelInfo(activity);
                                    type = info.type;
                                    name = info.name;
                                } else {
                                    const nameEl = activity.querySelector('.instancename, .activityname a, a');
                                    name = nameEl ? nameEl.textContent.trim().replace(/\\s{2,}.*$/, '').trim() : '(unnamed)';
                                }
                                result.push({ type, name, href });
                            });
                        });
                        return result;
                    }""")
                except Exception as eval_err:
                    self.log(f"✗ Could not read page — did it navigate away? ({eval_err})", "error")
                    try:
                        await tab.close()
                    except Exception:
                        pass
                    return None
                await tab.close()
                return items

            # Full HTML scrape (used for Gemini restyling)
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

            if not html:
                self.log("⚠ Could not find Moodle main content", "warning")
                return None

            raw_len = len(html)
            html = _clean_moodle_html(html)
            scraped_url = tab.url
            await tab.close()
            self.log(f"✓ Scraped {scraped_url}", "dim")
            self.log(f"  {raw_len:,} → {len(html):,} chars after cleanup", "success")
            return html
        except Exception as e:
            self.log(f"✗ Moodle scrape error: {e}", "error")
            try:
                await tab.close()
            except Exception:
                pass
            return None

    def _call_gemini(self, source_html: str, moodle_html: str) -> Optional[str]:
        from google import genai

        self.log("🤖 Sending to Gemini AI for restyling...", "info")
        try:
            client = genai.Client(api_key=self.gemini_api_key)
            prompt = _MIGRATOR_PROMPT.format(
                source_html=source_html,
                moodle_html=moodle_html,
                primary_color=self.primary_color,
            )
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
            )
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

        moodle_only = not self.brightspace_url

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context)

            if moodle_only:
                self.log("─" * 52, "dim")
                self.log("Moodle-only mode — skipping Brightspace.", "info")
                items = await self._scrape_moodle(context, structured=True)
                if items:
                    _ICONS = {
                        "SECTION": "──",
                        "FILE":    "📄",
                        "ASSIGN":  "📝",
                        "QUIZ":    "🧪",
                        "URL":     "🔗",
                        "PAGE":    "📖",
                        "FORUM":   "💬",
                        "LABEL":   "🏷 ",
                        "FOLDER":  "📁",
                        "VIDEO":   "🎥",
                    }
                    self.log("─" * 52, "dim")
                    for item in items:
                        icon = _ICONS.get(item["type"], "  ")
                        if item["type"] == "SECTION":
                            self.log("", "dim")
                            self.log(f"{icon} {item['name']}", "step")
                        else:
                            self.log(f"   {icon} {item['type']:<7}  {item['name']}", "info")
                    self.log("", "dim")
                    self.log(f"✓ Found {len([i for i in items if i['type'] != 'SECTION'])} items across {len([i for i in items if i['type'] == 'SECTION'])} sections", "success")
                else:
                    self.log("✗ No items found — make sure you're on a Moodle course page (view.php)", "error")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

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
                await page.wait_for_selector("iframe", timeout=8000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)

            _, btn = await _find_locator_any_frame(
                page, "d2l-button-icon.content-options-btn", retries=15, delay_ms=1000
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

            # Expand the toolbar overflow (⋯) first — Source Code is often chomped
            expand_result = await page.evaluate("""() => {
                function deepFind(root, fn, depth) {
                    if (depth > 15) return null;
                    for (const el of root.querySelectorAll('*')) {
                        if (fn(el)) return el;
                        if (el.shadowRoot) {
                            const found = deepFind(el.shadowRoot, fn, depth + 1);
                            if (found) return found;
                        }
                    }
                    return null;
                }
                // Native <button> whose innerHTML contains the three-dots SVG paths
                let btn = deepFind(document, el => {
                    if (!el.tagName || el.tagName.toLowerCase() !== 'button') return false;
                    const html = el.innerHTML || '';
                    return html.includes('M2,7') && html.includes('M9,7') && html.includes('M16,7');
                }, 0);
                if (btn) { btn.click(); return 'button-three-dots'; }

                // Fallback: d2l-htmleditor-button with no cmd attr
                const outer = deepFind(document, el =>
                    el.tagName && el.tagName.toLowerCase() === 'd2l-htmleditor-button'
                    && !el.getAttribute('cmd'), 0);
                if (outer) {
                    const inner = outer.shadowRoot && outer.shadowRoot.querySelector('button');
                    if (inner) { inner.click(); return 'no-cmd-inner-button'; }
                    outer.click();
                    return 'no-cmd-button';
                }
                return null;
            }""")
            if expand_result:
                self.log(f"  ✓ Overflow expanded ({expand_result})", "dim")
                await page.wait_for_timeout(700)

            src_frame, src_btn = await _find_locator_any_frame(
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

            # Click the native <button> inside the shadow DOM — bypasses the
            # TinyMCE iframe that intercepts pointer events on the toolbar.
            clicked = await src_frame.evaluate("""() => {
                function deepFind(root, fn, depth) {
                    if (depth > 15) return null;
                    for (const el of root.querySelectorAll('*')) {
                        if (fn(el)) return el;
                        if (el.shadowRoot) {
                            const found = deepFind(el.shadowRoot, fn, depth + 1);
                            if (found) return found;
                        }
                    }
                    return null;
                }
                const outer = deepFind(document, el =>
                    el.getAttribute && el.getAttribute('cmd') === 'd2l-source-code', 0);
                if (!outer) return false;
                const inner = outer.shadowRoot && outer.shadowRoot.querySelector('button');
                if (inner) { inner.click(); return true; }
                outer.click();
                return true;
            }""")
            if not clicked:
                self.log("  JS click failed — falling back to dispatch_event", "dim")
                await src_btn.first.dispatch_event('click')
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

            # ── Scrape Moodle ─────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            moodle_html = await self._scrape_moodle(context)
            if not moodle_html:
                self.log("✗ Could not scrape Moodle page — aborting", "error")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            # ── Gemini restyle ────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            styled_html = await asyncio.to_thread(
                self._call_gemini, source_html, moodle_html
            )
            if not styled_html:
                self.log("✗ AI returned nothing — aborting", "error")
                if self.on_complete:
                    self.on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            # ── Link fixer ────────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            moodle_links = _find_moodle_links(styled_html)
            if moodle_links:
                self.log(
                    f"⚠ Found {len(moodle_links)} Moodle link(s) — waiting for replacements…",
                    "warning",
                )
                if self.on_links_found:
                    replacements = await asyncio.to_thread(
                        self.on_links_found, moodle_links
                    )
                    for old_url, new_url in (replacements or {}).items():
                        if new_url and new_url.strip():
                            styled_html = styled_html.replace(old_url, new_url.strip())
                    self.log("✓ Links updated", "success")
                else:
                    self.log("⚠ No link handler — Moodle links left as-is", "warning")
            else:
                self.log("✓ No broken Moodle links found", "success")

            # ── Save back ─────────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            await self._replace_and_save(page, styled_html)

            self.log("─" * 52, "dim")
            self.log("✅ Migration complete! Close the browser when done.", "success")
            if self.on_complete:
                self.on_complete()
            while browser.is_connected():
                await asyncio.sleep(0.5)
            self.log("Browser closed.", "dim")
            return

        except Exception as e:
            self.log(f"✗ Unexpected error: {e}", "error")
            if self.on_complete:
                self.on_complete()
            raise
        finally:
            if browser.is_connected():
                await browser.close()
            await p.stop()