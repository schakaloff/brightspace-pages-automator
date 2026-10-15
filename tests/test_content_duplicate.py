import copy
import hashlib
import sys
import threading
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from content_duplicate import (CopyAPI, CopyRunner, make_copy_plan, copy_nodes, content_kind,
                               copy_notes, load_copy_plan, copy_signature)
from transfer_plan import all_objects, digest
from transfer_runner import save_plan


def sample_course():
    def file(id_, title, url):
        return {'Id': id_, 'Title': title, 'Type': 1, 'TopicType': 1, 'ActivityType': 1, 'Url': url,
                'IsHidden': False, 'Description': {'Html': ''}}
    return {'id': '1', 'name': 'Example course', 'root': '/content/enforced/1-test/', 'modules': [
        {'Id': 10, 'Type': 0, 'Title': 'Workshop', 'IsHidden': False, 'Description': {'Html': '<p>Original description</p>'}, 'children': [
            file(11, 'Session Overview', '/content/enforced/1-test/page.html'),
            file(12, 'Slides', '/content/enforced/1-test/slides.pdf'),
            {'Id': 13, 'Title': 'Video', 'Type': 1, 'TopicType': 3, 'ActivityType': 2, 'Url': 'https://example.org/video', 'IsHidden': False},
            {'Id': 14, 'Title': 'Assignment', 'Type': 1, 'TopicType': 3, 'ActivityType': 3, 'ToolItemId': 42,
             'Url': '/d2l/lms/dropbox/user/folder_submit_files.d2l?db=42&ou=1', 'IsHidden': False},
            {'Id': 15, 'Type': 0, 'Title': 'Nested', 'IsHidden': False, 'children': [file(16, 'Handout', '/content/enforced/1-test/slides.pdf')]}]},
        {'Id': 30, 'Type': 0, 'Title': 'Destination', 'IsHidden': False, 'children': []}]}


class MemoryAPI:
    base = 'https://learn.okanagancollege.ca'
    course_id = '1'
    def __init__(self):
        self.course = sample_course(); self.next_id = 100; self.writes = 0; self.uploads = 0
        self.copy_folder = None; self.lose_response = False; self.block_writes = False
        self.files = {
            self.base + '/content/enforced/1-test/page.html': (b'<html><head><link rel="stylesheet" href="style.css"></head><body><h1>Authored title</h1><p>Exact authored prose.</p><img src="image.png"><a href="/d2l/le/lessons/1/topics/14">Assignment</a></body></html>', 'text/html'),
            self.base + '/content/enforced/1-test/style.css': (b'body{color:#123456;background:url("image.png")}', 'text/css'),
            self.base + '/content/enforced/1-test/image.png': (b'fake image bytes', 'image/png'),
            self.base + '/content/enforced/1-test/slides.pdf': (b'exact document bytes', 'application/pdf')}

    async def inventory(self, *args): return copy.deepcopy(self.course)
    async def children(self, parent):
        if parent is None: return copy.deepcopy(self.course['modules'])
        return copy.deepcopy(next(n for n in all_objects(self.course['modules']) if str(n['Id']) == str(parent))['children'])
    async def find_owned(self, parent, marker):
        found = [n for n in await self.children(parent) if marker in (n.get('Description') or {}).get('Html', '')]
        assert len(found) <= 1
        return found[0] if found else None
    async def create_object(self, parent, payload):
        assert not self.block_writes, 'Completed rerun attempted a POST'
        self.writes += 1; self.next_id += 1
        node = copy.deepcopy(payload); node['Id'] = self.next_id
        node['Description'] = {'Html': payload['Description']['Content']}
        if node['Type'] == 0: node['children'] = []
        else: node['ActivityType'] = 1 if node['TopicType'] == 1 else 2
        entries = self.course['modules'] if parent is None else next(n for n in all_objects(self.course['modules']) if str(n['Id']) == str(parent))['children']
        entries.append(node)
        if self.lose_response: self.lose_response = False; raise TimeoutError('Response lost after POST')
        return copy.deepcopy(node)
    async def download(self, url): return self.files[url]
    async def file_exists(self, path): return self.base + self.course['root'] + path in self.files
    async def file_hash(self, path): return hashlib.sha256(self.files[self.base + self.course['root'] + path][0]).hexdigest()
    async def ensure_files_folder(self, folder): assert folder == self.copy_folder
    async def upload_file(self, folder, filename, body, content_type):
        assert not self.block_writes, 'Completed rerun attempted an upload'
        assert folder == self.copy_folder
        self.uploads += 1
        url = self.course['root'] + folder + '/' + filename
        assert self.base + url not in self.files
        self.files[self.base + url] = body, content_type
        return url


