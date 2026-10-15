"""Original Moodle SCORM ZIPs and durable, course-scoped transfer progress."""
from __future__ import annotations

import hashlib
import html
import json
import ntpath
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile, BadZipFile

from config import USERDATA_DIR
from h5p_progress import clean_url

RUNS_DIR = USERDATA_DIR / 'scorm-runs'


def normalized(text):
    return re.sub(r'[^\w]', '', html.unescape(str(text)).casefold().replace('&', 'and'))


def course_pair(bs_url, moodle_url):
    bs_url, moodle_url = clean_url(bs_url), clean_url(moodle_url)
    bs, moodle = urlsplit(bs_url), urlsplit(moodle_url)
    if bs.scheme != 'https' or bs.hostname != 'learn.okanagancollege.ca':
        raise ValueError('Enter an Okanagan College Brightspace course URL.')
    if moodle.scheme != 'https' or moodle.hostname != 'mymoodle.okanagan.bc.ca' or moodle.path != '/course/view.php':
        raise ValueError('Enter the Moodle course/view.php?id=… URL for the whole course.')
    if not parse_qs(moodle.query).get('id', [''])[0].isdigit():
        raise ValueError('The Moodle course URL needs a numeric course ID.')
    from content_checker import _extract_course_id
    course_id = _extract_course_id(bs_url)
    if not course_id or not str(course_id).isdigit():
        raise ValueError('The Brightspace URL needs a course ID.')
    return bs_url, moodle_url, str(course_id)


def scorm_sources(items):
    section, result, seen = '', [], set()
    for item in items:
        if item.get('type') == 'SECTION':
            section = item['name']
            continue
        href = item.get('href', '')
        parts = urlsplit(href)
        if parts.hostname != 'mymoodle.okanagan.bc.ca' or parts.path != '/mod/scorm/view.php':
            continue
        cmid = parse_qs(parts.query).get('id', [''])[0]
        if not cmid.isdigit() or cmid in seen:
            continue
        seen.add(cmid)
        result.append(dict(key=cmid, name=item['name'], section=section, href=clean_url(href)))
    return result


def validate_package(path, *, full=True):
    """Read the original archive without extracting or rewriting its contents."""
    try:
        with ZipFile(path) as archive:
            roots = [i for i in archive.infolist() if i.filename == 'imsmanifest.xml']
            if len(roots) != 1 or roots[0].file_size > 2_000_000:
                raise ValueError('The ZIP must contain one imsmanifest.xml at its root.')
            xml = archive.read(roots[0])
            # ElementTree does not fetch external entities; also reject entity expansion.
            if re.search(br'<!\s*(DOCTYPE|ENTITY)', xml, re.I):
                raise ValueError('SCORM manifest contains unsupported XML declarations.')
            root = ElementTree.fromstring(xml)
            if root.tag.rsplit('}', 1)[-1] != 'manifest':
                raise ValueError('The SCORM manifest is invalid.')
            metadata = [e.text or '' for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'schema']
            scorm_attrs = any('scormtype' in k.casefold() for e in root.iter() for k in e.attrib)
            if not scorm_attrs and not any('adl scorm' in value.casefold() for value in metadata):
                raise ValueError('The ZIP is an IMS package, but does not identify itself as SCORM.')
            titles = [e.text.strip() for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'title' and e.text and e.text.strip()]
            if full:
                # Bound decompression work before testing CRCs on untrusted ZIP members.
                if sum(i.file_size for i in archive.infolist()) > 4_000_000_000:
                    raise ValueError('Expanded SCORM package exceeds the 4 GB validation limit.')
                damaged = archive.testzip()
                if damaged:
                    raise ValueError(f'The ZIP contains a damaged file: {damaged}')
    except (BadZipFile, ElementTree.ParseError, RuntimeError, OSError) as exc:
        raise ValueError(f'Invalid SCORM ZIP: {exc}') from exc
    with Path(path).open('rb') as file:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            hasher.update(chunk)
    return dict(package_title=titles[0] if titles else '', sha256=hasher.hexdigest(), bytes=Path(path).stat().st_size)


def safe_filename(name):
    if not name or ntpath.basename(name) != name or Path(name).name != name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError('Moodle returned an unsafe package filename.')
    if not name.casefold().endswith('.zip'):
        raise ValueError('Moodle did not return a ZIP package.')
    return name


