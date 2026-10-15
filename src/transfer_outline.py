"""General course-to-course transfer: a destination outline the person builds.

Pure planning rules. No credentials, browser calls or course writes. A plan is
plain JSON so it can be saved, reopened and resumed:

    plan = {"schema": 3, "id", "source", "target", "source_hash",
            "items":   {row_id: row}   every transferable source topic (or page part)
            "outline": [node, ...]     the destination modules, in order
            "skipped": [row_id, ...]   rows left out on purpose
            "styling", "templates", "journal", "report"}

Nodes (only modules may sit at the top level):
    module       {"id", "kind": "module", "title", "children", "sources"?, "existing_id"?, "section"?, "pattern"?}
    placeholder  {"id", "kind": "placeholder", "title", "message"}
    template     {"id", "kind": "template", "title", "template": "introduction" | "session", "text": {...}}
    item         {"id", "kind": "item", "item": row_id}

A row is placed when exactly one item node refers to it. Rows that are neither
placed nor skipped still need a decision. Saved patterns (for example the EA105
five-part layout) are data that can be applied to any module.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import uuid4

from transfer_plan import (CLASSROOM_MESSAGE, EA105_SECTIONS, _validate_sections, all_objects, classify, digest,
                           norm, page_pieces, recipe_sections, section_for)

SCHEMA = 3
TITLE_LIMIT = 150
# Activities Brightspace's Copy Components wizard copies with their settings,
# rubrics, grade items and attachments. Everything else non-page needs a person.
COMPONENT_KINDS = ("assignment", "quiz", "discussion", "survey")
CONTENT_KINDS = ("page", "file", "link")
KIND_NAMES = {"page": "Page", "file": "File", "link": "Link", "assignment": "Assignment", "quiz": "Quiz",
              "discussion": "Discussion", "survey": "Survey", "tool": "External tool", "checklist": "Checklist",
              "self-assessment": "Self assessment", "other": "Other activity"}
STARTS = ("mirror", "pattern", "destination", "empty")
TEMPLATE_KINDS = ("introduction", "session")
DEFAULT_STYLING = {"mode": "preserve", "apply_template": False, "overrides": {}}


def is_outline(plan):
    return isinstance(plan, dict) and plan.get("schema") == SCHEMA


def activity_kind(obj):
    """page | file | link | assignment | quiz | discussion | survey | tool | checklist | self-assessment | other."""
    activity = obj.get("ActivityType")
    named = {3: "assignment", 4: "quiz", 5: "discussion", 6: "discussion", 7: "tool", 10: "checklist",
             11: "self-assessment", 12: "survey"}
    if activity in named: return named[activity]
    if activity not in (1, 2): return "other"
    url = (obj.get("Url") or "").lower().split("?")[0].split("#")[0]
    if obj.get("html") is not None or url.endswith((".html", ".htm")): return "page"
    if activity == 2 or obj.get("TopicType") == 3 and not url.startswith("/content/enforced/"): return "link"
    return "file"


def copy_method(kind):
    """How a row reaches the destination: content (API), components (wizard) or manual."""
    if kind in CONTENT_KINDS: return "content"
    if kind in COMPONENT_KINDS: return "components"
    return "manual"


def new_id():
    return "n" + uuid4().hex[:10]


# ── Source rows ─────────────────────────────────────────────────────────

def repaired_title(obj):
    """A page's full title when its Content title was cut off ("Part 1: Mental HealthAsync...").

    Moodle imports shorten long titles and run the heading into the first sentence.
    The longest heading the shortened title starts with is the real title.
    """
    title = " ".join(str(obj.get("Title", "")).split())
    if not title.endswith(("...", "…")) or not obj.get("html"): return None
    from bs4 import BeautifulSoup
    stem = title.rstrip(".…").strip().casefold()
    headings = [" ".join(h.get_text(" ").split()) for h in BeautifulSoup(obj["html"], "html.parser")
                .find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"], limit=40)]
    matches = [h for h in headings if len(h) >= 4 and stem.startswith(h.casefold())]
    return max(matches, key=len)[:TITLE_LIMIT] if matches else None


def make_rows(source):
    """One row per source topic, in course order, remembering its folders."""
    rows = {}
    def walk(entries, path, top):
        for obj in entries:
            if obj.get("Type") == 0:
                walk(obj.get("children", []), [*path, obj["Title"]], top or str(obj["Id"]))
            elif obj.get("Type") == 1:
                kind = activity_kind(obj)
                rows[str(obj["Id"])] = {"id": str(obj["Id"]), "source_id": str(obj["Id"]), "title": obj["Title"],
                                        "path": path, "top": top, "kind": kind, "method": copy_method(kind),
                                        "suggested": None, "reason": "", "target_activity_id": ""}
                fixed = repaired_title(obj) if kind == "page" else None
                if fixed: rows[str(obj["Id"])].update(title=fixed, repaired_from=obj["Title"])
    walk(source.get("modules", []), [], None)
    return rows


_LOOKUP = [None, {}]


def source_objects(plan):
    """Source topics and modules by ID (cached for the most recent inventory)."""
    cached_source, cached_lookup = _LOOKUP
    if cached_source is plan["source"]:
        return cached_lookup
    lookup = {str(o["Id"]): o for o in all_objects(plan["source"]["modules"])}
    _LOOKUP[:] = [plan["source"], lookup]
    # HTTP previews and the browser worker can replace the cache concurrently.
    # Return this call's lookup, never another thread's most recent inventory.
    return lookup


def source_of(plan, row):
    return source_objects(plan).get(row["source_id"], {})


# ── Outline traversal ───────────────────────────────────────────────────

def walk(nodes, parent=None):
    """Yield (node, parent, siblings) depth-first in display order."""
    for node in nodes:
        yield node, parent, nodes
        if node["kind"] == "module":
            yield from walk(node["children"], node)


def find(plan, node_id):
    for node, parent, siblings in walk(plan["outline"]):
        if node["id"] == node_id: return node, parent, siblings
    raise KeyError("That part of the outline no longer exists.")


def placements(plan):
    """{row_id: [module node ids holding it]}; normally one each."""
    found = {}
    for node, parent, _ in walk(plan["outline"]):
        if node["kind"] == "item":
            found.setdefault(node["item"], []).append(parent["id"] if parent else None)
    return found


def unplaced(plan):
    placed, skipped = placements(plan), set(plan["skipped"])
    return [row for id_, row in plan["items"].items() if id_ not in placed and id_ not in skipped]


def module_path(plan, node_id):
    """Titles from the top module down to this node."""
    titles = []
    while node_id:
        node, parent, _ = find(plan, node_id)
        titles.append(node["title"]); node_id = parent["id"] if parent else None
    return list(reversed(titles))


def _editable(plan):
    if plan.get("run_signature"): raise ValueError("This transfer has already been created, so its plan can't change.")
    plan["review_acknowledged"] = False


def _title(title):
    title = " ".join(str(title or "").split())
    if not title: raise ValueError("Give it a name.")
    if len(title) > TITLE_LIMIT: raise ValueError(f"Names can be at most {TITLE_LIMIT} characters.")
    return title


def _module(plan, node_id):
    node = find(plan, node_id)[0]
    if node["kind"] != "module": raise ValueError("Choose a module or submodule.")
    return node


# ── Editing ─────────────────────────────────────────────────────────────

def add_module(plan, parent_id=None, title="New module", index=None):
    _editable(plan)
    node = {"id": new_id(), "kind": "module", "title": _title(title), "children": [], "custom": True}
    siblings = _module(plan, parent_id)["children"] if parent_id else plan["outline"]
    siblings.insert(len(siblings) if index is None else index, node)
    return node


def add_page(plan, parent_id, kind, title, index=None, message=None):
    """A generated page: a placeholder note, or a page made from a template."""
    _editable(plan)
    if kind == "placeholder":
        node = {"id": new_id(), "kind": "placeholder", "title": _title(title), "message": message or CLASSROOM_MESSAGE}
    elif kind in TEMPLATE_KINDS:
        node = {"id": new_id(), "kind": "template", "title": _title(title), "template": kind,
                "text": {"introduction": "", "outcomes": [], "session": ""}}
    else:
        raise ValueError("Unknown page type.")
    siblings = _module(plan, parent_id)["children"]
    node["custom"] = True
    siblings.insert(len(siblings) if index is None else index, node)
    ensure_templates(plan)
    return node


def rename(plan, node_id, title):
    _editable(plan)
    node = find(plan, node_id)[0]
    if node.get("existing_id"): raise ValueError("Modules already in the destination keep their names.")
    if node["kind"] == "item":
        row = plan["items"][node["item"]]
        if row["method"] != "content":
            raise ValueError("Activities keep the name they have in the source. Rename them in Brightspace afterwards.")
        row["title"] = _title(title)
        row["custom"] = True
        return
    node["title"] = _title(title)
    node["custom"] = True


def set_text(plan, node_id, introduction="", outcomes=(), session=""):
    _editable(plan)
    node = find(plan, node_id)[0]
    if node["kind"] != "template": raise ValueError("Only template pages have text to fill in.")
    node["text"] = {"introduction": str(introduction).strip(), "session": str(session).strip(),
                    "outcomes": [str(o).strip() for o in outcomes if str(o).strip()]}
    node["custom"] = True


def remove(plan, node_id):
    """Remove a node. Items inside it go back to the unplaced list."""
    _editable(plan)
    node, parent, siblings = find(plan, node_id)
    siblings.remove(node)
    refresh_suggestions(plan)


def _contains(node, other_id):
    return any(n["id"] == other_id for n, _, _ in walk([node]))


def move(plan, node_id, parent_id, index=None):
    """Move a node to a new parent (None = top level) at index (None = end)."""
    _editable(plan)
    node, _, siblings = find(plan, node_id)
    if node.get("existing_id"): raise ValueError("Modules already in the destination stay where they are.")
    if parent_id is None and node["kind"] != "module": raise ValueError("Only modules can sit at the top level.")
    if parent_id and _contains(node, parent_id): raise ValueError("A module can't move inside itself.")
    target = _module(plan, parent_id)["children"] if parent_id else plan["outline"]
    position = siblings.index(node)
    siblings.remove(node)
    if index is None: index = len(target)
    elif target is siblings and index > position: index -= 1
    target.insert(max(0, min(index, len(target))), node)


def _drop_items(plan, row_ids):
    ids = set(row_ids)
    for node, _, siblings in list(walk(plan["outline"])):
        if node["kind"] == "item" and node["item"] in ids and node in siblings:
            siblings.remove(node)


def place(plan, row_ids, parent_id, index=None):
    """Put rows (in the given order) into a module, replacing earlier placements."""
    _editable(plan)
    parent = _module(plan, parent_id)
    rows = [plan["items"][id_] for id_ in row_ids if id_ in plan["items"]]
    if any(r["method"] == "manual" for r in rows):
        raise ValueError("This kind of activity can't be copied by the app. Leave it skipped and add it in Brightspace.")
    _drop_items(plan, [r["id"] for r in rows])
    _include_units_for_rows(plan, [r["id"] for r in rows])
    plan["skipped"] = [id_ for id_ in plan["skipped"] if id_ not in {r["id"] for r in rows}]
    at = len(parent["children"]) if index is None else max(0, min(index, len(parent["children"])))
    for offset, row in enumerate(rows):
        parent["children"].insert(at + offset, {"id": new_id(), "kind": "item", "item": row["id"]})


def unplace(plan, row_ids):
    _editable(plan)
    _drop_items(plan, row_ids)
    _include_units_for_rows(plan, row_ids)
    plan["skipped"] = [id_ for id_ in plan["skipped"] if id_ not in set(row_ids)]
    refresh_suggestions(plan)


def _include_units_for_rows(plan, row_ids):
    source_ids = {plan["items"][id_]["source_id"] for id_ in row_ids if id_ in plan["items"]}
    objects = source_objects(plan)
    if "skipped_units" in plan:
        plan["skipped_units"] = [id_ for id_ in plan["skipped_units"] if not source_ids.intersection(
            str(o["Id"]) for o in all_objects([objects[id_]]) if o.get("Type") == 1)]


def skip(plan, row_ids):
    _editable(plan)
    _drop_items(plan, row_ids)
    plan["skipped"] = list(dict.fromkeys([*plan["skipped"], *[id_ for id_ in row_ids if id_ in plan["items"]]]))


def unit_skip_review(plan, module_ids, row_ids):
    """Linked draft modules and personal edits affected by a whole-unit Skip.

    Links use source IDs, never a title match. Older patterned drafts can still
    recognise their unedited automatic pages without a provenance marker.
    """
    objects = source_objects(plan)
    modules = set()
    for id_ in module_ids:
        obj = objects.get(id_)
        if not obj or obj.get("Type") != 0: raise ValueError("Choose a source unit.")
        modules.update(str(o["Id"]) for o in all_objects([obj]) if o.get("Type") == 0)
    selected_rows = set(row_ids)
    linked = {n["id"] for n, _, _ in walk(plan["outline"]) if n["kind"] == "module" and
              (n.get("source_module") in modules or modules.intersection(n.get("sources", [])))}
    targets = []
    def outermost(nodes):
        for node in nodes:
            if node["id"] in linked: targets.append(node)
            elif node["kind"] == "module": outermost(node["children"])
    outermost(plan["outline"])
    protected = []
    source_titles = {norm(objects[id_]["Title"][:TITLE_LIMIT]) for id_ in modules}
    for unit in targets:
        sections = recipe_sections(_pattern_recipe(unit["pattern"])) if unit.get("pattern") else []
        automatic_templates = {("introduction", norm(s["title"])) for s in sections if s["kind"] == "introduction"}
        if any(s.get("session_overview") for s in sections): automatic_templates.add(("session", norm("Session Overview")))
        automatic_placeholders = {(norm(s["title"]), s["message"]) for s in sections if s["kind"] == "placeholder"}
        for node, _, _ in walk([unit]):
            row = plan["items"].get(node.get("item"), {})
            reason = ""
            if node.get("custom") or row.get("custom"): reason = "Added or edited in this draft"
            elif node["kind"] == "item" and node["item"] not in selected_rows: reason = "Content from another unit; will return to unplaced"
            elif node["kind"] == "template":
                if any(node.get("text", {}).values()): reason = "Page text has been written"
                elif not node.get("auto_generated") and (node["template"], norm(node["title"])) not in automatic_templates:
                    reason = "Page added to the draft"
            elif node["kind"] == "placeholder":
                if not node.get("auto_generated") and (norm(node["title"]), node.get("message")) not in automatic_placeholders:
                    reason = "Page added to the draft"
            elif node["kind"] == "module":
                if set(node.get("sources", [])) - modules or node.get("source_module") and node["source_module"] not in modules:
                    reason = "Also linked to another source unit"
                elif node is not unit and not node.get("existing_id") and not node.get("auto_generated") and not node.get("section") and norm(node["title"]) not in source_titles:
                    reason = "Folder added to the draft"
            if reason: protected.append({"unit": unit["title"], "title": node.get("title", row.get("title", "Item")), "reason": reason})
    return {"modules": [{"id": n["id"], "title": n["title"]} for n in targets], "protected": protected}


def place_source_module(plan, module_id, parent_id):
    """Copy a whole source folder: a new submodule holding its items and subfolders."""
    _editable(plan)
    obj = source_objects(plan).get(str(module_id))
    if not obj or obj.get("Type") != 0: raise ValueError("That source module is no longer in the scan.")
    parent = _module(plan, parent_id) if parent_id else None
    node = _mirror(plan, obj)
    ids = {n["item"] for n, _, _ in walk([node]) if n["kind"] == "item"}
    included_units = {str(o["Id"]) for o in all_objects([obj]) if o.get("Type") == 0}
    plan["skipped_units"] = [id_ for id_ in plan.get("skipped_units", []) if id_ not in included_units]
    _drop_items(plan, ids)
    plan["skipped"] = [id_ for id_ in plan["skipped"] if id_ not in ids]
    (parent["children"] if parent else plan["outline"]).append(node)
    return node


def use_existing(plan, node_id):
    """Add into the destination's module of the same name instead of creating another."""
    _editable(plan)
    node, parent, _ = find(plan, node_id)
    if node["kind"] != "module" or node.get("existing_id"): raise ValueError("Choose a new module.")
    if parent and not parent.get("existing_id"): raise ValueError("Only modules inside existing modules can be matched.")
    pool = [m for m in (plan["target"]["modules"] if parent is None else _target_children(plan, parent["existing_id"]) or [])
            if m.get("Type") == 0 and norm(m["Title"]) == norm(node["title"])]
    if len(pool) != 1: raise ValueError(f"The destination has {len(pool)} modules called “{node['title']}”; rename instead.")
    def adopt(new, existing):
        new.update(existing_id=str(existing["Id"]), title=existing["Title"])
        for child in new["children"]:
            same = [m for m in existing.get("children", []) if child["kind"] == "module" and m.get("Type") == 0
                    and norm(m["Title"]) == norm(child["title"])]
            if len(same) == 1 and not child.get("existing_id"): adopt(child, same[0])
    adopt(node, pool[0])


