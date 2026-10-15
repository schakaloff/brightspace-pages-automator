"""Reviewed same-course copies. Originals are never updated or deleted."""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from bs4 import BeautifulSoup
from transfer_api import TransferAPI
from transfer_files import FileBundle
from transfer_plan import all_objects, digest, norm
from transfer_runner import PlanLock, TransferRunner, load_plan, save_plan


def properties(node):
    return {k: v for k, v in node.items() if k not in ('children', 'Structure', 'LastModifiedDate')}


def content_kind(node):
    if node['Type'] == 0: return 'Section'
    if node.get('ActivityType', 1) not in (1, 2): return 'Existing activity link'
    if node.get('TopicType') == 3: return 'Link'
    return 'Page' if re.search(r'\.html?(?:[?#]|$)', node.get('Url', ''), re.I) else 'File'


def description(node):
    rich = node.get('Description') or {}
    markup = rich.get('Html', rich.get('Content', '')) if isinstance(rich, dict) else ''
    soup = BeautifulSoup(markup or '', 'html.parser')
    for text in list(soup.find_all(string=True)):
        if text.parent and str(text).strip().startswith(('bpa-transfer:', 'bpa-copy:')) and 'display:none' in text.parent.get('style', '').replace(' ', ''):
            text.parent.decompose()
    return str(soup)


def selected_roots(course, selected):
    chosen = set(map(str, selected))
    found = {str(n['Id']) for n in all_objects(course['modules'])}
    if not chosen: raise ValueError('Choose at least one section or page.')
    if chosen - found: raise ValueError('Some selected content is no longer available. Load the course again.')
    roots = []
    def walk(entries):
        for entry in entries:
            if str(entry['Id']) in chosen: roots.append(entry)
            else: walk(entry.get('children', []))
    walk(course['modules'])
    return roots


def make_copy_plan(course, selected, destination=None, copies=1, name='', include_nested=True, excluded=()):
    """Freeze the visible proposal, including omissions and collision-free names."""
    if type(copies) is not int or not 1 <= copies <= 20:
        raise ValueError('Choose between 1 and 20 copies.')
    if len(name.strip()) > 150: raise ValueError('Copy names must be 150 characters or fewer.')
    roots = selected_roots(course, selected)
    excluded = set(map(str, excluded))
    destination = str(destination) if destination is not None else None
    objects = {str(n['Id']): n for n in all_objects(course['modules'])}
    if destination is not None and (destination not in objects or objects[destination]['Type'] != 0):
        raise ValueError('Choose an existing destination section.')
    if destination is None and any(n['Type'] != 0 for n in roots):
        raise ValueError('Choose a destination section for individual pages, files or links.')
    if destination is not None and any(destination in {str(n['Id']) for n in all_objects([root])} for root in roots):
        raise ValueError('Choose a destination outside the section you are copying.')
    siblings = course['modules'] if destination is None else objects[destination].get('children', [])
    reserved = {norm(n['Title']) for n in siblings}
    plan = {'schema': 1, 'kind': 'duplicate', 'id': uuid4().hex, 'course': copy.deepcopy(course),
            'selected': list(map(str, selected)), 'excluded': sorted(excluded), 'destination': destination,
            'copies': copies, 'name': name.strip(), 'include_nested': bool(include_nested),
            'roots': [], 'journal': {}, 'report': '', 'reviewed': False}
    def build(node, group, title=None):
        if node.get('IsBroken'):
            raise ValueError(f"{node['Title']}: this source item is broken. Fix it in Brightspace or uncheck it.")
        if node['Type'] == 1 and node.get('ActivityType', 1) in (1, 2) and node.get('TopicType') not in (1, 3):
            raise ValueError(f"{node['Title']}: this file format needs Brightspace's own copy tool. Uncheck it to continue.")
        result = {'source': properties(copy.deepcopy(node)), 'title': title or node['Title'], 'group': group,
                  'key': f"c{group}:{node['Id']}", 'children': []}
        if include_nested:
            result['children'] = [build(child, group) for child in node.get('children', []) if str(child['Id']) not in excluded]
        return result
    for group in range(copies):
        for root in roots:
            base = name.strip() if len(roots) == 1 and name.strip() else root['Title'] + ' — Copy'
            base = base[:150]
            title, number = base, 1
            while norm(title) in reserved:
                number += 1
                suffix = f' ({number})'
                title = base[:150 - len(suffix)] + suffix
            reserved.add(norm(title))
            plan['roots'].append(build(root, group, title))
    if len(list(copy_nodes(plan))) > 2000: raise ValueError('This batch is too large. Choose fewer sections or copies.')
    return plan


def copy_nodes(plan):
    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node['children'])
    return walk(plan['roots'])


