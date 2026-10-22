"""Build an H5P transfer plan without downloading or changing course content."""
import asyncio
import copy
import os
from pathlib import Path
import shutil
from collections import Counter

from h5p_handler import H5PHandler
from js_helpers import _norm
from rebuild_helpers import valid_section_name


def _copy_package(source, target):
    temporary = target.with_suffix('.h5p.tmp')
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def build_plan(progress, moodle_items, bs_flat):
    modules = [dict(id=str(m['id']), title=m['title']) for m in bs_flat
               if m.get('kind') == 'MODULE' and m.get('id')]
    if progress.data['activities']:
        rows = copy.deepcopy(progress.data['activities'])
    else:
        import h5p_handler
        cache = Path(h5p_handler.__file__).parent.parent / 'downloads' / 'h5p'
        files = [cache / (stem + '.h5p') for stem in H5PHandler._download_stems(moodle_items).values()]
        rows = H5PHandler._build_assignments(files, moodle_items, bs_flat, progress.data['grade_all'])
        for row in rows:
            file = row.pop('file')
            row.update(key=file.stem, filename=file.name, upload='ready', insertion='ready')
    names = Counter(H5PHandler._norm_title(r['name']) for r in rows)
    for row in rows:
        module = next((m for m in modules if m['id'] == str(row.get('bs_module_id'))), None)
        row['bs_module_id'] = module['id'] if module else None
        row['bs_module_title'] = module['title'] if module else None
        row['selected'] = row.get('insertion') != 'confirmed'
        row['duplicate_title'] = names[H5PHandler._norm_title(row['name'])] > 1
    return dict(run_id=progress.data['id'], course_name=progress.data['course_name'],
                moodle_course_name=progress.data.get('moodle_course_name', ''),
                bs_url=progress.data['bs_url'], moodle_url=progress.data['moodle_url'],
                modules=modules, activities=rows)


def validate_choices(plan, choices):
    """Only reviewed activity identities and existing/explicitly new units are accepted."""
    if not isinstance(choices, list) or len(choices) != len(plan['activities']):
        raise ValueError('Review every activity before transferring.')
    by_key = {r['key']: r for r in plan['activities']}
    if len(by_key) != len(choices) or {c.get('key') for c in choices} != set(by_key):
        raise ValueError('The activity list changed. Scan the course again.')
    modules = {m['id']: m['title'] for m in plan['modules']}
    result, names = [], set()
    for choice in choices:
        row = copy.deepcopy(by_key[choice['key']])
        row['selected'] = bool(choice.get('selected'))
        if row['selected']:
            if row.get('name_check_required') and not row.get('saved_topic_id'):
                raise ValueError('Check and correct the previously saved page title in Brightspace before retrying this activity.')
            if row.get('duplicate_title'):
                raise ValueError('Two activities share a title. Rename them in Moodle, then start a new transfer so the correct content can be identified.')
            target = str(choice.get('module_id') or '')
            if row.get('name_check_required') and target != str(row.get('bs_module_id')):
                raise ValueError('A saved page must be renamed in its original destination unit.')
            new_unit = 'new:' + row['moodle_section']
            if target not in modules and not (target == new_unit and valid_section_name(row['moodle_section'])):
                raise ValueError(f"Choose a Brightspace unit for {row['name']}.")
            if target.startswith('new:') and any(_norm(t) == _norm(row['moodle_section']) for t in modules.values()):
                raise ValueError('Choose the existing destination unit instead of creating another with the same name.')
            row['bs_module_id'] = target
            row['bs_module_title'] = modules.get(target, row['moodle_section'])
            if not row.get('name_check_required'):
                row['should_grade'] = bool(choice.get('should_grade'))
            name = H5PHandler._norm_title(row['name'])
            if name in names:
                raise ValueError('Select only one activity with each title. Rename duplicate activities in Moodle before transferring both.')
            names.add(name)
        result.append(row)
    if not any(r['selected'] for r in result) and not all(r.get('already_present') for r in result):
        raise ValueError('Select at least one activity to transfer.')
    return result