def set_sources(plan, node_id, module_ids):
    """Source modules whose items are suggested for this destination module."""
    _editable(plan)
    node = _module(plan, node_id)
    node["sources"] = [str(i) for i in module_ids]
    node["custom"] = True
    refresh_suggestions(plan)


# ── Suggestions ─────────────────────────────────────────────────────────

def _pattern_recipe(pattern):
    return {"schema": 2, "sections": pattern["sections"], "default_is_rule": pattern.get("default_is_rule", False),
            "split_pages": pattern.get("split_pages", False),
            "review_assignment_keywords": pattern.get("review_assignment_keywords", [])}


def _home_for(plan, row):
    """Destination module whose sources include this row's source module."""
    found = None
    for node, _, _ in walk(plan["outline"]):
        if node["kind"] == "module" and row["top"] in node.get("sources", []):
            found = found or node
    return found


def _suggest(plan, row, home):
    """(node id, confidence, reason) inside `home` for one row."""
    obj = source_of(plan, row)
    if home.get("pattern"):
        recipe = _pattern_recipe(home["pattern"])
        if row.get("piece") and row["piece"]["phase"] in ("before", "during", "after") and section_for(recipe, "phase", row["piece"]["phase"]):
            title, confidence, reason = section_for(recipe, "phase", row["piece"]["phase"]), "deterministic", f"Page label: “{row['piece']['marker']}”"
        else:
            title, confidence, reason = classify(obj, row["path"], recipe)
        for child in home["children"]:
            if child["kind"] == "module" and (child.get("section") or {}).get("title") == title:
                return child["id"], confidence, reason
        return home["id"], confidence, reason  # inline section, or a removed section folder
    # No pattern: follow the source's own folder names below the top module.
    target, matched = home, []
    for folder in row["path"][1:]:
        child = next((c for c in target["children"] if c["kind"] == "module" and norm(c["title"]) == norm(folder)), None)
        if not child: break
        target, matched = child, [*matched, folder]
    if len(matched) == len(row["path"]) - 1:
        return target["id"], "deterministic", "Same folder as in the source"
    return target["id"], "inferred", "Closest matching folder"


