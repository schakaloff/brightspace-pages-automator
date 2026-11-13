import copy
import http.cookiejar
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from quiz_studio import QuizStudio
from quiz_workspace import QuizWorkspace
from test_quiz_workspace import fixture, prepared


def client_for(studio):
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    client.open(studio.start()).close()
    return client


def upload_request(studio, field, name, data, **extra):
    headers = {'Content-Type': 'application/octet-stream', 'Origin': studio.origin,
               'X-Studio-Request': '1', 'X-Quiz-Filename': urllib.parse.quote(name),
               'X-Quiz-Revision': str(studio.revision), **extra}
    return urllib.request.Request(studio.origin + '/api/upload?field=' + field, data=data, headers=headers)


def test_browser_upload_never_requests_a_desktop_file_dialog(tmp_path):
    candidate, _ = fixture(tmp_path)
    calls = []
    studio = QuizStudio(tmp_path / 'reviews', calls.append)
    try:
        client = client_for(studio)
        response = client.open(upload_request(studio, 'candidate', 'course.zip', candidate.read_bytes()))
        state = json.load(response)
        saved = Path(state['files']['candidate'])
        assert saved.read_bytes() == candidate.read_bytes()
        assert saved.is_relative_to(tmp_path / 'reviews/uploads')
        assert saved != candidate and calls == [] and not studio.pending_file
    finally:
        studio.close()


def test_upload_checks_origin_and_rejects_unsafe_names(tmp_path):
    candidate, _ = fixture(tmp_path)
    studio = QuizStudio(tmp_path / 'reviews', lambda field: None)
    try:
        client = client_for(studio)
        with pytest.raises(urllib.error.HTTPError) as error:
            client.open(upload_request(studio, 'candidate', 'course.zip', candidate.read_bytes(), Origin='https://example.invalid'))
        assert error.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as error:
            client.open(upload_request(studio, 'candidate', '../escape.zip', candidate.read_bytes()))
        assert error.value.code == 400
        assert not studio.files['candidate'] and not (tmp_path / 'escape.zip').exists()
    finally:
        studio.close()


def test_large_local_upload_uses_bounded_reads_and_interrupted_copy_is_removed(tmp_path):
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, 'w') as archive:
        archive.writestr('large.bin', b'x' * (3 * 1024 * 1024))
    data = blob.getvalue()
    class Bounded(io.BytesIO):
        def read(self, size=-1):
            assert 0 <= size <= 1024 * 1024
            return super().read(size)
    studio = QuizStudio(tmp_path / 'reviews', lambda field: None)
    studio.receive_upload('pictures', 'pictures.zip', Bounded(data), len(data), 0)
    saved = studio.files['pictures']
    with pytest.raises(ValueError, match='interrupted'):
        studio.receive_upload('pictures', 'broken.zip', Bounded(data), len(data) + 1, 1)
    assert studio.files['pictures'] == saved
    assert not list((tmp_path / 'reviews').rglob('*.part'))
    assert not list((tmp_path / 'reviews').rglob('broken.zip'))
    assert not studio.busy


def test_browser_download_is_an_attachment_and_does_not_use_the_desktop(tmp_path):
    workspace, _ = prepared(tmp_path)
    for key, variant in [('0:1', 'library:good2'), ('0:2', 'library:good3')]:
        workspace.edit(key, {'variant': variant, 'reviewed': True})
    calls = []
    studio = QuizStudio(tmp_path / 'reviews', calls.append)
    studio.workspace = workspace
    studio.files.update(workspace.paths)
    try:
        client = client_for(studio)
        body = json.dumps({'revision': studio.revision}).encode()
        request = urllib.request.Request(studio.origin + '/api/export', data=body,
                                        headers={'Content-Type': 'application/json', 'Origin': studio.origin, 'X-Studio-Request': '1'})
        state = json.load(client.open(request))
        response = client.open(studio.origin + state['download_url'])
        assert 'attachment;' in response.headers['Content-Disposition']
        assert '_FIXED.zip' in response.headers['Content-Disposition']
        with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
            assert archive.testzip() is None
        assert calls == []
        response = client.open(studio.origin + '/api/review-file')
        assert json.load(response)['edits']['0:1']['reviewed']
    finally:
        studio.close()


def test_formatting_only_reference_does_not_require_every_question_to_be_approved(tmp_path):
    from test_quiz_repair import _fixture
    from quiz_repair import repair_package
    moodle, original = _fixture(tmp_path)
    candidate = tmp_path / 'candidate.zip'
    repair_package(moodle, original, candidate)
    # The synthetic matching question omitted its point metadata; supply it explicitly.
    with zipfile.ZipFile(candidate) as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    files['questiondb.xml'] = files['questiondb.xml'].replace(b'<fieldentry>Matching</fieldentry></qti_metadatafield>', b'<fieldentry>Matching</fieldentry></qti_metadatafield><qti_metadatafield><fieldlabel>qmd_weighting</fieldlabel><fieldentry>2</fieldentry></qti_metadatafield>')
    reference = tmp_path / 'reference.zip'
    with zipfile.ZipFile(candidate, 'w') as archive:
        for name, blob in files.items(): archive.writestr(name, blob)
    files['questiondb.xml'] = files['questiondb.xml'].replace(b'<mattext>Choose two</mattext>', b'<mattext>&lt;p&gt;Choose two&lt;/p&gt;</mattext>')
    with zipfile.ZipFile(reference, 'w') as archive:
        for name, blob in files.items(): archive.writestr(name, blob)
    w = QuizWorkspace(tmp_path / 'reviews'); w.load(str(candidate), str(reference))
    assert not any(row['needs_decision'] for row in w.snapshot()['rows'])
    assert w.snapshot()['blocked'] == 0
    w.export(tmp_path / 'fixed.zip')


