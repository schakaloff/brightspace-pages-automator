# content_checker.py Refactor Phase 2 — Moodle Scraper Extraction Implementation Plan

> Use `/superpowers:executing-plans 2026-06-25-content-checker-refactor-phase2.md` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract ~800 lines of Moodle scraping logic from `content_checker.py` into `src/moodle_scraper.py`, leaving `ContentChecker` as a thin orchestrator.

**Architecture:** Standalone `MoodleScraper` class receives `log`, `confirm`, `eval_in_any_frame`, `auto_dismiss`, and credential strings in its constructor. `ContentChecker` instantiates it as `self._moodle` and delegates the two external call sites. The `_JS_MOODLE_ITEMS` constant moves to `js_helpers.py` alongside `DEEP_FIND_JS`.

**Tech Stack:** Python 3.x, asyncio, Playwright (already in use)

## Global Constraints

- Zero logic changes — mechanical move only
- No imports added inline; all at top of file
- Follow existing code style (no docstrings added, no reformatting)
- `src/` is on `sys.path` directly — use absolute imports (`from js_helpers import ...`), not relative (`from .js_helpers import ...`)

---

### Task 1: Move `_JS_MOODLE_ITEMS` to `js_helpers.py`

**Files:**
- Modify: `src/js_helpers.py`
- Modify: `src/content_checker.py` (remove definition, import from js_helpers)

**Interfaces:**
- Produces: `MOODLE_ITEMS_JS: str` — exported from `js_helpers`, imported by Task 2 and `content_checker.py`

- [ ] **Step 1: Read the constant to copy**

Open `src/content_checker.py` around line 180. The module-level `_JS_MOODLE_ITEMS` is the JS string to move. It starts with `"""() => {` and ends several lines later with `}"""`.

- [ ] **Step 2: Add `MOODLE_ITEMS_JS` to `src/js_helpers.py`**

Append to the bottom of `src/js_helpers.py` (after `DEEP_FIND_JS`):

```python
MOODLE_ITEMS_JS = """() => {
    const TYPES = {
        modtype_resource:     'FILE',
        modtype_assign:       'ASSIGN',
        modtype_quiz:         'QUIZ',
        modtype_url:          'URL',
        modtype_page:         'PAGE',
        modtype_book:         'PAGE',
        modtype_forum:        'FORUM',
        modtype_label:        'LABEL',
        modtype_folder:       'FOLDER',
        modtype_kalturamedia: 'EXTERNAL',
        modtype_kalvidres:    'VIDEO',
        modtype_lti:          'EXTERNAL',
        modtype_hvp:          'EXTERNAL',
        modtype_h5pactivity:  'EXTERNAL',
    };

    function labelInfo(activity) {
        const body = activity.querySelector(
            '.contentafterlink, .description, .no-overflow, .labelcontent, .activitybody'
        );
        if (!body) return { type: 'LABEL', name: '(empty label)' };

        const vjsEl  = body.querySelector('.video-js, [id*="videojs_"]');
        const kframe = body.querySelector('iframe[src*="kaltura"]');
        const hasVideo = !!(vjsEl || kframe);
        let entryId = '';
        let kframeTitle = '';
        if (kframe) {
            const m = (kframe.src || '').match(/entryid\\/([^\\/]+)/);
            if (m) entryId = m[1];
            kframeTitle = kframe.getAttribute('title') || '';
        }

        const rawText = body.textContent.trim().replace(/\\s+/g, ' ');
        const isOnlyNoise = /^Video Player is loading/.test(rawText);
        if (isOnlyNoise || (hasVideo && rawText.replace(/Video Player.*/, '').trim().length < 5)) {
            return { type: 'VIDEO', name: kframeTitle || (entryId ? 'Kaltura video [' + entryId + ']' : 'Kaltura Video') };
        }

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
            if (link) { const t = link.textContent.trim(); name = t.length > 2 ? t : (link.href || null); }
        }
        if (!name && cleanText.length > 2) name = cleanText.slice(0, 80) + (cleanText.length > 80 ? '…' : '');
        if (!name) name = body.querySelector('img') ? '(image)' : '(empty label)';
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
                type = info.type; name = info.name;
            } else {
                const nameEl = activity.querySelector('.instancename, .activityname a, a');
                name = nameEl ? nameEl.textContent.trim().replace(/\\s{2,}.*$/, '').trim() : '(unnamed)';
            }
            result.push({ type, name, href, hint: matched });
        });
    });
    return result;
}"""
```

- [ ] **Step 3: Update `content_checker.py` imports**