def refresh_suggestions(plan):
    placed, skipped = placements(plan), set(plan["skipped"])
    for row in plan["items"].values():
        if row["id"] in placed or row["id"] in skipped:
            row["suggested"], row["reason"] = None, ""
            continue
        home = _home_for(plan, row)
        if home is None:
            row["suggested"], row["reason"] = None, ""
            continue
        row["suggested"], row["confidence"], row["reason"] = _suggest(plan, row, home)


def _source_position(plan, parent_id, row_id):
    """Index that keeps a module's items in source order (after its generated pages)."""
    order = {id_: i for i, id_ in enumerate(plan["items"])}
    children = _module(plan, parent_id)["children"]
    position = 0
    for index, child in enumerate(children):
        if child["kind"] != "item" or order.get(child["item"], -1) < order[row_id]: position = index + 1
        elif child["kind"] == "item": break
    return position


def accept_suggestions(plan, row_ids=None, confident_only=False):
    """Place unplaced rows where they're suggested. Returns how many moved."""
    _editable(plan)
    refresh_suggestions(plan)
    wanted = set(row_ids) if row_ids is not None else None
    moved = 0
    for row in unplaced(plan):
        if not row["suggested"] or wanted is not None and row["id"] not in wanted: continue
        if confident_only and row.get("confidence") != "deterministic": continue
        if row["method"] == "manual": continue
        place(plan, [row["id"]], row["suggested"], _source_position(plan, row["suggested"], row["id"])); moved += 1
    refresh_suggestions(plan)
    return moved


