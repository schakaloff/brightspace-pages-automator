"""Combined-page regressions from ITUS-110 unit 969309 (2026-09-25).

That run saved a page whose file links had all vanished, embedded whole topic
documents inside it, and never built the instructor page for 22 hidden topics.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ai_styler
from unit_collector import UnitCollector, html_body_fragment


TOPIC_DOC = (
    "<!DOCTYPE html>\n\n<html><head>\n"
    '<meta content="text/html; charset=utf-8" http-equiv="Content-Type"/>\n'
    "<style>body { color: #494c4e; }</style>\n"
    "</head><body><p></p>\n<h3><strong>Cisco Resources</strong></h3>\n<p></p></body></html>"
)


def test_whole_topic_document_is_reduced_to_its_body():
    fragment = html_body_fragment(TOPIC_DOC)

    assert "<html" not in fragment and "<head" not in fragment and "DOCTYPE" not in fragment
    assert "<h3><strong>Cisco Resources</strong></h3>" in fragment


def test_plain_fragment_is_left_untouched():
    assert html_body_fragment('<p>Hi <a href="x">y</a></p>') == '<p>Hi <a href="x">y</a></p>'


def test_styler_refuses_a_page_its_cleaning_gutted():
    words = " ".join(f"word{i}" for i in range(60))
    source = TOPIC_DOC + f"<hr/><p>{words}</p>"
    logs = []

    styled, usage = asyncio.run(ai_styler.apply_style(
        source_html=source, style_reference_html="", theme_name="lake",
        api_key="unused", log_callback=lambda message, level: logs.append(message),
    ))

    assert (styled, usage) == (None, None)
    assert any("Cleaning kept only" in message for message in logs)


class _Tab:
    def __init__(self, existing=()):
        self.existing = set(existing)

    async def goto(self, *_args, **_kwargs):
        return None

    async def close(self):
        return None

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, _script, arg=None):
        return arg in self.existing


class _Context:
    def __init__(self, tab):
        self.tab = tab

    async def new_page(self):
        return self.tab


def _collector():
    collector = UnitCollector(
        unit_url="https://learn.test/d2l/le/lessons/42/units/7",
        target_url="https://learn.test/d2l/le/lessons/42/topics/99",
        theme_name="lake",
        theme_colors={},
    )
    collector._name_matcher = lambda _label: None
    return collector


def test_file_links_are_written_through_the_api_not_the_editor(monkeypatch):
    import editor_save

    root = "/content/enforced/42-X/"
    tab = _Tab(existing={root + "L1%20notes.docx"})
    saved = []
    collector = _collector()
    topics = [
        {"topic_id": "1", "label": "L1 notes", "url": "https://learn.test/d2l/le/lessons/42/topics/1"},
        {"topic_id": "2", "label": "Wrap up.txt", "url": "https://learn.test/d2l/le/lessons/42/topics/2"},
    ]

    async def scrape(_context, topic, _semaphore):
        name = "L1 notes.docx" if topic["topic_id"] == "1" else "Wrap up.txt"
        return {"topic": topic, "html": None, "link_url": None,
                "file": {"filename": name, "path": f"C:/tmp/{name}"}}

    async def save(_tab, _url, content, _log):
        saved.append(content)
        return True

    async def course_root(_page, _course_id):
        return root

    async def insert(_tab, file_item):
        return file_item["filename"] == "L1 notes.docx"

    async def ok(*_args, **_kwargs):
        return True

    async def must_not_save(*_args, **_kwargs):
        raise AssertionError("the editor's Save and Close must not be used")

    monkeypatch.setattr(editor_save, "replace_topic_html", save)
    monkeypatch.setattr(collector, "_scrape_topic", scrape)
    monkeypatch.setattr(collector, "_course_root", course_root)
    monkeypatch.setattr(collector, "_navigate_to_edit", ok)
    monkeypatch.setattr(collector, "_editor_cursor_end", ok)
    monkeypatch.setattr(collector, "_insert_file", insert)
    monkeypatch.setattr(collector, "_save_and_close", must_not_save)
    monkeypatch.setattr(collector, "_apply_youtube_transforms", ok)

    completed = asyncio.run(collector._collect_into(_Context(tab), topics, collector.target_url))

    assert not completed  # the failed upload needs review
    final = saved[-1]
    assert f'<a href="{root}L1%20notes.docx">L1 notes</a>' in final
    assert f'<a href="{topics[1]["url"]}">Wrap up.txt</a>' in final
    assert "<h2>Files</h2>\n<p></p>" not in final


def test_instructor_page_is_still_built_when_the_student_page_fails(monkeypatch):
    import editor_save
    import unit_overview

    collector = _collector()
    topics = [
        {"topic_id": "1", "label": "Public", "url": "https://learn.test/d2l/le/lessons/42/topics/1"},
        {"topic_id": "2", "label": "Staff", "url": "https://learn.test/d2l/le/lessons/42/topics/2"},
    ]
    calls = []

    class _Page:
        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

    async def scrape_topics(_page):
        return [dict(topic) for topic in topics]

    async def hidden_ids(_page):
        return {"2"}

    async def nothing(*_args, **_kwargs):
        return None

    async def module_without_description(_api):
        return {"Title": "Test unit", "Description": {"Html": ""}}

    async def empty_target(*_args):
        return "<p></p>"

    async def collect_into(_context, chosen, _url):
        calls.append(("student", [t["label"] for t in chosen]))
        return False  # e.g. styling was rejected

    async def collect_hidden(_context, _page, chosen):
        calls.append(("instructor", [t["label"] for t in chosen]))
        return True

    monkeypatch.setattr(collector, "_scrape_topics", scrape_topics)
    monkeypatch.setattr(collector, "_fetch_hidden_topic_ids", hidden_ids)
    monkeypatch.setattr(collector, "_build_name_matcher", nothing)
    monkeypatch.setattr(collector, "_collect_into", collect_into)
    monkeypatch.setattr(collector, "_collect_hidden_topics", collect_hidden)
    monkeypatch.setattr(unit_overview.BrowserContentAPI, "get_module", module_without_description)
    monkeypatch.setattr(editor_save, "read_topic_html", empty_target)

    completed = asyncio.run(collector.run(context=object(), page=_Page()))

    assert completed is False
    assert calls == [("student", ["Public"]), ("instructor", ["Staff"])]
