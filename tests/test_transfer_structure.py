"""Recipe-defined unit structures: planning, validation and a full offline run."""
import copy
import hashlib
import re
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transfer_plan import (SECTIONS, all_objects, build_plan, classify, destinations, digest, grade_categories,
                           recipe_sections, upgrade_recipe, validate_plan, validate_recipe, workshop_recipe)
from transfer_guided import (change_gradebook, change_structure, simple_structure,
                             structure_from_source, structure_from_target)
from transfer_runner import TransferRunner, load_plan

HOST = "https://learn.okanagancollege.ca"
STYLE = ('<div class="courseware-layouts-content-wrapper"><h1>Template</h1><div class="d2l-element">'
         '<div class="d2l-callout-title">Learning Outcomes</div><div><ul><li>Example</li></ul></div></div></div>')

CUSTOM = [
    {"title": "Overview", "kind": "introduction"},
    {"title": "Readings", "kind": "folder", "keywords": ["read"]},
    {"title": "Learning Activities", "kind": "inline", "default": True, "session_overview": True},
    {"title": "Assessment", "kind": "folder", "assignments": True},
]


def custom_recipe(sections=CUSTOM, grades=True):
    recipe = workshop_recipe()
    recipe["sections"] = copy.deepcopy(sections)
    recipe["workshops"] = [{"title": "Unit 1", "hours": None, "source_titles": ["Week 1"],
                            "outcomes": ["Explain"], "introduction": "Intro", "session": "Plan"}]
    recipe["gradebook"] = {"enabled": grades, "assignment_distribution": 1, "attendance_items": [],
                           "categories": [{"name": "Projects", "weight": 60, "role": "assignments"},
                                          {"name": "Quizzes", "weight": 40}]}
    return recipe


def legacy_recipe():
    """The schema 1 recipe that saved EA105 plans and sandbox journals use."""
    r = workshop_recipe()
    r.update(schema=1, sections=list(SECTIONS), keywords={s["title"]: s.get("keywords", []) for s in r["sections"] if s.get("keywords")})
    r["gradebook"] = {"enabled": True, "categories": {"Assignments": 70, "Attendance/Participation": 30},
                      "category_names": {"Attendance/Participation": "Attendance and Participation"},
                      "assignment_distribution": 1, "attendance_items": []}
    for key in ("unit_label", "review_assignment_keywords"): r.pop(key)
    return r


# ── Planning and validation ──────────────────────────────────────────────

def test_saved_schema_1_recipes_keep_their_structure_and_journal_keys():
    r = legacy_recipe()
    validate_recipe(r)
    sections = recipe_sections(r)
    assert [s["title"] for s in sections] == list(SECTIONS)
    assert [s["key"] for s in sections] == ["classroom", "introduction", "section:Before Class",
                                            "section:Class Materials", "section:After Class"]
    assert [c["label"] for c in grade_categories(r["gradebook"])] == ["Assignments", "Attendance/Participation"]
    assert grade_categories(r["gradebook"])[1]["name"] == "Attendance and Participation"
    upgraded = upgrade_recipe(r)
    validate_recipe(upgraded)
    assert [s["key"] for s in recipe_sections(upgraded)] == [s["key"] for s in sections]
    assert grade_categories(upgraded["gradebook"]) == grade_categories(r["gradebook"])


def test_default_recipe_is_the_ea105_structure_as_data():
    r = workshop_recipe()
    validate_recipe(r)
    assert [s["title"] for s in recipe_sections(r)] == list(SECTIONS)
    assert destinations(r) == ["Before Class", "Class Materials", "After Class", "Skip"]


def test_custom_structure_classifies_by_its_own_sections():
    r = custom_recipe()
    r["default_is_rule"] = False  # leave unmatched items for review
    validate_recipe(r)
    assert destinations(r) == ["Readings", "Learning Activities", "Assessment", "Skip"]
    assert classify({"Title": "Notes", "ActivityType": 1}, ["Week 1", "Readings"], r)[:2] == ("Readings", "deterministic")
    assert classify({"Title": "Read chapter 2", "ActivityType": 1}, ["Week 1"], r)[:2] == ("Readings", "inferred")
    assert classify({"Title": "Scenarios", "ActivityType": 1}, ["Week 1"], r)[:2] == ("Learning Activities", "unresolved")
    assert classify({"Title": "Essay", "ActivityType": 3}, ["Week 1"], r)[:2] == ("Assessment", "deterministic")


