"""Pure recipe/planning rules. No credentials, browser calls, or course writes."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from uuid import uuid4

# Schema 1 recipes (and plans saved with them) always use this fixed EA105
# structure. Schema 2 recipes describe their own sections; read either through
# recipe_sections() rather than this constant.
SECTIONS = ("Online Classroom", "Module Introduction", "Before Class", "Class Materials", "After Class")
SECTION_KINDS = ("placeholder", "introduction", "folder", "inline")
ITEM_KINDS = ("folder", "inline")
SKIP = "Skip"
CLASSROOM_MESSAGE = "The instructor will add the online classroom link before publication."
REVIEW_ASSIGNMENT_WORDS = ["optional", "alternate", "agreement", "photo id", "preferences"]
CATEGORY_ROLES = ("", "assignments", "attendance")


def category_name(spec, label):
    """Brightspace forbids slash characters in grade category names."""
    return spec.get("category_names", {}).get(label, label.replace("/", " and "))


def _legacy_sections(recipe):
    keywords = recipe.get("keywords", {})
    return [
        {"title": "Online Classroom", "kind": "placeholder", "key": "classroom", "message": CLASSROOM_MESSAGE},
        {"title": "Module Introduction", "kind": "introduction", "key": "introduction"},
        {"title": "Before Class", "kind": "folder", "key": "section:Before Class", "keywords": keywords.get("Before Class", [])},
        {"title": "Class Materials", "kind": "folder", "key": "section:Class Materials",
         "keywords": keywords.get("Class Materials", []), "session_overview": True, "default": True},
        {"title": "After Class", "kind": "folder", "key": "section:After Class",
         "keywords": keywords.get("After Class", []), "assignments": True},
    ]


def _section_key(spec):
    if spec.get("key"): return str(spec["key"])
    kind, title = spec.get("kind"), str(spec.get("title", ""))
    if kind == "introduction": return "introduction"
    if kind == "folder": return "section:" + title
    return ("page:" if kind == "placeholder" else "inline:") + norm(title).replace(" ", "-")


def recipe_sections(recipe):
    """The ordered children every unit gets, with defaults filled in."""
    if recipe.get("schema", 1) == 1:
        specs = _legacy_sections(recipe)
    else:
        specs = recipe.get("sections", [])
    sections = []
    for spec in specs:
        if not isinstance(spec, dict): raise ValueError("Each section must be an object with a title and kind.")
        sections.append({"title": str(spec.get("title", "")).strip(), "kind": spec.get("kind"),
                         "key": _section_key(spec), "keywords": list(spec.get("keywords", [])),
                         "aliases": list(spec.get("aliases", [])),
                         "message": spec.get("message") or (CLASSROOM_MESSAGE if spec.get("kind") == "placeholder" else ""),
                         "session_overview": bool(spec.get("session_overview")),
                         "assignments": bool(spec.get("assignments")), "default": bool(spec.get("default")),
                         "phase": spec.get("phase") or "", "files": bool(spec.get("files"))})
    return sections


def item_sections(recipe):
    """Sections that can hold transferred items, in order."""
    return [s for s in recipe_sections(recipe) if s["kind"] in ITEM_KINDS]


def destinations(recipe):
    return [s["title"] for s in item_sections(recipe)] + [SKIP]


def default_section(recipe):
    """Where items with no structural or keyword evidence are suggested."""
    sections = item_sections(recipe)
    if not sections: return SKIP
    for flag in ("default", "session_overview"):
        for s in sections:
            if s[flag]: return s["title"]
    return sections[0]["title"]


def assignment_sections(recipe):
    """Sections assignments must use. Empty means any item section."""
    return [s["title"] for s in item_sections(recipe) if s["assignments"]]


def section_named(recipe, title):
    return next((s for s in recipe_sections(recipe) if s["title"] == title), None)


def session_section(recipe):
    return next((s for s in item_sections(recipe) if s["session_overview"]), None)


def section_for(recipe, flag, value=True):
    """Title of the item section with this flag (or phase), if any."""
    return next((s["title"] for s in item_sections(recipe) if s[flag] == value), None)


def item_kind(item):
    """assignment | page | file (documents, media, links, videos) | activity (quiz, survey...)."""
    activity = item.get("ActivityType")
    if activity == 3: return "assignment"
    if activity not in (1, 2): return "activity"
    url = (item.get("Url") or "").lower().split("?")[0].split("#")[0]
    return "page" if item.get("html") is not None or url.endswith((".html", ".htm")) else "file"


def has_introduction(recipe):
    return any(s["kind"] == "introduction" for s in recipe_sections(recipe))


def grade_categories(spec):
    """Gradebook categories as [{label, name, weight, role}] for either schema."""
    categories = spec.get("categories", {})
    if isinstance(categories, dict):
        roles = {"Assignments": "assignments", "Attendance/Participation": "attendance"}
        return [{"label": label, "name": category_name(spec, label), "weight": weight, "role": roles.get(label, "")}
                for label, weight in categories.items()]
    return [{"label": str(c.get("label") or c.get("name", "")), "name": str(c.get("name", "")),
             "weight": c.get("weight"), "role": c.get("role", "")} for c in categories]


def category_for_role(spec, role):
    return next((c for c in grade_categories(spec) if c["role"] == role), None)


def upgrade_recipe(recipe):
    """Schema 2 copy of a schema 1 recipe; journal keys and labels stay identical."""
    recipe = copy.deepcopy(recipe)
    if recipe.get("schema", 1) != 1: return recipe
    recipe["sections"] = [{k: v for k, v in s.items() if v not in (False, [], None) or k == "kind"}
                          for s in _legacy_sections(recipe)]
    grades = recipe.get("gradebook", {})
    if isinstance(grades.get("categories"), dict):
        grades["categories"] = [{"name": c["name"], "weight": c["weight"],
                                 **({"label": c["label"]} if c["label"] != c["name"] else {}),
                                 **({"role": c["role"]} if c["role"] else {})} for c in grade_categories(grades)]
        grades.pop("category_names", None)
    recipe.pop("keywords", None)
    recipe["schema"] = 2
    return recipe


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def norm(value):
    return re.sub(r"[^\w]+", " ", str(value).casefold()).strip()


EA105_SECTIONS = [
    {"title": "Online Classroom", "kind": "placeholder", "key": "classroom", "message": CLASSROOM_MESSAGE},
    {"title": "Module Introduction", "kind": "introduction"},
    {"title": "Before Class", "kind": "folder", "phase": "before",
     "aliases": ["Pre-Class", "Preparation", "Before the Workshop"]},
    {"title": "Class Materials", "kind": "folder", "session_overview": True, "default": True, "phase": "during", "files": True,
     "aliases": ["Session Materials", "Workshop Materials"]},
    {"title": "After Class", "kind": "folder", "assignments": True, "phase": "after",
     "aliases": ["Post-Class", "After the Workshop"]},
]


def workshop_recipe():
    """Business example is data; all transfer behavior is recipe-independent."""
    titles = [
        ("Orientation and Successful Online Learning", 6, ["Orientation", "Successful Online Learning"]),
        ("Reconciliation through Indigenous Education", 18, ["Reconciliation Through Indigenous Education"]),
        ("Observation Day", 6, ["Observation Day"]),
        ("Physical Health and Safety", 6, ["Physical Health and Safety"]),
        ("Sexuality and Exceptionalities", 6, ["Sexuality and Exceptionalities"]),
        ("Anxiety in the Workplace", 6, ["Anxiety in the Workplace"]),
        ("Mental Health and Self-Care", 6, ["Mental Health and Self Care"]),
        ("Supports in Schools", 6, ["Supports in Schools"]),
        ("Employment Readiness", 6, ["Resumes and Cover Letters", "Interview Skills and Job Applications"]),
    ]
    return {"schema": 2, "name": "Workshop transfer", "unit_label": "Workshop",
        "sections": copy.deepcopy(EA105_SECTIONS),
        "templates": {"introduction_title": "Module Introduction template", "session_title": "Session Overview template"},
        "remove_introduction_sections": ["In this module you will explore"],
        "remove_session_sections": ["Completion Check", "Engagement Activities"],
        "review_assignment_keywords": list(REVIEW_ASSIGNMENT_WORDS),
        # How content is placed: page labels and titles that say before/after,
        # assignments to their section, files/links/videos and everything else
        # to the default section. Copied pages lose all styling.
        "split_pages": True, "default_is_rule": True, "page_style": "plain",
        "gradebook": {"enabled": True, "categories": [
                          {"name": "Assignments", "weight": 70, "role": "assignments"},
                          {"name": "Attendance and Participation", "label": "Attendance/Participation", "weight": 30, "role": "attendance"}],
                      "assignment_distribution": 1, "attendance_items": []},
        "workshops": [{"title": title, "hours": hours, "source_titles": sources,
                       "introduction": "", "outcomes": [], "session": ""} for title, hours, sources in titles]}


def _validate_sections(recipe):
    if recipe.get("schema") == 1:
        if tuple(recipe.get("sections", [])) != SECTIONS:
            raise ValueError("Recipe schema 1 requires the five ordered workshop sections.")
        return
    if recipe.get("schema") != 2:
        raise ValueError("Unsupported recipe schema; use schema 2.")
    raw = recipe.get("sections")
    if not isinstance(raw, list) or not raw:
        raise ValueError("The recipe needs at least one section.")
    sections = recipe_sections(recipe)
    titles = [norm(s["title"]) for s in sections]
    if any(not t for t in titles) or len(set(titles)) != len(titles) or norm(SKIP) in titles:
        raise ValueError("Section titles must be nonempty, unique, and not 'Skip'.")
    if any(len(s["title"]) > 150 for s in sections):
        raise ValueError("Section titles must be at most 150 characters.")
    if any(s["kind"] not in SECTION_KINDS for s in sections):
        raise ValueError("Section kind must be one of: " + ", ".join(SECTION_KINDS) + ".")
    if sum(s["kind"] == "introduction" for s in sections) > 1:
        raise ValueError("Only one section can be the introduction page.")
    if len({s["key"] for s in sections}) != len(sections):
        raise ValueError("Section keys must be unique.")
    if not any(s["kind"] in ITEM_KINDS for s in sections):
        raise ValueError("At least one section must hold transferred content (a folder or inline section).")
    for flag, label in (("session_overview", "Session Overview"), ("default", "default for unsure items"),
                        ("files", "files, links and videos")):
        if sum(s[flag] for s in sections) > 1:
            raise ValueError(f"Only one section can hold the {label}.")
    for phase in ("before", "during", "after"):
        if sum(s["phase"] == phase for s in sections) > 1:
            raise ValueError(f"Only one section can take content labelled {phase}.")
    if any(s["phase"] not in ("", "before", "during", "after") for s in sections):
        raise ValueError("A section's phase must be before, during, after or empty.")
    if recipe.get("page_style", "original") not in ("original", "plain"):
        raise ValueError("page_style must be 'original' or 'plain'.")
    for s in sections:
        if s["kind"] not in ITEM_KINDS and (s["session_overview"] or s["assignments"] or s["default"] or s["keywords"]
                                            or s["phase"] or s["files"]):
            raise ValueError(f"{s['title']}: only folder or inline sections can hold content, assignments or a Session Overview.")


def _validate_gradebook(recipe):
    grades = recipe.get("gradebook", {})
    categories = grade_categories(grades)
    if recipe.get("schema") == 1:
        legacy = grades.get("categories", {})
        if grades.get("enabled") and (set(legacy) != {"Assignments", "Attendance/Participation"}
                or any(not isinstance(n, (int, float)) or not 0 <= n <= 100 for n in legacy.values())
                or abs(sum(legacy.values()) - 100) > 1e-6):
            raise ValueError("The two category weights must total 100%.")
    elif grades.get("enabled"):
        if not categories:
            raise ValueError("Add at least one grade category, or turn gradebook setup off.")
        if any(not isinstance(c["weight"], (int, float)) or isinstance(c["weight"], bool) or not 0 <= c["weight"] <= 100
               for c in categories) or abs(sum(c["weight"] for c in categories) - 100) > 1e-6:
            raise ValueError("Grade category weights must each be 0–100% and total 100%.")
        if any(c["role"] not in CATEGORY_ROLES for c in categories):
            raise ValueError("A grade category role must be 'assignments', 'attendance' or empty.")
        for role in CATEGORY_ROLES[1:]:
            if sum(c["role"] == role for c in categories) > 1:
                raise ValueError(f"Only one grade category can hold {role}.")
    names = [c["name"] for c in categories]
    if len({norm(n) for n in names}) != len(names) or any(
            not name or len(name) > 128 or re.search(r'[/"*<>+=|,%]', name) for name in names):
        raise ValueError("Grade category names must be unique and use Brightspace-supported characters.")
    if len({c["label"] for c in categories}) != len(categories):
        raise ValueError("Grade category labels must be unique.")
    attendance = grades.get("attendance_items", [])
    if attendance and not grades.get("enabled"):
        raise ValueError("Attendance grade items require gradebook setup to be enabled.")
    if attendance and not category_for_role(grades, "attendance"):
        raise ValueError("Attendance grade items need a category with the attendance role.")
    if grades.get("assignment_distribution", 1) != 1:
        raise ValueError("Assignment category uses equal distribution; custom assignment weights require native gradebook setup.")
    if len({norm(a.get("name", "")) for a in attendance}) != len(attendance) or any(not a.get("name") for a in attendance):
        raise ValueError("Attendance items need unique names.")
    if attendance and (any(not 0 < a.get("weight", 0) <= 100 or a.get("max_points", 0) <= 0 for a in attendance)
                       or abs(sum(a["weight"] for a in attendance) - 100) > 1e-6):
        raise ValueError("Attendance items need positive max_points and category weights totaling 100%.")


def validate_recipe(recipe):
    _validate_sections(recipe)
    workshops = recipe.get("workshops", [])
    titles = [norm(w.get("title", "")) for w in workshops]
    if not titles or any(not t for t in titles) or len(set(titles)) != len(titles):
        raise ValueError("Recipe needs uniquely named workshops.")
    for w in workshops:
        if len(w["title"]) > 150 or not isinstance(w.get("source_titles"), list):
            raise ValueError("Each workshop needs source_titles and a title of at most 150 characters.")
        if not isinstance(w.get("outcomes", []), list):
            raise ValueError("Learning outcomes must be a list of text strings.")
        if not all(isinstance(x, str) for x in w.get("outcomes", [])):
            raise ValueError("Each learning outcome must be text.")
    _validate_gradebook(recipe)


def all_objects(modules):
    for module in modules:
        yield module
        yield from all_objects(module.get("children", []))


def classify(item, ancestors, recipe):
    """Where an item goes, most reliable evidence first.

    1. The source folder it already sits in.  2. Assignments go to the assignment
    section.  3. A title that says before/during/after.  4. Files, links and
    videos go to the files section.  5. Title keywords (a suggestion only).
    6. Everything else goes to the default section: a rule when the recipe says
    so, otherwise left for review.
    """
    from transfer_split import text_phase
    sections = item_sections(recipe)
    for ancestor in reversed(ancestors):
        for section in sections:
            if norm(ancestor) in {norm(section["title"]), *(norm(a) for a in section["aliases"])}:
                return section["title"], "deterministic", f"Inside source section {ancestor}"
    title = norm(item.get("Title", ""))
    kind = item_kind(item)
    if kind == "assignment":
        homes = assignment_sections(recipe)
        if not homes:
            return default_section(recipe), "inferred", "Assignment; this structure has no dedicated assignment section"
        words = recipe.get("review_assignment_keywords", REVIEW_ASSIGNMENT_WORDS)
        note = " (title suggests an optional task or a form; skip it if it isn't needed)" if any(norm(w) in title for w in words) else ""
        if recipe.get("default_is_rule") or not note:
            return homes[0], "deterministic", f"Rule: assignments go to {homes[0]}" + note
        return homes[0], "inferred", "Form or optional assignment; review inclusion and workshop purpose"
    phase = text_phase(item.get("Title", ""))
    home = section_for(recipe, "phase", phase) if phase else None
    if home:
        return home, "deterministic", f"Rule: the title says {phase}"
    if kind == "file" and section_for(recipe, "files"):
        return section_for(recipe, "files"), "deterministic", f"Rule: files, links and videos go to {section_for(recipe, 'files')}"
    suggestions = {s["title"] for s in sections if any(norm(word) in title for word in s["keywords"])}
    if len(suggestions) == 1:
        return suggestions.pop(), "inferred", "Title suggests a destination; review required"
    if recipe.get("default_is_rule") and sections:
        return default_section(recipe), "deterministic", f"Rule: everything else goes to {default_section(recipe)}"
    return default_section(recipe), "unresolved", "No clear structural destination; review required"


def base_id(row_or_id):
    """Source topic ID of a row; split pages use '<id>#<piece>'."""
    value = row_or_id["id"] if isinstance(row_or_id, dict) else row_or_id
    return str(value).split("#", 1)[0]


def page_pieces(item, path, recipe):
    """Rows for one page split at its own labels, or None to keep it whole."""
    from transfer_split import short_label, split_page
    if not recipe.get("split_pages") or item_kind(item) != "page" or item.get("html") is None: return None
    split = split_page(item["html"])
    if not split["complete"] or not split["marked"]: return None
    whole_section, whole_confidence, whole_reason = classify(item, path, recipe)
    rows = []
    for index, piece in enumerate(split["pieces"], 1):
        home = section_for(recipe, "phase", piece["phase"]) if piece["phase"] in ("before", "during", "after") else None
        if home:
            section, confidence, reason = home, "deterministic", f"Page label: “{piece['marker']}”"
        else:
            section, confidence, reason = whole_section, whole_confidence, whole_reason + " (page part with no before/after label)"
        rows.append({"title": f"{item['Title']}: {short_label(piece['marker'], piece['phase'])}" if len(split["pieces"]) > 1 else item["Title"],
                     "section": section, "confidence": confidence, "reason": reason,
                     "piece": {"index": index, "count": len(split["pieces"]), "phase": piece["phase"],
                               "marker": piece["marker"], "html": piece["html"]}})
    if len(rows) == 1:
        rows[0].pop("piece")  # one labelled part: the page moves whole, guided by its label
    return rows


def rubric_signature(value):
    """Compare authored structure without cross-course object identifiers."""
    if isinstance(value, dict):
        return {k: rubric_signature(v) for k, v in value.items()
                if not k.endswith("Id") and k not in ("Id", "ActivityId")}
    if isinstance(value, list):
        return [rubric_signature(v) for v in value]
    return value


def rubric_warnings(assignment):
    from bs4 import BeautifulSoup
    instructions = BeautifulSoup((assignment.get("CustomInstructions") or {}).get("Html", ""), "html.parser").get_text(" ", strip=True)
    parts = set(re.findall(r"\bpart\s*(\d+)\b", instructions, re.I))
    rubrics = (assignment.get("Assessment") or {}).get("Rubrics", [])
    labels = " ".join(label for r in rubrics for g in r.get("CriteriaGroups", [])
                      for label in [g.get("Name", ""), *[c.get("Name", "") for c in g.get("Criteria", [])]])
    rubric_parts = set(re.findall(r"\bpart\s*(\d+)\b", labels, re.I))
    if parts and rubrics and parts != rubric_parts:
        return ["Assignment parts and rubric criteria need comparison; wording was preserved."]
    return []


def build_plan(source, target, recipe, templates=None):
    validate_recipe(recipe)
    if source["id"] == target["id"]:
        raise ValueError("Source and target must be different courses.")
    recipe = copy.deepcopy(recipe)
    plan = {"schema": 1, "id": uuid4().hex, "source": source, "target": target,
            "source_hash": digest(source), "recipe": recipe, "workshops": [],
            "templates": templates or {}, "review_acknowledged": False, "journal": {}, "report": ""}
    assigned = set()
    for spec in recipe["workshops"]:
        matches = []
        missing = []
        if spec.get("source_ids"):
            for id_ in spec['source_ids']:
                found = [m for m in all_objects(source['modules']) if m.get('Type') == 0 and str(m['Id']) == str(id_)]
                if len(found) == 1: matches.append(str(id_))
                else: missing.append('A selected source workshop is no longer available. Scan again.')
        for title in ([] if spec.get('source_ids') else spec["source_titles"]):
            found = [m for m in all_objects(source["modules"]) if m.get("Type") == 0 and norm(m["Title"]) == norm(title)]
            if len(found) != 1:
                missing.append(f"Source module {title!r}: {len(found)} matches; select module IDs explicitly.")
            else:
                matches.append(str(found[0]["Id"]))
        workshop = {**spec, "source_module_ids": matches, "mapping_reviewed": False,
                    "mapping_notes": missing, "items": []}
        plan["workshops"].append(workshop)
        for module_id in matches:
            assigned.add(module_id)
        refresh_items(plan, workshop)
    plan["unmapped_modules"] = [m["Title"] for m in source["modules"] if str(m["Id"]) not in assigned]
    return plan


def refresh_items(plan, workshop):
    """Rebuild one workshop after the user corrects its module mapping."""
    old = {i["id"]: i for i in workshop.get("items", [])}
    workshop["items"] = []
    lookup = {str(m["Id"]): m for m in all_objects(plan["source"]["modules"]) if m.get("Type") == 0}
    valid = set(destinations(plan["recipe"]))
    def walk(module, path):
        for item in module.get("children", []):
            if item.get("Type") == 0:
                walk(item, [*path, item["Title"]])
            else:
                id_ = str(item["Id"])
                pieces = None if id_ in workshop.get("unsplit", []) else page_pieces(item, path, plan["recipe"])
                if pieces is None:
                    section, confidence, reason = classify(item, path, plan["recipe"])
                    pieces = [{"title": item["Title"], "section": section, "confidence": confidence, "reason": reason}]
                for piece in pieces:
                    row_id = id_ + (f"#{piece['piece']['index']}" if piece.get("piece") else "")
                    row = {"id": row_id, "title": piece["title"], "source": item, "path": " / ".join(path),
                           "section": piece["section"], "confidence": piece["confidence"], "reason": piece["reason"],
                           "reviewed": piece["confidence"] == "deterministic", "target_assignment_id": ""}
                    if piece.get("piece"): row["piece"] = piece["piece"]
                    if row_id in old:
                        row["target_assignment_id"] = old[row_id].get("target_assignment_id", "")
                        if 'grade_item' in old[row_id]: row['grade_item'] = old[row_id]['grade_item']
                        # A decision survives only while its section still exists.
                        if old[row_id]["section"] in valid:
                            row.update({k: old[row_id][k] for k in ("section", "reviewed")})
                    workshop["items"].append(row)
    for id_ in workshop["source_module_ids"]:
        if id_ in lookup:
            walk(lookup[id_], [lookup[id_]["Title"]])
    assigned = {id_ for w in plan["workshops"] for id_ in w["source_module_ids"]}
    plan["unmapped_modules"] = [m["Title"] for m in plan["source"]["modules"] if str(m["Id"]) not in assigned]
    plan["review_acknowledged"] = False


def assignment_for(plan, row):
    source = row["source"]
    matches = [a for a in plan["source"].get("assignments", []) if str(a["Id"]) == str(source.get("ToolItemId"))]
    if not matches:
        matches = [a for a in plan["source"].get("assignments", []) if norm(a["Name"]) == norm(row["title"])]
    return matches[0] if len(matches) == 1 else None


def native_required(assignment, versions):
    return bool((assignment.get("Assessment") or {}).get("Rubrics")
                or assignment.get("GroupTypeId") or assignment.get("Attachments")
                or assignment.get("LinkAttachments") or assignment.get("AllowOnlyUsersWithSpecialAccess"))


def assignment_grading(plan, row):
    grading = plan['recipe'].get('gradebook', {})
    return bool(grading.get('enabled') and category_for_role(grading, 'assignments') and row.get('grade_item') is not False)


def needs_grade_choice(plan, row):
    if row['section'] == SKIP or row['source'].get('ActivityType') != 3: return False
    assignment = assignment_for(plan, row)
    return bool(assignment and assignment_grading(plan, row)
                and not (assignment.get('Assessment') or {}).get('ScoreDenominator'))


def validate_plan(plan):
    """Return blockers and review notes. Never hides unsupported operations."""
    errors, warnings = [], []
    try:
        validate_recipe(plan["recipe"])
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    if plan["source"]["id"] == plan["target"]["id"]:
        errors.append("Source and target are the same course.")
    if digest(plan["source"]) != plan["source_hash"]:
        errors.append("Saved source inventory was edited; rescan the course.")
    recipe = plan["recipe"]
    try:
        allowed, homes = destinations(recipe), assignment_sections(recipe)
    except (ValueError, KeyError, TypeError):
        allowed, homes = [SKIP], []
    templates = plan.get("templates", {})
    if has_introduction(recipe) and not templates.get("introduction"):
        errors.append("Select an introduction template before applying.")
    if session_section(recipe) and not templates.get("session"):
        errors.append("Select a Session Overview template before applying.")
    if any(t.get('builtin') for t in plan.get('templates', {}).values()):
        warnings.append('Standard page formatting is being used where no unique development template was found. You can choose another style under Change page formatting.')
    seen, seen_items, seen_assignments = set(), set(), set()
    inventory = {str(o["Id"]): o for o in all_objects(plan["source"]["modules"])}
    titles = [norm(w.get("title", "")) for w in plan["workshops"]]
    if not titles or any(not t for t in titles) or len(titles) != len(set(titles)):
        errors.append("Workshop titles must be nonempty and unique.")
    owned = {str(j.get("result", {}).get("Id")) for j in plan.get("journal", {}).values()}
    for w in plan["workshops"]:
        if len(w.get("title", "")) > 150:
            errors.append("Workshop title exceeds 150 characters.")
        expected_items = {str(o["Id"]) for id_ in w["source_module_ids"] if id_ in inventory
                          for o in all_objects(inventory[id_].get("children", [])) if o.get("Type") == 1}
        if expected_items != {base_id(row) for row in w["items"]}:
            errors.append(f"{w['title']}: source content coverage changed; rebuild the mapping.")
        if not w["source_module_ids"] or not w["mapping_reviewed"]:
            errors.append(f"{w['title']}: review the source workshop mapping.")
        collisions = [m for m in plan["target"]["modules"] if norm(m["Title"]) == norm(w["title"]) and str(m["Id"]) not in owned]
        if collisions:
            errors.append(f"{w['title']}: target already has {len(collisions)} module(s); resolve existing content before applying.")
        for id_ in w["source_module_ids"]:
            if id_ not in inventory or inventory[id_].get("Type") != 0:
                errors.append(f"Unknown source module ID: {id_}")
            if id_ in seen:
                errors.append(f"Source module {id_} mapped to more than one workshop.")
            seen.add(id_)
        pieces = {}
        for row in w["items"]:
            if row.get("piece"): pieces.setdefault(base_id(row), []).append(row)
        for source_id, rows in pieces.items():
            fresh = page_pieces(inventory.get(source_id, {}), [], {**recipe, "split_pages": True}) or []
            if [r["piece"]["html"] for r in rows] != [p["piece"]["html"] for p in fresh if p.get("piece")]:
                errors.append(f"{rows[0]['source'].get('Title', source_id)}: page pieces no longer match the source page; rebuild the mapping.")
        if has_introduction(recipe) and (not w.get("introduction") or not w.get("outcomes")):
            warnings.append(f"{w['title']}: missing introduction/outcomes; a visible authoring placeholder will be created.")
        if session_section(recipe) and not w.get("session"):
            warnings.append(f"{w['title']}: Session Overview needs authoring; example lesson text will not be copied.")
        for row in w["items"]:
            if row["id"] in seen_items:
                errors.append(f"Source item {row['title']} is mapped more than once.")
            seen_items.add(row["id"])
            if inventory.get(base_id(row)) != row["source"]:
                errors.append(f"{row['title']}: item differs from the scanned source inventory.")
            if row["section"] not in allowed or not row["reviewed"]:
                errors.append(f"{row['title']}: choose/review a destination (or Skip).")
            if row["section"] == "Skip":
                continue
            item = row["source"]
            if item.get("html_error"):
                errors.append(f"{row['title']}: {item['html_error']} Restore the page and scan again, or Skip it.")
            if item.get("ActivityType") == 3:
                a = assignment_for(plan, row)
                if a is None:
                    errors.append(f"{row['title']}: assignment could not be identified uniquely.")
                elif native_required(a, plan["source"].get("versions", [])) and not row.get("target_assignment_id"):
                    errors.append(f"{row['title']}: copy this assignment with its rubric in Brightspace, then click Check copied assignments.")
                if a:
                    if str(a["Id"]) in seen_assignments:
                        errors.append(f"{row['title']}: this assignment is already included; keep one reviewed Content link.")
                    seen_assignments.add(str(a["Id"]))
                    warnings.extend(f"{row['title']}: {message}" for message in rubric_warnings(a))
                    if 'grade_item' in row and not isinstance(row['grade_item'], bool):
                        errors.append(f"{row['title']}: grade choice must be true or false.")
                    if needs_grade_choice(plan, row):
                        errors.append(f"{row['title']}: this task has no score. Choose Copy without a grade or Leave out of this transfer.")
                    if row.get('grade_item') is False:
                        warnings.append(f"{row['title']}: copied as an ungraded task, outside the gradebook.")
                        if (a.get('Assessment') or {}).get('ScoreDenominator') or a.get('GradeItemId'):
                            errors.append(f"{row['title']}: Copy without a grade is only available for an unscored task with no grade item.")
                        chosen = next((t for t in plan['target']['assignments'] if str(t['Id']) == str(row.get('target_assignment_id'))), {})
                        if chosen.get('GradeItemId'):
                            errors.append(f"{row['title']}: the copied task already has a grade item; review its grading in Brightspace.")
                    if row.get("target_assignment_id") and not any(str(t["Id"]) == str(row["target_assignment_id"]) for t in plan["target"]["assignments"]):
                        errors.append(f"{row['title']}: selected assignment is no longer in the target; click Check copied assignments.")
                    elif row.get('target_assignment_id'):
                        from transfer_guided import matching_assignment
                        chosen = next(t for t in plan['target']['assignments'] if str(t['Id']) == str(row['target_assignment_id']))
                        if not matching_assignment(a, chosen):
                            errors.append(f"{row['title']}: the selected target assignment's instructions, rubric, points or submission settings differ. Choose the matching copy.")
                if homes and row["section"] not in homes:
                    errors.append(f"{row['title']}: assignments must be in {' or '.join(homes)}.")
            elif item.get("ActivityType") not in (1, 2) or item.get("TopicType") not in (1, 3):
                errors.append(f"{row['title']}: unsupported activity; use native copy or Skip.")
    if plan.get("unmapped_modules"):
        warnings.append("Source modules outside the recipe: " + "; ".join(plan["unmapped_modules"]))
    warnings.append("Institutional outcome alignments, release conditions and tool-specific settings require instructor review.")
    warnings.append("Automatically recreated assignments are hidden, individual, and have no source dates or notification recipients. Review native copies' availability and tool settings before publication.")
    grades = plan["recipe"].get("gradebook", {})
    if grades.get("enabled"):
        categories = grade_categories(grades)
        actual_names = {c["name"] for c in categories}
        if plan["target"].get("setup", {}).get("GradingSystem") != "Weighted":
            errors.append("Target gradebook must be set to Weighted in Brightspace before applying.")
        for cat in plan["target"].get("categories", []):
            if cat["Name"] not in actual_names and cat.get("Grades"):
                errors.append(f"Existing grade category {cat['Name']} contains items; resolve the category mapping manually.")
            elif cat["Name"] not in actual_names:
                warnings.append(f"Grade category {cat['Name']} will be retained, excluded, and assigned 0%.")
        attendance = category_for_role(grades, "attendance")
        if attendance and not grades.get("attendance_items"):
            warnings.append(f"{attendance['name']} category will be created; individual attendance weighting is still required.")
        for c in categories:
            if c["name"] != c["label"]:
                warnings.append(f"{c['label']} will use the Brightspace category name {c['name']!r}.")
    if not plan.get("review_acknowledged"):
        errors.append("Acknowledge the review notes and proposed gradebook changes before applying.")
    return errors, list(dict.fromkeys(warnings))


def preview_text(plan):
    recipe = plan["recipe"]
    lines = [f"STRUCTURED TRANSFER — {recipe['name']}",
             f"Source (read only): {plan['source']['name']} ({plan['source']['id']})",
             f"Target: {plan['target']['name']} ({plan['target']['id']})", ""]
    describe = {"placeholder": "Create placeholder page", "introduction": "Clone styled template + introduction/outcomes"}
    for w in plan["workshops"]:
        lines.append(w['title'] + (f" ({w['hours']} hours)" if w.get('hours') else ''))
        for section in recipe_sections(recipe):
            lines.append(f"  {section['title']}" + ("  [items placed directly in the unit]" if section["kind"] == "inline" else ""))
            if section["kind"] in describe: lines.append("    " + describe[section["kind"]])
            if section["session_overview"]: lines.append("    Session Overview (clone styled template)")
            for row in w["items"]:
                if row["section"] == section["title"]:
                    lines.append(f"    {row['title']} [{row['confidence']}; {'reviewed' if row['reviewed'] else 'REVIEW REQUIRED'}]")
                    if row.get('grade_item') is False: lines.append('      Ungraded task — not included in the gradebook')
        for row in w["items"]:
            if row["section"] == SKIP: lines.append(f"  Skip: {row['title']}")
        lines.append("")
    if recipe.get("gradebook", {}).get("enabled"):
        lines.append("Gradebook: " + "; ".join(f"{c['name']}: {c['weight']}%" for c in grade_categories(recipe["gradebook"])))
    errors, warnings = validate_plan(plan)
    lines.extend(["", "BLOCKERS:", *[f"- {e}" for e in errors], "", "REVIEW NOTES:", *[f"- {w}" for w in warnings]])
    return "\n".join(lines)
