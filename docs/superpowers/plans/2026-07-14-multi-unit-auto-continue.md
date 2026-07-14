# Multi-Unit Auto-Continue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After Unit Collector finishes a unit, optionally auto-select the next real unit in the course and create its target page too, instead of stopping after one.

**Architecture:** New standalone module `src/multi_unit_selector.py` owns the D2L course-TOC fetch, the "which unit is next" rule, and the loop/stop-on-failure orchestration. `src/unit_collector.py` and `src/target_page_creator.py` are not modified — the new module calls `unit_collector.run()` once per selected unit exactly as `collector_panel.py` does today. `src/panels/collector_panel.py` adds two checkboxes and wires the loop in.

**Tech Stack:** Python 3.11, Playwright (async), PySide6, pytest, pytest-qt.

## Global Constraints

- No progress file — Brightspace's own state (a topic titled ending in "— Combined" inside a module) is the resume checkpoint. Confirmed in spec.
- On any unit failure, stop the whole multi-unit run immediately and log the failed module's title + `module_id`. No skip-and-continue in v1. Confirmed in spec review.
- Pause-and-confirm before each additional unit is the default; a "don't ask" checkbox opts into silent auto-continue.
- Safety cap: 10 units per run. Re-running after the cap (or after a stop) naturally continues where it left off.
- Reuse the D2L TOC endpoint and field names already confirmed live against course 8520: `GET /d2l/api/le/1.95/{courseId}/content/toc` → `{"Modules": [{"ModuleId", "Title", "SortOrder", "Topics": [{"Title", ...}], ...}]}`.

---

### Task 1: `multi_unit_selector.py` — selection rule + TOC fetch + loop

**Files:**
- Create: `src/multi_unit_selector.py`
- Test: `tests/test_multi_unit_selector.py`

