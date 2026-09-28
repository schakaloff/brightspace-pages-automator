import asyncio
import base64
import binascii
from collections import Counter
import html
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page



# Re-opening the target page straight after saving it races D2L's editor load,
# so the first read-back often comes back short or empty even though the save
# succeeded. Retry a few times before deciding the content really isn't there.
STYLE_READBACK_RETRIES = 3
STYLE_READBACK_DELAY_MS = 5000


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


def split_by_visibility(topics: list, hidden_ids: set) -> tuple:
    """Partition scraped topics into (visible, hidden) by D2L topic id.

    A topic whose id could not be read is treated as hidden. Brightspace only
    hides whole topics, so an unknown id is a topic we cannot prove is safe to
    show, and the safe default is to route it to the instructor page rather
    than publish it.
    """
    visible, hidden = [], []
    for topic in topics:
        topic_id = str(topic.get("topic_id") or "")
        is_hidden = (not topic_id) or topic_id in hidden_ids
        topic["hidden"] = is_hidden
        (hidden if is_hidden else visible).append(topic)
    return visible, hidden


def html_body_fragment(markup: str) -> str:
    """Return only the body content of a whole HTML document.

    A Brightspace topic's source is a complete document (doctype, ``<head>``
    with D2L's default font CSS, ``<body>``). Pasting several of those into one
    combined page nests documents inside each other: browsers shrug it off, but
    every parser downstream reads the first ``</html>`` as the end of the page.
    That is how a 5,700-character combined page reached Claude as 70.
    """
    if not markup or not re.search(r"<\s*(?:!doctype|html|head|body)\b", markup, re.I):
        return markup
    from bs4 import BeautifulSoup, Doctype

    soup = BeautifulSoup(markup, "html.parser")
    body = soup.find("body")
    if body is not None:
        return body.decode_contents().strip()
    for tag in soup.find_all("head"):
        tag.decompose()
    for tag in soup.find_all("html"):
        tag.unwrap()
    for item in soup.contents:
        if isinstance(item, Doctype):
            item.extract()
    return soup.decode().strip()


def course_file_link_html(url: str, label: str) -> str:
    return (
        f'<p><a href="{html.escape(url, quote=True)}">'
        f"{html.escape(str(label))}</a></p>\n"
    )


_UNIT_SOURCE_ID = "bpa-unit-description-source"


def unit_source_marker(source_html: str) -> str:
    """Keep the original unit description available for a later collector run."""
    encoded = base64.b64encode(source_html.encode("utf-8")).decode("ascii")
    return f'<template id="{_UNIT_SOURCE_ID}">{encoded}</template>'


def stored_unit_source(page_html: str) -> tuple[bool, str]:
    """Return (marker present, original HTML), rejecting damaged markers."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page_html or "", "html.parser")
    markers = soup.find_all("template", id=_UNIT_SOURCE_ID)
    if not markers:
        return False, ""
    if len(markers) != 1:
        raise ValueError("the combined page has multiple unit-description markers")
    try:
        encoded = re.sub(r"\s+", "", markers[0].decode_contents())
        original = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise ValueError("the combined page's unit-description marker is damaged") from exc
    if not original:
        raise ValueError("the combined page's unit-description marker is empty")
    return True, original


def with_unit_source_marker(page_html: str, source_html: str) -> str:
    """Replace a previous marker without changing the page's visible content."""
    without_old = re.sub(
        rf'<template\b[^>]*\bid\s*=\s*["\']{_UNIT_SOURCE_ID}["\'][^>]*>'
        rf'.*?</template\s*>',
        "", page_html or "", flags=re.I | re.S,
    )
    closing = re.search(r"</(?:body|html)\s*>", without_old, re.I)
    insert_at = closing.start() if closing else len(without_old)
    return (
        without_old[:insert_at] + "\n" + unit_source_marker(source_html)
        + "\n" + without_old[insert_at:]
    )


def is_collector_target_title(value: object) -> bool:
    """Whether a title belongs to a page generated by Unit Collector."""
    return bool(re.search(
        r"\s+[—-]\s+Combined(?:\s+\(Instructor\))?\s*$",
        str(value or ""),
        re.IGNORECASE,
    ))


def link_known_topic_references(
    source_html: str, topics: list, current_url: str = ""
) -> str:
    """Turn exact activity-name references into real Brightspace links.

    Moodle's activity-name filter makes plain text such as a video title act
    like a link at render time. That behavior is not stored in the page HTML,
    so an imported/collected page otherwise contains only a dead ``<span>``.
    Only unique, whole-text matches are linked; ambiguous titles are left alone.
    """
    if not source_html:
        return source_html

    def key(value: object) -> str:
        return " ".join(str(value or "").replace("\xa0", " ").split()).casefold()

    candidates: dict[str, list[str]] = {}
    for topic in topics:
        label_key = key(topic.get("label"))
        url = str(topic.get("url") or "").strip()
        if label_key and url and url.rstrip("/") != current_url.rstrip("/"):
            candidates.setdefault(label_key, []).append(url)
    links = {
        label: urls[0]
        for label, urls in candidates.items()
        if len(set(urls)) == 1
    }
    if not links:
        return source_html

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(source_html, "html.parser")

    # Retarget an explicit Moodle link when its visible label identifies a
    # unique Brightspace topic in this unit.
    for anchor in soup.find_all("a"):
        target = links.get(key(anchor.get_text(" ", strip=True)))
        if target:
            anchor["href"] = target

    # Recreate Moodle's activity-name auto-linking for exact plain-text nodes.
    for node in list(soup.find_all(string=True)):
        if node.parent is None or node.find_parent(
            ["a", "button", "code", "pre", "script", "style"]
        ):
            continue
        target = links.get(key(node))
        if not target:
            continue
        anchor = soup.new_tag("a", href=target)
        anchor.string = str(node)
        node.replace_with(anchor)

    return str(soup)


# A 30 MB upload routinely needs more than a minute before Brightspace
# shows its Duplicate Files prompt.
_OVERWRITE_DIALOG_TICKS = 240  # x500ms = 120s


