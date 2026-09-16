import sys

import pytest

sys.path.insert(0, "src")

from content_preservation import (
    add_generated_heading,
    content_is_equivalent,
    content_is_preserved,
    generated_title_matches_leading_content,
)
from automator import PageAutomator
from unit_collector import UnitCollector


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Orientation details", "<p>Orientation details</p>"),
        (
            "Do you have 90+ hours working as an MOA already? I...",
            "<p>Do you have 90+ hours working as an MOA already? If so, you may be eligible.</p>",
        ),
        (
            "Do you have 90+ hours working as an MOA already? I…",
            "<p>Do you have 90+ hours working as an MOA already? If so, you may be eligible.</p>",
        ),
        ("IMPORTANT COURSE ORIENTATION INFORMATION", "<p>Important Course Orientation Information</p>"),
        (
            "Health &amp; Safety information for students",
            "<p><strong>Health &amp; Safety</strong> information for students</p>",
        ),
        (
            "Café safety procedures for clinical students...",
            "<p>Café safety procedures for clinical students and instructors</p>",
        ),
    ],
)
def test_generated_heading_is_skipped_for_matching_leading_content(title, body):
    assert generated_title_matches_leading_content(title, body)
    assert add_generated_heading(title, body) == body


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Files", "<p>Files required for this week's assignment are below.</p>"),
        ("Course overview", "<p>Clinical placement requirements and dates.</p>"),
        (
            "Do you have 90+ hours working as an MOA already?",
            "<p>Do you have 90+ hours working as an MOA elsewhere?</p>",
        ),
    ],
)
def test_generated_heading_is_kept_for_non_matching_or_ambiguous_content(title, body):
    result = add_generated_heading(title, body)
    assert result != body
    assert result.endswith(body)


def test_unit_collector_uses_the_same_duplicate_heading_rule():
    collector = UnitCollector.__new__(UnitCollector)
    body = "<p>Do you have 90+ hours working as an MOA already? If so, apply here.</p>"
    result = collector._build_combined_html(
        [{"type": "html", "label": "Do you have 90+ hours working as an MOA already? I...", "html": body}]
    )
    assert "<h2>" not in result
    assert body in result


COMPLEX_HTML = """
<main class="old-layout">
  <h2>Clinical résumé — week one</h2>
  <p>Keep every word, including café, λ, and 한국어.</p>
  <ol><li>First item</li><li>Second item</li></ol>
  <table><tr><th>Day</th><th>Room</th></tr><tr><td>Monday</td><td>HSC 101</td></tr></table>
  <a href="/content/enforced/123/file%20one.pdf?download=1" download="file one.pdf">Course file</a>
  <img src="/content/enforced/123/diagram.png" alt="Anatomy diagram" title="Reference image">
  <div id="kaltura_player_123" class="kaltura-player" style="width: 640px; height: 360px">
    <script>KalturaPlayer.setup({targetId: 'kaltura_player_123', entryId: '1_abcd'});</script>
  </div>
  <iframe src="https://ocedtech.h5p.com/content/456/embed" title="H5P practice"></iframe>
</main>
"""


def test_ai_styling_may_add_headings_and_rearrange_layout():
    styled = """
    <div class="hero"><h1>Week one</h1></div>
    <div class="card"><h4>Overview</h4>
      <h2>Clinical <strong>résumé</strong> — week one</h2>
      <p>Keep every word, including café, λ, and 한국어.</p>
      <ol><li>First item</li><li>Second item</li></ol>
    </div>
    <div class="card"><h4>Files</h4>
      <iframe src="https://ocedtech.h5p.com/content/456/embed"></iframe>
      <a href="https://learn.example.test/content/enforced/123/file%20one.pdf?download=1">Course file</a>
      <img src="/content/enforced/123/diagram.png">
      <table><tr><th>Day</th><th>Room</th></tr><tr><td>Monday</td><td>HSC 101</td></tr></table>
      <div id="kaltura_player_123"><script>KalturaPlayer.setup({});</script></div>
    </div>
    """
    preserved, reason = content_is_preserved(COMPLEX_HTML, styled)
    assert preserved, reason


def test_ai_styling_that_drops_text_is_rejected():
    styled = COMPLEX_HTML.replace("<p>Keep every word, including café, λ, and 한국어.</p>", "")
    preserved, reason = content_is_preserved(COMPLEX_HTML, styled)
    assert not preserved
    assert "Keep every word" in reason


def test_ai_styling_that_rewords_text_is_rejected():
    styled = COMPLEX_HTML.replace("Second item", "Item two")
    preserved, reason = content_is_preserved(COMPLEX_HTML, styled)
    assert not preserved
    assert "Second item" in reason


