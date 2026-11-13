import copy
import hashlib
import http.cookiejar
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from quiz_workspace import QuizWorkspace
from quiz_repair import _xml, _correct_ids, _prompt, NS
from quiz_studio import QuizStudio


def fixture(tmp_path):
    def item(label, gid, prompt, answers):
        root = ET.Element('item', label=label, title=label)
        metadata = ET.SubElement(ET.SubElement(root, 'itemmetadata'), 'qtimetadata')
        for key, value in [('qmd_questiontype', 'Multiple Choice'), ('qmd_weighting', '1'), ('qmd_globalid', gid)]:
            field = ET.SubElement(metadata, 'qti_metadatafield')
            ET.SubElement(field, 'fieldlabel').text = key
            ET.SubElement(field, 'fieldentry').text = value
        flow = ET.SubElement(ET.SubElement(root, 'presentation'), 'flow')
        ET.SubElement(ET.SubElement(flow, 'material'), 'mattext').text = prompt
        response = ET.SubElement(flow, 'response_lid', ident=label + '_LID')
        render = ET.SubElement(response, 'render_choice')
        processing = ET.SubElement(root, 'resprocessing')
        for index, (text, correct) in enumerate(answers):
            ident = label + '_' + str(index)
            answer = ET.SubElement(ET.SubElement(render, 'flow_label'), 'response_label', ident=ident)
            ET.SubElement(ET.SubElement(answer, 'material'), 'mattext').text = text
            condition = ET.SubElement(processing, 'respcondition')
            ET.SubElement(ET.SubElement(condition, 'conditionvar'), 'varequal', respident=response.get('ident')).text = ident
            ET.SubElement(condition, 'setvar', action='Set').text = '100' if correct else '0'
            ET.SubElement(condition, 'displayfeedback', linkrefid=ident + '_feedback')
            ET.SubElement(ET.SubElement(root, 'itemfeedback', ident=ident + '_feedback'), 'material').text = 'preserved feedback'
        return root
    q2 = [('Bookwalter', False), ('Balfour', True), ('<br>', False), ('<br>', False)]
    good = [('Bookwalter', False), ('Balfour', True), ('Malleable', False)]
    q3 = [('Solid', False), ('All the above', True)]
    bank = ET.Element('questestinterop')
    obj = ET.SubElement(bank, 'objectbank')
    for i in [item('bad2', 'shared-q2', 'Name this instrument', q2),
              item('good2', 'shared-q2', '<p>Name this instrument</p><img src="Content/photo.png">', good),
              item('bad3', 'shared-q3', '<p><p>Lateral blades</p><br></p>', q3),
              item('good3', 'shared-q3', '<p>Lateral blades</p>', q3)]: obj.append(i)
    quiz = ET.Element('questestinterop')
    assessment = ET.SubElement(quiz, 'assessment', title='Exam 1')
    extension = ET.SubElement(assessment, 'assess_procextension')
    ET.SubElement(extension, 'is_active').text = 'no'
    section = ET.SubElement(assessment, 'section', title='Picture')
    material = ET.SubElement(section, 'presentation_material')
    ET.SubElement(ET.SubElement(material, 'material'), 'mattext').text = '<img src="Content/photo.png">'
    for label in ('bad2', 'bad3'):
        reference = ET.SubElement(assessment, 'itemref', linkrefid=label)
        ET.SubElement(reference, '{' + NS['d'] + '}points').text = '1'
    candidate = tmp_path / 'sandbox.zip'
    with zipfile.ZipFile(candidate, 'w') as z:
        z.writestr('imsmanifest.xml', '<manifest/>')
        z.writestr('questiondb.xml', ET.tostring(bank))
        z.writestr('quiz_d2l_1.xml', ET.tostring(quiz))
        z.writestr('unchanged.txt', 'keep me')
    picture = io.BytesIO(); Image.new('RGB', (4, 4), 'red').save(picture, format='PNG')
    pictures = tmp_path / 'pictures.zip'
    with zipfile.ZipFile(pictures, 'w') as z: z.writestr('Content/photo.png', picture.getvalue())
    return candidate, pictures


def prepared(tmp_path):
    candidate, pictures = fixture(tmp_path)
    workspace = QuizWorkspace(tmp_path / 'reviews')
    workspace.load(str(candidate), pictures=str(pictures))
    return workspace, candidate


def test_versions_are_explicit_and_blank_answers_and_images_block(tmp_path):
    candidate, pictures = fixture(tmp_path)
    w = QuizWorkspace(tmp_path / 'reviews'); w.load(str(candidate))
    assert len(w.rows['0:1']['variants']) == 2
    assert len(w.rows['0:2']['variants']) == 2
    assert w.chosen('0:1')['id'] == 'bad2'
    assert w.snapshot()['blocked'] == 2
    w.edit('0:1', {'variant': 'library:good2', 'reviewed': True})
    assert any('picture' in message for message in w.checks('0:1')[0])
    with pytest.raises(ValueError): w.export(tmp_path / 'blocked.zip')
    assert not (tmp_path / 'blocked.zip').exists()


