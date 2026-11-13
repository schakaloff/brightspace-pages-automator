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
from contextlib import ExitStack
from typing import Callable, Optional
from urllib.parse import urlparse

from content_checker import ContentChecker, _extract_course_id
from h5p_detect import html_contains_h5p_embed, is_h5p_topic
from h5p_handler import SUMMARY_KEYS
from h5p_progress import H5PRun, clean_url


async def _verify_topic_in_module_strict(bs_page, course_id, module_id, expected_name, *, exact_title=False) -> bool:
    """Stricter replacement for ContentChecker._verify_topic_in_module.

    H5P has its own duplicate rule: exact normalized title *and* an H5P
    topic.  A same-named PDF must not make Phase B skip the H5P insertion.
    """
    try:
        topics = await bs_page.evaluate(
            """async ([courseId, moduleId]) => {
                const resp = await fetch(
                    `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/?_=${Date.now()}`,
                    { credentials: 'include', cache: 'no-store' }
                );
                if (!resp.ok) throw Error(`Cannot verify H5P pages: HTTP ${resp.status}`);
                return await resp.json();
            }""",
            [str(course_id), str(module_id)],
        )
        if not isinstance(topics, list):
            raise RuntimeError('The destination topic list could not be read.')
        name_norm = re.sub(r'[^\w]', '', expected_name).lower()
        for topic in topics:
            title_norm = re.sub(r'[^\w]', '', topic.get('Title', '')).lower()
            matches = topic.get('Title', '') == expected_name if exact_title else title_norm == name_norm
            if matches and is_h5p_topic(topic):
                return True
            if matches and str(topic.get('Url', '')).split('?')[0].lower().endswith(('.html', '.htm')):
                from unit_overview import BrowserContentAPI
                html = await BrowserContentAPI(bs_page, str(course_id), str(module_id)).get_topic_html(topic['Id'])
                if html_contains_h5p_embed(html): return True
        return False
    except Exception as exc:
        raise RuntimeError(f"Cannot check whether {expected_name} is already saved. No new page will be created: {exc}") from exc


async def run_h5p_manual(
    bs_url: str,
    unit_id: str,
    files: list,
    grade: bool,
    log: Callable[[str, str], None],
    on_complete: Optional[Callable] = None,
    h5p_grade_recovery: Optional[Callable] = None,
    bs_username: str = "",
    bs_password: str = "",
    sso_email: str = "",
    sso_password: str = "",
    stop_flag: Optional[list] = None,
    on_state: Optional[Callable] = None,
    watch_browser: bool = True,
    **_ignored,  # the panel passes Moodle credentials too; a manual run has no Moodle
) -> None:
    """Upload local .h5p files to the H5P library and place each on a new page in one unit.

    `files` is [{'path': Path, 'title': str}]. Reuses the Moodle pipeline's upload,
    insert, save and verify steps; only the list of work comes from the user.
    """
    from browser import launch_browser, wait_for_login
    from h5p_browser_guide import H5PBrowserGuide

    course_id = _extract_course_id(bs_url)
    if not course_id:
        log(f"✗ Could not extract course ID from: {bs_url}", "error")
        return
    guide = H5PBrowserGuide(on_state, enabled=watch_browser)
    checker = ContentChecker(
        bs_url=bs_url, moodle_url="", log=log,
        h5p_grade_all=grade, h5p_grade_recovery=h5p_grade_recovery,
        h5p_verify=_verify_topic_in_module_strict,
        bs_username=bs_username, bs_password=bs_password,
        sso_email=sso_email, sso_password=sso_password,
    )
    if stop_flag is not None:
        checker.stop_flag = stop_flag
    summary = checker._summary = {k: [] for k in SUMMARY_KEYS}
    handler = checker._h5p
    handler._summary = summary
    handler.browser_guide = guide
    assignments = [{
        "file": f["path"], "name": f["title"], "should_grade": grade, "belongs_to_course": True,
        "moodle_section": "", "bs_module_title": "the selected unit", "bs_module_id": str(unit_id),
    } for f in files]

    p = browser = None
    try:
        p, browser, context, page = await launch_browser()
        await guide.install(context)
        await guide.status('Connecting to Brightspace · sign in if asked', kind='waiting')
        await wait_for_login(page, context, bs_username or None, bs_password or None,
                             sso_email or None, sso_password or None)
        parsed = urlparse(bs_url)
        bs_base = f"{parsed.scheme}://{parsed.netloc}"
        # Open the unit first so the API checks run on the Brightspace domain.
        await page.goto(f"{bs_base}/d2l/le/lessons/{course_id}/units/{unit_id}",
                        wait_until="domcontentloaded", timeout=30000)
        await handler.embed_in_brightspace(context, page, [], [], bs_base, course_id, assignments=assignments)
        bad = summary["h5p_failed"] or summary["h5p_grade_failed"] or summary["h5p_skipped"]
        log(f"{'⚠ Needs attention' if bad else '✓ Done'}: {len(summary['h5p_inserted'])} added, "
            f"{len(summary['h5p_failed'])} failed, {len(summary['h5p_graded'])} graded with a verified "
            f"gradebook link, {len(summary['h5p_grade_failed'])} to check in Manage Grades.",
            "warning" if bad else "success")
        await guide.status('Pages added and verified' if not bad else 'Some files need attention',
                           kind='attention' if bad else 'success')
        if on_complete and not bad:
            on_complete()
    finally:
        try:
            if browser and browser.is_connected(): await browser.close()
        finally:
            if p: await p.stop()


