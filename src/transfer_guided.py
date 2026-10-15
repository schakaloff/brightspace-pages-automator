"""Automatic, explainable setup for the two-course-link workflow."""
from __future__ import annotations

from bs4 import BeautifulSoup
import copy
from collections import Counter

from transfer_plan import (EA105_SECTIONS, all_objects, assignment_for, build_plan, norm, refresh_items,
                           rubric_signature, upgrade_recipe, validate_recipe, workshop_recipe)

# Words that mark a folder as the home for assignments when a shell's own
# content does not already show where assignments live.
ASSIGNMENT_FOLDER_WORDS = ("after", "assessment", "assignment", "submit", "homework", "evaluation")
SKIPPED_UNIT_WORDS = ("template", "instructor", "structured transfer")


def _sections_from_units(units, label):
    """Shared ordered structure of a course's own units, or None.

    Child folders always count. A page counts only when the same title recurs
    across units (for example "Module Introduction"); one-off pages are content.
    """
    if not units: return None
    page_counts = Counter(norm(c["Title"]) for u in units for c in u.get("children", []) if c.get("Type") == 1)
    shapes = []
    for unit in units:
        shape = tuple(("folder", c["Title"].strip()) if c.get("Type") == 0 else ("page", c["Title"].strip())
                      for c in unit.get("children", [])
                      if c.get("Type") == 0 or page_counts[norm(c["Title"])] >= 2)
        if any(kind == "folder" for kind, _ in shape): shapes.append(shape)
    if not shapes: return None
    shape, count = Counter(shapes).most_common(1)[0]
    if count < min(2, len(units)): return None
    matching = [u for u in units if tuple(("folder", c["Title"].strip()) if c.get("Type") == 0 else ("page", c["Title"].strip())
                                          for c in u.get("children", []) if c.get("Type") == 0 or page_counts[norm(c["Title"])] >= 2) == shape]
    sections, introduction = [], False
    for kind, title in shape:
        if kind == "page":
            if "introduction" in norm(title) and not introduction:
                sections.append({"title": title, "kind": "introduction"}); introduction = True
            else:
                sections.append({"title": title, "kind": "placeholder", "message": f"{title} to be completed before publication."})
            continue
        known = next((s for s in EA105_SECTIONS if s["kind"] == "folder" and norm(s["title"]) == norm(title)), None)
        sections.append({"title": title, "kind": "folder", "keywords": list((known or {}).get("keywords", [])) or [title],
                         "aliases": list((known or {}).get("aliases", []))})
    folders = [s for s in sections if s["kind"] == "folder"]

    def folder_children(title):
        return [c for u in matching for f in u.get("children", []) if f.get("Type") == 0 and f["Title"].strip() == title
                for c in all_objects(f.get("children", []))]
    sessions = [s for s in folders if sum(norm(c["Title"]) == "session overview" for c in folder_children(s["title"])) * 2 >= len(matching)]
    if sessions: sessions[0]["session_overview"] = True
    assignments = Counter({s["title"]: sum(c.get("ActivityType") == 3 for c in folder_children(s["title"])) for s in folders})
    home, used = assignments.most_common(1)[0] if assignments else (None, 0)
    if not used:
        home = next((s["title"] for s in folders if any(w in norm(s["title"]) for w in ASSIGNMENT_FOLDER_WORDS)), None)
    for s in folders:
        if s["title"] == home: s["assignments"] = True
    default = sessions[0] if sessions else folders[len(folders) // 2]
    default["default"] = True
    default["files"] = True
    from transfer_split import text_phase
    used = set()
    for s in folders:
        phase = text_phase(s["title"])
        if phase and phase not in used: s["phase"] = phase; used.add(phase)
    return {"label": label, "sections": sections}


def structure_from_target(target):
    """The section pattern the target development shell already uses."""
    units = [m for m in target.get("modules", []) if m.get("Type") == 0
             and not any(word in norm(m["Title"]) for word in SKIPPED_UNIT_WORDS)]
    return _sections_from_units(units, f"Match {target.get('name', 'target')} modules")


def structure_from_source(source, module_ids=None):
    """Sub-folders the source units already share."""
    ids = {str(i) for i in module_ids} if module_ids else None
    units = [m for m in all_objects(source.get("modules", [])) if m.get("Type") == 0
             and (str(m["Id"]) in ids if ids else m in source.get("modules", []))]
    return _sections_from_units(units, f"Keep {source.get('name', 'source')} sub-folders")


def simple_structure():
    return {"label": "Introduction, then content", "sections": [
        {"title": "Module Introduction", "kind": "introduction"},
        {"title": "Content", "kind": "inline", "default": True, "assignments": True, "files": True}]}


def ea105_structure():
    return {"label": "EA105 workshop layout", "sections": copy.deepcopy(EA105_SECTIONS)}


def structure_presets(plan_or_source, target=None):
    """Named structures a person can pick from, most specific first."""
    source = plan_or_source["source"] if "source_hash" in plan_or_source else plan_or_source
    target = target or plan_or_source.get("target")
    found = [s for s in (structure_from_target(target) if target else None, structure_from_source(source)) if s]
    return [*found, ea105_structure(), simple_structure()]


def change_structure(plan, sections):
    """Replace the unit structure and re-sort items; kept decisions survive."""
    if plan.get("run_signature"): raise ValueError("An applied plan's structure cannot change.")
    recipe = upgrade_recipe(plan["recipe"])
    recipe["sections"] = copy.deepcopy(sections)
    validate_recipe(recipe)
    plan["recipe"] = recipe
    if "introduction" not in plan["templates"] or "session" not in plan["templates"]:
        found = discover_templates(plan["source"], plan["target"], recipe)
        for kind, template in found.items():
            plan["templates"].setdefault(kind, template)
    for workshop in plan["workshops"]: refresh_items(plan, workshop)
    match_target_assignments(plan)
    plan["review_acknowledged"] = False


def set_page_split(plan, workshop, source_id, split):
    """Split a page at its labels, or keep it whole."""
    if plan.get("run_signature"): raise ValueError("An applied plan cannot change.")
    unsplit = set(workshop.get("unsplit", []))
    (unsplit.discard if split else unsplit.add)(str(source_id))
    workshop["unsplit"] = sorted(unsplit)
    refresh_items(plan, workshop)
    plan["review_acknowledged"] = False


def change_gradebook(plan, enabled, categories):
    """Replace gradebook categories ([{name, weight, role}])."""
    if plan.get("run_signature"): raise ValueError("An applied plan's gradebook cannot change.")
    recipe = upgrade_recipe(plan["recipe"])
    grades = recipe.setdefault("gradebook", {})
    labels = {c["name"]: c["label"] for c in (recipe.get("gradebook") or {}).get("categories", []) if c.get("label")}
    grades["enabled"] = enabled
    grades["categories"] = [{**({"label": labels[c["name"]]} if c["name"] in labels else {}), **c} for c in categories]
    grades.setdefault("assignment_distribution", 1); grades.setdefault("attendance_items", [])
    validate_recipe(recipe)
    plan["recipe"] = recipe
    plan["review_acknowledged"] = False


def standard_template(target, kind):
    """Self-contained fallback when neither course has a usable shell template."""
    markup = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Workshop</title>
    <style>body{font:1rem/1.6 system-ui,sans-serif;color:#253238;margin:0;background:#fff}.courseware-layouts-content-wrapper{max-width:960px;margin:auto;padding:32px}h1{color:#005f63;line-height:1.2}h2{line-height:1.3}.d2l-element{border-left:4px solid #005f63;background:#eef7f7;padding:20px;margin-top:24px}.d2l-callout-title{font-weight:700;font-size:1.2rem}li{margin:8px 0}</style></head>
    <body><main class="courseware-layouts-content-wrapper"><div class="courseware-headers-hero"><h1>Workshop</h1></div><div class="d2l-element"><div class="d2l-callout-title">Learning Outcomes</div><div><ul><li>To be supplied</li></ul></div></div></main></body></html>'''
    return {'id': 'standard-' + kind, 'course': target['id'], 'url': target['root'] + 'standard-template.html',
            'html': markup, 'title': 'Standard page style', 'path': '', 'label': 'Standard page style', 'builtin': True}


def automatic_recipe(source, target=None):
    recipe = workshop_recipe()
    modules = [m for m in all_objects(source['modules']) if m.get('Type') == 0]
    known = all(sum(norm(m['Title']) == norm(title) for m in modules) == 1
                for w in recipe['workshops'] for title in w['source_titles'])
    if known:
        recipe['name'] = 'EA105 workshop layout'
        recipe['structure_origin'] = 'EA105 workshop layout'
    else:
        recipe['name'] = 'Workshops from source modules'
        recipe['gradebook']['enabled'] = False
        recipe['workshops'] = [
            {'title': m['Title'], 'hours': None, 'source_titles': [m['Title']],
             'source_ids': [str(m['Id'])], 'introduction': '', 'outcomes': [], 'session': ''}
            for m in source['modules'] if m.get('Type') == 0 and m.get('children')]
        used = set()
        for workshop in recipe['workshops']:
            title, occurrence = workshop['title'], 1
            while norm(workshop['title']) in used:
                occurrence += 1
                workshop['title'] = f'{title} ({occurrence})'
                workshop['review_name'] = True
            used.add(norm(workshop['title']))
        if not recipe['workshops']:
            raise ValueError('The source course has no populated workshop modules to copy.')
        ids = [w['source_ids'][0] for w in recipe['workshops']]
        structure = (structure_from_target(target) if target else None) or structure_from_source(source, ids) or simple_structure()
        recipe['sections'] = structure['sections']
        recipe['structure_origin'] = structure['label']
        recipe['unit_label'] = 'Module'
    return recipe


def template_candidates(source, target, kind):
    result = []
    for course in (target, source):
        def walk(entries, path):
            for obj in entries:
                if obj.get('Type') == 0:
                    walk(obj.get('children', []), [*path, obj['Title']])
                elif obj.get('html'):
                    soup = BeautifulSoup(obj['html'], 'html.parser')
                    if not soup.select_one('.courseware-layouts-content-wrapper'): continue
                    if kind == 'introduction':
                        title = soup.select_one('.d2l-callout-title')
                        if title is None or title.find_parent(class_='d2l-element') is None or title.find_next_sibling('div') is None: continue
                    result.append({'id': str(obj['Id']), 'course': course['id'],
                                   'url': obj.get('Url'), 'html': obj['html'],
                                   'title': obj['Title'], 'path': ' / '.join(path),
                                   'label': f"{obj['Title']} — {'Target' if course is target else 'Source'} / {' / '.join(path)}"})
        walk(course['modules'], [])
    result.append(standard_template(target, kind))
    return result


def discover_templates(source, target, recipe):
    result = {}
    for kind in ('introduction', 'session'):
        candidates = template_candidates(source, target, kind)
        expected = norm(recipe['templates'].get(kind + '_title', ''))
        for course in (target, source):
            matches = [c for c in candidates if c['course'] == course['id'] and norm(c['title']) == expected]
            if len(matches) == 1:
                result[kind] = matches[0]
                break
            if matches: break  # Ambiguous explicit titles require a named choice.
        if kind not in result:
            matches = [c for c in candidates if 'template' in norm(c['title'])
                       and (('introduction' in norm(c['title'])) if kind == 'introduction' else 'session overview' in norm(c['title']))]
            if len(matches) == 1: result[kind] = matches[0]
        if kind not in result: result[kind] = standard_template(target, kind)
    return result


def assignment_text(assignment):
    soup = BeautifulSoup((assignment.get('CustomInstructions') or {}).get('Html', ''), 'html.parser')
    for text in list(soup.find_all(string=True)):
        if str(text).strip().startswith('bpa-transfer:') and text.parent.get('style', '').replace(' ', '').startswith('display:none'):
            text.extract()
    return soup.get_text(' ', strip=True)


def matching_assignment(source, candidate):
    if norm(source['Name']) != norm(candidate['Name']): return False
    if rubric_signature((source.get('Assessment') or {}).get('Rubrics', [])) != rubric_signature((candidate.get('Assessment') or {}).get('Rubrics', [])): return False
    if (source.get('Assessment') or {}).get('ScoreDenominator') != (candidate.get('Assessment') or {}).get('ScoreDenominator'): return False
    if any(source.get(field) != candidate.get(field) for field in ('DropboxType', 'SubmissionType', 'CompletionType', 'IsAnonymous', 'GroupTypeId')): return False
    return assignment_text(source) == assignment_text(candidate)


def match_target_assignments(plan):
    """Use only a single equivalent target assignment; never guess by title."""
    if plan.get('run_signature'): return
    for workshop in plan['workshops']:
        for row in workshop['items']:
            if row['source'].get('ActivityType') != 3 or row.get('target_assignment_id'): continue
            source = assignment_for(plan, row)
            if not source: continue
            candidates = [a for a in plan['target']['assignments'] if matching_assignment(source, a)]
            if len(candidates) == 1: row['target_assignment_id'] = str(candidates[0]['Id'])


def build_guided_plan(source, target):
    recipe = automatic_recipe(source, target)
    templates = discover_templates(source, target, recipe)
    for kind, template in templates.items():
        recipe['templates'][kind + '_id'] = template['id']
        recipe['templates'][kind + '_course'] = 'builtin' if template.get('builtin') else 'target' if template['course'] == target['id'] else 'source'
    plan = build_plan(source, target, recipe, templates)
    plan['guided'] = True
    for workshop in plan['workshops']:
        workshop['mapping_reviewed'] = bool(workshop['source_module_ids']) and not workshop['mapping_notes'] and not workshop.get('review_name')
    match_target_assignments(plan)
    return plan


def item_needs_attention(plan, row):
    from transfer_plan import native_required, needs_grade_choice
    if not row['reviewed']: return True
    if row['section'] == 'Skip': return False
    if needs_grade_choice(plan, row): return True
    obj = row['source']
    if obj.get('ActivityType') == 3:
        assignment = assignment_for(plan, row)
        return not assignment or native_required(assignment, []) and not row.get('target_assignment_id')
    return obj.get('ActivityType') not in (1, 2) or obj.get('TopicType') not in (1, 3)
