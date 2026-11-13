"""Kaltura orchestration: preflight, saved review, guarded writes and recovery."""
import asyncio
import copy
import hashlib
import inspect
import os
from contextlib import ExitStack
from urllib.parse import urlsplit

from kaltura_progress import KalturaRun, VERIFIED, validate_courses, safe_problem
from kaltura_verification import read_modules, reconcile_row, apply_page, read_video_index, match_video
from h5p_browser_guide import BrowserGuide
from transfer_runner import PlanLock


async def interruptible(awaitable, stop, timeout=600):
    """Cancel reads/login waits promptly; never used around course mutations."""
    task = asyncio.ensure_future(awaitable)
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not task.done():
            if stop.is_set():
                raise asyncio.CancelledError()
            await asyncio.wait({task}, timeout=0.1)
            if asyncio.get_running_loop().time() >= deadline and not task.done():
                raise TimeoutError('This step took too long. Progress is saved; reconnect and try again.')
        return await task
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class KalturaSession:
    def __init__(self, run, credentials, stop, emit, log, watch):
        from kaltura_categorizer import KalturaCategorizer
        self.run, self.credentials, self.stop, self.emit, self.log = run, credentials, stop, emit, log
        self.watch, self.guides = watch, []
        self.contexts, self.service = {}, 'Brightspace'
        self._review_inventory = {}
        self.p = self.browser = self.kmc_browser = self.page = self.kmc_page = None
        self.cat = KalturaCategorizer(on_state=emit, observe_context=self.observe, stop=stop, visible_scan=watch)

    async def observe(self, context):
        self.contexts[self.service] = context
        guide = BrowserGuide(self.emit, enabled=self.watch, label='KALTURA TRANSFER · LIVE')
        self.guides.append(guide)
        await guide.install(context)

    async def status(self, message, *, waiting=False, service=None):
        if service:
            self.service = service
        self.emit(dict(stage='status', service=self.service, message=message, waiting=waiting))
        for guide in self.guides:
            await guide.status(message, kind='waiting' if waiting else 'working')

    async def connect(self):
        from browser import launch_browser, wait_for_login
        c = self.credentials
        await self.status('Connect to Brightspace · complete sign-in or MFA in the browser if asked', waiting=True)
        self.p, self.browser, context, self.page = await launch_browser(log_fn=self.log)
        await self.observe(context)
        await wait_for_login(self.page, context, c.get('bs_username') or None, c.get('bs_password') or None,
                             c.get('sso_email') or None, c.get('sso_password') or None, log_fn=self.log)
        self.emit(dict(stage='connection', service='Brightspace', state='Connected'))
        parts = urlsplit(self.run.data['bs_url'])
        await self.page.goto(f'{parts.scheme}://{parts.netloc}/d2l/le/content/{self.run.data["course_id"]}/home', wait_until='domcontentloaded')
        self.run.data['modules'] = await read_modules(self.page, self.run.data['course_id'])
        try:
            details = await self.page.evaluate('''async id => {
                const r = await fetch(`/d2l/api/lp/1.49/courses/${id}`);
                return r.ok ? await r.json() : null;
            }''', self.run.data['course_id'])
            if isinstance(details, dict) and details.get('Name'):
                self.run.data['course_name'] = details['Name']
        except Exception:
            pass  # The course ID remains the authoritative identity.
        self.run.save()
        self.service = 'Kaltura'
        await self.status('Sign in to Kaltura · finish sign-in or MFA in the Kaltura browser', waiting=True)
        _, self.kmc_browser, self.kmc_page = await self.cat._get_kmc_context(self.p,
            kmc_username=c.get('kmc_username', ''), kmc_password=c.get('kmc_password', ''),
            sso_email=c.get('sso_email', ''), sso_password=c.get('sso_password', ''), log_fn=self.log)
        self.emit(dict(stage='connection', service='Kaltura', state='Connected'))

    async def scan(self):
        from kaltura_categorizer import MOODLE_SESSION_FILE
        c = self.credentials
        self.service = 'Moodle'
        self.cat.scan_issues = []
        if not os.path.exists(MOODLE_SESSION_FILE):
            await self.status('Sign in to Moodle · use the browser to finish sign-in', waiting=True)
            await self.cat.login_to_moodle(self.run.data['moodle_url'],
                moodle_username=c.get('moodle_username', ''), moodle_password=c.get('moodle_password', ''), log_fn=self.log)
        await self.status('Scanning the whole Moodle course, pages and book chapters…')
        finished = False
        try:
            entries = await self.cat.scan_moodle_course(self.run.data['moodle_url'], log_fn=self.log,
                moodle_username=c.get('moodle_username', ''), moodle_password=c.get('moodle_password', ''))
            finished = True
        finally:
            if not finished:
                self.run.capture_scan(getattr(self.cat, 'scan_entries', []),
                    self.cat.scan_issues + [dict(url=self.run.data['moodle_url'], reason='Source scan did not finish. Retry the source scan to establish complete coverage.')])
        self.emit(dict(stage='connection', service='Moodle', state='Connected'))
        return entries, self.cat.scan_issues

    async def reconcile(self, row):
        async def find(page, course, module, entry_id):
            key = (course, str(module))
            if key not in self._review_inventory:
                self._review_inventory[key] = await read_video_index(page, course, module)
            return match_video(self._review_inventory[key], entry_id)
        await reconcile_row(self.page, self.run, row, finder=find)

    async def create_unit(self, name):
        from bs_units import create_unit
        parts = urlsplit(self.run.data['bs_url'])
        return await create_unit(self.page, f'{parts.scheme}://{parts.netloc}', self.run.data['course_id'], name, self.log)

    async def modules(self):
        return await read_modules(self.page, self.run.data['course_id'])

    async def apply(self, row):
        self.service = 'Brightspace'
        await apply_page(self.run, row, self.page, self.cat, self.kmc_page, self.log,
                         stop=self.stop, announce=self.status)

    async def show_browser(self, service='Brightspace'):
        context = self.contexts.get(service)
        if context:
            for page in reversed(context.pages):
                if not page.is_closed():
                    await page.bring_to_front()
                    return

    async def close(self):
        try:
            if self.kmc_browser:
                await self.kmc_browser.close()
        finally:
            try:
                if self.browser:
                    await self.browser.close()
            finally:
                if self.p:
                    await self.p.stop()