**Interfaces:**
- Produces: `select_next_unit(modules: list[dict], combined_suffix: str = "— Combined") -> dict | None`
- Produces: `_flatten_modules(raw_modules: list[dict]) -> list[dict]` — each flat dict has keys `module_id` (int), `title` (str), `sort_order` (int), `topic_count` (int), `topic_titles` (list[str])
- Produces: `async fetch_toc(page, course_id: str, log: Callable | None = None) -> list[dict]` — returns the flattened list, or `[]` on any failure
- Produces: `async run_multi(page, course_id: str, base_url: str, run_unit: Callable[[str], Awaitable[bool]], confirm_fn: Callable[[str], bool], log: Callable | None = None, max_units: int = 10, combined_suffix: str = "— Combined", fetch_toc_fn: Callable | None = None) -> dict` — returns `{"processed": list[dict], "stopped_reason": str, "failed_unit": dict | None}`. `stopped_reason` is one of `"complete"`, `"cap"`, `"failure"`, `"declined"`.
- Consumes: nothing from other new files (Task 2 consumes this task's output).

- [ ] **Step 1: Write failing tests for `select_next_unit`**

Create `tests/test_multi_unit_selector.py`:

```python
import sys
sys.path.insert(0, "src")

from multi_unit_selector import select_next_unit, _flatten_modules


def _mod(module_id, title, sort_order, topic_titles):
    return {
        "module_id": module_id,
        "title": title,
        "sort_order": sort_order,
        "topic_count": len(topic_titles),
        "topic_titles": topic_titles,
    }


def test_select_next_unit_skips_already_combined():
    modules = [
        _mod(1, "Imported Module", -50, ["Meet Your Facilitators", "Imported Module — Combined"]),
        _mod(2, "Topic 1", 4, ["Welcome to Module 1"]),
    ]
    result = select_next_unit(modules)
    assert result["module_id"] == 2


def test_select_next_unit_skips_empty_modules():
    modules = [
        _mod(1, "Topic 6", 326, []),
        _mod(2, "Topic 1", 4, ["Welcome to Module 1"]),
    ]
    result = select_next_unit(modules)
    assert result["module_id"] == 2


def test_select_next_unit_orders_by_sort_order_not_list_order():
    modules = [
        _mod(2, "Topic 2", 58, ["Watch This"]),
        _mod(1, "Topic 1", 4, ["Welcome to Module 1"]),
    ]
    result = select_next_unit(modules)
    assert result["module_id"] == 1


def test_select_next_unit_returns_none_when_all_done_or_empty():
    modules = [
        _mod(1, "Imported Module", -50, ["Imported Module — Combined"]),
        _mod(2, "Topic 6", 326, []),
    ]
    assert select_next_unit(modules) is None


def test_select_next_unit_empty_list():
    assert select_next_unit([]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_multi_unit_selector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'multi_unit_selector'`

- [ ] **Step 3: Implement `select_next_unit`**

Create `src/multi_unit_selector.py`:

```python
"""
Multi-unit auto-continue for the Unit Collector.

FULLY SELF-CONTAINED and OPTIONAL, same pattern as target_page_creator.py.
Neither unit_collector.py nor target_page_creator.py import from this file.
To rip the feature out completely:
  1. Delete this file.
  2. Delete the "Continue to next unit automatically" / "Don't ask before
     each unit" checkboxes and their wiring in src/panels/collector_panel.py.

How "next unit" is chosen:
  - GET /d2l/api/le/1.95/{courseId}/content/toc returns every top-level
    module in the course with its SortOrder and Topics.
  - Walk modules in SortOrder order. Skip any with zero topics (empty
    placeholder units). Skip any that already contain a topic titled
    "<something> — Combined" (a prior run already finished it). The first
    module that survives both filters is "next".
  - No separate progress file: a module's own "— Combined" topic, visible
    to this same TOC call, IS the record of what's already done. Re-running
    after a stop (cap, failure, or crash) re-derives from live Brightspace
    state and continues where it left off.

Field names confirmed live against a real course (2026-07-14):
  Module: ModuleId, Title, SortOrder, Topics (list)
  Topic:  Title
"""

from typing import Awaitable, Callable, Optional

from playwright.async_api import Page


def _flatten_modules(raw_modules: list) -> list:
    """Pure. Convert raw D2L TOC module dicts into the flat shape select_next_unit expects."""
    flat = []
    for m in raw_modules:
        topics = m.get("Topics") or []
        flat.append({
            "module_id": m["ModuleId"],
            "title": m.get("Title") or "",
            "sort_order": m.get("SortOrder", 0),
            "topic_count": len(topics),
            "topic_titles": [t.get("Title") or "" for t in topics],
        })
    return flat


def select_next_unit(modules: list, combined_suffix: str = "— Combined") -> Optional[dict]:
    """Pure. Sort by sort_order, skip empty modules, skip modules already
    containing a "<title> — Combined" topic. Return the first survivor, or
    None if the course has no more units to process."""
    for m in sorted(modules, key=lambda m: m["sort_order"]):
        if m["topic_count"] == 0:
            continue
        if any(combined_suffix in t for t in m["topic_titles"]):
            continue
        return m
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_multi_unit_selector.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/multi_unit_selector.py tests/test_multi_unit_selector.py
git commit -m "feat: add select_next_unit rule for multi-unit auto-continue"
```

- [ ] **Step 6: Write failing test for `_flatten_modules` using the real captured TOC shape**

Append to `tests/test_multi_unit_selector.py`:

```python
def test_flatten_modules_matches_live_toc_shape():
    # Shape captured live from GET /d2l/api/le/1.95/8520/content/toc (2026-07-14)
    raw = [
        {
            "ModuleId": 194381, "Title": "Imported Module", "SortOrder": -50,
            "Topics": [
                {"Title": "Question Forum"},
                {"Title": "Imported Module — Combined"},
            ],
        },
        {
            "ModuleId": 194430, "Title": "Topic 6", "SortOrder": 326,
            "Topics": [],
        },
    ]
    flat = _flatten_modules(raw)
    assert flat[0] == {
        "module_id": 194381, "title": "Imported Module", "sort_order": -50,
        "topic_count": 2,
        "topic_titles": ["Question Forum", "Imported Module — Combined"],
    }
    assert flat[1] == {
        "module_id": 194430, "title": "Topic 6", "sort_order": 326,
        "topic_count": 0, "topic_titles": [],
    }
```

- [ ] **Step 7: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_multi_unit_selector.py::test_flatten_modules_matches_live_toc_shape -v`
Expected: FAIL — `_flatten_modules` not defined (not yet exported/tested standalone — it already exists from Step 3, so expect this to actually PASS immediately; if it does, skip to Step 9)

- [ ] **Step 8: Fix `_flatten_modules` if Step 7 failed**

Only needed if Step 7 showed a real mismatch. Adjust the field mapping in `_flatten_modules` (Step 3's code) to match, then re-run.

- [ ] **Step 9: Run full file, commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_multi_unit_selector.py -v`
Expected: 6 passed

```bash
git add tests/test_multi_unit_selector.py
git commit -m "test: pin _flatten_modules to the live-confirmed D2L TOC shape"
```

- [ ] **Step 10: Write failing test for `fetch_toc`**

Append to `tests/test_multi_unit_selector.py`:

```python
import asyncio
from multi_unit_selector import fetch_toc


class _FakePage:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def evaluate(self, js, args):
        self.calls.append(args)
        return self._result


def test_fetch_toc_flattens_successful_response():
    page = _FakePage({"ok": True, "modules": [
        {"ModuleId": 1, "Title": "Topic 1", "SortOrder": 4, "Topics": [{"Title": "Welcome"}]},
    ]})

    async def _run():
        return await fetch_toc(page, "8520")

    result = asyncio.run(_run())
    assert result == [{
        "module_id": 1, "title": "Topic 1", "sort_order": 4,
        "topic_count": 1, "topic_titles": ["Welcome"],
    }]
    assert page.calls == [["8520"]]


def test_fetch_toc_returns_empty_list_on_failure():
    page = _FakePage({"ok": False, "reason": "http-404"})

    async def _run():
        return await fetch_toc(page, "8520")

    assert asyncio.run(_run()) == []


def test_fetch_toc_returns_empty_list_on_exception():
    class _RaisingPage:
        async def evaluate(self, js, args):
            raise RuntimeError("boom")

    async def _run():
        return await fetch_toc(_RaisingPage(), "8520")

    assert asyncio.run(_run()) == []
```

- [ ] **Step 11: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_multi_unit_selector.py -v -k fetch_toc`
Expected: FAIL — `ImportError: cannot import name 'fetch_toc'`

- [ ] **Step 12: Implement `fetch_toc`**

Append to `src/multi_unit_selector.py`:

```python
_JS_FETCH_TOC = r"""async ([courseId]) => {
    try {
        const r = await fetch(
            `/d2l/api/le/1.95/${courseId}/content/toc`,
            { credentials: 'include', headers: { 'Accept': 'application/json' } });
        if (!r.ok) return { ok: false, reason: 'http-' + r.status };
        const data = await r.json();
        return { ok: true, modules: data.Modules || [] };
    } catch (e) { return { ok: false, reason: 'fetch-failed: ' + e }; }
}"""


async def fetch_toc(page: Page, course_id: str, log: Optional[Callable] = None) -> list:
    """Fetch and flatten this course's top-level modules. Never raises —
    returns [] on any failure so the caller can stop the multi-unit run."""
    def _log(msg: str, level: str = "info"):
        if log:
            log(msg, level)

    try:
        result = await page.evaluate(_JS_FETCH_TOC, [course_id])
    except Exception as e:
        _log(f"✗ Could not fetch course structure: {e}", "error")
        return []

    if not result or not result.get("ok"):
        _log(f"✗ Course structure fetch failed ({(result or {}).get('reason', 'unknown')})", "error")
        return []

    return _flatten_modules(result["modules"])
```

- [ ] **Step 13: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_multi_unit_selector.py -v`
Expected: 9 passed

- [ ] **Step 14: Commit**

```bash
git add src/multi_unit_selector.py tests/test_multi_unit_selector.py
git commit -m "feat: add fetch_toc glue for multi-unit auto-continue"
```

- [ ] **Step 15: Write failing tests for `run_multi`'s four stop reasons**

Append to `tests/test_multi_unit_selector.py`:

```python
from multi_unit_selector import run_multi


def _make_fetch_toc_fn(snapshots):
    """Returns a fetch_toc_fn that yields each snapshot in order, then repeats the last one."""
    calls = {"n": 0}

    async def _fetch(page, course_id):
        i = min(calls["n"], len(snapshots) - 1)
        calls["n"] += 1
        return snapshots[i]

    return _fetch


def test_run_multi_stops_when_course_complete():
    snapshots = [[]]  # no modules at all -> select_next_unit is always None
    fetch_toc_fn = _make_fetch_toc_fn(snapshots)

    async def run_unit(unit_url):
        raise AssertionError("should never be called")

    async def _run():
        return await run_multi(
            page=None, course_id="8520", base_url="https://learn.okanagancollege.ca",
            run_unit=run_unit, confirm_fn=lambda msg: True,
            fetch_toc_fn=fetch_toc_fn,
        )

    summary = asyncio.run(_run())
    assert summary == {"processed": [], "stopped_reason": "complete", "failed_unit": None}


def test_run_multi_stops_on_unit_failure_and_reports_it():
    mod = {"module_id": 2, "title": "Topic 1", "sort_order": 4,
           "topic_count": 1, "topic_titles": ["Welcome"]}
    fetch_toc_fn = _make_fetch_toc_fn([[mod]])  # never becomes "combined" -> would loop forever if not stopped

    async def run_unit(unit_url):
        return False

    async def _run():
        return await run_multi(
            page=None, course_id="8520", base_url="https://learn.okanagancollege.ca",
            run_unit=run_unit, confirm_fn=lambda msg: True,
            fetch_toc_fn=fetch_toc_fn,
        )

    summary = asyncio.run(_run())
    assert summary["stopped_reason"] == "failure"
    assert summary["failed_unit"]["module_id"] == 2
    assert summary["processed"] == []


def test_run_multi_stops_when_run_unit_raises():
    mod = {"module_id": 2, "title": "Topic 1", "sort_order": 4,
           "topic_count": 1, "topic_titles": ["Welcome"]}
    fetch_toc_fn = _make_fetch_toc_fn([[mod]])

    async def run_unit(unit_url):
        raise RuntimeError("browser crashed")

    async def _run():
        return await run_multi(
            page=None, course_id="8520", base_url="https://learn.okanagancollege.ca",
            run_unit=run_unit, confirm_fn=lambda msg: True,
            fetch_toc_fn=fetch_toc_fn,
        )

    summary = asyncio.run(_run())
    assert summary["stopped_reason"] == "failure"
    assert summary["failed_unit"]["module_id"] == 2


def test_run_multi_stops_when_user_declines_to_continue():
    mod1 = {"module_id": 1, "title": "Topic 1", "sort_order": 4,
            "topic_count": 1, "topic_titles": ["Welcome"]}
    mod1_done = {**mod1, "topic_titles": ["Welcome", "Topic 1 — Combined"]}
    mod2 = {"module_id": 2, "title": "Topic 2", "sort_order": 58,
            "topic_count": 1, "topic_titles": ["Watch This"]}
    # First fetch sees mod1 + mod2, both undone. After mod1's run_unit
    # "succeeds", the second fetch reflects mod1 now being combined.
    fetch_toc_fn = _make_fetch_toc_fn([[mod1, mod2], [mod1_done, mod2]])

    async def run_unit(unit_url):
        return True

    async def _run():
        return await run_multi(
            page=None, course_id="8520", base_url="https://learn.okanagancollege.ca",
            run_unit=run_unit, confirm_fn=lambda msg: False,
            fetch_toc_fn=fetch_toc_fn,
        )

    summary = asyncio.run(_run())
    assert summary["stopped_reason"] == "declined"
    assert [m["module_id"] for m in summary["processed"]] == [1]


def test_run_multi_stops_at_safety_cap():
    # A single unit that is never marked "combined" between fetches, so
    # select_next_unit would keep re-selecting it forever without the cap.
    mod = {"module_id": 1, "title": "Topic 1", "sort_order": 4,
           "topic_count": 1, "topic_titles": ["Welcome"]}
    fetch_toc_fn = _make_fetch_toc_fn([[mod]])

    async def run_unit(unit_url):
        return True

    async def _run():
        return await run_multi(
            page=None, course_id="8520", base_url="https://learn.okanagancollege.ca",
            run_unit=run_unit, confirm_fn=lambda msg: True,
            fetch_toc_fn=fetch_toc_fn, max_units=3,
        )

    summary = asyncio.run(_run())
    assert summary["stopped_reason"] == "cap"
    assert len(summary["processed"]) == 3
```

- [ ] **Step 16: Run to verify these fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_multi_unit_selector.py -v -k run_multi`
Expected: FAIL — `ImportError: cannot import name 'run_multi'`

- [ ] **Step 17: Implement `run_multi`**

Append to `src/multi_unit_selector.py`:

```python
async def run_multi(
    page: Page,
    course_id: str,
    base_url: str,
    run_unit: Callable[[str], Awaitable[bool]],
    confirm_fn: Callable[[str], bool],
    log: Optional[Callable] = None,
    max_units: int = 10,
    combined_suffix: str = "— Combined",
    fetch_toc_fn: Optional[Callable] = None,
) -> dict:
    """Loop: fetch the course TOC, pick the next unit, run it, ask to
    continue. Stops immediately on any unit failure (v1: no skip-and-continue
    — the failed module is reported so the user can decide what to do next).
    No progress file: re-calling this after any stop naturally resumes,
    because select_next_unit re-derives "next" from live Brightspace state.
    """
    def _log(msg: str, level: str = "info"):
        if log:
            log(msg, level)

    fetch = fetch_toc_fn or fetch_toc
    processed: list = []
    failed_unit = None
    stopped_reason = "complete"

    while True:
        modules = await fetch(page, course_id)
        next_unit = select_next_unit(modules, combined_suffix)

        if next_unit is None:
            stopped_reason = "complete"
            _log("✓ No more units to process — course complete", "success")
            break

        unit_url = f"{base_url}/d2l/le/lessons/{course_id}/units/{next_unit['module_id']}"
        _log(f"─── Next unit: {next_unit['title']} ───", "info")

        try:
            ok = await run_unit(unit_url)
        except Exception as e:
            ok = False
            _log(f"✗ Unit '{next_unit['title']}' raised an error: {e}", "error")

        if not ok:
            failed_unit = next_unit
            stopped_reason = "failure"
            _log(
                f"✗ Stopped: unit '{next_unit['title']}' "
                f"(module {next_unit['module_id']}) failed", "error",
            )
            break

        processed.append(next_unit)
        _log(f"✓ Unit '{next_unit['title']}' done ({len(processed)}/{max_units})", "success")

        if len(processed) >= max_units:
            stopped_reason = "cap"
            _log(f"⚠ Reached the {max_units}-unit safety cap — stopping", "warning")
            break

        if not confirm_fn(f"Unit '{next_unit['title']}' done. Continue to the next unit?"):
            stopped_reason = "declined"
            _log("Stopped — you chose not to continue", "info")
            break

    return {"processed": processed, "stopped_reason": stopped_reason, "failed_unit": failed_unit}
```

- [ ] **Step 18: Run to verify all tests pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_multi_unit_selector.py -v`
Expected: 14 passed

- [ ] **Step 19: Commit**

```bash
git add src/multi_unit_selector.py tests/test_multi_unit_selector.py
git commit -m "feat: add run_multi orchestration loop (stop-on-failure, safety cap)"
```

---

### Task 2: Wire multi-unit mode into `collector_panel.py`

**Files:**
- Modify: `src/panels/collector_panel.py`
- Test: `tests/test_gui_panels.py`

**Interfaces:**
- Consumes from Task 1: `multi_unit_selector.run_multi(page, course_id, base_url, run_unit, confirm_fn, log=None, max_units=10, combined_suffix="— Combined", fetch_toc_fn=None) -> dict`
- Consumes: `target_page_creator._parse_ids(unit_url: str) -> tuple[str | None, str | None]` (existing, reused for `course_id`)
- Consumes: `browser.launch_browser() -> (playwright, browser, context, page)` and `browser.wait_for_login(page, context, username, password, sso_email, sso_password) -> None` (existing)
- Consumes: `unit_collector.run(unit_url, target_url, theme_name, theme_colors, ..., log, on_complete, ...) -> None` (existing, unchanged, may raise on catastrophic failure)

**Known v1 nuance:** most per-topic scrape problems inside `unit_collector.run()` are only logged (`"error"` tag) and don't raise — they don't stop that unit. `run_unit`'s True/False signal below only catches exceptions that propagate out of `unit_collector.run()` itself (browser/session-level failures). This matches what "success" already means for a single-unit run today; it is not changed by this task.

- [ ] **Step 1: Write failing test — new checkboxes exist with correct default state**

Add to `tests/test_gui_panels.py`:

```python
def test_collector_panel_has_multi_unit_checkboxes(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    assert panel._multi_unit_chk.isChecked() is False
    assert panel._auto_continue_chk.isChecked() is False
    assert panel._auto_continue_chk.isEnabled() is False


def test_multi_unit_toggle_enables_auto_continue_checkbox(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    panel._multi_unit_chk.setChecked(True)
    assert panel._auto_continue_chk.isEnabled() is True
    panel._multi_unit_chk.setChecked(False)
    assert panel._auto_continue_chk.isEnabled() is False
    assert panel._auto_continue_chk.isChecked() is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_panels.py -v -k multi_unit`
Expected: FAIL — `AttributeError: 'CollectorPanel' object has no attribute '_multi_unit_chk'`

- [ ] **Step 3: Add the checkboxes to `collector_panel.py`**

In `src/panels/collector_panel.py`, in `_build()`, right after the existing `self._auto_create_chk` block (currently ending at `layout.addSpacing(8)` before the `TARGET PAGE URL` label — insert before that spacing line):

```python
        self._multi_unit_chk = QCheckBox("Continue to next unit automatically")
        self._multi_unit_chk.setToolTip(
            "After this unit finishes, find the next unit in the course\n"
            "(skipping empty units and units that already have a combined\n"
            "page) and run it too. You'll be asked to confirm before each\n"
            "additional unit unless “Don't ask before each unit” is also checked."
        )
        self._multi_unit_chk.toggled.connect(self._on_multi_unit_toggle)
        layout.addWidget(self._multi_unit_chk)

        self._auto_continue_chk = QCheckBox("Don't ask before each unit")
        self._auto_continue_chk.setEnabled(False)
        self._auto_continue_chk.setToolTip(
            "Runs straight through additional units without pausing to\n"
            "confirm. Only used when “Continue to next unit automatically” is on."
        )
        layout.addWidget(self._auto_continue_chk)
        layout.addSpacing(8)
```

Add the handler method near `_on_auto_toggle`:

```python
    def _on_multi_unit_toggle(self, checked: bool):
        self._auto_continue_chk.setEnabled(checked)
        if not checked:
            self._auto_continue_chk.setChecked(False)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_panels.py -v -k multi_unit`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/panels/collector_panel.py tests/test_gui_panels.py
git commit -m "feat: add multi-unit checkboxes to Collector panel"
```

- [ ] **Step 6: Write failing test — `__COL_CONFIRM__` shows a Yes/No dialog and unblocks the waiting event**

Add to `tests/test_gui_panels.py`:

```python
def test_col_confirm_message_shows_dialog_and_sets_event(qtbot, monkeypatch):
    import threading
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    from PySide6.QtWidgets import QMessageBox

    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)

    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)

    result_ref = [False]
    event = threading.Event()
    panel._log_queue.put(("__COL_CONFIRM__", ("Continue to next unit?", result_ref, event)))
    panel._poll_log()

    assert event.is_set()
    assert result_ref[0] is True
