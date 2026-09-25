import asyncio
from types import SimpleNamespace

import book_migration
from content_checker import ContentChecker


MOODLE_FILE = (
    "https://mymoodle.okanagan.bc.ca/pluginfile.php/42/mod_page/content/1/chart.png"
)


class FakeFiles:
    stored = []

    def __init__(self, *_args):
        pass

    async def course_root(self):
        return "/content/enforced/10318-Course/"

    async def ensure_files_folder(self, folder):
        assert folder == "Page Assets"

    async def file_exists(self, _path):
        return False

    async def upload_file(self, folder, filename, blob, _content_type):
        self.stored.append((folder, filename, blob))
        return f"/content/enforced/10318-Course/{folder}/{filename}"


def checker_for_page_test():
    checker = object.__new__(ContentChecker)
    checker.stop_flag = [False]
    checker.log = lambda *_args: None
    return checker


def test_new_page_stores_image_in_manage_files_and_rewrites_its_link(monkeypatch):
    FakeFiles.stored = []
    monkeypatch.setattr(book_migration, "BrightspaceBookAPI", FakeFiles)

    async def fake_download(_context, _url):
        return b"PNG", "image/png", MOODLE_FILE

    monkeypatch.setattr(book_migration, "_moodle_asset", fake_download)
    checker = checker_for_page_test()
    created = []

    async def fake_create(_page, _course, _destination, _title, html):
        created.append(html)
        return True

    checker._create_verified_html_topic = fake_create
    item = {"type": "PAGE", "status": "missing", "name": "Overview",
            "href": "https://mymoodle.okanagan.bc.ca/mod/page/view.php?id=5",
            "section": "Unit", "page_html": f'<p>Course chart</p><img src="{MOODLE_FILE}">'}
    count = asyncio.run(checker._create_missing_page_topics(
        SimpleNamespace(), SimpleNamespace(url="https://learn.okanagancollege.ca"),
        "10318", [item], [{"kind": "MODULE", "id": 9, "title": "Unit"}]))

    assert count == 1
    assert len(FakeFiles.stored) == 1
    assert FakeFiles.stored[0][0] == "Page Assets"
    assert "mymoodle.okanagan.bc.ca" not in created[0]
    assert "/content/enforced/10318-Course/Page Assets/" in created[0]


def test_new_page_with_unresolved_moodle_link_is_not_published(monkeypatch):
    monkeypatch.setattr(book_migration, "BrightspaceBookAPI", FakeFiles)
    checker = checker_for_page_test()

    async def fake_create(*_args):
        raise AssertionError("Unresolved Moodle link must block creation")

    checker._create_verified_html_topic = fake_create
    item = {"type": "PAGE", "status": "missing", "name": "Overview",
            "href": "https://mymoodle.okanagan.bc.ca/mod/page/view.php?id=5",
            "section": "Unit", "page_html": (
                '<p>Read this introduction.</p><a href="https://mymoodle.okanagan.bc.ca/'
                'mod/quiz/view.php?id=7">Quiz</a>')}
    count = asyncio.run(checker._create_missing_page_topics(
        SimpleNamespace(), SimpleNamespace(url="https://learn.okanagancollege.ca"),
        "10318", [item], [{"kind": "MODULE", "id": 9, "title": "Unit"}]))
    assert count == 0
    assert item["status"] == "missing"
