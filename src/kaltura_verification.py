"""Read destination evidence before claiming or repeating a Kaltura write."""
import asyncio
import html
import re
from urllib.parse import unquote, urlsplit

from unit_overview import BrowserContentAPI
from kaltura_progress import safe_problem


def entry_ids(markup):
    text = html.unescape(str(markup))
    for _ in range(2):
        text = unquote(text)
    return set(re.findall(r'''\bentry_?id[\s"':=/]+([01]_[a-z0-9_]+)''', text, re.I))


async def read_modules(page, course_id):
    from kaltura_categorizer import KalturaCategorizer
    result = await page.evaluate(KalturaCategorizer._TOC_MODULES_JS, str(course_id))
    if not isinstance(result, dict) or result.get('error') or not isinstance(result.get('modules'), list):
        raise RuntimeError('Cannot read the full Brightspace unit inventory. No units will be created.')
    return result['modules']


async def module_topics(page, course_id, module_id):
    rows = await BrowserContentAPI(page, course_id, module_id).list_structure()
    if not isinstance(rows, list) or any(not isinstance(t, dict) or 'Id' not in t for t in rows):
        raise RuntimeError('Cannot read the complete destination topic list. No new page will be created.')
    return rows


async def read_video_index(page, course_id, module_id):
    """Read one complete module inventory; unreadable pages invalidate the read.

    Read every HTML page, even if its title differs: title-only matching loses
    renamed pages and risks creating duplicates after an interrupted save.
    """
    topics = await module_topics(page, course_id, module_id)
    index = {}
    api = BrowserContentAPI(page, course_id, module_id)
    for topic in topics:
        path = urlsplit(topic.get('Url') or '').path.lower()
        if topic.get('Type') == 0 or topic.get('TopicType', 1) != 1:
            continue
        # TopicType 1 is a file page; known non-HTML resources cannot embed it.
        if '.' in path.rsplit('/', 1)[-1] and not path.endswith(('.html', '.htm')):
            continue
        markup = await api.get_topic_html(topic['Id'])
        for id_ in entry_ids(markup):
            index.setdefault(id_, []).append(topic)
    return index


_NAVIGATION_ERRORS = ('Execution context was destroyed', 'interrupted by another navigation')
INTERRUPTED_CHECK = ('Saved, but the check was cut off while Brightspace was still loading. '
                     'Choose Check & continue to confirm it.')


def _navigation_error(exc):
    return any(marker in str(exc) for marker in _NAVIGATION_ERRORS)


async def _settled_read(page, read, attempts=3):
    """Repeat a read cut off by Brightspace still navigating after Save."""
    for attempt in range(attempts):
        try:
            return await read()
        except Exception as exc:
            if attempt == attempts - 1 or not _navigation_error(exc):
                raise
            try:
                await page.wait_for_load_state('load', timeout=15000)
            except Exception:
                pass  # The retry below decides; a slow load is not itself a failure.
            await asyncio.sleep(1)


def match_video(index, entry_id):
    matches = index.get(entry_id, [])
    if len(matches) > 1:
        raise RuntimeError('Several pages contain this video in the destination. Inspect them before continuing.')
    return matches[0] if matches else None


async def find_video(page, course_id, module_id, entry_id):
    return match_video(await read_video_index(page, course_id, module_id), entry_id)


async def reconcile_row(page, run, row, *, finder=None):
    destination = row.get('destination') or {}
    if not destination.get('id'):
        return
    modules = {str(m['id']) for m in run.data['modules']}
    if str(destination['id']) not in modules:
        row['state'] = 'needs_check'
        row['problem'] = 'The saved destination is no longer in this course. Inspect the saved page before making another placement.'
        return
    try:
        found = await _settled_read(page, lambda: (finder or find_video)(
            page, run.data['course_id'], destination['id'], row['entry_id']))
        if found:
            if row.get('topic_id') and str(found['Id']) != str(row['topic_id']):
                raise RuntimeError('The saved page identity changed. Check the destination before continuing.')
            if row.get('attempted') and found.get('Title') != row['name']:
                raise RuntimeError('The video was saved with an unexpected title. Correct or inspect that page before continuing.')
            row.update(state='created' if row.get('attempted') else 'already_present',
                       topic_id=str(found['Id']), problem='', verified=True)
        elif row.get('topic_id'):
            raise RuntimeError('A previously verified page changed or disappeared. It will not be recreated automatically.')
        else:
            if row.get('attempted'):
                before = row.get('before_topic_ids')
                if before is None:
                    raise RuntimeError('The previous save has no destination snapshot. Inspect its outcome before creating another page.')
                topics = await _settled_read(page, lambda: module_topics(page, run.data['course_id'], destination['id']))
                if any(str(t['Id']) not in before for t in topics):
                    raise RuntimeError('New destination content exists after the previous save, but its video embed is not verified. Inspect that content before retrying.')
            row.update(state='ready', problem='', verified=False)
    except Exception as exc:
        problem = INTERRUPTED_CHECK if _navigation_error(exc) else safe_problem(exc)
        row.update(state='needs_check', problem=problem, verified=False)


async def apply_page(run, row, page, cat, kmc_page, log, *, stop=None, announce=None):
    """One placement. Persist intent before editor actions and reconcile on error."""
    course, module = run.data['course_id'], row['destination']['id']
    await reconcile_row(page, run, row)
    run.save()
    if row['state'] in ('created', 'already_present', 'needs_check'):
        return
    if stop is not None and stop.is_set():
        return
    row.update(state='preparing', problem='')
    run.save()
    try:
        if announce:
            await announce(f"Retrieving Kaltura embed: {row['name']}", service='Kaltura')
        code = await cat._get_embed_code(kmc_page, row['entry_id'], log)
        if not code or entry_ids(code) != {row['entry_id']}:
            raise ValueError('The exact video embed could not be retrieved from Kaltura. No page was created.')
    except Exception as exc:
        row.update(state='failed', problem=safe_problem(exc))
        run.save()
        return
    if stop is not None and stop.is_set():
        row['state'] = 'ready'
        run.save()
        return
    baseline = await module_topics(page, course, module)
    row.update(state='pending', attempted=True, attempts=row.get('attempts', 0) + 1,
               before_topic_ids=[str(t['Id']) for t in baseline])
    run.save()  # A crash from this point forward must never trigger a blind retry.
    parts = urlsplit(run.data['bs_url'])
    base = f'{parts.scheme}://{parts.netloc}'
    error = ''
    try:
        import asyncio
        if announce:
            await announce(f"Creating Brightspace page: {row['name']}", service='Brightspace')
        ok = await asyncio.wait_for(cat._create_bs_page(page, base, course, str(module), row['name'], code, log), 240)
        if not ok:
            error = 'The editor did not confirm the save.'
    except Exception as exc:
        error = safe_problem(exc)
    if announce:
        await announce(f"Checking saved page: {row['name']}", service='Brightspace')
    await reconcile_row(page, run, row)
    if row['state'] == 'ready':
        # Stay uncertain on this attempt. A fresh resume performs a new read,
        # then requires review/approval before a proven-absent placement retries.
        row.update(state='needs_check', problem=error or 'The saved page could not be found. Check the destination before retrying.')
    run.save()
