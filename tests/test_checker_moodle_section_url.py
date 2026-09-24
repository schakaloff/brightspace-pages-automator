"""Moodle section IDs must stay on section.php during Checker navigation."""

import sys

sys.path.insert(0, "src")

from content_checker import (
    _is_moodle_scrape_page,
    _is_moodle_scrape_target,
    _moodle_scrape_target_url,
)


def test_section_link_keeps_its_section_id():
    section = "https://mymoodle.example/course/section.php?id=1243011"
    assert _moodle_scrape_target_url(section) == section
    assert _is_moodle_scrape_page(section)
    assert _is_moodle_scrape_target(section, section)


def test_enrol_link_still_resolves_to_course_page():
    enrol = "https://mymoodle.example/enrol/index.php?id=169218"
    course = "https://mymoodle.example/course/view.php?id=169218"
    assert _moodle_scrape_target_url(enrol) == course
    assert _is_moodle_scrape_target(course, course)
    assert not _is_moodle_scrape_page(enrol)


def test_other_course_page_does_not_skip_navigation_to_requested_section():
    section = "https://mymoodle.example/course/section.php?id=1243011"
    assert not _is_moodle_scrape_target(
        "https://mymoodle.example/course/view.php?id=169218", section
    )
    assert not _is_moodle_scrape_target(
        "https://mymoodle.example/course/section.php?id=1243012", section
    )
