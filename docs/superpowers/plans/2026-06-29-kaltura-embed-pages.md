# Kaltura Embed → Brightspace Pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each Kaltura video found in a Moodle course, open it in KMC, grab its Share & Embed code, and create a new HTML page in the matching Brightspace module containing that embed iframe.

**Architecture:** Three-phase GUI workflow — scan Moodle (entry IDs + section names), user maps Moodle sections → Brightspace modules, then KMC browser extracts embed code per entry while a Brightspace browser creates a new page per entry. KMC and Brightspace each get their own Playwright browser instance. Backend helpers live in `kaltura_categorizer.py`; GUI lives in `kaltura_panel.py`.

**Tech Stack:** Python 3.12, Playwright async, PySide6, existing `_find_locator_any_frame` from `automator.py`, existing `_extract_course_id` from `content_checker.py`.

## Global Constraints

- All Playwright code is async; GUI workers use `asyncio.run()` in `threading.Thread(daemon=True)`
- Queue sentinel format: `(sentinel_str, payload)` tuples — never bare strings
- No commits unless user asks
- Do NOT spawn subagents without permission (per CLAUDE.md)
- Working directory for all commands: `/home/apelsinchik/antigravity/brightspace-page-automator`
- Python executable: `.venv/bin/python` (Linux)
- All `src/` imports run from inside `src/` (no `src.` prefix needed)

---

## File Structure

| File | Change |
|------|--------|
| `src/kaltura_categorizer.py` | Extend scanner, add `get_bs_modules`, add `_get_embed_code`, add `_create_bs_page`, add `embed_entries`, remove `categorize_entries` |
| `src/panels/kaltura_panel.py` | Full rewrite — new inputs, mapping area, updated sentinels |

---

### Task 1: Extend `scan_moodle_course` to capture section names

**Files:**
- Modify: `src/kaltura_categorizer.py` (lines 31–64)

**Interfaces:**
- Produces: `scan_moodle_course` returns `list[dict]` where each dict has keys `entry_id`, `name`, `moodle_url`, **`section_name`** (new)

- [ ] **Step 1: Replace the link-collection JS in `scan_moodle_course`**

Open `src/kaltura_categorizer.py`. Replace lines 32–35 (the `links = await page.evaluate(...)` block) with:

```python
                link_items = await page.evaluate("""() => {
                    const results = [];
                    document.querySelectorAll('li.section, li.section.main').forEach(section => {
                        const heading = section.querySelector('.sectionname, h3, h4');
                        const sectionName = heading ? heading.textContent.trim() : '(unnamed section)';
                        section.querySelectorAll('a[href*="mod/kalvidres/view.php"]').forEach(a => {
                            results.push({href: a.href, section_name: sectionName});
                        });
                    });
                    return results;
                }""")
                # deduplicate by href, preserve order
                seen = set()
                deduped = []
                for item in link_items:
                    if item["href"] not in seen:
                        seen.add(item["href"])
                        deduped.append(item)
                link_items = deduped
```

- [ ] **Step 2: Update the loop to use `link_items` instead of `links`**

Replace line 38 (`for link in links:`) and the `results.append(...)` block (lines 53–58) so the loop reads:

```python
                for item in link_items:
                    link = item["href"]
                    section_name = item["section_name"]
                    try:
                        await page.goto(link, wait_until="networkidle", timeout=20000)

                        iframe_src = await page.evaluate("""() => {
                            const f = document.querySelector('iframe#contentframe');
                            return f ? f.src : '';
                        }""")
                        m = re.search(r'entryid%2F([\w_]+)', iframe_src, re.IGNORECASE)
                        if not m:
                            continue
                        entry_id = m.group(1)

                        title = await page.title()
                        name = re.sub(r'\s*\|\s*OCmoodle\s*$', '', title).strip()

                        results.append({
                            "entry_id": entry_id,
                            "name": name,
                            "moodle_url": link,
                            "section_name": section_name,
                        })
                    except Exception as e:
                        print(f"[kaltura scanner] skipped {link}: {repr(e)}", file=sys.stderr)
                        continue
```