def suggest_destinations(run):
    from content_matcher import match_sections
    for row in run.data['rows']:
        if row.get('destination') or row['state'] in VERIFIED:
            continue
        match, score = match_sections([row['section_name']], run.data['modules'])[row['section_name']]
        if match and score >= 75:
            row['destination'] = {'id': str(match['id'])}
            row['selected'] = score >= 90
    run.save()


async def resolve_units(run, session, stop):
    """Only approved selected rows may request units; journal before creation."""
    from js_helpers import _norm
    for row in run.data['rows']:
        if not row['selected'] or not (row.get('destination') or {}).get('new'):
            continue
        if stop.is_set():
            raise asyncio.CancelledError()
        name = row['destination']['new'].strip()
        modules = await session.modules()  # Failed read raises, never implies absence.
        matches = [m for m in modules if _norm(m['title']) == _norm(name)]
        if len(matches) > 1:
            raise ValueError(f"Several units are named {name}. Choose an existing destination explicitly.")
        journal = run.data['units'].get(name)
        if not matches:
            if journal and journal['state'] in ('pending', 'needs_check'):
                raise ValueError(f"Unit {name} has an uncertain creation outcome. Inspect it in Brightspace before requesting another unit.")
            run.data['units'][name] = dict(state='pending')
            run.save()
            await session.status(f'Creating unit: {name}')
            try:
                await asyncio.wait_for(session.create_unit(name), 90)
            finally:
                # Keep pending evidence even when the UI operation times out.
                run.save()
            modules = await session.modules()
            matches = [m for m in modules if _norm(m['title']) == _norm(name)]
            if len(matches) != 1:
                run.data['units'][name]['state'] = 'needs_check'
                run.save()
                raise RuntimeError(f"Unit {name} could not be verified. Check it before retrying.")
        module_id = str(matches[0]['id'])
        run.data['units'][name] = dict(state='verified', id=module_id)
        run.data['modules'] = modules
        # Resolve every approved use of this name, preserving explicit exclusions.
        for item in run.data['rows']:
            if item['selected'] and item.get('destination') == {'new': name}:
                item['destination'] = {'id': module_id}
        run.save()


