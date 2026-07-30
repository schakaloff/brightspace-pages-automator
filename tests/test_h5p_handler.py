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
        if "SETTLE_PROBE" in js:
            if "page=2" in self.url:
                return {"rowCount": 14, "pagerCount": 1}
            return {"rowCount": 50, "pagerCount": 1}
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
            if "SETTLE_PROBE" in js:
                return {"rowCount": 1, "pagerCount": 0}
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


# Real titles from the THER-103 run. Questions 2&3 and 5 were silently skipped
# because the first 25 chars of all three Case Study titles are identical, so
# they were never uploaded. Question 4 survived only via a typo ("Cast").
GOODFEMUR_IN_CLOUD = [
    "Mrs Goodfemur Case Study - Question 1",
    "Mrs Goodfemur Cast Study - Question 4",
]


def test_goodfemur_q1_does_not_make_q23_present():
    assert _handler()._title_in_cloud(
        "Mrs Goodfemur Case Study - Question 23", GOODFEMUR_IN_CLOUD
    ) == ""


def test_goodfemur_q1_does_not_make_q5_present():
    assert _handler()._title_in_cloud(
        "Mrs Goodfemur Case Study - Question 5", GOODFEMUR_IN_CLOUD
    ) == ""


def test_goodfemur_cast_stays_distinct_from_case():
    handler = _handler()
    assert handler._title_in_cloud(
        "Mrs Goodfemur Cast Study - Question 4", GOODFEMUR_IN_CLOUD
    ) == "Mrs Goodfemur Cast Study - Question 4"
    assert handler._title_in_cloud(
        "Mrs Goodfemur Case Study - Question 4", GOODFEMUR_IN_CLOUD
    ) == ""


def test_present_items_still_match_exactly():
    assert _handler()._title_in_cloud(
        "Mrs Goodfemur Case Study - Question 1", GOODFEMUR_IN_CLOUD
    ) == "Mrs Goodfemur Case Study - Question 1"


def test_punctuation_and_spacing_normalised_on_both_sides():
    """Local names are filename-sanitised ('&' dropped); cloud titles may keep it."""
    handler = _handler()
    assert handler._title_in_cloud(
        "Drag  Drop Abbreviations", ["Drag & Drop Abbreviations"]
    ) == "Drag & Drop Abbreviations"
    assert handler._title_in_cloud(
        "Medical Terms Matching", ["Medical Terms: Matching"]
    ) == "Medical Terms: Matching"


def test_shared_prefix_is_not_a_match():
    assert _handler()._title_in_cloud(
        "Flashcards - Prefixes", ["Flashcards - Prefixes and Suffixes"]
    ) == ""


class GrowingListFrame:
    """Rows stream in after the frame is first usable, and the pager lands last.

    Mirrors the live failure: find_list_frame returns on the first row, and a
    scan right then saw 14 of 50 rows with no pager, so a 2-page library looked
    like a 1-page one.
    """

    def __init__(self, growth):
        self.url = BASE
        self._growth = list(growth)
        self._i = 0
        self.collected_at = None

    async def goto(self, url, **kwargs):
        self.url = url

    async def evaluate(self, js, *args):
        if "SETTLE_PROBE" in js:
            rows, pager = self._growth[min(self._i, len(self._growth) - 1)]
            self._i += 1
            return {"rowCount": rows, "pagerCount": pager}
        rows, _ = self._growth[min(self._i - 1, len(self._growth) - 1)]
        self.collected_at = rows
        return {"titles": [f"T{n}" for n in range(rows)], "pageHrefs": [], "rowCount": rows}


def test_scan_waits_for_rows_to_stop_growing():
    frame = GrowingListFrame([(14, 0), (30, 0), (50, 0), (50, 0), (50, 0), (50, 0)])
    tab = FakeTab()

    titles = asyncio.run(_handler()._collect_cloud_titles(tab, frame))

    # Must not have scanned the half-rendered 14-row page.
    assert frame.collected_at == 50
    assert len(titles) == 50


def test_scan_proceeds_as_soon_as_pager_appears():
    """A rendered pager means the server-rendered page is complete."""
    frame = GrowingListFrame([(50, 1)])
    tab = FakeTab()

    asyncio.run(_handler()._collect_cloud_titles(tab, frame))

    assert frame.collected_at == 50