# ── Patterns ────────────────────────────────────────────────────────────

def validate_pattern(pattern):
    if not isinstance(pattern, dict) or not str(pattern.get("name", "")).strip():
        raise ValueError("A pattern needs a name.")
    _validate_sections({"schema": 2, "sections": pattern.get("sections"), "page_style": "original"})


def ea105_pattern():
    return {"name": "EA105 workshop layout", "sections": copy.deepcopy(EA105_SECTIONS), "split_pages": True,
            "default_is_rule": True,
            "templates": {"introduction_title": "Module Introduction template", "session_title": "Session Overview template"},
            "remove_introduction_sections": ["In this module you will explore"],
            "remove_session_sections": ["Completion Check", "Engagement Activities"],
            "review_assignment_keywords": ["optional", "alternate", "agreement", "photo id", "preferences"]}


def simple_pattern():
    return {"name": "Introduction, then content", "sections": [
        {"title": "Module Introduction", "kind": "introduction"},
        {"title": "Content", "kind": "inline", "default": True, "assignments": True, "files": True}],
        "default_is_rule": True}


def pattern_choices(source, target, saved=()):
    """Saved patterns first, then ones detected from either course, then built-ins."""
    from transfer_guided import structure_from_source, structure_from_target
    found = [copy.deepcopy(p) for p in saved]
    for detected in (structure_from_target(target), structure_from_source(source)):
        if detected:
            found.append({"name": detected["label"], "sections": detected["sections"], "default_is_rule": True})
    names = {norm(p["name"]) for p in found}
    found.extend(p for p in (ea105_pattern(), simple_pattern()) if norm(p["name"]) not in names)
    return found