- [ ] **Step 3: Smoke-test the scanner**

Create a throwaway script at `/tmp/test_scan.py`:

```python
import asyncio, sys
sys.path.insert(0, 'src')
from kaltura_categorizer import KalturaCategorizer

async def main():
    entries = await KalturaCategorizer().scan_moodle_course(
        "https://mymoodle.okanagan.bc.ca/course/view.php?id=183744"
    )
    for e in entries:
        print(e)
    print(f"Total: {len(entries)}")

asyncio.run(main())
```

Run:
```bash
cd /home/apelsinchik/antigravity/brightspace-page-automator
.venv/bin/python /tmp/test_scan.py
```

Expected: 15 entries, each with a non-empty `section_name` field (e.g. `"Week 2 - Sterilization Methods"`). No `KeyError`.

---

### Task 2: Add `get_bs_modules` method

**Files:**
- Modify: `src/kaltura_categorizer.py`

**Interfaces:**
- Produces: `KalturaCategorizer.get_bs_modules(bs_url: str) -> list[dict]`
  - Each dict: `{"id": str, "title": str}` (all modules in the Brightspace course, recursively flattened)

- [ ] **Step 1: Add `get_bs_modules` to `KalturaCategorizer`**

Add the following method after `scan_moodle_course` (before `categorize_entries`) in `src/kaltura_categorizer.py`:

```python
    async def get_bs_modules(self, bs_url: str) -> list[dict]:
        """Return [{id, title}] for all modules in the Brightspace course via D2L TOC API."""
        from content_checker import _extract_course_id
        course_id = _extract_course_id(bs_url)
        if not course_id:
            raise ValueError(f"Could not extract course ID from URL: {bs_url}")
        base_url = "/".join(bs_url.split("/")[:3])

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
                context = await browser.new_context(storage_state=storage)
                page = await context.new_page()
                await page.goto(
                    f"{base_url}/d2l/le/content/{course_id}/home",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                modules = await page.evaluate(
                    """async (courseId) => {
                        const resp = await fetch(
                            `/d2l/api/le/1.0/${courseId}/content/toc`,
                            {credentials: 'include'}
                        );
                        if (!resp.ok) return null;
                        const toc = await resp.json();
                        const result = [];
                        function collect(modules) {
                            for (const m of (modules || [])) {
                                result.push({id: String(m.ModuleId), title: m.Title || '(unnamed)'});
                                collect(m.Modules);
                            }
                        }
                        collect(toc.Modules || []);
                        return result;
                    }""",
                    course_id,
                )
                return modules or []
            finally:
                await browser.close()
```

- [ ] **Step 2: Smoke-test `get_bs_modules`**

Create `/tmp/test_modules.py`:

```python
import asyncio, sys
sys.path.insert(0, 'src')
from kaltura_categorizer import KalturaCategorizer

async def main():
    modules = await KalturaCategorizer().get_bs_modules(
        "https://brightspace.okanagan.bc.ca/d2l/home/YOUR_COURSE_ID"
        # Replace with a real BS course URL you have access to
    )
    for m in modules:
        print(m)
    print(f"Total modules: {len(modules)}")

asyncio.run(main())
```

Run:
```bash
.venv/bin/python /tmp/test_modules.py
```

Expected: list of `{id, title}` dicts, one per course module. No `None` return, no exception.

---

### Task 3: Add `embed_entries` with `_get_embed_code` and `_create_bs_page` helpers

**Files:**
- Modify: `src/kaltura_categorizer.py`

