"""Durable H5P runs, isolated by course pair and saved without login data."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from config import USERDATA_DIR

RUNS_DIR = USERDATA_DIR / 'h5p-runs'


def clean_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password:
        raise ValueError('Enter a course URL without login details.')
    # Only resource identity belongs in a saved URL, never login/session tokens.
    query = parse_qs(parts.query)
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                      urlencode({k: query[k][0] for k in ('id', 'ou') if k in query}), ''))


class H5PRun:
    def __init__(self, path, data):
        self.path, self.data = Path(path), data
        self.on_change = None

    @classmethod
    def create(cls, bs_url, moodle_url, course_id, grade_all=False, directory=None):
        id_ = uuid4().hex
        run = cls(Path(directory or RUNS_DIR) / (id_ + '.json'), {
            'schema': 1, 'id': id_, 'bs_url': clean_url(bs_url),
            'moodle_url': clean_url(moodle_url), 'course_id': str(course_id),
            'course_name': f'Brightspace course {course_id}', 'grade_all': bool(grade_all),
            'status': 'paused', 'moodle_items': [], 'activities': [],
        })
        run.save()
        return run

    @classmethod
    def load(cls, id_, directory=None):
        if not isinstance(id_, str) or not re.fullmatch(r'[a-f0-9]{32}', id_):
            raise ValueError('Choose a saved H5P course.')
        path = Path(directory or RUNS_DIR) / (id_ + '.json')
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema') != 1 or data.get('id') != id_ or not isinstance(data.get('activities'), list):
            raise ValueError('This saved H5P run cannot be opened.')
        required = ('bs_url', 'moodle_url', 'course_id', 'course_name', 'grade_all', 'status', 'moodle_items')
        if any(key not in data for key in required) or not isinstance(data['moodle_items'], list):
            raise ValueError('This saved H5P run is incomplete.')
        for row in data['activities']:
            if not isinstance(row, dict) or any(k not in row for k in ('key', 'filename', 'name', 'should_grade', 'moodle_section', 'bs_module_title', 'bs_module_id', 'upload', 'insertion')):
                raise ValueError('A saved H5P activity is incomplete.')
        return cls(path, data)

    def save(self):
        self.data['updated'] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.json.tmp')
        with temp.open('w', encoding='utf-8') as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, self.path)
        if self.on_change:
            self.on_change(copy.deepcopy(self.data))

    def capture_moodle(self, items):
        from h5p_handler import H5PHandler
        stems = H5PHandler._download_stems(items)
        saved = []
        for item in items:
            if item.get('type') != 'SECTION' and id(item) not in stems: continue
            row = {k: item[k] for k in ('type', 'name', 'hint', 'href') if k in item}
            if row.get('href'): row['href'] = clean_url(row['href'])
            saved.append(row)
        self.data['moodle_items'] = saved
        self.save()

    def capture_assignments(self, assignments):
        """Own copies prevent another course overwriting this run's downloads."""
        if self.data['activities']: return
        from h5p_handler import H5PHandler
        names = [H5PHandler._norm_title(a['name']) for a in assignments if a['belongs_to_course']]
        if len(set(names)) != len(names):
            raise ValueError('Two H5P activities have the same name. Give them distinct names in Moodle before running this course so uploads and saved pages can be identified safely.')
        folder = self.path.parent / self.data['id'] / 'files'
        folder.mkdir(parents=True, exist_ok=True)
        for assignment in assignments:
            if not assignment['belongs_to_course']: continue
            file = Path(assignment['file'])
            cached = folder / file.name
            temp = cached.with_suffix('.h5p.tmp')
            if file.is_file():
                shutil.copyfile(file, temp)
                os.replace(temp, cached)
            row = {k: assignment[k] for k in ('name', 'should_grade', 'moodle_section', 'bs_module_title', 'bs_module_id')}
            row.update(key=file.stem, filename=file.name, upload='ready' if cached.is_file() else 'missing', insertion='ready')
            self.data['activities'].append(row)
        self.save()

    def assignments(self, bs_flat):
        modules = {str(row['id']): row for row in bs_flat if row.get('kind') == 'MODULE'}
        result = []
        for row in self.data['activities']:
            if not row.get('selected', True):
                continue
            module = modules.get(str(row['bs_module_id']))
            if not module:
                raise ValueError(f"{row['name']}: saved destination unit is missing. Run a fresh course scan.")
            if Path(row['filename']).name != row['filename']:
                raise ValueError('Invalid saved H5P filename.')
            result.append({**copy.deepcopy(row), 'file': self.path.parent / self.data['id'] / 'files' / row['filename'],
                           'belongs_to_course': True, 'bs_module_title': module['title'], 'bs_module_id': module['id']})
        return result

    def checkpoint(self, assignment, stage, state, **details):
        key = assignment.get('key') or Path(assignment['file']).stem
        row = next(a for a in self.data['activities'] if a['key'] == key)
        row[stage] = state
        row.update(details)
        self.save()

    def counts(self):
        rows = self.data['activities']
        inserted = sum(r['insertion'] == 'confirmed' for r in rows)
        return (sum(r['upload'] == 'confirmed' for r in rows), inserted, len(rows) - inserted)

    def label(self):
        uploaded, inserted, remaining = self.counts()
        moodle_id = parse_qs(urlsplit(self.data['moodle_url']).query).get('id', ['?'])[0]
        progress = f'{uploaded} uploaded · {inserted} inserted · {remaining} remaining' if self.data['activities'] else '0 H5P activities' if self.data['moodle_items'] else 'scan not finished'
        return f"{self.data['course_name']} ({self.data['course_id']}) ← Moodle {moodle_id} — {progress}"


def saved_runs(directory=None):
    result = []
    for path in Path(directory or RUNS_DIR).glob('*.json'):
        try: result.append(H5PRun.load(path.stem, directory))
        except (OSError, ValueError, KeyError, TypeError): continue
    return sorted(result, key=lambda run: run.data.get('updated', ''), reverse=True)
