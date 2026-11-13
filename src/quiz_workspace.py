"""Local quiz review drafts and conservative, explicitly reviewed package edits."""
from collections import Counter
from copy import deepcopy
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import tarfile
from urllib.parse import urlsplit, unquote
import uuid
import xml.etree.ElementTree as ET
import zipfile

from bs4 import BeautifulSoup
from PIL import Image
from quiz_review import (_brightspace, inspect_exports, _inline_images, _preview, _layout_flags)
from quiz_repair import (NS, MAX_XML, MAX_PACKAGE, _xml, _text, _visible_content,
                         _meta, _condition, _processing, _load_source, _restore_images)

ET.register_namespace('d2l_2p0', NS['d'])


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def image_paths(value):
    paths, external = set(), set()
    for img in BeautifulSoup(value or '', 'html.parser').find_all('img'):
        parsed = urlsplit(img.get('src', '').replace('\\', '/'))
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != 'data':
                external.add(img.get('src'))
        else:
            path = unquote(parsed.path).lstrip('/')
            if '..' in Path(path).parts or ':' in path:
                raise ValueError('An image has an unsafe file path.')
            if path:
                paths.add(path)
    return paths, external


def validate_record(record):
    if record['kind'] not in ('Multiple Choice', 'True/False', 'Multi-Select', 'Matching'):
        raise ValueError('This question type requires a separate review before export.')
    points = record.get('points')
    if not isinstance(points, (int, float)) or isinstance(points, bool) or not math.isfinite(points) or not 0 <= points <= 1_000_000:
        raise ValueError('Points must be a finite number from 0 to 1,000,000.')
    if not _visible_content(record.get('raw_prompt', '')):
        raise ValueError('The question prompt is blank.')
    choices = record['choices']
    if record['kind'] != 'Matching':
        if len(choices) < 2 or len(choices) > 100:
            raise ValueError('Keep between 2 and 100 answer choices.')
        if any(not _visible_content(a.get('raw_html', a.get('html', ''))) for a in choices):
            raise ValueError('Blank answer choices must be corrected before export.')
        correct = sum(a.get('correct') is True for a in choices)
        if not correct or (record['kind'] in ('Multiple Choice', 'True/False') and correct != 1):
            raise ValueError('Choose one correct answer, or at least one for multi-select.')
        if record['kind'] == 'True/False' and len(choices) != 2:
            raise ValueError('True/false questions must have exactly two answers.')
        if record['kind'] in ('Multiple Choice', 'True/False'):
            for answer in choices:
                credit = answer.get('credit')
                if not isinstance(credit, (int, float)) or not math.isfinite(credit) or not -1 <= credit <= 1 or (credit > 0) != answer['correct']:
                    raise ValueError('An answer score could not be verified. Check its scoring in Brightspace.')
    if record['kind'] == 'Matching' and (not record['pairs'] or any(not stem or len(answer) != 1 or not answer[0] for stem, answer in record['pairs'])):
        raise ValueError('Every matching row needs one verified answer.')


def content_key(record):
    return json.dumps([record['kind'], html.unescape(record['raw_prompt']), html.unescape(record.get('raw_context', '')),
                       [(html.unescape(a.get('raw_html', a['html'])), a['correct'], a.get('credit') if record['kind'] in ('Multiple Choice', 'True/False') else None) for a in record['choices']],
                       record['pairs']], sort_keys=True)


def meaning_key(record):
    """HTML wrappers from migration alone do not require a version decision."""
    return (record['kind'], _text(record['raw_prompt']),
            tuple(sorted((_text(a.get('raw_html', a['html'])), a['correct'], a.get('credit') if record['kind'] in ('Multiple Choice', 'True/False') else None) for a in record['choices'])),
            tuple(sorted((stem, tuple(answers)) for stem, answers in record['pairs'])),
            len(BeautifulSoup(record['raw_prompt'] + record.get('raw_context', ''), 'html.parser').find_all('img')))