async def run_h5p_only(
    bs_url: str,
    moodle_url: str,
    log: Callable[[str, str], None],
    on_complete: Optional[Callable] = None,
    moodle_ready_event: Optional[threading.Event] = None,
    on_moodle_waiting: Optional[Callable] = None,
    h5p_ready_event: Optional[threading.Event] = None,
    on_h5p_waiting: Optional[Callable] = None,
    h5p_recovery: Optional[Callable] = None,
    h5p_skip_flag: Optional[list] = None,
    h5p_grade_all: bool = False,
    h5p_grade_recovery: Optional[Callable] = None,
    confirm_fn: Optional[Callable] = None,
    bs_username: str = "",
    bs_password: str = "",
    sso_email: str = "",
    sso_password: str = "",
    moodle_username: str = "",
    moodle_password: str = "",
    stop_flag: Optional[list] = None,
    resume_id: Optional[str] = None,
    progress_directory=None,
    review_fn: Optional[Callable] = None,
    on_state: Optional[Callable] = None,
    watch_browser: bool = True,
) -> None:
    from browser import launch_browser, wait_for_login
    from h5p_browser_guide import H5PBrowserGuide

    guide = H5PBrowserGuide(on_state, enabled=watch_browser)

    checker = ContentChecker(
        bs_url=bs_url,
        moodle_url=moodle_url,
        log=log,
        moodle_ready_event=moodle_ready_event,
        on_moodle_waiting=on_moodle_waiting,
        h5p_ready_event=h5p_ready_event,
        on_h5p_waiting=on_h5p_waiting,
        h5p_recovery=h5p_recovery,
        h5p_grade_all=h5p_grade_all,
        h5p_grade_recovery=h5p_grade_recovery,
        h5p_verify=_verify_topic_in_module_strict,
        confirm_fn=confirm_fn,
        bs_username=bs_username,
        bs_password=bs_password,
        sso_email=sso_email,
        sso_password=sso_password,
        moodle_username=moodle_username,
        moodle_password=moodle_password,
    )
    checker.h5p_skip_flag = h5p_skip_flag or [False]
    if stop_flag is not None:
        checker.stop_flag = stop_flag

    def check_stopped():
        if checker.stop_flag[0]:
            raise asyncio.CancelledError()

    checker.do_h5p_embed = True
    checker._summary = {k: [] for k in SUMMARY_KEYS}
    checker._h5p._summary = checker._summary
    checker._h5p.browser_guide = guide

    course_id = _extract_course_id(bs_url)
    if not course_id:
        log(f"✗ Could not extract course ID from: {bs_url}", "error")
        return

    progress = H5PRun.load(resume_id, progress_directory) if resume_id else H5PRun.create(
        bs_url, moodle_url, course_id, h5p_grade_all, progress_directory)
    if clean_url(bs_url) != progress.data['bs_url'] or clean_url(moodle_url) != progress.data['moodle_url'] or str(course_id) != progress.data['course_id']:
        raise ValueError('The saved H5P run belongs to a different course pair.')
    checker._h5p.progress = progress
    checker._h5p.grade_all = progress.data['grade_all']
    if on_state:
        progress.on_change = lambda data: on_state(dict(stage='progress', run=data))

    check_stopped()
    from transfer_runner import PlanLock
    resources = ExitStack()
    resources.enter_context(PlanLock(progress.path))
    p = browser = None
    try:
        progress.data['status'] = 'running'
        progress.save()
        if on_state:
            on_state(dict(stage='connect', message='Connecting to Brightspace and Moodle'))
        p, browser, context, page = await launch_browser()
        await guide.install(context)
        check_stopped()
        await guide.status('Connecting to Brightspace · sign in if asked', kind='waiting')
        await wait_for_login(
            page, context,
            bs_username or None, bs_password or None,
            sso_email or None, sso_password or None,
        )

        log("─" * 52, "dim")
        check_stopped()
        await guide.status('Reading the Brightspace course structure…')
        bs_flat = await checker._fetch_bs_toc(page, course_id)
        if not bs_flat:
            return

        try:
            details = await page.evaluate('''async (id) => {
                const r = await fetch(`/d2l/api/lp/1.49/courses/${id}`);
                if (!r.ok) return null; return await r.json();
            }''', str(course_id))
            if isinstance(details, dict) and details.get('Name'):
                progress.data['course_name'] = details['Name']
                progress.save()
        except Exception:
            pass  # Course ID still identifies the saved run if its name is unavailable.

        log("─" * 52, "dim")
        check_stopped()
        if resume_id and progress.data['moodle_items']:
            await guide.status('Loading your saved Moodle activity list…')
            moodle_items = progress.data['moodle_items']
            log('Resuming the saved activity list — Moodle does not need to be scraped again.', 'info')
        else:
            await guide.status('Checking the Moodle course · continue in the app when ready', kind='waiting')
            moodle_items = await checker._scrape_moodle(context, inventory_only=True) if review_fn else await checker._scrape_moodle(context)
            if moodle_items: progress.capture_moodle(moodle_items)
            if review_fn and isinstance(getattr(checker, 'moodle_course_name', None), str):
                progress.data['moodle_course_name'] = checker.moodle_course_name
                progress.save()
        if not moodle_items:
            return

        check_stopped()
        parsed = urlparse(bs_url)
        bs_base = f"{parsed.scheme}://{parsed.netloc}"
        if review_fn:
            from h5p_review import review_and_prepare

            def on_download(key, state):
                row = next((r for r in progress.data['activities'] if r['key'] == key), None)
                if row:
                    progress.checkpoint(row, 'download', state)

            checker._h5p.on_download = on_download
            bs_flat = await review_and_prepare(checker, context, page, moodle_items, bs_flat,
                bs_base, course_id, progress, review_fn, on_state)
            if not checker._h5p._download_stems(moodle_items):
                progress.data['status'] = 'complete'
                progress.save()
                if on_complete:
                    on_complete()
                return
            for row in progress.data['activities']:
                if row.get('already_present'):
                    checker._summary['h5p_already_present'].append((row['name'], row['bs_module_title']))
                    if row['should_grade'] and not row.get('graded'):
                        checker._summary['h5p_grade_failed'].append((row['name'], row['bs_module_title']))
        elif not progress.data['activities']:
            bs_flat = await checker._ensure_h5p_destination_units(
                context, page, bs_base, course_id, moodle_items, bs_flat
            )
        if bs_flat is None:
            return
        check_stopped()
        await checker._h5p.embed_in_brightspace(
            context, page, moodle_items, bs_flat, bs_base, course_id
        )

        check_stopped()
        log("─" * 52, "dim")
        inserted = checker._summary.get("h5p_inserted", [])
        failed = checker._summary.get("h5p_failed", [])
        already = checker._summary.get("h5p_already_present", [])
        rows = progress.data['activities']
        failed_names = {name for name, _ in failed}
        grade_issues = {name for name, _ in checker._summary.get('h5p_grade_failed', [])}
        for row in rows:
            if row['name'] in failed_names:
                row['problem'] = row.get('problem') or 'Transfer needs attention. Resume to check existing work and retry.'
            if row['name'] in grade_issues:
                row['grade_attention'] = True
        remaining = progress.counts()[2]
        empty_course = bool(progress.data['moodle_items']) and not any(row['type'] == 'EXTERNAL' for row in progress.data['moodle_items'])
        completed = (bool(rows) or empty_course) and not remaining and not failed
        progress.data['status'] = 'complete' if completed else 'paused'
        progress.save()
        await guide.status('Transfer complete · saved pages verified' if completed else
            'Progress saved · some activities need attention', kind='success' if completed else 'attention')
        log(
            f"{'✓ H5P done' if completed else '⚠ H5P incomplete'}: {len(inserted)} newly inserted, "
            f"{len(already)} already present, {len(failed)} failed",
            "success" if completed else "warning",
        )
        log(
            f"  Gradebook: {len(checker._summary.get('h5p_graded', []))} graded, "
            f"{len(checker._summary.get('h5p_ungraded', []))} ungraded, "
            f"{len(checker._summary.get('h5p_grade_failed', []))} existing item(s) not changed",
            "info",
        )

        log(f"Saved course progress: {progress.label()}", 'info')
        if on_complete and completed:
            on_complete()

    except Exception as e:
        log(f"✗ Unexpected error: {e}", "error")
        raise
    finally:
        try:
            if progress.data['status'] != 'complete':
                progress.data['status'] = 'paused'
                progress.save()
            if on_state:
                on_state(dict(stage='results', run=progress.data, summary=checker._summary))
        finally:
            try:
                if browser and browser.is_connected(): await browser.close()
            finally:
                try:
                    if p: await p.stop()
                finally:
                    resources.close()