async def review_and_prepare(checker, context, page, moodle_items, bs_flat,
                             bs_base, course_id, progress, review_fn, on_state):
    """The approval gate precedes every download setting, unit creation and upload."""
    def emit(stage, **values):
        if on_state:
            on_state(dict(stage=stage, **values))

    plan = build_plan(progress, moodle_items, bs_flat)
    for row in plan['activities']:
        if checker.stop_flag[0]:
            raise asyncio.CancelledError()
        if row.get('duplicate_title'):
            row.update(already_present=False, selected=False, insertion='ready')
            continue
        verifier = getattr(checker._h5p, '_verify_saved_name', checker._h5p._verify_topic_in_module) if row.get('name_check_required') else checker._h5p._verify_topic_in_module
        row['already_present'] = bool(row['bs_module_id']) and await verifier(
            page, course_id, row['bs_module_id'], row['name'])
        if row.get('name_check_required') and not row['already_present']:
            row['selected'] = bool(row.get('saved_topic_id'))
            continue
        row.pop('name_check_required', None)
        # Saved confirmation is rechecked against the current course.
        if not row['already_present']:
            row['insertion'] = 'ready'
            row['selected'] = True
        else:
            row['selected'] = False
    if not plan['activities']:
        emit('empty', plan=plan)
        return None
    choices = await asyncio.get_running_loop().run_in_executor(None, review_fn, copy.deepcopy(plan))
    if checker.stop_flag[0]:
        raise asyncio.CancelledError()
    if choices is None:
        return None
    rows = validate_choices(plan, choices)
    emit('transfer', message='Preparing the selected activities')
    new_run = not progress.data['activities']
    progress.data['activities'] = rows
    progress.save()

    new_units = list(dict.fromkeys(r['moodle_section'] for r in rows
                     if r['selected'] and str(r['bs_module_id']).startswith('new:')))
    if new_units:
        await checker._create_missing_units(context, bs_base, course_id, new_units)
        bs_flat = await checker._fetch_bs_toc(page, course_id)
        for row in rows:
            if row['selected'] and str(row['bs_module_id']).startswith('new:'):
                matches = [m for m in bs_flat if m.get('kind') == 'MODULE'
                           and _norm(m.get('title', '')) == _norm(row['moodle_section'])]
                if len(matches) != 1:
                    raise ValueError(f"Could not confirm the destination unit for {row['name']}.")
                row.update(bs_module_id=matches[0]['id'], bs_module_title=matches[0]['title'])

    # Cache ownership is isolated by saved run; do not replace another course's copies.
    import h5p_handler
    cache = Path(h5p_handler.__file__).parent.parent / 'downloads' / 'h5p'
    folder = progress.path.parent / progress.data['id'] / 'files'
    folder.mkdir(parents=True, exist_ok=True)
    for row in rows:
        if Path(row['filename']).name != row['filename']:
            raise ValueError('Invalid saved H5P filename.')
        target = folder / row['filename']
        if not target.exists() and new_run and (cache / row['filename']).is_file():
            _copy_package(cache / row['filename'], target)
        row['download'] = 'confirmed' if target.is_file() else 'missing'
        if row.get('already_present'):
            row['insertion'] = 'confirmed'
        row.pop('problem', None)
        if row['selected'] and not row.get('already_present'):
            row.pop('grade_attention', None)
    progress.data['activities'] = rows
    progress.save()

    selected_keys = {r['key'] for r in rows if r['selected'] and not r.get('already_present')
                     and r['download'] != 'confirmed' and r['upload'] != 'confirmed'}
    stems = H5PHandler._download_stems(moodle_items)
    activity_keys = {H5PHandler.activity_key(item) for item in moodle_items
                     if stems.get(id(item)) in selected_keys}
    if activity_keys:
        emit('transfer', message='Downloading selected activities from Moodle')
        tab = await context.new_page()
        try:
            await tab.goto(progress.data['moodle_url'], wait_until='domcontentloaded', timeout=30000)
            await checker._switch_to_instructor_role(tab)
        finally:
            await tab.close()
        failures = await checker._h5p.enable_downloads(context, moodle_items, activity_keys=activity_keys)
        if failures and checker.h5p_recovery:
            selections = await asyncio.get_running_loop().run_in_executor(None, checker.h5p_recovery, failures)
            checker._h5p.recover_downloads(failures, selections or [])
        for row in rows:
            if row['key'] in selected_keys:
                source, target = cache / row['filename'], folder / row['filename']
                if source.is_file():
                    _copy_package(source, target)
                    row['download'] = 'confirmed'
                else:
                    row['download'] = 'failed'
                    row['problem'] = 'Download missing. Resume to retry or choose a replacement file.'
        progress.save()
    if checker.stop_flag[0]:
        raise asyncio.CancelledError()
    emit('transfer', message='Checking the H5P library and placing activities in Brightspace')
    return bs_flat
