import asyncio
import sys
from types import ModuleType

from editor_save import _topic_ids, replace_topic_html


class _Page:
    async def wait_for_timeout(self, _milliseconds):
        return None


def test_topic_ids_support_lessons_content_and_ou_urls():
    assert _topic_ids("https://learn.test/d2l/le/lessons/10164/topics/962889") == (
        "10164", "962889"
    )
    assert _topic_ids("https://learn.test/d2l/le/content/42/topics/7") == (
        "42", "7"
    )
    assert _topic_ids("https://learn.test/page?ou=42/topics/7") == ("42", "7")
    assert _topic_ids("https://learn.test/no-ids") is None


def test_api_write_requires_semantically_preserved_readback(monkeypatch):
    import content_preservation

    monkeypatch.setattr(
        content_preservation,
        "content_is_preserved",
        lambda expected, actual: (expected in actual, "content missing"),
    )
    writes = []

    class _API:
        def __init__(self, _page, course_id, module_id):
            assert (course_id, module_id) == ("10164", "0")

        async def replace_topic_html(self, topic_id, source_html):
            writes.append((topic_id, source_html))

        async def get_topic_html(self, _topic_id):
            return "<html><body><p>kept</p></body></html>"

    module = ModuleType("unit_overview")
    module.BrowserContentAPI = _API
    monkeypatch.setitem(sys.modules, "unit_overview", module)
    logs = []

    ok = asyncio.run(replace_topic_html(
        _Page(),
        "https://learn.test/d2l/le/lessons/10164/topics/962889",
        "<p>kept</p>",
        lambda message, level: logs.append((message, level)),
    ))

    assert ok
    assert writes == [("962889", "<p>kept</p>")]
    assert ("✓ Saved and verified", "success") in logs


def test_api_write_sets_new_window_on_collector_links(monkeypatch):
    import content_preservation

    monkeypatch.setattr(
        content_preservation,
        "content_is_preserved",
        lambda expected, actual: (expected == actual, "content changed"),
    )
    writes = []

    class _API:
        def __init__(self, *_args):
            pass

        async def replace_topic_html(self, _topic_id, source_html):
            writes.append(source_html)

        async def get_topic_html(self, _topic_id):
            return writes[-1]

    module = ModuleType("unit_overview")
    module.BrowserContentAPI = _API
    monkeypatch.setitem(sys.modules, "unit_overview", module)

    ok = asyncio.run(replace_topic_html(
        _Page(),
        "https://learn.test/d2l/le/lessons/10164/topics/962889",
        '<p><a href="/d2l/le/lessons/10164/topics/2">Lecture slide</a></p>',
        lambda *_args: None,
    ))

    assert ok
    assert 'target="_blank"' in writes[0]
    assert 'rel="noopener"' in writes[0]


def test_api_write_rejects_blank_stub_readback(monkeypatch):
    import content_preservation

    monkeypatch.setattr(
        content_preservation,
        "content_is_preserved",
        lambda expected, actual: (expected in actual, "content missing"),
    )

    class _API:
        def __init__(self, *_args):
            pass

        async def replace_topic_html(self, _topic_id, _source_html):
            return None

        async def get_topic_html(self, _topic_id):
            return "<p></p>"

    module = ModuleType("unit_overview")
    module.BrowserContentAPI = _API
    monkeypatch.setitem(sys.modules, "unit_overview", module)
    logs = []

    ok = asyncio.run(replace_topic_html(
        _Page(),
        "https://learn.test/d2l/le/lessons/10164/topics/962889",
        "<p>the collected lesson</p>",
        lambda message, level: logs.append((message, level)),
    ))

    assert not ok
    assert any("did not keep the assembled page" in message for message, _ in logs)


def test_text_only_collection_uses_api_without_opening_editor(monkeypatch):
    import content_preservation
    import editor_save
    from unit_collector import UnitCollector

    monkeypatch.setattr(
        content_preservation,
        "add_generated_heading",
        lambda title, source: f"<h2>{title}</h2>\n{source}",
    )
    writes = []

    async def api_write(_page, url, source_html, _log):
        writes.append((url, source_html))
        return True

    monkeypatch.setattr(editor_save, "replace_topic_html", api_write)

    class _Page:
        async def goto(self, *_args, **_kwargs):
            return None

        async def close(self):
            return None

    class _Context:
        async def new_page(self):
            return _Page()

    collector = UnitCollector.__new__(UnitCollector)
    collector.parallel_pages = 3
    collector.claude_api_key = ""
    collector._topic_metadata = {}
    collector._name_matcher = lambda _label: None
    collector.log = lambda *_args: None

    async def scrape(_context, topic, _semaphore):
        return {
            "topic": topic,
            "html": "<p>Collected lesson body.</p>",
            "link_url": None,
            "file": None,
        }

    async def youtube(_context, expected_min_chars=0):
        assert expected_min_chars == len(writes[0][1])
        return True

    async def editor_must_not_open(*_args, **_kwargs):
        raise AssertionError("text-only collection should not open the visual editor")

    collector._scrape_topic = scrape
    collector._apply_youtube_transforms = youtube
    collector._navigate_to_edit = editor_must_not_open
    target = "https://learn.test/d2l/le/lessons/10164/topics/962889"

    ok = asyncio.run(collector._collect_into(
        _Context(), [{"label": "Instructor Resources", "type": "html"}], target
    ))

    assert ok
    assert writes == [(
        target,
        "<h2>Instructor Resources</h2>\n<p>Collected lesson body.</p>\n<hr/>\n",
    )]
