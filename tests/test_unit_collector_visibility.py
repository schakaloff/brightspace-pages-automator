"""Topics hidden from students must never reach the student page."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unit_collector import (
    is_collector_target_title,
    link_known_topic_references,
    split_by_visibility,
)


def test_collector_generated_pages_are_not_valid_source_topics():
    assert is_collector_target_title("Instructor Resources — Combined")
    assert is_collector_target_title("Unit 1 — Combined (Instructor)")
    assert is_collector_target_title("Unit 1 - Combined")
    assert not is_collector_target_title("Combined resources")
    assert not is_collector_target_title("Instructor Resources")


def test_plain_moodle_activity_name_becomes_brightspace_topic_link():
    source = (
        '<div class="card-body"><span style="font-size: .9375rem;">'
        "How Every Child can Thrive by 5 video</span></div>"
    )
    topics = [{
        "label": "How Every Child can Thrive by 5 video",
        "url": "https://learn.test/d2l/le/lessons/42/topics/99",
    }]

    result = link_known_topic_references(source, topics)

    assert '<a href="https://learn.test/d2l/le/lessons/42/topics/99">' in result
    assert "How Every Child can Thrive by 5 video</a>" in result


def test_matching_moodle_anchor_is_retargeted_to_brightspace():
    source = (
        '<a href="https://moodle.test/mod/resource/view.php?id=7">'
        "BC Ministry of Education and Child Care</a>"
    )
    topics = [{
        "label": "BC Ministry of Education and Child Care",
        "url": "https://learn.test/d2l/le/lessons/42/topics/100",
    }]

    result = link_known_topic_references(source, topics)

    assert "moodle.test" not in result
    assert 'href="https://learn.test/d2l/le/lessons/42/topics/100"' in result


def test_topic_reference_linking_skips_self_links_and_ambiguous_titles():
    current = "https://learn.test/d2l/le/lessons/42/topics/1"
    source = "<p>Overview</p><p>Repeated</p>"
    topics = [
        {"label": "Overview", "url": current},
        {"label": "Repeated", "url": "https://learn.test/topics/2"},
        {"label": "Repeated", "url": "https://learn.test/topics/3"},
    ]

    result = link_known_topic_references(source, topics, current)

    assert "<a " not in result


def _topic(topic_id, label="Topic"):
    return {"topic_id": topic_id, "label": label, "url": f"/topics/{topic_id}"}


def test_hidden_ids_go_to_the_instructor_side():
    topics = [_topic("1", "Visible"), _topic("2", "Staff only")]
    visible, hidden = split_by_visibility(topics, {"2"})
    assert [t["label"] for t in visible] == ["Visible"]
    assert [t["label"] for t in hidden] == ["Staff only"]


def test_ids_compare_as_strings_not_numbers():
    """D2L returns ids as numbers in JSON and strings in the DOM."""
    visible, hidden = split_by_visibility([_topic(962726)], {"962726"})
    assert not visible and len(hidden) == 1


def test_a_topic_with_no_id_is_treated_as_hidden():
    """An unreadable id cannot be proven safe, so it must not be published."""
    visible, hidden = split_by_visibility([_topic(None, "Unknown")], set())
    assert not visible
    assert hidden[0]["label"] == "Unknown"


def test_nothing_hidden_leaves_every_topic_visible():
    visible, hidden = split_by_visibility([_topic("1"), _topic("2")], set())
    assert len(visible) == 2 and hidden == []


def test_every_topic_hidden_leaves_the_student_page_empty():
    visible, hidden = split_by_visibility([_topic("1"), _topic("2")], {"1", "2"})
    assert visible == [] and len(hidden) == 2


def test_the_hidden_flag_is_recorded_on_each_topic():
    topics = [_topic("1"), _topic("2")]
    split_by_visibility(topics, {"2"})
    assert [t["hidden"] for t in topics] == [False, True]