```

- [ ] **Step 7: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_panels.py -v -k col_confirm`
Expected: FAIL — event never gets set (message falls into the plain-log `else` branch, `_log_queue` empties without acting on it)

- [ ] **Step 8: Add `__COL_CONFIRM__` handling to `_poll_log`**

In `src/panels/collector_panel.py`, add `Qt` to the existing `QtCore` import:

```python
from PySide6.QtCore import Qt, Signal, QTimer
```

Then update `_poll_log`:

```python
    def _poll_log(self):
        try:
            while True:
                msg, tag = self._log_queue.get_nowait()
                if msg == "__DONE__":
                    self._run_btn.setText("Collect & Assemble")
                    self._run_btn.setEnabled(True)
                elif msg == "__SUCCESS__":
                    self._continue_btn.show()
                    self.step_success.emit()
                elif msg == "__COL_CONFIRM__":
                    conf_msg, result_ref, event = tag
                    from PySide6.QtWidgets import QMessageBox
                    dlg = QMessageBox(self)
                    dlg.setWindowTitle("Continue to next unit?")
                    dlg.setText(conf_msg)
                    dlg.setStandardButtons(
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    dlg.setDefaultButton(QMessageBox.StandardButton.No)
                    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
                    dlg.raise_()
                    dlg.activateWindow()
                    result_ref[0] = dlg.exec() == QMessageBox.StandardButton.Yes
                    event.set()
                else:
                    self._log.append_log(msg, tag)
        except queue.Empty:
            pass
```

