# Multi-Unit Auto-Continue — Design

## Goal
After Unit Collector finishes one unit, optionally auto-select the next unit
in the course and create its target page too, instead of stopping.

## New module: `src/multi_unit_selector.py`
Self-contained, same pattern as `target_page_creator.py`. `unit_collector.py`
and `target_page_creator.py` stay unchanged.

- `fetch_toc(page, course_id) -> list[dict]`
  Calls `GET /d2l/api/le/1.95/{courseId}/content/toc` (confirmed live against
  course 8520). Returns flattened top-level modules:
  `{module_id, title, sort_order, topic_count, topic_titles}`.

- `select_next_unit(modules, combined_suffix="— Combined") -> dict | None`
  Pure function, no I/O. Sort by `sort_order` ascending, drop modules with
  `topic_count == 0`, drop modules where any `topic_titles` entry contains
  `combined_suffix`. Return the first survivor, or `None` if none qualify
  (course fully processed).

- `run_multi(page, course_id, ..., confirm_fn, log) -> None`
  Loop: `fetch_toc` → `select_next_unit` → if `None`, log "course complete"
  and stop → build unit URL from `module_id` → call existing
  `unit_collector.run()` unchanged (`target_url=""`, `auto_create_target=True`)
  → on success, ask `confirm_fn` before continuing (unless auto-continue is
  on) → on failure, log it, add `module_id` to an in-memory skip-set for this
  run only, continue to next unit. Stop after 10 units (safety cap).

## Resume behavior
No progress file. `select_next_unit`'s "already has a Combined page" check
reads live Brightspace state, so re-running after hitting the cap (or a
crash) naturally skips finished units and picks up where it left off. The
in-memory failure skip-set only prevents retry-looping within one run.

## GUI wiring (`collector_panel.py`)
- New checkbox: "Continue to next unit automatically" — enables multi-unit
  mode for this run.
- New checkbox (only enabled when the above is on): "Don't ask before each
  unit" — off by default (pause-and-confirm is the default).
- Pause/confirm reuses this codebase's existing cross-thread dialog
  convention (see `checker_panel.py`'s `__CHK_CONFIRM__` handling): worker
  puts `(msg, result_ref, threading.Event)` on the queue, `_poll_log` shows a
  `QMessageBox` Yes/No on the Qt thread, sets `result_ref` + the event: the
  worker thread was blocked on `event.wait()`. Add the equivalent
  `__COL_CONFIRM__` case to `collector_panel._poll_log`.

## Confirmed live (course 8520, TIP-101)
TOC endpoint and field names verified by direct `fetch()` in an authenticated
browser tab. Dry run on that course correctly skipped "Imported Module"
(already had a "— Combined" topic) and selected "Topic 1" (id 194389) as
next. Not yet verified against a second course with a different shape.

## Out of scope
- Cross-course looping (one course per run only).
- Detecting/repairing a target page created in the wrong module.
- Any change to `unit_collector.py`'s per-unit scrape/save logic.
