import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from collector_section_check import misplaced_section_links


def test_slide_links_stay_under_lecture_slides_heading():
    href = "https://learn.test/topics/42"
    markup = (
        f'<h2>Lecture Slides</h2><p><a href="{href}">Week 7</a></p>'
        '<h2>Lecture Recordings</h2>'
    )
    assert misplaced_section_links(markup, [("Lecture Slides", href)]) == []


def test_slide_link_moved_to_generic_links_section_is_rejected():
    href = "https://learn.test/topics/42"
    markup = (
        '<h2>Lecture Slides</h2><h2>Links</h2>'
        f'<p><a href="{href}">Week 7</a></p>'
    )
    assert misplaced_section_links(markup, [("Lecture Slides", href)])
