"""Kaltura plans and write evidence; browser credentials never belong here."""
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit, parse_qs, urlencode, urlunsplit

from config import USERDATA_DIR
from h5p_progress import clean_url
from transfer_storage import atomic_json

RUNS_DIR = USERDATA_DIR / 'kaltura-runs'
VERIFIED = ('created', 'already_present')
STATES = ('ready', 'preparing', 'pending', 'needs_check', 'failed', *VERIFIED)


def now():
    return datetime.now(timezone.utc).isoformat()


def placement_key(entry_id, section_key):
    return hashlib.sha256(f'{entry_id}\0{section_key}'.encode()).hexdigest()[:24]


def source_url(url):
    clean_url(url)  # Reject credentials and invalid schemes using the shared rule.
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
        urlencode({key: query[key][0] for key in ('id', 'chapterid', 'section', 'sectionid') if key in query}), ''))


def safe_problem(error):
    text = str(error).split('Call log:')[0].strip()
    def scrub(match):
        try:
            return source_url(match.group())
        except ValueError:
            return '[redacted URL]'
    return re.sub(r'''https?://[^\s"'<>]+''', scrub, text)


def validate_courses(bs_url, moodle_url):
    from content_checker import _extract_course_id
    from kaltura_categorizer import _whole_moodle_course_url
    bs = clean_url(bs_url)
    moodle = clean_url(_whole_moodle_course_url(clean_url(moodle_url)))
    course_id = _extract_course_id(bs)
    if not course_id:
        raise ValueError('Enter a Brightspace course URL containing its course ID.')
    return bs, moodle, str(course_id)


class KalturaRun:
    def __init__(self, path, data):
        self.path, self.data = Path(path), data
        self.on_change = None

    @classmethod
    def create(cls, bs_url, moodle_url, directory=None):
        bs, moodle, course = validate_courses(bs_url, moodle_url)
        id_ = uuid4().hex
        run = cls(Path(directory or RUNS_DIR) / f'{id_}.json', dict(
            schema=1, id=id_, bs_url=bs, moodle_url=moodle, course_id=course,
            course_name=f'Brightspace course {course}', created=now(), status='paused',
            scan_complete=False, scan_issues=[], rows=[], modules=[], units={}, approval=None))
        run.save()
        return run

    @classmethod
    def load(cls, id_, directory=None):
        if not isinstance(id_, str) or not re.fullmatch('[a-f0-9]{32}', id_):
            raise ValueError('Choose a saved Kaltura transfer.')
        path = Path(directory or RUNS_DIR) / f'{id_}.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('schema') != 1 or data.get('id') != id_:
            raise ValueError('This saved Kaltura transfer cannot be opened.')
        if any(key not in data for key in ('bs_url', 'moodle_url', 'course_id', 'course_name',
                                           'status', 'rows', 'modules', 'units', 'scan_complete', 'scan_issues', 'created', 'updated')):
            raise ValueError('This saved Kaltura transfer is incomplete.')
        if not isinstance(data['rows'], list) or not isinstance(data['modules'], list) or not isinstance(data['units'], dict):
            raise ValueError('Invalid Kaltura transfer data.')
        bs, moodle, course = validate_courses(data['bs_url'], data['moodle_url'])
        if (bs, moodle, course) != (data['bs_url'], data['moodle_url'], data['course_id']):
            raise ValueError('The saved course identity is invalid.')
        keys = set()
        for row in data['rows']:
            if not isinstance(row, dict) or any(k not in row for k in (
                    'key', 'entry_id', 'name', 'section_name', 'section_key', 'selected', 'state', 'destination')):
                raise ValueError('A saved Kaltura placement is incomplete.')
            if row['state'] not in STATES or row['key'] in keys or not isinstance(row['selected'], bool):
                raise ValueError('A saved Kaltura placement is invalid.')
            keys.add(row['key'])
        return cls(path, data)

    def save(self, *, touch=True):
        if touch:
            self.data['updated'] = now()
        atomic_json(self.path, self.data)
        if self.on_change:
            self.on_change(copy.deepcopy(self.data))

    def capture_scan(self, entries, issues):
        previous = {r['key']: r for r in self.data['rows']}
        rows = {}
        for entry in entries:
            sources = entry.get('sources') or [entry]
            for source in sources:
                section = source.get('section_name', entry.get('section_name', '')) or '(unnamed section)'
                section_key = str(source.get('section_key') or section)
                key = placement_key(entry['entry_id'], section_key)
                if key not in rows:
                    rows[key] = previous.get(key) or dict(key=key, entry_id=entry['entry_id'],
                        name=source.get('name') or entry['name'], section_name=section, section_key=section_key,
                        selected=False, destination=None, state='ready', sources=[])
                    if not rows[key].get('attempted'):
                        rows[key]['name'] = source.get('name') or entry['name']
                    rows[key]['sources'] = []
                url = source.get('moodle_url') or entry.get('moodle_url')
                location = dict(name=source.get('name', entry['name']), url=source_url(url)) if url else dict(name=entry['name'])
                if location not in rows[key]['sources']:
                    rows[key]['sources'].append(location)
        # Never drop saved write evidence when an inventory becomes incomplete.
        for key, row in previous.items():
            if key not in rows and row['state'] in ('pending', 'needs_check', *VERIFIED):
                row['problem'] = 'Source placement is no longer in the scan; review its saved destination.'
                rows[key] = row
        safe_issues = [dict(url=source_url(issue['url']) if issue.get('url') else '',
                            reason=safe_problem(issue.get('reason', 'Source read failed.'))) for issue in issues]
        self.data.update(rows=list(rows.values()), scan_issues=safe_issues, scan_complete=not issues, approval=None)
        self.save()

    def approve(self, choices, *, partial_ack=False):
        if not self.data['scan_complete'] and not partial_ack:
            raise ValueError('Retry unread source pages, or acknowledge that this scan is incomplete.')
        by_key = {r['key']: r for r in self.data['rows']}
        if set(choices) != set(by_key):
            raise ValueError('The reviewed video list changed. Review it again.')
        modules = {str(m['id']) for m in self.data['modules']}
        used = set()
        prepared = []
        for key, choice in choices.items():
            selected, destination = choice['selected'], choice.get('destination')
            if not isinstance(selected, bool):
                raise ValueError('Invalid selection.')
            row = by_key[key]
            if selected:
                if row['state'] in ('pending', 'needs_check'):
                    raise ValueError(f"Check the saved outcome for {row['name']} before continuing, or exclude it.")
                if not isinstance(destination, dict) or set(destination) not in ({'id'}, {'new'}):
                    raise ValueError(f"Choose a destination for {row['name']}.")
                if 'id' in destination and str(destination['id']) not in modules:
                    raise ValueError('A selected destination is no longer in this course.')
                if 'new' in destination and (not isinstance(destination['new'], str) or not destination['new'].strip()):
                    raise ValueError('Enter a name for the new unit.')
                if row['state'] in VERIFIED and destination != row['destination']:
                    raise ValueError('A verified placement cannot be moved by changing its destination. Start a new transfer for another placement.')
                identity = (row['entry_id'], json.dumps(destination, sort_keys=True))
                if identity in used:
                    raise ValueError('The same video is selected twice for the same destination. Exclude one placement.')
                used.add(identity)
            prepared.append((row, selected, copy.deepcopy(destination)))
        if not any(s for _, s, _ in prepared) and self.data['rows']:
            raise ValueError('Select at least one video to continue.')
        for row, selected, destination in prepared:
            row.update(selected=selected, destination=destination)
        self.data['approval'] = dict(at=now(), partial_ack=partial_ack,
            bs_url=self.data['bs_url'], course_id=self.data['course_id'])
        self.save()

    def save_draft(self, choices):
        """Unmapped selections are valid drafts, never valid write approvals."""
        by_key = {r['key']: r for r in self.data['rows']}
        if set(choices) != set(by_key):
            raise ValueError('The draft video list changed.')
        for key, choice in choices.items():
            if not isinstance(choice.get('selected'), bool):
                raise ValueError('Invalid draft selection.')
            row = by_key[key]
            if row['state'] in VERIFIED and choice.get('destination') != row['destination']:
                raise ValueError('Keep the verified destination unchanged.')
        for key, choice in choices.items():
            by_key[key].update(selected=choice['selected'], destination=copy.deepcopy(choice.get('destination')))
        self.data['approval'] = None
        self.save()

    def counts(self):
        rows = self.data['rows']
        selected = [r for r in rows if r['selected']]
        return dict(total=len(selected), created=sum(r['state'] == 'created' for r in selected),
            present=sum(r['state'] == 'already_present' for r in selected),
            uncertain=sum(r['state'] in ('pending', 'needs_check') for r in selected),
            failed=sum(r['state'] == 'failed' for r in selected),
            remaining=sum(r['state'] not in VERIFIED for r in selected),
            excluded=sum(not r['selected'] for r in rows))


