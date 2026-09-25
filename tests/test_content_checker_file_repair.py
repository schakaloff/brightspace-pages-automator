import asyncio
from types import SimpleNamespace

import book_migration
import unit_overview
from content_checker import ContentChecker


class _Response:
    def __init__(self, url, *, headers=None, body=b""):
        self.url = url
        self.headers = headers or {}
        self._body = body

    async def body(self):
        return self._body


class _Request:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    async def get(self, url, **_kwargs):
        self.urls.append(url)
        return next(self.responses)


def test_moodle_file_index_resolves_direct_redirect_and_resource_page():
    request = _Request([
        _Response("https://moodle.example/mod/resource/view.php?id=1", headers={
            "location": "/pluginfile.php/10/mod_resource/content/1/Week%201.pdf"
        }),
        _Response(
            "https://moodle.example/mod/resource/view.php?id=2",
            headers={"content-type": "text/html; charset=utf-8"},
            body=(
                b'<a href="/pluginfile.php/10/mod_resource/content/1/Slides.pptx">'
                b'Download</a>'
            ),
        ),
    ])
    context = SimpleNamespace(request=request)
    checker = object.__new__(ContentChecker)
    results = [
        {"type": "FILE", "href": "https://moodle.example/mod/resource/view.php?id=1"},
        {"type": "URL", "href": "https://moodle.example/mod/resource/view.php?id=2"},
        {"type": "PAGE", "href": "https://moodle.example/mod/page/view.php?id=3"},
    ]

    indexed = asyncio.run(checker._index_moodle_files(context, results))

    assert set(indexed) == {"week 1.pdf", "slides.pptx"}
    assert indexed["week 1.pdf"] == [results[0]]
    assert indexed["slides.pptx"] == [results[1]]
    assert request.urls[0].endswith("id=1&redirect=1")
    assert request.urls[1].endswith("id=2&redirect=1")


def test_existing_page_media_link_is_rewritten_in_manage_files(monkeypatch):
    old = "https://learn.okanagancollege.ca/content/enforced/Course/Content/missing.mp4"
    new = "/content/enforced/Course/Recovered Files/video.mp4"
    store = {"html": f'<p>Keep this text.</p><video><source src="{old}"></video>'}

    class FakeContent:
        def __init__(self, *_args):
            pass

        async def get_topic(self, _topic_id):
            return {"Url": "/content/enforced/Course/Content/page.html"}

        async def get_topic_html(self, _topic_id):
            return store["html"]

    class FakeFiles:
        def __init__(self, *_args):
            pass

        async def course_root(self):
            return "/content/enforced/Course/"

        async def upload_file(self, folder, filename, blob, content_type, overwrite=False):
            assert (folder, filename, content_type, overwrite) == (
                "Content", "page.html", "text/html", True)
            store["html"] = blob.decode("utf-8")

    monkeypatch.setattr(unit_overview, "BrowserContentAPI", FakeContent)
    monkeypatch.setattr(book_migration, "BrightspaceBookAPI", FakeFiles)
    checker = object.__new__(ContentChecker)
    checker.bs_url = "https://learn.okanagancollege.ca/d2l/le/lessons/1"
    ok, why = asyncio.run(checker._patch_topic_link(
        SimpleNamespace(), "1", "44", old, new))

    assert ok, why
    assert old not in store["html"]
    assert new in store["html"]
    assert "Keep this text." in store["html"]
