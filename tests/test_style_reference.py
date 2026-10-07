from pathlib import Path

from bs4 import BeautifulSoup

from content_preservation import content_is_preserved


ROOT = Path(__file__).resolve().parents[1]


def test_shared_style_reference_uses_quiet_hierarchy_and_resource_groups():
    reference = (ROOT / "templates" / "style_reference.html").read_text(encoding="utf-8")

    for selector in (
        ".info-list",
        ".schedule-panel",
        ".action-link",
        ".resource-section",
        ".link-list",
        ".link-row",
        ".resource-link",
        ".sr-only",
    ):
        assert selector in reference
    assert "bootstrap-icons" not in reference
    assert "resource-badge" not in reference
    assert "resource-icon" not in reference
    assert "background: linear-gradient" not in reference
    assert "color: var(--charcoal);" in reference
    assert '<main class="content-body">' in reference


def test_calm_reference_uses_readable_text_without_icons_or_left_bars():
    reference = (ROOT / "templates" / "style_reference.html").read_text(encoding="utf-8")

    assert ".info-icon" not in reference
    assert "border-left:" not in reference
    assert "border: 2px solid var(--primary);" not in reference
    assert "font-size: 0.95rem;" in reference
    assert "text-transform: none;" in reference


def test_calm_prompt_separates_links_and_files_without_generated_markers():
    styler = (ROOT / "src" / "ai_styler.py").read_text(encoding="utf-8")

    assert "RESOURCE DIRECTORY OVERRIDE" in styler
    assert "For an unlabeled, mixed resource list" in styler
    assert "Lecture Slides, Lecture Recordings, or Course Materials" in styler
    assert "never leave that heading empty" in styler
    assert "ACCESSIBLE STRUCTURE" in styler
    assert "PLAIN RESOURCE ROWS" in styler
    assert "Do not add PDF/LINK/PAGE badges" in styler
    assert "no large empty" in styler
    assert "BRIGHTSPACE AUTHORING" in styler
    assert "exactly one shallow text-only anchor" in styler
    assert "duplicate href" in styler


def test_resource_rows_keep_links_shallow_and_easy_to_edit():
    reference = (ROOT / "templates" / "style_reference.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(reference, "html.parser")

    link_row = soup.select_one("p.link-row")
    link = link_row.select_one("a.link-title")
    assert link is not None
    assert not link.find(True)

    resource_row = soup.select_one("p.resource-row")
    resource_link = resource_row.select_one("a.resource-link")
    assert resource_link is not None
    assert not resource_link.find(True)

    assert not soup.select(".link-action, .resource-action")
    assert not soup.select(".resource-badge, .resource-icon, .bi")
    assert "bi-box-arrow-up-right" not in reference
    assert "bi-download" not in reference
    assert "grid-template-columns" not in reference
    assert "display: grid" not in reference


def test_resource_reference_uses_compact_spacing():
    reference = (ROOT / "templates" / "style_reference.html").read_text(encoding="utf-8")

    assert ".resource-directory .content-body { padding: 0.75rem 3rem 3rem; }" in reference
    assert ".resource-directory .content-body > .action-card:first-child { padding-top: 0; }" in reference
    assert ".resource-directory .link-row, .resource-directory .resource-row { padding: 0.7rem 0; }" in reference


def test_every_theme_prompt_leaves_layout_to_the_selected_design():
    prompts = list((ROOT / "prompts").glob("*.txt"))

    assert len(prompts) == 9
    for prompt in prompts:
        text = prompt.read_text(encoding="utf-8")
        assert "The selected reference and design instructions control the layout" in text
        assert "Do not repeat a heading" in text
        assert "Use a calm hierarchy" not in text
        assert ".action-link" not in text
        assert ".info-list" not in text
        assert ".resource-row" not in text


def test_zoom_button_can_keep_the_original_url_for_content_preservation():
    source = '<a href="https://zoom.us/j/123">https://zoom.us/j/123</a>'
    styled = (
        '<a class="action-link" href="https://zoom.us/j/123">'
        'Join recurring Zoom meeting'
        '<span class="sr-only">https://zoom.us/j/123</span>'
        '</a>'
    )

    assert content_is_preserved(source, styled) == (True, "")