def load_patterns(path):
    try:
        patterns = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    good = []
    for pattern in patterns if isinstance(patterns, list) else []:
        try: validate_pattern(pattern); good.append(pattern)
        except ValueError: continue
    return good


def save_patterns(path, patterns):
    for pattern in patterns: validate_pattern(pattern)
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(patterns), indent=2, ensure_ascii=False), encoding="utf-8")


def pattern_from_module(plan, node_id, name):
    """Save a module's current layout as a reusable pattern."""
    node = _module(plan, node_id)
    sections = []
    for child in node["children"]:
        if child["kind"] == "placeholder":
            sections.append({"title": child["title"], "kind": "placeholder", "message": child["message"]})
        elif child["kind"] == "template" and child["template"] == "introduction":
            sections.append({"title": child["title"], "kind": "introduction"})
        elif child["kind"] == "module":
            section = {k: v for k, v in (child.get("section") or {}).items() if k not in ("key", "kind", "title")}
            section = {k: v for k, v in section.items() if v not in ("", [], False, None)}
            if any(c["kind"] == "template" and c["template"] == "session" for c in child["children"]):
                section["session_overview"] = True
            sections.append({"title": child["title"], "kind": "folder", **section})
    if not any(s["kind"] == "folder" for s in sections):
        sections.append({"title": "Content", "kind": "inline", "default": True})
    pattern = {**{k: v for k, v in (node.get("pattern") or {}).items() if k not in ("name", "sections")},
               "name": _title(name), "sections": sections}
    validate_pattern(pattern)
    return pattern


def _split_rows(plan, row_ids, recipe):
    """Replace whole pages with their labelled parts where that's lossless."""
    rebuilt = {}
    for id_, row in plan["items"].items():
        if id_ not in row_ids or row.get("piece") or row["kind"] != "page" or row["source_id"] in plan.get("unsplit", []):
            rebuilt[id_] = row; continue
        pieces = page_pieces(source_of(plan, row), row["path"], recipe)
        if not pieces or not pieces[0].get("piece"):
            rebuilt[id_] = row; continue
        for piece in pieces:
            original = source_of(plan, row).get("Title", "")
            title = row["title"] + piece["title"][len(original):] if piece["title"].startswith(original) else piece["title"]
            new = {**row, "id": f"{row['source_id']}#{piece['piece']['index']}", "title": title[:TITLE_LIMIT], "piece": piece["piece"]}
            rebuilt[new["id"]] = new
    plan["items"] = rebuilt


def apply_pattern(plan, node_id, pattern):
    """Give a module the pattern's sections; confident items move in automatically."""
    _editable(plan)
    validate_pattern(pattern)
    node = _module(plan, node_id)
    pattern = copy.deepcopy(pattern)
    created = []
    for section in recipe_sections({"schema": 2, "sections": pattern["sections"]}):
        same = next((c for c in node["children"] if c["kind"] in ("module", "placeholder", "template")
                     and norm(c["title"]) == norm(section["title"])), None)
        if section["kind"] == "placeholder":
            child = same or {"id": new_id(), "kind": "placeholder", "title": section["title"], "message": section["message"], "auto_generated": True}
        elif section["kind"] == "introduction":
            child = same or {"id": new_id(), "kind": "template", "title": section["title"], "template": "introduction",
                             "text": {"introduction": "", "outcomes": [], "session": ""}, "auto_generated": True}
        elif section["kind"] == "folder":
            child = same if same and same["kind"] == "module" else {"id": new_id(), "kind": "module", "title": section["title"], "children": [], "auto_generated": True}
            child["section"] = section
            if section["session_overview"] and not any(c["kind"] == "template" and c["template"] == "session" for c in child["children"]):
                child["children"].insert(0, {"id": new_id(), "kind": "template", "title": "Session Overview", "template": "session",
                                             "text": {"introduction": "", "outcomes": [], "session": ""}, "auto_generated": True})
        else:  # inline: items sit directly in the module
            if section["session_overview"]:
                created.append({"id": new_id(), "kind": "template", "title": "Session Overview", "template": "session",
                                "text": {"introduction": "", "outcomes": [], "session": ""}, "auto_generated": True})
            continue
        created.append(child)
    kept = [c for c in node["children"] if c not in created]
    node["children"] = created + kept
    node["pattern"] = pattern
    ensure_templates(plan, pattern)
    if pattern.get("split_pages"):
        mine = {r["id"] for r in unplaced(plan) if r["top"] in node.get("sources", [])}
        _split_rows(plan, mine, _pattern_recipe(pattern))
    accept_suggestions(plan, [r["id"] for r in unplaced(plan) if r["top"] in node.get("sources", [])], confident_only=True)


