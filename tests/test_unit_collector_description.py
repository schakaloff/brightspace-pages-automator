"""The unit's own text is collected and moved only after verification."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import editor_save
import unit_overview
from content_preservation import content_is_preserved
from unit_collector import (
    UnitCollector, stored_unit_source, unit_source_marker,
    with_unit_source_marker,
)


def _collector():
    return UnitCollector(
        unit_url="https://learn.test/d2l/le/lessons/42/units/7",
        target_url="https://learn.test/d2l/le/lessons/42/topics/99",
        theme_name="lake", theme_colors={},
    )


def test_unit_source_marker_survives_a_retry_without_visible_duplicate():
    source = '<p>Welcome &amp; read <a href="/reading">this</a>.</p>'
    page = "<html><body><h2>Overview</h2>" + source + "</body></html>"
    marked = with_unit_source_marker(page, source)
    present, stored = stored_unit_source(marked)

    assert present and stored == source
    assert with_unit_source_marker(marked, source).count(unit_source_marker(source)) == 1
    assert marked.index(unit_source_marker(source)) < marked.index("</body>")


def test_content_api_uses_a_version_that_exposes_section_descriptions():
    scripts = []

    class Page:
        async def evaluate(self, script, _args):
            scripts.append(script)
            if "GET module" in script:
                return {"Title": "Unit", "Description": {"Html": "<p>Introduction</p>"}}
            return {"ParentModuleId": 7}

    api = unit_overview.BrowserContentAPI(Page(), "42", "7")
    module = asyncio.run(api.get_module())
    topic = asyncio.run(api.get_topic("99"))
    asyncio.run(api.replace_module_description(module, ""))

    assert unit_overview.extract_description_html(module) == "<p>Introduction</p>"
    assert topic["ParentModuleId"] == 7
    assert all("/d2l/api/le/1.75/" in script for script in scripts)


def test_empty_html_falls_back_to_plain_text_description():
    module = {"Description": {"Html": "", "Text": "Unit introduction"}}
    assert unit_overview.extract_description_html(module) == "<p>Unit introduction</p>"


def test_styled_label_may_drop_colon_but_facts_must_remain():
    source = "<p>Instructor:</p><p>Rob Bro</p>"
    styled = "<h2>Instructor</h2><p>Rob Bro</p>"

    assert content_is_preserved(source, styled, allow_label_colons=True)[0]
    assert not content_is_preserved(source, "<h2>Instructor</h2>", allow_label_colons=True)[0]


def test_section_description_is_first_in_the_assembled_page(monkeypatch):
    saved = []
    collector = _collector()

    class Tab:
        async def goto(self, *_args, **_kwargs):
            pass

        async def close(self):
            pass

    class Context:
        async def new_page(self):
            return Tab()

    async def replace(_page, _url, markup, _log):
        saved.append(markup)
        return True

    async def done(*_args, **_kwargs):
        return True

    monkeypatch.setattr(editor_save, "replace_topic_html", replace)
    monkeypatch.setattr(collector, "_apply_youtube_transforms", done)

    ok = asyncio.run(collector._collect_into(
        Context(), [], collector.target_url,
        '<html><head><title>x</title></head><body><p>Unit introduction</p></body></html>',
    ))

    assert ok
    assert saved[0].startswith("<h2>Overview</h2>\n<p>Unit introduction</p>")
    assert "<head>" not in saved[0]


def test_section_is_cleared_only_after_combined_page_keeps_a_reusable_copy(monkeypatch):
    source = "<p>Instructor:</p><p>Rob Bro</p>"
    combined = "<h2>Instructor</h2><p>Rob Bro</p><h2>Lesson</h2><p>Details</p>"
    state = {"combined": combined, "description": source, "clears": 0}
    collector = _collector()

    class API:
        def __init__(self, *_args):
            pass

        async def get_topic(self, _id):
            return {"ParentModuleId": 7}

        async def get_module(self):
            return {"Title": "Unit", "Description": {"Html": state["description"]}}

        async def replace_module_description(self, _module, markup):
            state["description"] = markup
            state["clears"] += 1

    async def read(_page, _url):
        return state["combined"]

    async def replace(_page, _url, markup, _log):
        state["combined"] = markup
        return True

    monkeypatch.setattr(unit_overview, "BrowserContentAPI", API)
    monkeypatch.setattr(editor_save, "read_topic_html", read)
    monkeypatch.setattr(editor_save, "replace_topic_html", replace)

    original = {"Title": "Unit", "Description": {"Html": source}}
    assert asyncio.run(collector._finish_unit_description_transfer(object(), original, source))
    assert state["description"] == "" and state["clears"] == 1
    assert stored_unit_source(state["combined"]) == (True, source)

    # A later collection overwrites the page while the section itself is empty.
    # The saved source marker must be written back for the next retry too.
    state["combined"] = combined
    assert asyncio.run(collector._finish_unit_description_transfer(
        object(), {"Title": "Unit", "Description": {"Html": ""}}, source,
        clear_source=False,
    ))
    assert state["description"] == "" and state["clears"] == 1
    assert stored_unit_source(state["combined"]) == (True, source)


def test_section_is_kept_when_combined_page_loses_its_text(monkeypatch):
    collector = _collector()
    source = "<p>Unit introduction</p>"
    calls = []

    class API:
        def __init__(self, *_args):
            pass

        async def get_topic(self, _id):
            return {"ParentModuleId": 7}

        async def get_module(self):
            return {"Title": "Unit", "Description": {"Html": source}}

        async def replace_module_description(self, *_args):
            calls.append("cleared")

    async def read(_page, _url):
        return "<p>Other content</p>"

    monkeypatch.setattr(unit_overview, "BrowserContentAPI", API)
    monkeypatch.setattr(editor_save, "read_topic_html", read)

    original = {"Title": "Unit", "Description": {"Html": source}}
    assert not asyncio.run(collector._finish_unit_description_transfer(object(), original, source))
    assert not calls


def test_failed_topic_scan_does_not_move_section_text(monkeypatch):
    collector = _collector()
    calls = []

    class Page:
        async def goto(self, *_args, **_kwargs):
            pass

        async def wait_for_load_state(self, *_args, **_kwargs):
            pass

    class API:
        def __init__(self, *_args):
            pass

        async def get_module(self):
            return {"Title": "Unit", "Description": {"Html": "<p>Introduction</p>"}}

        async def list_structure(self):
            return [{"Id": 99, "Title": "Unit — Combined"},
                    {"Id": 100, "Title": "A lesson"}]

    async def no_topics(_page):
        return []

    async def must_not_collect(*_args):
        calls.append("collected")
        return True

    monkeypatch.setattr(unit_overview, "BrowserContentAPI", API)
    monkeypatch.setattr(collector, "_scrape_topics", no_topics)
    monkeypatch.setattr(collector, "_collect_into", must_not_collect)

    assert not asyncio.run(collector.run(context=object(), page=Page()))
    assert not calls


def test_run_passes_section_text_into_styling_before_clearing_it(monkeypatch):
    collector = _collector()
    collector.claude_api_key = "test-key"
    calls = []
    source = "<p>Introduction</p>"

    class Page:
        async def goto(self, *_args, **_kwargs):
            pass

        async def wait_for_load_state(self, *_args, **_kwargs):
            pass

    class API:
        def __init__(self, *_args):
            pass

        async def get_module(self):
            return {"Title": "Unit", "Description": {"Html": source}}

    async def topics(_page):
        return [{"topic_id": "8", "label": "Lesson",
                 "url": "https://learn.test/d2l/le/lessons/42/topics/8"}]

    async def hidden(_page, _topics):
        return set()

    async def no_op():
        pass

    async def collect(_context, _topics, _target, intro):
        calls.append(("collect", intro))
        return True

    async def finish(_page, _module, intro, clear_source=True):
        calls.append(("clear", intro, clear_source))
        return True

    monkeypatch.setattr(unit_overview, "BrowserContentAPI", API)
    monkeypatch.setattr(collector, "_scrape_topics", topics)
    monkeypatch.setattr(collector, "_fetch_hidden_topic_ids", hidden)
    monkeypatch.setattr(collector, "_build_name_matcher", no_op)
    monkeypatch.setattr(collector, "_collect_into", collect)
    monkeypatch.setattr(collector, "_finish_unit_description_transfer", finish)

    assert asyncio.run(collector.run(context=object(), page=Page()))
    assert calls == [("collect", source), ("clear", source, True)]


def test_cleanup_only_verifies_existing_combined_page_without_restyling(monkeypatch):
    collector = _collector()
    calls = []

    class Page:
        async def goto(self, *_args, **_kwargs):
            pass

        async def wait_for_load_state(self, *_args, **_kwargs):
            pass

    class API:
        def __init__(self, *_args):
            pass

        async def get_topic(self, _id):
            return {"Title": "Unit — Combined", "ParentModuleId": 7}

        async def get_module(self):
            return {"Title": "Unit", "Description": {"Html": "<p>Instructor:</p>"}}

    async def must_not_scrape(*_args):
        raise AssertionError("cleanup must not scrape or restyle")

    async def finish(_page, _module, source):
        calls.append(source)
        return True

    monkeypatch.setattr(unit_overview, "BrowserContentAPI", API)
    monkeypatch.setattr(collector, "_scrape_topics", must_not_scrape)
    monkeypatch.setattr(collector, "_finish_unit_description_transfer", finish)

    assert asyncio.run(collector.run(context=object(), page=Page(), cleanup_only=True))
    assert calls == ["<p>Instructor:</p>"]