def test_settle_gives_up_and_reports_row_count():
    frame = GrowingListFrame([(5, 0), (9, 0), (13, 0), (17, 0)])  # never stabilises
    rows = asyncio.run(
        _handler()._wait_for_list_settled(FakeTab(), frame, attempts=3)
    )

    assert rows > 0  # returns what it saw rather than hanging


class FakeTitleInput:
    def __init__(self, sticks=True):
        self.value = ""
        self.sticks = sticks

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def wait_for(self, **kwargs):
        return None

    async def click(self, **kwargs):
        return None

    async def fill(self, value, **kwargs):
        if self.sticks:
            self.value = value

    async def input_value(self):
        return self.value


class FakeTitleFrame:
    def __init__(self, url, inp=None, matching=()):
        self.url = url
        self._inp = inp
        self._matching = matching

    def locator(self, selector):
        if self._inp is not None and selector in self._matching:
            return self._inp
        return FakeLocator(0)


class FakeTitleTab:
    def __init__(self, frames):
        self.frames = frames
        self.waits = 0

    async def wait_for_timeout(self, ms):
        self.waits += 1


def test_title_is_set_in_non_h5p_frame():
    """The old code skipped frames whose URL lacked 'h5p.com' — the editor's
    inner frame is sometimes about:blank, so the title was never set."""
    inp = FakeTitleInput()
    tab = FakeTitleTab([
        FakeTitleFrame("https://learn.example/editor"),
        FakeTitleFrame("about:blank", inp, matching=('input.h5peditor-text[maxlength="255"]',)),
    ])

    ok = asyncio.run(_handler()._set_content_title(tab, "Nervous System Flashcards"))

    assert ok is True
    assert inp.value == "Nervous System Flashcards"


def test_title_failure_is_reported_not_swallowed():
    tab = FakeTitleTab([FakeTitleFrame("https://ocedtech.h5p.com/x")])

    ok = asyncio.run(_handler()._set_content_title(tab, "Anything", attempts=2))

    assert ok is False
    assert tab.waits == 2  # it polled rather than giving up on the first look


def test_title_that_does_not_stick_is_a_failure():
    inp = FakeTitleInput(sticks=False)
    tab = FakeTitleTab([
        FakeTitleFrame("https://ocedtech.h5p.com/x", inp, matching=('input.h5peditor-text[maxlength="255"]',))
    ])

    ok = asyncio.run(_handler()._set_content_title(tab, "Nervous System Flashcards", attempts=2))

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
            if "SETTLE_PROBE" in js:
                return {"rowCount": 1, "pagerCount": 1}
            self.evaluates += 1
            nxt = f"{BASE}?page={self.evaluates + 1}"
            return {"titles": [f"Item {self.evaluates}"], "pageHrefs": [nxt], "rowCount": 1}

    frame = EndlessPager()
    titles = asyncio.run(_handler()._collect_cloud_titles(FakeTab(), frame, max_pages=5))

    assert len(titles) == 5
    assert frame.evaluates == 5


# ── Original Moodle titles vs sanitised filenames ────────────────────────────
# enable_downloads strips '&', ':' etc. for the FILENAME; the cloud title,
# duplicate check, and Phase B must keep using the original Moodle title.

MOODLE_ITEMS = [
    {"type": "SECTION", "name": "Week 1: Foundations"},
    {"type": "EXTERNAL", "name": "Drag & Drop: Abbreviations", "hint": "hvp", "href": "x"},
    {"type": "SECTION", "name": "Week 2"},
    {"type": "EXTERNAL", "name": "Mrs Goodfemur Case Study - Question 23", "hint": "h5p", "href": "x"},
    {"type": "EXTERNAL", "name": "Not an H5P", "hint": "resource", "href": "x"},
]


def test_sanitize_name_matches_download_filenames():
    assert H5PHandler._sanitize_name("Drag & Drop: Abbreviations") == "Drag  Drop Abbreviations"
    assert H5PHandler._sanitize_name("Plain Name") == "Plain Name"


