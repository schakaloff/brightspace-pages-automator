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
