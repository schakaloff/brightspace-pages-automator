import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from collector_file_validation import (
    direct_slide_url, external_topic_url, matching_direct_file_url, validate_topic_download,
)


def test_slide_topic_rejects_the_layout_stylesheet_from_the_failed_run():
    valid, reason = validate_topic_download(
        "Slides: PCIT 102.7 2025-12-09 IAM",
        "layout.css",
        "PCIT-102.7-IAM.pptx",
    )
    assert not valid
    assert "presentation" in reason


def test_slide_topic_accepts_a_presentation_matching_brightspace_metadata():
    assert validate_topic_download(
        "Slides: PCIT 102.7 2025-12-09 IAM",
        "PCIT-102.7-IAM.pptx",
        "PCIT-102.7-IAM.pptx",
    ) == (True, "")


def test_non_slide_topic_rejects_download_of_page_asset():
    valid, reason = validate_topic_download("Week 1 readings", "layout.css")
    assert not valid
    assert "page asset" in reason


def test_explicit_css_resource_can_still_be_collected():
    assert validate_topic_download("layout.css", "layout.css", "layout.css") == (True, "")


def test_brightspace_metadata_rejects_wrong_file_type():
    valid, reason = validate_topic_download("Course workbook", "notes.pdf", "workbook.xlsx")
    assert not valid
    assert "disagrees" in reason


def test_brightspace_metadata_rejects_wrong_file_with_same_extension():
    valid, reason = validate_topic_download("Week 1 slides", "week-2.pptx", "week-1.pptx")
    assert not valid
    assert "name" in reason


def test_unrelated_course_asset_cannot_be_used_as_the_file_fallback():
    assert matching_direct_file_url(
        "lecture.pdf", "https://learn.test/content/enforced/42/layout.css"
    ) == ""
    assert matching_direct_file_url(
        "lecture.pdf", "https://learn.test/content/enforced/42/lecture.pdf"
    ).endswith("/lecture.pdf")


def test_explicit_course_slide_url_can_be_linked_without_reupload():
    topic = "https://learn.test/d2l/le/lessons/42/topics/7"
    assert direct_slide_url(
        topic, "/content/enforced/42/iam.pptx", "Slides: IAM"
    ) == "https://learn.test/content/enforced/42/iam.pptx"
    assert not direct_slide_url(topic, "/content/enforced/42/layout.css", "Slides: IAM")
    assert not direct_slide_url(topic, "https://elsewhere.test/iam.pptx", "Slides: IAM")


def test_zoom_topic_metadata_is_kept_as_a_link_not_downloaded():
    topic = "https://learn.test/d2l/le/lessons/42/topics/7"
    zoom = "https://ca01web.zoom.us/rec/share/meeting-token"
    assert external_topic_url(topic, zoom) == zoom
    assert not external_topic_url(topic, "https://learn.test/content/enforced/42/layout.css")
