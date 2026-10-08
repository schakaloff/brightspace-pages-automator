"""Folder regressions reproduced on Data Collection Methods (unit 982573)."""

import asyncio
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import editor_save
from collector_section_check import misplaced_section_links
from unit_collector import UnitCollector, split_by_visibility


def collector():
    return UnitCollector(
        "https://learn.test/d2l/le/lessons/42/units/7",
        "https://learn.test/d2l/le/lessons/42/topics/99", "lake", {},
    )


def topic(topic_id):
    return {
        "topic_id": str(topic_id), "label": f"Document {topic_id}", "type": "file",
        "url": f"https://learn.test/d2l/le/lessons/42/topics/{topic_id}",
    }


class FolderPage:
    def __init__(self, hidden=False):
        # Brightspace embeds only summaries for the four folder documents.
        summaries = [{"Id": n, "Type": 1, "Title": f"Document {n}"} for n in range(1, 5)]
        self.structures = {
            "7": [{"Id": 10, "Type": 0, "IsHidden": hidden, "Structure": summaries}],
            "10": summaries,
        }
        self.modules = {
            "10": {"Id": 10, "ParentModuleId": 7, "IsHidden": hidden,
                   "Title": "Supporting documents", "Description": {"Html": "<p>Use these forms.</p>"}},
        }
        self.details = {
            str(n): {"Id": n, "ParentModuleId": 10, "IsHidden": False,
                     "Url": f"/content/enforced/42/Document {n}.docx",
                     "Description": {"Html": f"<p>Instructions for form {n}.</p>"}}
            for n in range(1, 5)
        }

    async def evaluate(self, script, args):
        if "GET module" in script:
            return self.modules[str(args[1])]
        if "/content/topics/" in script:
            return self.details[str(args[1])]
        return self.structures[str(args[1])]


def test_four_nested_documents_are_read_and_ordered_by_brightspace():
    c = collector()
    topics = [topic(n) for n in [4, 2, 1, 3]]
    assert asyncio.run(c._fetch_hidden_topic_ids(FolderPage(), topics)) == set()
    assert [t["topic_id"] for t in topics] == ["1", "2", "3", "4"]
    assert c._topic_metadata["1"]["Url"].endswith("Document 1.docx")
    assert c._topic_metadata["1"]["_FolderPath"] == ("10",)
    assert c._folder_metadata["10"]["Description"]["Html"] == "<p>Use these forms.</p>"


def test_hidden_folder_routes_visible_documents_to_the_instructor_page():
    c = collector()
    topics = [topic(n) for n in range(1, 5)]
    hidden_ids = asyncio.run(c._fetch_hidden_topic_ids(FolderPage(hidden=True), topics))
    visible, hidden = split_by_visibility(topics, hidden_ids)
    assert visible == []
    assert [t["topic_id"] for t in hidden] == ["1", "2", "3", "4"]


def test_hidden_ancestor_applies_through_multiple_folder_levels():
    c, page = collector(), FolderPage(hidden=True)
    page.structures["10"] = [{"Id": 11, "Type": 0, "Structure": [{"Id": 1, "Type": 1}]}]
    page.structures["11"] = [{"Id": 1, "Type": 1}]
    page.modules["11"] = {"Id": 11, "ParentModuleId": 10, "IsHidden": False,
                          "Title": "Samples", "Description": None}
    page.details["1"]["ParentModuleId"] = 11
    assert asyncio.run(c._fetch_hidden_topic_ids(page, [topic(1)])) == {"1"}
    assert c._topic_metadata["1"]["_FolderPath"] == ("10", "11")


@pytest.mark.parametrize("field,value", [("IsHidden", None), ("Description", "bad"), ("ParentModuleId", 8)])
def test_unreadable_or_moved_folder_stops_collection(field, value):
    c, page = collector(), FolderPage()
    page.modules["10"][field] = value
    assert asyncio.run(c._fetch_hidden_topic_ids(page, [topic(1)])) is None
    assert c._topic_metadata == {}


@pytest.mark.parametrize("field,value", [("IsHidden", None), ("Description", "bad"), ("ParentModuleId", 8), ("Id", 77)])
def test_unreadable_or_moved_nested_topic_stops_collection(field, value):
    c, page = collector(), FolderPage()
    page.details["1"][field] = value
    assert asyncio.run(c._fetch_hidden_topic_ids(page, [topic(1)])) is None
    assert c._topic_metadata == {}


def test_topic_outside_the_unit_stops_with_its_name_and_id():
    c, logs = collector(), []
    c._log_fn = lambda message, _level: logs.append(message)
    assert asyncio.run(c._fetch_hidden_topic_ids(FolderPage(), [topic(77)])) is None
    assert any("Document 77 (ID 77)" in message for message in logs)


def test_cyclic_folder_structure_stops_without_recursing_forever():
    c, page = collector(), FolderPage()
    page.structures["10"] = [{"Id": 7, "Type": 0, "Structure": []}]
    assert asyncio.run(c._fetch_hidden_topic_ids(page, [topic(1)])) is None


class Tab:
    async def goto(self, *_args, **_kwargs):
        pass

    async def close(self):
        pass


class Context:
    async def new_page(self):
        return Tab()


