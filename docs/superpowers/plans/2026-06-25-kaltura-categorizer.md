# Kaltura Categorizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Kaltura tab that scrapes Moodle for Kaltura video entry IDs, shows a review checklist, then automates KMC to categorize each selected video under a Brightspace course ID.

**Architecture:** New `KalturaCategorizer` backend class handles Playwright automation (Moodle scrape + KMC categorization). New `KalturaPanel` GUI panel follows the existing `CollectorPanel` worker-thread + queue pattern. Panel wired into `gui.py` as step 4 and sidebar.

**Tech Stack:** Python 3, Playwright (async), PySide6, existing `session.json` for Moodle auth, new `kmc_session.json` for KMC auth.

## Global Constraints

- All new files go in `src/` or `src/panels/` — follow existing layout
- Worker threads use `threading.Thread` + `queue.Queue` — no QThread
- Log messages use `(msg, tag)` tuples; tags: `"info"`, `"success"`, `"warning"`, `"error"`, `"dim"`
- Session file: `kmc_session.json` in `USERDATA_DIR` (same as `session.json` — from `src/config.py`)
- KMC URL: `https://kmc.cap2.ovp.kaltura.com/index.php/kmcng/content/entries/list`
- Moodle entry ID regex: `entryid%2F([\w_]+)` from iframe src
- Title strip suffix: ` | OCmoodle`
- No commits unless user asks

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/kaltura_categorizer.py` | Create | Playwright backend: scan Moodle, categorize in KMC |
| `src/panels/kaltura_panel.py` | Create | PySide6 panel: inputs, checklist, log |
| `src/gui_icons.py` | Modify | Add `_kaltura` icon draw function + registry entry |
| `gui.py` | Modify | Wire KalturaPanel as step 4 in sidebar + stack |

---

## Task 1: Backend — Moodle scanner

**Files:**
- Create: `src/kaltura_categorizer.py`

**Interfaces:**
- Produces: `KalturaCategorizer.scan_moodle_course(moodle_course_url: str) -> list[dict]`
  - Each dict: `{"entry_id": str, "name": str, "moodle_url": str}`
- Produces: `KMC_SESSION_FILE: str` (path constant)

- [ ] **Step 1: Create `src/kaltura_categorizer.py` with scan logic**

```python
import asyncio
import re
import os

from playwright.async_api import async_playwright

from config import USERDATA_DIR, SESSION_FILE

KMC_URL = "https://kmc.cap2.ovp.kaltura.com/index.php/kmcng/content/entries/list"
KMC_SESSION_FILE = str(USERDATA_DIR / "kmc_session.json")


class KalturaCategorizer:

    async def scan_moodle_course(self, moodle_course_url: str) -> list[dict]:
        """Scrape all kalvidres activity pages in a Moodle course.

        Returns list of {entry_id, name, moodle_url}.
        """
        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
            context = await browser.new_context(storage_state=storage)
            page = await context.new_page()

            await page.goto(moodle_course_url, wait_until="networkidle", timeout=30000)

            # Collect all kalvidres links on the course page
            links = await page.evaluate("""() => {
                return [...document.querySelectorAll('a[href*="mod/kalvidres/view.php"]')]
                    .map(a => a.href);
            }""")
            links = list(dict.fromkeys(links))  # deduplicate, preserve order

            for link in links:
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
                    })
                except Exception:
                    continue

            await browser.close()
        return results
```

- [ ] **Step 2: Manual smoke test**

```bash
source .venv/bin/activate
python -c "
import asyncio, sys
sys.path.insert(0, 'src')
from kaltura_categorizer import KalturaCategorizer
results = asyncio.run(KalturaCategorizer().scan_moodle_course(
    'https://mymoodle.okanagan.bc.ca/course/view.php?id=183744'
))
for r in results[:3]:
    print(r)
print('Total:', len(results))
"
```
Expected: prints dicts with `entry_id` like `0_xxxxxxxx`, `name` without `| OCmoodle`, total > 0.

---

## Task 2: Backend — KMC session login

**Files:**
- Modify: `src/kaltura_categorizer.py`

**Interfaces:**
- Consumes: `KMC_SESSION_FILE` from Task 1
- Produces: `KalturaCategorizer._get_kmc_context(playwright) -> tuple[BrowserContext, Browser]`

- [ ] **Step 1: Add `_get_kmc_context` inside `KalturaCategorizer` class**

```python
    async def _get_kmc_context(self, playwright):
        """Return a logged-in KMC browser context.

        Loads kmc_session.json if it exists and is still valid.
        Otherwise opens a visible browser for manual SSO login, waits
        for the KMC entries list page to load, then saves the session.
        """
        browser = await playwright.chromium.launch(headless=False)
        storage = KMC_SESSION_FILE if os.path.exists(KMC_SESSION_FILE) else None
        context = await browser.new_context(storage_state=storage)
        page = await context.new_page()
        await page.goto(KMC_URL, timeout=30000)

        # If redirected away from KMC (SSO login), wait for user
        if "kmcng/content/entries/list" not in page.url:
            await page.wait_for_url("**/kmcng/content/entries/list**", timeout=120000)

        await context.storage_state(path=KMC_SESSION_FILE)
        return context, browser
