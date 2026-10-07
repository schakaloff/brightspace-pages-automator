import asyncio
import sys
import threading
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, "src")
from automator import PageAutomator
from restyle_selection import selected_pages

PAGES = [{"label": f"Page {i + 1}", "url": f"https://example.test/topics/{i + 1}"}
         for i in range(20)]


def test_non_consecutive_selection_keeps_course_order():
    assert selected_pages(PAGES, [19, 3, 0]) == [PAGES[0], PAGES[3], PAGES[19]]
    assert selected_pages(PAGES, []) == []


@pytest.mark.parametrize("indices", [(0, 5), None, [True], [-1], [20], [0, 0], ["1"]])
def test_invalid_selection_is_rejected(indices):
    with pytest.raises(ValueError):
        selected_pages(PAGES, indices)


def test_batch_only_processes_chosen_pages_and_isolates_failures():
    results, tabs = [], []
    runner = PageAutomator("https://example.test/units/1", lambda *args: None,
                           on_pages_found=lambda pages: [0, 3, 19],
                           on_page_result=lambda i, p, state: results.append((p["url"], state)))
    async def new_page():
        tab = SimpleNamespace(close=AsyncMock())
        tabs.append(tab)
        return tab
    async def process(tab, url, label):
        if url == PAGES[3]["url"]:
            raise RuntimeError("One page failed")
        return True
    runner._process_topic_impl = process
    async def scenario():
        await runner._run_selected(SimpleNamespace(new_page=new_page), await runner._choose_pages(PAGES))
    asyncio.run(scenario())
    assert set(results) == {(PAGES[0]["url"], "changed"), (PAGES[3]["url"], "failed"),
                            (PAGES[19]["url"], "changed")}
    assert runner._run_summary.pages_changed == 2
    assert runner._run_summary.pages_failed == 1
    assert all(tab.close.await_count == 1 for tab in tabs)


def test_stop_allows_active_pages_to_finish_and_skips_queued_pages():
    stop, results, active = threading.Event(), [], []
    runner = PageAutomator("https://example.test/units/1", lambda *args: None,
                           stop_event=stop, on_page_result=lambda i, p, state: results.append(state))
    async def process(tab, url, label):
        active.append(url)
        stop.set()
        await asyncio.sleep(0)
        return True
    runner._process_topic_impl = process
    context = SimpleNamespace(new_page=AsyncMock(side_effect=lambda: SimpleNamespace(close=AsyncMock())))
    asyncio.run(runner._run_selected(context, PAGES))
    assert 1 <= len(active) <= 5
    assert results.count("changed") == len(active)
    assert results.count("skipped") == 20 - len(active)


def test_blank_encoded_page_title_logs_the_url_for_review():
    messages = []
    runner = PageAutomator(PAGES[0]["url"], lambda message, level: messages.append(message))
    runner._process_topic_impl = AsyncMock(return_value=False)
    context = SimpleNamespace(new_page=AsyncMock(return_value=SimpleNamespace(close=AsyncMock())))
    asyncio.run(runner._run_selected(context, [dict(label="&#x20;", url=PAGES[0]["url"])]))
    assert f"Needs review: {PAGES[0]['url']}" in messages
    assert not any("&#x20;" in message for message in messages)


def install_browser(monkeypatch, completed=None):
    browser = SimpleNamespace(is_connected=lambda: not completed, close=AsyncMock())
    page = SimpleNamespace(goto=AsyncMock())
    runtime = SimpleNamespace(stop=AsyncMock())
    monkeypatch.setitem(sys.modules, "browser", SimpleNamespace(
        launch_browser=AsyncMock(return_value=(runtime, browser, object(), page)), wait_for_login=AsyncMock()))
    transfer = AsyncMock()
    monkeypatch.setitem(sys.modules, "unit_overview", SimpleNamespace(move_unit_url_to_overview=transfer))
    return browser, runtime, transfer