class QuizWorkspace:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.paths = {}
        self.hashes = {}
        self.rows = {}
        self.edits = {}
        self.assets = {}
        self.draft_path = None
        self._check_cache = {}
        self.bank = []
        self.global_errors = []
        self.image_note = ''

    def load(self, candidate, reference='', pictures=''):
        paths = {'candidate': str(Path(candidate).resolve())}
        for name, value in [('reference', reference), ('pictures', pictures)]:
            if value:
                paths[name] = str(Path(value).resolve())
        for value in paths.values():
            if not Path(value).is_file():
                raise ValueError('An export could not be found. Choose the local file again.')
        target = _brightspace(paths['candidate'])
        if not target['quizzes']:
            raise ValueError('The Brightspace ZIP must contain at least one quiz.')
        comparison = inspect_exports(paths.get('reference', paths['candidate']), paths['candidate'])
        rows = {}
        by_global = {}
        for item in target['bank']:
            if item.get('global_id'):
                by_global.setdefault(item['global_id'], []).append(item)
        for qi, quiz in enumerate(comparison['quizzes']):
            for row in quiz['questions']:
                key = f'{qi}:{row["number"]}'
                current = deepcopy(row['candidate'])
                variants = [{'id': 'current', 'label': 'Brightspace question', 'record': current}]
                candidates = [('reference', 'Original ' + comparison['reference_format'] + ' question', row['reference'])] if 'reference' in paths else []
                candidates += [('library:' + r['id'], 'Stored library copy · ' + r['id'], r)
                               for r in by_global.get(current.get('global_id'), [])]
                seen = {content_key(current)}
                for ident, label, record in candidates:
                    if ident.startswith('library:') and record:
                        same_context = deepcopy(record)
                        same_context['raw_context'] = current.get('raw_context', '')
                        if content_key(same_context) in seen:
                            continue
                    if record and record['kind'] == current['kind'] and content_key(record) not in seen:
                        copy = deepcopy(record)
                        if ident.startswith('library:'):
                            count = len(record['choices']) or len(record['pairs'])
                            label = f'Another saved question · {count} ' + ('answers' if record['choices'] else 'matching pairs')
                            context = current.get('raw_context', '')
                            pictures = image_paths(context)[0]
                            moved_picture = pictures and pictures <= image_paths(record['raw_prompt'])[0] and not _text(context)
                            copy['raw_context'] = '' if moved_picture else context
                        copy['points'] = current['points']
                        copy.setdefault('raw_context', copy.get('context', ''))
                        variants.append({'id': ident, 'label': label, 'record': copy})
                        seen.add(content_key(record))
                findings = [f for f in row['findings'] if not f['code'].startswith('reference_')]
                if len(variants) > 1:
                    findings.insert(0, {'code': 'versions', 'severity': 'review', 'message': 'Different stored versions exist. Compare them and choose the copy to keep.'})
                rows[key] = {'id': key, 'quiz': quiz['title'], 'number': row['number'],
                             'title': row['title'], 'original': current, 'variants': variants, 'findings': findings,
                             'reference_kind': row['reference']['kind'] if 'reference' in paths and row['reference'] else current['kind'],
                             'decision_required': any(v['id'].startswith('library:') or meaning_key(v['record']) != meaning_key(current)
                                                      for v in variants[1:]) or any(f['severity'] == 'block' and f['code'] in
                                                      ('question_identity', 'answer_options', 'answer_key', 'answer_credit', 'matching_pairs', 'question_points', 'missing_reference')
                                                      for f in findings)}
        assets = {}
        wanted = set()
        for row in rows.values():
            for version in row['variants']:
                r = version['record']
                text = r['raw_prompt'] + r.get('raw_context', '') + ''.join(a.get('raw_html', a['html']) for a in r['choices'])
                wanted.update(image_paths(text)[0])
        for name in ('candidate', 'pictures', 'reference'):
            if name not in paths or not zipfile.is_zipfile(paths[name]):
                continue
            with zipfile.ZipFile(paths[name]) as archive:
                names = {n.replace('\\', '/'): n for n in archive.namelist()}
                if len(names) != len(archive.namelist()) or sum(i.file_size for i in archive.infolist()) > MAX_PACKAGE:
                    raise ValueError('The picture package has duplicate paths or exceeds the size limit.')
                for path in wanted & names.keys():
                    info = archive.getinfo(names[path])
                    if info.file_size > MAX_XML:
                        raise ValueError('An image exceeds the size limit.')
                    blob = archive.read(names[path])
                    try:
                        with Image.open(io.BytesIO(blob)) as image:
                            image.verify()
                    except (OSError, ValueError) as exc:
                        raise ValueError(f'The picture {path} could not be verified.') from exc
                    if path in assets and assets[path] != blob:
                        raise ValueError(f'Picture files disagree at {path}. Choose a single trusted picture package.')
                    assets[path] = blob
        image_note = ''
        if 'reference' in paths and not zipfile.is_zipfile(paths['reference']):
            try:
                with tarfile.open(paths['reference'], 'r:*') as moodle, zipfile.ZipFile(paths['candidate']) as package:
                    _, embedded, files = _load_source(moodle)
                    available = {n.replace('\\', '/').casefold() for n in package.namelist()} | {n.casefold() for n in assets}
                    restored = {}
                    # Active quiz sections identify the pictures before the library reuses them.
                    for names in ([n for n in package.namelist() if re.fullmatch(r'quiz_d2l_\d+\.xml', n)], ['questiondb.xml']):
                        texts = {n: package.read(n) for n in names if n in package.namelist()}
                        added, _ = _restore_images(texts, moodle, embedded, files, available, [])
                        restored.update(added)
                        available.update(n.casefold() for n in added)
                    assets.update(restored)
                    if restored:
                        image_note = f'{len(restored)} pictures recovered from the original Moodle course.'
            except (ValueError, KeyError, OSError, tarfile.TarError):
                image_note = 'Some pictures could not be recovered safely from Moodle. Add a ZIP with the missing pictures.'
        self.paths, self.rows, self.assets = paths, rows, assets
        self.image_note = image_note
        self.bank = target['bank']
        self.global_errors = [f['message'] for f in comparison['findings']
                              if f['severity'] == 'block' and f['code'] != 'library_blank_answer']
        self.global_errors += [f['message'] for q in comparison['quizzes'] for f in q['findings'] if f['severity'] == 'block']
        active_ids = {row['original']['id'] for row in rows.values()}
        self.global_errors += [f'Unused library question {q["title"]}: {finding["message"]} Correct it in Brightspace and export again.'
                               for q in target['bank'] if q['id'] not in active_ids
                               for finding in q['findings'] if finding['code'] == 'blank_answer']
        self.hashes = {name: digest(path) for name, path in paths.items()}
        self.edits = {}
        self._check_cache = {}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.draft_path = self.data_dir / (uuid.uuid4().hex + '.quiz-review.json')
        self.save()

    def save(self):
        data = {'schema': 1, 'paths': self.paths, 'hashes': self.hashes, 'edits': self.edits}
        temp = self.draft_path.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, self.draft_path)
        finally:
            temp.unlink(missing_ok=True)

    def restore(self, path):
        path = Path(path).resolve()
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('The saved review is too large.')
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema') != 1 or not isinstance(data.get('paths'), dict) or not isinstance(data.get('edits'), dict):
            raise ValueError('This is not a saved quiz review.')
        for name, value in data['paths'].items():
            if digest(value) != data['hashes'].get(name):
                raise ValueError('An export changed since this review was saved. Start a new review.')
        fresh = QuizWorkspace(self.data_dir)
        fresh.load(**data['paths'])
        for key, edit in data['edits'].items():
            fresh.edit(key, edit)
        fresh.draft_path.unlink(missing_ok=True)
        fresh.draft_path = path
        self.__dict__.update(fresh.__dict__)

    def chosen(self, key):
        return self.edits.get(key, {}).get('record', self.rows[key]['original'])

    def edit(self, key, body):
        if key not in self.rows:
            raise ValueError('Choose a question from this review.')
        row = self.rows[key]
        version = next((v for v in row['variants'] if v['id'] == body.get('variant', 'current')), None)
        if version is None:
            raise ValueError('That question version is no longer available.')
        record = deepcopy(version['record'])
        supplied = body.get('record')
        if supplied is not None:
            if not isinstance(supplied, dict):
                raise ValueError('Send a question edit.')
            for field in ('raw_prompt', 'raw_context'):
                value = supplied.get(field, record.get(field, ''))
                if not isinstance(value, str) or len(value) > 1_000_000:
                    raise ValueError('Question HTML is invalid or too large.')
                record[field] = value
            record['points'] = supplied.get('points', record['points'])
            if record['kind'] != 'Matching':
                choices = supplied.get('choices', record['choices'])
                if not isinstance(choices, list) or not 2 <= len(choices) <= 100:
                    raise ValueError('Keep between 2 and 100 answers.')
                previous_choices = record['choices']
                record['choices'] = []
                for index, choice in enumerate(choices):
                    if not isinstance(choice, dict):
                        raise ValueError('An answer edit is invalid.')
                    value = choice.get('raw_html', choice.get('html', ''))
                    if not isinstance(value, str) or len(value) > 1_000_000 or type(choice.get('correct')) is not bool:
                        raise ValueError('An answer edit is invalid.')
                    previous = previous_choices[index] if index < len(previous_choices) else {}
                    credit = previous.get('credit') if previous.get('correct') is choice['correct'] else None
                    if credit is None:
                        credit = 1.0 if choice['correct'] else 0.0
                    record['choices'].append({'raw_html': value, 'html': value, 'text': _text(value), 'correct': choice['correct'], 'credit': credit})
        validate_record(record)
        previous = self.edits.get(key)
        self.edits[key] = {'variant': version['id'], 'record': record, 'reviewed': body.get('reviewed') is True}
        self._check_cache.pop(key, None)
        try:
            self.save()
        except Exception:
            if previous is None:
                self.edits.pop(key, None)
            else:
                self.edits[key] = previous
            raise

    def reset(self, key):
        if key not in self.rows:
            raise ValueError('Choose a question.')
        previous = self.edits.pop(key, None)
        self._check_cache.pop(key, None)
        try:
            self.save()
        except Exception:
            if previous is not None:
                self.edits[key] = previous
            raise

    def checks(self, key):
        if key in self._check_cache:
            return self._check_cache[key]
        record = self.chosen(key)
        errors, warnings = [], []
        try:
            validate_record(record)
        except ValueError as exc:
            errors.append(str(exc))
        paths, external = image_paths(record['raw_prompt'] + record.get('raw_context', '') + ''.join(a.get('raw_html', a['html']) for a in record['choices']))
        missing = paths - self.assets.keys()
        if missing:
            errors.append(f'{len(missing)} picture file(s) are missing. Add the original course or a ZIP under Extra pictures.')
        if external:
            warnings.append('External pictures remain; their availability was not checked.')
        if _layout_flags(record['raw_prompt']):
            warnings.append('Paragraphs or line breaks may add space. Check the preview.')
        for index, answer in enumerate(record['choices'], 1):
            if _layout_flags(answer.get('raw_html', answer['html'])):
                warnings.append(f'Answer {index} contains paragraph spacing or line breaks. Check the preview.')
        if self.rows[key]['decision_required'] and not self.edits.get(key, {}).get('reviewed'):
            errors.append('Choose which question to keep. Use a question on the left, or keep the one on the right.')
        if self.rows[key]['reference_kind'] != record['kind']:
            errors.append('The question type disagrees with the reference. This workspace cannot change question types; choose a matching export or correct the type in Brightspace.')
        for finding in self.rows[key]['findings']:
            if finding['code'] in ('question_identity', 'answer_options', 'answer_key', 'answer_credit', 'matching_pairs', 'question_points', 'missing_reference'):
                if not self.edits.get(key, {}).get('reviewed'):
                    errors.append(finding['message'] + ' Review the question before exporting.')
            elif finding['code'] in ('html_difference', 'grading_rule', 'image_position', 'source_scope'):
                warnings.append('Original comparison: ' + finding['message'])
        self._check_cache[key] = errors, warnings
        return errors, warnings

    def snapshot(self):
        rows = []
        for key, row in self.rows.items():
            errors, warnings = self.checks(key)
            rows.append({k: row[k] for k in ('id', 'quiz', 'number', 'title')} | {
                'errors': errors, 'warnings': warnings, 'versions': len(row['variants']),
                'edited': key in self.edits, 'reviewed': self.edits.get(key, {}).get('reviewed', False),
                'needs_decision': row['decision_required'] and not self.edits.get(key, {}).get('reviewed', False)})
        return {'paths': self.paths, 'draft': str(self.draft_path or ''), 'rows': rows,
                'blocked': sum(bool(r['errors']) for r in rows), 'edited': len(self.edits), 'global_errors': self.global_errors,
                'image_note': self.image_note, 'decisions_left': sum(r['needs_decision'] for r in rows)}

    def decorate(self, record):
        record = deepcopy(record)
        record['prompt'] = _inline_images(record['raw_prompt'], self.assets)
        record['context'] = _inline_images(record.get('raw_context', ''), self.assets)
        for answer in record['choices']:
            answer['html'] = _inline_images(answer.get('raw_html', answer['html']), self.assets)
        return record

    def detail(self, key):
        row = self.rows[key]
        variants = []
        for version in row['variants']:
            problems = []
            try:
                validate_record(version['record'])
            except ValueError as exc:
                problems.append(str(exc))
            variants.append({**version, 'can_use': not problems, 'problems': problems})
        try:
            validate_record(self.chosen(key))
            can_keep = True
        except ValueError:
            can_keep = False
        return {k: row[k] for k in ('id', 'quiz', 'number', 'title', 'findings')} | {
            'variants': variants, 'selected': self.edits.get(key, {}).get('variant', 'current'),
            'record': self.chosen(key), 'reviewed': self.edits.get(key, {}).get('reviewed', False),
            'checks': self.checks(key), 'decision_required': row['decision_required'], 'can_keep': can_keep}

    def preview(self, key, variant=None):
        if variant is None:
            record = self.chosen(key)
        else:
            record = next(v['record'] for v in self.rows[key]['variants'] if v['id'] == variant)
        return BeautifulSoup(_preview(self.decorate(record)), 'html.parser').iframe['srcdoc'].encode('utf-8')

    def export(self, output, limited=True):
        if not self.rows:
            raise ValueError('Open a quiz export first.')
        if self.global_errors:
            raise ValueError(self.global_errors[0])
        for name, path in self.paths.items():
            if digest(path) != self.hashes[name]:
                raise ValueError('An input file changed. Start a new review before exporting.')
        blocked = [(r['quiz'], r['number'], self.checks(key)[0]) for key, r in self.rows.items() if self.checks(key)[0]]
        if blocked:
            raise ValueError(f'{len(blocked)} question(s) still need attention. {blocked[0][0]}, Q{blocked[0][1]}: {blocked[0][2][0]}')
        output = Path(output).resolve()
        if output.suffix.lower() != '.zip' or str(output) in self.paths.values() or output == self.draft_path or output.exists():
            raise ValueError('Choose a new .zip filename. Existing files are never overwritten.')
        with zipfile.ZipFile(self.paths['candidate']) as archive:
            names = archive.namelist()
            roots = {n: _xml(archive.read(n)) for n in names if n == 'questiondb.xml' or re.fullmatch(r'quiz_d2l_\d+\.xml', n)}
            original_counts = {name: (len(list(root.iter('item'))), len(list(root.iter('itemref')))) for name, root in roots.items()}
            bank = {i.get('label'): i for i in roots.get('questiondb.xml', ET.Element('empty')).iter('item')}
            parents = {child: parent for root in roots.values() for parent in root.iter() for child in parent}
            uses = Counter(n.get('linkrefid') for name, root in roots.items() if name != 'questiondb.xml' for n in root.iter('itemref'))
            locations = {}
            quiz_by_title = {root.find('assessment').get('title'): (name, root.find('assessment')) for name, root in roots.items() if name != 'questiondb.xml'}
            for title, (name, assessment) in quiz_by_title.items():
                pending, number = [], 0
                for node in assessment.iter():
                    if node.tag == 'section' and node.find('presentation_material') is not None:
                        pending.append(node.find('presentation_material'))
                    elif node.tag in ('item', 'itemref'):
                        number += 1
                        locations[title, number] = (name, node, list(pending))
                        pending = []
            changed = set()
            for key, edit in self.edits.items():
                record, row = edit['record'], self.rows[key]
                if content_key(record) == content_key(row['original']) and record['points'] == row['original']['points']:
                    continue
                name, placement, context = locations[row['quiz'], row['number']]
                if placement.tag == 'itemref':
                    if uses[placement.get('linkrefid')] != 1:
                        raise ValueError('This library question is shared by multiple quiz placements. Export a smaller selection to edit it independently.')
                    item = bank[placement.get('linkrefid')]
                    changed.add('questiondb.xml')
                    points = placement.find('d:points', NS)
                    if points is None:
                        points = ET.SubElement(placement, '{' + NS['d'] + '}points')
                    points.text = str(record['points'])
                else:
                    item = placement
                _apply_question(item, record)
                if record.get('raw_context', '') != row['original'].get('raw_context', ''):
                    if not context and record.get('raw_context'):
                        raise ValueError('Adding a new section block needs a separate review. Edit the prompt instead.')
                    for index, material in enumerate(context):
                        parent = parents[material]
                        if any(n.tag in ('item', 'itemref') for n in parent.iter()):
                            raise ValueError('These section instructions are shared by several questions and cannot be changed through a single question.')
                        if index == 0 and record.get('raw_context'):
                            _set_material(material, record['raw_context'])
                        else:
                            parent.remove(material)
                changed.add(name)
            if limited:
                for name, root in roots.items():
                    for item in root.iter('item'):
                        if _meta(item).get('qmd_questiontype') == 'Multi-Select':
                            from quiz_repair import _correct_ids
                            correct = _correct_ids(item)
                            options = item.findall('./presentation/flow/response_lid/render_choice//response_label')
                            if not correct or not correct <= {a.get('ident') for a in options}:
                                raise ValueError('A multi-select answer key could not be verified.')
                            if item.findtext('.//d:grading_type', namespaces=NS) != '3':
                                _set_scoring(item, correct)
                                changed.add(name)
            records = {r['id']: r for r in self.bank}
            for name, root in roots.items():
                for item in root.iter('item'):
                    if _meta(item).get('qmd_questiontype') == 'Matching' and item.findtext('.//d:grading_type', namespaces=NS) != '0':
                        record = records.get(item.get('label'))
                        if record is None:
                            record = next((r['original'] for r in self.rows.values() if r['original']['id'] == item.get('label')), None)
                        if record is None:
                            raise ValueError('A matching answer key could not be verified.')
                        _apply_question(item, record)
                        changed.add(name)
            # Unused bank versions are retained; blank options must still be resolved.
            for item in bank.values():
                if any(not _visible_content(''.join(m.text or '' for m in a.iter('mattext'))) for a in item.findall('./presentation/flow/response_lid/render_choice//response_label')):
                    raise ValueError('An unused library question still has blank choices. Review the library or export only the quizzes being corrected.')
            texts = {name: ET.tostring(roots[name], encoding='utf-8', xml_declaration=True) for name in changed}
            total = sum(i.file_size for i in archive.infolist()) + sum(len(v) for k, v in self.assets.items() if k not in names)
            if total > MAX_PACKAGE:
                raise ValueError('The output would exceed the package size limit.')
            fd, temporary = tempfile.mkstemp(suffix='.zip', dir=output.parent)
            os.close(fd)
            try:
                with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as target:
                    target.comment = archive.comment
                    for info in archive.infolist():
                        if info.filename in texts:
                            target.writestr(info, texts[info.filename])
                        else:
                            with archive.open(info) as source, target.open(info, 'w') as destination:
                                shutil.copyfileobj(source, destination, 1024 * 1024)
                    for name, blob in self.assets.items():
                        if name not in names:
                            target.writestr(name, blob)
                with zipfile.ZipFile(temporary) as check:
                    if check.testzip() is not None:
                        raise ValueError('The new ZIP failed its integrity check.')
                    for name in roots:
                        after = _xml(check.read(name))
                        if (len(list(after.iter('item'))), len(list(after.iter('itemref')))) != original_counts[name]:
                            raise ValueError('The quiz question count changed unexpectedly.')
                    if check.read('imsmanifest.xml') != archive.read('imsmanifest.xml'):
                        raise ValueError('The package manifest changed unexpectedly.')
                created = False
                try:
                    with output.open('xb') as target, open(temporary, 'rb') as source:
                        created = True
                        shutil.copyfileobj(source, target, 1024 * 1024)
                except Exception:
                    if created:
                        output.unlink(missing_ok=True)
                    raise
            finally:
                Path(temporary).unlink(missing_ok=True)
        return str(output)