At the top of `src/content_checker.py`, find:
```python
from js_helpers import DEEP_FIND_JS
from h5p_handler import H5PHandler
```
Change to:
```python
from js_helpers import DEEP_FIND_JS, MOODLE_ITEMS_JS
from h5p_handler import H5PHandler
```

- [ ] **Step 4: Replace `_JS_MOODLE_ITEMS` usage in `content_checker.py`**

Find all uses of `_JS_MOODLE_ITEMS` in `content_checker.py` (should be 1–2 occurrences inside `_scrape_moodle`) and replace with `MOODLE_ITEMS_JS`.

Then delete the `_JS_MOODLE_ITEMS = """..."""` definition from `content_checker.py` (the module-level constant around line 180).

- [ ] **Step 5: Verify import**

```
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); from js_helpers import MOODLE_ITEMS_JS; print(len(MOODLE_ITEMS_JS), 'chars OK')"
```

Expected: `1400 chars OK` (approximately)

- [ ] **Step 6: Commit**

```bash
git add src/js_helpers.py src/content_checker.py
git commit -m "refactor: move _JS_MOODLE_ITEMS to js_helpers.py as MOODLE_ITEMS_JS"
```

---

### Task 2: Create `src/moodle_scraper.py`

**Files:**
- Create: `src/moodle_scraper.py`
- Read: `src/content_checker.py` lines 1354–2467 (6 methods to copy)

**Interfaces:**
- Consumes: `MOODLE_ITEMS_JS` from `js_helpers`
- Produces: `MoodleScraper` class with methods listed below

| Source method | New name on MoodleScraper |
|---|---|
| `_scrape_moodle` | `scrape` |
| `_download_moodle_files` | `download_files` |
| `_switch_to_instructor_role` | `switch_to_instructor_role` |
| `_scan_moodle_labels_inline` | `scan_labels_inline` |
| `_scan_moodle_page_bodies` | `scan_page_bodies` |
| `_scan_moodle_folders` | `scan_folders` |
| `_enrich_kaltura_titles` | `enrich_kaltura_titles` |

**Internal call rewrites** (calls within these methods to each other):

| Old internal call | New internal call |
|---|---|
| `self._scan_moodle_labels_inline(tab, items)` | `self.scan_labels_inline(tab, items)` |
| `self._scan_moodle_page_bodies(tab, items)` | `self.scan_page_bodies(tab, items)` |
| `self._scan_moodle_folders(tab, items)` | `self.scan_folders(tab, items)` |
| `self._enrich_kaltura_titles(tab.context, ...)` | `self.enrich_kaltura_titles(tab.context, ...)` |
| `self._switch_to_instructor_role(tab)` | `self.switch_to_instructor_role(tab)` |
| `self._download_moodle_files(tab, items)` | `self.download_files(tab, items)` |

**Cross-class calls** that stay as injected callables (passed via constructor):
- `self._h5p.enable_downloads(context, items)` → `self._h5p_enable_downloads(context, items)` (injected)
- `self._confirm(msg)` → `self._confirm(msg)` (injected)
- `self.on_h5p_waiting()` → `self._on_h5p_waiting()` (injected)
- `self.h5p_ready_event` → `self._h5p_ready_event` (injected)
- `self.h5p_skip_flag` → `self._h5p_skip_flag` (injected ref)
- `self._eval_in_any_frame(tab, js)` → `self._eval_in_any_frame(tab, js)` (injected)

- [ ] **Step 1: Create the file scaffold**

```python
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from js_helpers import MOODLE_ITEMS_JS

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext


class MoodleScraper:
    def __init__(
        self,
        log: Callable[[str, str], None],
        confirm: Callable,
        eval_in_any_frame: Callable,
        moodle_url: str,
        moodle_username: str,
        moodle_password: str,
        sso_email: str,
        sso_password: str,
        h5p_enable_downloads: Callable,
        on_h5p_waiting: Optional[Callable],
        h5p_ready_event,
        h5p_skip_flag,
        summary: dict,
    ) -> None:
        self.log = log
        self._confirm = confirm
        self._eval_in_any_frame = eval_in_any_frame
        self.moodle_url = moodle_url
        self.moodle_username = moodle_username
        self.moodle_password = moodle_password
        self.sso_email = sso_email
        self.sso_password = sso_password
        self._h5p_enable_downloads = h5p_enable_downloads
        self.on_h5p_waiting = on_h5p_waiting
        self.h5p_ready_event = h5p_ready_event
        self.h5p_skip_flag = h5p_skip_flag
        self._summary = summary
```

- [ ] **Step 2: Copy all 7 methods into `MoodleScraper`**