async def run_kaltura(bs_url, moodle_url, *, stop, review, emit, log,
                      credentials=None, resume_id=None, directory=None,
                      watch=True, rescan=False, session_factory=KalturaSession):
    bs, moodle, course = validate_courses(bs_url, moodle_url)
    run = KalturaRun.load(resume_id, directory) if resume_id else KalturaRun.create(bs, moodle, directory)
    if (run.data['bs_url'], run.data['moodle_url'], run.data['course_id']) != (bs, moodle, course):
        raise ValueError('This saved transfer belongs to another course pair.')
    if run.data.get('removed_at'):
        raise ValueError('Restore this transfer to the list before continuing it.')
    run.on_change = lambda data: emit(dict(stage='progress', run=data))
    session = None
    with ExitStack() as locks:
        locks.enter_context(PlanLock(run.path))
        target_key = hashlib.sha256((urlsplit(bs).netloc.lower() + ':' + course).encode()).hexdigest()
        locks.enter_context(PlanLock(run.path.parent / f'target-{target_key}'))
        try:
            run.data.update(status='running', error='')
            run.save()
            session = session_factory(run, credentials or {}, stop, emit, log, watch)
            if hasattr(session, 'show_browser'):
                emit(dict(stage='browser_control', show=session.show_browser))
            emit(dict(stage='connect', message='Connecting your courses and Kaltura'))
            await interruptible(session.connect(), stop)
            if not run.data['scan_complete'] or rescan:
                entries, issues = await interruptible(session.scan(), stop, timeout=3600)
                run.capture_scan(entries, issues)
                suggest_destinations(run)
            # Validate every saved placement before presenting any retry action.
            await session.status('Checking saved destinations before review…', service='Brightspace')
            for row in run.data['rows']:
                await interruptible(session.reconcile(row), stop, timeout=120)
                run.save()
            if not run.data['rows'] and run.data['scan_complete']:
                run.data['status'] = 'complete'
                run.save()
                return run
            run.data['status'] = 'review'
            run.save()
            await session.status('Review selections and destinations in the app', waiting=True)
            emit(dict(stage='review', run=copy.deepcopy(run.data)))
            answer = review(copy.deepcopy(run.data), run.save_draft)
            answer = await answer if inspect.isawaitable(answer) else answer
            if answer is None or stop.is_set():
                raise asyncio.CancelledError()
            run.approve(answer['choices'], partial_ack=bool(answer.get('partial_ack')))
            # Reread IDs after the human wait. No approved stale destination writes.
            run.data['modules'] = await interruptible(session.modules(), stop)
            known = {str(m['id']) for m in run.data['modules']}
            if any(r['selected'] and (r.get('destination') or {}).get('id') not in known
                   for r in run.data['rows'] if 'id' in (r.get('destination') or {})):
                raise ValueError('A destination changed while you were reviewing. Check the plan again.')
            run.data['status'] = 'running'
            run.save()
            emit(dict(stage='create', message='Creating and verifying approved pages'))
            await resolve_units(run, session, stop)
            for row in run.data['rows']:
                if stop.is_set():
                    raise asyncio.CancelledError()
                if not row['selected']:
                    continue
                await session.status(f"Checking and placing: {row['name']}", service='Brightspace')
                await session.apply(row)  # Never cancel an in-flight save for Stop.
                run.save()
            complete = (run.data['scan_complete'] and not run.counts()['remaining']
                        and not any(r['state'] in ('pending', 'needs_check') for r in run.data['rows']))
            run.data['status'] = 'complete' if complete else 'paused'
            run.save()
            return run
        except asyncio.CancelledError:
            log('Stopped. Saved pages and uncertain write evidence are kept.', 'warning')
        except Exception as exc:
            run.data['error'] = safe_problem(exc)
            log(run.data['error'], 'error')
        finally:
            if run.data['status'] != 'complete':
                run.data['status'] = 'paused'
            run.save()
            try:
                if session:
                    await session.close()
            finally:
                emit(dict(stage='results', run=copy.deepcopy(run.data)))
    return run