**Interfaces:**
- Consumes (from Task 1): `entry["section_name"]` field
- Consumes (from Task 2): `get_bs_modules` (called by GUI, not by `embed_entries` directly)
- Produces:
  - `KalturaCategorizer._get_embed_code(kmc_page, entry_id: str, log_fn) -> str | None`
  - `KalturaCategorizer._create_bs_page(bs_page, base_url: str, course_id: str, module_id: str, title: str, html: str, log_fn) -> bool`
  - `KalturaCategorizer.embed_entries(entries: list[dict], section_map: dict[str, str], bs_url: str, log_fn) -> None`

- [ ] **Step 1: Add import for `_find_locator_any_frame` at top of file**

In `src/kaltura_categorizer.py`, add to the imports section:

```python
from automator import _find_locator_any_frame
```

- [ ] **Step 2: Add `_get_embed_code` private method**

Add after `get_bs_modules`:

```python
    async def _get_embed_code(self, kmc_page, entry_id: str, log_fn) -> "str | None":
        """Navigate KMC to entry, open Share & Embed, return embed code from textarea."""
        log_fn(f"  KMC: searching {entry_id}…", "dim")
        await kmc_page.goto(KMC_URL, wait_until="networkidle", timeout=20000)

        search = kmc_page.locator("input[type='text']").first
        await search.click()
        await search.click(click_count=3)
        await search.type(entry_id)
        await kmc_page.keyboard.press("Enter")
        await kmc_page.wait_for_timeout(2000)

        rows = kmc_page.locator("p-table tbody tr, tr.kEntry")
        if await rows.count() == 0:
            log_fn(f"  ⚠ Entry {entry_id} not found in KMC", "warning")
            return None
        await rows.first.click()
        await kmc_page.wait_for_timeout(1500)

        try:
            share_link = kmc_page.locator(".kPreviewAndEmbedContainer a").first
            await share_link.wait_for(state="visible", timeout=5000)
            await share_link.click()
        except Exception:
            log_fn(f"  ⚠ Share & Embed link not found for {entry_id}", "warning")
            return None
        await kmc_page.wait_for_timeout(1500)

        try:
            code = await kmc_page.locator("textarea").first.input_value(timeout=5000)
            if code and code.strip():
                return code.strip()
        except Exception:
            pass

        log_fn(f"  ⚠ Embed textarea empty for {entry_id}", "warning")
        return None
```

- [ ] **Step 3: Add `_create_bs_page` private method**

Add after `_get_embed_code`:

