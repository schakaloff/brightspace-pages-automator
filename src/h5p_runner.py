"""
Standalone H5P download + paste pipeline.

Reuses ContentChecker's Brightspace-TOC fetch, Moodle scrape, and H5PHandler
as-is, but skips the Moodle<->Brightspace diff (_compare_items, missing-file
upload, re-link) that ContentChecker.run() always does first. Use this when
all you want is: pull H5P activities out of Moodle and drop them into the
matching Brightspace modules.
"""
import asyncio
import re
import threading
from typing import Callable, Optional
from urllib.parse import urlparse

from content_checker import ContentChecker, _extract_course_id


async def _verify_topic_in_module_strict(bs_page, course_id, module_id, expected_name) -> bool:
    """Stricter replacement for ContentChecker._verify_topic_in_module.

    That method treats a match as "either normalized name is a substring of the
    other", which false-positives when the module already has an unrelated topic
    whose short title happens to be a substring of expected_name (e.g. a topic
    titled "Quiz" reads as already-matching "Inclusion Quiz") — causing a real
    insert to be silently skipped. Require an exact normalized match instead.
    """
    try:
        topics = await bs_page.evaluate(
            """async ([courseId, moduleId]) => {
                const resp = await fetch(
                    `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
                    { credentials: 'include' }
                );
                if (!resp.ok) return null;
                return await resp.json();
            }""",
            [str(course_id), str(module_id)],
        )
        if not topics:
            return False
        name_norm = re.sub(r'[^\w]', '', expected_name).lower()
        for topic in topics:
            title_norm = re.sub(r'[^\w]', '', topic.get('Title', '')).lower()
            if title_norm == name_norm:
                return True
        return False
    except Exception:
        return False


async def run_h5p_only(
    bs_url: str,
    moodle_url: str,
    log: Callable[[str, str], None],
    on_complete: Optional[Callable] = None,
    moodle_ready_event: Optional[threading.Event] = None,
    on_moodle_waiting: Optional[Callable] = None,
    h5p_ready_event: Optional[threading.Event] = None,
    on_h5p_waiting: Optional[Callable] = None,
    h5p_skip_flag: Optional[list] = None,
    confirm_fn: Optional[Callable] = None,
    bs_username: str = "",
    bs_password: str = "",
    sso_email: str = "",
    sso_password: str = "",
    moodle_username: str = "",
    moodle_password: str = "",
) -> None:
    from browser import launch_browser, wait_for_login

    checker = ContentChecker(
        bs_url=bs_url,
        moodle_url=moodle_url,
        log=log,
        moodle_ready_event=moodle_ready_event,
        on_moodle_waiting=on_moodle_waiting,
        h5p_ready_event=h5p_ready_event,
        on_h5p_waiting=on_h5p_waiting,
        confirm_fn=confirm_fn,
        bs_username=bs_username,
        bs_password=bs_password,
        sso_email=sso_email,
        sso_password=sso_password,
        moodle_username=moodle_username,
        moodle_password=moodle_password,
    )
    checker.h5p_skip_flag = h5p_skip_flag or [False]
    checker._summary = {"h5p_inserted": [], "h5p_failed": []}
    checker._h5p._summary = checker._summary
    # Override with the stricter exact-match check (see _verify_topic_in_module_strict).
    checker._h5p._verify_topic_in_module = _verify_topic_in_module_strict

    course_id = _extract_course_id(bs_url)
    if not course_id:
        log(f"✗ Could not extract course ID from: {bs_url}", "error")
        if on_complete:
            on_complete()
        return

    p, browser, context, page = await launch_browser()
    try:
        await wait_for_login(
            page, context,
            bs_username or None, bs_password or None,
            sso_email or None, sso_password or None,
        )

        log("─" * 52, "dim")
        bs_flat = await checker._fetch_bs_toc(page, course_id)
        if not bs_flat:
            if on_complete:
                on_complete()
            return

        log("─" * 52, "dim")
        moodle_items = await checker._scrape_moodle(context)
        if not moodle_items:
            if on_complete:
                on_complete()
            return

        parsed = urlparse(bs_url)
        bs_base = f"{parsed.scheme}://{parsed.netloc}"
        await checker._h5p.embed_in_brightspace(
            context, page, moodle_items, bs_flat, bs_base, course_id
        )

        log("─" * 52, "dim")
        inserted = checker._summary.get("h5p_inserted", [])
        failed = checker._summary.get("h5p_failed", [])
        log(f"✓ H5P done: {len(inserted)} inserted, {len(failed)} failed", "success")

        if on_complete:
            on_complete()
        while browser.is_connected():
            await asyncio.sleep(0.5)

    except Exception as e:
        log(f"✗ Unexpected error: {e}", "error")
        if on_complete:
            on_complete()
        raise
    finally:
        if browser.is_connected():
            await browser.close()
        await p.stop()
