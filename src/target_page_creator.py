"""
Auto-create a blank Brightspace target page for the Unit Collector.

FULLY SELF-CONTAINED and OPTIONAL. This module is the entire blast radius of the
"auto-create target page" feature. To rip the feature out completely:
  1. Delete this file.
  2. Delete the small `create_target_page(...)` block at the top of
     `UnitCollector.run()` in unit_collector.py.
  3. Delete the "Auto-create target page" checkbox + its wiring in
     src/panels/collector_panel.py.
Nothing else in the codebase imports or depends on this.

How it works (recipe verified live against a real course, 2026-07-13):
  - Brightspace's D2L LE API creates a content topic via
    POST /d2l/api/le/1.0/{courseId}/content/modules/{moduleId}/structure/
  - WRITE requests need the anti-forgery token `X-Csrf-Token`, read from the
    page's localStorage['XSRF.Token']. Without it the API returns a silent 200
    that is actually a login-redirect.
  - The body must be hand-built multipart/mixed (NOT FormData): part 1 is the
    JSON descriptor, part 2 is the initial HTML file. Both parts carry
    `Content-Disposition: form-data; name=""`.
  - A successful create returns an EMPTY body, so we re-list the module and match
    the new topic by its unique filename to recover its Id.
  - The viewable/editable URL uses the new Lessons format:
    /d2l/le/lessons/{courseId}/topics/{topicId}
"""

import re
from typing import Callable, Optional
from urllib.parse import urlparse

from playwright.async_api import Page


def _parse_ids(unit_url: str) -> tuple[Optional[str], Optional[str]]:
    """Extract (course_id, module_id) from a Brightspace unit URL.

    Unit URLs look like:
        https://host/d2l/le/lessons/{course_id}/units/{module_id}
    course_id also appears in older /content/{id}/ forms; module_id is always the
    last path segment.
    """
    course_id = None
    for pat in (r'/lessons/(\d+)', r'/content/(\d+)', r'[?&]ou=(\d+)'):
        m = re.search(pat, unit_url)
        if m:
            course_id = m.group(1)
            break

    path = urlparse(unit_url).path.rstrip('/')
    module_id = path.split('/')[-1] if path else None
    if module_id and not module_id.isdigit():
        module_id = None
    return course_id, module_id


_JS_CREATE = r"""async ([courseId, moduleId, title]) => {
    const xsrf = localStorage.getItem('XSRF.Token');
    if (!xsrf) return { ok: false, reason: 'no-xsrf-token' };

    const fname = `collected-${Date.now()}.html`;
    const descriptor = {
        Title: title, ShortTitle: '', Type: 1, TopicType: 1, Url: fname,
        StartDate: null, EndDate: null, DueDate: null,
        IsHidden: false, IsLocked: false
    };
    const stub = '<p></p>';
    const B = 'xxAUTOxxCREATExxBOUNDARYxx';
    const body =
        `--${B}\r\nContent-Disposition: form-data; name=""\r\n` +
        `Content-Type: application/json\r\n\r\n${JSON.stringify(descriptor)}\r\n` +
        `--${B}\r\nContent-Disposition: form-data; name=""; filename="${fname}"\r\n` +
        `Content-Type: text/html\r\n\r\n${stub}\r\n--${B}--\r\n`;

    let r;
    try {
        r = await fetch(
            `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
            { method: 'POST', credentials: 'include',
              headers: { 'X-Csrf-Token': xsrf,
                         'Content-Type': `multipart/mixed; boundary=${B}` },
              body });
    } catch (e) { return { ok: false, reason: 'fetch-failed: ' + e }; }
    if (!r.ok) return { ok: false, reason: 'http-' + r.status };

    // Successful create returns an empty body — re-list and match by filename.
    try {
        const s = await fetch(
            `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
            { credentials: 'include', headers: { 'Accept': 'application/json' } });
        const items = await s.json();
        const match = items.find(i => (i.Url || '').endsWith(fname));
        if (!match) return { ok: false, reason: 'created-but-not-found' };
        return { ok: true, id: match.Id };
    } catch (e) { return { ok: false, reason: 'relist-failed: ' + e }; }
}"""

_JS_MODULE_TITLE = r"""async ([courseId, moduleId]) => {
    try {
        const r = await fetch(
            `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}`,
            { credentials: 'include', headers: { 'Accept': 'application/json' } });
        if (!r.ok) return null;
        const m = await r.json();
        return (m && m.Title) ? m.Title : null;
    } catch (e) { return null; }
}"""


async def create_target_page(
    page: Page, unit_url: str, log: Optional[Callable] = None
) -> Optional[str]:
    """Create a blank HTML topic at the end of the unit and return its View URL.

    Runs entirely in the authenticated page context via fetch(). Never raises —
    returns None on any failure so the caller can fall back to a manual URL.
    """
    def _log(msg: str, level: str = "info"):
        if log:
            log(msg, level)

    course_id, module_id = _parse_ids(unit_url)
    if not course_id or not module_id:
        _log(f"✗ Auto-create: couldn't read course/unit id from URL: {unit_url}", "error")
        return None

    # Name the page after its unit, falling back to a generic label.
    try:
        unit_title = await page.evaluate(_JS_MODULE_TITLE, [course_id, module_id])
    except Exception:
        unit_title = None
    title = f"{unit_title} — Combined" if unit_title else "Combined Page"

    _log(f"Auto-creating target page “{title}” in unit {module_id}…", "info")
    try:
        result = await page.evaluate(_JS_CREATE, [course_id, module_id, title])
    except Exception as e:
        _log(f"✗ Auto-create failed: {e}", "error")
        return None

    if not result or not result.get("ok"):
        _log(f"✗ Auto-create failed ({(result or {}).get('reason', 'unknown')})", "error")
        return None

    base = f"{urlparse(unit_url).scheme}://{urlparse(unit_url).netloc}"
    view_url = f"{base}/d2l/le/lessons/{course_id}/topics/{result['id']}"
    _log(f"✓ Target page created: {view_url}", "success")
    return view_url
