"""Read-only export comparison and a local, sandboxed HTML review report."""
from collections import Counter, defaultdict
import base64
from copy import deepcopy
import hashlib
import html
from pathlib import Path
import re
import tarfile
from urllib.parse import urlsplit, unquote
import zipfile

from bs4 import BeautifulSoup
from quiz_repair import (NS, MAX_XML, MAX_PACKAGE, _xml, _text, _meta, _material,
                         _prompt, _source_kind, _correct_ids, _safe_name, _visible_content)


def _finding(code, message, severity='review'):
    return {'code': code, 'message': message, 'severity': severity}


def _layout_flags(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    flags = []
    if any(p.find('p') for p in soup.find_all('p')):
        flags.append('nested paragraph tags')
    if any(not _visible_content(str(p)) for p in soup.find_all(['p', 'div'])):
        flags.append('empty paragraph/block')
    if re.search(r'(?:<br\s*/?>\s*){2,}', value or '', re.I):
        flags.append('repeated line breaks')
    if re.search(r'<br\s*/?>\s*(?:</(?:p|div|span)>\s*)*$', value or '', re.I):
        flags.append('trailing line break')
    return flags


def _inline_images(value, package):
    """Embed only packaged raster images; never make a network request."""
    soup = BeautifulSoup(value or '', 'html.parser')
    names = {n: n for n in package} if isinstance(package, dict) else {n.replace('\\', '/'): n for n in package.namelist()}
    for image in soup.find_all('img'):
        src = image.get('src', '')
        parsed = urlsplit(src)
        if src.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/gif;base64,', 'data:image/webp;base64,')):
            continue
        key = unquote(parsed.path).replace('\\', '/').lstrip('/')
        ext = Path(key).suffix.lower()
        mime = {'.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.gif': 'gif', '.webp': 'webp'}.get(ext)
        if not parsed.scheme and not parsed.netloc and key in names and mime and (len(package[key]) if isinstance(package, dict) else package.getinfo(names[key]).file_size) <= MAX_XML:
            image['src'] = f'data:image/{mime};base64,' + base64.b64encode(package[key] if isinstance(package, dict) else package.read(names[key])).decode()
        else:
            placeholder = soup.new_tag('span')
            placeholder.string = '[Image unavailable in this export or external image blocked]'
            image.replace_with(placeholder)
    return str(soup)


def _bs_record(item, reference, context, package, filename):
    kind = _meta(item).get('qmd_questiontype', 'Unknown')
    findings = []
    try:
        correct = _correct_ids(item) if kind in ('Multiple Choice', 'True/False', 'Multi-Select') else set()
    except (ValueError, TypeError):
        correct = set()
        findings.append(_finding('unreadable_scoring', 'The answer-key format could not be verified.', 'block'))
    choices = []
    weights = {}
    if kind in ('Multiple Choice', 'True/False'):
        for condition in item.findall('resprocessing/respcondition'):
            equal, setter = condition.find('conditionvar/varequal'), condition.find('setvar')
            if equal is not None and setter is not None and setter.get('action') == 'Set':
                try:
                    weights[equal.text] = float(setter.text) / 100
                except (ValueError, TypeError):
                    pass
    for index, option in enumerate(item.findall('./presentation/flow/response_lid/render_choice//response_label'), 1):
        content = _material(option)
        choices.append({'raw_html': content, 'html': _inline_images(content, package), 'text': _text(content),
                        'correct': option.get('ident') in correct, 'credit': weights.get(option.get('ident'))})
        if not _visible_content(content):
            findings.append(_finding('blank_answer', f'Answer choice {index} is blank.', 'block'))
    if choices and not correct:
        findings.append(_finding('missing_correct_answer', 'No correct choice could be verified.', 'block'))
    prompt = _prompt(item)
    embedded_prompt = _inline_images(prompt, package)
    embedded_context = _inline_images(context, package)
    missing = (embedded_prompt + embedded_context + ''.join(a['html'] for a in choices)).count('[Image unavailable in this export or external image blocked]')
    if missing:
        findings.append(_finding('unavailable_image', f'{missing} image(s) are missing from this export or are external and were not fetched.'))
    for field, value in [('Prompt', prompt)] + [(f'Answer {i}', _material(o)) for i, o in enumerate(item.findall('./presentation/flow/response_lid/render_choice//response_label'), 1)]:
        flags = _layout_flags(value)
        if flags:
            findings.append(_finding('html_layout', f'{field}: {", ".join(flags)}; spacing needs visual review.'))
    points = reference.findtext('d:points', namespaces=NS) if reference.tag == 'itemref' else None
    if points is None:
        points = _meta(item).get('qmd_weighting')
    pairs = []
    for row in item.findall('.//response_grp'):
        options = {o.get('ident'): _text(_material(o)) for o in row.findall('.//response_label')}
        answers = []
        for condition in item.findall('resprocessing/respcondition'):
            equal = condition.find('conditionvar/varequal')
            if equal is not None and equal.get('respident') == row.get('respident'):
                if any(s.get('varname') == 'D2L_Correct' and s.text == '1' for s in condition.findall('setvar')):
                    answers.append(options.get(equal.text, '[unresolved]'))
        pairs.append([_text(_material(row.find('material'))), answers])
    return {'title': item.get('title', 'Untitled'), 'id': item.get('label'), 'kind': kind,
            'prompt': embedded_prompt, 'raw_prompt': prompt,
            'context': embedded_context, 'raw_context': context, 'choices': choices, 'pairs': pairs,
            'global_id': _meta(item).get('qmd_globalid'), 'display_id': _meta(item).get('qmd_displayid'),
            'points': float(points) if points is not None else None,
            'grading': item.findtext('.//d:grading_type', namespaces=NS), 'file': filename,
            'findings': findings}


def _brightspace(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        normalized = [_safe_name(n).casefold() for n in names]
        if len(set(normalized)) != len(normalized) or sum(i.file_size for i in z.infolist()) > MAX_PACKAGE:
            raise ValueError('The export has duplicate/unsafe paths or exceeds the size limit.')
        roots = {}
        for name in names:
            if name == 'questiondb.xml' or re.fullmatch(r'quiz_d2l_\d+\.xml', name):
                if z.getinfo(name).file_size > MAX_XML:
                    raise ValueError('An XML file exceeds the size limit.')
                roots[name] = _xml(z.read(name))
        if not roots:
            raise ValueError('This is not a Brightspace quiz export.')
        bank = {i.get('label'): i for i in roots.get('questiondb.xml', []).iter('item')} if 'questiondb.xml' in roots else {}
        bank_records = {}
        for key, item in bank.items():
            bank_records[key] = _bs_record(item, item, '', z, 'questiondb.xml')
        quizzes = {}
        for name, root in roots.items():
            if name == 'questiondb.xml':
                continue
            assessment = root.find('assessment')
            if assessment is None:
                continue
            title = assessment.get('title', name)
            key = _text(title).casefold()
            if key in quizzes:
                raise ValueError('Quiz names are duplicated; their pairing requires manual review.')
            rows, pending = [], ''
            for node in assessment.iter():
                if node.tag == 'section':
                    pending += _material(node.find('presentation_material'))
                elif node.tag in ('item', 'itemref'):
                    item = bank.get(node.get('linkrefid')) if node.tag == 'itemref' else node
                    if item is None:
                        raise ValueError('A quiz refers to a missing library item.')
                    row = _bs_record(item, node, pending, z, name)
                    row['number'] = len(rows) + 1
                    rows.append(row)
                    pending = ''
            quizzes[key] = {'title': title, 'questions': rows}
        return {'format': 'Brightspace', 'quizzes': quizzes, 'bank': list(bank_records.values())}


def _moodle(path):
    with tarfile.open(path, 'r:*') as archive:
        def read(name):
            member = archive.getmember(name)
            if not member.isfile() or member.size > MAX_XML:
                raise ValueError('A required Moodle XML file is invalid or too large.')
            return _xml(archive.extractfile(member).read())
        root = read('questions.xml')
        bank = []
        for q in root.findall('.//question'):
            if q.findtext('qtype') == 'description':
                continue
            choices = [{'html': a.findtext('answertext') or '', 'text': _text(a.findtext('answertext')),
                        'correct': float(a.findtext('fraction') or 0) > 0,
                        'credit': float(a.findtext('fraction') or 0)} for a in q.findall('.//answer')]
            pairs = [[_text(m.findtext('questiontext')), [_text(m.findtext('answertext'))]] for m in q.findall('.//matches/match')]
            bank.append({'title': q.findtext('name'), 'id': q.get('id'), 'kind': _source_kind(q),
                         'prompt': q.findtext('questiontext') or '', 'raw_prompt': q.findtext('questiontext') or '',
                         'context': '', 'choices': choices, 'pairs': pairs,
                         'points': float(q.findtext('defaultmark')) if q.findtext('defaultmark') is not None else None,
                         'grading': 'Moodle answer fractions', 'findings': []})
        by_id = {q['id']: q for q in bank}
        entries = {e.get('id'): e for e in root.findall('.//question_bank_entry')}
        quizzes = {}
        for name in archive.getnames():
            if not (name.startswith('activities/quiz_') and name.endswith('/quiz.xml')):
                continue
            quiz = read(name).find('quiz')
            rows, pending = [], ''
            for slot in sorted(quiz.findall('question_instances/question_instance'), key=lambda s: int(s.findtext('slot'))):
                ref = slot.find('question_reference')
                if ref is None or ref.findtext('questionbankentryid') not in entries:
                    raise ValueError('A source question pool/reference requires manual review.')
                versions = entries[ref.findtext('questionbankentryid')].findall('question_version/question_versions')
                requested = ref.findtext('version')
                version = max(versions, key=lambda v: int(v.findtext('version'))) if requested in (None, '$@NULL@$') else next(v for v in versions if v.findtext('version') == requested)
                question = version.find('questions/question')
                if question.findtext('qtype') == 'description':
                    pending += question.findtext('questiontext') or ''
                    continue
                row = deepcopy(by_id[question.get('id')])
                row.update(number=len(rows) + 1, context=pending, points=float(slot.findtext('maxmark') or 0),
                           version=version.findtext('version'), source_status=version.findtext('status'))
                if row['source_status'] != 'ready':
                    row['findings'].append(_finding('source_revision_status', f'Source revision {row["version"]} is marked {row["source_status"]}; confirm the authoritative version.'))
                rows.append(row)
                pending = ''
            title = quiz.findtext('name')
            quizzes[_text(title).casefold()] = {'title': title, 'questions': rows}
        return {'format': 'Moodle', 'quizzes': quizzes, 'bank': bank}


def _key(record):
    return record['kind'], _text(record['raw_prompt']), tuple(sorted(a['text'] for a in record['choices']))


def _image_fingerprints(record):
    result = []
    for image in BeautifulSoup(record['prompt'] + record['context'], 'html.parser').find_all('img'):
        src = image.get('src', '')
        if src.startswith('data:image/') and ';base64,' in src:
            try:
                result.append(hashlib.sha256(base64.b64decode(src.split(',', 1)[1], validate=True)).hexdigest())
            except ValueError:
                result.append('[invalid embedded image]')
    return Counter(result)


def inspect_exports(reference_path, candidate_path):
    """Compare Moodle→Brightspace or original→sandbox Brightspace exports."""
    reference, candidate = Path(reference_path).resolve(), Path(candidate_path).resolve()
    source = _brightspace(reference) if zipfile.is_zipfile(reference) else _moodle(reference)
    target = _brightspace(candidate)
    source_bank = defaultdict(list)
    for row in source['bank']:
        source_bank[_key(row)].append(row)
    quizzes, issues = [], []
    for key, quiz in target['quizzes'].items():
        original = source['quizzes'].get(key)
        rows, quiz_findings = [], []
        if original and len(original['questions']) != len(quiz['questions']):
            quiz_findings.append(_finding('question_count', f'Question counts differ: reference {len(original["questions"])}; candidate {len(quiz["questions"])}.', 'block'))
        elif not original:
            quiz_findings.append(_finding('source_scope', 'Reference quiz membership/settings are unavailable; only question-bank matches can be checked.'))
        for index, row in enumerate(quiz['questions']):
            old = original['questions'][index] if original and index < len(original['questions']) else None
            if old is None:
                matches = source_bank.get(_key(row), [])
                old = matches[0] if len(matches) == 1 else None
            findings = list(row['findings'])
            if old:
                findings.extend({**f, 'code': 'reference_' + f['code'], 'message': 'Reference: ' + f['message']}
                                for f in old['findings'])
                if old['kind'] != row['kind'] or _text(old['raw_prompt']) != _text(row['raw_prompt']):
                    findings.append(_finding('question_identity', 'Question type/prompt differs at this position. Verify the pairing.', 'block'))
                old_answers, new_answers = Counter(a['text'] for a in old['choices']), Counter(a['text'] for a in row['choices'])
                if old_answers != new_answers:
                    missing, extra = list((old_answers-new_answers).elements()), list((new_answers-old_answers).elements())
                    findings.append(_finding('answer_options', f'Answer options differ. Missing: {missing!r}. Extra: {extra!r}.', 'block'))
                if sorted((a['text'], a['correct']) for a in old['choices']) != sorted((a['text'], a['correct']) for a in row['choices']):
                    findings.append(_finding('answer_key', 'Answer text/correct-choice mapping differs.', 'block'))
                if sorted(old['pairs']) != sorted(row['pairs']):
                    findings.append(_finding('matching_pairs', 'Matching stem/answer mappings differ.',
                                             'block' if source['format'] == 'Brightspace' else 'review'))
                if old['kind'] in ('Multiple Choice', 'True/False'):
                    old_credit = Counter((a['text'], round(a['credit'], 6) if a['credit'] is not None else None) for a in old['choices'])
                    new_credit = Counter((a['text'], round(a['credit'], 6) if a['credit'] is not None else None) for a in row['choices'])
                    if old_credit != new_credit or any(a['credit'] is None for a in row['choices']):
                        findings.append(_finding('answer_credit', 'Per-answer scoring credit differs or could not be read.', 'block'))
                if old['points'] is not None and row['points'] is not None and abs(old['points']-row['points']) > 1e-6:
                    findings.append(_finding('question_points', f'Points differ: {old["points"]} vs {row["points"]}.', 'block'))
                if old['raw_prompt'] != row['raw_prompt'] or [a['html'] for a in old['choices']] != [a['html'] for a in row['choices']]:
                    findings.append(_finding('html_difference', 'Prompt/answer HTML differs. Text matching alone cannot verify spacing or formatting.'))
                if old['grading'] != row['grading'] and (source['format'] == 'Brightspace' or row['kind'] in ('Multi-Select', 'Matching')):
                    findings.append(_finding('grading_rule', f'Grading representation differs: {old["grading"]} → {row["grading"]}. Check the approved scoring policy.'))
                old_images, new_images = _image_fingerprints(old), _image_fingerprints(row)
                if old_images != new_images:
                    findings.append(_finding('image_content', 'Embedded/packaged image content differs or an image is unavailable.',
                                             'block' if source['format'] == 'Brightspace' and old_images else 'review'))
                old_position = len(BeautifulSoup(old['raw_prompt'], 'html.parser').find_all('img'))
                new_position = len(BeautifulSoup(row['raw_prompt'], 'html.parser').find_all('img'))
                if old_position != new_position:
                    findings.append(_finding('image_position', 'Image placement differs between the question prompt and surrounding section material.'))
            else:
                findings.append(_finding('missing_reference', 'No unique reference question was found.', 'block'))
            # Preserve all fields and raw HTML for local, side-by-side review.
            rows.append({'number': index+1, 'title': row['title'], 'reference': old, 'candidate': row, 'findings': findings})
        quizzes.append({'title': quiz['title'], 'questions': rows, 'findings': quiz_findings})
    if source['format'] == 'Brightspace':
        for key in source['quizzes'].keys()-target['quizzes'].keys():
            issues.append(_finding('missing_quiz', f'The candidate is missing quiz {source["quizzes"][key]["title"]}.', 'block'))
    # Blank library choices also block repair even if not currently used in a quiz.
    for row in target['bank']:
        for finding in row['findings']:
            if finding['code'] == 'blank_answer':
                issues.append(_finding('library_blank_answer', f'Library {row["title"]} ({row["id"]}): {finding["message"]}', 'block'))
    findings = issues + [f for q in quizzes for f in q['findings'] + [f for row in q['questions'] for f in row['findings']]]
    with reference.open('rb') as f:
        source_hash = hashlib.file_digest(f, 'sha256').hexdigest()
    with candidate.open('rb') as f:
        target_hash = hashlib.file_digest(f, 'sha256').hexdigest()
    return {'reference_path': str(reference), 'candidate_path': str(candidate), 'reference_format': source['format'],
            'reference_sha256': source_hash, 'candidate_sha256': target_hash, 'quizzes': quizzes, 'findings': issues,
            'question_count': sum(len(q['questions']) for q in quizzes),
            'blocking_count': sum(f['severity'] == 'block' for f in findings),
            'review_count': sum(f['severity'] != 'block' for f in findings),
            'live_brightspace_tested': False}


def _safe_preview(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in list(soup.find_all(['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'form', 'input', 'button'])):
        tag.decompose()
    for tag in soup.find_all(True):
        for key in list(tag.attrs):
            if key not in ('style', 'src', 'alt', 'width', 'height', 'colspan', 'rowspan'):
                del tag[key]
        if 'style' in tag.attrs and re.search(r'url\s*\(|expression|@import|behavior|binding', tag['style'], re.I):
            del tag['style']
        if tag.name == 'img' and not tag.get('src', '').startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/gif;base64,', 'data:image/webp;base64,')):
            tag.replace_with('[External or unavailable image blocked]')
    return str(soup)


def _preview(record):
    if record is None:
        return '<p>Reference question unavailable.</p>'
    content = _safe_preview(record['context']) + '<div>' + _safe_preview(record['prompt']) + '</div>'
    if record['choices']:
        content += '<ol>' + ''.join('<li>' + (_safe_preview(a['html']) if _visible_content(a['html']) else '<strong>[BLANK CHOICE]</strong>')
                                   + (' <em>(correct)</em>' if a['correct'] else '') + '</li>' for a in record['choices']) + '</ol>'
    if record['pairs']:
        content += '<table>' + ''.join('<tr><td>' + html.escape(stem) + '</td><td>' + html.escape(', '.join(answers)) + '</td></tr>' for stem, answers in record['pairs']) + '</table>'
    document = '<!doctype html><meta charset="utf-8"><style>body{font:16px Arial,sans-serif;padding:12px;line-height:1.5}img{max-width:100%;height:auto}li{padding:4px}td{padding:5px}pre{white-space:pre-wrap}</style>' + content
    return '<iframe sandbox="" loading="lazy" title="Local question approximation" srcdoc="' + html.escape(document, quote=True) + '"></iframe>'


def write_review_report(review, output_path):
    output = Path(output_path).resolve()
    if output.suffix.lower() != '.html' or output in {Path(review['reference_path']), Path(review['candidate_path'])}:
        raise ValueError('Choose a separate .html report filename.')
    esc = html.escape
    def items(findings):
        return '<ul>' + ''.join('<li><strong>' + esc(f['severity'].upper()) + ':</strong> ' + esc(f['message']) + '</li>' for f in findings) + '</ul>'
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; frame-src \'self\' about:; base-uri \'none\'; form-action \'none\'">',
             '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Quiz export review</title>',
             '<style>body{font:16px system-ui,sans-serif;line-height:1.55;color:#182432;background:#fff;margin:30px auto;padding:0 20px;max-width:1280px}h1{font-size:30px}h2{margin-top:36px}summary{cursor:pointer;padding:12px 0;font-weight:650}details{border-top:1px solid #ccd4db}iframe{width:100%;height:480px;border:1px solid #ccd4db;background:white}.compare{display:grid;grid-template-columns:1fr 1fr;gap:20px}.note{background:#f0f3f6;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}li{margin-bottom:5px}@media(max-width:800px){.compare{grid-template-columns:1fr}}</style>',
             '<h1>Quiz export review</h1>',
             f'<p><strong>{len(review["quizzes"])} quizzes · {review["question_count"]} question placements · {review["blocking_count"]} blocking findings · {review["review_count"]} review findings</strong></p>',
             '<p class="note">This compares the selected files, not the current live course. Export agreement cannot prove the reference is current. Previews approximate the supplied HTML; actual Brightspace layout and grading require a sandbox check. External requests and scripts are blocked.</p>',
             '<p><strong>Reference:</strong> ' + esc(review['reference_path']) + '<br><strong>Candidate:</strong> ' + esc(review['candidate_path']) + '</p>', items(review['findings'])]
    for quiz in review['quizzes']:
        parts += ['<h2>' + esc(quiz['title']) + '</h2>', items(quiz['findings'])]
        for row in quiz['questions']:
            status = 'BLOCKED' if any(f['severity'] == 'block' for f in row['findings']) else 'REVIEW' if row['findings'] else 'No static differences found'
            parts += ['<details><summary>' + esc(f'Question {row["number"]} · {row["title"]} · {status}') + '</summary>', items(row['findings']),
                      '<div class="compare"><section><h3>Reference</h3>' + _preview(row['reference']) + '</section><section><h3>Candidate</h3>' + _preview(row['candidate']) + '</section></div>']
            for side, record in [('Reference', row['reference']), ('Candidate', row['candidate'])]:
                if record:
                    parts += ['<details><summary>' + side + ' prompt HTML and provenance</summary><p>' + esc(f'ID: {record["id"]}; points: {record["points"]}; grading: {record["grading"]}; version: {record.get("version", "not supplied")}; status: {record.get("source_status", "not supplied")}') + '</p><pre>' + esc(record['raw_prompt']) + '</pre></details>']
            parts.append('</details>')
    parts += ['<h2>Input fingerprints</h2><pre>' + esc(review['reference_sha256'] + '\n' + review['candidate_sha256']) + '</pre></html>']
    data = '\n'.join(parts)
    if len(data.encode('utf-8')) > 128 * 1024 * 1024:
        raise ValueError('The review report is too large; export a smaller quiz selection.')
    with output.open('x', encoding='utf-8') as f:
        f.write(data)
    return str(output)