def join_page(plan, source_id):
    """Keep a split page whole again (unplaces its parts)."""
    _editable(plan)
    parts = [r for r in plan["items"].values() if r["source_id"] == str(source_id) and r.get("piece")]
    if not parts: return
    _drop_items(plan, [r["id"] for r in parts])
    plan["skipped"] = [i for i in plan["skipped"] if i not in {r["id"] for r in parts}]
    plan["unsplit"] = sorted({*plan.get("unsplit", []), str(source_id)})
    rebuilt, done = {}, False
    for id_, row in plan["items"].items():
        if row["source_id"] != str(source_id) or not row.get("piece"):
            rebuilt[id_] = row
        elif not done:
            whole = {k: v for k, v in row.items() if k != "piece"}
            obj = source_of(plan, row)
            whole.update(id=str(source_id), title=repaired_title(obj) or obj.get("Title", row["title"]))
            rebuilt[whole["id"]] = whole; done = True
    plan["items"] = rebuilt
    refresh_suggestions(plan)


# ── Building a plan ─────────────────────────────────────────────────────

def _mirror(plan, module):
    node = {"id": new_id(), "kind": "module", "title": module["Title"][:TITLE_LIMIT], "children": [],
            "sources": [str(module["Id"])], "source_module": str(module["Id"])}
    for obj in module.get("children", []):
        if obj.get("Type") == 0:
            child = _mirror(plan, obj); child.pop("sources")
            node["children"].append(child)
        elif str(obj["Id"]) in plan["items"] and plan["items"][str(obj["Id"])]["method"] != "manual":
            node["children"].append({"id": new_id(), "kind": "item", "item": str(obj["Id"])})
    return node


def _existing(module):
    return {"id": new_id(), "kind": "module", "title": module["Title"], "existing_id": str(module["Id"]),
            "children": [_existing(c) for c in module.get("children", []) if c.get("Type") == 0]}


def build_outline_plan(source, target, start="mirror", pattern=None, templates=None):
    """A fresh plan. start: mirror | pattern | destination | empty."""
    if str(source["id"]) == str(target["id"]): raise ValueError("Copy from and Copy into must be different courses.")
    if start not in STARTS: raise ValueError("Unknown starting point.")
    if start == "pattern" and not pattern: raise ValueError("Choose a pattern.")
    plan = {"schema": SCHEMA, "id": uuid4().hex, "source": source, "target": target, "source_hash": digest(source),
            "start": start, "items": make_rows(source), "outline": [], "skipped": [], "skipped_units": [], "unsplit": [],
            "styling": copy.deepcopy(DEFAULT_STYLING), "templates": dict(templates or {}),
            "gradebook": {"enabled": False, "categories": []},
            "review_acknowledged": False, "journal": {}, "report": ""}
    plan["skipped"] = [id_ for id_, row in plan["items"].items() if row["method"] == "manual"]
    tops = [m for m in source.get("modules", []) if m.get("Type") == 0]
    if start == "mirror":
        plan["outline"] = [_mirror(plan, m) for m in tops]
    elif start == "pattern":
        for module in tops:
            if not any(o.get("Type") == 1 for o in all_objects(module.get("children", []))): continue
            node = add_module(plan, None, module["Title"][:TITLE_LIMIT]); node.pop("custom"); node["sources"] = [str(module["Id"])]
            apply_pattern(plan, node["id"], pattern)
    elif start == "destination":
        plan["outline"] = [_existing(m) for m in target.get("modules", []) if m.get("Type") == 0]
        for node in plan["outline"]:
            node["sources"] = [str(m["Id"]) for m in tops if norm(m["Title"]) == norm(node["title"])]
        accept_suggestions(plan, confident_only=True)
    refresh_suggestions(plan)
    plan["review_acknowledged"] = False
    return plan


def ensure_templates(plan, pattern=None):
    """Find introduction/Session Overview templates once a plan needs them."""
    from transfer_guided import discover_templates
    needed = {n["template"] for n, _, _ in walk(plan["outline"]) if n["kind"] == "template"}
    missing = needed - set(plan["templates"])
    if not missing: return
    names = (pattern or {}).get("templates") or ea105_pattern()["templates"]
    found = discover_templates(plan["source"], plan["target"], {"templates": names})
    for kind in missing:
        if kind in found: plan["templates"][kind] = found[kind]


# ── Checking ────────────────────────────────────────────────────────────

def _issue(text, level="error", node=None, row=None):
    return {"text": text, "level": level, "node": node, "row": row}


def _target_children(plan, existing_id):
    for obj in all_objects(plan["target"]["modules"]):
        if str(obj["Id"]) == str(existing_id): return obj.get("children", [])
    return None