- [ ] **Step 9: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_panels.py -v -k col_confirm`
Expected: 1 passed

- [ ] **Step 10: Commit**

```bash
git add src/panels/collector_panel.py tests/test_gui_panels.py
git commit -m "feat: handle __COL_CONFIRM__ pause dialog in Collector panel"
```

- [ ] **Step 11: Wire `_start_run` and the worker to use multi-unit mode**

In `src/panels/collector_panel.py`, add these imports near the top (with the other stdlib imports):

```python
import threading
```

(Only add this if not already imported — `collector_panel.py` currently imports `threading` already at the top of the file; skip this if so.)

Replace the body of `_start_run` from the `unit_url = ...` lines through the `worker()` closure with:

```python
        unit_url   = _normalize_url(self._unit_entry.text())
        target_url = _normalize_url(self._target_entry.text())
        moodle_url = _normalize_url(self._moodle_entry.text())
        auto_create = self._auto_create_chk.isChecked()
        multi_unit  = self._multi_unit_chk.isChecked()
        auto_continue = self._auto_continue_chk.isChecked()
        if not unit_url:
            self._log.append_log("Paste a Brightspace unit URL first.", "warning"); return
        if not target_url and not auto_create:
            self._log.append_log(
                "Paste a target page URL, or turn on “Auto-create the target page”.", "warning"
            ); return

        course_id = None
        if multi_unit:
            from target_page_creator import _parse_ids
            course_id, _ = _parse_ids(unit_url)
            if not course_id:
                self._log.append_log(
                    "Couldn't read a course id from that unit URL — multi-unit mode needs one.",
                    "warning",
                ); return

        self._mw.save_config({"col_auto_create": auto_create})

        theme_name   = self._selected_theme[0]
        theme_colors = PAGE_THEMES[theme_name]
        parallel     = self._parallel_spin.value()

        style_ref_path = Path(__file__).parent.parent.parent / "templates" / "style_reference.html"
        try:
            style_reference_html = style_ref_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            style_reference_html = ""

        self._run_btn.setText("Running…"); self._run_btn.setEnabled(False)
        self._continue_btn.hide()
        self._log.clear_log()

        q = self._log_queue
        shared_kwargs = dict(
            theme_name=theme_name,
            theme_colors=theme_colors,
            claude_api_key=self._mw.claude_api_key,
            claude_model=self._mw.claude_model,
            style_reference_html=style_reference_html,
            parallel_pages=parallel,
            bs_username=self._mw.bs_username,
            bs_password=self._mw.bs_password,
            sso_email=self._mw.sso_email,
            sso_password=self._mw.sso_password,
            moodle_url=moodle_url,
            moodle_username=self._mw.moodle_username,
            moodle_password=self._mw.moodle_password,
        )

        def worker():
            done_sent = [False]
            def on_done():
                if not done_sent[0]:
                    done_sent[0] = True
                    q.put(("__DONE__", ""))
            try:
                from unit_collector import run as collector_run
                if multi_unit:
                    asyncio.run(self._run_multi_unit(
                        unit_url, course_id, auto_continue, shared_kwargs, collector_run, q
                    ))
                else:
                    asyncio.run(collector_run(
                        unit_url=unit_url,
                        target_url=target_url,
                        auto_create_target=auto_create,
                        log=lambda msg, tag="info": q.put((msg, tag)),
                        on_complete=on_done,
                        **shared_kwargs,
                    ))
            except Exception as e:
                q.put((f"Error: {e}", "error"))
            finally:
                on_done()

        threading.Thread(target=worker, daemon=True).start()