def test_moodle_maps_recover_original_title_from_file_stem():
    section_map, title_map = H5PHandler._moodle_h5p_maps(MOODLE_ITEMS)

    # A downloaded file is named by the sanitised stem; the map must hand back
    # the original punctuation-bearing Moodle title.
    assert title_map["Drag  Drop Abbreviations"] == "Drag & Drop: Abbreviations"
    assert section_map["Drag  Drop Abbreviations"] == "Week 1: Foundations"
    assert title_map["Mrs Goodfemur Case Study - Question 23"] == "Mrs Goodfemur Case Study - Question 23"
    assert section_map["Mrs Goodfemur Case Study - Question 23"] == "Week 2"


def test_moodle_maps_ignore_non_h5p_items():
    _, title_map = H5PHandler._moodle_h5p_maps(MOODLE_ITEMS)
    assert "Not an H5P" not in title_map


def test_leftover_file_stem_is_absent_from_maps():
    """A cached .h5p from another course maps to nothing — the caller treats
    that as 'do not upload'."""
    section_map, title_map = H5PHandler._moodle_h5p_maps(MOODLE_ITEMS)
    assert "MEDR Anatomy Leftover" not in section_map
    assert "MEDR Anatomy Leftover" not in title_map


# ── Phase B insert: exact full-title match across pager pages ────────────────

INSERT_PAGE1 = [
    "Mrs Goodfemur Case Study - Question 1",
    "Mrs Goodfemur Cast Study - Question 4",
    "Combining Form Practice",
]
INSERT_PAGE2 = [
    "Mrs Goodfemur Case Study - Question 23",
    "Organizational Structure Quiz",
]


class FakeInsertFrame:
    """Two server-rendered pages; records which row index gets clicked."""

    def __init__(self, page1=INSERT_PAGE1, page2=INSERT_PAGE2):
        self.url = BASE
        self._pages = {BASE: page1, PAGE2_URL: page2}
        self.clicked = []  # (url, row_index)

    async def goto(self, url, **kwargs):
        self.url = url

    def _titles(self):
        return self._pages.get(self.url, [])

    async def evaluate(self, js, *args):
        if "SETTLE_PROBE" in js:
            return {"rowCount": len(self._titles()), "pagerCount": 1}
        if "ROW_TITLES" in js:
            hrefs = [PAGE2_URL] if self.url == BASE else []
            return {"titles": list(self._titles()), "pageHrefs": hrefs}
        if "CLICK_ROW_INSERT" in js:
            i = args[0]
            if 0 <= i < len(self._titles()):
                self.clicked.append((self.url, i))
                return True
            return False
        raise AssertionError(f"unexpected evaluate: {js[:60]}")


def test_insert_clicks_exact_title_on_page_2():
    frame = FakeInsertFrame()
    matched = asyncio.run(_handler()._click_insert_for_title(
        FakeTab(), frame, "Mrs Goodfemur Case Study - Question 23"
    ))

    assert matched == "Mrs Goodfemur Case Study - Question 23"
    assert frame.clicked == [(PAGE2_URL, 0)]


def test_insert_does_not_prefix_match_question_1():
    """Asking for Q23 with only Q1 in the cloud must click NOTHING — the old
    25-char rule would have embedded Question 1 on the Question 23 page."""
    frame = FakeInsertFrame(page2=["Organizational Structure Quiz"])
    matched = asyncio.run(_handler()._click_insert_for_title(
        FakeTab(), frame, "Mrs Goodfemur Case Study - Question 23"
    ))

    assert matched == ""
    assert frame.clicked == []


def test_insert_matches_original_title_against_sanitised_cloud_title():
    """38 items were uploaded with sanitised titles; Phase B now passes the
    original Moodle title and must still find them."""
    frame = FakeInsertFrame(page1=["Drag  Drop Abbreviations"], page2=[])
    matched = asyncio.run(_handler()._click_insert_for_title(
        FakeTab(), frame, "Drag & Drop: Abbreviations"
    ))

    assert matched == "Drag  Drop Abbreviations"
    assert frame.clicked == [(BASE, 0)]


def test_insert_cast_vs_case_stays_distinct():
    frame = FakeInsertFrame()
    matched = asyncio.run(_handler()._click_insert_for_title(
        FakeTab(), frame, "Mrs Goodfemur Cast Study - Question 4"
    ))

    assert matched == "Mrs Goodfemur Cast Study - Question 4"
    assert frame.clicked == [(BASE, 1)]  # row index 1 on page 1, not row 0