def check(plan):
    """Every problem and note, each tied to the node or row it concerns."""
    issues = []
    if str(plan["source"]["id"]) == str(plan["target"]["id"]):
        issues.append(_issue("Copy from and Copy into are the same course."))
    if digest(plan["source"]) != plan["source_hash"]:
        issues.append(_issue("The saved source scan was changed. Scan both courses again."))
    lookup = source_objects(plan)
    placed = placements(plan)
    owned = {str(j.get("result", {}).get("Id")) for j in plan.get("journal", {}).values()}
    for row_id, holders in placed.items():
        row = plan["items"].get(row_id)
        if row is None:
            issues.append(_issue("An item in the outline is no longer in the source scan.")); continue
        if len(holders) > 1: issues.append(_issue(f"{row['title']} is placed more than once.", row=row_id))
    for row in plan["items"].values():
        obj = lookup.get(row["source_id"])
        if obj is None or obj.get("Type") != 1:
            issues.append(_issue(f"{row['title']} is no longer in the source scan.", row=row["id"])); continue
        if obj.get("html_error") and row["id"] in placed:
            issues.append(_issue(f"{row['title']}: {obj['html_error']} Restore the page and scan again, or Skip it.", row=row["id"]))
        if row.get("piece"):
            fresh = page_pieces(obj, row["path"], {"split_pages": True, "schema": 2, "sections": []}) or []
            match = [p for p in fresh if p.get("piece") and p["piece"]["index"] == row["piece"]["index"]]
            if not match or match[0]["piece"]["html"] != row["piece"]["html"]:
                issues.append(_issue(f"{row['title']}: the page changed since it was split. Keep it whole or scan again.", row=row["id"]))
        if row["id"] in placed and row["method"] == "manual":
            issues.append(_issue(f"{row['title']} can't be copied by the app. Skip it and add it in Brightspace.", row=row["id"]))
    waiting = unplaced(plan)
    if waiting:
        issue = _issue(f"{len(waiting)} {'item still needs' if len(waiting) == 1 else 'items still need'} a place, or Skip.")
        issue["fix"] = "place_unplaced"
        issues.append(issue)
    activities = {}
    for row_id in placed:
        row = plan["items"].get(row_id)
        if row and row["method"] == "components":
            key = (row["kind"], str(source_of(plan, row).get("ToolItemId") or row["title"]))
            if key in activities:
                issues.append(_issue(f"{row['title']} links the same activity as {activities[key]}. Keep one of them.", row=row_id))
            activities[key] = row["title"]
    for node, parent, siblings in walk(plan["outline"]):
        if node["kind"] == "item": continue
        if not node.get("existing_id") and (not str(node.get("title", "")).strip() or len(node["title"]) > TITLE_LIMIT):
            issues.append(_issue(f"Names must be 1–{TITLE_LIMIT} characters.", node=node["id"]))
        if parent is None and node["kind"] != "module":
            issues.append(_issue(f"{node['title']} must be inside a module.", node=node["id"]))
        if node["kind"] == "template" and node["template"] not in plan.get("templates", {}):
            issues.append(_issue(f"Choose a page template for {node['title']}.", node=node["id"]))
        if node["kind"] == "template":
            text = node.get("text", {})
            empty = not text.get("introduction") or not text.get("outcomes") if node["template"] == "introduction" else not text.get("session")
            if empty:
                where = " in " + parent["title"] if parent else ""
                issues.append(_issue(f"{node['title']}{where} has no text yet; its new page will contain “to be supplied” and start hidden.",
                                     "note", node=node["id"]))
        if node["kind"] == "module" and not node.get("existing_id") and not node["children"]:
            issues.append(_issue(f"{node['title']} is empty and will be created empty.", "note", node=node["id"]))
    # Sibling names must be unique among new objects and what's already there.
    def names(children, existing):
        seen = {}
        for obj in existing or []:
            if str(obj["Id"]) not in owned: seen.setdefault(norm(obj["Title"]), "existing")
        for child in children:
            if child.get("existing_id"): continue
            title = plan["items"][child["item"]]["title"] if child["kind"] == "item" and child["item"] in plan["items"] else child.get("title", "")
            key = norm(title)
            if key in seen:
                where = "already in the destination" if seen[key] == "existing" else "used twice here"
                merge = seen[key] == "existing" and child["kind"] == "module"
                issue = _issue(f"“{title}” is {where}. Rename one of them" + (", or add into the existing one." if merge else "."),
                               node=child["id"])
                if merge: issue["fix"] = "use_existing"
                issues.append(issue)
            seen[key] = "new"
    existing_roots = [m for m in plan["target"]["modules"] if m.get("Type") == 0]
    names(plan["outline"], [m for m in existing_roots if not any(n.get("existing_id") == str(m["Id"]) for n in plan["outline"])])
    for node, _, _ in walk(plan["outline"]):
        if node["kind"] == "module":
            existing = _target_children(plan, node["existing_id"]) if node.get("existing_id") else []
            names(node["children"], [c for c in existing or [] if not any(n.get("existing_id") == str(c["Id"]) for n in node["children"])])
    from transfer_components import activity_name, target_matches
    names = {}
    for row_id in placed:
        row = plan["items"].get(row_id)
        if not row or row["method"] != "components": continue
        name = activity_name(row, plan["source"])
        if row["kind"] == "survey" and any("surveys" in course and course["surveys"] is None for course in (plan["source"], plan["target"])):
            issues.append(_issue(f"{name}: the source or destination survey catalogue is unavailable. Scan again or Skip this survey.", row=row_id))
        if row["kind"] == "survey":
            survey_id = str(source_of(plan, row).get("ToolItemId"))
            survey = next((s for s in plan["source"].get("surveys") or [] if str(s.get("SurveyId")) == survey_id), {})
            if survey.get("IsActive"):
                issues.append(_issue(f"{name} is active in the Surveys tool. Its Content link will be hidden, but Copy Components keeps the survey's availability settings. Review those settings in Surveys before sharing the destination course.", "note", row=row_id))
        if (row["kind"], norm(name)) in names:
            issues.append(_issue(f"Two {KIND_NAMES[row['kind']].lower()}s are called “{name}”. Rename one in the source "
                                 "first so their copies can be told apart.", row=row_id))
        names[(row["kind"], norm(name))] = row_id
        if target_matches(row["kind"], name, plan["target"]):
            if _identical_assignment(plan, row):
                issues.append(_issue(f"{name} is already in the destination, identical; that copy will be used.", "note", row=row_id))
            else:
                issues.append(_issue(f"{name} already exists in the destination. Rename or remove it there and scan "
                                     "again, or Skip it here.", row=row_id))
    copies = [plan["items"][r] for r in placed if r in plan["items"] and plan["items"][r]["method"] == "components"
              and not plan["items"][r].get("target_activity_id")]
    from transfer_files import broken_moodle_links
    for row_id in placed:
        row = plan["items"].get(row_id)
        if not row or row["kind"] != "page": continue
        broken = len(broken_moodle_links(row["piece"]["html"] if row.get("piece") else source_of(plan, row).get("html", "")))
        if broken:
            issues.append(_issue(f"{broken} link{'s' if broken != 1 else ''} on “{row['title']}” {'were' if broken != 1 else 'was'} already "
                                 "broken in the source (left from the Moodle import). The text stays; the dead link is removed.",
                                 "note", row=row_id))
    repaired = [r for r_id, r in plan["items"].items() if r.get("repaired_from") and r_id in placed]
    if repaired:
        issues.append(_issue(f"{len(repaired)} page title{'s were' if len(repaired) != 1 else ' was'} cut off in the source "
                             "and will use the page's own heading instead (rename any of them in Arrange).", "note"))
    if copies:
        issues.append(_issue(f"{len(copies)} {'activity' if len(copies) == 1 else 'activities'} will be copied with Brightspace's "
                             "Copy Components, including rubrics, grade items and attachments. Their dates come from the source.", "note"))
        from transfer_components import category_notes
        merge = plan.get("grade_categories", "merge") == "merge"
        issues.extend(_issue(text, "note") for text in category_notes(copies, plan["source"], plan["target"], merge))
    from transfer_activity_links import issues as assignment_link_issues
    issues.extend(assignment_link_issues(plan))
    return issues


