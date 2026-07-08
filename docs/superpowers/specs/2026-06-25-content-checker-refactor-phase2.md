# content_checker.py Refactor — Phase 2: Moodle Scraper Extraction

**Date:** 2026-06-25
**Status:** Approved, ready for implementation

---

## Goal

`content_checker.py` is ~2,645 lines after Phase 1. This phase extracts the Moodle scraping block (~800 lines) into `src/moodle_scraper.py`.

---

## New Files

```
src/
  js_helpers.py        # gains MOODLE_ITEMS_JS (moved from content_checker)
  moodle_scraper.py    # MoodleScraper class — 7 Moodle methods
  content_checker.py   # imports MoodleScraper; delegates Moodle scrape call
```

---

## MoodleScraper

```python
# src/moodle_scraper.py
class MoodleScraper:
    def __init__(
        self,
        log,
        confirm,
        eval_in_any_frame,
        moodle_url, moodle_username, moodle_password,
        sso_email, sso_password,
        h5p_enable_downloads,
        on_h5p_waiting, h5p_ready_event, h5p_skip_flag,
        summary,
    ): ...
```

Constructed once in `ContentChecker.__init__` as `self._moodle`.

---

## Method Mapping

| Old (on ContentChecker) | New (on MoodleScraper) |
|---|---|
| `_scrape_moodle` | `scrape` |
| `_download_moodle_files` | `download_files` |
| `_switch_to_instructor_role` | `switch_to_instructor_role` |
| `_scan_moodle_labels_inline` | `scan_labels_inline` |
| `_scan_moodle_page_bodies` | `scan_page_bodies` |
| `_scan_moodle_folders` | `scan_folders` |
| `_enrich_kaltura_titles` | `enrich_kaltura_titles` |

External call site in `run()`: `await self._scrape_moodle(context)` → `await self._moodle.scrape(context)`

---

## Constraints

- Zero behaviour change — pure mechanical move
- No logic altered in any method
- Absolute imports only (`from js_helpers import ...`)

---

## Future Phases (not in scope here)

| Phase | Target | Est. lines |
|---|---|---|
| 3 | File upload/download | ~400 |
| 4 | Logging/reporting | ~200 |

End state: `content_checker.py` ~500 lines (orchestration + comparison logic only).
