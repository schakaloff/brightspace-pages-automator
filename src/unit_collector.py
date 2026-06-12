import asyncio
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
        const inner = el.shadowRoot.querySelector('button');
        if (inner) { inner.click(); return true; }
    }
    el.click();
    return true;
}"""


class UnitCollector:
    def __init__(
        self,
        unit_url: str,
        output_path: str,
        theme_name: str,
        theme_colors: dict,
        log: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
    ):
        self.unit_url = unit_url
        self.output_path = Path(output_path)
        self.theme_name = theme_name
        self.theme_colors = theme_colors
        self._log_fn = log
        self._on_complete = on_complete
        self._clipboard_lock = asyncio.Lock()

    def log(self, msg: str, level: str = "info"):
        if self._log_fn:
            self._log_fn(msg, level)

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
        return result if (result and "<" in result) else None

    async def _scrape_topics(self, page: Page) -> List[dict]:
        self.log("Scanning unit for topic pages...", "info")
        try:
            await page.wait_for_selector("iframe", timeout=8000)
        except Exception:
            pass

        base_url = "/".join(self.unit_url.split("/")[:3])
        lesson_id = self.unit_url.rstrip("/").split("/")[-1]

        SKIP_TYPES = ["quiz", "dropbox", "video", "youtube",
                      "discussion", "survey", "assignment", "checklist", "lti"]

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
                hint = t.get("hint", "")
                suffix = "  [link]" if "link" in hint else ""
                self.log(f"  + {t['label']}{suffix}", "dim")
                unique.append({"label": t["label"], "url": t["url"], "hint": hint})

        if unique:
            self.log(f"✓ Found {len(unique)} topic(s)", "success")
        else:
            self.log("⚠ No topics found — are you logged in? Is the unit expanded?", "warning")
        return unique

    async def _collect_link(self, page: Page, url: str, label: str) -> Optional[str]:
        """Navigate to a link-type topic, click Open Link, capture the new tab's URL."""
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

        # Snapshot pages that already exist so we can detect what's new after clicking
        pages_before = set(id(p) for p in page.context.pages)

        # Try clicking via Playwright locator in main frame and every sub-frame
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

        # Wait up to 8s for a new tab to appear
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

    async def _collect_topic(self, page: Page, url: str, label: str) -> Optional[str]:
        self.log(f"─" * 52, "dim")
        self.log(f"Collecting: {label}", "step")
        self.log(f"  {url}", "dim")

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
            self.log("✗ Options button not found — skipping", "error")
            return None
        await btn.first.scroll_into_view_if_needed()
        await btn.first.click()

        _, edit_btn = await _find_locator_any_frame(page, "d2l-menu-item#optEdit", retries=8, delay_ms=500)
        if edit_btn is None:
            self.log("✗ Edit menu not found — skipping", "error")
            return None
        await edit_btn.first.wait_for(state="visible", timeout=4000)
        await edit_btn.first.click()

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(800)

        async def js_click(selector: str) -> bool:
            for ctx in [page, *[f for f in page.frames if f != page.main_frame]]:
                try:
                    if await ctx.evaluate(_JS_DEEP_CLICK, selector):
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
            await js_click("d2l-htmleditor-button-toggle.d2l-htmleditor-toolbar-chomper")
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

        if not opened:
            self.log("✗ Source Code button not found — skipping", "error")
            return None

        html = await self._extract_html(page)
        if html:
            self.log(f"✓ {label} ({len(html):,} chars)", "success")
        else:
            self.log(f"✗ Could not extract HTML for {label}", "error")
        return html

    def _build_output_html(self, topics_html: List[dict]) -> str:
        c = self.theme_colors
        primary  = c["primary"]
        mid      = c["mid"]
        bg_from  = c["bg_from"]

        sections = ""
        for item in topics_html:
            label = item["label"].replace("<", "&lt;").replace(">", "&gt;")
            if item.get("is_link"):
                link_url = item.get("link_url", "")
                if link_url:
                    sections += f"""
  <div class="link-item">
    <span class="link-icon">🔗</span>
    <a href="{link_url}" target="_blank" rel="noopener noreferrer">{label}</a>
  </div>
"""
            else:
                content = item.get("html", "")
                sections += f"""
  <details>
    <summary>{label}</summary>
    <div class="topic-content">{content}</div>
  </details>
"""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Unit Collection — {self.theme_name}</title>
  <style>
    :root {{
      --primary: {primary};
      --mid: {mid};
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: system-ui, -apple-system, sans-serif;
      background: {bg_from};
      padding: 28px 20px;
      color: #1a1a2e;
      line-height: 1.6;
    }}
    details {{
      background: #fff;
      border: 1.5px solid var(--primary);
      border-radius: 10px;
      margin: 14px 0;
      overflow: hidden;
      box-shadow: 0 2px 10px rgba(0,0,0,0.06);
    }}
    details[open] > summary {{
      border-bottom: 1.5px solid var(--primary);
    }}
    summary {{
      background: linear-gradient(135deg, var(--primary), var(--mid));
      color: #fff;
      padding: 14px 20px;
      cursor: pointer;
      font-size: 15px;
      font-weight: 600;
      list-style: none;
      user-select: none;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    summary::-webkit-details-marker {{ display: none; }}
    summary::before {{
      content: "▶";
      font-size: 11px;
      opacity: 0.85;
      transition: transform 0.2s;
    }}
    details[open] > summary::before {{
      content: "▼";
    }}
    .topic-content {{
      padding: 20px 24px;
    }}
    .topic-content a {{ color: var(--primary); }}
    .topic-content img {{ max-width: 100%; border-radius: 6px; }}
    .topic-content table {{ border-collapse: collapse; width: 100%; }}
    .topic-content th, .topic-content td {{
      border: 1px solid #dde; padding: 8px 12px;
    }}
    .link-item {{
      display: flex;
      align-items: center;
      gap: 10px;
      background: #fff;
      border: 1.5px solid var(--primary);
      border-radius: 10px;
      margin: 14px 0;
      padding: 14px 20px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.06);
    }}
    .link-item a {{
      color: var(--primary);
      font-weight: 600;
      font-size: 15px;
      text-decoration: none;
    }}
    .link-item a:hover {{ text-decoration: underline; }}
    .link-icon {{ font-size: 18px; }}
  </style>
</head>
<body>
{sections}
</body>
</html>"""

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login
        from config import SESSION_FILE

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context)

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
            if not topics:
                self.log("✗ No topics found — nothing to collect", "error")
                if self._on_complete:
                    self._on_complete()
                while browser.is_connected():
                    await asyncio.sleep(0.5)
                return

            self.log(f"Collecting {len(topics)} topic(s) — up to 5 at a time", "info")

            sem = asyncio.Semaphore(5)
            results: List[Optional[dict]] = [None] * len(topics)

            async def collect_one(topic: dict, idx: int) -> None:
                async with sem:
                    tab = await context.new_page()
                    try:
                        is_link = "link" in topic.get("hint", "")
                        if is_link:
                            link_url = await self._collect_link(tab, topic["url"], topic["label"])
                            results[idx] = {"label": topic["label"], "is_link": True, "link_url": link_url or ""}
                        else:
                            html = await self._collect_topic(tab, topic["url"], topic["label"])
                            results[idx] = {"label": topic["label"], "is_link": False, "html": html or ""}
                    finally:
                        await tab.close()

            await asyncio.gather(*[collect_one(t, i) for i, t in enumerate(topics)])

            collected = [r for r in results if r and (r.get("html") or r.get("link_url"))]
            self.log(f"✓ Collected {len(collected)}/{len(topics)} topic(s)", "success")

            if collected:
                output_html = self._build_output_html(collected)
                self.output_path.parent.mkdir(parents=True, exist_ok=True)
                self.output_path.write_text(output_html, encoding="utf-8")
                self.log(f"✓ Saved → {self.output_path}", "success")
            else:
                self.log("✗ Nothing collected — output file not written", "error")

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
    output_path: str,
    theme_name: str,
    theme_colors: dict,
    log: Callable[[str, str], None],
    on_complete: Callable = None,
) -> None:
    await UnitCollector(
        unit_url=unit_url,
        output_path=output_path,
        theme_name=theme_name,
        theme_colors=theme_colors,
        log=log,
        on_complete=on_complete,
    ).run()