def test_cancel_picker_does_not_move_unit_description_or_process_pages(monkeypatch):
    browser, runtime, transfer = install_browser(monkeypatch)
    calls = []
    runner = PageAutomator("https://example.test/units/1", lambda *args: None,
                           on_pages_found=lambda pages: [], on_complete=lambda: calls.append("done"))
    runner.scrape_section_pages = AsyncMock(return_value=[dict(p) for p in PAGES])
    runner._process_topic = AsyncMock()
    asyncio.run(runner.run())
    transfer.assert_not_awaited()
    runner._process_topic.assert_not_awaited()
    browser.close.assert_awaited_once()
    runtime.stop.assert_awaited_once()
    assert calls == ["done"]


def test_unchecked_overview_is_never_moved(monkeypatch):
    completed = []
    browser, runtime, transfer = install_browser(monkeypatch, completed)
    runner = PageAutomator("https://example.test/units/1", lambda *args: None,
                           on_pages_found=lambda pages: [2, 5], on_complete=lambda: completed.append(True))
    runner.scrape_section_pages = AsyncMock(return_value=[dict(p) for p in PAGES])
    runner._run_selected = AsyncMock()
    asyncio.run(runner.run())
    transfer.assert_not_awaited()
    assert runner._run_selected.call_args.args[1] == [PAGES[1], PAGES[4]]


def test_batch_limits_concurrent_editors_to_five():
    active, maximum, processed = 0, 0, []
    runner = PageAutomator("https://example.test/units/1", lambda *args: None)
    async def process(tab, url, label):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        processed.append(url)
        active -= 1
        return True
    runner._process_topic_impl = process
    context = SimpleNamespace(new_page=AsyncMock(side_effect=lambda: SimpleNamespace(close=AsyncMock())))
    asyncio.run(runner._run_selected(context, PAGES))
    assert maximum == 5
    assert len(processed) == len(set(processed)) == 20


def test_single_page_keeps_original_title_discovery(monkeypatch):
    completed = []
    install_browser(monkeypatch, completed)
    runner = PageAutomator(PAGES[0]["url"], lambda *args: None,
                           on_complete=lambda: completed.append(True))
    runner._run_selected = AsyncMock()
    asyncio.run(runner.run())
    assert runner._run_selected.call_args.args[1] == [{"label": "", "url": PAGES[0]["url"]}]


@pytest.mark.skipif(not shutil.which("node"), reason="Node needed to execute discovery JavaScript")
@pytest.mark.parametrize("has_unit", [True, False])
def test_section_discovery_matches_exact_unit_and_never_falls_back_to_course(has_unit):
    runner = PageAutomator("https://example.test/d2l/le/lessons/100/units/12", lambda *args: None)
    async def evaluate(script, args):
        # Execute the actual adapter code against a small fake sidebar. Unit 112
        # comes first to expose prefix matching, with an unrelated course topic.
        fixture = r'''
        function topic(id, label) {
            return {getAttribute: key => ({'action-href':'/d2l/le/lessons/100/topics/'+id, label})[key] || '',
                    querySelectorAll: () => [], shadowRoot:null};
        }
        function unit(id, children) {
            return {getAttribute: key => ({'action-href':'/d2l/le/lessons/100/units/'+id, key:String(id)})[key] || '',
                    querySelectorAll: selector => selector==='d2l-list-item-nav' ? children : [], shadowRoot:null};
        }
        const nodes = [unit(112, [topic(8,'Wrong unit')]), topic(12,'Unrelated topic with same ID')];
        if (HAS_UNIT) nodes.push(unit(12, [topic(9,'Chosen unit')]));
        global.document = {querySelectorAll: () => nodes};
        '''.replace("HAS_UNIT", json.dumps(has_unit))
        code = fixture + "process.stdout.write(JSON.stringify((" + script + ")(" + json.dumps(args) + ")));"
        result = subprocess.run([shutil.which("node"), "-"], input=code, text=True, capture_output=True, check=True)
        return json.loads(result.stdout)
    page = SimpleNamespace(wait_for_selector=AsyncMock(), wait_for_timeout=AsyncMock(),
                           frames=[], main_frame=None, evaluate=evaluate)
    result = asyncio.run(runner.scrape_section_pages(page))
    assert result == ([{"label": "Chosen unit", "url": "https://example.test/d2l/le/lessons/100/topics/9"}]
                      if has_unit else [])
