"""Presentation and reversible history changes for saved H5P transfers."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from h5p_progress import H5PRun


def describe_run(run):
    data = run.data
    rows = data['activities']
    uploaded, placed, remaining = run.counts()
    found = sum(item.get('type') == 'EXTERNAL' and any(
        name in item.get('hint', '') for name in ('h5p', 'hvp')) for item in data['moodle_items'])
    if any(row.get('grade_attention') for row in rows):
        status, category = 'Check gradebook', 'attention'
    elif rows and data['status'] == 'complete' and not remaining:
        status, category = 'Complete', 'complete'
    elif rows and remaining:
        status, category = f'{remaining} remaining', 'attention'
    elif rows:
        status, category = 'Review results', 'attention'
    elif found:
        status, category = 'Ready to review', 'attention'
    elif data['moodle_items'] or data['status'] == 'complete':
        status, category = 'No H5P activities', 'empty'
    else:
        status, category = 'Scan incomplete', 'attention'
    progress = (f'{placed} of {len(rows)} placed · {uploaded} uploaded' if rows else
                f"{found} {'activity' if found == 1 else 'activities'} found" if found else
                'Course checked' if category == 'empty' else 'Activity list not saved yet')
    source_id = parse_qs(urlsplit(data['moodle_url']).query).get('id', ['?'])[0]
    route = f"Moodle {source_id} → Brightspace {data['course_id']}"
    try:
        stamp = datetime.fromisoformat(data.get('updated', ''))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        date, time = stamp.strftime('%d %b %Y'), stamp.strftime('%H:%M:%S UTC')
    except (ValueError, TypeError):
        date, time = 'Date unavailable', ''
    return dict(title=data['course_name'], route=route, status=status, category=category,
        progress=progress, date=date, time=time, removed=bool(data.get('removed_at')),
        search=' '.join((data['course_name'], data.get('moodle_course_name', ''), route,
                         data['id'], status)).casefold())


def set_run_removed(run_id, removed, directory=None):
    """Hide/restore a record without losing its journal, downloads or course work.

    Use the same lock as the runner so another app window cannot remove a run
    while its transfer is active. A crashed run can still be tidied up.
    """
    from transfer_runner import PlanLock
    run = H5PRun.load(run_id, directory)  # Validate identity before opening a lock.
    with PlanLock(run.path):
        run = H5PRun.load(run_id, directory)
        if removed:
            run.data['removed_at'] = datetime.now(timezone.utc).isoformat()
        else:
            run.data.pop('removed_at', None)
        run.save(touch=False)  # History changes must not look like new transfer work.
    return run
