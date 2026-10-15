"""Brightspace's SCORM package picker (separate from H5P's editor dialog)."""
import asyncio
import time
import re
from pathlib import Path
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from scorm_packages import normalized


def matching_topics(topics, row):
    # A same-named HTML page or Moodle link does not prove a SCORM transfer.
    names = {normalized(row.get(k, '')) for k in ('name', 'package_title')}
    if row.get('filename'):
        names.add(normalized(Path(row['filename']).stem))
    names.discard('')
    def is_package(url):
        url = str(url).casefold()
        return (url.startswith('d2l:brightspace:content:') and ':scorm:' in url) or (
            url.startswith(('/d2l/', 'https://learn.okanagancollege.ca/d2l/')) and 'scorm' in url)
    return [t for t in topics if normalized(t.get('Title', '')) in names and is_package(t.get('Url', ''))]


async def module_topics(page, course_id, module_id):
    result = await asyncio.wait_for(page.evaluate('''async ([course, module]) => {
        const r = await fetch(`/d2l/api/le/1.0/${course}/content/modules/${module}/structure/?_=${Date.now()}`,
            {credentials:'include',cache:'no-store'});
        if (!r.ok) throw Error(`Cannot read destination unit: HTTP ${r.status}`);
        return await r.json();
    }''', [str(course_id), str(module_id)]), timeout=30)
    if not isinstance(result, list):
        raise RuntimeError('Cannot verify the destination unit. Transfer stopped before adding content.')
    return result