def copy_signature(plan):
    return digest({k: plan[k] for k in ('id', 'course', 'selected', 'excluded', 'destination', 'copies', 'name', 'include_nested', 'roots')})


def copy_notes(plan):
    notes = ['New content starts hidden. Review and publish it in Brightspace.',
             'Copies have no content dates or release conditions. Check availability before publishing.']
    if any(content_kind(n['source']) == 'Existing activity link' for n in copy_nodes(plan)):
        notes.append('Activity shortcuts open the existing assignment, quiz or other activity. Its settings, rubrics, grades and submissions stay with the original activity; no new graded activity is created.')
    if not plan['include_nested']: notes.append('Sections are copied without their contents.')
    if plan['excluded']: notes.append('Unchecked content inside selected sections is omitted from the copies.')
    notes.append('Links to other existing Content topics keep pointing to those topics. Course-file dependencies are copied and rewritten.')
    return notes


def load_copy_plan(path):
    plan = load_plan(path)
    if plan.get('kind') != 'duplicate': raise ValueError('This file is not a saved duplication plan.')
    return plan


class CopyAPI(TransferAPI):
    """Allow additive same-course content writes; never PUT or DELETE originals."""
    def __init__(self, page, course_id, *, writable=False):
        super().__init__(page, course_id)
        self.writable = writable
        self.copy_folder = None

    def guard(self):
        if not self.writable: raise PermissionError('Review the duplication plan before creating copies.')

    async def _json(self, path, method='GET', payload=None):
        if method != 'GET':
            allowed = re.fullmatch(rf'/d2l/api/le/[\d.]+/{self.course_id}/content/(?:root/|modules/\d+/structure/)', path)
            folder = path == f'/d2l/api/lp/1.49/{self.course_id}/managefiles/folder' and payload and payload.get('RelativePath') == self.copy_folder
            if method != 'POST' or not (allowed or folder):
                raise PermissionError('Duplication can only add new content and its own files.')
        return await super()._json(path, method, payload)

    async def upload_file(self, folder, filename, body, content_type, overwrite=False):
        if folder != self.copy_folder or overwrite: raise PermissionError('Uploads must stay in this copy’s new folder.')
        return await super().upload_file(folder, filename, body, content_type, overwrite=False)

    async def inventory(self, log=None, stop=None, include_html=False):
        details = await self._json(f'/d2l/api/lp/1.49/courses/{self.course_id}')
        root = await self.course_root()
        seen = set()
        async def walk(parent=None):
            result = []
            for entry in await self.children(parent):
                if stop and stop.is_set(): raise InterruptedError('Loading stopped.')
                id_ = str(entry['Id'])
                if not id_.isdigit() or id_ in seen: raise ValueError('Invalid or repeated content identifier.')
                seen.add(id_)
                kind = 'modules' if entry['Type'] == 0 else 'topics'
                full = await self._json(f'/d2l/api/le/1.82/{self.course_id}/content/{kind}/{id_}')
                node = {**entry, **full}; node.pop('Structure', None)
                if node['Type'] == 0: node['children'] = await walk(id_)
                result.append(node)
            return result
        modules = await walk()
        if log: log(f"Loaded {details['Name']}: {len(seen)} sections and topics.", 'info')
        return {'id': self.course_id, 'name': details['Name'], 'root': root, 'modules': modules}