def test_section_aliases_count_as_structure():
    r = workshop_recipe()
    assert classify({"Title": "Notes", "ActivityType": 1}, ["Workshop", "Pre-Class"], r)[:2] == ("Before Class", "deterministic")


def test_structure_without_assignment_section_lets_assignments_go_anywhere_after_review():
    r = custom_recipe([{"title": "Content", "kind": "inline", "default": True}])
    validate_recipe(r)
    assert classify({"Title": "Essay", "ActivityType": 3}, ["Week 1"], r)[:2] == ("Content", "inferred")


@pytest.mark.parametrize("sections, message", [
    ([], "at least one section"),
    ([{"title": "Intro", "kind": "introduction"}], "hold transferred content"),
    ([{"title": "A", "kind": "folder"}, {"title": "a", "kind": "folder"}], "unique"),
    ([{"title": "Skip", "kind": "folder"}], "not 'Skip'"),
    ([{"title": "A", "kind": "shelf"}], "kind must be"),
    ([{"title": "A", "kind": "introduction"}, {"title": "B", "kind": "introduction"}, {"title": "C", "kind": "folder"}], "one section can be the introduction"),
    ([{"title": "A", "kind": "folder", "session_overview": True}, {"title": "B", "kind": "folder", "session_overview": True}], "Session Overview"),
    ([{"title": "A", "kind": "placeholder", "assignments": True}, {"title": "B", "kind": "folder"}], "only folder or inline"),
])
def test_invalid_structures_are_rejected_with_a_reason(sections, message):
    with pytest.raises(ValueError, match=message):
        validate_recipe(custom_recipe(sections))


@pytest.mark.parametrize("categories, message", [
    ([{"name": "A", "weight": 60}, {"name": "B", "weight": 30}], "total 100"),
    ([{"name": "A/B", "weight": 100}], "supported characters"),
    ([{"name": "A", "weight": 50, "role": "assignments"}, {"name": "B", "weight": 50, "role": "assignments"}], "Only one"),
    ([{"name": "A", "weight": 100, "role": "marks"}], "role must be"),
])
def test_gradebook_categories_are_free_but_validated(categories, message):
    r = custom_recipe()
    r["gradebook"]["categories"] = categories
    with pytest.raises(ValueError, match=message):
        validate_recipe(r)


def source_course():
    week = {"Id": 10, "Type": 0, "Title": "Week 1", "children": [
        {"Id": 11, "Type": 1, "Title": "Read chapter 2", "ActivityType": 1, "TopicType": 1,
         "Url": "/content/enforced/1-src/reading.html", "html": '<p>See <a href="notes.pdf">notes</a></p>'},
        {"Id": 12, "Type": 1, "Title": "Discussion prompts", "ActivityType": 1, "TopicType": 1,
         "Url": "/content/enforced/1-src/prompts.pdf"},
        {"Id": 13, "Type": 1, "Title": "Week 1 Essay", "ActivityType": 3, "ToolItemId": 70,
         "Url": "/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&type=dropbox&rcode=essay"},
        {"Id": 14, "Type": 0, "Title": "Readings", "children": [
            {"Id": 15, "Type": 1, "Title": "Background", "ActivityType": 1, "TopicType": 1,
             "Url": "/content/enforced/1-src/background.pdf"}]}]}
    return {"id": "1", "name": "Source course", "root": "/content/enforced/1-src/", "modules": [week],
            "assignments": [{"Id": 70, "Name": "Week 1 Essay", "DropboxType": 1, "SubmissionType": 0,
                             "CompletionType": 0, "IsAnonymous": False, "GroupTypeId": None,
                             "CustomInstructions": {"Html": "<p>Write it.</p>"}, "Assessment": {"ScoreDenominator": 20, "Rubrics": []}}],
            "categories": [], "grades": [], "setup": {"GradingSystem": "Weighted"}, "versions": ["1.99"]}


