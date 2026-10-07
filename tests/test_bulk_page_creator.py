import asyncio
import json
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, "src")

from bulk_page_creator import (
    BrowserPageAPI, PageDraft, Section, CreatedPage, course_id_from_url,
    create_batch, render_content,
    create_course_pages,
    _JS_CREATE, _JS_SECTIONS,
)


SECTION = Section("20", "Unit 1 / Lessons")


class API:
    def __init__(self, sections=None, titles=(), fail_at=None):
        self.sections = [SECTION] if sections is None else sections
        self.titles = list(titles)
        self.fail_at = fail_at
        self.calls = []

    async def list_sections(self):
        return self.sections

    async def list_titles(self, section):
        assert section == "20"
        return self.titles

    async def create_page(self, section, draft, hidden):
        self.calls.append((section, draft, hidden))
        if len(self.calls) == self.fail_at:
            raise RuntimeError("response lost")
        return CreatedPage(draft, str(len(self.calls)), f"https://example.test/{len(self.calls)}")


def test_twenty_five_pages_keep_order_content_and_visibility():
    drafts = [PageDraft(f"Page {n}", f"Content {n}") for n in range(25)]
    api, progress = API(), []
    result = asyncio.run(create_batch(api, SECTION, drafts, hidden=False,
                                     progress=lambda index, item: progress.append((index, item))))
    assert [item.draft for item in result.created] == drafts
    assert [call[1] for call in api.calls] == drafts
    assert all(call[0] == SECTION and call[2] is False for call in api.calls)
    assert len(progress) == 25
    assert result.failed_index is None


@pytest.mark.parametrize("drafts", [[], [PageDraft("")], [PageDraft(" " )],
    [PageDraft("x" * 151)], [PageDraft("Same"), PageDraft(" same ")],
    [PageDraft("Page", format="unsupported")]])
def test_invalid_batch_never_writes(drafts):
    api = API()
    with pytest.raises(ValueError):
        asyncio.run(create_batch(api, SECTION, drafts))
    assert api.calls == []


def test_duplicate_in_destination_blocks_entire_batch():
    api = API(titles=[" existing "])
    with pytest.raises(ValueError, match="already contains"):
        asyncio.run(create_batch(api, SECTION, [PageDraft("New"), PageDraft("Existing")]))
    assert api.calls == []


def test_renamed_or_moved_section_blocks_write():
    api = API(sections=[Section("20", "Renamed section")])
    with pytest.raises(ValueError, match="section changed"):
        asyncio.run(create_batch(api, SECTION, [PageDraft("New")]))
    assert api.calls == []


def test_uncertain_failure_stops_without_retrying_or_sending_later_pages():
    api = API(fail_at=2)
    drafts = [PageDraft(f"Page {n}") for n in range(3)]
    result = asyncio.run(create_batch(api, SECTION, drafts))
    assert len(result.created) == 1
    assert result.failed_index == 1
    assert result.error == "response lost"
    assert len(api.calls) == 2


def test_stop_preserves_confirmed_page_and_leaves_rest_unsent():
    api, stop = API(), threading.Event()
    result = asyncio.run(create_batch(api, SECTION, [PageDraft("One"), PageDraft("Two")],
        stop=stop, progress=lambda *_: stop.set()))
    assert result.stopped
    assert len(result.created) == len(api.calls) == 1


def test_stop_before_first_write():
    api, stop = API(), threading.Event()
    stop.set()
    result = asyncio.run(create_batch(api, SECTION, [PageDraft("One")], stop=stop))
    assert result.stopped and api.calls == []


def test_stop_during_login_closes_browser_without_writing(monkeypatch):
    stop, closed = threading.Event(), []

    async def close_context():
        closed.append("context")

    async def close_browser():
        closed.append("browser")

    async def stop_playwright():
        closed.append("playwright")

    async def launch_browser(**kwargs):
        return (SimpleNamespace(stop=stop_playwright), SimpleNamespace(close=close_browser),
                SimpleNamespace(close=close_context), object())

    async def login(*args, **kwargs):
        stop.set()
        await asyncio.Future()

    monkeypatch.setitem(sys.modules, "browser", SimpleNamespace(
        launch_browser=launch_browser, wait_for_login=login))
    result = asyncio.run(create_course_pages(
        "https://learn.okanagancollege.ca/d2l/le/lessons/123", SECTION,
        [PageDraft("One")], {}, stop=stop))
    assert result.stopped and result.created == ()
    assert closed == ["context", "browser", "playwright"]


def test_text_is_escaped_and_html_preserved():
    text = render_content(PageDraft("A & B", "<script>unsafe</script>\nline 2\n\nParagraph 2"))
    assert "<title>A &amp; B</title>" in text
    assert "&lt;script&gt;unsafe&lt;/script&gt;<br>line 2" in text
    assert "<p>Paragraph 2</p>" in text
    assert '<meta charset="utf-8">' in text
    assert "<h2>Heading</h2>" in render_content(PageDraft("HTML", "<h2>Heading</h2>", "html"))
    document = '<!doctype html><html><body><p>Full page</p></body></html>'
    assert render_content(PageDraft("HTML", document, "html")) == document


@pytest.mark.parametrize("url", ["https://example.test/d2l/le/lessons/123",
    "http://learn.okanagancollege.ca/d2l/le/lessons/123", "https://learn.okanagancollege.ca/",
    "https://learn.okanagancollege.ca:444/d2l/le/lessons/123"])