class SCORMHandler:
    def __init__(self, page, course_id, log):
        self.page, self.course_id, self.log = page, str(course_id), log

    async def _click(self, name, *, frame=None, timeout=15000):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            for current in [frame] if frame else self.page.frames:
                candidates = current.get_by_role('button', name=name, exact=True)
                for i in range(await candidates.count()):
                    candidate = candidates.nth(i)
                    if await candidate.is_visible() and await candidate.is_enabled():
                        try:
                            await candidate.click(timeout=1000)
                        except PlaywrightTimeoutError:
                            if not await candidate.is_visible() or not await candidate.is_enabled():
                                continue
                            await candidate.evaluate('(el)=>el.click()')
                        return
                # D2L sometimes exposes only the host text before the inner button.
                hosts = current.locator('d2l-button:visible').filter(has_text=name)
                for i in range(await hosts.count()):
                    candidate = hosts.nth(i)
                    if (await candidate.inner_text()).strip() == name and not await candidate.evaluate("e=>e.disabled || e.hasAttribute('disabled')"):
                        try:
                            await candidate.click(timeout=1000)
                        except PlaywrightTimeoutError:
                            await candidate.evaluate("el=>(el.shadowRoot?.querySelector('button') || el).click()")
                        return
            await asyncio.sleep(.25)
        raise RuntimeError(f'The SCORM dialog’s {name} button did not become available.')

    async def _check(self, radio):
        await radio.wait_for(state='visible', timeout=15000)
        try:
            await radio.check(timeout=1000)
        except PlaywrightTimeoutError:
            if not await radio.is_visible() or not await radio.is_enabled(): raise
            await radio.evaluate('(el)=>{if(!el.checked)el.click()}')
        if not await radio.is_checked():
            raise RuntimeError('The SCORM dialog did not keep the selected option.')

    async def open_picker(self, module_id):
        # This is the live URL loaded by Add Existing → SCORM/xAPI Object.
        # Hosting the same picker directly prevents stacked modal iframes from
        # intercepting its controls during smart-curriculum re-renders.
        self.log('Opening Brightspace SCORM package picker…', 'info')
        await self.page.goto(
            f'https://learn.okanagancollege.ca/d2l/le/content/contentservice/addcontent/AddContent?edit=0&oId={module_id}&ou={self.course_id}',
            wait_until='domcontentloaded', timeout=30000)
        await self.page.locator('#upload-button').wait_for(state='visible', timeout=30000)
        return self.page.main_frame

    async def find_package(self, frame, filename):
        self.log(f'Checking SCORM library for {filename}…', 'info')
        search = frame.locator('input[type="search"]')
        # The service searches words, and generated filenames contain token
        # punctuation. Use a plain leading phrase, then require the complete
        # exact filename in the result before selecting anything.
        phrase = ' '.join(Path(filename).stem.split('-')[:3])
        await search.fill(phrase)
        await search.press('Enter')
        # Search is debounced and performed by the package service. Require a
        # stable, non-loading result list before deciding it is absent.
        previous, stable = None, 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            await asyncio.sleep(.5)
            titles = await frame.locator('.search-result .title').all_text_contents()
            titles = [t.strip() for t in titles]
            loading = await frame.locator('[aria-busy="true"]:visible,d2l-loading-spinner:visible').count()
            stable = stable + 1 if titles == previous and not loading else 0
            previous = titles
            if stable >= 6:
                matches = [i for i, title in enumerate(titles) if title == Path(filename).stem]
                if len(matches) > 1:
                    raise RuntimeError('Multiple library packages have this filename. Choose the correct package manually before retrying.')
                if matches:
                    row = frame.locator('.search-result').nth(matches[0])
                    radio = row.locator('input[name="selected-result"]')
                    return radio
                self.log(f"No exact library filename match for {Path(filename).stem} ({len(titles)} search results).", 'detail')
                return None
        raise RuntimeError('SCORM library search did not finish; no upload attempted.')

    async def transfer(self, row, file, checkpoint):
        frame = await self.open_picker(row['module_id'])
        radio = await self.find_package(frame, row['filename'])
        if radio is None:
            if row.get('upload') == 'pending':
                raise RuntimeError('A previous upload has an unknown result. Check the SCORM library before retrying; automatic upload is paused to avoid duplicates.')
            await self._click('Bulk Upload', frame=frame)
            chooser = frame.locator('#file-select')
            await chooser.wait_for(state='attached', timeout=15000)
            # Setting the input starts the service upload immediately.
            checkpoint(upload='pending')
            await chooser.set_input_files(str(file))
            # Uploading/processing can take several minutes. Only an exact
            # library result counts as successful, never the file-input event.
            deadline = time.monotonic() + 1800
            next_update = time.monotonic()
            direct_settings = False
            while time.monotonic() < deadline:
                if time.monotonic() >= next_update:
                    self.log(f"Uploading / processing {row['filename']}…", 'info')
                    self.log('SCORM upload controls: ' + ', '.join((t.strip() for t in await frame.locator('d2l-button:visible').all_text_contents())), 'detail')
                    next_update = time.monotonic() + 15
                settings = frame.locator('#create-grade-item-no:visible')
                if await settings.count():
                    direct_settings = True
                    break
                back = frame.locator('#uploader-back:visible')
                if await back.count() and await back.is_enabled():
                    # Do not leave the uploader until its completion text exists.
                    completed = frame.get_by_text(re.compile(r'upload(?:ed)? complete|successfully uploaded', re.I)).first
                    if await completed.count() and await completed.is_visible():
                        await back.click()
                        break
                if await frame.locator('#upload-button:visible').count():
                    break
                # Different package-service versions finish with Save or Next.
                for name in ('Save', 'Next', 'Done'):
                    finish = frame.get_by_role('button', name=name, exact=True)
                    if await finish.count() and await finish.is_visible() and await finish.is_enabled():
                        await self._click(name, frame=frame)
                        await asyncio.sleep(.5)
                        break
                await asyncio.sleep(.5)
            else:
                raise RuntimeError('SCORM upload was not confirmed. Cached ZIP retained; check the open library before retrying.')
            if not direct_settings:
                radio = await self.find_package(frame, row['filename'])
                if radio is None:
                    raise RuntimeError('Uploaded SCORM package was not found in the library. Its upload result needs checking.')
        if radio is not None:
            await self._check(radio)
        checkpoint(upload='confirmed')
        self.log('Package found. Choosing ungraded, fixed-version settings…', 'info')
        if radio is not None:
            await self._click('Next', frame=frame)
        no_grade = frame.locator('#create-grade-item-no')
        await no_grade.wait_for(state='visible', timeout=30000)
        await self._check(no_grade)
        await self._check(frame.locator('#this-version'))
        if not await no_grade.is_checked() or not await frame.locator('#this-version').is_checked():
            raise RuntimeError('Could not confirm ungraded, fixed-version SCORM settings.')
        checkpoint(insertion='pending')
        self.log('Adding the SCORM package to its unit…', 'info')
        await self._click('Add', frame=frame)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            matches = matching_topics(await module_topics(self.page, self.course_id, row['module_id']), row)
            if len(matches) == 1:
                checkpoint(insertion='confirmed', topic_id=str(matches[0]['Id']))
                return matches[0]
            if len(matches) > 1:
                raise RuntimeError('Multiple matching SCORM topics appeared; inspect the destination unit.')
            await asyncio.sleep(1)
        raise RuntimeError('Added SCORM topic could not be verified. Check the destination unit before retrying.')
