from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Callable, List, Optional

if TYPE_CHECKING:
    from playwright.async_api import Page

from run_summary import RestyleRunSummary


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
        claude_api_key: str = "",
        claude_model: str = "",
        style_reference_html: str = "",
        theme_name: str = "blue",
        on_pages_found: Callable = None,
        bs_username: str = "",
        bs_password: str = "",
        sso_email: str = "",
        sso_password: str = "",
        move_unit_content: bool = True,
        stop_event=None,
        on_page_result: Callable = None,
    ):
        self.url = url
        self.log = log
        self.on_complete = on_complete
        self.claude_api_key = claude_api_key
        self.claude_model = claude_model
        self.style_reference_html = style_reference_html
        self.theme_name = theme_name
        self.on_pages_found = on_pages_found  # fn(pages) -> list of checked indices
        self.bs_username = bs_username
        self.bs_password = bs_password
        self.sso_email = sso_email
        self.sso_password = sso_password
        self.move_unit_content = move_unit_content
        self.stop_event = stop_event
        self.on_page_result = on_page_result
        self._clipboard_lock = asyncio.Lock()  # one tab touches clipboard at a time
        self._token_usage = {"input_tokens": 0, "output_tokens": 0, "cost_cad": 0.0}
        self._run_summary = RestyleRunSummary()
        self._completion_sent = False

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

    async def _read_editor_full_text(self, page: Page) -> str:
        """Select-all + copy + read clipboard. Unlike textContent, this reads the real
        CM6 doc model rather than only the virtualized (on-screen) viewport."""
        await page.evaluate("navigator.clipboard.writeText('')")
        if not await self._focus_codemirror(page):
            return ""
        await page.wait_for_timeout(200)
        await page.keyboard.press("Control+a")
        await page.wait_for_timeout(150)
        await page.keyboard.press("Control+c")
        await page.wait_for_timeout(400)
        return await page.evaluate("navigator.clipboard.readText()")

    async def replace_html_in_editor(self, page: Page, html: str) -> bool:
        self.log("Pasting styled HTML (Ctrl+A, Ctrl+V)...", "info")

        expected_len = len(html)
        pasted_ok = False
        for attempt in range(3):
            async with self._clipboard_lock:
                await page.evaluate("(h) => navigator.clipboard.writeText(h)", html)
                await page.wait_for_timeout(300)

                await self._focus_codemirror(page)
                await page.wait_for_timeout(400)
                await page.keyboard.press("Control+a")
                await page.wait_for_timeout(200)
                await page.keyboard.press("Control+v")
                await page.wait_for_timeout(1500)

                # Verify the paste actually landed in the CM6 doc model before trusting it
                cm_len = len(await self._read_editor_full_text(page))

            if cm_len >= expected_len * 0.9:
                pasted_ok = True
                break
            self.log(f"⚠ Paste verify failed (editor has {cm_len} chars, expected ~{expected_len}) — retrying", "warning")
            await page.wait_for_timeout(800)

        if not pasted_ok:
            self.log("✗ Paste never landed in editor — aborting save to avoid overwriting with stale content", "error")
            return False

        self.log("✓ HTML pasted", "success")
        await page.wait_for_timeout(1500)

        # Close source-code dialog
        for selector in ['[data-dialog-action="save"]', 'd2l-button:has-text("OK")', 'button:has-text("OK")', 'd2l-button:has-text("Update")', 'button:has-text("Update")']:
            _, btn = await _find_locator_any_frame(page, selector, retries=3, delay_ms=400)
            if btn:
                await btn.first.click()
                self.log("✓ Source dialog closed", "success")
                break

        await page.wait_for_timeout(1500)

        # Save and Close the editor page. This is the Collector's save path,
        # shared verbatim via editor_save so the two cannot drift apart again:
        # it refuses to click while a dialog is open, matches only the editor's
        # own "Save and Close", and reads the page back to prove it saved.
        self.log("Saving page...", "info")
        from editor_save import save_and_close, verify_topic_saved

        topic_url = page.url  # captured before the save navigates away
        if not await save_and_close(page, self.log):
            return False
        return await verify_topic_saved(page, topic_url, expected_len, self.log)

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

        _JS = r"""([baseUrl, lessonId, skipTypes]) => {
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
                    if (!href.includes('/topics/') &&
                        (href.includes('/units/') || href.includes('/lessons/')) &&
                        (key === lessonId || href.split(/[?#]/)[0].replace(/\/$/, '').endsWith('/' + lessonId))) return el;
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
            // Never fall back to the entire course sidebar for a missing unit.
            return [];
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

    async def _check_accessibility(self, page: Page, styled_html: str, label: str) -> None:
        """Run an advisory axe audit without changing the existing save flow."""
        try:
            from accessibility_checker import log_report, scan_html

            report = await scan_html(page.context, styled_html)
            self._run_summary.record_accessibility(report)
            log_report(report, self.log, label=label or "Page")
        except Exception as exc:
            self._run_summary.accessibility_checks_unavailable += 1
            self.log(f"♿ Accessibility check unavailable: {exc}", "warning")

    async def _process_topic(self, page: Page, url: str, label: str = "") -> bool:
        """Track one topic while the implementation performs the existing flow."""
        try:
            success = await self._process_topic_impl(page, url, label)
        except Exception:
            self._run_summary.record_page(False)
            raise
        self._run_summary.record_page(success)
        return success

    async def _process_topic_impl(self, page: Page, url: str, label: str = "") -> bool:
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

        from resource_restyle import remove_generated_resource_markers

        source_html, old_markers = remove_generated_resource_markers(source_html)
        if old_markers:
            self.log(f"Removed {old_markers} old generated resource marker(s) before restyling", "info")

        from youtube_embed import transform_standalone_youtube_urls

        youtube = transform_standalone_youtube_urls(source_html)
        source_html = youtube.html
        if youtube.changed:
            self.log(
                f"▶ YouTube: created {youtube.embeds_created} player(s), removed "
                f"{youtube.redundant_urls_removed} redundant raw URL(s)",
                "info",
            )

        # Unit Collector's assembled pages restyle far better than single topic pages
        # under the same prompt — the difference is the <h2>label</h2> section header
        # it prepends to every topic, which gives Claude a clear anchor to build a
        # "card" around. A lone page's raw source has no such heading, so the prompt
        # (which assumes card-per-section layout) has nothing to hang the design on.
        # Mirror that scaffold here for single-page restyles.
        heading = label
        if not heading:
            try:
                heading = await page.evaluate("() => document.title || ''")
                heading = heading.split(" - ")[0].strip()
            except Exception:
                heading = ""
        if heading:
            from content_preservation import add_generated_heading
            source_html = add_generated_heading(heading, source_html)

        from ai_styler import apply_style, DEFAULT_MODEL
        styled_html, usage = await apply_style(
            source_html=source_html,
            style_reference_html=self.style_reference_html,
            theme_name=self.theme_name,
            api_key=self.claude_api_key,
            model=self.claude_model or DEFAULT_MODEL,
            log_callback=self.log,
        )

        if not styled_html:
            self.log("✗ AI returned nothing — skipping", "error")
            return False

        if "BPA: CLASSIC" not in self.style_reference_html:
            styled_html, new_markers = remove_generated_resource_markers(styled_html)
            if new_markers:
                self.log(f"Removed {new_markers} generated resource marker(s) from result", "info")
            from resource_restyle import mark_resource_directory

            styled_html, is_directory = mark_resource_directory(styled_html)
            if is_directory:
                self.log("Applied compact spacing for a resource directory", "info")

        if usage:
            self._token_usage["input_tokens"] += usage["input_tokens"]
            self._token_usage["output_tokens"] += usage["output_tokens"]
            self._token_usage["cost_cad"] += usage["cost_cad"]

        await self._check_accessibility(page, styled_html, label)

        if not await self.replace_html_in_editor(page, styled_html):
            return False
        await page.wait_for_timeout(1500)
        return True

    # ── Main run ──────────────────────────────────────────────────────────────

    async def _choose_pages(self, pages: list[dict]) -> list[dict]:
        from restyle_selection import selected_pages
        indices = list(range(len(pages)))
        if self.on_pages_found:
            indices = await asyncio.to_thread(self.on_pages_found, pages)
        return selected_pages(pages, indices)

    def _stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _complete(self):
        if not self._completion_sent:
            self._completion_sent = True
            if self.on_complete:
                self.on_complete()

    async def _run_selected(self, context, selected: list[dict]) -> None:
        """Isolate each page failure; Stop prevents queued pages from starting."""
        sem = asyncio.Semaphore(5)

        async def process_one(topic: dict, index: int):
            async with sem:
                state = "skipped"
                tab = None
                attempted = False
                try:
                    if self._stopped():
                        self.log(f"Skipped: {topic['label']} (stopped)", "warning")
                        return
                    state = "failed"
                    tab = await context.new_page()
                    self.log(f"[{index + 1}/{len(selected)}] {topic['label']}", "step")
                    attempted = True
                    success = await self._process_topic(tab, topic["url"], topic["label"])
                    state = "changed" if success else "failed"
                    self.log(f"{'Saved' if success else 'Needs review'}: {topic['label']}",
                             "success" if success else "error")
                except Exception as exc:
                    if not attempted:
                        self._run_summary.record_page(False)
                    self.log(f"Failed: {topic['label']} — {exc}", "error")
                finally:
                    if self.on_page_result:
                        self.on_page_result(index, topic, state)
                    if tab is not None:
                        try:
                            await tab.close()
                        except Exception:
                            pass

        await asyncio.gather(*(process_one(topic, i) for i, topic in enumerate(selected)))

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context, self.bs_username or None, self.bs_password or None, self.sso_email or None, self.sso_password or None)

            self.log("─" * 52, "dim")
            self.log(f"Navigating to: {self.url}", "info")
            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.log("✓ Page loaded", "success")

            is_section = "/topics/" not in self.url
            move_overview = self.move_unit_content and re.search(r"/units/\d+(?:/|$)", self.url)
            selected = []
            if is_section:
                pages = await self.scrape_section_pages(page)
                if move_overview:
                    pages.insert(0, {"label": "Unit description → Overview page (move and restyle)",
                                     "url": self.url, "kind": "unit_overview"})
                if not pages:
                    self.log("No HTML pages found in this section. No changes made.", "warning")
                    return
                selected = await self._choose_pages(pages)
                if not selected or self._stopped():
                    self.log("Restyle cancelled. No changes made.", "info")
                    return
                # Moving a unit description is a separate, explicitly checked item.
                move_overview = any(item.get("kind") == "unit_overview" for item in selected)
                selected = [item for item in selected if item.get("kind") != "unit_overview"]
                self._run_summary.pages_selected = len(selected) + int(bool(move_overview))

            completed_overview_url = ""
            if move_overview:
                self.log("Checking the unit description for transferable content…", "info")

                async def restyle_overview(source_html: str):
                    from ai_styler import apply_style, DEFAULT_MODEL

                    return await apply_style(
                        source_html=source_html,
                        style_reference_html=self.style_reference_html,
                        theme_name=self.theme_name,
                        api_key=self.claude_api_key,
                        model=self.claude_model or DEFAULT_MODEL,
                        log_callback=self.log,
                    )

                from unit_overview import move_unit_url_to_overview

                transfer = await move_unit_url_to_overview(
                    page, self.url, restyle_overview, self.log
                )
                if self.on_page_result:
                    self.on_page_result(-1, {"label": "Unit description → Overview page", "url": self.url},
                                        "changed" if transfer.ok and transfer.status != "no-content"
                                        else "skipped" if transfer.ok else "failed")
                if not transfer.ok:
                    self._run_summary.record_page(False)
                    for i, topic in enumerate(selected):
                        if self.on_page_result:
                            self.on_page_result(i, topic, "skipped")
                    self.log(f"✗ Unit Overview transfer failed: {transfer.reason}", "error")
                    self._complete()
                    return
                if transfer.status == "no-content":
                    self.log("○ Unit description has no editable content to move", "dim")
                else:
                    self._run_summary.record_page(True)
                    completed_overview_url = transfer.topic_url.rstrip("/")
                    self.log(
                        f"✓ Unit description moved safely to {transfer.topic_url}", "success"
                    )
                    if transfer.usage:
                        self._token_usage["input_tokens"] += transfer.usage["input_tokens"]
                        self._token_usage["output_tokens"] += transfer.usage["output_tokens"]
                        self._token_usage["cost_cad"] += transfer.usage["cost_cad"]

            if is_section:
                if completed_overview_url:
                    for i, item in enumerate(selected):
                        if item["url"].rstrip("/") == completed_overview_url and self.on_page_result:
                            self.on_page_result(i, item, "changed")
                    selected = [
                        item for item in selected
                        if item["url"].rstrip("/") != completed_overview_url
                    ]
                self.log(f"Processing {len(selected)} page(s) — up to 5 at a time", "info")
                await self._run_selected(context, selected)
            else:
                # Single topic URL
                self._run_summary.pages_selected = 1
                await self._run_selected(context, [{"label": "", "url": self.url}])

            self.log("─" * 52, "dim")
            self._run_summary.log(self.log, self._token_usage)
            self.log("Batch finished. Close the browser when finished.", "info")
            self._complete()

            while browser.is_connected():
                await asyncio.sleep(0.5)
            self.log("Browser closed.", "dim")

        except Exception:
            self._complete()
            raise
        finally:
            self._complete()
            if browser.is_connected():
                await browser.close()
            await p.stop()


async def run(
    url: str,
    log: Callable[[str, str], None],
    on_complete: Callable = None,
    claude_api_key: str = "",
    claude_model: str = "",
    style_reference_html: str = "",
    theme_name: str = "blue",
    on_pages_found: Callable = None,
    bs_username: str = "",
    bs_password: str = "",
    sso_email: str = "",
    sso_password: str = "",
    move_unit_content: bool = True,
    stop_event=None,
    on_page_result: Callable = None,
) -> None:
    await PageAutomator(
        url=url,
        log=log,
        on_complete=on_complete,
        claude_api_key=claude_api_key,
        claude_model=claude_model,
        style_reference_html=style_reference_html,
        theme_name=theme_name,
        on_pages_found=on_pages_found,
        bs_username=bs_username,
        bs_password=bs_password,
        sso_email=sso_email,
        sso_password=sso_password,
        move_unit_content=move_unit_content,
        stop_event=stop_event,
        on_page_result=on_page_result,
    ).run()