```

- [ ] **Step 2: Manual smoke test**

```bash
source .venv/bin/activate
python -c "
import asyncio, sys
sys.path.insert(0, 'src')
from kaltura_categorizer import KalturaCategorizer
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        ctx, browser = await KalturaCategorizer()._get_kmc_context(p)
        pages = ctx.pages
        print('KMC page count:', len(pages))
        if pages:
            print('URL:', pages[0].url)
        await browser.close()

asyncio.run(test())
"
```
Expected: URL contains `kmcng/content/entries/list`. On second run (session saved), no login prompt appears.

---

## Task 3: Backend — KMC categorize entries

**Files:**
- Modify: `src/kaltura_categorizer.py`

**Interfaces:**
- Consumes: `_get_kmc_context` from Task 2
- Produces: `KalturaCategorizer.categorize_entries(entries: list[dict], brightspace_course_id: str, log_fn: callable) -> None`

- [ ] **Step 1: Add `categorize_entries` inside `KalturaCategorizer` class**

```python
    async def categorize_entries(
        self,
        entries: list[dict],
        brightspace_course_id: str,
        log_fn,
    ) -> None:
        """For each entry: search KMC by entry ID, select, add to category."""
        async with async_playwright() as p:
            context, browser = await self._get_kmc_context(p)
            page = await context.new_page()

            for entry in entries:
                entry_id = entry["entry_id"]
                name = entry["name"]
                try:
                    await page.goto(KMC_URL, wait_until="networkidle", timeout=20000)

                    # Search by entry ID
                    search = page.locator("input[type='text']").first
                    await search.click()
                    await search.triple_click()
                    await search.type(entry_id)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(2000)

                    # Select checkbox on first result row
                    checkbox = page.locator("p-tablecheckbox .p-checkbox-box").first
                    await checkbox.click()
                    await page.wait_for_timeout(500)

                    # Open Actions dropdown
                    actions_btn = page.locator("button:has-text('Actions')").first
                    await actions_btn.click()
                    await page.wait_for_timeout(500)

                    # Add To New Category / Playlist → Add To New Category
                    await page.locator(".p-menuitem-text:has-text('Add To New Category / Playlist')").click()
                    await page.wait_for_timeout(400)
                    await page.locator(".p-menuitem-text:has-text('Add To New Category')").first.click()
                    await page.wait_for_timeout(800)

                    # Type Brightspace course ID in category search
                    cat_input = page.locator("input[placeholder*='earch']").first
                    await cat_input.click()
                    await cat_input.type(brightspace_course_id)
                    await page.wait_for_timeout(1000)

                    # Select first autocomplete result
                    await page.locator(".p-autocomplete-item, li[role='option']").first.click()
                    await page.wait_for_timeout(500)

                    # Confirm
                    confirm = page.locator("button:has-text('Apply'), button:has-text('Save'), button:has-text('OK')").first
                    await confirm.click()
                    await page.wait_for_timeout(1000)

                    log_fn(f"✓ {name}", "success")

                except Exception as e:
                    log_fn(f"✗ {name}: {e}", "error")

            await context.storage_state(path=KMC_SESSION_FILE)
            await browser.close()
```

**Note:** Selectors are best-effort from HTML snippets. After first live run, adjust any that fail — inspect with Playwright MCP browser tool if needed.

- [ ] **Step 2: Manual smoke test with one entry**

```bash
source .venv/bin/activate
python -c "
import asyncio, sys
sys.path.insert(0, 'src')
from kaltura_categorizer import KalturaCategorizer

logs = []
asyncio.run(KalturaCategorizer().categorize_entries(
    [{'entry_id': '0_e35b5e8b', 'name': '115 Nov 20 Instruments', 'moodle_url': ''}],
    '10263',
    lambda msg, tag='info': logs.append((msg, tag))
))
for l in logs:
    print(l)
"
```
Expected: `('✓ 115 Nov 20 Instruments', 'success')`. Verify in KMC that category `10263` now appears on entry `0_e35b5e8b`.

---

## Task 4: Kaltura icon

**Files:**
- Modify: `src/gui_icons.py`

**Interfaces:**
- Produces: icon key `"kaltura"` registered in `_FNS` dict

- [ ] **Step 1: Add `_kaltura` function after `_restyle` in `src/gui_icons.py`**

```python
def _kaltura(p, s, c):
    pen = QPen(c, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.1, s * 0.2, s * 0.8, s * 0.6), 3, 3)
    p.setBrush(QBrush(c))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.38, s * 0.35),
        QPointF(s * 0.38, s * 0.65),
        QPointF(s * 0.68, s * 0.50),
    ]))