```python
    async def _create_bs_page(
        self,
        bs_page,
        base_url: str,
        course_id: str,
        module_id: str,
        title: str,
        html: str,
        log_fn,
    ) -> bool:
        """Navigate to a Brightspace module, create a new Page, paste embed HTML, save."""
        log_fn(f"  BS: opening module {module_id}…", "dim")

        # Navigate to module view (URL format verified against D2L; adjust if needed)
        module_url = f"{base_url}/d2l/le/content/{course_id}/modules/{module_id}/home"
        try:
            await bs_page.goto(module_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        await bs_page.wait_for_timeout(2000)

        # ── Shadow-DOM helpers ─────────────────────────────────────────────────
        _JS_DEEP_CLICK = """(selector) => {
            function deepFind(root, sel, depth) {
                if (depth === 0) return null;
                const el = root.querySelector(sel);
                if (el) return el;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot, sel, depth - 1);
                        if (f) return f;
                    }
                }
                return null;
            }
            const el = deepFind(document, selector, 10);
            if (!el) return false;
            if (el.shadowRoot) {
                const inner = el.shadowRoot.querySelector('button');
                if (inner) { inner.click(); return true; }
            }
            el.click();
            return true;
        }"""

        async def js_click(selector: str) -> bool:
            frames = [bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]
            for ctx in frames:
                try:
                    if await ctx.evaluate(_JS_DEEP_CLICK, selector):
                        return True
                except Exception:
                    pass
            return False

        # ── Click "Create New" ─────────────────────────────────────────────────
        if not await js_click('button[aria-label="Create New"]'):
            log_fn("  ✗ 'Create New' button not found", "error")
            return False
        await bs_page.wait_for_timeout(1000)

        # ── Click "Page" tile ──────────────────────────────────────────────────
        _JS_PAGE_TILE = """() => {
            function deepFind(root, fn, depth) {
                if (depth === 0) return null;
                const found = fn(root);
                if (found) return found;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot, fn, depth - 1);
                        if (f) return f;
                    }
                }
                return null;
            }
            const tile = deepFind(document, r => {
                for (const d of r.querySelectorAll('div.add-material-tile-inner')) {
                    const t = d.querySelector('.material-tile-text');
                    if (t && t.textContent.trim() === 'Page') return d;
                    if (d.querySelector('svg#htmldoc')) return d;
                }
                return null;
            }, 10);
            if (!tile) return false;
            tile.click();
            return true;
        }"""

        page_tile_found = False
        for ctx in [bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]:
            try:
                if await ctx.evaluate(_JS_PAGE_TILE):
                    page_tile_found = True
                    break
            except Exception:
                pass

        if not page_tile_found:
            log_fn("  ✗ 'Page' tile not found", "error")
            return False
        await bs_page.wait_for_timeout(3000)

        # ── Fill title ─────────────────────────────────────────────────────────
        for title_sel in (
            'd2l-input-text input',
            'input[aria-label*="itle"]',
            'input[name="title"]',
            'input[id*="title"]',
        ):
            _, loc = await _find_locator_any_frame(bs_page, title_sel, retries=3, delay_ms=500)
            if loc:
                await loc.fill(title)
                log_fn(f"  ✓ Title: {title}", "dim")
                break
        else:
            log_fn("  ⚠ Title field not found — set manually", "warning")

        # ── Open Source Code editor ────────────────────────────────────────────
        source_opened = False
        for _ in range(5):
            if await js_click('d2l-htmleditor-button[cmd="d2l-source-code"]'):
                source_opened = True
                break
            await bs_page.wait_for_timeout(700)

        if not source_opened:
            await js_click('d2l-htmleditor-button-toggle.d2l-htmleditor-toolbar-chomper')
            await bs_page.wait_for_timeout(700)
            for sel in (
                'd2l-htmleditor-button[cmd="d2l-source-code"]',
                'd2l-htmleditor-menu-item[cmd="d2l-source-code"]',
            ):
                for _ in range(4):
                    if await js_click(sel):
                        source_opened = True
                        break
                    await bs_page.wait_for_timeout(500)
                if source_opened:
                    break

        if not source_opened:
            log_fn("  ✗ Source Code button not found", "error")
            return False

        await bs_page.wait_for_timeout(800)

        # ── Paste HTML ─────────────────────────────────────────────────────────
        await bs_page.evaluate("(h) => navigator.clipboard.writeText(h)", html)
        await bs_page.wait_for_timeout(300)

        await bs_page.evaluate("""() => {
            function deepFind(root) {
                const el = root.querySelector('[contenteditable="true"].cm-content');
                if (el) return el;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot);
                        if (f) return f;
                    }
                }
                return null;
            }
            const el = deepFind(document);
            if (el) { el.focus(); el.click(); }
        }""")
        await bs_page.wait_for_timeout(400)
        await bs_page.keyboard.press("Control+a")
        await bs_page.wait_for_timeout(200)
        await bs_page.keyboard.press("Control+v")
        await bs_page.wait_for_timeout(600)

        # ── Close Source Code dialog ───────────────────────────────────────────
        for selector in (
            '[data-dialog-action="save"]',
            'd2l-button:has-text("OK")',
            'button:has-text("OK")',
            'd2l-button:has-text("Update")',
            'button:has-text("Update")',
        ):
            _, btn = await _find_locator_any_frame(bs_page, selector, retries=3, delay_ms=400)
            if btn:
                await btn.first.click()
                break
        await bs_page.wait_for_timeout(1200)

        # ── Save and Close ─────────────────────────────────────────────────────
        for selector in (
            'd2l-button:has-text("Save and Close")',
            'button:has-text("Save and Close")',
            'd2l-button:has-text("Save")',
            'button:has-text("Save")',
        ):
            _, btn = await _find_locator_any_frame(bs_page, selector, retries=6, delay_ms=600)
            if btn:
                await btn.first.click()
                log_fn(f"  ✓ Saved: {title}", "success")
                return True

        log_fn("  ⚠ Save button not found", "warning")
        return False
```

