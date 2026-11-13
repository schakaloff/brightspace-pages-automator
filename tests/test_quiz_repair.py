import base64
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import xml.etree.ElementTree as ET
import zipfile

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from quiz_repair import repair_package, _xml, _correct_ids, NS


def _png(color):
    data = io.BytesIO()
    Image.new('RGB', (2, 2), color).save(data, format='PNG')
    return data.getvalue()


def _fixture(tmp_path, *, ambiguous=False, unrelated=False, unsafe=False):
    image = base64.b64encode(_png('red')).decode()
    extra = ''
    if ambiguous:
        other = base64.b64encode(_png('blue')).decode()
        extra = f'<question><name>Diagram</name><qtype>description</qtype><questiontext>&lt;img src="data:image/png;base64,{other}"&gt;</questiontext></question>'
    qtext = 'Unrelated question' if unrelated else 'Choose two'
    source = f'''<question_categories><question><name>Diagram</name><qtype>description</qtype><questiontext>&lt;img src="data:image/png;base64,{image}"&gt;</questiontext></question>
    <question><name>Q1</name><qtype>multichoice</qtype><questiontext>{qtext}</questiontext><single>0</single><answers>
    <answer><answertext>A</answertext><fraction>0.5</fraction></answer><answer><answertext>B</answertext><fraction>0.5</fraction></answer>
    <answer><answertext>C</answertext><fraction>0</fraction></answer><answer><answertext>D</answertext><fraction>0</fraction></answer></answers></question>
    <question><name>Q2</name><qtype>match</qtype><questiontext>Match</questiontext><matches>
    <match><questiontext>One</questiontext><answertext>First</answertext></match><match><questiontext>Two</questiontext><answertext>Second</answertext></match></matches></question>{extra}</question_categories>'''
    moodle = tmp_path / 'source.mbz'
    with tarfile.open(moodle, 'w:gz') as t:
        for name, data in [('questions.xml', source.encode()), ('files.xml', b'<files/>')]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    options = ''.join(f'<response_label ident="{v}"><material><mattext>{v}</mattext></material></response_label>' for v in 'ABCD')
    ms = f'''<item label="multi" title="Q1"><itemmetadata><qtimetadata><qti_metadatafield><fieldlabel>qmd_questiontype</fieldlabel><fieldentry>Multi-Select</fieldentry></qti_metadatafield><qti_metadatafield><fieldlabel>qmd_weighting</fieldlabel><fieldentry>1</fieldentry></qti_metadatafield></qtimetadata></itemmetadata>
    <presentation><flow><material><mattext>Choose two</mattext></material><response_extension><d2l_2p0:grading_type>0</d2l_2p0:grading_type></response_extension>
    <response_lid ident="LID" rcardinality="Multiple"><render_choice>{options}</render_choice></response_lid></flow></presentation>
    <resprocessing><respcondition title="Scoring for the correct answers"><conditionvar><varequal respident="LID">A</varequal><varequal respident="LID">B</varequal><not><varequal respident="LID">C</varequal><varequal respident="LID">D</varequal></not></conditionvar><setvar action="Add">1</setvar></respcondition>
    <respcondition title="Scoring for the incorrect answers"><setvar action="Add">0</setvar></respcondition></resprocessing></item>'''
    rows = ''.join(f'<response_grp respident="R{idx}"><material><mattext>{stem}</mattext></material><render_choice><response_label ident="F"><material><mattext>First</mattext></material></response_label><response_label ident="S"><material><mattext>Second</mattext></material></response_label></render_choice></response_grp>' for idx, stem in enumerate(['One', 'Two']))
    matching = f'''<item label="matching" title="Q2"><itemmetadata><qtimetadata><qti_metadatafield><fieldlabel>qmd_questiontype</fieldlabel><fieldentry>Matching</fieldentry></qti_metadatafield></qtimetadata></itemmetadata><presentation><flow><material><mattext>Match</mattext></material><response_extension><d2l_2p0:grading_type>1</d2l_2p0:grading_type></response_extension>{rows}</flow></presentation><resprocessing><respcondition><setvar varname="que_score" action="Set">0</setvar></respcondition></resprocessing></item>'''
    library = f'<questestinterop xmlns:d2l_2p0="{NS["d"]}"><objectbank><section title="Diagram"><presentation_material><material><mattext>&lt;img src="Content/image.png"&gt;</mattext></material></presentation_material></section>{ms}{matching}</objectbank></questestinterop>'
    package = tmp_path / 'input.zip'
    with zipfile.ZipFile(package, 'w') as z:
        z.writestr('imsmanifest.xml', '<manifest/>')
        z.writestr('questiondb.xml', library)
        z.writestr('quiz_d2l_1.xml', '<questestinterop><assessment title="Test"><itemref linkrefid="multi"/><itemref linkrefid="matching"/></assessment></questestinterop>')
        z.writestr('unchanged.txt', b'keep this exactly')
        if unsafe:
            z.writestr('../escape.txt', 'bad path')
    return moodle, package