def test_changing_structure_keeps_valid_decisions_and_resets_removed_ones():
    source = source_course()
    target = {**copy.deepcopy(source), "id": "2", "name": "Target", "root": "/content/enforced/2-tgt/",
              "modules": [], "assignments": []}
    plan = build_plan(source, target, workshop_recipe() | {"workshops": custom_recipe()["workshops"]},
                      {"introduction": {"html": STYLE}, "session": {"html": STYLE}})
    rows = {r["id"]: r for r in plan["workshops"][0]["items"]}
    rows["11"].update(section="Before Class", reviewed=True)
    rows["12"].update(section="Skip", reviewed=True)
    change_structure(plan, CUSTOM)
    rows = {r["id"]: r for r in plan["workshops"][0]["items"]}
    assert rows["12"]["section"] == "Skip" and rows["12"]["reviewed"]          # still valid: kept
    assert rows["11"]["section"] == "Readings" and not rows["11"]["reviewed"]  # section gone: re-suggested
    assert rows["15"]["section"] == "Readings" and rows["15"]["confidence"] == "deterministic"
    assert rows["13"]["section"] == "Assessment"
    assert plan["recipe"]["schema"] == 2 and not plan["review_acknowledged"]


def test_gradebook_edits_validate_before_replacing_the_plan():
    plan = build_plan(source_course(), {**source_course(), "id": "2", "modules": []}, custom_recipe(),
                      {"introduction": {"html": STYLE}, "session": {"html": STYLE}})
    before = copy.deepcopy(plan["recipe"])
    with pytest.raises(ValueError):
        change_gradebook(plan, True, [{"name": "Only", "weight": 50}])
    assert plan["recipe"] == before
    change_gradebook(plan, True, [{"name": "Labs", "weight": 25}, {"name": "Essays", "weight": 75, "role": "assignments"}])
    assert [c["name"] for c in grade_categories(plan["recipe"]["gradebook"])] == ["Labs", "Essays"]


def test_legacy_planner_also_blocks_included_missing_html_without_blocking_skip():
    source = source_course()
    page = source['modules'][0]['children'][0]
    page.pop('html')
    page['html_error'] = 'The page file is missing in Brightspace (404).'
    plan = build_plan(source, {**source_course(), 'id': '2', 'modules': [], 'assignments': []}, custom_recipe(),
                      {'introduction': {'html': STYLE}, 'session': {'html': STYLE}})
    row = next(r for r in plan['workshops'][0]['items'] if r['id'] == '11')
    row.update(section='Readings', reviewed=True)
    assert any('Read chapter 2' in error and 'missing' in error for error in validate_plan(plan)[0])
    row['section'] = 'Skip'
    assert not any('missing in Brightspace' in error for error in validate_plan(plan)[0])


def test_assignment_outside_its_section_is_blocked_with_section_names():
    plan = build_plan(source_course(), {**source_course(), "id": "2", "modules": [], "assignments": []}, custom_recipe(),
                      {"introduction": {"html": STYLE}, "session": {"html": STYLE}})
    row = next(r for r in plan["workshops"][0]["items"] if r["id"] == "13")
    row.update(section="Readings", reviewed=True)
    assert any("must be in Assessment" in e for e in validate_plan(plan)[0])


def test_structure_without_introduction_or_session_needs_no_templates():
    r = custom_recipe([{"title": "Content", "kind": "inline", "default": True}], grades=False)
    plan = build_plan(source_course(), {**source_course(), "id": "2", "modules": [], "assignments": []}, r, {})
    assert not any("template" in e for e in validate_plan(plan)[0])
    assert not any("introduction" in w or "Session Overview" in w for w in validate_plan(plan)[1])


# ── Structure detection ──────────────────────────────────────────────────

def shell_unit(id_, title, extra=()):
    return {"Id": id_, "Type": 0, "Title": title, "children": [
        {"Id": id_ + 1, "Type": 1, "Title": "Module Introduction"},
        {"Id": id_ + 2, "Type": 0, "Title": "Prepare", "children": []},
        {"Id": id_ + 3, "Type": 0, "Title": "In Class", "children": [{"Id": id_ + 4, "Type": 1, "Title": "Session Overview"}]},
        {"Id": id_ + 5, "Type": 0, "Title": "Assess", "children": [{"Id": id_ + 6, "Type": 1, "Title": "Essay", "ActivityType": 3}]},
        *extra]}


