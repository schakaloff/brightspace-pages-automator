import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

sys.path.insert(0, 'src')
from scorm_packages import SCORMRun, scorm_sources, safe_filename, validate_package
from scorm_handler import matching_topics

BS = 'https://learn.okanagancollege.ca/d2l/le/lessons/23840'
MOODLE = 'https://mymoodle.okanagan.bc.ca/course/view.php?id=166972'
MANIFEST = '''<manifest xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2">
<organizations><organization><title>Dental training</title></organization></organizations>
<resources><resource identifier="course" adlcp:scormtype="sco" href="index.html"/></resources></manifest>'''


def package(path, manifest=MANIFEST, filename='imsmanifest.xml'):
    with ZipFile(path, 'w') as archive:
        archive.writestr(filename, manifest)
        archive.writestr('index.html', '<html>Course</html>')
    return path


def test_validate_original_scorm_without_modifying_it(tmp_path):
    file = package(tmp_path / 'dental.zip')
    before = file.read_bytes()
    result = validate_package(file)
    assert result['package_title'] == 'Dental training'
    assert result['bytes'] == len(before) and len(result['sha256']) == 64
    assert file.read_bytes() == before


@pytest.mark.parametrize('xml,filename', [
    (MANIFEST, 'folder/imsmanifest.xml'), ('<manifest/>', 'imsmanifest.xml'),
    ('<!DOCTYPE manifest [<!ENTITY x "test">]>' + MANIFEST, 'imsmanifest.xml'),
    ('<html>Login required</html>', 'imsmanifest.xml'), ('not xml', 'imsmanifest.xml'),
])
def test_rejects_wrong_package_or_manifest(tmp_path, xml, filename):
    with pytest.raises(ValueError): validate_package(package(tmp_path / 'bad.zip', xml, filename))


@pytest.mark.parametrize('name', ['../bad.zip', r'..\bad.zip', 'C:bad.zip', 'bad.html', '/file.zip', 'file.zip:token'])
def test_download_filename_cannot_escape_cache(name):
    with pytest.raises(ValueError): safe_filename(name)


def test_scorm_inventory_is_activity_based_and_keeps_section_names():
    href = 'https://mymoodle.okanagan.bc.ca/mod/scorm/view.php?id=42'
    items = [{'type': 'SECTION', 'name': 'Dental Dam'}, {'type': 'EXTERNAL', 'name': 'Practice', 'href': href},
             {'type': 'EXTERNAL', 'name': 'Practice duplicate navigation', 'href': href},
             {'type': 'EXTERNAL', 'name': 'H5P', 'href': href.replace('/scorm/', '/hvp/')}]
    assert scorm_sources(items) == [dict(key='42', name='Practice', section='Dental Dam', href=href)]


def test_cache_isolated_by_course_pair_and_detects_changed_zip(tmp_path):
    run = SCORMRun.open(BS, MOODLE, tmp_path)
    run.capture([dict(key='42', name='Practice', section='Dental Dam', href='')],
                [dict(id=1, title='Dental Dam')])
    row = run.data['activities'][0]
    run.folder(row).mkdir(parents=True)
    file = package(run.folder(row) / 'course.zip')
    row.update(validate_package(file), filename='course.zip', upload='confirmed')
    run.save()
    reloaded = SCORMRun.open(BS + '/units/123', MOODLE, tmp_path)
    assert reloaded.cached(reloaded.data['activities'][0]) == file
    other = SCORMRun.open(BS.replace('23840', '999'), MOODLE, tmp_path)
    assert other.path != run.path and not other.data['activities']
    file.write_bytes(b'<html>login</html>')
    assert reloaded.cached(row) is None


def test_ambiguous_modules_are_not_guessed(tmp_path):
    run = SCORMRun.open(BS, MOODLE, tmp_path)
    run.capture([dict(key='42', name='A', section='Dental Dam', href='')],
                [dict(id=1, title='Dental Dam'), dict(id=2, title='Dental Dam')])
    assert not run.data['activities'][0]['module_id']


def test_only_matching_scorm_topics_count_as_present():
    row = dict(name='Practice', filename='dental.zip', package_title='Dental training')
    topics = [dict(Id=1, Title='Practice', Url='/files/practice.html'),
              dict(Id=2, Title='Practice', Url='https://mymoodle.okanagan.bc.ca/mod/scorm/view.php?id=4'),
              dict(Id=3, Title='dental', Url='d2l:brightspace:content:tenant:scorm:uuid/latest')]
    # A link back to Moodle isn't a Brightspace SCORM package.
    assert [t['Id'] for t in matching_topics(topics, row)] == [3]


def test_brightspace_encoded_titles_do_not_create_duplicate_scorms():
    row = dict(name='Matricies/Wedges & Liners (Scorm)', filename='original.zip')
    assert matching_topics([dict(Id=1, Title='Matricies/Wedges &amp; Liners (Scorm)',
        Url='d2l:brightspace:content:tenant:scorm:id/latest')], row)
