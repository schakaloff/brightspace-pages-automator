"""Transactional transfer of a Brightspace unit description to an Overview page."""

from __future__ import annotations

import html
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from content_preservation import (
    add_generated_heading,
    content_is_equivalent,
    content_is_preserved,
)
from target_page_creator import _parse_ids
from youtube_embed import transform_standalone_youtube_urls


_RESOURCE_ELEMENTS = {"a", "audio", "embed", "iframe", "img", "object", "video"}
_ATOMIC_CONTENT_ELEMENTS = {"canvas", "math", "svg"}
_PRESERVED_MODULE_FIELDS = (
    "Title", "ShortTitle", "ModuleStartDate", "ModuleEndDate", "ModuleDueDate",
    "IsHidden", "IsLocked", "ParentModuleId", "Duration", "Color",
)


def extract_description_html(module: dict) -> str:
    description = (module or {}).get("Description") or {}
    if description.get("Html") is not None:
        return str(description.get("Html") or "")
    text = str(description.get("Text") or "")
    return f"<p>{html.escape(text)}</p>" if text.strip() else ""


def has_meaningful_unit_content(source_html: str) -> bool:
    markup = source_html or ""
    if "<" not in markup and ">" not in markup:
        markup = f"<body>{html.escape(markup)}</body>"
    soup = BeautifulSoup(markup, "lxml")
    root = soup.body or soup
    if root.get_text(" ", strip=True):
        return True
    for tag in root.find_all(_RESOURCE_ELEMENTS | _ATOMIC_CONTENT_ELEMENTS):
        if tag.name in _ATOMIC_CONTENT_ELEMENTS:
            return True
        if (
            tag.get("href") or tag.get("src") or tag.get("srcset")
            or tag.get("data") or tag.get("data-src") or tag.get("srcdoc")
            or tag.find("source", src=True)
        ):
            return True
    return False


def overview_title(unit_title: str) -> str:
    return f"{unit_title} — Overview"


def _module_metadata_matches(expected: dict, actual: dict) -> tuple[bool, str]:
    for field in _PRESERVED_MODULE_FIELDS:
        if expected.get(field) != actual.get(field):
            return False, f"unit {field} changed"
    return True, ""


def _rich_text_input(value: dict | None) -> dict | None:
    if not value:
        return None
    if value.get("Html") is not None:
        return {"Content": str(value.get("Html") or ""), "Type": "Html"}
    return {"Content": str(value.get("Text") or ""), "Type": "Text"}


def _module_update_payload(module: dict, description_html: str) -> dict:
    payload = {
        "Title": module.get("Title", ""),
        "ShortTitle": module.get("ShortTitle") or "",
        "Type": 0,
        "ModuleStartDate": module.get("ModuleStartDate"),
        "ModuleEndDate": module.get("ModuleEndDate"),
        "ModuleDueDate": module.get("ModuleDueDate"),
        "IsHidden": bool(module.get("IsHidden", False)),
        "IsLocked": bool(module.get("IsLocked", False)),
        "Description": {"Content": description_html, "Type": "Html"},
    }
    if module.get("Duration") is not None:
        payload["Duration"] = module["Duration"]
    return payload


def _topic_update_payload(topic: dict, is_hidden: bool) -> dict:
    payload = {
        "Title": topic.get("Title", ""),
        "ShortTitle": topic.get("ShortTitle") or "",
        "Type": 1,
        "TopicType": topic.get("TopicType", 1),
        "Url": topic.get("Url") or "",
        "StartDate": topic.get("StartDate"),
        "EndDate": topic.get("EndDate"),
        "DueDate": topic.get("DueDate"),
        "IsHidden": bool(is_hidden),
        "IsLocked": bool(topic.get("IsLocked", False)),
        "OpenAsExternalResource": topic.get("OpenAsExternalResource"),
        "Description": _rich_text_input(topic.get("Description")),
        "MajorUpdate": None,
        "MajorUpdateText": None,
        "ResetCompletionTracking": None,
    }
    if topic.get("Duration") is not None:
        payload["Duration"] = topic["Duration"]
    return payload