def test_selection_copies_nested_structure_once_and_keeps_the_inventory_unchanged():
    course = sample_course(); before = digest(course)
    plan = make_copy_plan(course, ['10', '11', '12'], destination='30')
    assert len(plan['roots']) == 1 and len(list(copy_nodes(plan))) == 7
    assert plan['roots'][0]['title'] == 'Workshop — Copy'
    assert digest(course) == before


def test_partial_selection_and_sections_only_show_exact_omissions():
    plan = make_copy_plan(sample_course(), ['10'], excluded=['12', '15'])
    assert [n['source']['Id'] for n in copy_nodes(plan)] == [10, 11, 13, 14]
    assert any('omitted' in note for note in copy_notes(plan))
    empty = make_copy_plan(sample_course(), ['10'], include_nested=False)
    assert len(list(copy_nodes(empty))) == 1


def test_multiple_copies_get_distinct_names_and_file_groups_and_preserve_order():
    course = sample_course()
    course['modules'].append({'Id': 40, 'Type': 0, 'Title': 'Workshop — Copy', 'children': []})
    plan = make_copy_plan(course, ['10'], copies=2)
    assert [n['title'] for n in plan['roots']] == ['Workshop — Copy (2)', 'Workshop — Copy (3)']
    assert [n['group'] for n in plan['roots']] == [0, 1]
    assert [n['source']['Id'] for n in plan['roots'][0]['children']] == [11, 12, 13, 14, 15]


@pytest.mark.parametrize('kwargs,message', [
    ({'selected': []}, 'Choose at least'),
    ({'selected': ['99']}, 'no longer available'),
    ({'selected': ['11']}, 'destination section'),
    ({'selected': ['10'], 'destination': '15'}, 'outside'),
    ({'selected': ['10'], 'destination': '11'}, 'destination section'),
    ({'selected': ['10'], 'copies': 0}, 'between'),
    ({'selected': ['10'], 'copies': 21}, 'between'),
    ({'selected': ['10'], 'name': 'a' * 151}, '150')])
def test_invalid_proposals_are_blocked(kwargs, message):
    with pytest.raises(ValueError, match=message): make_copy_plan(sample_course(), **kwargs)


def test_content_service_file_is_not_silently_linked_instead_of_copied():
    course = sample_course(); course['modules'][0]['children'][0]['TopicType'] = 11
    with pytest.raises(ValueError, match='own copy tool'): make_copy_plan(course, ['10'])


@pytest.mark.asyncio
async def test_independent_file_copies_hidden_content_existing_activity_shortcuts_and_get_only_rerun(tmp_path):
    api = MemoryAPI(); originals = copy.deepcopy(api.course['modules'][0]); original_files = copy.deepcopy(api.files)
    plan = make_copy_plan(api.course, ['10'], copies=2); plan['reviewed'] = True
    path = tmp_path / 'copy.json'; save_plan(plan, path)
    result = await CopyRunner(api, plan, path).run('1')
    assert result['complete'] and api.writes == 14 and api.uploads == 8
    assert api.course['modules'][0] == originals
    assert all(api.files[url] == value for url, value in original_files.items())
    copies = api.course['modules'][2:]
    assert all(n['IsHidden'] for n in all_objects(copies))
    assert copies[0]['children'][0]['Url'] != copies[1]['children'][0]['Url']
    page, _ = api.files[api.base + copies[0]['children'][0]['Url']]
    assert b'Exact authored prose.' in page and b'color:#123456' in next(body for url,(body,_) in api.files.items() if 'bpa-copy-' in url and url.endswith('.css'))
    assert b'/d2l/le/lessons/1/topics/14' in page
    assert copies[0]['children'][3]['TopicType'] == 3
    assert copies[0]['children'][3]['Url'].endswith('db=42&ou=1')
    assert 'no new graded activity' in result['report']
    journal = copy.deepcopy(result['journal']); api.block_writes = True
    rerun = await CopyRunner(api, result, path).run('1')
    assert {k: v['result'].get('Id') for k,v in rerun['journal'].items()} == {k: v['result'].get('Id') for k,v in journal.items()}
    assert api.writes == 14 and api.uploads == 8


