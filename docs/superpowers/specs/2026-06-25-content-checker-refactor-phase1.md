# content_checker.py Refactor — Phase 1: H5P Extraction

**Date:** 2026-06-25
**Status:** Approved, ready for implementation

---

## Goal

`content_checker.py` is 3,777 lines. This phase extracts the H5P block (~1,135 lines) as a pilot to prove the extraction pattern before tackling remaining clusters in later phases.

---

## New Files

```
src/
  js_helpers.py        # DEEP_FIND_JS constant (and future reusable JS strings)
  h5p_handler.py       # H5PHandler class — all 10 H5P methods
  content_checker.py   # imports H5PHandler + DEEP_FIND_JS; delegates H5P calls
```

---

## H5PHandler

```python
# src/h5p_handler.py
from typing import Callable
from .js_helpers import DEEP_FIND_JS

class H5PHandler:
    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self._DEEP_FIND_JS = DEEP_FIND_JS
```

Constructed once in `ContentChecker.__init__`:
```python
self._h5p = H5PHandler(self.log)
```

---

## Method Mapping

| Old (on ContentChecker) | New (on H5PHandler) |
|---|---|
| `_enable_h5p_downloads` | `enable_downloads` |
| `_h5p_open_interactives` | `open_interactives` |
| `_h5p_upload_one` | `upload_one` |
| `_h5p_insert_from_list` | `insert_from_list` |
| `_h5p_upload_to_cloud` | `upload_to_cloud` |
| `_h5p_insert_existing` | `insert_existing` |
| `_h5p_finalize` | `finalize` |
| `_find_h5p_list_frame` | `find_list_frame` |
| `_open_editor_and_get_h5p_frame` | `open_editor_and_get_frame` |
| `_embed_h5p_in_brightspace` | `embed_in_brightspace` |

Call sites in `content_checker.py` change: `self._embed_h5p_in_brightspace(...)` → `self._h5p.embed_in_brightspace(...)`

---

## js_helpers.py

Holds `_DEEP_FIND_JS` (currently at `content_checker.py:1010`), exported as `DEEP_FIND_JS`.

Used by:
- `H5PHandler` (6 methods)
- `ContentChecker._auto_dismiss` (line 391) — stays in content_checker, imports from js_helpers

---

## Dead Code

`_upload_files_to_bs_module_ui` (line 1054) — replaced by two-step API approach (`_upload_file_to_brightspace` + `_create_bs_file_topic`). **Comment out, do not delete** — kept as reference fallback.

---

## Constraints

- Zero behaviour change — pure mechanical move
- No logic altered in any method
- All imports added at top of each file (no inline imports)

---

## Future Phases (not in scope here)

| Phase | Target | Est. lines |
|---|---|---|
| 2 | Moodle scraper | ~800 |
| 3 | File upload/download | ~400 |
| 4 | Logging/reporting | ~200 |

End state: `content_checker.py` ~500 lines (orchestration + comparison logic only).