@pytest.mark.parametrize(
    "removed",
    [
        '<a href="/content/enforced/123/file%20one.pdf?download=1" download="file one.pdf">Course file</a>',
        '<iframe src="https://ocedtech.h5p.com/content/456/embed" title="H5P practice"></iframe>',
        '<img src="/content/enforced/123/diagram.png" alt="Anatomy diagram" title="Reference image">',
    ],
)
def test_ai_styling_that_drops_a_file_embed_or_image_is_rejected(removed):
    # Keep the visible link text so only the resource itself is missing.
    styled = COMPLEX_HTML.replace(removed, "Course file" if "Course file" in removed else "")
    preserved, reason = content_is_preserved(COMPLEX_HTML, styled)
    assert not preserved
    assert "missing" in reason


def test_ai_styling_that_changes_a_link_target_is_rejected():
    styled = COMPLEX_HTML.replace("file%20one.pdf", "file%20two.pdf")
    preserved, _ = content_is_preserved(COMPLEX_HTML, styled)
    assert not preserved



def test_semantic_readback_allows_wrappers_entities_unicode_and_absolute_urls():
    expected = '<p>Café&nbsp;&amp; tea</p><a href="/content/file%20one.pdf?x=1&amp;y=2">File</a>'
    actual = (
        '<div class="d2l-wrapper"><p> Café &amp;   tea </p>'
        '<a href="https://learn.example.edu/content/file one.pdf?x=1&amp;y=2">File</a></div>'
    )
    equivalent, reason = content_is_equivalent(expected, actual)
    assert equivalent, reason


@pytest.mark.parametrize(
    "changed",
    [
        '<p>Changed wording</p><a href="/file.pdf">File</a><img src="/image.png">',
        '<p>Original wording</p><a href="/other.pdf">File</a><img src="/image.png">',
        '<p>Original wording</p><a href="/file.pdf">File</a><img src="/other.png">',
        '<a href="/file.pdf">File</a><p>Original wording</p><img src="/image.png">',
    ],
)
def test_semantic_readback_rejects_changed_text_resources_and_order(changed):
    original = '<p>Original wording</p><a href="/file.pdf">File</a><img src="/image.png">'
    equivalent, _ = content_is_equivalent(original, changed)
    assert not equivalent


@pytest.mark.parametrize(
    "changed",
    [
        "<p>One</p><p>Two</p>",
        "<table><tr><td>A</td><td>B</td></tr></table>",
    ],
)
def test_semantic_readback_rejects_lost_list_or_table_structure(changed):
    if changed.startswith("<p>"):
        original = "<ul><li>One</li><li>Two</li></ul>"
    else:
        original = "<table><tr><th>A</th><th>B</th></tr></table>"
    equivalent, reason = content_is_equivalent(original, changed)
    assert not equivalent
    assert "list or table structure" in reason


class _Keyboard:
    async def press(self, _keys):
        return None


class _FakeEditorPage:
    def __init__(self):
        self.keyboard = _Keyboard()
        self.frames = []

    async def evaluate(self, script, _arg=None):
        if "deepFind" in script:
            return True
        return None

    async def wait_for_timeout(self, _milliseconds):
        return None


@pytest.mark.asyncio
async def test_page_changer_never_saves_when_paste_does_not_land():
    logs = []
    automator = PageAutomator("https://example.test/topics/1", lambda message, level: logs.append(message))

    async def focus(_page):
        return True

    async def stale_readback(_page):
        return "<p>Old</p>"

    automator._focus_codemirror = focus
    automator._read_editor_full_text = stale_readback
    assert not await automator.replace_html_in_editor(
        _FakeEditorPage(), "<p>Much longer newly styled wording.</p>"
    )
    assert any("aborting save" in message for message in logs)


@pytest.mark.asyncio
async def test_unit_collector_never_saves_when_paste_does_not_land():
    logs = []
    collector = UnitCollector.__new__(UnitCollector)
    collector.log = lambda message, level="info": logs.append(message)
    collector._clipboard_lock = __import__("asyncio").Lock()

    async def stale_readback(_page):
        return "<p>Old</p>"

    collector._read_editor_full_text = stale_readback
    styled = '<p>Original wording.</p><a href="/original.pdf">File</a>'
    assert not await collector._paste_html(_FakeEditorPage(), styled)
    assert any("aborting save" in message for message in logs)


@pytest.mark.asyncio
async def test_unit_collector_accepts_paste_that_brightspace_reformats():
    logs = []
    collector = UnitCollector.__new__(UnitCollector)
    collector.log = lambda message, level="info": logs.append(message)
    collector._clipboard_lock = __import__("asyncio").Lock()
    styled = '<h4>Files</h4><p>Original wording.</p><a href="/original.pdf">File</a>'

    async def reformatted_readback(_page):
        return (
            '<h4>Files</h4>\n<p>Original wording.</p>\n'
            '<p><a href="https://x.test/original.pdf">File</a></p>'
        )

    collector._read_editor_full_text = reformatted_readback
    assert await collector._paste_html(_FakeEditorPage(), styled)