```

- [ ] **Step 2: Add `"kaltura": _kaltura` to `_FNS` dict**

Change:
```python
_FNS = {
    "checker": _checker, "collect": _collect, "restyle": _restyle,
    "settings": _settings, "run": _run, "next": _next_arrow,
```
To:
```python
_FNS = {
    "checker": _checker, "collect": _collect, "restyle": _restyle,
    "kaltura": _kaltura,
    "settings": _settings, "run": _run, "next": _next_arrow,
```

- [ ] **Step 3: Visual check**

```bash
source .venv/bin/activate
python -c "
import sys
sys.path.insert(0, 'src')
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import Qt
app = QApplication(sys.argv)
from gui_icons import get_icon
lbl = QLabel()
lbl.setPixmap(get_icon('kaltura', 32, '#ffffff'))
lbl.setStyleSheet('background:#222;')
lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
lbl.resize(80, 80)
lbl.show()
app.exec()
"
```
Expected: play-button-inside-screen icon visible on dark background.

---

## Task 5: GUI Panel

**Files:**
- Create: `src/panels/kaltura_panel.py`

**Interfaces:**
- Consumes: `KalturaCategorizer.scan_moodle_course` and `categorize_entries` from Tasks 1–3
- Produces: `KalturaPanel(main_window)` — QWidget

- [ ] **Step 1: Create `src/panels/kaltura_panel.py`**

```python
import asyncio
import queue
import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QCheckBox,
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
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_log)
        self._poll_timer.start(100)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)

        layout.addWidget(_section_header("Kaltura Categorizer"))
        sub = QLabel("Scans Moodle for Kaltura videos and assigns them to a Brightspace course in KMC.")
        sub.setProperty("role", "dim")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(20)

        layout.addWidget(_form_label("MOODLE COURSE URL"))
        layout.addSpacing(4)
        self._moodle_url = QLineEdit()
        self._moodle_url.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=183744")
        self._moodle_url.setFixedHeight(40)
        layout.addWidget(self._moodle_url)
        layout.addSpacing(12)

        id_row = QHBoxLayout()
        id_col = QVBoxLayout()
        id_col.setSpacing(4)
        id_col.addWidget(_form_label("BRIGHTSPACE COURSE ID"))
        self._course_id = QLineEdit()
        self._course_id.setPlaceholderText("10263")
        self._course_id.setFixedHeight(40)
        self._course_id.setMaximumWidth(160)
        id_col.addWidget(self._course_id)
        id_row.addLayout(id_col)
        id_row.addStretch()
        self._scan_btn = QPushButton("Scan Moodle")
        self._scan_btn.setFixedHeight(40)
        self._scan_btn.clicked.connect(self._start_scan)
        id_row.addWidget(self._scan_btn)
        layout.addLayout(id_row)
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
        layout.addSpacing(12)

        self._categorize_btn = QPushButton("Categorize Selected")
        self._categorize_btn.setFixedHeight(42)
        self._categorize_btn.setEnabled(False)
        self._categorize_btn.clicked.connect(self._start_categorize)
        layout.addWidget(self._categorize_btn)
        layout.addSpacing(8)

        layout.addWidget(_form_label("LOG"))
        layout.addSpacing(4)
        self._log = LogWidget()
        layout.addWidget(self._log, 1)

    def _populate_list(self, entries: list[dict]):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._checkboxes.clear()
        for entry in entries:
            cb = QCheckBox(f"{entry['entry_id']} — {entry['name']}")
            cb.setChecked(True)
            self._list_layout.addWidget(cb)
            self._checkboxes.append((cb, entry))
        self._categorize_btn.setEnabled(bool(entries))

    def _set_all(self, checked: bool):
        for cb, _ in self._checkboxes:
            cb.setChecked(checked)

    def _selected_entries(self) -> list[dict]:
        return [entry for cb, entry in self._checkboxes if cb.isChecked()]

    def _start_scan(self):
        url = self._moodle_url.text().strip()
        if not url:
            self._log.append_log("Paste a Moodle course URL first.", "warning")
            return
        self._scan_btn.setText("Scanning…")
        self._scan_btn.setEnabled(False)
        self._categorize_btn.setEnabled(False)
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
                q.put(("__SCAN_FAIL__", []))

        threading.Thread(target=worker, daemon=True).start()

    def _start_categorize(self):
        entries = self._selected_entries()
        course_id = self._course_id.text().strip()
        if not entries:
            self._log.append_log("No videos selected.", "warning")
            return
        if not course_id:
            self._log.append_log("Enter Brightspace course ID first.", "warning")
            return
        self._categorize_btn.setText("Running…")
        self._categorize_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        q = self._log_queue

        def worker():
            try:
                from kaltura_categorizer import KalturaCategorizer
                asyncio.run(KalturaCategorizer().categorize_entries(
                    entries,
                    course_id,
                    log_fn=lambda msg, tag="info": q.put((msg, tag)),
                ))
                q.put(("__CAT_DONE__", None))
            except Exception as e:
                q.put((f"Error: {e}", "error"))
                q.put(("__CAT_DONE__", None))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_log(self):
        try:
            while True:
                msg, payload = self._log_queue.get_nowait()
                if msg == "__SCAN_DONE__":
                    entries = payload
                    self._scan_btn.setText("Scan Moodle")
                    self._scan_btn.setEnabled(True)
                    self._populate_list(entries)
                    self._log.append_log(f"Found {len(entries)} Kaltura video(s).", "success")
                elif msg == "__SCAN_FAIL__":
                    self._scan_btn.setText("Scan Moodle")
                    self._scan_btn.setEnabled(True)
                elif msg == "__CAT_DONE__":
                    self._categorize_btn.setText("Categorize Selected")
                    self._categorize_btn.setEnabled(True)
                    self._scan_btn.setEnabled(True)
                else:
                    self._log.append_log(msg, payload)
        except queue.Empty:
            pass
