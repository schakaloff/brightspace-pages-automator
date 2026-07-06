import asyncio
import re
import tempfile
from pathlib import Path
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


class UnitCollector:
    def __init__(
        self,
        unit_url: str,
        target_url: str,
        theme_name: str,
        theme_colors: dict,
        gemini_api_key: str = "",
        style_reference_html: str = "",
        parallel_pages: int = 3,
        log: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
        bs_username: str = "",
        bs_password: str = "",
        sso_email: str = "",
        sso_password: str = "",
        moodle_url: str = "",
        moodle_username: str = "",
        moodle_password: str = "",
    ):
        self.unit_url = unit_url
        self.target_url = target_url
        self.theme_name = theme_name
        self.theme_colors = theme_colors
        self.gemini_api_key = gemini_api_key
        self.style_reference_html = style_reference_html
        self.parallel_pages = max(1, parallel_pages)
        self._log_fn = log
        self._on_complete = on_complete
        self.bs_username = bs_username
        self.bs_password = bs_password
        self.sso_email = sso_email
        self.sso_password = sso_password
        self.moodle_url = moodle_url.strip()
        self.moodle_username = moodle_username
        self.moodle_password = moodle_password
        self._name_matcher = lambda label: None
        self._clipboard_lock = asyncio.Lock()
        self._link_lock = asyncio.Lock()
        self._dl_dir = Path(tempfile.gettempdir()) / "brightspace_collector"
        self._dl_dir.mkdir(exist_ok=True)

    async def _build_name_matcher(self) -> None:
        """Populate self._name_matcher from the Moodle course, if configured.
        Non-fatal on any failure — falls back to a no-op matcher."""
        if not self.moodle_url:
            return
        try:
            import os
            from moodle_matcher import (
                ensure_moodle_session, scrape_moodle_names, build_name_matcher,
                MOODLE_SESSION_FILE,
            )
            if not os.path.exists(MOODLE_SESSION_FILE):
                await ensure_moodle_session(
                    self.moodle_username, self.moodle_password, log_fn=self.log
                )
            names = await scrape_moodle_names(self.moodle_url, log_fn=self.log)
            if not names:
                self.log("⚠ No Moodle names scraped — using Brightspace labels as-is", "warning")
                return
            self._name_matcher = build_name_matcher(names)
            self.log(f"✓ Moodle name matcher ready ({len(names)} item(s))", "success")
        except Exception as e:
            self.log(f"⚠ Moodle matching unavailable: {e} — using Brightspace labels as-is", "warning")

    def log(self, msg: str, level: str = "info"):
        if self._log_fn:
            self._log_fn(msg, level)

    # ── Editor helpers ────────────────────────────────────────────────────────

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

    async def _extract_html(self, page: Page) -> Optional[str]:
        _FIND_CM = """() => {
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
        }"""

        result = None
        for _ in range(8):
            await page.wait_for_timeout(1000)
            async with self._clipboard_lock:
                await page.evaluate("navigator.clipboard.writeText('')")
                focused = False
                for ctx in [page, *page.frames]:
                    try:
                        if await ctx.evaluate(_FIND_CM):
                            focused = True
                            break
                    except Exception:
                        pass
                if not focused:
                    continue
                await page.wait_for_timeout(300)
                await page.keyboard.press("Control+a")
                await page.wait_for_timeout(200)
                await page.keyboard.press("Control+c")
                await page.wait_for_timeout(400)
                result = await page.evaluate("navigator.clipboard.readText()")
            if result and "<" in result:
                break
        return result if (result and "<" in result) else None

    async def _js_click(self, page: Page, selector: str) -> bool:
        for ctx in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                if await ctx.evaluate(_JS_DEEP_CLICK, selector):
                    return True
            except Exception:
                pass
        return False

    async def _navigate_to_edit(self, page: Page, url: str) -> bool:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        _, btn = await _find_locator_any_frame(page, "d2l-button-icon.content-options-btn", retries=15)
        if btn is None:
            return False
        await btn.first.scroll_into_view_if_needed()
        await btn.first.click()

        _, edit_btn = await _find_locator_any_frame(page, "d2l-menu-item#optEdit", retries=8, delay_ms=500)
        if edit_btn is None:
            return False
        await edit_btn.first.wait_for(state="visible", timeout=4000)
        await edit_btn.first.click()

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(800)

        # Confirm we're in an HTML content editor, not a topic-properties form.
        # File topics have an optEdit that opens properties — no d2l-htmleditor there.
        has_editor = False
        for _ in range(8):
            try:
                has_editor = await page.evaluate("""() => {
                    function deepFind(root) {
                        if (root.querySelector('d2l-htmleditor')) return true;
                        for (const c of root.querySelectorAll('*')) {
                            if (c.shadowRoot && deepFind(c.shadowRoot)) return true;
                        }
                        return false;
                    }
                    return deepFind(document);
                }""")
                if has_editor:
                    break
            except Exception:
                pass
            await page.wait_for_timeout(600)
        return has_editor

    async def _open_source_code(self, page: Page) -> bool:
        opened = False
        for _ in range(5):
            if await self._js_click(page, 'd2l-htmleditor-button[cmd="d2l-source-code"]'):
                opened = True
                break
            await page.wait_for_timeout(700)

        if not opened:
            await self._js_click(page, "d2l-htmleditor-button-toggle.d2l-htmleditor-toolbar-chomper")
            await page.wait_for_timeout(700)
            for sel in (
                'd2l-htmleditor-button[cmd="d2l-source-code"]',
                'd2l-htmleditor-menu-item[cmd="d2l-source-code"]',
            ):
                for _ in range(4):
                    if await self._js_click(page, sel):
                        opened = True
                        break
                    await page.wait_for_timeout(500)
                if opened:
                    break
        return opened

    async def _close_source_dialog(self, page: Page) -> bool:
        for sel in ['[data-dialog-action="save"]', 'd2l-button:has-text("Update")',
                    'button:has-text("Update")', 'd2l-button:has-text("OK")', 'button:has-text("OK")']:
            _, btn = await _find_locator_any_frame(page, sel, retries=5, delay_ms=500)
            if btn:
                await btn.first.click()
                await page.wait_for_timeout(1200)
                return True
        self.log("  ⚠ Source code dialog close button not found — content may not apply", "warning")
        await page.wait_for_timeout(800)
        return False

    async def _close_any_dialog(self, page: Page):
        """Dismiss a stuck Insert Stuff dialog iframe — only if one is actually open.
        Never touches Cancel/Close buttons on the main editor page."""
        try:
            # Only act if an Insert Stuff dialog iframe is present
            isf_count = await page.locator(
                'iframe[title="Insert Stuff"], iframe.d2l-dialog-frame'
            ).count()
            if isf_count == 0:
                return
        except Exception:
            return
        # Click Cancel/Close only inside the dialog frames, not the main page
        for frame in page.frames:
            url = frame.url or ""
            # Skip the main page frame
            if frame == page.main_frame:
                continue
            for sel in ['button:has-text("Cancel")', 'button:has-text("Close")']:
                try:
                    loc = frame.locator(sel)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        await loc.first.click(timeout=2000)
                        await page.wait_for_timeout(600)
                        return
                except Exception:
                    pass

    async def _save_and_close(self, page: Page) -> bool:
        # Wait for any d2l-shim overlay (left by Insert Stuff dialogs) to clear.
        # The shim is a <div class="d2l-shim ..."> so use class selector, not tag.
        for _ in range(30):
            try:
                shim_count = await page.locator('.d2l-shim').count()
                if shim_count == 0:
                    break
            except Exception:
                break
            await page.wait_for_timeout(500)

        for sel in ['d2l-button:has-text("Save and Close")', 'button:has-text("Save and Close")',
                    'd2l-button:has-text("Save")', 'button:has-text("Save")']:
            _, btn = await _find_locator_any_frame(page, sel, retries=6, delay_ms=600)
            if btn:
                await btn.first.click()
                try:
                    await page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass
                # Extra wait — Brightspace can finish its POST but not yet committed on the server
                await page.wait_for_timeout(2000)
                self.log("✓ Saved", "success")
                return True
        self.log("⚠ Save button not found", "warning")
        return False

    # ── Scraping ──────────────────────────────────────────────────────────────

    async def _scrape_topics(self, page: Page) -> List[dict]:
        self.log("Scanning unit for topic pages...", "info")
        try:
            await page.wait_for_selector("iframe", timeout=8000)
        except Exception:
            pass

        base_url = "/".join(self.unit_url.split("/")[:3])
        lesson_id = self.unit_url.rstrip("/").split("/")[-1]

        SKIP_TYPES = ["quiz", "dropbox", "discussion", "survey", "assignment", "checklist", "lti"]

        _JS = """([baseUrl, lessonId, skipTypes]) => {
            function iconHint(el) {
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
                return (el.getAttribute('sub-title-text') || '').toLowerCase();
            }
            const FILE_SUBTITLES = ['pdf', 'powerpoint', 'excel', 'word document', 'zip',
                                       'video', 'audio', 'mp4', 'mp3', 'wav', 'image'];
            const FILE_HINT_RE = /file-(pdf|pptx?|xlsx?|docx?|zip|mp[34]|wav|png|jpe?g|gif)\b/;
            function topicsIn(root) {
                return Array.from(root.querySelectorAll('d2l-list-item-nav'))
                    .filter(el => (el.getAttribute('action-href') || '').includes('/topics/'))
                    .filter(el => !skipTypes.some(t => iconHint(el).includes(t)))
                    .map(el => {
                        const hint = iconHint(el);
                        const subtitle = (el.getAttribute('sub-title-text') || '').toLowerCase();
                        const isLink = hint.includes('link') || hint.includes('url') || hint.includes('media');
                        const isFile = !isLink && (
                            FILE_SUBTITLES.some(s => subtitle.includes(s)) ||
                            FILE_HINT_RE.test(hint)
                        );
                        return {
                            label: el.getAttribute('label') || el.getAttribute('drag-handle-text') || 'Untitled',
                            url: baseUrl + el.getAttribute('action-href'),
                            hint,
                            subtitle,
                            type: isLink ? 'link' : (isFile ? 'file' : 'html'),
                        };
                    });
            }
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

        topics = []
        for attempt in range(10):
            await page.wait_for_timeout(2000)
            try:
                topics = await page.evaluate(_JS, [base_url, lesson_id, SKIP_TYPES])
            except Exception:
                pass
            if not topics:
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        topics = await frame.evaluate(_JS, [base_url, lesson_id, SKIP_TYPES])
                        if topics:
                            break
                    except Exception:
                        pass
            if topics:
                break
            self.log(f"  Waiting for SPA ({attempt + 1}/10)...", "dim")

        seen = set()
        unique = []
        for t in (topics or []):
            if t["url"] not in seen:
                seen.add(t["url"])
                suffix = {"link": "  [link]", "file": "  [file]"}.get(t.get("type", "html"), "")
                self.log(f"  + {t['label']}{suffix}", "dim")
                unique.append(t)

        if unique:
            self.log(f"✓ Found {len(unique)} topic(s)", "success")
        else:
            self.log("⚠ No topics found — are you logged in? Is the unit expanded?", "warning")
        return unique

    # ── Collect methods ───────────────────────────────────────────────────────

    async def _collect_link(self, page: Page, url: str, label: str) -> Optional[str]:
        self.log(f"  Link: {label}", "step")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)

        pages_before = set(id(p) for p in page.context.pages)
        clicked = False
        for ctx in [page, *page.frames]:
            try:
                loc = ctx.locator("d2l-button.topic-jump-button, .topic-jump-button")
                if await loc.count() > 0:
                    await loc.first.click(timeout=4000)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            self.log(f"  ⚠ Open Link button not found for {label}", "warning")
            return None

        for _ in range(16):
            await page.wait_for_timeout(500)
            new_tabs = [p for p in page.context.pages if id(p) not in pages_before]
            if new_tabs:
                new_tab = new_tabs[0]
                try:
                    await new_tab.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                link_url = new_tab.url
                await new_tab.close()
                self.log(f"  ✓ {label} → {link_url}", "success")
                return link_url

        self.log(f"  ⚠ No new tab opened for {label}", "warning")
        return None

    async def _collect_html(self, page: Page, url: str, label: str) -> Optional[str]:
        self.log(f"─" * 52, "dim")
        self.log(f"Collecting: {label}", "step")

        if not await self._navigate_to_edit(page, url):
            self.log(f"  → {label} is a file topic, skipping HTML editor", "dim")
            return None
        if not await self._open_source_code(page):
            self.log(f"  → No HTML editor found for {label}, treating as file", "dim")
            return None

        html = await self._extract_html(page)
        if html:
            self.log(f"✓ {label} ({len(html):,} chars)", "success")
        else:
            self.log(f"✗ Could not extract HTML for {label}", "error")
        return html

    async def _download_file(self, page: Page, url: str, label: str) -> Optional[dict]:
        self.log(f"─" * 52, "dim")
        self.log(f"Downloading: {label}", "step")

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        _, btn = await _find_locator_any_frame(page, "d2l-button-icon.content-options-btn", retries=15)
        if btn is None:
            self.log(f"✗ No options button for {label}", "error")
            return None
        await btn.first.scroll_into_view_if_needed()
        await btn.first.click()

        _, dl_btn = await _find_locator_any_frame(page, "d2l-menu-item#optDownload", retries=5, delay_ms=500)
        if dl_btn is None:
            self.log(f"✗ No Download option for {label}", "error")
            return None

        try:
            async with page.expect_download(timeout=120000) as dl_info:
                await dl_btn.first.click()
            dl = await dl_info.value
            filename = dl.suggested_filename
            save_path = self._dl_dir / filename
            await dl.save_as(str(save_path))
            self.log(f"✓ Downloaded: {filename}", "success")
            return {"label": label, "path": str(save_path), "filename": filename}
        except Exception as e:
            self.log(f"✗ Download failed for {label}: {e}", "error")
            return None

    def _html_from_zip(self, zip_path: str) -> Optional[str]:
        import zipfile
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                html_names = [n for n in zf.namelist() if n.lower().endswith((".html", ".htm"))]
                if not html_names:
                    return None
                raw = zf.read(html_names[0]).decode("utf-8", errors="replace")
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, "lxml")
            for tag in soup.find_all(["script", "style", "meta", "link", "head"]):
                tag.decompose()
            body = soup.find("body")
            return (body.decode_contents() if body else str(soup)).strip()
        except Exception as e:
            self.log(f"  ✗ Could not extract HTML from zip: {e}", "error")
            return None

    # ── Assemble + Style ──────────────────────────────────────────────────────

    def _build_combined_html(self, items: list, has_files: bool = False) -> str:
        parts = []
        for item in items:
            t = item.get("type")
            if t == "html" and item.get("html"):
                label = item["label"].replace("<", "&lt;").replace(">", "&gt;")
                parts.append(f"<h2>{label}</h2>\n{item['html']}\n<hr/>\n")
            elif t == "link" and item.get("link_url"):
                label = item["label"].replace("<", "&lt;").replace(">", "&gt;")
                parts.append(
                    f'<p><strong>{label}:</strong> '
                    f'<a href="{item["link_url"]}">{item["link_url"]}</a></p>\n'
                )
        if has_files:
            parts.append("<h2>Files</h2>\n<p></p>\n")
        return "\n".join(parts)

    async def _paste_html(self, page: Page, html: str) -> bool:
        _FIND_CM = """() => {
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
        }"""

        async with self._clipboard_lock:
            await page.evaluate("(h) => navigator.clipboard.writeText(h)", html)
            await page.wait_for_timeout(300)
            focused = False
            for ctx in [page, *page.frames]:
                try:
                    if await ctx.evaluate(_FIND_CM):
                        focused = True
                        break
                except Exception:
                    pass
            if not focused:
                self.log("✗ Could not find HTML editor for paste", "error")
                return False
            await page.wait_for_timeout(400)
            await page.keyboard.press("Control+a")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Control+v")
            await page.wait_for_timeout(600)
        self.log("✓ HTML pasted", "success")
        return True

    async def _editor_cursor_end(self, page: Page):
        for frame in page.frames:
            try:
                body = frame.locator('body[contenteditable="true"]')
                if await body.count() > 0 and await body.first.is_visible():
                    await body.first.click()
                    await page.keyboard.press("Control+End")
                    await page.wait_for_timeout(200)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(200)
                    return
            except Exception:
                pass
        # Do not press Enter as fallback — it can trigger focused page buttons (Cancel, etc.)

    async def _insert_file(self, page: Page, file_item: dict) -> bool:
        self.log(f"  Inserting: {file_item['filename']}", "info")
        try:
            # Dismiss any visible dialog left open from a previous failed insert
            await self._close_any_dialog(page)
            await page.wait_for_timeout(400)

            # Step 1: Click Insert Stuff button — retry until toolbar is ready
            isf_clicked = False
            for _ in range(12):
                if await self._js_click(page, 'd2l-htmleditor-button[cmd="d2l-isf"]'):
                    isf_clicked = True
                    break
                await page.wait_for_timeout(1000)
            if not isf_clicked:
                # Debug: dump what editor buttons/page state we actually see
                for ctx in [page, *page.frames]:
                    try:
                        info = await ctx.evaluate("""() => {
                            function deepCollect(root, depth) {
                                if (depth > 6) return [];
                                const tags = [];
                                for (const c of root.querySelectorAll('d2l-htmleditor-button, d2l-htmleditor-button-toggle')) {
                                    tags.push((c.getAttribute('cmd') || c.getAttribute('text') || '?'));
                                }
                                for (const c of root.querySelectorAll('*')) {
                                    if (c.shadowRoot) tags.push(...deepCollect(c.shadowRoot, depth+1));
                                }
                                return tags;
                            }
                            const btns = deepCollect(document, 0);
                            return {url: location.href, hasEditor: !!document.querySelector('d2l-htmleditor'), buttons: btns.slice(0,20)};
                        }""")
                        if info:
                            self.log(f"  (page={info['url'][-60:]}, hasEditor={info['hasEditor']}, buttons={info['buttons']})", "dim")
                            break
                    except Exception:
                        pass
                self.log("  ✗ Insert Stuff button not found", "warning")
                return False
            # Step 2: Wait for My Computer option and click it (content-driven, not fixed wait)
            _JS_CLICK_MY_COMPUTER = """() => {
                for (const el of document.querySelectorAll('.d2l-datalist-item-content, [title="My Computer"]')) {
                    if ((el.getAttribute('title') || el.textContent || '').includes('My Computer')) {
                        el.click(); return true;
                    }
                }
                return false;
            }"""
            clicked = False
            for _ in range(20):
                await page.wait_for_timeout(500)
                for frame in page.frames:
                    try:
                        if await frame.evaluate(_JS_CLICK_MY_COMPUTER):
                            clicked = True
                            break
                    except Exception:
                        pass
                if clicked:
                    break
            if not clicked:
                self.log("  ✗ My Computer option not found", "warning")
                return False

            # Give the ISF dialog time to load the file-input UI after My Computer is selected.
            # First call is slow (cold frame); subsequent calls are instant from cache.
            await page.wait_for_timeout(2000)

            # Step 3: Find the file-chooser trigger button and open the OS file dialog
            upload_trigger = None
            for _ in range(40):
                await page.wait_for_timeout(500)
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        loc = frame.locator('.d2l-fileinput-addbuttons button')
                        if await loc.count() > 0 and await loc.first.is_visible():
                            upload_trigger = loc.first
                            break
                    except Exception:
                        pass
                if upload_trigger is not None:
                    break

            if upload_trigger is None:
                self.log("  ✗ File chooser trigger not found inside Insert Stuff dialog", "warning")
                return False

            async with page.expect_file_chooser(timeout=15000) as fc_info:
                await upload_trigger.click(timeout=5000)

            # Step 4: Set the file
            fc = await fc_info.value
            await fc.set_files(file_item["path"])
            # Give Brightspace time to start the XHR upload before we poll for completion
            await page.wait_for_timeout(1500)

            # Step 5: Wait for Brightspace's XHR upload to finish, then click the footer
            # "Upload" button (NOT the file-chooser trigger — that one is inside
            # .d2l-fileinput-addbuttons; the confirm button is in .d2l-dialog-footer).
            _JS_UPLOAD_DONE = """() => {
                const progress = document.querySelector(
                    '.d2l-fileinput-upload-progress-container:not(.d2l-hidden)');
                if (progress) return false;
                const files = document.querySelectorAll(
                    '.d2l-fileinput-filelist li:not(.d2l-fileinput-placeholder)');
                if (files.length > 0) return true;
                // Also done when a file-error element is shown (file already exists)
                const err = document.querySelector(
                    '.d2l-fileinput-error, .d2l-alert-critical, [class*="fileinput-error"]');
                return !!(err && err.offsetParent !== null);
            }"""
            _JS_CLICK_FOOTER_UPLOAD = """() => {
                const footer = document.querySelector('.d2l-dialog-footer');
                if (!footer) return false;
                for (const b of footer.querySelectorAll('button')) {
                    if (b.textContent.trim() === 'Upload' && b.offsetParent !== null) {
                        b.click(); return true;
                    }
                }
                return false;
            }"""

            upload_done = False
            for _ in range(40):  # up to 20s for XHR upload
                await page.wait_for_timeout(500)
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        if await frame.evaluate(_JS_UPLOAD_DONE):
                            upload_done = True
                            break
                    except Exception:
                        pass
                if upload_done:
                    break

            if not upload_done:
                self.log(f"  ⚠ File upload did not complete for {file_item['filename']}", "warning")

            uploaded = False
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    if await frame.evaluate(_JS_CLICK_FOOTER_UPLOAD):
                        self.log(f"  ↑ Clicked footer Upload button", "info")
                        uploaded = True
                        break
                except Exception:
                    pass

            if not uploaded:
                self.log(f"  ⚠ Footer Upload button not clicked for {file_item['filename']}", "warning")

            # Step 5a: After clicking Upload in an error state, Brightspace may show
            # an intermediate screen with an "Insert" button before the overwrite dialog.
            _JS_CLICK_INSERT_ON_ERROR = """() => {
                const footer = document.querySelector('.d2l-dialog-footer');
                if (!footer) return false;
                for (const b of footer.querySelectorAll('button')) {
                    if (b.textContent.trim() === 'Insert' && b.offsetParent !== null) {
                        b.click(); return true;
                    }
                }
                return false;
            }"""
            for _ in range(10):
                await page.wait_for_timeout(500)
                clicked_insert_on_error = False
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        if await frame.evaluate(_JS_CLICK_INSERT_ON_ERROR):
                            self.log("  ↩ File error: clicked Insert to proceed to overwrite dialog", "info")
                            clicked_insert_on_error = True
                            break
                    except Exception:
                        pass
                if clicked_insert_on_error:
                    await page.wait_for_timeout(1200)
                    # Check if dialog already closed — if so, insertion is done
                    try:
                        if await page.locator('iframe[title="Insert Stuff"], iframe.d2l-dialog-frame').count() == 0:
                            self.log(f"  ✓ Inserted (via error-Insert): {file_item['filename']}", "success")
                            return True
                    except Exception:
                        pass
                    break

            # Step 5b: Handle overwrite dialog if the file already exists
            _JS_OVERWRITE = """() => {
                const ul = document.getElementById('SelectedOverwriteOption');
                if (!ul) return false;
                const radios = ul.querySelectorAll('input[type="radio"]');
                for (const r of radios) {
                    if (r.value === '2') { r.click(); break; }
                }
                const footer = document.querySelector('.d2l-dialog-footer');
                if (footer) {
                    for (const b of footer.querySelectorAll('button')) {
                        if (b.hasAttribute('primary') && b.offsetParent !== null) { b.click(); return true; }
                    }
                    for (const b of footer.querySelectorAll('button')) {
                        if (b.textContent.trim() === 'Save' && b.offsetParent !== null) { b.click(); return true; }
                    }
                }
                return true;
            }"""
            for _ in range(10):
                await page.wait_for_timeout(500)
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        handled = await frame.evaluate(_JS_OVERWRITE)
                        if handled:
                            self.log(f"  ↩ Overwrite dialog: selected 'Overwrite existing file'", "info")
                            break
                    except Exception:
                        pass
                else:
                    continue
                break

            # Step 5c: Fill the link-text field with the corrected name (if we have one)
            # so Brightspace shows a readable title instead of the raw file path.
            corrected = file_item.get("corrected_name")
            if corrected:
                display_name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", corrected)
                _JS_FILL_ZK = """(name) => {
                    const el = document.querySelector('#z_k');
                    if (!el) return false;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, name);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }"""
                filled = False
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        if await frame.evaluate(_JS_FILL_ZK, display_name):
                            filled = True
                            break
                    except Exception:
                        pass
                if filled:
                    self.log(f"  ✓ Set link text: {display_name}", "dim")
                else:
                    self.log(f"  ⚠ #z_k field not found — link text left as default", "dim")

            # Step 6: Wait for "Insert" button (appears after upload completes) and click it
            _JS_CLICK_INSERT = """() => {
                const btns = Array.from(document.querySelectorAll('button'));
                for (const b of btns) {
                    if (b.textContent.trim() === 'Insert' && b.offsetParent !== null) {
                        b.click(); return true;
                    }
                }
                return false;
            }"""
            inserted = False
            for _ in range(40):
                await page.wait_for_timeout(500)
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        if await frame.evaluate(_JS_CLICK_INSERT):
                            inserted = True
                            break
                    except Exception:
                        pass
                if inserted:
                    break

            if not inserted:
                self.log(f"  ⚠ Insert button not found for {file_item['filename']}", "warning")
                await self._close_any_dialog(page)
                return False

            # Wait for the dialog to fully close
            for _ in range(20):
                await page.wait_for_timeout(500)
                try:
                    if await page.locator('iframe[title="Insert Stuff"], iframe.d2l-dialog-frame').count() == 0:
                        break
                except Exception:
                    break

            self.log(f"  ✓ Inserted: {file_item['filename']}", "success")
            return True
        except Exception as e:
            self.log(f"  ✗ Insert failed for {file_item['filename']}: {e}", "error")
            return False

    async def _source_code_append(self, page: Page, section_html: str) -> bool:
        """Open source code dialog on an already-open edit page, append HTML at end, close dialog.
        Does NOT save — caller is responsible for saving."""
        _FIND_CM = """() => {
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
        }"""

        if not await self._open_source_code(page):
            self.log("✗ Could not open source code on target page", "error")
            return False

        focused = False
        for _ in range(8):
            await page.wait_for_timeout(800)
            for ctx in [page, *page.frames]:
                try:
                    if await ctx.evaluate(_FIND_CM):
                        focused = True
                        break
                except Exception:
                    pass
            if focused:
                break

        if not focused:
            self.log("✗ Could not find source code editor", "error")
            return False

        async with self._clipboard_lock:
            await page.evaluate("(h) => navigator.clipboard.writeText(h)", section_html)
            await page.wait_for_timeout(300)
            for ctx in [page, *page.frames]:
                try:
                    if await ctx.evaluate(_FIND_CM):
                        break
                except Exception:
                    pass
            await page.wait_for_timeout(400)
            await page.keyboard.press("Control+End")
            await page.wait_for_timeout(200)
            await page.keyboard.press("Control+v")
            await page.wait_for_timeout(1500)

        return await self._close_source_dialog(page)

    async def _scrape_topic(self, context, topic: dict, semaphore: asyncio.Semaphore) -> dict:
        """Scrape one topic and return its content. Runs under semaphore for HTML/file types.
        Link types use _link_lock instead to avoid the new-tab race condition."""
        label = topic["label"]
        t = topic.get("type", "html")
        result: dict = {"topic": topic, "html": None, "link_url": None, "file": None}

        if t == "link":
            async with self._link_lock:
                tab = await context.new_page()
                try:
                    result["link_url"] = await self._collect_link(tab, topic["url"], label)
                finally:
                    try:
                        await tab.close()
                    except Exception:
                        pass
        else:
            async with semaphore:
                tab = await context.new_page()
                try:
                    if t == "file":
                        fd = await self._download_file(tab, topic["url"], label)
                    else:
                        html = await self._collect_html(tab, topic["url"], label)
                        if html is not None:
                            result["html"] = html
                            return result
                        # HTML collection failed → treat as file
                        fd = await self._download_file(tab, topic["url"], label)

                    if fd:
                        if fd.get("filename", "").lower().endswith(".html.zip"):
                            extracted = self._html_from_zip(fd["path"])
                            if extracted:
                                result["html"] = extracted
                            else:
                                fd["corrected_name"] = self._name_matcher(label)
                                result["file"] = fd
                        else:
                            fd["corrected_name"] = self._name_matcher(label)
                            result["file"] = fd
                finally:
                    try:
                        await tab.close()
                    except Exception:
                        pass
        return result

    async def _append_to_target(self, context, section_html: str) -> bool:
        """Open the target page editor, append section_html via source code, save and close."""
        page = await context.new_page()
        try:
            if not await self._navigate_to_edit(page, self.target_url):
                self.log("✗ Could not open target page editor", "error")
                return False
            if not await self._source_code_append(page, section_html):
                return False
            return await self._save_and_close(page)
        except Exception as e:
            self.log(f"✗ Append to target failed: {e}", "error")
            return False
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _apply_gemini(self, context) -> bool:
        if not self.gemini_api_key:
            self.log("⚠ No Gemini API key — skipping styling step", "warning")
            return False

        self.log("─" * 52, "dim")
        self.log("Applying Gemini styling to assembled page...", "info")

        page = await context.new_page()
        try:
            if not await self._navigate_to_edit(page, self.target_url):
                self.log("✗ Could not reopen target page for styling", "error")
                return False
            if not await self._open_source_code(page):
                self.log("✗ Source Code not found for styling", "error")
                return False

            source_html = await self._extract_html(page)
            if not source_html:
                self.log("✗ Could not extract assembled HTML", "error")
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
                self.log("✗ Gemini returned nothing", "error")
                return False

            await self._paste_html(page, styled_html)
            if not await self._close_source_dialog(page):
                return False
            return await self._save_and_close(page)
        finally:
            await page.close()

    # ── Main run ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context, self.bs_username or None, self.bs_password or None, self.sso_email or None, self.sso_password or None)
            self.log("─" * 52, "dim")
            self.log(f"Navigating to unit: {self.unit_url}", "info")

            try:
                await page.goto(self.unit_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            topics = await self._scrape_topics(page)
            # Never collect the target page itself
            target_path = self.target_url.rstrip("/")
            topics = [t for t in topics if t["url"].rstrip("/") != target_path]
            if not topics:
                self.log("✗ No topics found — nothing to collect", "error")
                if self._on_complete:
                    self._on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            await self._build_name_matcher()

            # ── Phase 1: scrape all topics in parallel ────────────────────────
            self.log("─" * 52, "dim")
            self.log(
                f"Scraping {len(topics)} topic(s) "
                f"({self.parallel_pages} page(s) in parallel)...", "info"
            )
            semaphore = asyncio.Semaphore(self.parallel_pages)
            scrape_tasks = [self._scrape_topic(context, t, semaphore) for t in topics]
            results = await asyncio.gather(*scrape_tasks, return_exceptions=True)

            # ── Phase 2: build ordered section list + file list ───────────────
            self.log("─" * 52, "dim")
            self.log("Assembling target page...", "info")
            sections: list = []
            file_items: list = []
            html_count = link_count = file_count = 0

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.log(f"✗ Topic {i + 1} scrape failed: {result}", "error")
                    continue
                topic = result["topic"]
                safe = topic["label"].replace("<", "&lt;").replace(">", "&gt;")

                if result["html"]:
                    sections.append(f"<h2>{safe}</h2>\n{result['html']}\n<hr/>\n")
                    html_count += 1
                elif result["link_url"]:
                    corrected = self._name_matcher(topic["label"])
                    link_label = (corrected or topic["label"]).replace("<", "&lt;").replace(">", "&gt;")
                    sections.append(
                        f'<p><strong>{link_label}:</strong> '
                        f'<a href="{result["link_url"]}">{result["link_url"]}</a></p>\n'
                    )
                    link_count += 1
                elif result["file"]:
                    file_items.append(result["file"])
                    file_count += 1

            # ── Phase 3: one persistent editor session — append all, save once ─
            tab = await context.new_page()
            try:
                if not await self._navigate_to_edit(tab, self.target_url):
                    self.log("✗ Could not open target editor", "error")
                else:
                    for section in sections:
                        if not await self._source_code_append(tab, section):
                            self.log("⚠ Failed to append a section — continuing", "warning")

                    # Let the WYSIWYG editor fully render the new content before saving
                    await tab.wait_for_timeout(3000)

                    if file_items:
                        self.log(f"Inserting {file_count} file(s)...", "info")
                        if not await self._source_code_append(tab, "<h2>Files</h2>\n<p></p>\n"):
                            self.log("⚠ Could not append Files header", "warning")
                        await tab.wait_for_timeout(5000)
                        for f in file_items:
                            await self._editor_cursor_end(tab)
                            await self._insert_file(tab, f)

                    await self._save_and_close(tab)
            finally:
                try:
                    await tab.close()
                except Exception:
                    pass

            self.log("─" * 52, "dim")
            self.log(f"✓ Text done: {html_count} pages, {link_count} links", "success")

            if self.gemini_api_key:
                await self._apply_gemini(context)

            self.log("─" * 52, "dim")
            self.log("✓ Done! Close the browser when finished.", "success")

            if self._on_complete:
                self._on_complete()

            while browser.is_connected():
                await asyncio.sleep(0.5)

        except Exception:
            if self._on_complete:
                self._on_complete()
            raise
        finally:
            if browser.is_connected():
                await browser.close()
            await p.stop()


async def run(
    unit_url: str,
    target_url: str,
    theme_name: str,
    theme_colors: dict,
    gemini_api_key: str = "",
    style_reference_html: str = "",
    parallel_pages: int = 3,
    log: Callable = None,
    on_complete: Callable = None,
    bs_username: str = "",
    bs_password: str = "",
    sso_email: str = "",
    sso_password: str = "",
    moodle_url: str = "",
    moodle_username: str = "",
    moodle_password: str = "",
) -> None:
    await UnitCollector(
        unit_url=unit_url,
        target_url=target_url,
        theme_name=theme_name,
        theme_colors=theme_colors,
        gemini_api_key=gemini_api_key,
        style_reference_html=style_reference_html,
        parallel_pages=parallel_pages,
        log=log,
        on_complete=on_complete,
        bs_username=bs_username,
        bs_password=bs_password,
        sso_email=sso_email,
        sso_password=sso_password,
        moodle_url=moodle_url,
        moodle_username=moodle_username,
        moodle_password=moodle_password,
    ).run()