class BrowserContentAPI:
    """Small authenticated D2L API adapter backed by a Playwright page."""

    def __init__(self, page, course_id: str, module_id: str):
        self.page = page
        self.course_id = str(course_id)
        self.module_id = str(module_id)

    async def get_module(self) -> dict:
        result = await self.page.evaluate(
            """async ([courseId, moduleId]) => {
                const r = await fetch(`/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}`,
                    { credentials: 'include', headers: { Accept: 'application/json' } });
                if (!r.ok) throw new Error(`GET module ${r.status}: ${(await r.text()).slice(0,200)}`);
                return await r.json();
            }""",
            [self.course_id, self.module_id],
        )
        return result

    async def list_structure(self) -> list:
        return await self.page.evaluate(
            """async ([courseId, moduleId]) => {
                const r = await fetch(`/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
                    { credentials: 'include', headers: { Accept: 'application/json' } });
                if (!r.ok) throw new Error(`GET structure ${r.status}: ${(await r.text()).slice(0,200)}`);
                return await r.json();
            }""",
            [self.course_id, self.module_id],
        )

    async def create_html_topic(self, title: str, initial_html: str | None = None) -> dict:
        token = uuid.uuid4().hex
        filename = f"bpa-overview-{self.module_id}-{token}.html"
        result = await self.page.evaluate(
            r"""async ([courseId, moduleId, title, filename, marker, initialHtml]) => {
                const xsrf = localStorage.getItem('XSRF.Token');
                if (!xsrf) throw new Error('create topic: no XSRF token');
                const descriptor = {
                    Title: title, ShortTitle: '', Type: 1, TopicType: 1, Url: filename,
                    StartDate: null, EndDate: null, DueDate: null,
                    IsHidden: true, IsLocked: false, OpenAsExternalResource: null,
                    Description: null
                };
                const stub = initialHtml ?? `<p data-bpa-overview-run="${marker}"></p>`;
                const boundary = `bpa_${marker}`;
                const body =
                    `--${boundary}\r\nContent-Disposition: form-data; name=""\r\n` +
                    `Content-Type: application/json\r\n\r\n${JSON.stringify(descriptor)}\r\n` +
                    `--${boundary}\r\nContent-Disposition: form-data; name=""; filename="${filename}"\r\n` +
                    `Content-Type: text/html\r\n\r\n${stub}\r\n--${boundary}--\r\n`;
                const r = await fetch(
                    `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
                    { method: 'POST', credentials: 'include',
                      headers: { 'X-Csrf-Token': xsrf,
                                 'Content-Type': `multipart/mixed; boundary=${boundary}` }, body });
                if (!r.ok) throw new Error(`create topic ${r.status}: ${(await r.text()).slice(0,200)}`);
                const responseText = await r.text();
                if (responseText) {
                    try { const item = JSON.parse(responseText); if (item && item.Id) return item; } catch (_) {}
                }
                const list = await fetch(
                    `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
                    { credentials: 'include', headers: { Accept: 'application/json' } });
                if (!list.ok) throw new Error(`created topic but relist failed ${list.status}`);
                const items = await list.json();
                const matches = (items || []).filter(i => String(i.Url || '').endsWith(filename));
                if (matches.length !== 1 || !matches[0].Id)
                    throw new Error(`created topic could not be identified uniquely (${matches.length} matches)`);
                return matches[0];
            }""",
            [self.course_id, self.module_id, title, filename, token, initial_html],
        )
        return result

    async def get_topic(self, topic_id: int | str) -> dict:
        return await self.page.evaluate(
            """async ([courseId, topicId]) => {
                const r = await fetch(`/d2l/api/le/1.0/${courseId}/content/topics/${topicId}`,
                    { credentials: 'include', headers: { Accept: 'application/json' } });
                if (!r.ok) throw new Error(`GET topic ${r.status}: ${(await r.text()).slice(0,200)}`);
                return await r.json();
            }""",
            [self.course_id, str(topic_id)],
        )

    async def get_topic_html(self, topic_id: int | str) -> str:
        return await self.page.evaluate(
            """async ([courseId, topicId]) => {
                const r = await fetch(`/d2l/api/le/1.75/${courseId}/content/topics/${topicId}/file`,
                    { credentials: 'include' });
                if (!r.ok) throw new Error(`GET topic file ${r.status}: ${(await r.text()).slice(0,200)}`);
                const type = (r.headers.get('content-type') || '').toLowerCase();
                if (!type.includes('text/html')) throw new Error(`topic file is not HTML (${type || 'unknown'})`);
                return await r.text();
            }""",
            [self.course_id, str(topic_id)],
        )

    async def replace_topic_html(self, topic_id: int | str, source_html: str) -> None:
        await self.page.evaluate(
            """async ([courseId, topicId, sourceHtml]) => {
                const xsrf = localStorage.getItem('XSRF.Token');
                if (!xsrf) throw new Error('replace topic file: no XSRF token');
                const blob = new Blob([sourceHtml], { type: 'text/html' });
                const form = new FormData();
                form.append('file', blob, 'index.html');
                const headers = { 'X-Csrf-Token': xsrf };
                const r = await fetch(`/d2l/api/le/1.75/${courseId}/content/topics/${topicId}/file`,
                    { method: 'PUT', credentials: 'include', headers, body: form });
                if (!r.ok) throw new Error(`PUT topic file ${r.status}: ${(await r.text()).slice(0,200)}`);
            }""",
            [self.course_id, str(topic_id), source_html],
        )

    async def set_topic_hidden(self, topic_id: int | str, is_hidden: bool) -> None:
        topic = await self.get_topic(topic_id)
        payload = _topic_update_payload(topic, is_hidden)
        await self.page.evaluate(
            """async ([courseId, topicId, payload]) => {
                const xsrf = localStorage.getItem('XSRF.Token');
                if (!xsrf) throw new Error('update topic: no XSRF token');
                const headers = { 'Content-Type': 'application/json' };
                headers['X-Csrf-Token'] = xsrf;
                const r = await fetch(`/d2l/api/le/1.0/${courseId}/content/topics/${topicId}`,
                    { method: 'PUT', credentials: 'include', headers, body: JSON.stringify(payload) });
                if (!r.ok) throw new Error(`PUT topic ${r.status}: ${(await r.text()).slice(0,200)}`);
            }""",
            [self.course_id, str(topic_id), payload],
        )

    async def move_topic_first(self, topic_id: int | str) -> None:
        await self.page.evaluate(
            """async ([courseId, topicId]) => {
                const xsrf = localStorage.getItem('XSRF.Token');
                if (!xsrf) throw new Error('move topic: no XSRF token');
                const headers = { 'X-Csrf-Token': xsrf };
                const r = await fetch(
                    `/d2l/api/le/1.82/${courseId}/content/order/objectId/${topicId}?position=first`,
                    { method: 'POST', credentials: 'include', headers });
                if (!r.ok) throw new Error(`move topic first ${r.status}: ${(await r.text()).slice(0,200)}`);
            }""",
            [self.course_id, str(topic_id)],
        )

    async def replace_module_description(self, original_module: dict, source_html: str) -> None:
        payload = _module_update_payload(original_module, source_html)
        await self.page.evaluate(
            """async ([courseId, moduleId, payload]) => {
                const xsrf = localStorage.getItem('XSRF.Token');
                if (!xsrf) throw new Error('update module: no XSRF token');
                const headers = { 'Content-Type': 'application/json' };
                headers['X-Csrf-Token'] = xsrf;
                const r = await fetch(`/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}`,
                    { method: 'PUT', credentials: 'include', headers, body: JSON.stringify(payload) });
                if (!r.ok) throw new Error(`PUT module ${r.status}: ${(await r.text()).slice(0,200)}`);
            }""",
            [self.course_id, self.module_id, payload],
        )

    async def delete_topic(self, topic_id: int | str) -> None:
        await self.page.evaluate(
            """async ([courseId, topicId]) => {
                const xsrf = localStorage.getItem('XSRF.Token');
                if (!xsrf) throw new Error('delete topic: no XSRF token');
                const headers = { 'X-Csrf-Token': xsrf };
                const r = await fetch(`/d2l/api/le/1.0/${courseId}/content/topics/${topicId}`,
                    { method: 'DELETE', credentials: 'include', headers });
                if (!r.ok && r.status !== 404)
                    throw new Error(`DELETE topic ${r.status}: ${(await r.text()).slice(0,200)}`);
            }""",
            [self.course_id, str(topic_id)],
        )