- [ ] **Step 4: Add `embed_entries` method**

Add after `_create_bs_page`:

```python
    async def embed_entries(
        self,
        entries: list[dict],
        section_map: dict[str, str],
        bs_url: str,
        log_fn,
    ) -> None:
        """For each entry: get KMC embed code → create Brightspace page."""
        from content_checker import _extract_course_id
        course_id = _extract_course_id(bs_url)
        if not course_id:
            raise ValueError(f"Could not extract course ID from URL: {bs_url}")
        base_url = "/".join(bs_url.split("/")[:3])

        async with async_playwright() as p:
            kmc_context, kmc_browser = await self._get_kmc_context(p)
            bs_browser = await p.chromium.launch(headless=False)
            try:
                storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
                bs_context = await bs_browser.new_context(storage_state=storage)
                kmc_page = await kmc_context.new_page()
                bs_page = await bs_context.new_page()

                for entry in entries:
                    entry_id = entry["entry_id"]
                    name = entry["name"]
                    section_name = entry.get("section_name", "")
                    module_id = section_map.get(section_name)

                    if not module_id:
                        log_fn(
                            f"⚠ No module mapped for section '{section_name}', skipping {name}",
                            "warning",
                        )
                        continue

                    try:
                        embed_code = await self._get_embed_code(kmc_page, entry_id, log_fn)
                        if not embed_code:
                            continue
                        ok = await self._create_bs_page(
                            bs_page, base_url, course_id, module_id, name, embed_code, log_fn
                        )
                        if not ok:
                            log_fn(f"✗ Failed to create page: {name}", "error")
                    except Exception as e:
                        log_fn(f"✗ {name}: {e}", "error")

                await kmc_context.storage_state(path=KMC_SESSION_FILE)
            finally:
                await kmc_browser.close()
                await bs_browser.close()
```

- [ ] **Step 5: Remove `categorize_entries` from `kaltura_categorizer.py`**

Delete the `categorize_entries` method (lines 66–129 in original file). It is fully replaced by `embed_entries`.

- [ ] **Step 6: Verify imports parse cleanly**

```bash
cd /home/apelsinchik/antigravity/brightspace-page-automator
.venv/bin/python -c "import sys; sys.path.insert(0,'src'); from kaltura_categorizer import KalturaCategorizer; print('OK')"
```

Expected output: `OK`

---

### Task 4: Redesign `KalturaPanel` GUI

**Files:**
- Modify: `src/panels/kaltura_panel.py` (full rewrite)

**Interfaces:**
- Consumes (Task 1): `entry["section_name"]`
- Consumes (Task 2): `KalturaCategorizer.get_bs_modules(bs_url)`
- Consumes (Task 3): `KalturaCategorizer.embed_entries(entries, section_map, bs_url, log_fn)`

- [ ] **Step 1: Rewrite `kaltura_panel.py`**

Replace the entire file with:

