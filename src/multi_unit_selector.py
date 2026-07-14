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
