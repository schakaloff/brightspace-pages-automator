"""Generated links should use Brightspace's New window setting."""

import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from link_behavior import open_page_links_in_new_window


def test_navigational_links_open_new_window_without_changing_text_or_href():
    source = (
        '<p><a href="/d2l/le/lessons/42/topics/2">Slides: IAM</a></p>'
        '<p><a href="https://zoom.test/j/1" rel="nofollow" target="_self">Join Zoom</a></p>'
        '<p><a href="/content/enforced/42/handout.pdf">Handout</a></p>'
    )
    result = open_page_links_in_new_window(source)
    anchors = BeautifulSoup(result, "html.parser").find_all("a")

    assert [a.get_text() for a in anchors] == ["Slides: IAM", "Join Zoom", "Handout"]
    assert [a["href"] for a in anchors] == [
        "/d2l/le/lessons/42/topics/2",
        "https://zoom.test/j/1",
        "/content/enforced/42/handout.pdf",
    ]
    assert all(a.get("target") == "_blank" for a in anchors)
    assert all("noopener" in a.get("rel", []) for a in anchors)
    assert "nofollow" in anchors[1]["rel"]
    assert open_page_links_in_new_window(result) == result


def test_fragment_and_action_links_are_untouched():
    source = (
        '<a href="#schedule">Schedule</a>'
        '<a href="mailto:prof@example.test">Email</a>'
        '<a href="tel:+12505551234">Call</a>'
        '<a href="javascript:void(0)">Editor action</a>'
    )
    assert open_page_links_in_new_window(source) == source
