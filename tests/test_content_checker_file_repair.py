import asyncio
from types import SimpleNamespace

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