class SCORMRun:
    @classmethod
    def open(cls, bs_url, moodle_url, directory=None):
        bs_url, moodle_url, course_id = course_pair(bs_url, moodle_url)
        moodle_id = parse_qs(urlsplit(moodle_url).query)['id'][0]
        key = hashlib.sha256(f'{course_id}:{moodle_id}'.encode()).hexdigest()[:32]
        path = Path(directory or RUNS_DIR) / f'{key}.json'
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(
            schema=1, id=key, course_id=course_id, bs_url=bs_url, moodle_url=moodle_url, activities=[])
        if data.get('schema') != 1 or data.get('id') != key or not isinstance(data.get('activities'), list):
            raise ValueError('Saved SCORM progress is invalid.')
        if data.get('course_id') != course_id or data.get('moodle_url') != moodle_url:
            raise ValueError('Saved SCORM progress belongs to a different course pair.')
        run = cls()
        run.path, run.data = path, data
        for row in data['activities']:
            if not isinstance(row, dict) or not str(row.get('key', '')).isdigit():
                raise ValueError('Saved SCORM activity is invalid.')
            if row.get('filename'):
                safe_filename(row['filename'])
        return run

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.json.tmp')
        with temp.open('w', encoding='utf-8') as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, self.path)

    def capture(self, sources, modules):
        old = {row['key']: row for row in self.data['activities']}
        rows = []
        for source in sources:
            row = {**old.get(source['key'], {}), **source}
            matches = [m for m in modules if normalized(m['title']) == normalized(row['section']) and normalized(row['section'])]
            row['module_id'] = str(matches[0]['id']) if len(matches) == 1 else ''
            row['module_title'] = matches[0]['title'] if len(matches) == 1 else ''
            rows.append(row)
        self.data['activities'] = rows
        self.save()

    def folder(self, row):
        if not str(row.get('key', '')).isdigit():
            raise ValueError('Invalid SCORM activity ID.')
        return self.path.parent / self.data['id'] / row['key']

    def cached(self, row):
        if row.get('download_valid') is False or not row.get('filename') or not row.get('sha256'):
            return None
        file = self.folder(row) / safe_filename(row['filename'])
        try:
            return file if validate_package(file, full=False)['sha256'] == row['sha256'] else None
        except ValueError:
            return None


async def download_package(page, run, row, *, refresh=False):
    cached = run.cached(row)
    if cached and not refresh:
        return cached, True
    href = urlsplit(row['href'])
    if href.hostname != 'mymoodle.okanagan.bc.ca' or href.path != '/mod/scorm/view.php' or parse_qs(href.query).get('id') != [row['key']]:
        raise ValueError('Invalid Moodle SCORM activity URL.')
    await page.goto(row['href'], wait_until='domcontentloaded')
    settings = page.locator('a[href*="modedit.php?update="]').first
    try:
        await settings.wait_for(state='visible', timeout=15000)
    except Exception as exc:
        raise RuntimeError('SCORM Settings is unavailable. Use a Moodle role that can edit this activity.') from exc
    await settings.click()
    area = page.locator('#fitem_id_packagefile')
    await area.wait_for(state='attached', timeout=15000)
    await area.scroll_into_view_if_needed()
    files = page.locator('#fitem_id_packagefile .fp-file')
    try:
        await files.first.wait_for(state='visible', timeout=30000)
    except Exception as exc:
        raise RuntimeError('No stored ZIP is available in Package file. This may be an external package or a permissions restriction.') from exc
    if await files.count() != 1:
        raise RuntimeError('Expected one original SCORM ZIP in Package file.')
    await files.first.click()
    async with page.expect_download(timeout=120000) as pending:
        await page.locator('button.fp-file-download:visible').click()
    download = await pending.value
    filename = safe_filename(download.suggested_filename)
    folder = run.folder(row)
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / 'download.part'
    try:
        await download.save_as(temp)
        details = validate_package(temp)
        target = folder / filename
        os.replace(temp, target)
        row.update(details, filename=filename, download_valid=True)
        run.save()
        return target, False
    finally:
        temp.unlink(missing_ok=True)
