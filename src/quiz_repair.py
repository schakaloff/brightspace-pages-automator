"""Offline, conservative quiz repairs; originals and course delivery settings stay intact.

Uses CS_Fixer's source-to-package repair approach with XML-aware answer handling.
The scoring shapes match native Brightspace exports (limited selections and matching).
"""
import base64
from collections import Counter, defaultdict
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from bs4 import BeautifulSoup
from PIL import Image

NS = {'d': 'http://desire2learn.com/xsd/d2lcp_v2p0'}
ITEM = re.compile(r'<item\b[^>]*>.*?</item>', re.S)
MAX_XML = 64 * 1024 * 1024
MAX_PACKAGE = 4 * 1024 ** 3


def _xml(data):
    if len(data) > MAX_XML or re.search(br'<!\s*(DOCTYPE|ENTITY)\b', data, re.I):
        raise ValueError('This XML is too large or contains unsupported entity declarations.')
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError('An export contains malformed XML. Export it again before repairing.') from exc


def _item(text):
    return _xml(('<root xmlns:d2l_2p0="' + NS['d'] + '">' + text + '</root>').encode())[0]


def _text(value):
    text = ' '.join(BeautifulSoup(value or '', 'html.parser').get_text(' ').split())
    return unicodedata.normalize('NFKC', text).translate(str.maketrans({'’': "'", '‘': "'", '“': '"', '”': '"'}))


def _meta(item):
    return {f.findtext('fieldlabel'): f.findtext('fieldentry') for f in item.findall('.//qti_metadatafield')}


def _material(node):
    return '\n'.join(x.text or '' for x in node.findall('.//mattext')) if node is not None else ''


def _prompt(item):
    return '\n'.join(x.text or '' for x in item.findall('./presentation/flow/material/mattext'))


def _options(item):
    return [(x.get('ident'), _text(_material(x))) for x in item.findall('./presentation/flow/response_lid/render_choice//response_label')]