def test_target_shell_structure_is_detected_from_its_own_units():
    target = {"name": "New shell", "modules": [
        {"Id": 1, "Type": 0, "Title": "Development templates", "children": [{"Id": 2, "Type": 0, "Title": "X template", "children": []}]},
        shell_unit(100, "Module 1", [{"Id": 199, "Type": 1, "Title": "One-off page"}]),
        shell_unit(200, "Module 2"), shell_unit(300, "Module 3")]}
    found = structure_from_target(target)
    assert [(s["title"], s["kind"]) for s in found["sections"]] == [
        ("Module Introduction", "introduction"), ("Prepare", "folder"), ("In Class", "folder"), ("Assess", "folder")]
    flags = {s["title"]: s for s in found["sections"]}
    assert flags["In Class"].get("session_overview") and flags["In Class"].get("default")
    assert flags["Assess"].get("assignments")
    recipe = custom_recipe(found["sections"]); validate_recipe(recipe)


def test_ea105_development_shell_yields_its_four_part_pattern():
    def unit(i, title):
        return {"Id": i, "Type": 0, "Title": title, "children": [
            {"Id": i + 1, "Type": 1, "Title": "Module Introduction"},
            {"Id": i + 2, "Type": 0, "Title": "Before Class", "children": []},
            {"Id": i + 3, "Type": 0, "Title": "Class Materials", "children": [{"Id": i + 4, "Type": 1, "Title": "Session Overview"}]},
            {"Id": i + 5, "Type": 0, "Title": "After Class", "children": []}]}
    found = structure_from_target({"name": "EA105", "modules": [unit(10, "Orientation"), unit(20, "Module 2"), unit(30, "Module 4")]})
    titles = [s["title"] for s in found["sections"]]
    assert titles == ["Module Introduction", "Before Class", "Class Materials", "After Class"]
    assert next(s for s in found["sections"] if s["title"] == "After Class").get("assignments")


def test_flat_courses_have_no_detected_structure_and_fall_back_to_simple():
    flat = {"name": "Flat", "modules": [{"Id": 1, "Type": 0, "Title": "Week 1", "children": [{"Id": 2, "Type": 1, "Title": "Page"}]}]}
    assert structure_from_target(flat) is None and structure_from_source(flat) is None
    validate_recipe(custom_recipe(simple_structure()["sections"]))


# ── A full run of a custom structure against an in-memory Brightspace ─────

