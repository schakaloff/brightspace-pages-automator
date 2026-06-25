# content_checker.py Refactor Phase 1 — H5P Extraction Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract ~1,135 lines of H5P logic from `content_checker.py` into `src/h5p_handler.py`, with shared JS in `src/js_helpers.py`. Zero behaviour change.

**Architecture:** Standalone `H5PHandler` class receives only a `log` callable. `DEEP_FIND_JS` moves to `js_helpers.py` and is imported by both `H5PHandler` and `ContentChecker`. Two external call sites in `content_checker.py` updated to delegate to `self._h5p`.

**Tech Stack:** Python 3.x, asyncio, Playwright (already in use)

## Global Constraints

- Zero logic changes — mechanical move only
- No imports added inline; all at top of file
- `_upload_files_to_bs_module_ui` (line 1054) commented out, not deleted
- Follow existing code style (no docstrings added, no reformatting)

---

### Task 1: Create `src/js_helpers.py`

**Files:**
- Create: `src/js_helpers.py`

**Interfaces:**
- Produces: `DEEP_FIND_JS: str` — imported by Task 2 and Task 3

- [ ] **Step 1: Read the constant to copy**

Open `src/content_checker.py` lines 1010–1025. The class attribute `_DEEP_FIND_JS` is the JS string to move.

- [ ] **Step 2: Create `src/js_helpers.py`**

```python
# Shared JavaScript helpers used across multiple modules.

DEEP_FIND_JS = """
    function deepFind(root, fn, depth) {
        depth = depth === undefined ? 15 : depth;
        if (!root || depth <= 0) return null;
        var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (fn(el)) return el;
            if (el.shadowRoot) {
                var found = deepFind(el.shadowRoot, fn, depth - 1);
                if (found) return found;
            }
        }
        return null;
    }
    """
```

- [ ] **Step 3: Verify file is importable**

```bash
cd "c:/Users/300353682/OneDrive - Okanagan College/Desktop/Page Automator/brightspace-pages-automator"
venv/Scripts/python -c "from src.js_helpers import DEEP_FIND_JS; print(len(DEEP_FIND_JS), 'chars OK')"
```

Expected output: `280 chars OK` (approximately)

- [ ] **Step 4: Commit**

```bash
git add src/js_helpers.py
git commit -m "refactor: add js_helpers.py with DEEP_FIND_JS constant"
```

---

### Task 2: Create `src/h5p_handler.py`

**Files:**
- Create: `src/h5p_handler.py`
- Read: `src/content_checker.py` lines 1748–2883

**Interfaces:**
- Consumes: `DEEP_FIND_JS` from `src.js_helpers`
- Produces: `H5PHandler` class with methods listed below

- [ ] **Step 1: Create the file with class scaffold**

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from .js_helpers import DEEP_FIND_JS

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page


class H5PHandler:
    def __init__(self, log: Callable[[str, str], None]) -> None:
        self.log = log
        self._DEEP_FIND_JS = DEEP_FIND_JS