def _set_material(parent, value):
    nodes = list(parent.iter('mattext'))
    if not nodes:
        material = ET.SubElement(parent, 'material')
        nodes = [ET.SubElement(material, 'mattext', texttype='text/html')]
    nodes[0].text = value
    for node in nodes[1:]:
        node.text = ''


def _set_scoring(item, correct, credits=None):
    response = item.find('./presentation/flow/response_lid')
    options = response.findall('./render_choice//response_label')
    kind = _meta(item)['qmd_questiontype']
    if kind == 'Multi-Select':
        conditions = [_condition(response.get('ident'), a.get('ident'), 'D2L_Correct' if a.get('ident') in correct else 'D2L_Incorrect') for a in options]
        processing = _xml(_processing(conditions).encode())
        grading = item.find('.//d:grading_type', NS)
        if grading is None:
            raise ValueError('The multi-select grading field is missing.')
        grading.text = '3'
    else:
        old = item.find('resprocessing')
        processing = deepcopy(old)
        if processing is None:
            raise ValueError('The answer scoring block is missing.')
        templates = {}
        for condition in processing.findall('respcondition'):
            equal = condition.find('conditionvar/varequal')
            if equal is None or len(list(condition.find('conditionvar'))) != 1 or len(condition.findall('setvar')) != 1:
                raise ValueError('This answer uses custom scoring that cannot be edited safely here.')
            templates[equal.text] = deepcopy(condition)
            processing.remove(condition)
        for answer in options:
            condition = templates.get(answer.get('ident'))
            if condition is None:
                condition = ET.Element('respcondition')
                ET.SubElement(ET.SubElement(condition, 'conditionvar'), 'varequal', respident=response.get('ident')).text = answer.get('ident')
                ET.SubElement(condition, 'setvar', varname='que_score', action='Set')
            desired = credits[answer.get('ident')] * 100 if credits is not None else (100 if answer.get('ident') in correct else 0)
            setter = condition.find('setvar')
            try:
                unchanged = abs(float(setter.text) - desired) < 1e-9
            except (ValueError, TypeError):
                unchanged = False
            if not unchanged:
                setter.text = str(desired)
            processing.append(condition)
    old = item.find('resprocessing')
    if old is None:
        raise ValueError('The question scoring block is missing.')
    index = list(item).index(old)
    item.remove(old)
    item.insert(index, processing)