def test_repairs_images_and_scoring_without_promoting_negated_choices(tmp_path):
    moodle, package = _fixture(tmp_path)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    output = tmp_path / 'corrected.zip'
    report = repair_package(moodle, package, output)
    assert report['images_restored'] == 1
    with zipfile.ZipFile(output) as z:
        items = ET.fromstring(z.read('questiondb.xml')).findall('.//item')
        assert _correct_ids(items[0]) == {'A', 'B'}
        assert items[0].findtext('.//d:grading_type', namespaces=NS) == '3'
        wrong = {c.findtext('conditionvar/varequal') for c in items[0].findall('resprocessing/respcondition') if c.find('setvar').get('varname') == 'D2L_Incorrect'}
        assert wrong == {'C', 'D'}
        assert items[1].findtext('.//d:grading_type', namespaces=NS) == '0'
        positive = [(c.find('conditionvar/varequal').get('respident'), c.findtext('conditionvar/varequal')) for c in items[1].findall('resprocessing/respcondition') if c.find('setvar').get('varname') == 'D2L_Correct']
        assert positive == [('R0', 'F'), ('R1', 'S')]
        assert z.read('Content/image.png') == _png('red')
        with zipfile.ZipFile(package) as original:
            for name in ('imsmanifest.xml', 'quiz_d2l_1.xml', 'unchanged.txt'):
                assert z.read(name) == original.read(name)
    assert hashlib.sha256(package.read_bytes()).hexdigest() == digest


def test_second_repair_is_idempotent(tmp_path):
    moodle, package = _fixture(tmp_path)
    first, second = tmp_path / 'first.zip', tmp_path / 'second.zip'
    repair_package(moodle, package, first)
    result = repair_package(moodle, first, second)
    assert result['changes'] == []
    with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
        assert a.namelist() == b.namelist()
        assert all(a.read(n) == b.read(n) for n in a.namelist())


@pytest.mark.parametrize('options,error', [
    ({'ambiguous': True}, 'ambiguous'),
    ({'unrelated': True}, 'Cannot match'),
    ({'unsafe': True}, 'unsafe file path'),
])
def test_unsafe_or_unmatched_input_produces_no_output(tmp_path, options, error):
    moodle, package = _fixture(tmp_path, **options)
    output = tmp_path / 'corrected.zip'
    with pytest.raises(ValueError, match=error):
        repair_package(moodle, package, output)
    assert not output.exists()


def test_existing_files_are_not_overwritten(tmp_path):
    moodle, package = _fixture(tmp_path)
    data = package.read_bytes()
    with pytest.raises(ValueError, match='never overwritten'):
        repair_package(moodle, package, package)
    assert package.read_bytes() == data


def test_xml_entities_are_rejected():
    with pytest.raises(ValueError, match='entity declarations'):
        _xml(b'<!DOCTYPE root [<!ENTITY x "expanded">]><root>&x;</root>')