def test_invalid_course_urls(url):
    with pytest.raises(ValueError):
        course_id_from_url(url)


def test_lesson_and_legacy_course_urls():
    assert course_id_from_url("https://learn.okanagancollege.ca/d2l/le/lessons/123/units/20") == "123"
    assert course_id_from_url("https://learn.okanagancollege.ca/d2l/le/content/123/home") == "123"
    assert course_id_from_url("https://learn.okanagancollege.ca/d2l/home?ou=123") == "123"


def test_adapter_passes_unique_filenames_and_authored_html():
    class Page:
        def __init__(self):
            self.calls = []

        async def evaluate(self, script, args):
            self.calls.append((script, args))
            return "42"

    page = Page()
    api = BrowserPageAPI(page, "123")
    draft = PageDraft("A title", "<p>Résumé</p>", "html")
    result = asyncio.run(api.create_page(SECTION, draft, True))
    asyncio.run(api.create_page(SECTION, draft, True))
    assert page.calls[0][1][:3] == ["123", "20", "A title"]
    assert "<p>Résumé</p>" in page.calls[0][1][3]
    assert page.calls[0][1][4] is True
    assert page.calls[0][1][5] != page.calls[1][1][5]
    assert result.url.endswith("/123/topics/42")


def run_browser_script(script, args, setup):
    """Execute the actual browser adapter JS with deterministic fetch responses."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for browser adapter JavaScript tests")
    source = (
        "const assert = require('node:assert/strict');\n"
        "globalThis.crypto = require('node:crypto').webcrypto;\n"
        "globalThis.setTimeout = fn => { fn(); return 0; };\n"
        "const response = (data, extra={}) => ({ok:true, redirected:false, status:200, "
        "headers:{get:()=> 'application/json'}, json:async()=>data, ...extra});\n"
        + setup + "\n"
        + f"const action = ({script});\n"
        + f"action({json.dumps(args)}).then(result => process.stdout.write(JSON.stringify({{result, calls}})))"
        + ".catch(error => process.stdout.write(JSON.stringify({error:error.message, calls})));"
    )
    completed = subprocess.run([node, "-e", source], text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_browser_upload_body_and_unique_filename_verification():
    output = run_browser_script(_JS_CREATE,
        ["123", "20", "A title", "<p>Résumé</p>", True, "unique.html", "1.0"], r"""
        const calls = [];
        globalThis.localStorage = {getItem:key => key === 'XSRF.Token' ? 'token' : null};
        globalThis.fetch = async (url, options) => {
            calls.push(url);
            if (options.method === 'POST') {
                assert.equal(url, '/d2l/api/le/1.0/123/content/modules/20/structure/?renameFileIfExists=true');
                assert.equal(options.headers['X-Csrf-Token'], 'token');
                const boundary = options.headers['Content-Type'].split('boundary=')[1];
                const parts = options.body.split('--' + boundary);
                assert.equal(parts.length, 4);
                const descriptor = JSON.parse(parts[1].split('\r\n\r\n')[1].trim());
                assert.equal(descriptor.Title, 'A title');
                assert.equal(descriptor.IsHidden, true);
                assert.equal(descriptor.Url, 'unique.html');
                assert.ok(parts[2].includes('filename="unique.html"'));
                assert.ok(parts[2].includes('<p>Résumé</p>'));
                // Empty success response, as observed in this installation.
                return response(null);
            }
            return response([
                {Type:1, Id:41, Title:'A title', Url:'/other.html'},
                {Type:1, Id:42, Title:'A title', Url:'/content/enforced/123/unique.html'}]);
        };
    """)
    assert output.get("result") == "42", output
    assert len(output["calls"]) == 2


@pytest.mark.parametrize("failure", ["token", "redirect", "http", "verify", "not-found"])
def test_browser_failure_never_retries_post(failure):
    setup = "const failure = " + json.dumps(failure) + r""";
        const calls = [];
        globalThis.localStorage = {getItem:() => failure === 'token' ? null : 'token'};
        globalThis.fetch = async (url, options) => {
            calls.push(options.method || 'GET');
            if (options.method === 'POST')
                return response(null, {ok:failure !== 'http', status:403, redirected:failure === 'redirect'});
            if (failure === 'verify') return response(null, {headers:{get:()=> 'text/html'}});
            return response([]);
        };
    """
    output = run_browser_script(_JS_CREATE,
        ["123", "20", "Title", "body", False, "unique.html", "1.0"], setup)
    assert output.get("error"), output
    assert output["calls"].count("POST") == (0 if failure == "token" else 1)


def test_browser_sections_include_empty_and_nested_sections():
    output = run_browser_script(_JS_SECTIONS, ["123", "1.0"], r"""
        const calls = [];
        globalThis.fetch = async url => {
            calls.push(url);
            if (url.endsWith('/root/')) return response([{Type:0, Id:20, Title:'Unit 1'}]);
            if (url.includes('/20/')) return response([{Type:0, Id:21, Title:'Empty section'},
                {Type:1, Id:42, Title:'A page'}]);
            return response([]);
        };
    """)
    assert output["result"] == [{"id": "20", "path": "Unit 1"},
                                 {"id": "21", "path": "Unit 1 / Empty section"}]