def _apply_question(item, record):
    validate_record(record)
    flow = item.find('./presentation/flow')
    material = flow.find('material')
    if material is None:
        raise ValueError('This question prompt layout cannot be edited safely.')
    _set_material(material, record['raw_prompt'])
    for field in item.findall('.//qti_metadatafield'):
        if field.findtext('fieldlabel') == 'qmd_weighting':
            field.find('fieldentry').text = str(record['points'])
    if record['kind'] == 'Matching':
        from quiz_repair import _material
        pairs = {stem: values[0] for stem, values in record['pairs']}
        groups = item.findall('.//response_grp')
        if len(groups) != len(pairs):
            raise ValueError('Matching rows cannot be added or removed in this version.')
        conditions = []
        for group in groups:
            stem = _text(_material(group.find('material')))
            options = group.findall('.//response_label')
            if stem not in pairs or sum(_text(_material(a)) == pairs[stem] for a in options) != 1:
                raise ValueError('A matching answer is not in the original answer pool.')
            conditions.extend(_condition(group.get('respident'), a.get('ident'), 'D2L_Correct' if _text(_material(a)) == pairs[stem] else 'D2L_Incorrect') for a in options)
        old = item.find('resprocessing')
        index = list(item).index(old)
        item.remove(old)
        item.insert(index, _xml(_processing(conditions, matching=True).encode()))
        item.find('.//d:grading_type', NS).text = '0'
        return
    render = flow.find('./response_lid/render_choice')
    original = render.findall('.//response_label')
    parents = {child: parent for parent in render.iter() for child in parent}
    target = parents[original[0]] if original else render
    template = deepcopy(target) if target.tag == 'flow_label' and len(target.findall('.//response_label')) == 1 else None
    for option in original[len(record['choices']):]:
        parent = parents[option]
        parent.remove(option)
        if parent.tag == 'flow_label' and not list(parent) and not (parent.text or '').strip():
            parents[parent].remove(parent)
    correct = set()
    credits = {}
    for index, answer in enumerate(record['choices']):
        if index < len(original):
            option = original[index]
        else:
            option = ET.Element('response_label', ident=f'{item.get("label")}_REVIEW_{index + 1}')
            if template is not None:
                wrapper = deepcopy(template)
                old_option = wrapper.find('.//response_label')
                owner = next(p for p in wrapper.iter() if old_option in list(p))
                position = list(owner).index(old_option)
                owner.remove(old_option)
                owner.insert(position, option)
                parents[target].append(wrapper)
            else:
                target.append(option)
        ident = option.get('ident')
        _set_material(option, answer.get('raw_html', answer['html']))
        if answer['correct']:
            correct.add(ident)
        credits[ident] = answer.get('credit', 1.0 if answer['correct'] else 0.0)
    _set_scoring(item, correct, credits)