class UnitCollector:
    def __init__(
        self,
        unit_url: str,
        target_url: str,
        theme_name: str,
        theme_colors: dict,
        claude_api_key: str = "",
        claude_model: str = "",
        style_reference_html: str = "",
        parallel_pages: int = 3,
        auto_create_target: bool = True,
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
        self._active_target = target_url
        self.theme_name = theme_name
        self.theme_colors = theme_colors
        self.claude_api_key = claude_api_key
        self.claude_model = claude_model
        self.style_reference_html = style_reference_html
        self.parallel_pages = max(1, parallel_pages)
        self.auto_create_target = auto_create_target
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
        self._topic_metadata: dict[str, dict] = {}
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
                self.log("⚠ No names scraped — Moodle session may be stale, logging in again…", "warning")
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
                await page.wait_for_timeout(1500)
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
                        if await self._dialog_still_open(page):
                            self.log(
                                "  ⚠ A Brightspace dialog would not close. Nothing "
                                "can be saved while it covers the editor.", "warning",
                            )
                        return
                except Exception:
                    pass

    async def _save_and_close(self, page: Page) -> bool:
        """Shared with Restyle — see src/editor_save.py."""
        from editor_save import save_and_close

        return await save_and_close(page, self.log)
    async def _dialog_still_open(self, page: Page) -> bool:
        """Shared with Restyle — see src/editor_save.py."""
        from editor_save import dialog_still_open

        return await dialog_still_open(page)
    async def _verify_saved(self, page: Page, target_url: str, expected_min_chars: int) -> bool:
        """Shared with Restyle — see src/editor_save.py."""
        from editor_save import verify_topic_saved

        return await verify_topic_saved(page, target_url, expected_min_chars, self.log)
    async def _scrape_topics(self, page: Page) -> List[dict]:
        self.log("Scanning unit for topic pages...", "info")
        try:
            await page.wait_for_selector("iframe", timeout=8000)
        except Exception:
            pass

        base_url = "/".join(self.unit_url.split("/")[:3])
        lesson_id = self.unit_url.rstrip("/").split("/")[-1]

        SKIP_TYPES = ["quiz", "dropbox", "discussion", "survey", "assignment", "checklist", "lti"]

        _JS = r"""([baseUrl, lessonId, skipTypes]) => {
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
                        const href = el.getAttribute('action-href') || '';
                        const idMatch = href.match(/\/topics\/(\d+)/);
                        return {
                            label: el.getAttribute('label') || el.getAttribute('drag-handle-text') || 'Untitled',
                            url: baseUrl + href,
                            topic_id: idMatch ? idMatch[1] : null,
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

    async def _fetch_hidden_topic_ids(self, page: Page) -> Optional[set]:
        """Ask D2L which topics in this unit are hidden from students.

        The list item in the DOM carries no reliable visibility flag, so the
        authoritative answer comes from the content API. Returns None when the
        answer could not be obtained — the caller must treat that as fatal
        rather than guessing, because guessing wrong publishes staff-only
        material to students.
        """
        from target_page_creator import _parse_ids

        course_id, module_id = _parse_ids(self.unit_url)
        if not course_id or not module_id:
            self.log("  ✗ Could not read course/unit id for the visibility check", "error")
            return None
        try:
            items = await page.evaluate(
                r"""async ([courseId, moduleId]) => {
                    const r = await fetch(
                        `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
                        { credentials: 'include', headers: { Accept: 'application/json' } });
                    if (!r.ok) return null;
                    const items = await r.json();
                    return items;
                }""",
                [course_id, module_id],
            )
        except Exception as e:
            self.log(f"  ✗ Visibility check failed: {e}", "error")
            return None
        if not isinstance(items, list):
            self.log("  ✗ Visibility check failed: D2L refused the structure request", "error")
            return None
        self._topic_metadata = {
            str(item.get("Id") or item.get("TopicId")): item
            for item in items
            if isinstance(item, dict) and (item.get("Id") or item.get("TopicId"))
        }
        return {
            topic_id for topic_id, item in self._topic_metadata.items()
            if item.get("IsHidden") is True
        }

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
        # Video/link pages can render the button late (embedded players keep
        # the page "loading" long after goto returns) — retry instead of a
        # single one-shot check.
        for _ in range(10):
            for ctx in [page, *page.frames]:
                try:
                    loc = ctx.locator("d2l-button.topic-jump-button, .topic-jump-button")
                    if await loc.count() > 0:
                        await loc.first.click(timeout=4000)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break
            await page.wait_for_timeout(1000)

        if not clicked:
            # Fallback: many link topics embed their destination directly
            # (YouTube player etc.) — read the URL straight off the page.
            embed_url = self._detect_embedded_media_url(page)
            if embed_url:
                self.log(f"  ✓ {label} → {embed_url} (from embedded player)", "success")
                return embed_url
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

    _MEDIA_HOSTS = ("youtube.com", "youtube-nocookie.com", "youtu.be", "vimeo.com",
                    "dailymotion.com", "kaltura.com")
    _NOISE_HOSTS = ("readspeaker", "google", "gstatic", "doubleclick", "facebook",
                    "d2l", "brightspace", "okanagancollege")

    def _detect_embedded_media_url(self, page: Page) -> Optional[str]:
        """Look through the page's embedded frames for an external player
        (YouTube, Vimeo, ...) and return its address. YouTube embed URLs are
        converted to regular watch links so readers land on the normal page."""
        external: list[str] = []
        try:
            for frame in page.frames:
                u = frame.url or ""
                if "://" not in u:
                    continue
                host = u.split("/")[2].lower()
                if any(m in host for m in self._MEDIA_HOSTS):
                    external.insert(0, u)  # media hosts take priority
                elif not any(n in host for n in self._NOISE_HOSTS):
                    external.append(u)
        except Exception:
            return None
        if not external:
            return None
        url = external[0]
        # youtube.com/embed/VIDEOID?... → youtube.com/watch?v=VIDEOID
        m = re.search(r"youtube(?:-nocookie)?\.com/embed/([\w-]+)", url)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
        return url

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
            from icon_shortcodes import replace_fontawesome_shortcodes
            html = replace_fontawesome_shortcodes(html)
            self.log(f"✓ {label} ({len(html):,} chars)", "success")
        else:
            self.log(f"✗ Could not extract HTML for {label}", "error")
        return html

    # ── TEMPORARY DEBUG (added 2026-07-31) ────────────────────────────────────
    # Topics that dead-end here (no Options button on either the edit or the
    # download path) have proven impossible to inspect afterwards — by the time
    # anyone goes looking, they are gone from the course. Capture the page state
    # at the moment of failure instead. Remove this helper and its single call
    # site in _download_file once the Kaltura dead-end is understood.
    _JS_TOPIC_DIAGNOSTICS = """() => {
        function deepTags(root, acc, depth) {
            if (!root || depth > 10) return acc;
            try {
                root.querySelectorAll('*').forEach(el => {
                    const t = el.tagName.toLowerCase();
                    if (t.startsWith('d2l-')) acc[t] = (acc[t] || 0) + 1;
                    if (el.shadowRoot) deepTags(el.shadowRoot, acc, depth + 1);
                });
            } catch (e) {}
            return acc;
        }
        const roots = [document];
        for (const f of document.querySelectorAll('iframe')) {
            try { if (f.contentDocument) roots.push(f.contentDocument); } catch (e) {}
        }
        const tags = {};
        roots.forEach(r => deepTags(r, tags, 0));
        return {
            url: location.href,
            title: document.title,
            d2lElementCounts: tags,
            iframeSrcs: [...document.querySelectorAll('iframe')]
                .map(f => (f.getAttribute('src') || '').slice(0, 200)),
            bodyText: (document.body.innerText || '').slice(0, 1500),
        };
    }"""

    async def _dump_topic_diagnostics(self, page: Page, label: str, reason: str) -> None:
        """Write a screenshot + page-state dump for a topic that could not be
        collected. Never raises — diagnostics must not break a run."""
        try:
            from config import SCREENSHOTS_DIR

            out_dir = Path(SCREENSHOTS_DIR) / "topic_diagnostics"
            out_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9]+", "_", label)[:50].strip("_") or "topic"
            base = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}"

            try:
                await page.screenshot(path=str(base) + ".png", full_page=True)
            except Exception as e:
                self.log(f"  (diagnostic screenshot failed: {e})", "dim")

            info = {"label": label, "reason": reason, "playwrightUrl": page.url}
            try:
                info["frames"] = [f.url for f in page.frames]
            except Exception:
                pass
            try:
                info.update(await page.evaluate(self._JS_TOPIC_DIAGNOSTICS))
            except Exception as e:
                info["evaluateError"] = str(e)

            (Path(str(base) + ".json")).write_text(
                json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self.log(f"  🔍 Diagnostics written to {base.name}.json/.png", "dim")
        except Exception as e:
            self.log(f"  (diagnostics failed: {e})", "dim")

    async def _download_file(
        self, page: Page, url: str, label: str, source_url: str = ""
    ) -> Optional[dict]:
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
            await self._dump_topic_diagnostics(page, label, "no-options-button")  # TEMPORARY DEBUG
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
            from collector_file_validation import matching_direct_file_url, validate_topic_download

            valid, reason = validate_topic_download(label, filename, source_url)
            if not valid:
                download_path = urlparse(dl.url).path if getattr(dl, "url", "") else ""
                path_note = f" (download path: {download_path})" if download_path else ""
                self.log(
                    f"⚠ {label}: {reason}{path_note}; keeping the original topic link",
                    "warning",
                )
                try:
                    await dl.cancel()
                except Exception:
                    pass
                return None
            topic_id = re.search(r"/topics/(\d+)", url)
            save_dir = self._dl_dir / (topic_id.group(1) if topic_id else "unknown")
            save_dir.mkdir(exist_ok=True)
            save_path = save_dir / filename
            await dl.save_as(str(save_path))
            self.log(f"✓ Downloaded for {label}: {filename}", "success")
            direct_url = matching_direct_file_url(
                filename, await self._detect_direct_file_url(page)
            )
            return {
                "label": label,
                "path": str(save_path),
                "filename": filename,
                "direct_url": direct_url,
            }
        except Exception as e:
            self.log(f"✗ Download failed for {label}: {e}", "error")
            return None

    async def _detect_direct_file_url(self, page: Page) -> str:
        """Find the permanent /content/enforced/ URL of the file shown on a
        file-topic page. Unlike the topic URL, this address lives in Manage
        Files and keeps working after the topic is deleted from the unit,
        so it makes a durable fallback link target."""
        try:
            for frame in page.frames:
                url = frame.url or ""
                if "/content/enforced/" in url:
                    return url.split("?")[0]
            src = await page.evaluate("""() => {
                function find(root, depth) {
                    if (depth > 6) return null;
                    for (const f of root.querySelectorAll('iframe')) {
                        const s = f.getAttribute('src') || '';
                        if (s.includes('/content/enforced/')) return s;
                    }
                    for (const el of root.querySelectorAll('*')) {
                        if (el.shadowRoot) {
                            const r = find(el.shadowRoot, depth + 1);
                            if (r) return r;
                        }
                    }
                    return null;
                }
                return find(document, 0);
            }""")
            if src:
                if src.startswith("/"):
                    base = "/".join(page.url.split("/")[:3])
                    src = base + src
                return src.split("?")[0]
        except Exception:
            pass
        return ""

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
            result = (body.decode_contents() if body else str(soup)).strip()
            from icon_shortcodes import replace_fontawesome_shortcodes
            return replace_fontawesome_shortcodes(result)
        except Exception as e:
            self.log(f"  ✗ Could not extract HTML from zip: {e}", "error")
            return None

    # ── Assemble + Style ──────────────────────────────────────────────────────

    def _build_combined_html(self, items: list, has_files: bool = False) -> str:
        from content_preservation import add_generated_heading
        from youtube_embed import parse_youtube_url

        parts = []
        for item in items:
            t = item.get("type")
            if t == "html" and item.get("html"):
                section = add_generated_heading(item["label"], item["html"])
                parts.append(f"{section}\n<hr/>\n")
            elif t == "link" and item.get("link_url"):
                label = item["label"].replace("<", "&lt;").replace(">", "&gt;")
                video = parse_youtube_url(item["link_url"])
                if video:
                    url = html.escape(item["link_url"], quote=True)
                    parts.append(
                        f'<h2>{label}</h2>\n<p><a href="{url}">{url}</a></p>\n'
                    )
                else:
                    parts.append(
                        f'<p><strong>{label}:</strong> '
                        f'<a href="{item["link_url"]}">{item["link_url"]}</a></p>\n'
                    )
        if has_files:
            parts.append("<h2>Files</h2>\n<p></p>\n")
        return "\n".join(parts)

    async def _read_editor_full_text(self, page: Page) -> str:
        """Select-all + copy + read clipboard. Unlike textContent, this reads the real
        CM6 doc model rather than only the virtualized (on-screen) viewport."""
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
            return ""
        await page.wait_for_timeout(200)
        await page.keyboard.press("Control+a")
        await page.wait_for_timeout(150)
        await page.keyboard.press("Control+c")
        await page.wait_for_timeout(400)
        return await page.evaluate("navigator.clipboard.readText()")

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

        expected_len = len(html)
        for attempt in range(3):
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
                await page.wait_for_timeout(1500)

                cm_len = len(await self._read_editor_full_text(page))
            if cm_len >= expected_len * 0.9:
                self.log("✓ HTML pasted", "success")
                await page.wait_for_timeout(1500)
                return True
            self.log(f"⚠ Paste verify failed (editor has {cm_len} chars, expected ~{expected_len}) — retrying", "warning")
            await page.wait_for_timeout(800)

        self.log("✗ Paste never landed in editor — aborting save to avoid overwriting with stale content", "error")
        return False

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
                await self._close_any_dialog(page)
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
                await self._close_any_dialog(page)
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
                await self._close_any_dialog(page)
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

            # Brightspace sometimes answers a perfectly good upload with
            # "You must select at least one file" while the file is plainly
            # listed in the dialog. Clicking Upload again clears it. Only retry
            # while a file really is attached, so a genuinely empty dialog is
            # not clicked forever.
            _JS_SPURIOUS_FILE_ERROR = """() => {
                const body = document.body ? document.body.innerText : '';
                if (!/must select at least one file/i.test(body)) return false;
                const files = document.querySelectorAll(
                    '.d2l-fileinput-filelist li:not(.d2l-fileinput-placeholder)');
                return files.length > 0;
            }"""
            for retry in range(3):
                await page.wait_for_timeout(1500)
                spurious = False
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        if await frame.evaluate(_JS_SPURIOUS_FILE_ERROR):
                            spurious = True
                            break
                    except Exception:
                        pass
                if not spurious:
                    break
                clicked_again = False
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        if await frame.evaluate(_JS_CLICK_FOOTER_UPLOAD):
                            clicked_again = True
                            break
                    except Exception:
                        pass
                if not clicked_again:
                    break
                self.log(
                    f"  ↻ \"must select a file\" but {file_item['filename']} is "
                    f"attached — clicked Upload again ({retry + 1}/3)", "info",
                )

            # Step 5a: After clicking Upload in an error state, Brightspace may show
            # an intermediate screen with an "Insert" button before the overwrite dialog.
            # That screen also carries the Link Text / Alternate Text fields, and clicking
            # Insert here can be the final submission (dialog closes with no overwrite
            # conflict) — so both fields must be filled before this click, not after, or
            # they never make it in for files that don't hit an overwrite. Brightspace now
            # requires both — leaving Alternate Text blank fails validation with "Specify a
            # text alternative for non-decorative items", which leaves this dialog open and
            # its backdrop shim blocking every click after it (including Save and Close).
            # D2L ignores synthetic input/change events on these fields (Lit property
            # observers only react to real DOM keyboard events), so each must be set via
            # a real click + Playwright keyboard.type(), not JS value-setter + dispatchEvent
            # — and the type must fully land (verified by reading the value back) before
            # Insert is clicked, or a slow/laggy render can submit the old value.
            #
            # Fields are located by their <label for="..."> text ("Link Text", "Alternate
            # Text"), not a hard-coded id — both fields share the exact same class/type, so
            # an id-less selector matches both and silently fills only the first (this was
            # the actual bug: link text got set, alt text never did). Label text is what a
            # person reads and stays stable even if Brightspace's generated ids shift.
            _JS_RESOLVE_FIELD = """(labelText) => {
                for (const label of document.querySelectorAll('label')) {
                    if (label.textContent.trim() === labelText) {
                        const id = label.getAttribute('for');
                        const el = id ? document.getElementById(id) : null;
                        if (!el) return null;
                        return { id, visible: el.offsetParent !== null, value: el.value };
                    }
                }
                return null;
            }"""

            async def _fill_labeled_field(frame, label_text: str, value: str) -> bool:
                try:
                    info = await frame.evaluate(_JS_RESOLVE_FIELD, label_text)
                except Exception:
                    return False
                if not info or not info.get("visible") or not info.get("id"):
                    return False
                # Already correct — never type into it a second time. Without this,
                # a retry for the *other* field re-types this one and (if the
                # select-all doesn't land) appends, producing repeated link text.
                if info.get("value") == value:
                    return True
                loc = frame.locator(f'#{info["id"]}')
                await loc.click()
                await loc.press("Control+A")
                await loc.press("Delete")
                await page.keyboard.type(value, delay=20)
                await page.wait_for_timeout(200)
                after = await frame.evaluate(_JS_RESOLVE_FIELD, label_text)
                return bool(after and after.get("value") == value)

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
            corrected = file_item.get("corrected_name")
            raw_name = corrected or file_item["filename"]
            display_name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", raw_name)
            clicked_insert_on_error = False
            filled_before_error_click = False
            # Tracked across attempts so a field that already succeeded is never
            # re-typed because the other one is still failing.
            link_ok = False
            alt_ok = False
            for _ in range(10):
                await page.wait_for_timeout(500)

                if not filled_before_error_click:
                    for frame in page.frames:
                        if frame == page.main_frame:
                            continue
                        try:
                            if not link_ok:
                                link_ok = await _fill_labeled_field(frame, "Link Text", display_name)
                            if not alt_ok:
                                alt_ok = await _fill_labeled_field(frame, "Alternate Text", display_name)
                            if link_ok and alt_ok:
                                filled_before_error_click = True
                                self.log(f"  ✓ Set link text + alt text: {display_name}", "dim")
                                break
                        except Exception:
                            pass

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
                    break

            # If the error-Insert click above already closed the whole dialog, there
            # was no overwrite conflict — link text (if any) was already filled before
            # that click, so this is done.
            if clicked_insert_on_error:
                try:
                    if await page.locator('iframe[title="Insert Stuff"], iframe.d2l-dialog-frame').count() == 0:
                        self.log(f"  ✓ Inserted (via error-Insert): {file_item['filename']}", "success")
                        return True
                except Exception:
                    pass

            # Step 5b: Handle overwrite dialog if the file already exists
            # "Overwrite the existing file" + Save. The dialog only appears once
            # Brightspace has finished transferring the file, so a large upload
            # can take well over a minute to reach this point — waiting 5s here
            # used to move on while the transfer was still running.
            _JS_OVERWRITE = """() => {
                const ul = document.getElementById('SelectedOverwriteOption');
                if (!ul) return 'absent';
                // Pick Overwrite by value, falling back to the label wording so a
                // renumbered radio cannot silently leave "Create a new file" set.
                let picked = false;
                for (const r of ul.querySelectorAll('input[type="radio"]')) {
                    const label = (r.closest('li') || r.parentElement);
                    const text = ((label && label.textContent) || '').toLowerCase();
                    if (r.value === '2' || text.includes('overwrite')) {
                        if (!r.checked) r.click();
                        picked = true;
                        break;
                    }
                }
                if (!picked) return 'no-overwrite-option';
                const footer = document.querySelector('.d2l-dialog-footer');
                if (!footer) return 'no-footer';
                for (const b of footer.querySelectorAll('button')) {
                    if (b.offsetParent === null) continue;
                    if (b.hasAttribute('primary') || b.textContent.trim() === 'Save') {
                        b.click(); return 'saved';
                    }
                }
                return 'no-save-button';
            }"""
            overwrite_state = "absent"
            for _ in range(_OVERWRITE_DIALOG_TICKS):
                await page.wait_for_timeout(500)
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        state = await frame.evaluate(_JS_OVERWRITE)
                    except Exception:
                        continue
                    if state and state != "absent":
                        overwrite_state = state
                        break
                if overwrite_state != "absent":
                    break

            if overwrite_state == "saved":
                self.log("  ↩ Overwrite dialog: chose 'Overwrite the existing file' and saved", "info")
            elif overwrite_state != "absent":
                # Previously this path still logged success and carried on, leaving
                # the dialog open to block Save and Close later on.
                self.log(
                    f"  ⚠ Overwrite dialog appeared but could not be completed "
                    f"({overwrite_state})", "warning",
                )

            # Step 5c: Fill Link Text + Alternate Text if they weren't already filled in
            # Step 5a (these fields only render on the overwrite path once 5a/5b have
            # resolved). Same real-keystroke approach as 5a — synthetic events don't
            # commit for these fields.
            if not filled_before_error_click:
                filled = False
                link_ok = False
                alt_ok = False
                for _ in range(10):
                    for frame in page.frames:
                        if frame == page.main_frame:
                            continue
                        try:
                            if not link_ok:
                                link_ok = await _fill_labeled_field(frame, "Link Text", display_name)
                            if not alt_ok:
                                alt_ok = await _fill_labeled_field(frame, "Alternate Text", display_name)
                            if link_ok and alt_ok:
                                filled = True
                                break
                        except Exception:
                            pass
                    if filled:
                        break
                    await page.wait_for_timeout(400)
                if filled:
                    self.log(f"  ✓ Set link text + alt text: {display_name}", "dim")
                else:
                    self.log(f"  ⚠ Link/alt text fields not found — left as default", "dim")

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
            # Leaving the Insert Stuff dialog open blocks every later click,
            # including Save and Close — always clear it before giving up.
            try:
                await self._close_any_dialog(page)
            except Exception:
                pass
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

        len_before = len(await self._read_editor_full_text(page))
        pasted_ok = False
        for attempt in range(3):
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

                len_after = len(await self._read_editor_full_text(page))
            if len_after >= len_before + len(section_html) * 0.9:
                pasted_ok = True
                break
            self.log(f"⚠ Append verify failed (editor grew by {len_after - len_before} chars, expected ~{len(section_html)}) — retrying", "warning")
            await page.wait_for_timeout(800)

        if not pasted_ok:
            self.log("✗ Append never landed in editor — aborting to avoid overwriting with stale content", "error")
            return False

        return await self._close_source_dialog(page)

    async def _scrape_topic(self, context, topic: dict, semaphore: asyncio.Semaphore) -> dict:
        """Scrape one topic and return its content. Runs under semaphore for HTML/file types.
        Link types use _link_lock instead to avoid the new-tab race condition."""
        label = topic["label"]
        t = topic.get("type", "html")
        source_url = str(self._topic_metadata.get(str(topic.get("topic_id")), {}).get("Url") or "")
        result: dict = {"topic": topic, "html": None, "link_url": None, "file": None}

        from collector_file_validation import direct_slide_url, external_topic_url

        external_url = external_topic_url(topic["url"], source_url)
        if external_url:
            result["link_url"] = external_url
            self.log(f"  ✓ External topic destination: {label}", "success")
            return result

        slide_url = direct_slide_url(topic["url"], source_url, label)
        if slide_url:
            result["file"] = {
                "filename": Path(urlparse(slide_url).path).name,
                "direct_url": slide_url,
            }
            self.log(f"  ✓ Slide already in course files: {label}", "success")
            return result

        # Brightspace can present an HTML slide deck as a topic while its
        # Download action offers a supporting asset (for example layout.css).
        # The original topic is the reliable, student-accessible slide entry;
        # keep it under the source unit's Lecture Slides heading instead of
        # downloading or trying to inline its page-specific CSS and scripts.
        if t == "html" and re.match(r"^slides?\s*:", label, re.I):
            result["link_url"] = topic["url"]
            result["slide_topic"] = True
            self.log(f"  ✓ Kept Brightspace slide topic: {label}", "success")
            return result

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
                        fd = await self._download_file(tab, topic["url"], label, source_url)
                    else:
                        html = await self._collect_html(tab, topic["url"], label)
                        if html is not None:
                            result["html"] = html
                            return result
                        # HTML collection failed → treat as file
                        fd = await self._download_file(tab, topic["url"], label, source_url)

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
            if not await self._navigate_to_edit(page, self._active_target):
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

    async def _read_back_for_styling(self, page: Page, expected_min_chars: int) -> Optional[str]:
        """Re-open the saved target page and read its HTML back out of the
        source dialog. Returns None when this attempt raced the editor's own
        load — a not-yet-populated editor looks exactly like an empty page, and
        styling that would overwrite the real content with nothing."""
        if not await self._navigate_to_edit(page, self._active_target):
            self.log("  ✗ Could not reopen target page", "warning")
            return None
        if not await self._open_source_code(page):
            self.log("  ✗ Source Code not found", "warning")
            return None

        html = await self._extract_html(page)
        if not html:
            self.log("  ✗ Could not extract assembled HTML", "warning")
            return None

        if expected_min_chars and len(html) < expected_min_chars * 0.5:
            self.log(
                f"  ✗ Re-opened page only had {len(html):,} chars, expected "
                f"~{expected_min_chars:,}. If this repeats with the same number, "
                "the page is empty and the save was lost — not a slow editor.",
                "warning",
            )
            return None
        return html

    async def _apply_claude_style(
        self, context, expected_min_chars: int = 0,
        required_section_links: Optional[list[tuple[str, str]]] = None,
    ) -> bool:
        if not self.claude_api_key:
            self.log("⚠ No Claude API key — skipping styling step", "warning")
            return False

        self.log("─" * 52, "dim")
        self.log("Applying Claude styling to assembled page...", "info")

        page = await context.new_page()
        try:
            try:
                await page.goto(
                    self._active_target, wait_until="domcontentloaded", timeout=30000
                )
            except Exception:
                pass

            from editor_save import read_topic_html, replace_topic_html
            try:
                source_html = await read_topic_html(page, self._active_target)
            except Exception as exc:
                self.log(
                    f"✗ Could not read the assembled page for styling ({exc})",
                    "error",
                )
                return False
            if expected_min_chars and len(source_html) < expected_min_chars * 0.5:
                self.log(
                    f"✗ Saved page only had {len(source_html):,} chars before styling; "
                    f"expected ~{expected_min_chars:,}.",
                    "error",
                )
                return False

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
                self.log("✗ Claude returned nothing", "error")
                return False

            from collector_section_check import misplaced_section_links

            moved = misplaced_section_links(styled_html, required_section_links or [])
            if moved:
                self.log(
                    f"✗ Styling moved {len(moved)} resource(s) out of their "
                    "Lecture Slides/Recordings section. The assembled page was kept.",
                    "error",
                )
                for item in moved[:5]:
                    self.log(f"  {item}", "detail")
                return False

            try:
                from accessibility_checker import log_report, scan_html

                report = await scan_html(context, styled_html)
                log_report(report, self.log, label="Combined page")
            except Exception as exc:
                self.log(f"♿ Accessibility check unavailable: {exc}", "warning")

            return await replace_topic_html(
                page, self._active_target, styled_html, self.log
            )
        finally:
            await page.close()

    async def _apply_youtube_transforms(self, context, expected_min_chars: int = 0) -> bool:
        """Read, transform, and verify the assembled page through the D2L API."""
        page = await context.new_page()
        try:
            try:
                await page.goto(
                    self._active_target, wait_until="domcontentloaded", timeout=30000
                )
            except Exception:
                pass

            from editor_save import read_topic_html, replace_topic_html
            try:
                source_html = await read_topic_html(page, self._active_target)
            except Exception as exc:
                self.log(
                    "✗ Could not verify the assembled page before YouTube conversion; "
                    f"original links were left unchanged ({exc}).",
                    "error",
                )
                return False

            if expected_min_chars and len(source_html) < expected_min_chars * 0.5:
                self.log(
                    f"✗ Saved page only had {len(source_html):,} chars before YouTube "
                    f"conversion; expected ~{expected_min_chars:,}.",
                    "error",
                )
                return False

            from youtube_embed import transform_standalone_youtube_urls

            transformed = transform_standalone_youtube_urls(source_html)
            if not transformed.changed:
                return True
            self.log(
                f"▶ YouTube: creating {transformed.embeds_created} player(s), "
                f"removing {transformed.redundant_urls_removed} redundant raw URL(s)",
                "info",
            )
            if not await replace_topic_html(
                page, self._active_target, transformed.html, self.log
            ):
                self.log(
                    "✗ YouTube conversion failed read-back verification; original links "
                    "may still be present.",
                    "error",
                )
                return False
            return True
        finally:
            await page.close()

    # ── Main run ──────────────────────────────────────────────────────────────

    _JS_COURSE_FILE_EXISTS = """async (path) => {
        try {
            const r = await fetch(path, { method: 'HEAD', credentials: 'include', cache: 'no-store' });
            return r.ok;
        } catch (e) { return false; }
    }"""

    async def _course_root(self, page: Page, course_id: str) -> str:
        """The course's Manage Files folder, e.g. ``/content/enforced/24877-X/``."""
        try:
            course = await page.evaluate(
                """async (id) => {
                    const r = await fetch(`/d2l/api/lp/1.49/courses/${id}`,
                        { credentials: 'include', headers: { Accept: 'application/json' } });
                    return r.ok ? await r.json() : null;
                }""",
                str(course_id),
            )
        except Exception:
            return ""
        path = str((course or {}).get("Path") or "")
        if not re.fullmatch(r"/content/enforced/[^/]+/?", path):
            return ""
        return path.rstrip("/") + "/"

    async def _upload_files(
        self, tab: Page, target_url: str, file_items: list
    ) -> tuple[str, int, int]:
        """Upload files through Insert Stuff and return link HTML for them.

        Insert Stuff is used only as an uploader: it puts each file into the
        course's Manage Files the moment Upload is clicked. The editor session
        is then abandoned, and the caller writes the links through the content
        API. A link is only made once the uploaded file is proven to exist.

        Returns ``(links_html, fallback_link_count, unresolved_count)``.
        """
        from editor_save import _topic_ids
        from urllib.parse import quote

        ids = _topic_ids(target_url)
        course_root = await self._course_root(tab, ids[0]) if ids else ""
        if not course_root:
            self.log(
                "⚠ Could not read this course's file folder — files will be linked "
                "to their original topics instead of uploaded.", "warning",
            )

        editor_ready = False
        if course_root:
            editor_ready = await self._navigate_to_edit(tab, target_url)
            if not editor_ready:
                self.log("✗ Could not open the page editor to upload files", "error")

        parts: list[str] = []
        fallback_links = unresolved = 0
        if editor_ready:
            self.log(f"Uploading {len(file_items)} file(s)...", "info")
            await tab.wait_for_timeout(3000)
        for f in file_items:
            label = f.get("corrected_name") or f.get("topic_label") or f.get("filename", "File")
            if editor_ready:
                await self._editor_cursor_end(tab)
                if await self._insert_file(tab, f):
                    uploaded_url = course_root + quote(f["filename"])
                    if await tab.evaluate(self._JS_COURSE_FILE_EXISTS, uploaded_url):
                        parts.append(course_file_link_html(uploaded_url, label))
                        continue
                    self.log(
                        f"  ⚠ {f['filename']} was not found in Manage Files after "
                        "uploading.", "warning",
                    )
            # Fallback: prefer the file's permanent Manage Files address
            # (survives topic deletion); otherwise link to the topic itself.
            fallback_url = f.get("direct_url") or f.get("topic_url") or ""
            unresolved += 1
            if not fallback_url:
                self.log(
                    f"  ✗ Could not upload {f['filename']} and there is no original "
                    "URL to link to.", "error",
                )
                continue
            parts.append(course_file_link_html(fallback_url, label))
            fallback_links += 1
            self.log(
                f"  ⚠ Linked {f['filename']} to its original file instead of "
                "uploading it.", "warning",
            )
        return "".join(parts), fallback_links, unresolved

    async def _collect_into(
        self, context, topics: list, target_url: str, unit_description_html: str = ""
    ) -> bool:
        """Scrape *topics*, assemble them into *target_url*, then transform it.

        Split out of run() so the same pipeline can fill either the student
        page or the hidden instructor page. Returns False on the partial
        completions the caller reports as a unit failure.
        """
        self._active_target = target_url
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
        html_count = link_count = file_count = file_link_count = 0
        unresolved_count = 0
        required_section_links: list[tuple[str, str]] = []
        current_resource_heading = ""

        # Insert Stuff uses the original filename in course Manage Files. Two
        # different topics with that name would overwrite each other, so keep
        # both original topic links instead of uploading either one.
        file_name_counts = Counter(
            result["file"]["filename"].casefold()
            for result in results
            if isinstance(result, dict) and result.get("file")
        )

        from content_preservation import add_generated_heading
        from youtube_embed import parse_youtube_url

        if unit_description_html:
            linked_intro = link_known_topic_references(
                html_body_fragment(unit_description_html), topics
            )
            sections.append(add_generated_heading("Overview", linked_intro) + "\n<hr/>\n")
            self.log("  + Unit description (Overview)", "dim")

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.log(f"✗ Topic {i + 1} scrape failed: {result}", "error")
                topic = topics[i]
                safe_url = html.escape(topic["url"], quote=True)
                sections.append(
                    f'<p><a href="{safe_url}">{html.escape(topic["label"])}</a></p>\n'
                )
                if current_resource_heading:
                    required_section_links.append((current_resource_heading, topic["url"]))
                link_count += 1
                unresolved_count += 1
                continue
            topic = result["topic"]
            safe = html.escape(topic["label"])

            if result["html"]:
                from bs4 import BeautifulSoup

                linked_html = link_known_topic_references(
                    html_body_fragment(result["html"]), topics, topic.get("url", "")
                )
                section = add_generated_heading(topic["label"], linked_html)
                current_resource_heading = (
                    topic["label"] if re.search(r"\b(slides?|recordings?)\b", topic["label"], re.I)
                    else ""
                )
                parsed = BeautifulSoup(linked_html, "html.parser")
                has_authored_content = bool(
                    parsed.get_text(" ", strip=True)
                    or parsed.find(["a", "img", "iframe", "video", "audio", "object", "embed", "script"])
                )
                sections.append(f"{section}\n" + ("<hr/>\n" if has_authored_content else ""))
                html_count += 1
            elif result["link_url"]:
                corrected = self._name_matcher(topic["label"])
                link_label = html.escape(corrected or topic["label"])
                video = parse_youtube_url(result["link_url"])
                if result.get("slide_topic"):
                    safe_url = html.escape(result["link_url"], quote=True)
                    sections.append(f'<p><a href="{safe_url}">{link_label}</a></p>\n')
                elif video:
                    url = html.escape(result["link_url"], quote=True)
                    sections.append(
                        f'<h2>{link_label}</h2>\n'
                        f'<p><a href="{url}">{url}</a></p>\n<hr/>\n'
                    )
                else:
                    safe_url = html.escape(result["link_url"], quote=True)
                    sections.append(
                        f'<p><strong>{link_label}:</strong> '
                        f'<a href="{safe_url}">{safe_url}</a></p>\n'
                    )
                if current_resource_heading:
                    required_section_links.append((current_resource_heading, result["link_url"]))
                link_count += 1
            elif result["file"]:
                # Keep both fallback link targets for when Insert Stuff can't
                # embed the file: direct_url (permanent Manage Files address,
                # set during download) and topic_url (dies if topic deleted).
                fi = result["file"]
                if re.search(r"\bslides?\b", current_resource_heading, re.I) or not fi.get("path"):
                    # A slide already hosted in this Brightspace course should
                    # stay under Lecture Slides. Re-uploading moves it to the
                    # generic Files area and risks filename collisions.
                    slide_url = fi.get("direct_url") or topic["url"]
                    sections.append(
                        f'<p><a href="{html.escape(slide_url, quote=True)}">'
                        f'{html.escape(topic["label"])}</a></p>\n'
                    )
                    if current_resource_heading:
                        required_section_links.append((current_resource_heading, slide_url))
                    link_count += 1
                    self.log(f"  ↳ Kept slide link in place: {topic['label']}", "info")
                    continue
                if file_name_counts[fi["filename"].casefold()] > 1:
                    self.log(
                        f"⚠ '{fi['filename']}' is shared by multiple topics; "
                        f"keeping the original link for '{topic['label']}'",
                        "warning",
                    )
                    safe_url = html.escape(topic["url"], quote=True)
                    sections.append(f'<p><a href="{safe_url}">{html.escape(topic["label"])}</a></p>\n')
                    if current_resource_heading:
                        required_section_links.append((current_resource_heading, topic["url"]))
                    link_count += 1
                    unresolved_count += 1
                    continue
                fi.setdefault("topic_url", topic.get("url", ""))
                fi.setdefault("topic_label", topic["label"])
                file_items.append(fi)
                file_count += 1
            else:
                # Nothing could be collected — never drop a topic silently:
                # link to the original topic so its content stays reachable.
                self.log(
                    f"⚠ Nothing collected for '{topic['label']}' — "
                    "keeping a link to the original topic for review.",
                    "warning",
                )
                safe_url = html.escape(topic["url"], quote=True)
                sections.append(
                    f'<p><a href="{safe_url}">{safe}</a></p>\n'
                )
                if current_resource_heading:
                    required_section_links.append((current_resource_heading, topic["url"]))
                link_count += 1
                unresolved_count += 1

        # ── Phase 3: persist the assembled HTML through the content API ───
        # Save and Close in D2L's visual editor can report success while keeping
        # the blank creation stub. Text and links do not need the visual editor,
        # so write them directly and prove they survived before doing anything
        # else. The editor is opened only when files need Insert Stuff.
        assembled_html = "".join(sections)
        if file_items:
            assembled_html += "<h2>Files</h2>\n<p></p>\n"
        if not assembled_html.strip():
            self.log("✗ Nothing was collected for this page — leaving it blank", "error")
            return False

        tab = await context.new_page()
        try:
            try:
                await tab.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            from editor_save import replace_topic_html

            # Text first, on its own, so it is safe even if uploading goes wrong.
            if not await replace_topic_html(tab, target_url, assembled_html, self.log):
                return False

            if file_items:
                files_html, file_link_count, file_unresolved = await self._upload_files(
                    tab, target_url, file_items
                )
                unresolved_count += file_unresolved
                assembled_html = assembled_html.replace(
                    "<h2>Files</h2>\n<p></p>\n",
                    "<h2>Files</h2>\n" + files_html if files_html else "",
                    1,
                )
                # The editor that uploaded the files is abandoned, never saved:
                # its Save and Close has repeatedly reported success while
                # discarding everything inserted in that session. The links go
                # in through the same verified API write as the text.
                if not await replace_topic_html(tab, target_url, assembled_html, self.log):
                    self.log(
                        "✗ The files were uploaded, but their links could not be "
                        "saved to the page.", "error",
                    )
                    return False
        finally:
            try:
                await tab.close()
            except Exception:
                pass

        self.log("─" * 52, "dim")
        file_link_note = f", {file_link_count} file link(s)" if file_link_count else ""
        self.log(
            f"{'Text assembled' if unresolved_count else '✓ Text done'}: "
            f"{html_count} pages, {link_count} links{file_link_note}",
            "info" if unresolved_count else "success",
        )
        if unresolved_count:
            self.log(
                f"⚠ {unresolved_count} topic(s) need review; the combined page "
                "keeps links to their original topics.", "warning",
            )

        assembled_chars = len(assembled_html)
        if not await self._apply_youtube_transforms(
            context, expected_min_chars=assembled_chars
        ):
            self.log(
                "✗ Unit is only partially complete: collected content was saved, "
                "but YouTube conversion was not applied.",
                "error",
            )
            return False

        if self.claude_api_key:
            if not await self._apply_claude_style(
                context, expected_min_chars=assembled_chars,
                required_section_links=required_section_links,
            ):
                self.log(
                    "✗ Unit is only partially complete: text was saved, but "
                    "the requested styling was not applied.",
                    "error",
                )
                return False

        if unresolved_count:
            self.log(
                f"✗ Combined page needs review: {unresolved_count} topic(s) "
                "could not be copied as content or files.", "error",
            )
            return False
        return True

    async def _finish_unit_description_transfer(
        self, page, original_module: dict, source_html: str,
        clear_source: bool = True,
    ) -> bool:
        """Clear the section only after its combined page is verified and reusable."""
        from content_preservation import content_is_equivalent, content_is_preserved
        from editor_save import read_topic_html, replace_topic_html, _topic_ids
        from unit_overview import (
            BrowserContentAPI, _module_metadata_matches,
            extract_description_html, has_meaningful_unit_content,
        )
        from target_page_creator import _parse_ids

        course_id, module_id = _parse_ids(self.unit_url)
        target_ids = _topic_ids(self.target_url)
        if not course_id or not module_id or not target_ids or target_ids[0] != course_id:
            self.log("✗ Could not verify the target belongs to this unit; section text was kept", "error")
            return False
        api = BrowserContentAPI(page, course_id, module_id)
        try:
            target = await api.get_topic(target_ids[1])
            if str(target.get("ParentModuleId")) != str(module_id):
                raise ValueError("the combined page is outside the source unit")
            combined = await read_topic_html(page, self.target_url)
            preserved, reason = content_is_preserved(
                source_html, combined, allow_label_colons=True
            )
            if not preserved:
                raise ValueError(f"combined page is missing section content: {reason}")

            if clear_source:
                current_module = await api.get_module()
                metadata_ok, metadata_reason = _module_metadata_matches(original_module, current_module)
                unchanged, description_reason = content_is_equivalent(
                    source_html, extract_description_html(current_module)
                )
                if not metadata_ok or not unchanged:
                    raise ValueError(metadata_reason or f"section changed during collection: {description_reason}")

            marked = with_unit_source_marker(combined, source_html)
            if not await replace_topic_html(page, self.target_url, marked, self.log):
                raise ValueError("could not save the reusable copy in the combined page")
            saved = await read_topic_html(page, self.target_url)
            marker_present, stored = stored_unit_source(saved)
            if not marker_present or stored != source_html:
                raise ValueError("Brightspace did not retain the reusable copy")

            if clear_source:
                await api.replace_module_description(current_module, "")
                cleared = await api.get_module()
                metadata_ok, metadata_reason = _module_metadata_matches(original_module, cleared)
                if not metadata_ok or has_meaningful_unit_content(extract_description_html(cleared)):
                    raise ValueError(metadata_reason or "section description was not cleared")
        except Exception as exc:
            self.log(f"✗ Could not verify section text in the combined page: {exc}", "error")
            # A failed update can still have cleared the section. Restore its
            # original HTML if the read-back is ambiguous or metadata changed.
            if clear_source:
                try:
                    actual = await api.get_module()
                    if not has_meaningful_unit_content(extract_description_html(actual)):
                        await api.replace_module_description(original_module, source_html)
                        restored = await api.get_module()
                        same, reason = content_is_equivalent(
                            source_html, extract_description_html(restored)
                        )
                        if not same:
                            self.log(f"✗ Section restoration could not be verified: {reason}", "error")
                except Exception as restore_exc:
                    self.log(f"✗ Section restoration could not be verified: {restore_exc}", "error")
            return False
        if clear_source:
            self.log("✓ Section text moved into the styled combined page", "success")
        else:
            self.log("✓ Section text retained for future collector runs", "success")
        return True

    async def _collect_hidden_topics(self, context, page, hidden_topics: list) -> bool:
        """Assemble staff-only topics into their own page, hidden from students.

        The page is hidden *before* any content goes into it, so it is never
        briefly visible while holding staff material, and the flag is read back
        from D2L afterwards. If hiding cannot be confirmed the page is left
        empty and the unit fails — an instructor page that quietly stayed
        visible is the exact failure this feature exists to prevent.
        """
        from target_page_creator import create_target_page, _parse_ids
        from unit_overview import BrowserContentAPI

        self.log("─" * 52, "dim")
        course_id, module_id = _parse_ids(self.unit_url)
        if not course_id or not module_id:
            self.log("✗ Could not read course/unit id for the instructor page", "error")
            return False

        instructor_url = await create_target_page(
            page, self.unit_url, log=self.log, title_suffix="— Combined (Instructor)"
        )
        if not instructor_url:
            self.log(
                "✗ Could not create the instructor page — the hidden topics were "
                "NOT collected and remain only in their original topics.", "error",
            )
            return False

        match = re.search(r"/topics/(\d+)", instructor_url)
        if not match:
            self.log(f"✗ Could not read the topic id from {instructor_url}", "error")
            return False
        topic_id = match.group(1)

        api = BrowserContentAPI(page, course_id, module_id)
        try:
            await api.set_topic_hidden(topic_id, True)
        except Exception as e:
            self.log(
                f"✗ Could not hide the instructor page ({e}). It was left empty so "
                "no staff-only content is exposed; delete it and re-run.", "error",
            )
            return False

        try:
            confirmed = bool((await api.get_topic(topic_id)).get("IsHidden", False))
        except Exception as e:
            self.log(f"✗ Could not confirm the instructor page is hidden ({e})", "error")
            return False
        if not confirmed:
            self.log(
                "✗ Brightspace still reports the instructor page as visible. Leaving "
                "it empty rather than filling a page students can read.", "error",
            )
            return False
        self.log(f"🔒 Instructor page created and hidden: {instructor_url}", "success")

        if not await self._collect_into(context, hidden_topics, instructor_url):
            return False

        try:
            still_hidden = bool((await api.get_topic(topic_id)).get("IsHidden", False))
        except Exception:
            still_hidden = False
        if not still_hidden:
            self.log(
                "✗ The instructor page became visible while it was being filled — "
                "hide it by hand in Brightspace now.", "error",
            )
            return False

        self.log(
            f"✓ {len(hidden_topics)} hidden topic(s) collected into the instructor "
            "page, still hidden from students.", "success",
        )
        return True

    async def run(
        self, context: Optional[BrowserContext] = None,
        page: Optional[Page] = None,
        cleanup_only: bool = False,
    ) -> bool:
        """Run this unit. Returns True on a normal finish, False for the two
        known dead-ends (no target page, no topics found).

        If context/page are omitted, launches and owns its own browser exactly
        as before: logs in, and — on every non-exception exit — waits for the
        human to manually close that browser window before returning (that
        pause is deliberate single-run review UX, see the hang loops below).

        If context/page ARE supplied, this call is a participant in someone
        else's browser session: it skips launching, logging in, the
        wait-for-close pauses, and closing the browser/stopping Playwright —
        the caller owns all of that, once, for as many run() calls as it makes.
        """
        from browser import launch_browser, wait_for_login

        external = context is not None and page is not None
        if external:
            p = None
            browser = None
        else:
            p, browser, context, page = await launch_browser()
        try:
            if not external:
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

            if cleanup_only:
                from editor_save import _topic_ids
                from target_page_creator import _parse_ids
                from unit_overview import (
                    BrowserContentAPI, extract_description_html,
                    has_meaningful_unit_content,
                )

                def cleanup_result(ok: bool) -> bool:
                    if self._on_complete:
                        self._on_complete()
                    return ok

                course_id, module_id = _parse_ids(self.unit_url)
                target_ids = _topic_ids(self.target_url)
                if not course_id or not module_id or not target_ids or target_ids[0] != course_id:
                    self.log("✗ Paste the existing combined page URL to clear its section duplicate", "error")
                    return cleanup_result(False)
                api = BrowserContentAPI(page, course_id, module_id)
                try:
                    target = await api.get_topic(target_ids[1])
                    if not is_collector_target_title(target.get("Title")):
                        raise ValueError("the target is not a Collector combined page")
                    original_module = await api.get_module()
                    source_html = extract_description_html(original_module)
                except Exception as exc:
                    self.log(f"✗ Could not verify the existing combined page: {exc}", "error")
                    return cleanup_result(False)
                if not has_meaningful_unit_content(source_html):
                    self.log("✓ Done! The original section text is already clear.", "success")
                    return cleanup_result(True)
                self.log("Verifying the styled page before clearing duplicate section text...", "info")
                if not await self._finish_unit_description_transfer(
                    page, original_module, source_html
                ):
                    return cleanup_result(False)
                self.log("✓ Done! Duplicate section text cleared.", "success")
                return cleanup_result(True)

            # ── Auto-create target page (optional, self-contained feature) ────
            # If no target URL was given and auto-create is on, make a blank page
            # in this same unit via the D2L API, then proceed exactly as if the
            # user had pasted its URL. Everything below is untouched. To remove
            # this feature, delete this block + src/target_page_creator.py.
            if not self.target_url:
                if self.auto_create_target:
                    from target_page_creator import create_target_page
                    self.target_url = await create_target_page(
                        page, self.unit_url, log=self.log
                    )
                if not self.target_url:
                    self.log(
                        "✗ No target page. Paste a Target Page URL (or enable "
                        "auto-create) and re-run.", "error"
                    )
                    if self._on_complete:
                        self._on_complete()
                    if not external:
                        while browser.is_connected():
                            await asyncio.sleep(0.5)
                    return False

            topics = await self._scrape_topics(page)
            # Never collect the current target or a failed target left behind
            # by an earlier run. Feeding a generated Combined page back into a
            # retry duplicates content and can turn its blank stub into a bogus
            # lesson section.
            target_path = self.target_url.rstrip("/")
            topics = [
                topic for topic in topics
                if topic["url"].rstrip("/") != target_path
                and not is_collector_target_title(topic.get("label"))
            ]
            from target_page_creator import _parse_ids
            from unit_overview import (
                BrowserContentAPI, extract_description_html,
                has_meaningful_unit_content,
            )
            from editor_save import read_topic_html

            course_id, module_id = _parse_ids(self.unit_url)
            if not course_id or not module_id:
                self.log("✗ Could not identify the source unit", "error")
                return False
            unit_api = BrowserContentAPI(page, course_id, module_id)
            try:
                original_module = await unit_api.get_module()
                original_description = extract_description_html(original_module)
                description_needs_move = has_meaningful_unit_content(original_description)
                unit_description = original_description if description_needs_move else ""
                if not description_needs_move:
                    combined_before = await read_topic_html(page, self.target_url)
                    marker_present, stored = stored_unit_source(combined_before)
                    if marker_present:
                        from content_preservation import content_is_preserved

                        preserved, reason = content_is_preserved(
                            stored, combined_before, allow_label_colons=True
                        )
                        if not preserved:
                            raise ValueError(
                                f"stored section text is missing from the combined page: {reason}"
                            )
                        unit_description = stored
                        self.log("↻ Reusing section text from the existing combined page", "info")
            except Exception as exc:
                self.log(f"✗ Could not read the unit description safely: {exc}", "error")
                return False

            if not topics and not unit_description:
                self.log("✗ No topics or section text found — nothing to collect", "error")
                if self._on_complete:
                    self._on_complete()
                if not external:
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                return False
            if not topics:
                from editor_save import _topic_ids

                target_ids = _topic_ids(self.target_url)
                try:
                    children = await unit_api.list_structure()
                    if not isinstance(children, list):
                        raise ValueError("Brightspace returned an invalid child list")
                except Exception as exc:
                    self.log(f"✗ Could not verify the section is empty: {exc}", "error")
                    return False
                uncollected = [
                    child for child in children
                    if isinstance(child, dict)
                    and str(child.get("Id")) != (target_ids[1] if target_ids else "")
                    and not is_collector_target_title(child.get("Title"))
                ]
                if uncollected:
                    self.log(
                        "✗ The section has child items, but the page scan found none. "
                        "Its text was left in place; retry after the unit loads.", "error",
                    )
                    return False

            await self._build_name_matcher()

            # ── Split topics by who is allowed to see them ───────────────────
            # Brightspace visibility is per-topic; there is no way to hide text
            # inside a page. Staff-only topics therefore need a page of their own.
            hidden_ids = await self._fetch_hidden_topic_ids(page)
            if hidden_ids is None:
                self.log(
                    "✗ Stopping this unit: Brightspace would not say which topics are "
                    "hidden from students. Re-run once it responds — publishing "
                    "staff-only content by mistake is worse than retrying.",
                    "error",
                )
                if self._on_complete:
                    self._on_complete()
                if not external:
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                return False

            visible_topics, hidden_topics = split_by_visibility(topics, hidden_ids)

            if hidden_topics:
                self.log("─" * 52, "dim")
                self.log(
                    f"🔒 {len(hidden_topics)} topic(s) hidden from students — routing "
                    "to a separate instructor page:", "warning",
                )
                for t in hidden_topics:
                    self.log(f"     • {t['label']}", "dim")

            student_ok = True
            if visible_topics or unit_description:
                if unit_description:
                    student_ok = await self._collect_into(
                        context, visible_topics, self.target_url, unit_description
                    )
                else:
                    student_ok = await self._collect_into(
                        context, visible_topics, self.target_url
                    )
            else:
                self.log(
                    "⚠ Every topic in this unit is hidden from students — the student "
                    "page was left empty.", "warning",
                )

            if student_ok and unit_description:
                if not description_needs_move or self.claude_api_key:
                    student_ok = await self._finish_unit_description_transfer(
                        page, original_module, unit_description,
                        clear_source=description_needs_move,
                    )
                else:
                    self.log(
                        "⚠ Section text was collected, but remains in the section "
                        "until styling is enabled and verified.", "warning",
                    )

            # The instructor page is independent of the student page. A styling
            # or review problem on one must not leave the hidden topics
            # uncollected.
            hidden_ok = True
            if hidden_topics:
                if not student_ok:
                    self.log(
                        "Continuing with the instructor page — the student page "
                        "problems above are reported at the end.", "info",
                    )
                hidden_ok = await self._collect_hidden_topics(
                    context, page, hidden_topics
                )

            self.log("─" * 52, "dim")
            if not (student_ok and hidden_ok):
                failed = [
                    name for name, ok in (
                        ("student page", student_ok), ("instructor page", hidden_ok)
                    ) if not ok
                ]
                self.log(
                    f"✗ Finished with problems on the {' and '.join(failed)} — "
                    "see the messages above.", "error",
                )
                if self._on_complete:
                    self._on_complete()
                if not external:
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                return False

            self.log("✓ Done! Close the browser when finished.", "success")

            if self._on_complete:
                self._on_complete()

            if not external:
                while browser.is_connected():
                    await asyncio.sleep(0.5)

            return True

        except Exception:
            if self._on_complete:
                self._on_complete()
            raise
        finally:
            if not external:
                if browser.is_connected():
                    await browser.close()
                await p.stop()


async def run(
    unit_url: str,
    target_url: str,
    theme_name: str,
    theme_colors: dict,
    claude_api_key: str = "",
    claude_model: str = "",
    style_reference_html: str = "",
    parallel_pages: int = 3,
    auto_create_target: bool = True,
    log: Callable = None,
    on_complete: Callable = None,
    bs_username: str = "",
    bs_password: str = "",
    sso_email: str = "",
    sso_password: str = "",
    moodle_url: str = "",
    moodle_username: str = "",
    moodle_password: str = "",
    context: Optional[BrowserContext] = None,
    page: Optional[Page] = None,
    cleanup_only: bool = False,
) -> bool:
    return await UnitCollector(
        unit_url=unit_url,
        target_url=target_url,
        theme_name=theme_name,
        theme_colors=theme_colors,
        claude_api_key=claude_api_key,
        claude_model=claude_model,
        style_reference_html=style_reference_html,
        parallel_pages=parallel_pages,
        auto_create_target=auto_create_target,
        log=log,
        on_complete=on_complete,
        bs_username=bs_username,
        bs_password=bs_password,
        sso_email=sso_email,
        sso_password=sso_password,
        moodle_url=moodle_url,
        moodle_username=moodle_username,
        moodle_password=moodle_password,
    ).run(context=context, page=page, cleanup_only=cleanup_only)
