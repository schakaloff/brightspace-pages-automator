"""Copy chosen activities with Brightspace's own Copy Components wizard.

The wizard is the only supported way to copy an assignment together with its
rubric on this Brightspace release, and it also copies quizzes, discussions and
surveys with their settings, grade items and attachments.

The app drives the wizard exactly as a person would, choosing "Select
individual items to copy" for each tool and ticking only the planned items by
their Brightspace IDs. Content is never selected: the app builds the course
outline itself. Before Finish, the wizard's own confirmation page is read back
and must list exactly the planned IDs; anything else stops the copy.
"""
from __future__ import annotations

import asyncio
import re

from transfer_plan import norm

# Wizard names per tool: include checkbox, option radio, checklist page, confirmation field.
TOOLS = {
    "dropbox": ("includeDropbox", "dropboxOpt", "copy_select_dropbox", "wizSelectedDropbox"),
    "rubrics": ("includeRubrics", "rubricsOpt", "copy_select_rubrics", "wizSelectedRubrics"),
    "grades": ("includeGrades", "gradesOpt", "copy_select_grades", "wizSelectedGrades"),
    "quizzes": ("includeQuizzes", "quizzesOpt", "copy_select_quizzes", "wizSelectedQuizzes"),
    "discuss": ("includeDiscuss", "discussOpt", "copy_select_discuss", "wizSelectedDiscuss"),
    "surveys": ("includeSurveys", "surveysOpt", "copy_select_surveys", "wizSelectedSurveys"),
    "files": ("includeContentFiles", "contentFilesOpt", "copy_select_files", "wizSelectedFiles"),
}
ASSOCIATED_FILES = {"dropbox": "dropboxIncludeAssociatedFiles", "quizzes": "quizzesIncludeAssociatedFiles",
                    "discuss": "discussIncludeAssociatedFiles", "surveys": "surveyIncludeAssociatedFiles"}
TOOL_OF_KIND = {"assignment": "dropbox", "quiz": "quizzes", "discussion": "discuss", "survey": "surveys"}
# Content's Add Existing picker: (tool button, Content ActivityType).
LINK_KINDS = {"assignment": "ASSIGN", "quiz": "QUIZ", "discussion": "DISCUSS", "survey": "SURVEY"}


def _key(kind, tool_id):
    return ("t" if kind == "discussion" else "") + str(tool_id)


def file_key(path):
    """The wizard's key for a course file: its path from the course root, backslash-separated."""
    return "\\" + str(path).strip("/").replace("/", "\\")


def file_selection(paths):
    """{"files": [...], "_file_folders": [...]} for course-file paths like "img/preview.png"."""
    keys = list(dict.fromkeys(file_key(p) for p in paths))
    folders = []
    for key in keys:
        parts = key.strip("\\").split("\\")[:-1]
        for i in range(1, len(parts) + 1):
            folder = "\\" + "\\".join(parts[:i])
            if folder not in folders: folders.append(folder)
    return {"files": keys, **({"_file_folders": folders} if folders else {})}