def _visible_content(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    text = ''.join(c for c in soup.get_text() if not unicodedata.category(c).startswith('C'))
    return bool(text.strip() or any(img.get('src', '').strip() for img in soup.find_all('img'))
                or soup.find(['math', 'svg', 'audio', 'video']))


def _source_kind(question):
    kind = question.findtext('qtype')
    if kind == 'multichoice':
        return 'Multiple Choice' if question.findtext('.//single') == '1' else 'Multi-Select'
    return {'truefalse': 'True/False', 'match': 'Matching'}.get(kind, kind)


def _signature(kind, prompt, answers):
    return kind, _text(prompt), tuple(sorted(answers))


def _safe_name(name):
    path = unquote(name).replace('\\', '/')
    if not path or path.startswith('/') or ':' in path or '..' in PurePosixPath(path).parts:
        raise ValueError('The package contains an unsafe file path.')
    return path


def _condition(response, answer, outcome):
    condition = ET.Element('respcondition', title='Response Condition', **{'continue': 'yes'})
    ET.SubElement(ET.SubElement(condition, 'conditionvar'), 'varequal', respident=response).text = answer
    ET.SubElement(condition, 'setvar', varname=outcome, action='Add').text = '1'
    return condition


def _processing(conditions, matching=False):
    root = ET.Element('resprocessing')
    outcomes = ET.SubElement(root, 'outcomes')
    for name in ('que_score', 'D2L_Correct', 'D2L_Incorrect'):
        attrs = dict(vartype='Integer', defaultval='0', varname=name, minvalue='0')
        if name == 'que_score':
            attrs['maxvalue'] = '100'
        ET.SubElement(outcomes, 'decvar', **attrs)
    root.extend(conditions)
    final = ET.SubElement(root, 'respcondition')
    if matching:
        ET.SubElement(ET.SubElement(final, 'conditionvar'), 'other')
    ET.SubElement(final, 'setvar', varname='que_score', action='Set').text = 'D2L_Correct'
    return ET.tostring(root, encoding='unicode')


def _correct_ids(item):
    kind = _meta(item).get('qmd_questiontype')
    conditions = item.findall('resprocessing/respcondition')
    if kind == 'Multi-Select':
        raw = next((c for c in conditions if c.get('title') == 'Scoring for the correct answers'), None)
        if raw is not None:
            return {x.text for x in raw.findall('conditionvar/varequal')}
        return {c.findtext('conditionvar/varequal') for c in conditions
                if any(s.get('varname') == 'D2L_Correct' and s.text == '1' for s in c.findall('setvar'))}
    return {c.findtext('conditionvar/varequal') for c in conditions
            if any(s.get('action') == 'Set' and float(s.text or 0) > 0 for s in c.findall('setvar'))}


def _repair_item(text, source_index, changes, filename):
    item = _item(text)
    kind = _meta(item).get('qmd_questiontype')
    if kind not in ('Multiple Choice', 'True/False', 'Multi-Select', 'Matching'):
        raise ValueError(f'Question type {kind!r} needs manual review; no output was created.')
    options = _options(item)
    if kind in ('Multiple Choice', 'True/False', 'Multi-Select'):
        blank = [i for i, option in enumerate(item.findall('./presentation/flow/response_lid/render_choice//response_label'), 1)
                 if not _visible_content(_material(option))]
        if blank:
            raise ValueError(f'{filename}: {item.get("title", "Untitled")} has blank answer choices '
                             f'{", ".join(map(str, blank))}. No ZIP was saved. Review the source version; '
                             'blank choices will not be deleted or invented automatically.')
    candidates = source_index.get(_signature(kind, _prompt(item), [a for _, a in options]), [])
    if not candidates:
        raise ValueError(f'Cannot match {item.get("title", "a question")} to this Moodle backup. No output was created.')
    if kind != 'Matching':
        expected = {tuple(sorted((_text(a.findtext('answertext')), float(a.findtext('fraction') or 0))
                                 for a in q.findall('.//answer'))) for q in candidates}
        if len(expected) != 1:
            raise ValueError('Matching source revisions disagree on scoring. Review them before repairing.')
        answers = next(iter(expected))
        intended = {a for a, value in answers if value > 0}
        actual_ids = _correct_ids(item)
        if not actual_ids <= {cid for cid, _ in options} or intended != {a for cid, a in options if cid in actual_ids}:
            raise ValueError(f'Correct answers differ for {item.get("title")}. Manual review is required.')
        if kind != 'Multi-Select':
            weights = {c.findtext('conditionvar/varequal'): float(c.findtext('setvar') or 0) / 100
                       for c in item.findall('resprocessing/respcondition')}
            if sorted((a, weights.get(cid)) for cid, a in options) != list(answers):
                raise ValueError(f'Answer weights differ for {item.get("title")}. Manual review is required.')
            return text
        if not actual_ids or len({cid for cid, _ in options}) != len(options):
            raise ValueError('A multi-select question has missing or duplicate answer identifiers.')
        conditions = [_condition(item.find('.//response_lid').get('ident'), cid,
                                 'D2L_Correct' if cid in actual_ids else 'D2L_Incorrect') for cid, _ in options]
        value, action = '3', 'Multi-select: Correct Answers, Limited Selections'
        processing = _processing(conditions)
    else:
        pair_sets = {tuple(sorted((_text(m.findtext('questiontext')), _text(m.findtext('answertext')))
                                 for m in q.findall('.//matches/match'))) for q in candidates}
        if len(pair_sets) != 1:
            raise ValueError('Source matching-question revisions disagree; review them before repairing.')
        pairs = dict(next(iter(pair_sets)))
        rows = item.findall('.//response_grp')
        if '' in pairs or len(rows) != len(pairs):
            raise ValueError('This matching layout needs manual review; no rows were removed.')
        conditions = []
        seen = set()
        for row in rows:
            stem = _text(_material(row.find('material')))
            choices = [(x.get('ident'), _text(_material(x))) for x in row.findall('.//response_label')]
            if {answer for _, answer in choices} != set(pairs.values()):
                raise ValueError('A matching choice pool differs from Moodle; manual review is required.')
            if stem not in pairs or stem in seen or sum(a == pairs[stem] for _, a in choices) != 1:
                raise ValueError('A matching row cannot be paired unambiguously with Moodle.')
            seen.add(stem)
            conditions.extend(_condition(row.get('respident'), cid, 'D2L_Correct' if a == pairs[stem] else 'D2L_Incorrect')
                              for cid, a in choices)
        value, action = '0', 'Matching: equal weighting and Moodle answer pairs'
        processing = _processing(conditions, matching=True)
    fixed, count = re.subn(r'<resprocessing>.*?</resprocessing>', lambda _: processing, text, flags=re.S)
    if count != 1:
        raise ValueError('A question has an unsupported scoring layout.')
    fixed, count = re.subn(r'(<d2l_2p0:grading_type>)\d+(</d2l_2p0:grading_type>)', rf'\g<1>{value}\g<2>', fixed)
    if count != 1:
        raise ValueError('A question has an unsupported grading field.')
    after = _item(fixed)
    for version in (item, after):
        version.remove(version.find('resprocessing'))
        version.find('.//d:grading_type', NS).text = '[grading]'
    if ET.tostring(item) != ET.tostring(after):
        raise ValueError('Repair validation failed: question content or points changed.')
    if fixed != text:
        changes.append({'file': filename, 'question': item.get('title'), 'id': item.get('label'), 'repair': action})
    if kind == 'Multi-Select' and _correct_ids(_item(fixed)) != actual_ids:
        raise ValueError('Repair validation failed: the correct-answer set changed.')
    return fixed


def _load_source(archive):
    def read(name):
        try:
            member = archive.getmember(name)
            if not member.isfile() or member.size > MAX_XML:
                raise ValueError('A required Moodle XML file is too large or invalid.')
            return archive.extractfile(member).read()
        except KeyError as exc:
            raise ValueError(f'This Moodle backup is missing {name}.') from exc
    questions = _xml(read('questions.xml'))
    source_index, embedded = defaultdict(list), defaultdict(dict)
    for q in questions.findall('.//question'):
        if q.findtext('qtype') != 'description':
            source_index[_signature(_source_kind(q), q.findtext('questiontext'),
                                    [_text(a.findtext('answertext')) for a in q.findall('.//answer')])].append(q)
        for image in BeautifulSoup(q.findtext('questiontext') or '', 'html.parser').find_all('img'):
            src = image.get('src', '')
            if src.startswith('data:image/') and ';base64,' in src:
                try:
                    blob = base64.b64decode(src.split(',', 1)[1], validate=True)
                except ValueError as exc:
                    raise ValueError('A Moodle embedded image is corrupt.') from exc
                if not blob or len(blob) > MAX_XML:
                    raise ValueError('A Moodle embedded image is empty or too large.')
                digest = hashlib.sha256(blob).hexdigest()
                embedded[_text(q.findtext('name'))][digest] = blob
    files = defaultdict(dict)
    for record in _xml(read('files.xml')).findall('file'):
        digest, name = record.findtext('contenthash'), record.findtext('filename')
        if name and name != '.' and re.fullmatch('[0-9a-f]{40}', digest or ''):
            files[name.casefold()][digest] = f'files/{digest[:2]}/{digest}'
    return source_index, embedded, files


def _restore_images(texts, archive, embedded, files, available, changes):
    added, remote = {}, set()
    for name, data in texts.items():
        root = _xml(data)
        parents = {c: p for p in root.iter() for c in p}
        for node in root.iter('mattext'):
            images = BeautifulSoup(node.text or '', 'html.parser').find_all('img')
            if not images:
                continue
            owner = node
            while owner in parents and owner.tag not in ('item', 'section'):
                owner = parents[owner]
            titles, current = [], owner
            while current is not None:
                if current.get('title'):
                    titles.append(_text(current.get('title')))
                current = parents.get(current)
            if owner.tag == 'item' and owner in parents:
                siblings = list(parents[owner])
                for sibling in reversed(siblings[:siblings.index(owner)]):
                    if sibling.tag in ('item', 'itemref'):
                        break
                    if sibling.tag == 'section' and sibling.get('title'):
                        titles.insert(0, _text(sibling.get('title')))
                        break
            for image in images:
                src = image.get('src', '').strip()
                parsed = urlsplit(src)
                if parsed.scheme or parsed.netloc:
                    if parsed.scheme != 'data':
                        remote.add(src)
                    continue
                path = _safe_name(parsed.path.lstrip('/'))
                if path.casefold() in available and path not in added:
                    continue
                candidates = {}
                for title in titles:
                    if title in embedded:
                        candidates = embedded[title]
                        break
                blob = None
                if len(candidates) == 1:
                    blob = next(iter(candidates.values()))
                    extension = Path(path).suffix.lower()
                    expected = b'\x89PNG\r\n\x1a\n' if extension == '.png' else b'\xff\xd8\xff' if extension in ('.jpg', '.jpeg') else None
                    if expected is None or not blob.startswith(expected):
                        raise ValueError(f'The Moodle image does not match the file type for {path}.')
                elif not candidates:
                    records = files.get(PurePosixPath(path).name.casefold(), {})
                    if len(records) == 1:
                        member = archive.getmember(next(iter(records.values())))
                        if member.isfile() and member.size <= MAX_XML:
                            blob = archive.extractfile(member).read()
                            if hashlib.sha1(blob).hexdigest() != next(iter(records)):
                                raise ValueError('A Moodle image failed its content-hash check.')
                if blob is None:
                    raise ValueError(f'Cannot safely restore {path}. The source image is missing or ambiguous; no output was created.')
                try:
                    with Image.open(io.BytesIO(blob)) as restored_image:
                        restored_image.verify()
                except (OSError, ValueError) as exc:
                    raise ValueError(f'The source image for {path} failed its integrity check.') from exc
                if path in added:
                    if added[path] != blob:
                        raise ValueError(f'Two source images conflict at {path}; no output was created.')
                    continue
                added[path] = blob
                available.add(path.casefold())
                changes.append({'file': name, 'asset': path, 'repair': 'Restored image from Moodle'})
    return added, remote


def repair_package(moodle_path, package_path, output_path, progress=lambda message: None):
    """Return a change summary only after re-opening and validating a new ZIP."""
    moodle, package, output = map(lambda p: Path(p).resolve(), (moodle_path, package_path, output_path))
    if output in (moodle, package) or output.exists():
        raise ValueError('Choose a new output filename. Original files and existing exports are never overwritten.')
    if output.suffix.lower() != '.zip':
        raise ValueError('The corrected package must use a .zip filename.')
    progress('Reading Moodle questions and images…')
    changes = []
    with tarfile.open(moodle, 'r:*') as archive, zipfile.ZipFile(package) as z:
        source_index, embedded, files = _load_source(archive)
        names = z.namelist()
        normal = [_safe_name(n).casefold() for n in names]
        if len(set(normal)) != len(normal) or sum(i.file_size for i in z.infolist()) > MAX_PACKAGE:
            raise ValueError('This package has duplicate paths or exceeds the 4 GB unpacked size limit.')
        question_files = [n for n in names if n == 'questiondb.xml' or re.fullmatch(r'quiz_d2l_\d+\.xml', n)]
        if 'imsmanifest.xml' not in names or not question_files:
            raise ValueError('Choose a Brightspace course export containing quizzes or a question library.')
        if z.getinfo('imsmanifest.xml').file_size > MAX_XML:
            raise ValueError('The package manifest exceeds the XML size limit.')
        manifest = z.read('imsmanifest.xml')
        _xml(manifest)
        original = {}
        for name in question_files:
            if z.getinfo(name).file_size > MAX_XML:
                raise ValueError('A quiz XML file exceeds the 64 MB limit.')
            original[name] = z.read(name)
        texts = {}
        progress('Repairing multi-select and matching scoring…')
        for name, data in original.items():
            _xml(data)
            fixed = ITEM.sub(lambda match: _repair_item(match.group(), source_index, changes, name), data.decode('utf-8-sig'))
            texts[name] = (b'\xef\xbb\xbf' if data.startswith(b'\xef\xbb\xbf') else b'') + fixed.encode('utf-8')
        progress('Restoring missing question and section images…')
        assets, remote = _restore_images(texts, archive, embedded, files, set(normal), changes)
        if sum(i.file_size for i in z.infolist()) + sum(map(len, assets.values())) > MAX_PACKAGE:
            raise ValueError('Restored assets exceed the package size limit.')
        progress('Writing and checking the corrected package…')
        handle, temporary = tempfile.mkstemp(prefix='.quiz-repair-', suffix='.zip', dir=output.parent)
        os.close(handle)
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as out:
                out.comment = z.comment
                for info in z.infolist():
                    if info.filename in texts:
                        out.writestr(info, texts[info.filename])
                    else:
                        with z.open(info) as source, out.open(info, 'w') as target:
                            shutil.copyfileobj(source, target, length=1024 * 1024)
                for name, blob in assets.items():
                    out.writestr(name, blob)
            with zipfile.ZipFile(temporary) as check:
                if check.testzip() is not None:
                    raise ValueError('The corrected ZIP failed its integrity check.')
                bank = {i.get('label') for i in _xml(check.read('questiondb.xml')).findall('.//item')} if 'questiondb.xml' in names else set()
                for name in question_files:
                    root = _xml(check.read(name))
                    if len(root.findall('.//item')) != len(_xml(original[name]).findall('.//item')):
                        raise ValueError('Validation failed: the question count changed.')
                    for ref in root.findall('.//itemref'):
                        if ref.get('linkrefid') not in bank:
                            raise ValueError('A quiz references a missing library question.')
                if check.read('imsmanifest.xml') != manifest:
                    raise ValueError('Validation failed: the package manifest changed.')
            # Exclusive creation prevents overwriting a file created during the repair.
            created = False
            try:
                with output.open('xb') as target, open(temporary, 'rb') as source:
                    created = True
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            except Exception:
                if created:
                    output.unlink(missing_ok=True)
                raise
        finally:
            Path(temporary).unlink(missing_ok=True)
    counts = Counter(c['repair'] for c in changes)
    warnings = ['Dates, instructions, visibility, pagination, and randomization were preserved. Review these before use.']
    if remote:
        warnings.append(f'{len(remote)} external image URL(s) remain; their availability was not checked.')
    return {'output': str(output), 'counts': dict(counts), 'changes': changes,
            'warnings': warnings, 'quizzes': sum(n.startswith('quiz_d2l_') for n in question_files),
            'images_restored': len(assets), 'local_checks_passed': True, 'brightspace_tested': False}
