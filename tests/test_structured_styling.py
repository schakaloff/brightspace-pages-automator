import json
import sys

sys.path.insert(0, "src")

from content_preservation import content_is_preserved
from structured_styling import (
    PagePlan,
    PlannedComponent,
    analysis_payload,
    build_conservative_plan,
    extract_document,
    plan_from_json,
    render_plan,
    validate_plan,
)


def _kinds(plan):
    return [component.kind for component in plan.components]


def test_page_without_meeting_does_not_invent_meeting_component():
    source = """
        <h2>Course Information</h2>
        <p>Professor: Troy Berg</p>
        <p>Class Schedule: Tuesday 6:00pm</p>
        <a href="https://example.edu/library">College Library</a>
    """
    document = extract_document(source)
    plan = build_conservative_plan(document, page_type="course_information")

    assert "contact" in _kinds(plan)
    assert "schedule" in _kinds(plan)
    assert "meeting" not in _kinds(plan)

    result = render_plan(document, plan)
    assert result.used_fallback is False
    assert "zoom" not in result.html.casefold()
    assert content_is_preserved(source, result.html)[0] is True


def test_real_meeting_link_can_use_meeting_component():
    source = '<p><a href="https://ca01web.zoom.us/j/123">Weekly class meeting</a></p>'
    document = extract_document(source)
    plan = build_conservative_plan(document)

    assert _kinds(plan) == ["meeting"]
    assert render_plan(document, plan).used_fallback is False


def test_top_level_link_is_classified_as_a_link():
    source = '<a href="https://example.edu/library">College Library</a>'
    document = extract_document(source)

    assert document.blocks[0].hrefs == ("https://example.edu/library",)
    assert build_conservative_plan(document).components[0].kind == "links"


def test_fake_meeting_component_is_rejected_and_original_is_returned():
    source = '<p><a href="https://example.edu/library">College Library</a></p>'
    document = extract_document(source)
    invalid = PagePlan(
        source_hash=document.source_hash,
        page_type="resources",
        components=(PlannedComponent("meeting", (document.block_ids[0],)),),
    )

    validation = validate_plan(document, invalid)
    result = render_plan(document, invalid)

    assert validation.valid is False
    assert "no source evidence" in validation.errors[0]
    assert result.used_fallback is True
    assert result.html == source


def test_non_heading_cannot_be_mislabeled_as_heading():
    source = "<p>This is ordinary body text.</p>"
    document = extract_document(source)
    invalid = PagePlan(
        source_hash=document.source_hash,
        page_type="general",
        components=(PlannedComponent("heading", (document.block_ids[0],)),),
    )

    result = render_plan(document, invalid)

    assert result.used_fallback is True
    assert result.html == source


def test_unknown_content_is_preserved_as_ordinary_content():
    source = """
        <details><summary>Optional lab</summary><p>Use the unusual simulator.</p></details>
        <custom-widget mode="practice">Keep this widget</custom-widget>
    """
    document = extract_document(source)
    plan = build_conservative_plan(document)
    result = render_plan(document, plan)

    assert _kinds(plan) == ["content", "content"]
    assert result.used_fallback is False
    assert "<details>" in result.html
    assert "<custom-widget" in result.html
    assert content_is_preserved(source, result.html)[0] is True


def test_plan_must_account_for_every_block_exactly_once():
    source = "<h2>Resources</h2><p>Read chapter one.</p><p>Complete the activity.</p>"
    document = extract_document(source)
    first, second, _third = document.block_ids
    invalid = PagePlan(
        source_hash=document.source_hash,
        page_type="general",
        components=(
            PlannedComponent("heading", (first,)),
            PlannedComponent("content", (second, second)),
        ),
    )

    validation = validate_plan(document, invalid)
    result = render_plan(document, invalid)

    assert validation.valid is False
    assert any("missing" in error for error in validation.errors)
    assert any("duplicated" in error for error in validation.errors)
    assert result.html == source


def test_plan_for_old_page_version_is_rejected():
    first_document = extract_document("<p>Old content</p>")
    second_document = extract_document("<p>New content</p>")
    stale_plan = build_conservative_plan(first_document)

    validation = validate_plan(second_document, stale_plan)

    assert validation.valid is False
    assert "different source page" in validation.errors[0]


def test_analysis_payload_contains_content_but_no_style_decisions():
    source = """
        <p>Professor: Ada Lovelace</p>
        <p><a href="/content/enforced/12/course-outline.pdf">Course outline</a></p>
    """
    document = extract_document(source)
    payload = analysis_payload(document)

    assert payload["source_hash"] == document.source_hash
    assert payload["blocks"][0]["text"] == "Professor: Ada Lovelace"
    assert "contact" in payload["blocks"][0]["hints"]
    assert "files" in payload["blocks"][1]["hints"]
    assert "font_size" not in json.dumps(payload)
    assert "color" not in json.dumps(payload)


def test_json_plan_round_trip_can_reorder_without_losing_content():
    source = "<p>Second in the proposal</p><p>First in the proposal</p>"
    document = extract_document(source)
    first, second = document.block_ids
    raw_plan = json.dumps(
        {
            "source_hash": document.source_hash,
            "page_type": "lesson",
            "components": [
                {"kind": "content", "block_ids": [second]},
                {"kind": "content", "block_ids": [first]},
            ],
        }
    )

    plan = plan_from_json(raw_plan)
    result = render_plan(document, plan)

    assert result.used_fallback is False
    assert result.html.index("First in the proposal") < result.html.index("Second in the proposal")
    assert content_is_preserved(source, result.html)[0] is True
