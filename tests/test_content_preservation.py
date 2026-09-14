import sys

import pytest

sys.path.insert(0, "src")

from content_preservation import (
    ContentProtectionError,
    add_generated_heading,
    content_is_equivalent,
    generated_title_matches_leading_content,
    protect_html,
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


def test_parser_protection_round_trips_text_lists_tables_files_images_and_embeds():
    protection = protect_html(COMPLEX_HTML)
    assert "Keep every word" not in protection.protected_html
    assert "file%20one.pdf" not in protection.protected_html
    assert "kaltura_player_123" not in protection.protected_html
    assert "ocedtech.h5p.com" not in protection.protected_html

    candidate = protection.protected_html.replace('class="old-layout"', 'class="new-layout card"')
    restored = protection.restore_and_validate(candidate)
    equivalent, reason = content_is_equivalent(COMPLEX_HTML, restored)
    assert equivalent, reason
    assert "Keep every word, including café, λ, and 한국어." in restored
    assert "kaltura_player_123" in restored
    assert "ocedtech.h5p.com/content/456/embed" in restored


def test_missing_placeholder_is_rejected():
    protection = protect_html("<p>One</p><p>Two</p>")
    candidate = protection.protected_html.replace(protection.tokens[0], "", 1)
    with pytest.raises(ContentProtectionError, match="missing"):
        protection.restore_and_validate(candidate)


def test_duplicated_placeholder_is_rejected():
    protection = protect_html("<p>One</p><p>Two</p>")
    candidate = protection.protected_html.replace(
        "</body>", f"{protection.tokens[0]}</body>", 1
    )
    with pytest.raises(ContentProtectionError, match="duplicated"):
        protection.restore_and_validate(candidate)


def test_reordered_placeholders_are_rejected():
    protection = protect_html("<p>One</p><p>Two</p>")
    first, second = protection.tokens[:2]
    candidate = protection.protected_html.replace(first, "__SWAP__").replace(second, first).replace("__SWAP__", second)
    with pytest.raises(ContentProtectionError, match="reordered"):
        protection.restore_and_validate(candidate)


def test_new_unprotected_ai_text_is_rejected():
    protection = protect_html("<p>Original sentence.</p>")
    candidate = protection.protected_html.replace("</body>", "<p>AI-added sentence.</p></body>")
    with pytest.raises(ContentProtectionError, match="visible text changed"):
        protection.restore_and_validate(candidate)


def test_ai_cannot_add_visible_text_through_css_pseudo_content():
    protection = protect_html("<p>Original sentence.</p>")
    candidate = protection.protected_html.replace(
        "<body>", '<head><style>p::after { content: "AI-added words"; }</style></head><body>'
    )
    with pytest.raises(ContentProtectionError, match="CSS-generated visible content"):
        protection.restore_and_validate(candidate)


def test_malformed_claude_html_is_rejected_even_if_the_placeholder_survives():
    protection = protect_html("<p>Original sentence.</p>")
    candidate = f"<div><span>{protection.tokens[0]}</div>"
    with pytest.raises(ContentProtectionError, match="malformed HTML"):
        protection.restore_and_validate(candidate)


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
async def test_page_changer_never_saves_when_pasted_content_differs():
    logs = []
    automator = PageAutomator("https://example.test/topics/1", lambda message, level: logs.append(message))

    async def focus(_page):
        return True

    async def changed_readback(_page):
        return "<p>Claude changed this wording.</p>"

    automator._focus_codemirror = focus
    automator._read_editor_full_text = changed_readback
    assert not await automator.replace_html_in_editor(_FakeEditorPage(), "<p>Original wording.</p>")
    assert any("aborting without saving" in message for message in logs)


@pytest.mark.asyncio
async def test_unit_collector_never_saves_when_pasted_content_differs():
    logs = []
    collector = UnitCollector.__new__(UnitCollector)
    collector.log = lambda message, level="info": logs.append(message)
    collector._clipboard_lock = __import__("asyncio").Lock()

    async def changed_readback(_page):
        return '<p>Original wording.</p><a href="/changed.pdf">File</a>'

    collector._read_editor_full_text = changed_readback
    original = '<p>Original wording.</p><a href="/original.pdf">File</a>'
    assert not await collector._paste_html(_FakeEditorPage(), original)
    assert any("aborting without saving" in message for message in logs)