def test_wrong_answer_key_is_not_silently_accepted(tmp_path):
    moodle, package = _fixture(tmp_path)
    with zipfile.ZipFile(package) as z:
        values = {n: z.read(n) for n in z.namelist()}
    values['questiondb.xml'] = values['questiondb.xml'].replace(b'<varequal respident="LID">B</varequal><not>', b'<varequal respident="LID">C</varequal><not>')
    with zipfile.ZipFile(package, 'w') as z:
        for name, data in values.items():
            z.writestr(name, data)
    with pytest.raises(ValueError, match='Correct answers differ'):
        repair_package(moodle, package, tmp_path / 'output.zip')


def test_panel_worker_success_error_and_input_change(qtbot, monkeypatch, tmp_path):
    from panels import quiz_repair_panel as ui
    moodle, package = _fixture(tmp_path)
    panel = ui.QuizRepairPanel()
    qtbot.addWidget(panel)
    panel._moodle.setText(str(moodle))
    panel._package.setText(str(package))
    assert panel._run.isEnabled()
    output = tmp_path / 'gui.zip'
    panel._begin(str(moodle), str(package), str(output))
    qtbot.waitUntil(lambda: not panel._busy, timeout=10000)
    assert panel._result['images_restored'] == 1 and output.exists()
    assert 'Structural checks passed' in panel._status.text()
    assert panel._details.isHidden()
    panel._show_details.click()
    assert not panel._details.isHidden()
    panel._show_details.click()
    assert panel._details.isHidden()
    panel._package.setText(str(tmp_path / 'missing.zip'))
    assert panel._result is None and not panel._run.isEnabled()
    panel._package.setText(str(package))
    panel._begin(str(moodle), str(package), str(package))
    qtbot.waitUntil(lambda: not panel._busy, timeout=10000)
    assert panel._result is None and 'Repair stopped' in panel._status.text()


def test_report_cannot_overwrite_the_saved_zip(qtbot, monkeypatch, tmp_path):
    from panels import quiz_repair_panel as ui
    moodle, package = _fixture(tmp_path)
    original = package.read_bytes()
    panel = ui.QuizRepairPanel()
    qtbot.addWidget(panel)
    panel._moodle.setText(str(moodle))
    panel._package.setText(str(package))
    panel._result = {'output': str(package)}
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', lambda *args: (str(package), ''))
    panel._save_report()
    assert package.read_bytes() == original
    assert 'could not be saved' in panel._status.text()