```python
import asyncio
import json
import queue
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QCheckBox,
    QFrame, QGridLayout, QComboBox,
)
from PySide6.QtCore import QTimer

from gui_log import LogWidget
from panels._shared import _form_label, _section_header


class KalturaPanel(QWidget):

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._log_queue: queue.Queue = queue.Queue()
        self._checkboxes: list[tuple[QCheckBox, dict]] = []
        self._combos: dict[str, QComboBox] = {}   # section_name → QComboBox
        self._bs_modules: list[dict] = []          # [{id, title}] cached after fetch
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Kaltura → Brightspace Pages"))
        sub = QLabel(
            "Scans Moodle for Kaltura videos, then creates matching pages in Brightspace."
        )
        sub.setProperty("role", "dim")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("MOODLE COURSE URL"))
        layout.addSpacing(4)
        self._moodle_url = QLineEdit()
        self._moodle_url.setPlaceholderText(
            "https://mymoodle.okanagan.bc.ca/course/view.php?id=183744"
        )
        self._moodle_url.setFixedHeight(40)
        layout.addWidget(self._moodle_url)
        layout.addSpacing(12)

        bs_row = QHBoxLayout()
        bs_col = QVBoxLayout()
        bs_col.setSpacing(4)
        bs_col.addWidget(_form_label("BRIGHTSPACE COURSE URL"))
        self._bs_url = QLineEdit()
        self._bs_url.setPlaceholderText(
            "https://brightspace.okanagan.bc.ca/d2l/home/10263"
        )
        self._bs_url.setFixedHeight(40)
        bs_col.addWidget(self._bs_url)
        bs_row.addLayout(bs_col)
        bs_row.addSpacing(12)
        self._scan_btn = QPushButton("Scan Moodle")
        self._scan_btn.setFixedHeight(40)
        self._scan_btn.clicked.connect(self._start_scan)
        bs_row.addWidget(self._scan_btn, 0)
        layout.addLayout(bs_row)
        layout.addSpacing(16)

        layout.addWidget(_form_label("FOUND VIDEOS"))
        layout.addSpacing(4)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_widget)
        scroll.setFixedHeight(180)
        layout.addWidget(scroll)
        layout.addSpacing(8)

        sel_row = QHBoxLayout()
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setFixedHeight(30)
        self._select_all_btn.clicked.connect(lambda: self._set_all(True))
        self._deselect_btn = QPushButton("Deselect All")
        self._deselect_btn.setFixedHeight(30)
        self._deselect_btn.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(self._select_all_btn)
        sel_row.addWidget(self._deselect_btn)
        sel_row.addStretch()
        layout.addLayout(sel_row)
        layout.addSpacing(16)

        # ── Map Sections area (hidden until scan) ─────────────────────────────
        self._mapping_frame = QFrame()
        self._mapping_frame.setVisible(False)
        mapping_layout = QVBoxLayout(self._mapping_frame)
        mapping_layout.setContentsMargins(0, 0, 0, 0)
        mapping_layout.setSpacing(8)

        map_header_row = QHBoxLayout()
        map_header_row.addWidget(_form_label("MAP SECTIONS → BRIGHTSPACE MODULES"))
        map_header_row.addStretch()
        self._fetch_modules_btn = QPushButton("Fetch BS Modules")
        self._fetch_modules_btn.setFixedHeight(32)
        self._fetch_modules_btn.clicked.connect(self._start_fetch_modules)
        map_header_row.addWidget(self._fetch_modules_btn)
        mapping_layout.addLayout(map_header_row)

        self._mapping_grid_widget = QWidget()
        self._mapping_grid = QGridLayout(self._mapping_grid_widget)
        self._mapping_grid.setContentsMargins(0, 0, 0, 0)
        self._mapping_grid.setSpacing(6)
        self._mapping_grid.setColumnStretch(1, 1)
        mapping_layout.addWidget(self._mapping_grid_widget)

        layout.addWidget(self._mapping_frame)
        layout.addSpacing(12)

        self._create_btn = QPushButton("Create Pages")
        self._create_btn.setFixedHeight(42)
        self._create_btn.setEnabled(False)
        self._create_btn.clicked.connect(self._start_create_pages)
        layout.addWidget(self._create_btn)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)

    # ── List helpers ──────────────────────────────────────────────────────────

    def _populate_list(self, entries: list[dict]):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._checkboxes.clear()
        for entry in entries:
            section = entry.get("section_name", "")
            label = f"[{section}] {entry['name']}" if section else entry["name"]
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._list_layout.addWidget(cb)
            self._checkboxes.append((cb, entry))

    def _set_all(self, checked: bool):
        for cb, _ in self._checkboxes:
            cb.setChecked(checked)

    def _selected_entries(self) -> list[dict]:
        return [entry for cb, entry in self._checkboxes if cb.isChecked()]

    # ── Mapping helpers ───────────────────────────────────────────────────────

    def _build_mapping_rows(self, section_names: list[str]):
        """Create one label + QComboBox row per unique section name."""
        # Clear existing rows
        while self._mapping_grid.count():
            item = self._mapping_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._combos.clear()

        for row, name in enumerate(section_names):
            lbl = QLabel(name)
            lbl.setWordWrap(True)
            combo = QComboBox()
            combo.addItem("— select module —", None)
            combo.currentIndexChanged.connect(self._check_mapping_complete)
            self._mapping_grid.addWidget(lbl, row, 0)
            self._mapping_grid.addWidget(combo, row, 1)
            self._combos[name] = combo

    def _populate_combos(self, modules: list[dict]):
        """Fill QComboBox options from fetched BS module list."""
        self._bs_modules = modules
        for combo in self._combos.values():
            # Keep placeholder at index 0, clear any previously loaded options
            while combo.count() > 1:
                combo.removeItem(1)
            for mod in modules:
                combo.addItem(mod["title"], mod["id"])
        self._check_mapping_complete()

    def _check_mapping_complete(self):
        """Enable Create Pages only when every section combo has a non-placeholder selection."""
        if not self._combos:
            self._create_btn.setEnabled(False)
            return
        all_selected = all(
            combo.currentData() is not None
            for combo in self._combos.values()
        )
        self._create_btn.setEnabled(all_selected)

    def _build_section_map(self) -> dict[str, str]:
        """Return {section_name: bs_module_id} from current combo selections."""
        return {
            name: combo.currentData()
            for name, combo in self._combos.items()
            if combo.currentData() is not None
        }

    # ── Workers ───────────────────────────────────────────────────────────────

    def _start_scan(self):
        url = self._moodle_url.text().strip()
        if not url:
            self._log.append_log("Paste a Moodle course URL first.", "warning")
            return
        self._scan_btn.setText("Scanning…")
        self._scan_btn.setEnabled(False)
        self._create_btn.setEnabled(False)
        self._mapping_frame.setVisible(False)
        self._log.clear_log()
        q = self._log_queue

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                q.put(("Scanning Moodle course…", "dim"))
                entries = asyncio.run(KalturaCategorizer().scan_moodle_course(url))
                q.put(("__SCAN_DONE__", entries))
            except Exception as e:
                q.put((f"Scan error: {e}", "error"))
                q.put(("__SCAN_FAIL__", None))

        threading.Thread(target=worker, daemon=True).start()

    def _start_fetch_modules(self):
        bs_url = self._bs_url.text().strip()
        if not bs_url:
            self._log.append_log("Enter Brightspace course URL first.", "warning")
            return
        self._fetch_modules_btn.setText("Fetching…")
        self._fetch_modules_btn.setEnabled(False)
        q = self._log_queue

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                q.put(("Fetching Brightspace modules…", "dim"))
                modules = asyncio.run(KalturaCategorizer().get_bs_modules(bs_url))
                q.put(("__MODULES_DONE__", modules))
            except Exception as e:
                q.put((f"Fetch modules error: {e}", "error"))
                q.put(("__MODULES_FAIL__", None))

        threading.Thread(target=worker, daemon=True).start()

    def _start_create_pages(self):
        entries = self._selected_entries()
        bs_url = self._bs_url.text().strip()
        section_map = self._build_section_map()
        if not entries:
            self._log.append_log("No videos selected.", "warning")
            return
        if not bs_url:
            self._log.append_log("Enter Brightspace course URL first.", "warning")
            return
        if not section_map:
            self._log.append_log("Map sections to modules first.", "warning")
            return
        self._create_btn.setText("Running…")
        self._create_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        q = self._log_queue

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                asyncio.run(KalturaCategorizer().embed_entries(
                    entries,
                    section_map,
                    bs_url,
                    log_fn=lambda msg, tag="info": q.put((msg, tag)),
                ))
                q.put(("__CAT_DONE__", None))
            except Exception as e:
                q.put((f"Error: {e}", "error"))
                q.put(("__CAT_DONE__", None))

        threading.Thread(target=worker, daemon=True).start()

    # ── Poll ──────────────────────────────────────────────────────────────────

    def _poll_log(self):
        try:
            while True:
                msg, payload = self._log_queue.get_nowait()
                if msg == "__SCAN_DONE__":
                    entries = payload
                    self._scan_btn.setText("Scan Moodle")
                    self._scan_btn.setEnabled(True)
                    self._populate_list(entries)
                    section_names = list(dict.fromkeys(
                        e.get("section_name", "") for e in entries if e.get("section_name")
                    ))
                    self._build_mapping_rows(section_names)
                    self._mapping_frame.setVisible(bool(entries))
                    self._log.append_log(f"Found {len(entries)} Kaltura video(s).", "success")
                elif msg == "__SCAN_FAIL__":
                    self._scan_btn.setText("Scan Moodle")
                    self._scan_btn.setEnabled(True)
                    self._create_btn.setEnabled(False)
                    self._mapping_frame.setVisible(False)
                    self._populate_list([])
                elif msg == "__MODULES_DONE__":
                    modules = payload
                    self._fetch_modules_btn.setText("Fetch BS Modules")
                    self._fetch_modules_btn.setEnabled(True)
                    self._populate_combos(modules)
                    self._log.append_log(f"Loaded {len(modules)} module(s).", "success")
                elif msg == "__MODULES_FAIL__":
                    self._fetch_modules_btn.setText("Fetch BS Modules")
                    self._fetch_modules_btn.setEnabled(True)
                elif msg == "__CAT_DONE__":
                    self._create_btn.setText("Create Pages")
                    self._check_mapping_complete()
                    self._scan_btn.setEnabled(True)
                else:
                    self._log.append_log(msg, payload)
        except queue.Empty:
            pass

    def apply_theme(self, colors: dict):
        pass
```