```

Add the new async helper method to `CollectorPanel` (e.g. right after `_start_run`):

```python
    async def _run_multi_unit(self, first_unit_url, course_id, auto_continue, shared_kwargs, collector_run, q):
        from browser import launch_browser, wait_for_login
        from multi_unit_selector import run_multi

        log = lambda msg, tag="info": q.put((msg, tag))
        base = "/".join(first_unit_url.split("/")[:3])

        p, browser_, context, page = await launch_browser()
        try:
            await wait_for_login(
                page, context,
                self._mw.bs_username or None, self._mw.bs_password or None,
                self._mw.sso_email or None, self._mw.sso_password or None,
            )

            async def run_unit(unit_url: str) -> bool:
                try:
                    await collector_run(
                        unit_url=unit_url,
                        target_url="",
                        auto_create_target=True,
                        log=log,
                        on_complete=lambda: None,
                        **shared_kwargs,
                    )
                    return True
                except Exception as e:
                    log(f"✗ Unit failed: {e}", "error")
                    return False

            def confirm_fn(message: str) -> bool:
                if auto_continue:
                    return True
                result_ref = [False]
                event = threading.Event()
                q.put(("__COL_CONFIRM__", (message, result_ref, event)))
                event.wait()
                return result_ref[0]

            summary = await run_multi(
                page=page,
                course_id=course_id,
                base_url=base,
                run_unit=run_unit,
                confirm_fn=confirm_fn,
                log=log,
            )
            log(
                f"─── Multi-unit run finished: {len(summary['processed'])} unit(s) done, "
                f"stopped because: {summary['stopped_reason']} ───",
                "info",
            )
        finally:
            if browser_.is_connected():
                await browser_.close()
            await p.stop()