Copy verbatim from `content_checker.py`. Apply the internal call rewrites from the table above. Constructor attributes map:

| Old `self.xxx` | New `self.xxx` |
|---|---|
| `self.moodle_url` | `self.moodle_url` (same) |
| `self.moodle_username` | `self.moodle_username` (same) |
| `self.moodle_password` | `self.moodle_password` (same) |
| `self.sso_password` | `self.sso_password` (same) |
| `self.sso_email` | `self.sso_email` (same) |
| `self.on_h5p_waiting` | `self.on_h5p_waiting` (same) |
| `self.h5p_ready_event` | `self.h5p_ready_event` (same) |
| `self.h5p_skip_flag` | `self.h5p_skip_flag` (same) |
| `self._h5p.enable_downloads(context, items)` | `await self._h5p_enable_downloads(context, items)` |
| `_JS_MOODLE_ITEMS` or `MOODLE_ITEMS_JS` | `MOODLE_ITEMS_JS` |

- [ ] **Step 3: Verify file is importable**

```
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); from moodle_scraper import MoodleScraper; print('MoodleScraper OK')"
```

Expected: `MoodleScraper OK`

- [ ] **Step 4: Commit**

```bash
git add src/moodle_scraper.py
git commit -m "refactor: add MoodleScraper class (extracted from content_checker)"
```

---

### Task 3: Wire `MoodleScraper` into `ContentChecker`

**Files:**
- Modify: `src/content_checker.py`

**Interfaces:**
- Consumes: `MoodleScraper` from `moodle_scraper`

- [ ] **Step 1: Add import at top of `content_checker.py`**

Find:
```python
from js_helpers import DEEP_FIND_JS, MOODLE_ITEMS_JS
from h5p_handler import H5PHandler
```
Change to:
```python
from js_helpers import DEEP_FIND_JS, MOODLE_ITEMS_JS
from h5p_handler import H5PHandler
from moodle_scraper import MoodleScraper
```

- [ ] **Step 2: Instantiate `MoodleScraper` in `ContentChecker.__init__`**

After the `self._h5p = H5PHandler(...)` block, add:

```python
        self._moodle = MoodleScraper(
            log=self.log,
            confirm=self._confirm,
            eval_in_any_frame=self._eval_in_any_frame,
            moodle_url=self.moodle_url,
            moodle_username=self.moodle_username,
            moodle_password=self.moodle_password,
            sso_email=self.sso_email,
            sso_password=self.sso_password,
            h5p_enable_downloads=self._h5p.enable_downloads,
            on_h5p_waiting=self.on_h5p_waiting,
            h5p_ready_event=self.h5p_ready_event,
            h5p_skip_flag=None,
            summary=self._summary,
        )
```

- [ ] **Step 3: Update the external call site in `run()`**

Find the one call site in `run()` that calls `await self._scrape_moodle(context)` and change it to:

```python
await self._moodle.scrape(context)
```

- [ ] **Step 4: Sync `_summary` ref after `run()` re-initialises it**

In `run()`, after the line `self._summary = { ... }` and the existing `self._h5p._summary = self._summary`, add:

```python
        self._moodle._summary = self._summary
```

- [ ] **Step 5: Remove the 7 Moodle method definitions from `ContentChecker`**

Delete these methods from `content_checker.py` (they now live in `MoodleScraper`):
- `_scrape_moodle` (and its inner `_click_manual_login` / `_handle_microsoft_sso` closures)
- `_download_moodle_files`
- `_switch_to_instructor_role`
- `_scan_moodle_labels_inline`
- `_scan_moodle_page_bodies`
- `_scan_moodle_folders`
- `_enrich_kaltura_titles`

Also remove the now-unused `MOODLE_ITEMS_JS` import if nothing else in `content_checker.py` uses it after the deletion.

- [ ] **Step 6: Verify import**

```
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); from content_checker import ContentChecker; print('ContentChecker OK')"
```

Expected: `ContentChecker OK`

- [ ] **Step 7: Verify line count dropped**

```
(Get-Content src/content_checker.py).Count
```

Expected: ~1,800 lines (was ~2,645)

- [ ] **Step 8: Commit**

```bash
git add src/content_checker.py
git commit -m "refactor: delegate Moodle scraper methods to MoodleScraper"
```

---

## Verification

After all 3 tasks complete:

- [ ] Launch the app: `.\run.bat` — GUI opens without import errors
- [ ] Open Checker tab — no errors on load
- [ ] Git log shows 3 clean commits

No automated tests exist for this codebase — verification is import check + app launch.