def selection_for(rows, source):
    """{tool: [wizard keys]} for component rows, plus the rubrics and grade items they need.

    rows: plan rows with 'kind' and 'source_id'; source: the scanned source inventory.
    """
    from transfer_plan import all_objects
    topics = {str(o["Id"]): o for o in all_objects(source["modules"])}
    assignments = {str(a["Id"]): a for a in source.get("assignments", [])}
    quizzes = {str(q.get("QuizId")): q for q in source.get("quizzes", [])}
    selection = {tool: [] for tool in TOOLS}
    def add(tool, key):
        if key and key not in selection[tool]: selection[tool].append(key)
    for row in rows:
        tool = TOOL_OF_KIND.get(row["kind"])
        topic = topics.get(row["source_id"], {})
        tool_id = str(topic.get("ToolItemId") or "")
        if not tool or not tool_id:
            raise ValueError(f"{row['title']}: the source activity couldn't be identified. Scan again.")
        add(tool, _key(row["kind"], tool_id))
        if row["kind"] == "assignment":
            assignment = assignments.get(tool_id)
            if assignment is None: raise ValueError(f"{row['title']}: the source assignment is missing from the scan.")
            for rubric in (assignment.get("Assessment") or {}).get("Rubrics") or []:
                add("rubrics", str(rubric.get("RubricId") or ""))
            add("grades", str(assignment.get("GradeItemId") or ""))
        elif row["kind"] == "quiz":
            add("grades", str((quizzes.get(tool_id) or {}).get("GradeItemId") or ""))
    result = {tool: keys for tool, keys in selection.items() if keys}
    # The wizard ticks a grade item's category along with it; nothing else may be added.
    parents = [str(c["Id"]) for c in source.get("categories", [])
               if any(str(g.get("Id")) in selection["grades"] for g in c.get("Grades") or [])]
    if parents: result["_grade_categories"] = parents
    return result


def activity_name(row, source):
    """The source tool's own name for an activity (Content titles can differ)."""
    from transfer_plan import all_objects
    topic = next((o for o in all_objects(source["modules"]) if str(o["Id"]) == row["source_id"]), {})
    tool_id = str(topic.get("ToolItemId") or "")
    if row["kind"] == "assignment":
        return next((a["Name"] for a in source.get("assignments", []) if str(a["Id"]) == tool_id), row["title"])
    if row["kind"] == "quiz":
        return next((q["Name"] for q in source.get("quizzes", []) if str(q.get("QuizId")) == tool_id), row["title"])
    if row["kind"] == "discussion":
        return next((t["Name"] for t in source.get("discussions", []) if str(t.get("TopicId")) == tool_id), row["title"])
    if row["kind"] == "survey":
        return next((s["Name"] for s in source.get("surveys") or [] if str(s.get("SurveyId")) == tool_id), row["title"])
    return row["title"]


def target_matches(kind, name, target):
    """Destination activities of this kind with exactly this name."""
    pools = {"assignment": ("assignments", "Name", "Id"), "quiz": ("quizzes", "Name", "QuizId"),
             "discussion": ("discussions", "Name", "TopicId"), "survey": ("surveys", "Name", "SurveyId")}
    if kind not in pools: return []
    pool, field, id_field = pools[kind]
    return [str(a[id_field]) for a in target.get(pool) or [] if norm(a.get(field, "")) == norm(name)]


def check_selection(selected, expected):
    """Compare the wizard's confirmation fields with the planned keys."""
    problems = []
    for tool, (_, _, _, field) in TOOLS.items():
        got = {k for k in (selected.get(field) or "").split("|") if k}
        want = set(expected.get(tool, []))
        if tool == "discuss":
            got = {k for k in got if not k.startswith("f")}  # a topic brings its forum along
        if tool == "grades":
            got -= set(expected.get("_grade_categories", []))
        if tool == "files":  # a file brings its folder along; paths ignore case
            got = {k.lower() for k in got} - {k.lower() for k in expected.get("_file_folders", [])}
            want = {k.lower() for k in want}
        if got != want:
            problems.append(f"{tool}: wizard has {sorted(got)}, plan needs {sorted(want)}")
    others = {k: v for k, v in selected.items() if k.startswith("wizSelected") and v
              and k not in {f for *_, f in TOOLS.values()}}
    if others: problems.append("other components are selected: " + ", ".join(sorted(others)))
    return problems