```

- [ ] **Step 2: Import check**

```bash
source .venv/bin/activate
python -c "
import sys
sys.path.insert(0, 'src')
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
from panels.kaltura_panel import KalturaPanel
print('Import OK')
"
```
Expected: `Import OK` with no errors.

---

## Task 6: Wire into gui.py and sidebar

**Files:**
- Modify: `gui.py`

**Interfaces:**
- Consumes: `KalturaPanel` from Task 5, `"kaltura"` icon from Task 4

- [ ] **Step 1: Update sidebar steps list in `gui.py`**

Change:
```python
        self._sidebar = Sidebar([
            (1, "checker", "Checker"),
            (2, "collect", "Collect"),
            (3, "restyle", "Restyle"),
        ])
```
To:
```python
        self._sidebar = Sidebar([
            (1, "checker", "Checker"),
            (2, "collect", "Collect"),
            (3, "restyle", "Restyle"),
            (4, "kaltura", "Kaltura"),
        ])
```

- [ ] **Step 2: Add KalturaPanel import and instantiation**

After existing panel imports (around line 79), add:
```python
        from panels.kaltura_panel import KalturaPanel
```

After `self._settings = SettingsPanel(self)`, add:
```python
        self._kaltura = KalturaPanel(self)
```

- [ ] **Step 3: Add to stack and unlock**

Change:
```python
        for panel in (self._checker, self._collector, self._restyle, self._settings):
            self._stack.addWidget(panel)  # indices 0-3
```
To:
```python
        for panel in (self._checker, self._collector, self._restyle, self._kaltura, self._settings):
            self._stack.addWidget(panel)  # indices 0-4
```

Change:
```python
        for n in (1, 2, 3):
            self._sidebar.set_step_state(n, StepButton.PENDING)
```
To:
```python
        for n in (1, 2, 3, 4):
            self._sidebar.set_step_state(n, StepButton.PENDING)
```

- [ ] **Step 4: Update `_on_step` and `_on_settings` index maps**

Change:
```python
    def _on_step(self, n: int):
        idx = {1: 0, 2: 1, 3: 2}.get(n)
```
To:
```python
    def _on_step(self, n: int):
        idx = {1: 0, 2: 1, 3: 2, 4: 3}.get(n)
```

Change:
```python
    def _on_settings(self):
        self._stack.setCurrentIndex(3)
```
To:
```python
    def _on_settings(self):
        self._stack.setCurrentIndex(4)
```

- [ ] **Step 5: Update theme refresh loop**

Change:
```python
        for panel in (self._checker, self._collector, self._restyle):
```
To:
```python
        for panel in (self._checker, self._collector, self._restyle, self._kaltura):
```

- [ ] **Step 6: Launch and verify**

```bash
source .venv/bin/activate
python gui.py
```
Expected:
- Sidebar shows 4 steps: Checker, Collect, Restyle, Kaltura
- Kaltura tab shows inputs + empty checklist + log
- Settings still works (index 4)
- Existing 3 tabs unchanged

---

## Selector Adjustment Note

The KMC Playwright selectors in Task 3 are best-effort based on the HTML snippets provided. After the Task 3 smoke test, if any step fails, use the Playwright MCP browser to navigate to KMC live and inspect the actual element. Most likely failures:
- Actions button — may need `[aria-label='Actions']` or similar
- Category search input — placeholder text may differ
- Confirm button — text may be `"Add"` instead of `"Apply"` / `"Save"` / `"OK"`