```

- [ ] **Step 12: Run the full panel test file to verify nothing broke**

Run: `.venv\Scripts\python.exe -m pytest tests/test_gui_panels.py -v`
Expected: all `collector_panel`-related tests pass (the pre-existing unrelated `test_divider_returns_frame` failure, if present, is not caused by this change — confirm it was already failing before this task by checking `git stash` or by re-reading the assertion, not by fixing it here)

- [ ] **Step 13: Commit**

```bash
git add src/panels/collector_panel.py
git commit -m "feat: wire multi-unit auto-continue loop into Collector panel"
```

- [ ] **Step 14: Manual smoke test**

This step has no automated test — it drives a real browser against a real course, which is outside what pytest can exercise.

1. Run `dev.bat`.
2. Open the Unit Collector tab, paste a unit URL from a real course.
3. Check "Continue to next unit automatically", leave "Don't ask before each unit" unchecked.
4. Click "Collect & Assemble".
5. Confirm: the first unit runs as before, then a "Continue to next unit?" dialog appears naming the next unit.
6. Click Yes once, then No — confirm the run stops cleanly and logs "stopped because: declined".
7. Re-run with "Don't ask before each unit" also checked against a course with 2+ un-combined units — confirm it runs straight through without pausing.
8. If a course/module is available where a unit can be made to fail (e.g. temporarily break network mid-run), confirm the run stops and logs the failed module's title and id, rather than continuing.

Report back what you saw — no code changes in this step regardless of outcome; issues found here become new tasks.

---

## Self-Review Notes

- **Spec coverage:** new module (Task 1) ✓, no progress file / Brightspace-state resume (Task 1's `run_multi` re-derives from `fetch_toc` every call) ✓, skip empty modules ✓ (`select_next_unit`), skip already-"— Combined" modules ✓, stop-on-failure with logged title+module_id ✓ (`run_multi`'s failure branch), pause/confirm by default with opt-out ✓ (`confirm_fn` / `_auto_continue_chk`), 10-unit cap ✓ (`max_units=10` default), `unit_collector.py` and `target_page_creator.py` unchanged ✓ (only imported from, never edited).
- **Type consistency:** `run_multi`'s `run_unit` and `confirm_fn` signatures in Task 1 match exactly how Task 2's `_run_multi_unit` defines and passes them. `select_next_unit`'s return dict keys (`module_id`, `title`, `sort_order`, `topic_count`, `topic_titles`) are used consistently across `_flatten_modules`, `fetch_toc`, `run_multi`, and all tests.
- **No placeholders:** every step has complete, runnable code; the one step without automated tests (Task 2 Step 14) is explicitly a manual smoke test, not a stand-in for a missing automated one — Playwright-driven real-browser-against-real-Brightspace behavior isn't something pytest can exercise, matching how `unit_collector.py` itself has no automated tests today either.