def test_choose_versions_export_preserves_keys_feedback_settings_and_originals(tmp_path):
    w, candidate = prepared(tmp_path); original = candidate.read_bytes()
    w.edit('0:1', {'variant': 'library:good2', 'reviewed': True})
    w.edit('0:2', {'variant': 'library:good3', 'reviewed': True})
    output = tmp_path / 'reviewed.zip'; w.export(output)
    assert candidate.read_bytes() == original
    with zipfile.ZipFile(output) as z:
        bank = {i.get('label'): i for i in _xml(z.read('questiondb.xml')).iter('item')}
        assert len(bank['bad2'].findall('.//response_label')) == 3
        wrappers = bank['bad2'].findall('.//render_choice/flow_label')
        assert len(wrappers) == 3 and all(len(n.findall('.//response_label')) == 1 for n in wrappers)
        assert _correct_ids(bank['bad2']) == {'bad2_1'}
        assert _prompt(bank['bad3']) == '<p>Lateral blades</p>'
        assert len(bank['bad2'].findall('itemfeedback')) == 4
        assert bank['bad2'].find('resprocessing/respcondition/displayfeedback').get('linkrefid') == 'bad2_0_feedback'
        quiz = _xml(z.read('quiz_d2l_1.xml'))
        assert quiz.findtext('.//is_active') == 'no'
        assert quiz.findtext('.//section/presentation_material/material/mattext') in (None, '')
        assert [p.text for p in quiz.findall('.//d:points', NS)] == ['1.0', '1.0']
        assert z.read('unchanged.txt') == b'keep me'
        assert z.read('imsmanifest.xml') == b'<manifest/>'
    with pytest.raises(ValueError, match='never overwritten'): w.export(output)
    with pytest.raises(ValueError): w.export(candidate)


def test_saved_review_reopens_and_rejects_changed_input(tmp_path):
    w, candidate = prepared(tmp_path)
    w.edit('0:1', {'variant': 'library:good2', 'reviewed': True})
    saved = w.draft_path
    reopened = QuizWorkspace(tmp_path / 'reviews'); reopened.restore(saved)
    assert reopened.chosen('0:1')['choices'][2]['text'] == 'Malleable'
    assert reopened.edits['0:1']['reviewed']
    candidate.write_bytes(candidate.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='changed'): reopened.restore(saved)
    with pytest.raises(ValueError, match='changed'): reopened.export(tmp_path / 'out.zip')


@pytest.mark.parametrize('value', [float('nan'), -1, float('inf'), '1'])
def test_invalid_points_never_replace_a_saved_question(tmp_path, value):
    w, _ = prepared(tmp_path)
    record = copy.deepcopy(w.rows['0:1']['variants'][1]['record']); record['points'] = value
    with pytest.raises(ValueError): w.edit('0:1', {'variant': 'library:good2', 'record': record, 'reviewed': True})
    assert not w.edits


def test_preview_blocks_imported_scripts_and_external_images(tmp_path):
    w, _ = prepared(tmp_path)
    record = copy.deepcopy(w.rows['0:1']['variants'][1]['record'])
    record['raw_prompt'] += '<script>alert(1)</script><img onerror="alert(1)" src="https://example.invalid/pic.png">'
    w.edit('0:1', {'variant': 'library:good2', 'record': record})
    preview = w.preview('0:1').decode()
    assert '<script' not in preview and 'onerror' not in preview and 'src="https:' not in preview
    assert 'data:image/png' in preview


def test_loopback_launch_auth_origin_and_stale_edit_guard(tmp_path):
    studio = QuizStudio(tmp_path / 'reviews', lambda field: None)
    jar = http.cookiejar.CookieJar(); client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        url = studio.start()
        with pytest.raises(urllib.error.HTTPError) as exc: client.open(studio.origin + '/api/state')
        assert exc.value.code == 403
        response = client.open(url); assert b'Quiz Repair' in response.read()
        assert 'HttpOnly' in response.headers['Set-Cookie']
        with pytest.raises(urllib.error.HTTPError): client.open(url)
        headers = {'Content-Type': 'application/json', 'Origin': studio.origin, 'X-Studio-Request': '1'}
        request = urllib.request.Request(studio.origin + '/api/choose', data=b'{"field":"candidate"}', headers={**headers, 'Origin': 'https://example.invalid'})
        with pytest.raises(urllib.error.HTTPError) as exc: client.open(request)
        assert exc.value.code == 403
        candidate, pictures = fixture(tmp_path)
        studio.files.update(candidate=str(candidate), pictures=str(pictures))
        studio.load()
        request = urllib.request.Request(studio.origin + '/api/edit', data=json.dumps({'id':'0:1','variant':'library:good2','reviewed':True,'revision':99}).encode(), headers=headers)
        with pytest.raises(urllib.error.HTTPError) as exc: client.open(request)
        assert b'another tab' in exc.value.read()
        assert not studio.workspace.edits
        response = client.open(studio.origin + '/api/preview?id=0:1&variant=library:good2')
        assert 'sandbox' in response.headers['Content-Security-Policy']
        assert b'Malleable' in response.read()
    finally: studio.close()