def install_saves(monkeypatch, c):
    saved, requirements = [], []

    async def save(_tab, _url, markup, _log):
        saved.append(markup)
        return True

    async def transforms(*_args, **_kwargs):
        return True

    async def style(*_args, **kwargs):
        requirements.extend(kwargs["required_section_links"])
        assert misplaced_section_links(saved[-1], requirements) == []
        return True

    async def no_upload(*_args):
        raise AssertionError("folder documents must stay linked in their source folder")

    monkeypatch.setattr(editor_save, "replace_topic_html", save)
    monkeypatch.setattr(c, "_apply_youtube_transforms", transforms)
    monkeypatch.setattr(c, "_apply_claude_style", style)
    monkeypatch.setattr(c, "_upload_files", no_upload)
    c.claude_api_key = "unused"
    return saved, requirements


def test_folder_documents_and_descriptions_stay_grouped_without_reupload(monkeypatch):
    c, topics = collector(), [topic(n) for n in range(1, 5)]
    assert asyncio.run(c._fetch_hidden_topic_ids(FolderPage(), topics)) == set()
    saved, requirements = install_saves(monkeypatch, c)
    assert asyncio.run(c._collect_into(Context(), topics, c.target_url))
    soup = BeautifulSoup(saved[-1], "html.parser")
    folder = soup.find("section", class_="bpa-folder")
    assert folder.h2.get_text() == "Supporting documents"
    assert [a.get_text() for a in folder.find_all("a")] == [f"Document {n}" for n in range(1, 5)]
    assert "Use these forms." in folder.get_text()
    assert all(f"Instructions for form {n}." in folder.get_text() for n in range(1, 5))
    assert len(requirements) == 4
    assert "<h2>Files</h2>" not in saved[-1]


def test_nested_headings_and_sibling_resources_keep_their_folder_boundaries(monkeypatch):
    c = collector()
    topics = [topic(n) for n in range(1, 5)]
    c._topic_metadata = {
        "1": {"_FolderPath": ("10",)},
        "2": {"_FolderPath": ("10", "11")},
        "3": {"_FolderPath": ("10",)},
        "4": {"_FolderPath": ()},
    }
    c._folder_metadata = {"10": {"Title": "Forms"}, "11": {"Title": "Examples"}}

    async def scrape(_context, t, _semaphore):
        return {"topic": t, "html": f"<h1>Lesson {t['topic_id']}</h1><p>Read this.</p>",
                "link_url": None, "file": None}

    monkeypatch.setattr(c, "_scrape_topic", scrape)
    saved, _ = install_saves(monkeypatch, c)
    assert asyncio.run(c._collect_into(Context(), topics, c.target_url))
    soup = BeautifulSoup(saved[-1], "html.parser")
    forms = soup.find("section", attrs={"data-folder-id": "10"})
    examples = soup.find("section", attrs={"data-folder-id": "11"})
    assert examples.h3.get_text() == "Examples"
    assert examples.find("h4", string="Lesson 2") is not None
    assert forms.find("h3", string="Lesson 3").find_parent("section") == forms
    assert soup.find("h1", string="Lesson 4").find_parent("section") is None


def test_failed_folder_topic_keeps_its_original_link_in_the_folder(monkeypatch):
    c, topics = collector(), [topic(1)]
    assert asyncio.run(c._fetch_hidden_topic_ids(FolderPage(), topics)) == set()

    async def failed(*_args):
        raise RuntimeError("download failed")

    monkeypatch.setattr(c, "_scrape_topic", failed)
    saved, _ = install_saves(monkeypatch, c)
    assert not asyncio.run(c._collect_into(Context(), topics, c.target_url))
    folder = BeautifulSoup(saved[-1], "html.parser").find("section", class_="bpa-folder")
    assert folder.a["href"] == topics[0]["url"]
    assert "Instructions for form 1." in folder.get_text()


def test_downloaded_folder_resource_keeps_its_link_out_of_generic_files(monkeypatch):
    c, topics = collector(), [topic(1)]
    assert asyncio.run(c._fetch_hidden_topic_ids(FolderPage(), topics)) == set()

    async def scrape(_context, t, _semaphore):
        return {"topic": t, "html": None, "link_url": None,
                "file": {"filename": "archive.zip", "path": "C:/tmp/archive.zip",
                         "direct_url": "/content/enforced/42/archive.zip"}}

    monkeypatch.setattr(c, "_scrape_topic", scrape)
    saved, _ = install_saves(monkeypatch, c)
    assert asyncio.run(c._collect_into(Context(), topics, c.target_url))
    assert BeautifulSoup(saved[-1], "html.parser").find("section").a["href"] == "/content/enforced/42/archive.zip"


def test_styling_cannot_move_a_document_out_of_its_folder():
    url = "/content/enforced/42/form.docx"
    moved = f'<section class="bpa-folder"><h2>Supporting documents</h2></section><p><a href="{url}">Form</a></p>'
    assert misplaced_section_links(moved, [("Supporting documents", url)])


def test_folder_wrapper_does_not_relax_the_lecture_slides_section_check():
    url = "/content/enforced/42/slides.pdf"
    moved = (
        '<section class="bpa-folder"><h2>Resources</h2>'
        '<h3>Lecture Slides</h3><h3>Lecture Recordings</h3>'
        f'<p><a href="{url}">Slides</a></p></section>'
    )
    assert misplaced_section_links(moved, [("Lecture Slides", url)])
    assert not misplaced_section_links(moved, [("Resources", url)])
