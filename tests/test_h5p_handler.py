import asyncio
import sys

sys.path.insert(0, "src")

from h5p_handler import H5PHandler


# Mirrors the live ocedtech.h5p.com content list captured 2026-07-30:
# page 1 renders 50 rows, page 2 renders 14, three titles appear on both,
# so 11 titles exist only on page 2 and are invisible to a single-page scan.
PAGE1_TITLES = [f"Course Item {i:02d}" for i in range(47)] + [
    "Combining Form Practice",
    "Digestive System Build-a-Word Practice",
    "Planes and Body Cavity Practice",
]
PAGE2_ONLY = [
    "Cardiovascular Heart Build-a-Word Practice",
    "Designations and Funding Quiz 1",
    "Female Reproductive System Build-a-Word Practice",
    "Integument Build a Word Practice 1",
    "Muscular System Build-a-Word Practice",
    "Nervous System Build-a-Word Practice",
    "Obtaining Designations Quiz",
    "Organizational Structure Quiz",
    "Respiratory System Build-a-Word Practice",
    "Skeletal System Build-a-Word Practice",
    "Urinary System Build-a-Word Practice",
]
PAGE2_TITLES = PAGE2_ONLY + [
    "Combining Form Practice",
    "Digestive System Build-a-Word Practice",
    "Planes and Body Cavity Practice",
]

BASE = "https://ocedtech.h5p.com/lti/1p3-123/content"
PAGE2_URL = f"{BASE}?page=2"


class FakeFrame:
    """Server-rendered H5P content list: page 2 is a real link, page 1 is a span."""

    def __init__(self):
        self.url = BASE
        self.goto_calls = []

    async def goto(self, url, **kwargs):
        self.goto_calls.append(url)
        self.url = url

    async def evaluate(self, js, *args):
        if "page=2" in self.url:
            return {"titles": list(PAGE2_TITLES), "pageHrefs": [], "rowCount": 14}
        return {"titles": list(PAGE1_TITLES), "pageHrefs": [PAGE2_URL], "rowCount": 50}


class FakeTab:
    async def wait_for_timeout(self, ms):
        return None


def _handler():
    return H5PHandler(
        log=lambda *a, **k: None,
        eval_in_any_frame=None,
        auto_dismiss=None,
        confirm=None,
        diagnose=None,
        verify_topic_in_module=None,
        summary={"h5p_inserted": [], "h5p_failed": []},
    )


def test_collect_cloud_titles_spans_pages():
    frame = FakeFrame()
    titles = asyncio.run(_handler()._collect_cloud_titles(FakeTab(), frame))

    # The whole point: a title that only exists on page 2 must be found.
    assert "Organizational Structure Quiz" in titles
    for title in PAGE2_ONLY:
        assert title in titles, f"page-2-only title missing: {title}"

    # 50 + 14 rows, 3 shared => 61 unique titles.
    assert len(titles) == 61
    assert len(set(titles)) == 61

    # It actually navigated to page 2, and returned the frame to the start URL
    # so later upload steps still act on a live content list.
    assert PAGE2_URL in frame.goto_calls
    assert frame.url == BASE


def test_collect_cloud_titles_single_page_does_not_navigate():
    class SinglePage(FakeFrame):
        async def evaluate(self, js, *args):
            return {"titles": ["Only Item"], "pageHrefs": [], "rowCount": 1}

    frame = SinglePage()
    titles = asyncio.run(_handler()._collect_cloud_titles(FakeTab(), frame))

    assert titles == ["Only Item"]
    assert frame.goto_calls == []


class FakeLocator:
    def __init__(self, count, clickable=True, box=None):
        self._count = count
        self._clickable = clickable
        self._box = box
        self.clicked = False

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def wait_for(self, **kwargs):
        if not self._count:
            raise RuntimeError("not found")

    async def click(self, **kwargs):
        if not self._clickable:
            raise RuntimeError("element is not clickable")
        self.clicked = True

    async def bounding_box(self):
        return self._box


class FakeClickFrame:
    def __init__(self, locator=None):
        self.url = "https://learn.example/frame"
        self._locator = locator

    def locator(self, selector):
        return self._locator if self._locator else FakeLocator(0)


class FakeMouse:
    def __init__(self):
        self.clicks = []

    async def click(self, x, y):
        self.clicks.append((x, y))


class FakeClickTab:
    def __init__(self, frames):
        self.frames = frames
        self.mouse = FakeMouse()

    async def wait_for_timeout(self, ms):
        return None


def test_native_click_used_when_element_is_clickable():
    loc = FakeLocator(count=1)
    tab = FakeClickTab([FakeClickFrame(), FakeClickFrame(loc)])

    ok = asyncio.run(
        _handler()._native_click_in_frames(tab, "d2l-button.create-new-btn", "Create New")
    )

    assert ok is True
    assert loc.clicked is True
    assert tab.mouse.clicks == []


def test_native_click_falls_back_to_mouse_coordinates():
    loc = FakeLocator(count=1, clickable=False, box={"x": 10, "y": 20, "width": 40, "height": 10})
    tab = FakeClickTab([FakeClickFrame(loc)])

    ok = asyncio.run(
        _handler()._native_click_in_frames(tab, "a.add-material-tile", "Page tile")
    )

    assert ok is True
    assert loc.clicked is False
    assert tab.mouse.clicks == [(30, 25)]


def test_native_click_reports_failure_so_caller_can_use_js_fallback():
    tab = FakeClickTab([FakeClickFrame(), FakeClickFrame()])

    ok = asyncio.run(
        _handler()._native_click_in_frames(tab, "d2l-button.create-new-btn", "Create New")
    )

    assert ok is False


def test_chooser_open_reports_tiles_not_clicks():
    """The Create New loop must key off rendered tiles, not a dispatched click.

    Observed live in opposite runs: a native click dispatched cleanly and the
    dropdown stayed shut; a JS click "failed" and the dropdown opened.
    """
    handler = _handler()
    seen = {}

    async def fake_eval(tab, js):
        seen["js"] = js
        return "add-material-tile" in js

    handler._eval_in_any_frame = fake_eval
    assert asyncio.run(handler._tile_chooser_open(object())) is True
    assert "loadActivity/file/" in seen["js"]


def test_collect_cloud_titles_stops_at_max_pages():
    class EndlessPager(FakeFrame):
        def __init__(self):
            super().__init__()
            self.evaluates = 0

        async def evaluate(self, js, *args):
            self.evaluates += 1
            nxt = f"{BASE}?page={self.evaluates + 1}"
            return {"titles": [f"Item {self.evaluates}"], "pageHrefs": [nxt], "rowCount": 1}

    frame = EndlessPager()
    titles = asyncio.run(_handler()._collect_cloud_titles(FakeTab(), frame, max_pages=5))

    assert len(titles) == 5
    assert frame.evaluates == 5
