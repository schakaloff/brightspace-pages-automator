# Multi-Unit Auto-Continue — Design

## Goal
After Unit Collector finishes one unit, optionally auto-select the next unit
in the course and create its target page too, instead of stopping.

## New module: `src/multi_unit_selector.py`
Self-contained, same pattern as `target_page_creator.py`. `target_page_creator.py`
stays unchanged. `unit_collector.py` gets a small, additive change — see
"One-session decision" below; this was not the original plan.

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
  and stop → build unit URL from `module_id` → call `unit_collector.run()`
  with a shared `context`/`page` (see below) → on success (`True`), ask
  `confirm_fn` before continuing (unless auto-continue is on) → **on failure
  (`False` or a raised exception), log the failed module's title and
  `module_id`, stop the whole multi-unit run immediately** (no skip, no
  continue — user decides what to do next). Stop after 10 units (safety cap).

## One-session decision (reverses the original "unit_collector.py unchanged" call)
Originally scoped as one full browser launch + login per unit, calling
`unit_collector.run()` completely untouched. Rejected after review: every
non-exception exit of `run()` — success and both early-return dead-ends —
ends in `while browser.is_connected(): await asyncio.sleep(0.5)`, which
blocks until a human manually closes that unit's browser window
(`unit_collector.py:1203-1204, 1215-1216, 1301-1302`). That's fine for one
person watching one run; it means multi-unit mode would hang after every
single unit waiting on an unattended window. Per-unit browser launches also
add real (if secondary) overhead — 5-12s of Chromium startup + session
re-verification per unit, N times.

Chosen fix instead of working around the hang from outside: `UnitCollector
.run()` (and the module-level `run(...)` wrapper) gain two optional
parameters, `context` and `page`. When both are supplied:
- Skip `launch_browser()` and `wait_for_login()` — the caller already did both.
- Skip all three `while browser.is_connected()` hang loops — nothing to wait
  on when the browser is shared, so the call just returns.
- Skip closing the browser / stopping Playwright in `finally` — the caller
  owns that lifecycle, once, for the whole multi-unit run.

When `context`/`page` are omitted (every existing call site), `run()`
behaves exactly as before — same launch, same login, same hang-until-closed
review pause. This keeps single-unit Collector behavior unchanged.

`run()` also changes its return value from always-`None` to `bool`: `True`
on a normal finish, `False` for the two known dead-ends ("no target page,"
"no topics found"). Existing callers already discard the return value, so
this is invisible to them, but it gives `multi_unit_selector.run_unit`
(passed into `run_multi`) a real success/failure signal instead of having
to pattern-match log text. Per-topic/per-file errors that `run()` already
tolerates and logs as `"error"` (one topic among several failing to scrape,
one file failing to insert) still do **not** count as unit failure — only
the two whole-unit dead-ends and an actually-raised exception do. This is a
deliberate, narrower failure definition than "any error-tagged log," chosen
so multi-unit mode doesn't stop on things the tool already shrugs off in
single-unit mode.

`collector_panel.py`'s `_run_multi_unit` launches one browser/context/page
for the entire multi-unit run (login once), uses it for both TOC fetches
and every unit's `unit_collector.run(context=..., page=..., ...)` call, and
is the single place that browser ever closes — in its own outer `finally`,
after the whole loop ends (success, cap, decline, or failure).

## Resume behavior
No progress file. `select_next_unit`'s "already has a Combined page" check
reads live Brightspace state, so re-running after a stop (cap hit, failure,
or crash) naturally skips finished units and picks up where it left off.

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
- Any change to `unit_collector.py`'s per-unit scrape/save logic (Phases
  1-3: topic scraping, section assembly, save-and-close). Only the browser
  lifecycle (launch/login/hang/cleanup) and the return value change.