@dataclass(frozen=True)
class TransferResult:
    ok: bool
    status: str
    reason: str = ""
    topic_id: Optional[int | str] = None
    topic_url: str = ""
    created: bool = False
    usage: Optional[dict] = None


async def _rollback_created(api, topic_id, log) -> str:
    try:
        await api.delete_topic(topic_id)
        log(f"  ↩ Removed incomplete Overview page {topic_id}", "warning")
        return "incomplete child page removed"
    except Exception as delete_error:
        try:
            await api.set_topic_hidden(topic_id, True)
        except Exception:
            pass
        detail = f"could not remove incomplete page {topic_id}; it was left hidden: {delete_error}"
        log(f"  ⚠ {detail}", "error")
        return detail


async def _verify_page(api, topic_id, title: str, hidden: bool) -> tuple[bool, str]:
    topic = await api.get_topic(topic_id)
    if str(topic.get("Id")) != str(topic_id):
        return False, "read-back returned a different topic ID"
    if topic.get("Title") != title:
        return False, f"page title mismatch ({topic.get('Title')!r})"
    if str(topic.get("ParentModuleId")) != str(api.module_id):
        return False, "page is not a child of the source unit"
    if bool(topic.get("IsHidden", False)) != bool(hidden):
        return False, "page visibility does not match its parent unit"
    structure = await api.list_structure()
    if not structure or str(structure[0].get("Id")) != str(topic_id):
        return False, "page is not the first child in the unit"
    return True, ""