def test_web_export_normalizes_scoring_without_promoting_wrong_options(tmp_path):
    from test_quiz_repair import _fixture
    from quiz_repair import repair_package
    moodle, package = _fixture(tmp_path)
    repaired = tmp_path / 'prepared.zip'; repair_package(moodle, package, repaired)
    with zipfile.ZipFile(repaired) as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    files['questiondb.xml'] = files['questiondb.xml'].replace(b'<d2l_2p0:grading_type>3', b'<d2l_2p0:grading_type>2').replace(b'<d2l_2p0:grading_type>0', b'<d2l_2p0:grading_type>1')
    with zipfile.ZipFile(repaired, 'w') as archive:
        for name, blob in files.items(): archive.writestr(name, blob)
    w = QuizWorkspace(tmp_path / 'reviews'); w.load(str(repaired))
    matching = copy.deepcopy(w.chosen('0:2')); matching['points'] = 2
    w.edit('0:2', {'record': matching, 'reviewed': True})
    out = tmp_path / 'out.zip'; w.export(out)
    with zipfile.ZipFile(out) as archive:
        items = list(_xml(archive.read('questiondb.xml')).iter('item'))
        assert _correct_ids(items[0]) == {'A', 'B'}
        assert items[0].findtext('.//d:grading_type', namespaces=NS) == '3'
        assert items[1].findtext('.//d:grading_type', namespaces=NS) == '0'


def test_native_launcher_and_file_picker_bridge(qtbot, monkeypatch, tmp_path):
    from panels import quiz_repair_panel as panel_module
    candidate, _ = fixture(tmp_path)
    monkeypatch.setattr(panel_module, 'USERDATA_DIR', tmp_path)
    opened = []
    monkeypatch.setattr(panel_module.webbrowser, 'open', lambda url: opened.append(url) or True)
    monkeypatch.setattr(panel_module.QFileDialog, 'getOpenFileName', lambda *args: (str(candidate), ''))
    panel = panel_module.QuizRepairPanel(); qtbot.addWidget(panel)
    try:
        panel.open_studio()
        assert opened and opened[0].startswith('http://127.0.0.1:')
        panel._studio.choose('candidate'); panel._poll_studio()
        qtbot.waitUntil(lambda: panel._studio.files['candidate'] == str(candidate))
        assert not panel.studio_busy
    finally: panel.close_studio()


def test_shared_section_content_is_not_silently_changed(tmp_path):
    candidate, pictures = fixture(tmp_path)
    with zipfile.ZipFile(candidate) as archive: files = {n: archive.read(n) for n in archive.namelist()}
    quiz = _xml(files['quiz_d2l_1.xml']); assessment = quiz.find('assessment'); section = assessment.find('section')
    for reference in list(assessment.findall('itemref')):
        assessment.remove(reference); section.append(reference)
    files['quiz_d2l_1.xml'] = ET.tostring(quiz)
    with zipfile.ZipFile(candidate, 'w') as archive:
        for name, blob in files.items(): archive.writestr(name, blob)
    w = QuizWorkspace(tmp_path / 'reviews'); w.load(str(candidate), pictures=str(pictures))
    for key, version in [('0:1', 'library:good2'), ('0:2', 'library:good3')]: w.edit(key, {'variant': version, 'reviewed': True})
    with pytest.raises(ValueError, match='shared'): w.export(tmp_path / 'blocked.zip')
    assert not (tmp_path / 'blocked.zip').exists()


def test_reference_type_conflict_is_visible_and_cannot_be_acknowledged_away(tmp_path):
    candidate, pictures = fixture(tmp_path)
    reference = tmp_path / 'reference.zip'
    with zipfile.ZipFile(candidate) as source, zipfile.ZipFile(reference, 'w') as target:
        for name in source.namelist():
            data = source.read(name)
            if name == 'questiondb.xml': data = data.replace(b'Multiple Choice', b'True/False')
            target.writestr(name, data)
    w = QuizWorkspace(tmp_path / 'reviews'); w.load(str(candidate), str(reference), str(pictures))
    w.edit('0:1', {'variant': 'library:good2', 'reviewed': True})
    assert any('type disagrees' in error for error in w.checks('0:1')[0])
    with pytest.raises(ValueError): w.export(tmp_path / 'blocked.zip')


def test_adding_an_answer_preserves_a_separate_native_answer_row(tmp_path):
    w, _ = prepared(tmp_path)
    w.edit('0:1', {'variant': 'library:good2', 'reviewed': True})
    record = copy.deepcopy(w.rows['0:2']['variants'][1]['record'])
    record['choices'].append({'raw_html': 'Another answer', 'correct': False})
    w.edit('0:2', {'variant': 'library:good3', 'record': record, 'reviewed': True})
    out = tmp_path / 'added.zip'; w.export(out)
    with zipfile.ZipFile(out) as archive:
        item = next(i for i in _xml(archive.read('questiondb.xml')).iter('item') if i.get('label') == 'bad3')
        rows = item.findall('.//render_choice/flow_label')
        assert len(rows) == 3 and all(len(row.findall('.//response_label')) == 1 for row in rows)
        assert _correct_ids(item) == {'bad3_1'}