@pytest.mark.asyncio
async def test_lost_content_response_is_reconciled_without_duplicate_post(tmp_path):
    api = MemoryAPI(); plan = make_copy_plan(api.course, ['10'], include_nested=False); plan['reviewed'] = True
    path = tmp_path / 'copy.json'; save_plan(plan, path); api.lose_response = True
    with pytest.raises(TimeoutError): await CopyRunner(api, plan, path).run('1')
    assert api.writes == 1 and plan['journal']['c0:10']['state'] == 'pending'
    restored = load_copy_plan(path)
    assert (await CopyRunner(api, restored, path).run('1'))['complete']
    assert api.writes == 1


@pytest.mark.asyncio
async def test_stop_after_a_successful_mutation_resumes_without_duplicate(tmp_path):
    api = MemoryAPI(); stop = threading.Event()
    plan = make_copy_plan(api.course, ['10']); plan['reviewed'] = True; path = tmp_path / 'copy.json'; save_plan(plan, path)
    original = api.create_object
    async def stop_after_create(*args):
        node = await original(*args); stop.set(); return node
    api.create_object = stop_after_create
    with pytest.raises(InterruptedError): await CopyRunner(api, plan, path, stop=stop).run('1')
    assert api.writes == 1
    stop.clear(); api.create_object = original
    assert (await CopyRunner(api, load_copy_plan(path), path).run('1'))['complete']
    assert api.writes == 7


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['course', 'review', 'source', 'membership', 'destination', 'tamper', 'script', 'other-course-file'])
async def test_blockers_cause_zero_mutations(tmp_path, case):
    api = MemoryAPI(); plan = make_copy_plan(api.course, ['10'], destination='30'); plan['reviewed'] = case != 'review'
    if case == 'source': api.course['modules'][0]['Title'] = 'Edited source'
    if case == 'membership': api.course['modules'][0]['children'].append({'Id': 19, 'Title': 'New source page', 'Type': 1})
    if case == 'destination': api.course['modules'][1]['children'].append({'Id': 60, 'Type': 0, 'Title': 'Workshop — Copy', 'children': []})
    if case == 'tamper': plan['roots'][0]['title'] = 'Injected title'
    if case == 'script':
        api.files[api.base + '/content/enforced/1-test/page.html'] = (b'<script src="local.js"></script>', 'text/html')
        api.files[api.base + '/content/enforced/1-test/local.js'] = (b'import "another.js"', 'text/javascript')
    if case == 'other-course-file': api.files[api.base + '/content/enforced/1-test/page.html'] = (b'<img src="/content/enforced/2-other/private.png">', 'text/html')
    path = tmp_path / 'copy.json'; save_plan(plan, path)
    with pytest.raises(ValueError): await CopyRunner(api, plan, path).run('2' if case == 'course' else '1')
    assert api.writes == 0 and api.uploads == 0


@pytest.mark.asyncio
async def test_a_changed_copy_or_file_is_never_overwritten(tmp_path):
    api = MemoryAPI(); plan = make_copy_plan(api.course, ['11'], destination='30'); plan['reviewed'] = True
    path = tmp_path / 'copy.json'; save_plan(plan, path); await CopyRunner(api, plan, path).run('1')
    copied = api.course['modules'][1]['children'][0]
    api.files[api.base + copied['Url']] = b'Instructor edit', 'text/html'
    writes, uploads = api.writes, api.uploads
    with pytest.raises(ValueError, match='edited'): await CopyRunner(api, plan, path).run('1')
    assert (api.writes, api.uploads) == (writes, uploads)


@pytest.mark.asyncio
async def test_api_guard_never_allows_updates_deletion_other_courses_or_other_files():
    class Page: url = 'https://learn.okanagancollege.ca/d2l/home'
    api = CopyAPI(Page(), '1', writable=True); api.copy_folder = 'bpa-copy-test'
    for method, route in [('PUT', '/d2l/api/le/1.82/1/content/topics/11'), ('DELETE', '/d2l/api/le/1.82/1/content/topics/11'),
                          ('POST', '/d2l/api/le/1.82/2/content/root/'), ('POST', '/d2l/api/le/1.82/1/dropbox/folders/')]:
        with pytest.raises(PermissionError): await api._json(route, method, {})
    with pytest.raises(PermissionError): await api.upload_file('original-folder', 'page.html', b'text', 'text/html')
    with pytest.raises(PermissionError): CopyAPI(Page(), '1').guard()