class FakeCourse:
    """Just enough of TransferAPI for TransferRunner, with every write recorded."""

    def __init__(self, inventory, writable, files=None):
        self.base, self.course_id = HOST, inventory["id"]
        self.state, self.writable = copy.deepcopy(inventory), writable
        self.files = dict(files or {})
        self.writes, self.next_id = [], 5000
        self.objects = {}
        self._index(self.state["modules"], None)

    def _index(self, entries, parent):
        for o in entries:
            o["ParentModuleId"] = parent
            self.objects[str(o["Id"])] = o
            self._index(o.get("children", []), o["Id"])

    def guard(self):
        if not self.writable: raise PermissionError("source is read only")

    def _write(self, what):
        self.guard(); self.writes.append(what)

    def _list(self, parent):
        return self.state["modules"] if parent is None else self.objects[str(parent)].setdefault("children", [])

    async def download(self, url):
        path = url.split(HOST, 1)[-1].split("?")[0].split("#")[0]
        if "managefiles/file?path=" in url:
            from urllib.parse import unquote
            path = self.state["root"] + unquote(url.split("path=", 1)[1])
        return self.files[path], "application/pdf" if path.endswith(".pdf") else "text/html"

    async def inventory(self, log=None, stop=None, include_html=True):
        result = copy.deepcopy(self.state)
        def add_html(entries):
            for o in entries:
                add_html(o.get("children", []))
                url = o.get("Url") or ""
                if include_html and o.get("Type") == 1 and url.endswith(".html") and url in self.files and url.startswith(self.state["root"]):
                    o["html"] = self.files[url].decode()
        add_html(result["modules"])
        return result

    async def children(self, parent):
        return copy.deepcopy([{k: v for k, v in o.items() if k != "children"} for o in self._list(parent)])

    async def create_object(self, parent, payload):
        self._write(("create", payload["Title"]))
        self.next_id += 1
        obj = {**{k: v for k, v in payload.items() if k != "Description"}, "Id": self.next_id, "ParentModuleId": parent,
               "Description": {"Html": payload["Description"]["Content"]}}
        if payload["Type"] == 0: obj["children"] = []
        self._list(parent).append(obj); self.objects[str(obj["Id"])] = obj
        return copy.deepcopy(obj)

    async def find_owned(self, parent, marker):
        found = [o for o in await self.children(parent) if marker in (o.get("Description") or {}).get("Html", "")]
        return found[0] if found else None

    async def order(self, object_id):
        self._write(("order", object_id))
        obj = self.objects[str(object_id)]
        siblings = self._list(obj["ParentModuleId"])
        siblings.remove(obj); siblings.append(obj)

    async def file_info(self, url):
        from urllib.parse import unquote
        path = unquote(url.split(HOST, 1)[-1].split("?")[0].split("#")[0])
        found = next((k for k in self.files if k.lower() == path.lower()), None)
        return (200, len(self.files[found])) if found else (404, 0)

    async def ensure_files_folder(self, folder): self._write(("folder", folder))
    async def file_exists(self, relative): return self.state["root"] + relative in self.files
    async def file_hash(self, relative): return hashlib.sha256(self.files[self.state["root"] + relative]).hexdigest()

    async def upload_file(self, folder, filename, body, type_, overwrite=False):
        self._write(("upload", filename))
        self.files[self.state["root"] + folder + "/" + filename] = body

    async def _json(self, path, method="GET", payload=None):
        if method != "GET": self._write((method, path))
        tail = re.sub(r"^/d2l/api/(?:le|lp)/[\d.]+/\d+/", "", path)
        if tail.startswith("managefiles"): return {}
        table = {"grades/categories/": "categories", "grades/": "grades", "dropbox/folders/": "assignments"}
        for prefix, key in table.items():
            if tail == prefix:
                if method == "GET": return copy.deepcopy(self.state[key])
                self.next_id += 1
                item = {**payload, "Id": self.next_id}
                if "CustomInstructions" in item: item["CustomInstructions"] = {"Html": payload["CustomInstructions"]["Content"]}
                item.setdefault("Assessment", {}).setdefault("Rubrics", [])
                self.state[key].append(item)
                return copy.deepcopy(item)
            match = re.fullmatch(re.escape(prefix) + r"(\d+)", tail)
            if match:
                item = next(i for i in self.state[key] if str(i["Id"]) == match.group(1))
                if method == "PUT":
                    update = dict(payload)
                    if "CustomInstructions" in update: update["CustomInstructions"] = {"Html": update["CustomInstructions"]["Content"]}
                    if "Assessment" in update: update["Assessment"] = {**item.get("Assessment", {}), **update["Assessment"]}
                    item.update(update)
                return copy.deepcopy(item)
        raise AssertionError("Unexpected API call " + path)


def offline_courses():
    source = source_course()
    target = {"id": "2", "name": "Target sandbox", "root": "/content/enforced/2-tgt/", "modules": [],
              "assignments": [], "categories": [], "grades": [], "setup": {"GradingSystem": "Weighted"}, "versions": ["1.99"]}
    files = {"/content/enforced/1-src/reading.html": source["modules"][0]["children"][0]["html"].encode(),
             "/content/enforced/1-src/notes.pdf": b"%PDF notes", "/content/enforced/1-src/prompts.pdf": b"%PDF prompts",
             "/content/enforced/1-src/background.pdf": b"%PDF background"}
    return FakeCourse(source, False, files), FakeCourse(target, True)


async def fake_link(self, key, parent, assignment):
    async def create():
        return await self.target.create_object(parent, {"Title": assignment["Name"], "Type": 1, "ActivityType": 3,
            "ToolItemId": assignment["Id"], "IsHidden": True, "Url": f"/d2l/common/dialogs/quickLink/quickLink.d2l?ou=2&rcode={assignment['Id']}",
            "Description": {"Content": "", "Type": 1}})
    async def find():
        found = [o for o in await self.target.children(parent) if str(o.get("ToolItemId")) == str(assignment["Id"])]
        return found[0] if found else None
    return await self.operation(key, create, find)