def test_moodle_reference_can_restore_pictures_without_an_extra_zip(tmp_path):
    from test_quiz_repair import _fixture, _png
    moodle, candidate = _fixture(tmp_path)
    w = QuizWorkspace(tmp_path / 'reviews'); w.load(str(candidate), str(moodle))
    assert w.assets['Content/image.png'] == _png('red')
    assert 'recovered' in w.image_note


def test_version_confirmation_cannot_fix_blank_answers(tmp_path):
    w, _ = prepared(tmp_path)
    with pytest.raises(ValueError, match='Blank answer'):
        w.edit('0:1', {'variant': 'current', 'reviewed': True})
    assert w.snapshot()['blocked'] > 0
    assert not w.detail('0:1')['variants'][0]['can_use']


def test_ambiguous_moodle_pictures_are_not_guessed(tmp_path):
    from test_quiz_repair import _fixture
    moodle, candidate = _fixture(tmp_path, ambiguous=True)
    workspace = QuizWorkspace(tmp_path / 'reviews'); workspace.load(str(candidate), str(moodle))
    assert 'Content/image.png' not in workspace.assets
    assert 'could not be recovered safely' in workspace.image_note


def test_download_during_an_upload_cannot_clear_the_busy_guard(tmp_path):
    workspace, _ = prepared(tmp_path)
    studio = QuizStudio(tmp_path / 'reviews', lambda field: None); studio.workspace = workspace
    try:
        client = client_for(studio); studio.busy = True
        with pytest.raises(urllib.error.HTTPError) as error:
            client.open(studio.origin + '/api/review-file')
        assert error.value.code == 400 and studio.busy
    finally:
        studio.busy = False; studio.close()


def test_new_file_selection_cannot_export_the_previous_course(tmp_path):
    workspace, candidate = prepared(tmp_path)
    studio = QuizStudio(tmp_path / 'reviews', lambda field: None)
    studio.workspace = workspace; studio.files.update(workspace.paths)
    studio.receive_upload('candidate', 'new-course.zip', io.BytesIO(candidate.read_bytes()), candidate.stat().st_size, 0)
    assert studio.snapshot()['files_changed']
    with pytest.raises(ValueError, match='Start checking'):
        studio.export_for_browser()
    assert studio.download_path is None


def test_unused_blank_library_questions_appear_before_the_download_step(tmp_path):
    from quiz_repair import _xml
    import xml.etree.ElementTree as ET
    candidate, pictures = fixture(tmp_path)
    with zipfile.ZipFile(candidate) as archive: files = {n: archive.read(n) for n in archive.namelist()}
    bank = _xml(files['questiondb.xml'])
    extra = copy.deepcopy(next(i for i in bank.iter('item') if i.get('label') == 'bad2'))
    extra.set('label', 'unused'); extra.set('title', 'Unused copy')
    for field in extra.findall('.//qti_metadatafield'):
        if field.findtext('fieldlabel') == 'qmd_globalid': field.find('fieldentry').text = 'unused-only'
    bank.find('objectbank').append(extra); files['questiondb.xml'] = ET.tostring(bank)
    with zipfile.ZipFile(candidate, 'w') as archive:
        for name, data in files.items(): archive.writestr(name, data)
    workspace = QuizWorkspace(tmp_path / 'reviews'); workspace.load(str(candidate), pictures=str(pictures))
    assert any('Unused library question' in error for error in workspace.snapshot()['global_errors'])


def test_manual_formatting_edit_preserves_partial_credit_and_wrong_answer_penalty(tmp_path):
    from quiz_repair import _xml
    import xml.etree.ElementTree as ET
    candidate, pictures = fixture(tmp_path)
    with zipfile.ZipFile(candidate) as archive: files = {n: archive.read(n) for n in archive.namelist()}
    root = _xml(files['questiondb.xml'])
    item = next(i for i in root.iter('item') if i.get('label') == 'bad3')
    setters = item.findall('resprocessing/respcondition/setvar')
    setters[0].text = '-25'; setters[1].text = '50'
    files['questiondb.xml'] = ET.tostring(root)
    with zipfile.ZipFile(candidate, 'w') as archive:
        for name, data in files.items(): archive.writestr(name, data)
    workspace = QuizWorkspace(tmp_path / 'reviews'); workspace.load(str(candidate), pictures=str(pictures))
    workspace.edit('0:1', {'variant':'library:good2', 'reviewed':True})
    edit = copy.deepcopy(workspace.chosen('0:2')); edit['raw_prompt'] = '<p>Lateral blades</p>'
    for answer in edit['choices']: answer.pop('credit', None)
    workspace.edit('0:2', {'variant':'current','record':edit,'reviewed':True})
    output = tmp_path / 'preserved.zip'; workspace.export(output)
    with zipfile.ZipFile(output) as archive:
        item = next(i for i in _xml(archive.read('questiondb.xml')).iter('item') if i.get('label') == 'bad3')
        assert [s.text for s in item.findall('resprocessing/respcondition/setvar')] == ['-25', '50']