- [ ] **Step 2: Verify GUI imports cleanly**

```bash
cd /home/apelsinchik/antigravity/brightspace-page-automator
.venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from panels.kaltura_panel import KalturaPanel
print('KalturaPanel import OK')
"
```

Expected: `KalturaPanel import OK`

- [ ] **Step 3: Verify full app launches without error**

```bash
cd /home/apelsinchik/antigravity/brightspace-page-automator
.venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)

# Minimal stub for main_window
class MW:
    theme_name = 'lake'
    credentials = {}

from panels.kaltura_panel import KalturaPanel
panel = KalturaPanel(MW())
panel.show()
print('Panel created OK')
app.quit()
"
```

Expected: `Panel created OK` with no exceptions.

- [ ] **Step 4: Manual smoke test — launch app and inspect Kaltura tab**

```bash
cd /home/apelsinchik/antigravity/brightspace-page-automator
.venv/bin/python src/gui.py
```

Verify:
- Kaltura tab shows in sidebar
- "Brightspace Course URL" input (not "Course ID")
- Mapping area hidden initially
- After scan: mapping area appears with one row per Moodle section
- Combos empty until "Fetch BS Modules" is clicked
- "Create Pages" button disabled until all combos have a selection
- After all combos selected: "Create Pages" enables