@pytest.mark.asyncio
async def test_custom_structure_runs_end_to_end_and_reruns_without_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(TransferRunner, "link_assignment", fake_link)
    source, target = offline_courses()
    templates = {kind: {"id": kind, "course": "2", "url": "/content/enforced/2-tgt/" + kind + ".html", "html": STYLE}
                 for kind in ("introduction", "session")}
    plan = build_plan(await source.inventory(), await target.inventory(), custom_recipe(), templates)
    plan["workshops"][0]["mapping_reviewed"] = True
    for row in plan["workshops"][0]["items"]:
        row["reviewed"] = True
    plan["review_acknowledged"] = True
    assert not validate_plan(plan)[0]
    path = tmp_path / "plan.json"
    from transfer_runner import save_plan
    save_plan(plan, path)

    result = await TransferRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values()), [k for k, ok in result["validation"].items() if not ok]
    assert source.writes == []

    unit = target.state["modules"][0]
    assert unit["Title"] == "Unit 1" and unit["IsHidden"]
    assert [c["Title"] for c in unit["children"]] == [
        "Overview", "Readings", "Session Overview", "Discussion prompts", "Assessment"]
    folders = {c["Title"]: c for c in unit["children"] if c["Type"] == 0}
    assert [c["Title"] for c in folders["Readings"]["children"]] == ["Read chapter 2", "Background"]
    assert [c["Title"] for c in folders["Assessment"]["children"]] == ["Week 1 Essay"]
    projects = next(c for c in target.state["categories"] if c["Name"] == "Projects")
    essay = next(a for a in target.state["assignments"] if a["Name"] == "Week 1 Essay")
    grade = next(g for g in target.state["grades"] if g["Id"] == essay["GradeItemId"])
    assert grade["CategoryId"] == projects["Id"]
    reading = next(f for p, f in target.files.items() if b"See" in f and b"notes" in f)
    assert b"1-src" not in reading and b"/content/enforced/2-tgt/" in reading
    assert b"style" not in reading.lower() and b"class=" not in reading  # copied pages are plain

    writes = len(target.writes)
    again = await TransferRunner(source, target, load_plan(path), path).run("2", True)
    assert all(again["validation"].values())
    assert len(target.writes) == writes, target.writes[writes:]


@pytest.mark.asyncio
async def test_unscored_task_copies_without_grade_while_other_assignments_keep_70_30(tmp_path, monkeypatch):
    monkeypatch.setattr(TransferRunner, 'link_assignment', fake_link)
    source, target = offline_courses()
    source.state['modules'][0]['children'].append({'Id': 16, 'Type': 1, 'Title': 'Confidentiality agreement',
        'ActivityType': 3, 'ToolItemId': 71, 'Url': '/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&type=dropbox&rcode=agreement'})
    task = copy.deepcopy(source.state['assignments'][0]); task.update(Id=71, Name='Confidentiality agreement', GradeItemId=None)
    task['Assessment']['ScoreDenominator'] = None; source.state['assignments'].append(task)
    recipe = workshop_recipe(); recipe['workshops'] = custom_recipe()['workshops']
    templates = {kind: {'id': kind, 'course': '2', 'url': '/content/enforced/2-tgt/' + kind + '.html', 'html': STYLE}
                 for kind in ('introduction', 'session')}
    plan = build_plan(await source.inventory(), await target.inventory(), recipe, templates)
    plan['workshops'][0]['mapping_reviewed'] = True
    row = next(r for r in plan['workshops'][0]['items'] if r['title'] == task['Name'])
    assert any('this task has no score' in e for e in validate_plan(plan)[0])
    row['grade_item'] = False
    for item in plan['workshops'][0]['items']: item['reviewed'] = True
    plan['review_acknowledged'] = True
    path = tmp_path / 'ungraded.json'
    from transfer_runner import save_plan
    save_plan(plan, path)
    result = await TransferRunner(source, target, plan, path).run('2', True)
    assert all(result['validation'].values()), [k for k, ok in result['validation'].items() if not ok]
    assert not next(a for a in target.state['assignments'] if a['Name'] == task['Name']).get('GradeItemId')
    assert not any(g['Name'] == task['Name'] for g in target.state['grades'])
    assert any(g['Name'] == 'Week 1 Essay' for g in target.state['grades'])
    assert {c['Name']: c['Weight'] for c in target.state['categories']} == {'Assignments': 70, 'Attendance and Participation': 30}
    assert source.writes == []
    writes = len(target.writes)
    again = await TransferRunner(source, target, load_plan(path), path).run('2', True)
    assert all(again['validation'].values()) and len(target.writes) == writes