async def move_unit_content_to_overview(
    api,
    restyle_html: Callable[[str], Awaitable[tuple[Optional[str], Optional[dict]]]],
    log: Callable[[str, str], None],
    base_url: str = "",
) -> TransferResult:
    """Move a unit description only after a fully verified child page exists."""
    created_id = None
    try:
        original_module = await api.get_module()
    except Exception as exc:
        return TransferResult(False, "failed", f"could not read unit before page creation: {exc}")
    original_title = str(original_module.get("Title") or "")
    original_html = extract_description_html(original_module)
    if not original_title:
        return TransferResult(False, "failed", "unit has no readable title")
    if not has_meaningful_unit_content(original_html):
        return TransferResult(True, "no-content")

    title = overview_title(original_title)
    transformed = transform_standalone_youtube_urls(original_html)
    source_for_style = add_generated_heading(title, transformed.html)
    try:
        structure = await api.list_structure()
    except Exception as exc:
        return TransferResult(False, "failed", f"could not read unit children before page creation: {exc}")
    matching = [
        item for item in structure
        if item.get("Title") == title
        and int(item.get("Type", 1)) == 1
        and str(item.get("ParentModuleId", api.module_id)) == str(api.module_id)
    ]
    if len(matching) > 1:
        return TransferResult(False, "conflict", f"found {len(matching)} child pages named {title!r}")

    usage = None
    created = False
    try:
        if matching:
            topic_id = matching[0].get("Id")
            if topic_id is None:
                return TransferResult(False, "conflict", "existing Overview page has no readable ID")
            existing_html = await api.get_topic_html(topic_id)
            equivalent, reason = content_is_preserved(source_for_style, existing_html)
            if not equivalent:
                return TransferResult(
                    False, "conflict",
                    f"existing Overview page cannot be verified against the unit description: {reason}",
                    topic_id=topic_id,
                )
            log(f"↻ Verified existing Overview page {topic_id}; resuming transfer", "info")
        else:
            created_topic = await api.create_html_topic(title)
            topic_id = created_topic.get("Id")
            if topic_id is None:
                raise RuntimeError("create returned no exact new topic ID")
            created_id = topic_id
            created = True
            log(f"✓ Created hidden Overview page {topic_id}", "success")

            styled_html, usage = await restyle_html(source_for_style)
            if not styled_html:
                raise RuntimeError("Restyle returned no verified HTML")
            # Claude may add headings or rearrange layout; it must not lose
            # any text or link, because the unit description is cleared below.
            equivalent, reason = content_is_preserved(source_for_style, styled_html)
            if not equivalent:
                raise RuntimeError(f"styled content verification failed: {reason}")
            await api.replace_topic_html(topic_id, styled_html)
            readback = await api.get_topic_html(topic_id)
            equivalent, reason = content_is_equivalent(styled_html, readback)
            if not equivalent:
                raise RuntimeError(f"Overview read-back validation failed: {reason}")

        # Keep the page hidden while placement and metadata are verified.
        await api.set_topic_hidden(topic_id, True)
        await api.move_topic_first(topic_id)
        verified, reason = await _verify_page(api, topic_id, title, True)
        if not verified:
            raise RuntimeError(reason)

        final_hidden = bool(original_module.get("IsHidden", False))
        await api.set_topic_hidden(topic_id, final_hidden)
        verified, reason = await _verify_page(api, topic_id, title, final_hidden)
        if not verified:
            raise RuntimeError(reason)

        # This is the commit point: the source is cleared only after every child
        # page check above has passed.
        try:
            await api.replace_module_description(original_module, "")
            module_readback = await api.get_module()
            metadata_ok, metadata_reason = _module_metadata_matches(
                original_module, module_readback
            )
            if not metadata_ok:
                raise RuntimeError(metadata_reason)
            if has_meaningful_unit_content(extract_description_html(module_readback)):
                raise RuntimeError("unit description was not empty after clear")
        except Exception as clear_error:
            # A failed HTTP response can be ambiguous. Put the exact original HTML
            # back and prove it before reporting failure.
            restore_error = None
            try:
                await api.replace_module_description(original_module, original_html)
                restored_module = await api.get_module()
                equivalent, restore_reason = content_is_equivalent(
                    original_html, extract_description_html(restored_module)
                )
                metadata_ok, metadata_reason = _module_metadata_matches(
                    original_module, restored_module
                )
                if not equivalent or not metadata_ok:
                    restore_error = restore_reason or metadata_reason
            except Exception as exc:
                restore_error = str(exc)
            detail = f"failed to clear and verify unit description: {clear_error}"
            if restore_error:
                # The clear request may have succeeded even when its response was
                # lost.  If the restoration cannot be proven, the Overview is the
                # only confirmed copy of the original content.  Never delete it.
                hidden_detail = ""
                try:
                    await api.set_topic_hidden(topic_id, True)
                    hidden_ok, hidden_reason = await _verify_page(api, topic_id, title, True)
                    if not hidden_ok:
                        hidden_detail = f"; could not verify Overview was hidden: {hidden_reason}"
                except Exception as hide_error:
                    hidden_detail = f"; could not hide Overview for review: {hide_error}"
                detail += (
                    "; CRITICAL: original description restore could not be verified: "
                    f"{restore_error}. Preserved Overview for manual recovery "
                    f"(course {api.course_id}, unit {api.module_id}, topic {topic_id})"
                    f"{hidden_detail}"
                )
                return TransferResult(False, "failed", detail, topic_id=topic_id, created=created)
            if created_id is not None:
                detail += "; " + await _rollback_created(api, created_id, log)
            return TransferResult(False, "failed", detail, topic_id=topic_id, created=created)

        topic_url = (
            f"{base_url.rstrip('/')}/d2l/le/lessons/{api.course_id}/topics/{topic_id}"
            if base_url else ""
        )
        return TransferResult(
            True, "moved" if created else "existing-resumed", topic_id=topic_id,
            topic_url=topic_url, created=created, usage=usage,
        )
    except Exception as exc:
        detail = str(exc)
        if created_id is not None:
            detail += "; " + await _rollback_created(api, created_id, log)
        return TransferResult(False, "failed", detail, topic_id=created_id, created=created)


async def move_unit_url_to_overview(page, unit_url: str, restyle_html, log) -> TransferResult:
    course_id, module_id = _parse_ids(unit_url)
    if not course_id or not module_id:
        return TransferResult(False, "failed", "URL does not identify one Brightspace unit")
    parsed = urlparse(unit_url)
    api = BrowserContentAPI(page, course_id, module_id)
    return await move_unit_content_to_overview(
        api, restyle_html, log, base_url=f"{parsed.scheme}://{parsed.netloc}"
    )
