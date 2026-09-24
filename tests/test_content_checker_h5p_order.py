"""Existing H5P pages can be recognized and ordered without inserting copies."""

import asyncio
import sys

sys.path.insert(0, "src")

from content_checker import ContentChecker


class StructurePage:
    def __init__(self, html):
        self.html = html

    async def evaluate(self, script, args):
        if "/structure/" in script:
            return [{
                "Id": 42,
                "Type": 1,
                "Title": "Practice",
                "Url": "/content/enforced/1/practice.html",
            }]
        if "/topics/" in script:
            return self.html
        raise AssertionError("Unexpected Brightspace request")


def test_h5p_embedded_in_html_page_is_already_present():
    checker = ContentChecker.__new__(ContentChecker)
    page = StructurePage(
        '<iframe src="https://ocedtech.h5p.com/content/456/embed"></iframe>'
    )

    found = asyncio.run(checker._verify_topic_in_module(
        page, "1", "2", "Practice", require_h5p=True
    ))

    assert found is True


def test_same_named_plain_page_is_not_mistaken_for_h5p():
    checker = ContentChecker.__new__(ContentChecker)
    page = StructurePage('<p>Practice instructions without an interactive.</p>')

    found = asyncio.run(checker._verify_topic_in_module(
        page, "1", "2", "Practice", require_h5p=True
    ))

    assert found is False


def test_brightspace_lti_quicklink_page_is_recognized():
    checker = ContentChecker.__new__(ContentChecker)
    page = StructurePage(
        '<iframe src="https://learn.example/d2l/common/dialogs/quickLink/quickLink.d2l'
        '?ou=21808&amp;type=lti&amp;rCode=example"></iframe>'
    )

    found = asyncio.run(checker._verify_topic_in_module(
        page, "1", "2", "Practice", require_h5p=True
    ))

    assert found is True
    assert ContentChecker._html_contains_h5p_embed(
        '<iframe src="https://learn.example/d2l/common/dialogs/quickLink/quickLink.d2l'
        '?type=content&amp;rCode=example"></iframe>'
    ) is False


def test_order_existing_pages_moves_to_moodle_order(monkeypatch):
    import unit_overview

    children = [
        {"Id": 1, "Type": 1, "Title": "A second"},
        {"Id": 2, "Type": 1, "Title": "Z first"},
    ]

    class FakeAPI:
        def __init__(self, page, course_id, module_id):
            assert (course_id, module_id) == ("1", "7")

        async def list_structure(self):
            return list(children)

    class FakePage:
        async def evaluate(self, script, args):
            assert "position=last" in script
            topic_id = int(args[1])
            child = next(item for item in children if item["Id"] == topic_id)
            children.remove(child)
            children.append(child)

    checker = ContentChecker.__new__(ContentChecker)
    checker.stop_flag = [False]
    checker.log = lambda *args: None

    async def toc(*args):
        return [{"kind": "MODULE", "id": 7, "title": "Vit 22"}]

    checker._fetch_bs_toc = toc
    monkeypatch.setattr(unit_overview, "BrowserContentAPI", FakeAPI)
    results = [
        {"type": "EXTERNAL", "name": "Z first", "section": "Vit 22"},
        {"type": "EXTERNAL", "name": "A second", "section": "Vit 22"},
    ]

    asyncio.run(checker._order_units_like_moodle(FakePage(), "1", results))

    assert [child["Title"] for child in children] == ["Z first", "A second"]