SELECT_KEYS = """([keys]) => {
  // Course file paths ignore case in Brightspace ("Preview.png" = "preview.png").
  const wanted = new Map(keys.map(k => [k.toLowerCase(), k])), found = new Set();
  for (const li of document.querySelectorAll('li.dtn')) {
    const key = li.querySelector(':scope > .dtn_n input[name$="_collapsedKeys"]');
    const box = li.querySelector(':scope > .dtn_n input[type=checkbox]');
    if (!key || !box) continue;
    const leaf = !li.querySelector('ul.dtn_ch li');
    const want = leaf && wanted.has(key.value.toLowerCase());
    if (want) found.add(key.value.toLowerCase());
    if (leaf && box.checked !== want) box.click();
  }
  return [...wanted.keys()].filter(k => !found.has(k)).map(k => wanted.get(k));
}"""
READ_FIELDS = """() => Object.fromEntries([...document.querySelectorAll('input[type=hidden][name^=wiz]')].map(i => [i.name, i.value]))"""


async def _settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    await page.wait_for_timeout(800)


async def copy_components(page, base, target_id, source_id, selection, log=None, stop=None, finish=True):
    """Run the wizard for exactly `selection`. Returns the confirmation fields.

    With finish=False it stops on the confirmation page (a dry run).
    """
    log = log or (lambda *a: None)
    if str(target_id) == str(source_id): raise ValueError("Copy from and Copy into must differ.")
    if not selection: return {}
    unknown = {t for t in selection if not t.startswith("_")} - set(TOOLS)
    if unknown: raise ValueError("Unsupported components: " + ", ".join(sorted(unknown)))
    alerts = []
    page.on("dialog", lambda d: (alerts.append(d.message), asyncio.ensure_future(d.dismiss())))
    await page.goto(f"{base}/d2l/lms/importExport/copy/copy_select_components.d2l?copyFrom=1&isPartialCopy=1"
                    f"&courseId={source_id}&ou={target_id}", wait_until="domcontentloaded", timeout=60000)
    await _settle(page)
    fields = await page.evaluate(READ_FIELDS)
    if fields.get("wizCopyCourseId") != str(source_id):
        raise RuntimeError("Copy Components didn't open for the source course. Nothing was copied.")
    # Start from nothing selected, then tick only the planned tools.
    await page.evaluate("""() => { for (const box of document.querySelectorAll('input[type=checkbox][name^=include]'))
        if (box.checked) box.click(); const all = document.querySelector('input[name=checkAll]'); if (all && all.checked) all.click(); }""")
    tools = [t for t in selection if t in TOOLS]
    for tool in tools:
        include, option, _, _ = TOOLS[tool]
        if not await page.locator(f"input[name={include}]").count():
            raise RuntimeError(f"Copy Components has no {tool} option in this course. Nothing was copied.")
        await page.check(f"input[name={include}]")
        await page.check(f"input[name={option}][value='2']")
        files = ASSOCIATED_FILES.get(tool)
        if files and await page.locator(f"input[name={files}]").count():
            await page.check(f"input[name={files}]")
    await page.click("button:has-text('Continue')")
    for _ in range(len(TOOLS) + 2):
        if stop and stop.is_set(): raise InterruptedError("Stopped before copying. Nothing was copied.")
        await _settle(page)
        if alerts: raise RuntimeError("Copy Components: " + alerts[-1])
        if "copy_select_confirm" in page.url: break
        tool = next((t for t, (*_, slug, _f) in TOOLS.items() if slug + ".d2l" in page.url), None)
        if tool is None or tool not in tools:
            raise RuntimeError(f"Copy Components showed an unexpected step ({page.url.rsplit('/', 1)[-1]}). Nothing was copied.")
        missing = await page.evaluate(SELECT_KEYS, [selection[tool]])
        if missing: raise RuntimeError(f"Copy Components doesn't list {tool} item(s) {', '.join(missing)}. Nothing was copied.")
        log(f"Copy Components: selected {len(selection[tool])} {tool} item(s).", "info")
        await page.click("button:has-text('Continue')")
    else:
        raise RuntimeError("Copy Components didn't reach its confirmation page. Nothing was copied.")
    confirmed = await page.evaluate(READ_FIELDS)
    problems = check_selection(confirmed, selection)
    if await page.evaluate("""() => [...document.querySelectorAll('input[type=checkbox]')].some(b => b.checked
            && /offset/i.test(document.querySelector(`label[for="${b.id}"]`)?.innerText || ''))"""):
        problems.append("date offset is switched on")
    if problems:
        raise RuntimeError("Copy Components selection didn't match the plan (" + "; ".join(problems) + "). Nothing was copied.")
    if not finish:
        log("Copy Components: confirmation matches the plan (dry run, not finished).", "info")
        return confirmed
    log("Copy Components: confirmation matches the plan. Starting the copy…", "warning")
    await page.click("button:has-text('Finish')")
    await _settle(page)
    return confirmed


