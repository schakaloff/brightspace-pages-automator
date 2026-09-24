"""A bad file download must leave its slide topic visible in the combined page."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unit_collector import UnitCollector


class _Tab:
    async def goto(self, *_args, **_kwargs):
        return None

    async def close(self):
        return None


class _Context:
    async def new_page(self):
        return _Tab()


def _topic(number, title):
    return {
        "topic_id": str(number),
        "label": title,
        "url": f"https://learn.test/d2l/le/lessons/42/topics/{number}",
    }


def test_rejected_slide_download_keeps_each_slide_link_and_reports_partial(monkeypatch):
    import editor_save

    topics = [_topic(1, "LECTURE SLIDES"), *(
        _topic(number, title) for number, title in (
            (2, "Slides: IAM"),
            (3, "Slides: Logging"),
            (4, "Slides: Incident Response"),
        )
    )]
    saved = []
    logs = []
    collector = UnitCollector(
        unit_url="https://learn.test/d2l/le/lessons/42/units/7",
        target_url="https://learn.test/d2l/le/lessons/42/topics/99",
        theme_name="lake",
        theme_colors={},
        log=lambda message, level: logs.append((message, level)),
    )

    async def scrape(_context, topic, _semaphore):
        return {
            "topic": topic,
            "html": "<p></p>" if topic["topic_id"] == "1" else None,
            "link_url": None,
            "file": None,
        }

    async def save(_tab, _url, content, _log):
        saved.append(content)
        return True

    async def no_op(*_args, **_kwargs):
        return True

    monkeypatch.setattr(collector, "_scrape_topic", scrape)
    monkeypatch.setattr(collector, "_apply_youtube_transforms", no_op)
    monkeypatch.setattr(editor_save, "replace_topic_html", save)

    completed = asyncio.run(collector._collect_into(_Context(), topics, collector.target_url))

    assert not completed
    assert len(saved) == 1
    assert "layout.css" not in saved[0]
    assert "<h2>LECTURE SLIDES</h2>" in saved[0]
    before_first_slide = saved[0].split('href="' + topics[1]["url"] + '"')[0]
    assert "<hr/>" not in before_first_slide
    for topic in topics[1:]:
        assert f'href="{topic["url"]}"' in saved[0]
        assert topic["label"] in saved[0]
    assert any("3 topic(s) need review" in message for message, _ in logs)


def test_duplicate_download_names_do_not_enter_upload_flow(monkeypatch):
    import editor_save

    topics = [_topic(2, "Slides: IAM"), _topic(3, "Slides: Logging")]
    saved = []
    collector = UnitCollector(
        unit_url="https://learn.test/d2l/le/lessons/42/units/7",
        target_url="https://learn.test/d2l/le/lessons/42/topics/99",
        theme_name="lake",
        theme_colors={},
    )

    async def scrape(_context, topic, _semaphore):
        return {
            "topic": topic,
            "html": None,
            "link_url": None,
            "file": {"filename": "shared.pdf", "path": f"C:/temp/{topic['topic_id']}/shared.pdf"},
        }

    async def save(_tab, _url, content, _log):
        saved.append(content)
        return True

    async def no_op(*_args, **_kwargs):
        return True

    monkeypatch.setattr(collector, "_scrape_topic", scrape)
    monkeypatch.setattr(collector, "_apply_youtube_transforms", no_op)
    monkeypatch.setattr(editor_save, "replace_topic_html", save)

    completed = asyncio.run(collector._collect_into(_Context(), topics, collector.target_url))

    assert not completed
    assert saved[0].count("shared.pdf") == 0
    for topic in topics:
        assert topic["url"] in saved[0]


def test_valid_slide_file_is_linked_under_lecture_slides_not_uploaded(monkeypatch):
    import editor_save

    topics = [_topic(1, "LECTURE SLIDES"), _topic(2, "Slides: IAM")]
    direct_url = "https://learn.test/content/enforced/42/iam.pptx"
    saved = []
    collector = UnitCollector(
        unit_url="https://learn.test/d2l/le/lessons/42/units/7",
        target_url="https://learn.test/d2l/le/lessons/42/topics/99",
        theme_name="lake",
        theme_colors={},
    )

    async def scrape(_context, topic, _semaphore):
        return {
            "topic": topic,
            "html": "<p></p>" if topic["topic_id"] == "1" else None,
            "link_url": None,
            "file": None if topic["topic_id"] == "1" else {
                "filename": "iam.pptx", "path": "C:/temp/iam.pptx", "direct_url": direct_url,
            },
        }

    async def save(_tab, _url, content, _log):
        saved.append(content)
        return True

    async def no_op(*_args, **_kwargs):
        return True

    monkeypatch.setattr(collector, "_scrape_topic", scrape)
    monkeypatch.setattr(collector, "_apply_youtube_transforms", no_op)
    monkeypatch.setattr(editor_save, "replace_topic_html", save)

    completed = asyncio.run(collector._collect_into(_Context(), topics, collector.target_url))

    assert completed
    assert '<h2>LECTURE SLIDES</h2>' in saved[0]
    assert f'href="{direct_url}"' in saved[0]
    assert '<h2>Files</h2>' not in saved[0]


def test_html_slide_topics_keep_original_links_without_downloading(monkeypatch):
    import editor_save

    topics = [_topic(1, "LECTURE SLIDES"), *(
        _topic(number, title) for number, title in (
            (2, "Slides: PCIT 102.7 2025-12-09 IAM"),
            (3, "Slides: PCIT 102.8 2025-12-11 Logging"),
            (4, "Slides: PCIT 102.9 2025-12-16 Incident Response"),
        )
    )]
    for topic in topics:
        topic["type"] = "html"
    saved = []
    collector = UnitCollector(
        unit_url="https://learn.test/d2l/le/lessons/42/units/7",
        target_url="https://learn.test/d2l/le/lessons/42/topics/99",
        theme_name="lake",
        theme_colors={},
    )

    async def scrape_heading(_context, topic, semaphore):
        if topic["topic_id"] == "1":
            return {"topic": topic, "html": "<p></p>", "link_url": None, "file": None}
        return await UnitCollector._scrape_topic(collector, _context, topic, semaphore)

    async def unexpected_download(*_args, **_kwargs):
        raise AssertionError("Slide topics must not trigger Download")

    async def save(_tab, _url, content, _log):
        saved.append(content)
        return True

    async def no_op(*_args, **_kwargs):
        return True

    monkeypatch.setattr(collector, "_scrape_topic", scrape_heading)
    monkeypatch.setattr(collector, "_download_file", unexpected_download)
    monkeypatch.setattr(collector, "_apply_youtube_transforms", no_op)
    monkeypatch.setattr(editor_save, "replace_topic_html", save)

    completed = asyncio.run(collector._collect_into(_Context(), topics, collector.target_url))

    assert completed
    assert len(saved) == 1
    assert "layout.css" not in saved[0]
    assert "<h2>LECTURE SLIDES</h2>" in saved[0]
    assert saved[0].count('href="https://learn.test/d2l/le/lessons/42/topics/') == 3
    assert '<h2>Files</h2>' not in saved[0]
    for topic in topics[1:]:
        assert f'<a href="{topic["url"]}">{topic["label"]}</a>' in saved[0]
