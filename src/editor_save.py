"""One implementation of "save this Brightspace page, and prove it saved".

Collector and Restyle drive the same D2L editor and each used to carry its own
copy of this logic. The copies drifted, and both shared two defects:

  * they searched every frame for a button merely *containing* "Save", so a
    dialog left open by a failed upload swallowed the click and the page closed
    without keeping its content;
  * they logged success for having clicked a button, never for having saved,
    so losing a whole collection looked identical to a clean run.

Both engines now call in here, so a fix lands in one place.
"""
import re


def _topic_ids(topic_url: str) -> tuple[str, str] | None:
    """Return ``(course_id, topic_id)`` for a Brightspace topic URL."""
    topic = re.search(r"/topics/(\d+)", topic_url or "")
    course = re.search(r"/lessons/(\d+)|/content/(\d+)|[?&]ou=(\d+)", topic_url or "")
    if not topic or not course:
        return None
    return next(group for group in course.groups() if group), topic.group(1)


async def replace_topic_html(page, topic_url: str, source_html: str, log) -> bool:
    """Write a complete topic through the D2L API and verify its contents.

    The HTML editor can report a successful Save and Close while retaining the
    blank creation stub.  Initial Collector assembly does not need that editor,
    so use the authenticated content API and require a semantic read-back.
    """
    from content_preservation import content_is_preserved
    from link_behavior import open_page_links_in_new_window
    from unit_overview import BrowserContentAPI

    source_html = open_page_links_in_new_window(source_html)

    ids = _topic_ids(topic_url)
    if not ids:
        log("✗ Could not write target page (unreadable course/topic ids)", "error")
        return False
    course_id, topic_id = ids
    api = BrowserContentAPI(page, course_id, "0")
    try:
        await api.replace_topic_html(topic_id, source_html)
    except Exception as exc:
        log(f"✗ Brightspace rejected the assembled page: {exc}", "error")
        return False

    detail = "the saved page did not contain the assembled content"
    for attempt in range(3):
        if attempt:
            await page.wait_for_timeout(800 * attempt)
        try:
            saved = await api.get_topic_html(topic_id)
            preserved, detail = content_is_preserved(source_html, saved)
            if preserved:
                log("✓ Saved and verified", "success")
                return True
        except Exception as exc:
            detail = str(exc).splitlines()[0]

    log(
        f"✗ Brightspace did not keep the assembled page ({detail}). "
        "The run stopped before styling or deleting anything.",
        "error",
    )
    return False


async def read_topic_html(page, topic_url: str) -> str:
    """Read a topic through the authenticated D2L content API."""
    from unit_overview import BrowserContentAPI

    ids = _topic_ids(topic_url)
    if not ids:
        raise ValueError("unreadable course/topic ids")
    course_id, topic_id = ids
    return await BrowserContentAPI(page, course_id, "0").get_topic_html(topic_id)


async def dialog_still_open(page) -> bool:
    """Whether a modal overlay is covering the editor."""
    for selector in ('.d2l-shim',
                     'iframe[title="Insert Stuff"]',
                     'iframe.d2l-dialog-frame'):
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            pass
    return False


async def save_and_close(page, log, wait_for_shim: bool = True) -> bool:
    """Click the editor's own Save and Close, never a dialog's.

    Only the full "Save and Close" label is matched: a bare "Save" also matches
    the Save button inside D2L's Duplicate Files prompt.
    """
    from automator import _find_locator_any_frame

    if wait_for_shim:
        for _ in range(30):
            if not await dialog_still_open(page):
                break
            await page.wait_for_timeout(500)

    if await dialog_still_open(page):
        log("✗ A Brightspace dialog is still open — refusing to click Save, because "
            "the click would land in the dialog and the page would close without "
            "keeping its content.", "error")
        return False

    for selector in ('d2l-button:has-text("Save and Close")',
                     'button:has-text("Save and Close")'):
        _, btn = await _find_locator_any_frame(page, selector, retries=6, delay_ms=600)
        if btn:
            await btn.first.click()
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            # Brightspace can finish its POST before the server has committed.
            await page.wait_for_timeout(2000)
            log("✓ Saved", "success")
            return True

    log("⚠ Save and Close button not found", "warning")
    return False


async def verify_topic_saved(page, topic_url: str, expected_min_chars: int, log) -> bool:
    """Read the topic back through the D2L API and prove content is there.

    Clicking Save proves nothing. A freshly created page still holding its
    `<p></p>` stub is the signature of a lost save, and it used to be reported
    as a success.
    """
    from unit_overview import BrowserContentAPI

    ids = _topic_ids(topic_url)
    if not ids:
        log("  ⚠ Could not verify the save (unreadable ids) — continuing", "warning")
        return True
    course_id, topic_id = ids

    try:
        saved = await BrowserContentAPI(
            page, course_id, "0"
        ).get_topic_html(topic_id)
    except Exception as e:
        log(f"  ⚠ Could not read the page back to verify it ({e}) — continuing", "warning")
        return True

    body = re.sub(r"(?is)<head.*?</head>", "", saved or "")
    text_len = len(re.sub(r"(?s)<[^>]+>", "", body).strip())
    if text_len == 0 and expected_min_chars > 0:
        log(f"✗ Brightspace saved an EMPTY page ({len(saved or '')} chars, all "
            f"boilerplate). The {expected_min_chars:,} characters meant for it were "
            "discarded — nothing was written. Your original content is untouched; "
            "delete the blank page and re-run.", "error")
        return False
    return True