def test_tool_is_available_in_existing_navigation(qtbot, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from gui import MainWindow
    for method in ('_set_window_icon', '_load_api_key', '_start_chromium_check', '_start_update_check', '_show_welcome_if_needed'):
        monkeypatch.setattr(MainWindow, method, lambda self: None)
    monkeypatch.setattr(MainWindow, 'load_config', lambda self: {'welcomed': True})
    monkeypatch.setattr(MainWindow, 'save_config', lambda self, data: None)
    monkeypatch.setattr(MainWindow, 'closeEvent', lambda self, event: event.accept())
    window = MainWindow()
    qtbot.addWidget(window)
    opened = []
    monkeypatch.setattr(window._quiz_repair, 'open_studio', lambda: opened.append(True))
    window._build_hub.buttons['quiz_repair'].click()
    assert opened == [True]


def _change_xml(package, edit):
    with zipfile.ZipFile(package) as z:
        members = {name: z.read(name) for name in z.namelist()}
    members['questiondb.xml'] = edit(members['questiondb.xml'])
    with zipfile.ZipFile(package, 'w') as z:
        for name, data in members.items():
            z.writestr(name, data)


def test_blank_choices_stop_output_instead_of_matching_bad_source(tmp_path):
    from quiz_repair import _visible_content
    moodle, package = _fixture(tmp_path)
    _change_xml(package, lambda value: value.replace(b'<mattext>C</mattext>', b'<mattext>&lt;p&gt;&lt;br/&gt;&lt;/p&gt;</mattext>'))
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    output = tmp_path / 'blocked.zip'
    with pytest.raises(ValueError, match='blank answer choices 3'):
        repair_package(moodle, package, output)
    assert not output.exists()
    assert hashlib.sha256(package.read_bytes()).hexdigest() == digest
    assert not _visible_content('<p>&nbsp;<br></p>')
    assert not _visible_content('<span>\u200b</span>')
    assert _visible_content('<img src="Content/choice.png">')


def test_brightspace_comparison_detects_missing_option_and_two_blanks(tmp_path):
    from quiz_review import inspect_exports, write_review_report
    _, candidate = _fixture(tmp_path)
    reference = tmp_path / 'real-course.zip'
    reference.write_bytes(candidate.read_bytes())
    _change_xml(reference, lambda data: data.replace(b'<response_label ident="D"><material><mattext>D</mattext></material></response_label>', b''))
    _change_xml(candidate, lambda data: data.replace(b'<mattext>C</mattext>', b'<mattext>&lt;br/&gt;</mattext>').replace(b'<mattext>D</mattext>', b'<mattext>&lt;br/&gt;</mattext>'))
    review = inspect_exports(reference, candidate)
    row = review['quizzes'][0]['questions'][0]
    assert len(row['reference']['choices']) == 3
    assert len(row['candidate']['choices']) == 4
    assert sum(f['code'] == 'blank_answer' for f in row['findings']) == 2
    assert any(f['code'] == 'answer_options' and "'C'" in f['message'] for f in row['findings'])
    assert review['blocking_count'] > 0
    path = tmp_path / 'review.html'
    write_review_report(review, path)
    report = path.read_text(encoding='utf-8')
    assert '[BLANK CHOICE]' in report and 'BLOCKED' in report
    assert 'Content-Security-Policy' in report and 'sandbox=""' in report
    assert not (tmp_path / 'corrected.zip').exists()


def test_layout_differences_are_not_hidden_by_equal_plain_text(tmp_path):
    from quiz_review import inspect_exports
    _, candidate = _fixture(tmp_path)
    reference = tmp_path / 'reference.zip'
    reference.write_bytes(candidate.read_bytes())
    _change_xml(candidate, lambda data: data.replace(b'<mattext>Choose two</mattext>', b'<mattext>&lt;p&gt;&lt;p&gt;Choose two&lt;/p&gt;&lt;br/&gt;&lt;/p&gt;</mattext>'))
    review = inspect_exports(reference, candidate)
    row = review['quizzes'][0]['questions'][0]
    assert any(f['code'] == 'html_layout' and 'nested paragraph' in f['message'] for f in row['findings'])
    assert any(f['code'] == 'html_difference' for f in row['findings'])
    assert not any(f['code'] == 'question_identity' for f in row['findings'])


def test_html_report_does_not_execute_imported_markup():
    from quiz_review import _safe_preview
    value = _safe_preview('<script>fetch("https://example.invalid")</script><img src="https://example.invalid/x" onerror="alert(1)"><p onclick="alert(1)">Text</p>')
    assert '<script' not in value and 'onclick' not in value and 'onerror' not in value
    assert 'src="https:' not in value
    assert 'Text' in value


def test_native_comparison_mode_saves_report_without_repair(qtbot, tmp_path):
    from panels.quiz_repair_panel import QuizRepairPanel
    _, candidate = _fixture(tmp_path)
    reference = tmp_path / 'reference.zip'
    reference.write_bytes(candidate.read_bytes())
    panel = QuizRepairPanel()
    qtbot.addWidget(panel)
    panel._moodle.setText(str(reference))
    panel._package.setText(str(candidate))
    assert panel._review.isEnabled() and not panel._run.isEnabled()
    report = tmp_path / 'review.html'
    panel._begin(str(reference), str(candidate), str(report), review_only=True)
    qtbot.waitUntil(lambda: not panel._busy, timeout=10000)
    assert report.is_file()
    assert 'review' in panel._result and panel._folder.text() == 'Open review report'
    assert 'Review saved' in panel._status.text()
