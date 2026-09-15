import sys

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, "src")

from content_preservation import content_is_equivalent, protect_html
from unit_collector import UnitCollector
from youtube_embed import parse_youtube_url, transform_standalone_youtube_urls


@pytest.mark.parametrize(
    ("url", "video_id", "suffix"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ", ""),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ", ""),
        ("https://youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ", ""),
        ("https://youtube-nocookie.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ", ""),
        ("https://youtu.be/dQw4w9WgXcQ?t=1m30s", "dQw4w9WgXcQ", "?start=90"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&start=45&end=75", "dQw4w9WgXcQ", "?start=45&end=75"),
    ],
)
def test_supported_youtube_urls_are_canonicalized(url, video_id, suffix):
    video = parse_youtube_url(url)
    assert video is not None
    assert video.video_id == video_id
    assert video.embed_url == f"https://www.youtube.com/embed/{video_id}{suffix}"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch",
        "https://youtube.com/watch?v=too-short",
        "javascript:alert(1)",
        "not a URL",
    ],
)
def test_invalid_youtube_urls_are_rejected(url):
    assert parse_youtube_url(url) is None


def test_plain_standalone_url_becomes_one_embed_without_raw_url():
    source = '<p><a href="https://youtu.be/dQw4w9WgXcQ">https://youtu.be/dQw4w9WgXcQ</a></p>'
    result = transform_standalone_youtube_urls(source)
    assert result.embeds_created == 1
    assert result.redundant_urls_removed == 0
    assert result.html.count("<iframe") == 1
    assert "youtu.be" not in result.html


def test_root_level_plain_url_is_also_standalone():
    result = transform_standalone_youtube_urls("https://youtu.be/dQw4w9WgXcQ")
    assert result.embeds_created == 1
    assert result.html.count("<iframe") == 1
    assert "youtu.be" not in result.html


def test_existing_embed_wins_over_duplicate_raw_url():
    source = """
    <iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
    <p>https://www.youtube.com/watch?v=dQw4w9WgXcQ</p>
    """
    result = transform_standalone_youtube_urls(source)
    assert result.embeds_created == 0
    assert result.redundant_urls_removed == 1
    assert result.html.count("<iframe") == 1
    assert "/watch?v=" not in result.html


def test_meaningful_link_text_and_url_in_sentence_remain_links():
    source = """
    <p>Review https://youtu.be/dQw4w9WgXcQ before class.</p>
    <p><a href="https://youtu.be/dQw4w9WgXcQ">Optional transcript and video</a></p>
    """
    result = transform_standalone_youtube_urls(source)
    assert not result.changed
    assert "Review https://youtu.be" in result.html
    assert "Optional transcript and video" in result.html
    assert "<iframe" not in result.html


def test_nearby_different_videos_get_distinct_players():
    source = """
    <p>https://youtu.be/dQw4w9WgXcQ</p>
    <p>https://youtube.com/watch?v=aqz-KE-bpKQ</p>
    """
    result = transform_standalone_youtube_urls(source)
    assert result.embeds_created == 2
    assert result.html.count("<iframe") == 2
    assert "dQw4w9WgXcQ" in result.html
    assert "aqz-KE-bpKQ" in result.html


def test_repeat_run_does_not_duplicate_embed():
    first = transform_standalone_youtube_urls("<p>https://youtu.be/dQw4w9WgXcQ</p>")
    second = transform_standalone_youtube_urls(first.html)
    assert first.embeds_created == 1
    assert not second.changed
    assert second.html.count("<iframe") == 1


def test_generated_embed_is_atomic_and_compatible_with_ai_protection():
    transformed = transform_standalone_youtube_urls(
        "<p>Intro text.</p><p>https://youtu.be/dQw4w9WgXcQ?t=30</p>"
    ).html
    protection = protect_html(transformed)
    assert "dQw4w9WgXcQ" not in protection.protected_html
    candidate = protection.protected_html.replace("<body>", '<body><main class="card">')
    candidate = candidate.replace("</body>", "</main></body>")
    restored = protection.restore_and_validate(candidate)
    equivalent, reason = content_is_equivalent(transformed, restored)
    assert equivalent, reason
    soup = BeautifulSoup(restored, "lxml")
    assert soup.iframe["src"].endswith("/dQw4w9WgXcQ?start=30")


def test_collector_assembles_youtube_topic_as_a_transformable_raw_url():
    collector = UnitCollector.__new__(UnitCollector)
    assembled = collector._build_combined_html([
        {
            "type": "link",
            "label": "Orientation video",
            "link_url": "https://youtu.be/dQw4w9WgXcQ?t=30",
        }
    ])
    # The collector's first save retains the source link. The verified second
    # transaction uses the same deterministic transform as Restyle.
    assert "Orientation video" in assembled
    assert "youtu.be" in assembled
    result = transform_standalone_youtube_urls(assembled)
    assert result.embeds_created == 1
    assert "youtu.be" not in result.html
    assert "?start=30" in result.html


class _TransformPage:
    async def close(self):
        return None

    async def wait_for_timeout(self, _milliseconds):
        return None


class _TransformContext:
    async def new_page(self):
        return _TransformPage()


@pytest.mark.asyncio
async def test_collector_does_not_save_when_youtube_readback_verification_fails():
    collector = UnitCollector.__new__(UnitCollector)
    collector.log = lambda *_args: None
    source = '<p><a href="https://youtu.be/dQw4w9WgXcQ">https://youtu.be/dQw4w9WgXcQ</a></p>'
    saved = []

    async def readback(_page, _expected):
        return source

    async def rejected_paste(_page, _html):
        return False

    async def save(_page):
        saved.append(True)
        return True

    collector._read_back_for_styling = readback
    collector._paste_html = rejected_paste
    collector._save_and_close = save
    assert not await collector._apply_youtube_transforms(_TransformContext())
    assert saved == []