class CopyRunner:
    # Share the already-tested durable pending/verified/rejected request journal.
    operation = TransferRunner.operation

    def __init__(self, api, plan, path, log=None, stop=None):
        self.api, self.plan, self.path = api, plan, Path(path)
        self.log, self.stop = log or (lambda *args: None), stop
        self.journal = plan['journal']
        self.bundles, self.prepared = {}, {}

    def persist(self): save_plan(self.plan, self.path)

    def stopped(self):
        if self.stop and self.stop.is_set(): raise InterruptedError('Stopped after the current request. Resume this saved plan.')

    def folder(self, group): return f"bpa-copy-{self.plan['id']}-{group}"

    async def prepare(self):
        async def download(url):
            path = urlparse(url).path
            if not path.startswith(self.plan['course']['root']):
                raise ValueError('A course file points to another course. Resolve that reference before copying.')
            self.stopped()
            return await self.api.download(url)
        for node in copy_nodes(self.plan):
            self.stopped()
            group = node['group']
            bundle = self.bundles.setdefault(group, FileBundle(download, urlparse(self.api.base).hostname,
                self.plan['course']['root'], self.folder(group), []))
            original = node['source']
            markup = description(original)
            desc = await bundle.page(markup, self.api.base + self.plan['course']['root']) if markup else ''
            url = None
            if original['Type'] == 1:
                if content_kind(original) == 'Existing activity link':
                    candidate = urljoin(self.api.base, original.get('Url') or '')
                    url = candidate if original.get('Url') and urlparse(candidate).scheme in ('http', 'https') else f"{self.api.base}/d2l/le/lessons/{self.api.course_id}/topics/{original['Id']}"
                else:
                    if not original.get('Url'): raise ValueError(f"{node['title']}: source URL is missing.")
                    url = urljoin(self.api.base, original['Url'])
                    if urlparse(url).scheme not in ('http', 'https'): raise ValueError('Unsupported source link scheme.')
                    if original.get('TopicType') == 1 and not bundle.split_course_file(url):
                        raise ValueError(f"{node['title']}: file is not stored in the course’s Content folder.")
                    url = await bundle.add(url)
            self.prepared[node['key']] = {'description': desc, 'url': url}
        hashes = {f'{group}:{url}': item['source_hash'] for group, bundle in self.bundles.items() for url, item in bundle.files.items()}
        if self.plan.get('source_files') is not None and self.plan['source_files'] != hashes:
            raise ValueError('An original file changed since this copy started. Leaving the copies unchanged.')
        self.plan['source_files'] = hashes
        self.persist()

    async def upload(self):
        for group, bundle in self.bundles.items():
            self.api.copy_folder = self.folder(group)
            for original, item in bundle.files.items():
                self.stopped()
                relative = self.api.copy_folder + '/' + item['filename']
                key = f'file:{group}:{item["filename"]}'
                async def find(relative=relative, item=item):
                    if not await self.api.file_exists(relative): return None
                    if await self.api.file_hash(relative) != item['hash']: raise ValueError('A copied file was edited. Leaving it unchanged.')
                    return {'url': self.plan['course']['root'] + relative, 'hash': item['hash']}
                if key not in self.journal and await find(): raise ValueError('Copy folder exists without its saved journal. Recover the original plan.')
                async def action(item=item):
                    await self.api.ensure_files_folder(self.api.copy_folder)
                    url = await self.api.upload_file(self.api.copy_folder, item['filename'], item['body'], item['type'])
                    return {'url': url}
                await self.operation(key, action, find)

    def payload(self, node):
        source, prepared = node['source'], self.prepared[node['key']]
        marker = f"bpa-copy:{self.plan['id']}:{node['key']}"
        result = {'Title': node['title'], 'ShortTitle': source.get('ShortTitle') or '', 'Type': source['Type'],
                  'Description': {'Content': prepared['description'] + f'<p style="display:none" aria-hidden="true">{marker}</p>', 'Type': 1},
                  'IsHidden': True, 'IsLocked': False}
        if source['Type'] == 0:
            result.update(ModuleStartDate=None, ModuleEndDate=None, ModuleDueDate=None)
        else:
            own_file = urlparse(prepared['url']).path.startswith(self.plan['course']['root'] + self.folder(node['group']) + '/')
            result.update(TopicType=1 if own_file and source.get('TopicType') == 1 else 3, Url=prepared['url'], StartDate=None, EndDate=None, DueDate=None,
                          OpenAsExternalResource=bool(source.get('OpenAsExternalResource')))
        return result

    async def create_node(self, node, parent):
        key = node['key']
        marker = f"bpa-copy:{self.plan['id']}:{key}"
        payload = self.payload(node)
        async def find():
            result = await self.api.find_owned(parent, marker)
            if result:
                if str(result['Id']) in self.original_ids: raise ValueError('Ownership marker belongs to original content. No changes made.')
                for field in ('Title', 'Type', 'IsHidden', 'Url', 'TopicType'):
                    if field in payload and result.get(field) != payload[field]:
                        raise ValueError(f"Copied content was edited or differs: {node['title']}. Leaving it unchanged.")
                if description(result) != prepared_description(self.prepared[key]['description']):
                    raise ValueError(f"Copied description was edited: {node['title']}. Leaving it unchanged.")
            return result
        if key not in self.journal and await find(): raise ValueError('Copies exist without the saved journal. Reopen the original plan.')
        async def action():
            if any(norm(n['Title']) == norm(node['title']) for n in await self.api.children(parent)):
                # Duplicate authored child names are allowed inside an owned new section.
                if parent not in self.new_ids: raise ValueError('The destination gained content with this name. Build a new proposal.')
            return await self.api.create_object(parent, payload)
        result = await self.operation(key, action, find)
        self.new_ids.add(result['Id'])
        expected = []
        for child in node['children']:
            created = await self.create_node(child, result['Id']); expected.append(created['Id'])
        if node['source']['Type'] == 0 and [n['Id'] for n in await self.api.children(result['Id'])] != expected:
            raise ValueError(f"Copy contents or order differ: {node['title']}. Inspect the saved copies before resuming.")
        return result

    async def check_originals(self, fresh):
        fresh_nodes = {str(n['Id']): n for n in all_objects(fresh['modules'])}
        lookup = {id_: properties(n) for id_, n in fresh_nodes.items()}
        original_nodes = {str(n['Id']): n for n in all_objects(self.plan['course']['modules'])}
        for node in copy_nodes(self.plan):
            id_ = str(node['source']['Id'])
            if lookup.get(id_) != node['source']:
                raise ValueError('Original content changed. Load the course again before making new copies.')
            if node['source']['Type'] == 0 and [str(n['Id']) for n in fresh_nodes[id_]['children']] != [str(n['Id']) for n in original_nodes[id_]['children']]:
                raise ValueError('Original section contents changed. Load the course again before making new copies.')

    async def run(self, confirmed_course):
        if str(confirmed_course) != str(self.plan['course']['id']) or self.api.course_id != str(confirmed_course):
            raise ValueError('The confirmed course does not match this duplication plan.')
        if not self.plan.get('reviewed'): raise ValueError('Review the copies before creating them.')
        rebuilt = make_copy_plan(self.plan['course'], self.plan['selected'], self.plan['destination'], self.plan['copies'],
                                 self.plan['name'], self.plan['include_nested'], self.plan['excluded'])
        if rebuilt['roots'] != self.plan['roots']: raise ValueError('The saved proposal changed. Build it again.')
        signature = copy_signature(self.plan)
        if self.plan.get('run_signature') and signature != self.plan['run_signature']:
            raise ValueError('An active copy plan cannot be edited. Reopen its saved plan.')
        fresh = await self.api.inventory(self.log, self.stop)
        await self.check_originals(fresh)
        self.original_ids = {str(n['Id']) for n in all_objects(self.plan['course']['modules'])}
        self.new_ids = set()
        destination = self.plan['destination']
        if destination is not None and not any(str(n['Id']) == destination and n['Type'] == 0 for n in all_objects(fresh['modules'])):
            raise ValueError('The destination section no longer exists.')
        siblings = fresh['modules'] if destination is None else next(n for n in all_objects(fresh['modules']) if str(n['Id']) == destination)['children']
        for root in self.plan['roots']:
            marker = f"bpa-copy:{self.plan['id']}:{root['key']}"
            if any(norm(n['Title']) == norm(root['title']) and marker not in (n.get('Description') or {}).get('Html', '') for n in siblings):
                raise ValueError('The destination gained a conflicting copy name. Build a new proposal.')
        if not self.plan.get('backup'):
            backup = self.path.with_suffix('.recovery.json')
            save_plan({'schema': 1, 'journal': {}, 'course': fresh}, backup)
            self.plan['backup'] = str(backup)
        self.plan['run_signature'] = signature
        self.persist()
        try:
            await self.prepare()  # All file/link preparation precedes mutations.
            await self.upload()
            for root in self.plan['roots']: await self.create_node(root, destination)
            await self.check_originals(await self.api.inventory())
            for group, bundle in self.bundles.items():
                for url, item in bundle.files.items():
                    body, _ = await self.api.download(url)
                    if hashlib.sha256(body).hexdigest() != item['source_hash']: raise ValueError('An original file changed during copying.')
            self.plan['complete'] = True
            self.plan['report'] = '\n'.join(['COPY COMPLETE', f"Course: {fresh['name']}",
                f"Created and verified {len(list(copy_nodes(self.plan)))} hidden sections/topics.",
                'Original content and downloaded source files were checked unchanged.', *copy_notes(self.plan), '',
                *[f"{n['title']}: {self.journal[n['key']]['result']['Id']}" for n in copy_nodes(self.plan)],
                '', 'Saved plan: ' + str(self.path), 'Recovery inventory: ' + self.plan['backup']])
        except BaseException as exc:
            self.plan['complete'] = False
            self.plan['report'] = 'COPY INCOMPLETE\n' + str(exc).split('Call log:')[0] + '\nResume this same saved plan. Copies remain hidden.'
            raise
        finally:
            self.persist()
            self.path.with_suffix('.report.txt').write_text(self.plan['report'], encoding='utf-8')
        return self.plan


def prepared_description(markup):
    return description({'Description': {'Html': markup}})


async def scan_copy_course(page, course_id, log=None, stop=None):
    return await CopyAPI(page, course_id).inventory(log, stop)


async def apply_copy(page, plan, path, confirmed_course, log=None, stop=None):
    with PlanLock(path):
        disk = load_copy_plan(path)
        if disk['id'] != plan['id'] or digest(disk['journal']) != digest(plan['journal']) or copy_signature(disk) != copy_signature(plan):
            raise ValueError('The saved journal changed. Reopen the copy plan.')
        api = CopyAPI(page, plan['course']['id'], writable=True)
        return await CopyRunner(api, plan, path, log, stop).run(confirmed_course)