def _identical_assignment(plan, row):
    if row["kind"] != "assignment": return False
    from transfer_guided import matching_assignment
    tool_id = str(source_of(plan, row).get("ToolItemId"))
    original = next((a for a in plan["source"].get("assignments", []) if str(a["Id"]) == tool_id), None)
    return bool(original) and sum(matching_assignment(original, a) for a in plan["target"].get("assignments", [])) == 1


def validate_outline(plan):
    """(blockers, notes) as plain text, for the runner and the report."""
    issues = check(plan)
    return ([i["text"] for i in issues if i["level"] == "error"],
            list(dict.fromkeys(i["text"] for i in issues if i["level"] != "error")))


def counts(plan):
    placed = placements(plan)
    rows = [plan["items"][r] for r in placed if r in plan["items"]]
    nodes = [n for n, _, _ in walk(plan["outline"])]
    return {"modules": sum(n["kind"] == "module" and not n.get("existing_id") for n in nodes),
            "pages": sum(r["kind"] == "page" for r in rows) + sum(n["kind"] in ("placeholder", "template") for n in nodes),
            "files and links": sum(r["kind"] in ("file", "link") for r in rows),
            "activities": sum(r["method"] == "components" for r in rows),
            "skipped": len(plan["skipped"])}


def preview_text(plan):
    lines = ["STRUCTURED TRANSFER",
             f"Copy from (read only): {plan['source']['name']} ({plan['source']['id']})",
             f"Copy into: {plan['target']['name']} ({plan['target']['id']})", ""]
    describe = {"placeholder": "placeholder page", "template": "page from template"}
    def emit(nodes, depth):
        for node in nodes:
            pad = "  " * depth
            if node["kind"] == "item":
                row = plan["items"].get(node["item"], {"title": "(missing item)", "kind": "other"})
                lines.append(f"{pad}- {row['title']} [{KIND_NAMES.get(row['kind'], row['kind'])}]")
            elif node["kind"] == "module":
                lines.append(f"{pad}{node['title']}" + ("  (already in destination)" if node.get("existing_id") else ""))
                emit(node["children"], depth + 1)
            else:
                lines.append(f"{pad}- {node['title']} [{describe[node['kind']]}]")
    emit(plan["outline"], 0)
    skipped = [plan["items"][r]["title"] for r in plan["skipped"] if r in plan["items"]]
    if skipped: lines.extend(["", "Not copied:", *[f"- {t}" for t in skipped]])
    errors, notes = validate_outline(plan)
    lines.extend(["", "To fix before creating:", *([f"- {e}" for e in errors] or ["- Nothing"]),
                  "", "Notes:", *([f"- {n}" for n in notes] or ["- None"])])
    return "\n".join(lines)