def test_copy_without_grade_rejects_scored_assignments_and_non_boolean_choices():
    source = source_course()
    target = {**copy.deepcopy(source), 'id': '2', 'modules': [], 'assignments': []}
    plan = build_plan(source, target, custom_recipe(), {'introduction': {'html': STYLE}, 'session': {'html': STYLE}})
    row = next(r for r in plan['workshops'][0]['items'] if r['source'].get('ActivityType') == 3)
    row['grade_item'] = False
    assert any('only available for an unscored task' in e for e in validate_plan(plan)[0])
    row['grade_item'] = 'false'
    assert any('grade choice must be true or false' in e for e in validate_plan(plan)[0])


@pytest.mark.asyncio
async def test_saved_ea105_plan_still_builds_five_sections_with_original_journal_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(TransferRunner, "link_assignment", fake_link)
    source, target = offline_courses()
    templates = {kind: {"id": kind, "course": "2", "url": "/content/enforced/2-tgt/" + kind + ".html", "html": STYLE}
                 for kind in ("introduction", "session")}
    recipe = legacy_recipe()
    recipe["workshops"] = custom_recipe()["workshops"]
    plan = build_plan(await source.inventory(), await target.inventory(), recipe, templates)
    plan["workshops"][0]["mapping_reviewed"] = True
    for row in plan["workshops"][0]["items"]:
        row["reviewed"] = True
    plan["review_acknowledged"] = True
    path = tmp_path / "plan.json"
    from transfer_runner import save_plan
    save_plan(plan, path)
    result = await TransferRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values()), [k for k, ok in result["validation"].items() if not ok]
    assert [c["Title"] for c in target.state["modules"][0]["children"]] == list(SECTIONS)
    for key in ("w0:classroom", "w0:introduction", "w0:session", "w0:section:After Class",
                "category:Assignments", "category:Attendance/Participation"):
        assert result["journal"][key]["state"] == "verified", key
    assert {c["Name"] for c in target.state["categories"]} == {"Assignments", "Attendance and Participation"}


# ── Page splitting and plain pages ────────────────────────────────────────

PHASED_PAGE = """<!doctype html><html><head><style>body{color:red}</style><link rel="stylesheet" href="x.css"></head>
<body><div class="main"><header class="hero"><h1>Safety</h1><img alt="OC" src="logo.png"></header>
<div class="content"><div class="card">
<p>Opening words about the safety workshop for everyone.</p>
<p><strong>Instructions for BEFORE Workshop:</strong></p><p style="color:blue">Read <a href="notes.pdf">the notes</a>.</p>
<p><strong>Materials for SYNCHRONOUS Workshop</strong></p><div class="video"><iframe src="https://www.youtube.com/embed/x"></iframe></div>
<p><strong>Instructions for AFTER Workshop</strong></p><ol><li>Reflect on it.</li></ol>
</div></div></div></body></html>"""


def test_labelled_page_splits_into_plain_pieces_without_losing_words():
    from transfer_split import plain_document, split_page
    from bs4 import BeautifulSoup
    split = split_page(PHASED_PAGE)
    assert split["complete"] and [p["phase"] for p in split["pieces"]] == ["lead", "before", "during", "after"]
    words = lambda html: BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split()
    body = BeautifulSoup(PHASED_PAGE, "html.parser"); body.find("header").decompose()
    assert [w for p in split["pieces"] for w in words(p["html"])] == words(str(body.body))
    page = plain_document(split["pieces"][1]["html"], "Safety: BEFORE Workshop")
    assert "style" not in page.lower() and "class=" not in page and "x.css" not in page
    assert 'href="notes.pdf"' in page and "Read" in page and "<title>Safety: BEFORE Workshop</title>" in page
    video = plain_document(split["pieces"][2]["html"], "Safety: Materials")
    assert 'src="https://www.youtube.com/embed/x"' in video and 'width="560"' in video


def test_prose_that_mentions_after_is_not_a_label_and_unlabelled_parts_are_not_guessed():
    from transfer_split import split_page
    prose = "<body><p>After viewing the video, please review the following documents:</p><ul><li>One</li></ul></body>"
    assert not split_page(prose)["marked"]
    parts = ("<body><p><strong>Part 1: Think!</strong> Instructions for BEFORE workshop:</p><p>Questions.</p>"
             "<p><strong>Part 2: Explore</strong> the presentation.</p><p><strong>Part 3: Show it</strong></p><p>Do it.</p></body>")
    assert [p["phase"] for p in split_page(parts)["pieces"]] == ["before", "unknown", "unknown"]


