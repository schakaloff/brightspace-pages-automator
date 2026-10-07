"""Create a reviewed batch of HTML Content topics in an existing module.

Uses the authenticated multipart upload recipe from target_page_creator.py.
Each POST gets a unique file name and is verified in the module outline. Writes
are sequential and never retried automatically: a lost response may still have
created a topic, so an unverified row must be checked in Brightspace first.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from uuid import uuid4


HOST = "learn.okanagancollege.ca"
API_VERSION = "1.0"  # Same endpoint as the existing, live-verified page creator.


@dataclass(frozen=True)
class Section:
    id: str
    path: str


@dataclass(frozen=True)
class PageDraft:
    title: str
    content: str = ""
    format: str = "text"


@dataclass(frozen=True)
class CreatedPage:
    draft: PageDraft
    id: str
    url: str


@dataclass(frozen=True)
class BatchResult:
    created: tuple[CreatedPage, ...]
    failed_index: int | None = None
    error: str = ""
    stopped: bool = False


def course_id_from_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port not in (None, 443):
        raise ValueError(f"Enter a Brightspace course or section URL on {HOST}.")
    match = re.search(r"/d2l/le/(?:lessons|content)/(\d+)(?:/|$)", parsed.path)
    course_id = match.group(1) if match else parse_qs(parsed.query).get("ou", [""])[0]
    if not re.fullmatch(r"[0-9]+", course_id):
        raise ValueError("The URL needs a Brightspace course ID.")
    return course_id


def validate_drafts(drafts: list[PageDraft]) -> None:
    if not drafts:
        raise ValueError("Add at least one page title.")
    for number, draft in enumerate(drafts, 1):
        if not draft.title.strip():
            raise ValueError(f"Page {number} needs a title.")
        if len(draft.title.strip()) > 150:
            raise ValueError(f"Page {number}'s title must be 150 characters or fewer.")
        if draft.format not in ("text", "html"):
            raise ValueError(f"Page {number} has an unsupported content format.")
    titles = [draft.title.strip().casefold() for draft in drafts]
    if len(set(titles)) != len(titles):
        raise ValueError("Use a different title for each page in the batch.")


def render_content(draft: PageDraft) -> str:
    """Plain text is escaped; explicit HTML is preserved as authored."""
    body = draft.content if draft.format == "html" else "\n".join(
        f"<p>{html.escape(paragraph).replace(chr(10), '<br>')}</p>"
        for paragraph in re.split(r"\n\s*\n", draft.content.replace("\r\n", "\n"))
    )
    if re.search(r"<!doctype\s|<html(?:\s|>)", body, re.IGNORECASE):
        return body
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(draft.title.strip())}</title></head><body>{body}</body></html>"
    )


_JS_SECTIONS = r"""async ([courseId, version]) => {
    async function read(path) {
        const r = await fetch(path, {credentials:'include', cache:'no-store',
                                    headers:{Accept:'application/json'}});
        if (!r.ok) throw new Error(`Load sections: HTTP ${r.status}`);
        if (r.redirected || !(r.headers.get('content-type') || '').includes('json'))
            throw new Error('Sign in to Brightspace again to load the sections.');
        const data = await r.json();
        if (!Array.isArray(data)) throw new Error('Invalid Brightspace section outline.');
        return data;
    }
    const result = [], visited = new Set();
    async function walk(entries, parents) {
        for (const entry of entries) {
            if (entry.Type !== 0) continue;
            const id = String(entry.Id);
            if (!/^\d+$/.test(id) || visited.has(id))
                throw new Error('Invalid or repeated Brightspace section ID.');
            visited.add(id);
            const names = [...parents, entry.Title || '(untitled section)'];
            result.push({id, path:names.join(' / ')});
            await walk(await read(`/d2l/api/le/${version}/${courseId}/content/modules/${id}/structure/`), names);
        }
    }
    await walk(await read(`/d2l/api/le/${version}/${courseId}/content/root/`), []);
    return result;
}"""


_JS_CREATE = r"""async ([courseId, moduleId, title, content, hidden, filename, version]) => {
    const token = localStorage.getItem('XSRF.Token');
    if (!token) throw new Error('No Brightspace security token. Sign in again.');
    const path = `/d2l/api/le/${version}/${courseId}/content/modules/${moduleId}/structure/`;
    const descriptor = {Title:title, ShortTitle:'', Type:1, TopicType:1, Url:filename,
        StartDate:null, EndDate:null, DueDate:null, IsHidden:hidden, IsLocked:false};
    const boundary = 'bulk-' + crypto.randomUUID();
    const body = `--${boundary}\r\nContent-Disposition: form-data; name=""\r\n` +
        `Content-Type: application/json\r\n\r\n${JSON.stringify(descriptor)}\r\n` +
        `--${boundary}\r\nContent-Disposition: form-data; name=""; filename="${filename}"\r\n` +
        `Content-Type: text/html; charset=utf-8\r\n\r\n${content}\r\n--${boundary}--\r\n`;
    const response = await fetch(path + '?renameFileIfExists=true', {
        method:'POST', credentials:'include',
        headers:{'X-Csrf-Token':token, 'Content-Type':`multipart/mixed; boundary=${boundary}`}, body});
    if (!response.ok) throw new Error(`Create page: HTTP ${response.status}`);
    if (response.redirected) throw new Error('Brightspace redirected the create request.');
    // Support both the documented JSON response and the empty response seen
    // on this installation. Verify by unique filename, never by title alone.
    for (let attempt = 0; attempt < 4; attempt++) {
        const check = await fetch(path, {credentials:'include', cache:'no-store',
                                        headers:{Accept:'application/json'}});
        if (!check.ok || check.redirected ||
            !(check.headers.get('content-type') || '').includes('json'))
            throw new Error('Could not verify the new page. Check the section in Brightspace.');
        const items = await check.json();
        if (!Array.isArray(items)) throw new Error('Invalid verification response.');
        const match = items.find(item => item.Type === 1 &&
            ((item.Url || '').split('?')[0].endsWith('/' + filename) || item.Url === filename));
        if (match && /^\d+$/.test(String(match.Id))) return String(match.Id);
        if (attempt < 3) await new Promise(resolve => setTimeout(resolve, 400));
    }
    throw new Error('The page may have been created but could not be verified. Check Brightspace before retrying.');
}"""


class BrowserPageAPI:
    def __init__(self, page, course_id: str):
        self.page, self.course_id = page, course_id

    async def list_sections(self) -> list[Section]:
        entries = await self.page.evaluate(_JS_SECTIONS, [self.course_id, API_VERSION])
        return [Section(entry["id"], entry["path"]) for entry in entries]

    async def list_titles(self, section_id: str) -> list[str]:
        return await self.page.evaluate(
            """async ([course, module, version]) => {
                const r = await fetch(`/d2l/api/le/${version}/${course}/content/modules/${module}/structure/`,
                    {credentials:'include', cache:'no-store', headers:{Accept:'application/json'}});
                if (!r.ok || r.redirected || !(r.headers.get('content-type') || '').includes('json'))
                    throw new Error('Could not check existing pages in the selected section.');
                const data = await r.json();
                if (!Array.isArray(data)) throw new Error('Invalid section content.');
                return data.map(item => String(item.Title || ''));
            }""", [self.course_id, section_id, API_VERSION],
        )

    async def create_page(self, section: Section, draft: PageDraft, hidden: bool) -> CreatedPage:
        filename = f"bulk-page-{uuid4().hex}.html"
        topic_id = await self.page.evaluate(_JS_CREATE, [
            self.course_id, section.id, draft.title.strip(), render_content(draft),
            hidden, filename, API_VERSION,
        ])
        if not isinstance(topic_id, str) or not re.fullmatch(r"[0-9]+", topic_id):
            raise RuntimeError("Brightspace did not return a verified page ID. Check the section.")
        return CreatedPage(draft, topic_id, f"https://{HOST}/d2l/le/lessons/{self.course_id}/topics/{topic_id}")


async def create_batch(api, section: Section, drafts: list[PageDraft], hidden=True,
                       stop=None, progress=None) -> BatchResult:
    validate_drafts(drafts)
    if section not in await api.list_sections():
        raise ValueError("The selected section changed. Reload sections before creating pages.")
    existing = {title.strip().casefold() for title in await api.list_titles(section.id)}
    conflicts = [draft.title for draft in drafts if draft.title.strip().casefold() in existing]
    if conflicts:
        raise ValueError("This section already contains these titles: " + ", ".join(conflicts) +
                         ". Rename or remove those rows before creating the batch.")
    created = []
    for index, draft in enumerate(drafts):
        if stop is not None and stop.is_set():
            return BatchResult(tuple(created), stopped=True)
        try:
            item = await api.create_page(section, draft, hidden)
        except Exception as exc:
            return BatchResult(tuple(created), index, str(exc))
        created.append(item)
        if progress:
            progress(index, item)
    return BatchResult(tuple(created))


async def _with_browser(url, credentials, action, log=None, stop=None):
    from browser import launch_browser, wait_for_login

    course_id = course_id_from_url(url)
    p, browser, context, page = await launch_browser(log_fn=log)
    try:
        login = asyncio.create_task(wait_for_login(
            page, context, credentials.get("bs_username") or None,
            credentials.get("bs_password") or None,
            credentials.get("sso_email") or None,
            credentials.get("sso_password") or None, log_fn=log))
        # Authentication has no writes. Allow Stop here too, rather than making
        # the user wait for the full manual-login timeout to close the app.
        while not login.done():
            if stop is not None and stop.is_set():
                login.cancel()
                await asyncio.gather(login, return_exceptions=True)
                return BatchResult((), stopped=True)
            await asyncio.wait([login], timeout=0.1)
        await login
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if urlparse(page.url).hostname != HOST:
            raise RuntimeError("Brightspace redirected away from the course. Sign in and retry.")
        return await action(BrowserPageAPI(page, course_id))
    finally:
        # Cleanup errors must not hide a completed or partial batch result.
        for close in (context.close, browser.close, p.stop):
            try:
                await close()
            except Exception as exc:
                if log:
                    log(f"Browser cleanup: {exc}", "warning")


async def load_sections(url, credentials, log=None):
    return await _with_browser(url, credentials, lambda api: api.list_sections(), log)


async def create_course_pages(url, section, drafts, credentials, hidden=True,
                              stop=None, progress=None, log=None):
    validate_drafts(drafts)
    if stop is not None and stop.is_set():
        return BatchResult((), stopped=True)
    return await _with_browser(url, credentials,
        lambda api: create_batch(api, section, drafts, hidden, stop, progress), log, stop)