def saved_runs(directory=None, *, include_removed=False):
    runs = []
    for path in Path(directory or RUNS_DIR).glob('*.json'):
        try:
            run = KalturaRun.load(path.stem, directory)
            if include_removed or not run.data.get('removed_at'):
                runs.append(run)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return sorted(runs, key=lambda r: r.data.get('updated', ''), reverse=True)


def set_run_removed(id_, removed, directory=None):
    from transfer_runner import PlanLock
    run = KalturaRun.load(id_, directory)
    with PlanLock(run.path):
        run = KalturaRun.load(id_, directory)
        if removed:
            run.data['removed_at'] = now()
        else:
            run.data.pop('removed_at', None)
        run.save(touch=False)
    return run


def describe_run(run):
    d, c = run.data, run.counts()
    if not d['scan_complete'] or d.get('error') or any(r['state'] in ('pending', 'needs_check', 'failed') for r in d['rows']):
        status, category = 'Needs attention', 'attention'
    elif d['status'] == 'complete':
        status, category = ('Complete', 'complete') if d['rows'] else ('No Kaltura videos', 'empty')
    elif not d.get('approval'):
        status, category = 'Ready to review', 'ready'
    else:
        status, category = 'Paused', 'attention'
    progress = (f"{c['created']} created · {c['present']} kept · {c['remaining']} remaining" if d.get('approval') or any(r['state'] in VERIFIED or r.get('attempted') for r in d['rows']) else
                f"{len(d['rows'])} placements found" if d['rows'] else 'Complete scan · no Kaltura videos' if d['scan_complete'] else 'Scan incomplete')
    source = parse_qs(urlsplit(d['moodle_url']).query).get('id', ['?'])[0]
    route = f"Moodle {source} → Brightspace {d['course_id']}"
    try:
        stamp = datetime.fromisoformat(d['updated']).astimezone(timezone.utc)
        date, time = stamp.strftime('%d %b %Y'), stamp.strftime('%H:%M:%S UTC')
    except (ValueError, TypeError, KeyError):
        date, time = 'Date unavailable', ''
    return dict(title=d['course_name'], route=route, status=status, category=category, progress=progress,
        date=date, time=time, removed=bool(d.get('removed_at')),
        search=' '.join((d['course_name'], route, d['id'], status, ' '.join(r['entry_id'] for r in d['rows']))).casefold())