def phased_plan():
    source = source_course()
    source["modules"][0]["children"].append({"Id": 16, "Type": 1, "Title": "Safety", "ActivityType": 1, "TopicType": 1,
                                             "Url": "/content/enforced/1-src/safety.html", "html": PHASED_PAGE})
    target = {**copy.deepcopy(source), "id": "2", "modules": [], "assignments": []}
    recipe = workshop_recipe(); recipe["workshops"] = custom_recipe()["workshops"]
    return build_plan(source, target, recipe, {"introduction": {"html": STYLE}, "session": {"html": STYLE}})


def test_ea105_plan_places_split_pieces_by_their_labels():
    plan = phased_plan()
    rows = {r["id"]: r for r in plan["workshops"][0]["items"]}
    assert [rows[f"16#{n}"]["section"] for n in range(1, 5)] == ["Class Materials", "Before Class", "Class Materials", "After Class"]
    assert rows["16#2"]["title"] == "Safety: BEFORE Workshop" and rows["16#2"]["reviewed"]
    assert rows["12"]["section"] == "Class Materials" and rows["12"]["reason"].startswith("Rule: files")
    assert rows["13"]["section"] == "After Class"


def test_tampered_or_stale_pieces_block_and_keeping_a_page_whole_is_reversible():
    from transfer_guided import set_page_split
    plan = phased_plan(); w = plan["workshops"][0]; w["mapping_reviewed"] = True; plan["review_acknowledged"] = True
    piece = next(r for r in w["items"] if r["id"] == "16#2")
    piece["piece"]["html"] = "<p>Something else</p>"
    assert any("pieces no longer match" in e for e in validate_plan(plan)[0])
    set_page_split(plan, w, "16", False)
    assert [r["id"] for r in w["items"] if r["id"].startswith("16")] == ["16"]
    assert not any("pieces no longer match" in e for e in validate_plan(plan)[0])
    set_page_split(plan, w, "16", True)
    assert [r["id"] for r in w["items"] if r["id"].startswith("16")] == ["16#1", "16#2", "16#3", "16#4"]


@pytest.mark.asyncio
async def test_split_pages_run_end_to_end_as_separate_plain_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(TransferRunner, "link_assignment", fake_link)
    source, target = offline_courses()
    page = {"Id": 16, "Type": 1, "Title": "Safety", "ActivityType": 1, "TopicType": 1,
            "Url": "/content/enforced/1-src/safety.html", "html": PHASED_PAGE}
    source.state["modules"][0]["children"].append(page); source.objects["16"] = page; page["ParentModuleId"] = 10
    source.files["/content/enforced/1-src/safety.html"] = PHASED_PAGE.encode()
    templates = {kind: {"id": kind, "course": "2", "url": "/content/enforced/2-tgt/" + kind + ".html", "html": STYLE}
                 for kind in ("introduction", "session")}
    recipe = workshop_recipe(); recipe["workshops"] = custom_recipe()["workshops"]; recipe["gradebook"]["enabled"] = False
    plan = build_plan(await source.inventory(), await target.inventory(), recipe, templates)
    plan["workshops"][0]["mapping_reviewed"] = True
    for row in plan["workshops"][0]["items"]: row["reviewed"] = True
    plan["review_acknowledged"] = True
    assert not validate_plan(plan)[0], validate_plan(plan)[0]
    path = tmp_path / "plan.json"
    from transfer_runner import save_plan
    save_plan(plan, path)
    result = await TransferRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values()), [k for k, ok in result["validation"].items() if not ok]
    unit = target.state["modules"][0]
    folders = {c["Title"]: [x["Title"] for x in c.get("children", [])] for c in unit["children"] if c["Type"] == 0}
    assert "Safety: BEFORE Workshop" in folders["Before Class"]
    assert "Safety: AFTER Workshop" in folders["After Class"]
    assert {"Safety: Overview", "Safety: Materials for SYNCHRONOUS Workshop"} <= set(folders["Class Materials"])
    before = next(o for o in all_objects(unit["children"]) if o["Title"] == "Safety: BEFORE Workshop")
    body = target.files[before["Url"]].decode()
    assert "Read" in body and "Reflect" not in body and "style" not in body.lower() and "/content/enforced/2-tgt/" in body