async def wait_for_copies(fetch_target, expected, log=None, stop=None, timeout=900, interval=10):
    """Poll the destination until every expected activity exists. expected: [(kind, name)]."""
    log = log or (lambda *a: None)
    waited = 0
    while True:
        target = await fetch_target()
        missing = [(k, n) for k, n in expected if k in TOOL_OF_KIND and not target_matches(k, n, target)]
        if not missing: return target
        if waited >= timeout:
            raise RuntimeError("Copy Components hasn't finished after 15 minutes: " + ", ".join(n for _, n in missing)
                               + ". Check Import/Export/Copy Components → View History, then resume.")
        if stop and stop.is_set(): raise InterruptedError("Stopped while Brightspace was copying. Resume to continue.")
        log(f"Waiting for Brightspace to finish copying ({len(missing)} left)…", "info")
        await asyncio.sleep(interval); waited += interval


# ── Grade categories ────────────────────────────────────────────────────
# Copy Components always creates the source's category for a copied grade item,
# even when the destination already has one with the same name. These helpers
# put copied grade items into the destination's own category and remove the
# empty duplicate the copy made.

def grade_ids_for(rows, source):
    """Source grade item IDs of component rows (assignments and quizzes)."""
    from transfer_plan import all_objects
    topics = {str(o["Id"]): o for o in all_objects(source["modules"])}
    assignments = {str(a["Id"]): a for a in source.get("assignments", [])}
    quizzes = {str(q.get("QuizId")): q for q in source.get("quizzes", [])}
    ids = []
    for row in rows:
        tool_id = str(topics.get(row["source_id"], {}).get("ToolItemId") or "")
        found = (assignments.get(tool_id) if row["kind"] == "assignment" else quizzes.get(tool_id) if row["kind"] == "quiz" else None) or {}
        if found.get("GradeItemId"): ids.append(str(found["GradeItemId"]))
    return ids


def category_notes(rows, source, target, merge=True):
    """Plain-language notes on what the copy will do to the destination's gradebook."""
    wanted = set(grade_ids_for(rows, source))
    notes = []
    for category in source.get("categories", []):
        count = sum(str(g.get("Id")) in wanted for g in category.get("Grades") or [])
        if not count: continue
        same = [c for c in target.get("categories", []) if norm(c["Name"]) == norm(category["Name"])]
        items = f"{count} grade item{'s' if count != 1 else ''}"
        if same and merge:
            notes.append(f"{target['name']} already has a “{category['Name']}” grade category; {items} will be moved into it.")
        elif same:
            notes.append(f"Brightspace will add a second “{category['Name']}” category ({category.get('Weight', 0)}%) for {items}; "
                         f"{target['name']}'s category weights will need adjusting.")
        else:
            notes.append(f"Brightspace will add the grade category “{category['Name']}” ({category.get('Weight', 0)}%) for {items}; "
                         f"check {target['name']}'s category weights afterwards.")
    return notes


