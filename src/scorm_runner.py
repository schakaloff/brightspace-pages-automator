"""Separate, resumable Moodle ZIP → Brightspace SCORM workflow."""
from contextlib import ExitStack

from browser import launch_browser, wait_for_login
from config import SESSION_FILE
from content_checker import ContentChecker
from scorm_packages import SCORMRun, scorm_sources, download_package
from scorm_handler import SCORMHandler, module_topics, matching_topics
from transfer_runner import PlanLock


async def prepare_scorm(bs_url, moodle_url, log, *, ready_event=None, on_waiting=None,
                        credentials=None, directory=None, refresh=False):
    run = SCORMRun.open(bs_url, moodle_url, directory)
    checker = ContentChecker(bs_url, moodle_url, log,
                             moodle_ready_event=ready_event, on_moodle_waiting=on_waiting,
                             **(credentials or {}))
    with ExitStack() as resources:
        resources.enter_context(PlanLock(run.path))
        p = browser = None
        try:
            p, browser, context, page = await launch_browser()
            await wait_for_login(page, context, checker.bs_username or None, checker.bs_password or None,
                                 checker.sso_email or None, checker.sso_password or None, log_fn=log)
            flat = await checker._fetch_bs_toc(page, run.data['course_id'])
            if flat is None:
                raise RuntimeError('Cannot read Brightspace units.')
            modules = [m for m in flat if m['kind'] == 'MODULE']
            items = await checker._scrape_moodle(context, inventory_only=True)
            if items is None:
                raise RuntimeError('Moodle inventory could not be completed.')
            run.capture(scorm_sources(items), modules)
            log(f"Found {len(run.data['activities'])} SCORM activities in this Moodle course.", 'info')
            tab = await context.new_page()
            for row in run.data['activities']:
                row.pop('error', None)
                try:
                    log(f"Checking original ZIP: {row['name']}…", 'info')
                    try:
                        file, cached = await download_package(tab, run, row, refresh=refresh)
                    except Exception:
                        row['download_valid'] = False
                        raise
                    log(f"{'Cached' if cached else 'Downloaded + validated'}: {row['name']} ({file.stat().st_size / 1_000_000:.1f} MB)", 'success')
                    row['state'] = 'Ready' if row['module_id'] else 'Choose a destination unit'
                    if row['module_id']:
                        matches = matching_topics(await module_topics(page, run.data['course_id'], row['module_id']), row)
                        if len(matches) > 1:
                            raise RuntimeError('Multiple SCORM topics match this activity in the destination unit.')
                        if matches:
                            row.update(state='Already present', insertion='confirmed', topic_id=str(matches[0]['Id']))
                except Exception as exc:
                    row.update(state='Needs attention', error=str(exc))
                    log(f"{row['name']}: {exc}", 'error')
                run.save()
            await context.storage_state(path=SESSION_FILE)
            log('SCORM preparation finished. Review the destination units before transferring.', 'info')
            return run, modules
        finally:
            if browser: await browser.close()
            if p: await p.stop()


async def transfer_scorm(bs_url, moodle_url, selections, log, *, credentials=None, directory=None):
    run = SCORMRun.open(bs_url, moodle_url, directory)
    credentials = credentials or {}
    with PlanLock(run.path):
        p = browser = None
        try:
            p, browser, context, page = await launch_browser()
            await wait_for_login(page, context, credentials.get('bs_username'), credentials.get('bs_password'),
                                 credentials.get('sso_email'), credentials.get('sso_password'), log_fn=log)
            checker = ContentChecker(bs_url, moodle_url, log)
            flat = await checker._fetch_bs_toc(page, run.data['course_id'])
            if flat is None:
                raise RuntimeError('Cannot read Brightspace units; no packages added.')
            modules = {str(m['id']): m['title'] for m in flat if m['kind'] == 'MODULE'}
            handler = SCORMHandler(page, run.data['course_id'], log)
            inserted = existing = failed = 0
            for key, module_id in selections:
                row = next((r for r in run.data['activities'] if r['key'] == str(key)), None)
                if row is None or str(module_id) not in modules:
                    raise ValueError('The selected SCORM activity or destination no longer exists. Prepare the course again.')
                row.update(module_id=str(module_id), module_title=modules[str(module_id)])
                try:
                    file = run.cached(row)
                    if file is None:
                        raise RuntimeError('Cached ZIP is missing or changed. Prepare the course again.')
                    topics = await module_topics(page, run.data['course_id'], module_id)
                    matches = matching_topics(topics, row)
                    if len(matches) > 1:
                        raise RuntimeError('More than one matching SCORM topic exists; inspect the unit.')
                    if matches:
                        row.update(insertion='confirmed', topic_id=str(matches[0]['Id']), state='Already present')
                        existing += 1
                        log(f"Already present: {row['name']} → {row['module_title']}", 'success')
                        continue
                    if row.get('insertion') == 'pending':
                        raise RuntimeError('A previous Add has an unknown result. Inspect the destination unit before retrying; automatic insertion is paused to avoid duplicates.')
                    def checkpoint(**details):
                        row.update(details)
                        run.save()
                    topic = await handler.transfer(row, file, checkpoint)
                    row.update(state='Done + verified', topic_id=str(topic['Id']))
                    row.pop('error', None)
                    inserted += 1
                    log(f"Done + verified: {row['name']} → {row['module_title']}", 'success')
                except Exception as exc:
                    row.update(state='Needs attention', error=str(exc))
                    failed += 1
                    log(f"{row['name']}: {exc}", 'error')
                    try:
                        folder = run.folder(row)
                        folder.mkdir(parents=True, exist_ok=True)
                        await page.screenshot(path=str(folder / 'transfer-error.png'))
                    except Exception:
                        pass
                    # A failed service dialog may still contain an in-flight
                    # upload/Add. Stop instead of navigating it away or retrying.
                    break
                finally:
                    run.save()
            log(f'SCORM result: {inserted} inserted, {existing} already present, {failed} failed.',
                'warning' if failed else 'success')
            return run
        finally:
            if browser: await browser.close()
            if p: await p.stop()