```

- [ ] **Step 2: Copy all 10 H5P methods from `content_checker.py` into `H5PHandler`**

Copy these methods verbatim (change `self` references stay — they now refer to `H5PHandler`):

| Source lines | Method name | New name on H5PHandler |
|---|---|---|
| 1748–1923 | `_enable_h5p_downloads` | `enable_downloads` |
| 1924–2073 | `_h5p_open_interactives` | `open_interactives` |
| 2073–2089 | `_find_h5p_list_frame` | `find_list_frame` |
| 2090–2250 | `_h5p_upload_one` | `upload_one` |
| 2251–2363 | `_h5p_insert_from_list` | `insert_from_list` |
| 2364–2528 | `_h5p_upload_to_cloud` | `upload_to_cloud` |
| 2529–2555 | `_h5p_insert_existing` | `insert_existing` |
| 2556–2607 | `_h5p_finalize` | `finalize` |
| 2608–2674 | `_open_editor_and_get_h5p_frame` | `open_editor_and_get_frame` |
| 2675–2883 | `_embed_h5p_in_brightspace` | `embed_in_brightspace` |

**Important:** Inside the copied methods, internal calls like `self._find_h5p_list_frame(tab)` must be updated to `self.find_list_frame(tab)`, `self._h5p_open_interactives(...)` → `self.open_interactives(...)`, etc. These are internal H5P→H5P calls — full list:

| Old internal call | New internal call |
|---|---|
| `self._find_h5p_list_frame(tab)` | `self.find_list_frame(tab)` |
| `self._h5p_open_interactives(tab, ...)` | `self.open_interactives(tab, ...)` |
| `self._open_editor_and_get_h5p_frame(...)` | `self.open_editor_and_get_frame(...)` |
| `self._h5p_upload_one(...)` | `self.upload_one(...)` |
| `self._h5p_insert_from_list(...)` | `self.insert_from_list(...)` |
| `self._h5p_finalize(...)` | `self.finalize(...)` |

Error log strings referencing old method names (e.g. `"✗ _h5p_open_interactives error"`) can stay as-is — they're log output, not code.

- [ ] **Step 3: Verify file is importable**

```bash
venv/Scripts/python -c "from src.h5p_handler import H5PHandler; print('H5PHandler OK')"
```

Expected: `H5PHandler OK`

- [ ] **Step 4: Commit**

```bash
git add src/h5p_handler.py
git commit -m "refactor: add H5PHandler class (extracted from content_checker)"
```

---

### Task 3: Wire `H5PHandler` into `ContentChecker`

**Files:**
- Modify: `src/content_checker.py`

**Interfaces:**
- Consumes: `H5PHandler` from `src.h5p_handler`, `DEEP_FIND_JS` from `src.js_helpers`

- [ ] **Step 1: Add imports at top of `content_checker.py`**

Find the existing import block (around line 1–28). Add:

```python
from .js_helpers import DEEP_FIND_JS
from .h5p_handler import H5PHandler
```

- [ ] **Step 2: Replace `_DEEP_FIND_JS` class attribute with import**

Find line ~1010:
```python
    _DEEP_FIND_JS = """
    function deepFind(root, fn, depth) {
        ...
    }
    """
```

Replace with:
```python
    _DEEP_FIND_JS = DEEP_FIND_JS
```

- [ ] **Step 3: Instantiate `H5PHandler` in `ContentChecker.__init__`**

At the end of `__init__` (after line 309, before the closing of `__init__`), add:

```python
        self._h5p = H5PHandler(self.log)
```

- [ ] **Step 4: Update the 2 external call sites**

**Call site 1** — line ~1630 (inside `_scrape_moodle`):
```python
# Old:
await self._enable_h5p_downloads(context, items)
# New:
await self._h5p.enable_downloads(context, items)
```

**Call site 2** — line ~3757 (inside `run()`):
```python
# Old:
await self._embed_h5p_in_brightspace(context, page, moodle_items, bs_flat, bs_base, course_id)
# New:
await self._h5p.embed_in_brightspace(context, page, moodle_items, bs_flat, bs_base, course_id)
```

- [ ] **Step 5: Comment out `_upload_files_to_bs_module_ui`**

Find lines 1054–1145. Add a comment block above the method:

```python
    # NOTE: Replaced by two-step API approach (_upload_file_to_brightspace +
    # _create_bs_file_topic). Kept as reference. Do not delete.
    # async def _upload_files_to_bs_module_ui(
    #     self, ...
```

Comment out the entire method body (lines 1054–1145).

- [ ] **Step 6: Remove the 10 H5P method definitions from `ContentChecker`**

Delete lines 1748–2883 from `content_checker.py` (the original H5P methods). They now live in `H5PHandler`.

- [ ] **Step 7: Verify import still works**

```bash
venv/Scripts/python -c "from src.content_checker import ContentChecker; print('ContentChecker OK')"
```

Expected: `ContentChecker OK`

- [ ] **Step 8: Verify line count dropped**

```bash
wc -l src/content_checker.py
```

Expected: ~2,640 lines (was 3,777)

- [ ] **Step 9: Commit**

```bash
git add src/content_checker.py
git commit -m "refactor: delegate H5P methods to H5PHandler, comment out dead UI upload"
```

---

## Verification

After all 3 tasks complete:

- [ ] Launch the app: `.\run.bat` — GUI opens without import errors
- [ ] Open Checker tab — no errors on load
- [ ] Check git log shows 3 clean commits

No automated tests exist for this codebase — verification is import check + app launch.