def category_plan(target, before_ids, grade_ids):
    """(moves, removals) that put copied grade items into same-named categories that existed before the copy.

    A category is only removed when the copy created it, a move empties it, and
    the destination already had exactly one category with that name.
    """
    before = {str(i) for i in before_ids}
    categories = target.get("categories", [])
    older = {}
    for category in categories:
        if str(category["Id"]) in before: older.setdefault(norm(category["Name"]), []).append(category)
    grades = {str(g["Id"]): g for g in target.get("grades", [])}
    moves = []
    for grade_id in grade_ids:
        grade = grades.get(str(grade_id))
        if not grade: continue
        current = next((c for c in categories if str(c["Id"]) == str(grade.get("CategoryId"))), None)
        if not current or str(current["Id"]) in before: continue
        home = older.get(norm(current["Name"]), [])
        if len(home) == 1:
            moves.append({"grade": str(grade_id), "name": grade["Name"], "from": str(current["Id"]),
                          "to": str(home[0]["Id"]), "category": home[0]["Name"]})
    moving, sources = {m["grade"] for m in moves}, {m["from"] for m in moves}
    removals = [{"category": str(c["Id"]), "name": c["Name"]} for c in categories if str(c["Id"]) in sources
                and not [g for g in c.get("Grades") or [] if str(g.get("Id")) not in moving]]
    return moves, removals


async def move_grade_item(api, grade_id, category_id):
    """Put one grade item into another category, keeping everything else about it."""
    api.guard()
    grade = await api._json(f"/d2l/api/le/1.89/{api.course_id}/grades/{grade_id}")
    payload = {k: grade[k] for k in ("Name", "ShortName", "GradeType", "MaxPoints", "CanExceedMaxPoints", "IsBonus",
                                     "ExcludeFromFinalGradeCalculation", "GradeSchemeId", "IsHidden") if k in grade}
    payload.update(CategoryId=int(category_id), Description={"Content": (grade.get("Description") or {}).get("Html", ""), "Type": 1})
    if grade.get("AssociatedTool"): payload["AssociatedTool"] = grade["AssociatedTool"]
    await api._json(f"/d2l/api/le/1.89/{api.course_id}/grades/{grade_id}", "PUT", payload)
    return await api._json(f"/d2l/api/le/1.89/{api.course_id}/grades/{grade_id}")


async def remove_empty_category(api, category_id):
    """Delete a grade category, refusing if anything is in it."""
    api.guard()
    found = [c for c in await api._json(f"/d2l/api/le/1.98/{api.course_id}/grades/categories/") if str(c["Id"]) == str(category_id)]
    if not found: return {"removed": str(category_id)}
    if found[0].get("Grades"): raise ValueError(f"The grade category “{found[0]['Name']}” isn't empty; leaving it.")
    await api._json(f"/d2l/api/le/1.98/{api.course_id}/grades/categories/{category_id}", "DELETE")
    return {"removed": str(category_id)}


async def activity_lists(api):
    """Tool catalogues, including surveys not linked in Content. Never reads attempts."""
    async def paged(tool):
        objects, seen = [], set()
        url = f"/d2l/api/le/1.82/{api.course_id}/{tool}/"
        while url:
            if url in seen: raise ValueError(f"Repeated page in the {tool} inventory.")
            seen.add(url)
            result = await api._json(url)
            if not isinstance(result, dict) or not isinstance(result.get("Objects"), list):
                raise ValueError(f"Incomplete {tool} inventory. Nothing can be applied.")
            objects.extend(result["Objects"])
            url = result.get("Next")
            if url: url = re.sub(r"^https?://[^/]+", "", url)
        return objects
    quizzes = await paged("quizzes")
    try:
        surveys = await paged("surveys")
    except Exception as exc:
        # A missing tool/permission differs from an empty catalogue. Survey
        # transfers are blocked when either catalogue is unavailable.
        if not any(f"HTTP {status}" in str(exc) for status in (403, 404)): raise
        surveys = None
    topics = []
    for forum in await api._json(f"/d2l/api/le/1.82/{api.course_id}/discussions/forums/"):
        for topic in await api._json(f"/d2l/api/le/1.82/{api.course_id}/discussions/forums/{forum['ForumId']}/topics/"):
            topics.append({**topic, "ForumId": forum["ForumId"], "ForumName": forum["Name"]})
    return {"quizzes": quizzes, "discussions": topics, "surveys": surveys}
