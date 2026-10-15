"""Check Structured Transfer's Copy Components step against two real courses.

Uses the app's saved Brightspace session (sign in through the app first).

    python tools/copy_components_check.py SOURCE_ID TARGET_ID "Assignment name"
        Dry run: selects the assignment, its rubric and grade item in the wizard,
        checks the confirmation page, and stops before Finish. Nothing is copied.

    python tools/copy_components_check.py SOURCE_ID TARGET_ID "Assignment name" --copy
        Copies just that assignment (with its rubric and grade item) into TARGET_ID,
        waits for Brightspace to finish, and reads the copy back.

    python tools/copy_components_check.py SOURCE_ID TARGET_ID "Assignment name" --merge-categories
        For an assignment copied earlier: moves its grade item into the destination's
        other category with the same name, then removes the copy's category if it's empty.

Results are saved to transfer-investigation/copy-components-check.json.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import SESSION_FILE
from playwright.async_api import async_playwright
from transfer_api import TransferAPI
from transfer_components import copy_components, selection_for, target_matches, wait_for_copies
from transfer_guided import assignment_text, matching_assignment
from transfer_plan import all_objects, rubric_signature

HOST = "https://learn.okanagancollege.ca"


async def merge(page, target_id, source_id, name):
    from transfer_components import category_plan, move_grade_item, remove_empty_category
    api = TransferAPI(page, target_id, source_id=source_id, writable=True)
    target = await api.inventory(include_html=False)
    made = next((a for a in target["assignments"] if a["Name"] == name), None)
    if not made or not made.get("GradeItemId"): raise SystemExit(f"{name!r} has no grade item in {target['name']}.")
    grade = next(g for g in target["grades"] if g["Id"] == made["GradeItemId"])
    categories = await api._json(f"/d2l/api/le/1.98/{target_id}/grades/categories/")
    target["categories"] = categories
    before = {str(c["Id"]) for c in categories if c["Id"] != grade["CategoryId"]}
    moves, removals = category_plan(target, before, [str(grade["Id"])])
    if not moves: raise SystemExit("Nothing to merge: no other category has the same name.")
    for move in moves:
        after = await move_grade_item(api, move["grade"], move["to"])
        print(("PASS " if str(after["CategoryId"]) == move["to"] else "FAIL ") + f"{move['name']} is in category {after['CategoryId']}")
        print(("PASS " if after.get("AssociatedTool") == grade.get("AssociatedTool") else "FAIL ") + "still linked to the assignment")
        print(("PASS " if after.get("MaxPoints") == grade.get("MaxPoints") and after.get("Name") == grade.get("Name") else "FAIL ") + "name and points unchanged")
    for removal in removals:
        await remove_empty_category(api, removal["category"])
    listed = await api._json(f"/d2l/api/le/1.98/{target_id}/grades/categories/")
    for removal in removals:
        print(("PASS " if not any(str(c["Id"]) == removal["category"] for c in listed) else "FAIL ") + f"empty duplicate {removal['category']} removed")
    print("Categories now:", [(c["Name"], c.get("Weight"), [g.get("Id") for g in c.get("Grades") or []]) for c in listed])
    check = next(a for a in await api._json(f"/d2l/api/le/1.82/{target_id}/dropbox/folders/") if a["Id"] == made["Id"])
    print(("PASS " if check.get("GradeItemId") == grade["Id"] else "FAIL ") + "assignment still uses the same grade item")


async def main(source_id, target_id, name, copy, merge_only=False):
    if merge_only:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=SESSION_FILE)
            page = await context.new_page()
            await page.goto(HOST + "/d2l/home", wait_until="domcontentloaded")
            await merge(page, target_id, source_id, name)
            await browser.close()
        return
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=SESSION_FILE)
        page = await context.new_page()
        await page.goto(HOST + "/d2l/home", wait_until="domcontentloaded")
        source = await TransferAPI(page, source_id).inventory(include_html=False)
        target_api = TransferAPI(page, target_id, source_id=source_id, writable=True)
        before = await target_api.inventory(include_html=False)
        original = next((a for a in source["assignments"] if a["Name"] == name), None)
        if original is None: raise SystemExit(f"No assignment called {name!r} in {source['name']}.")
        if target_matches("assignment", name, before): raise SystemExit(f"{before['name']} already has {name!r}. Choose another.")
        topic = next((o for o in all_objects(source["modules"]) if o.get("ActivityType") == 3 and o.get("ToolItemId") == original["Id"]), None)
        if topic is None: raise SystemExit(f"{name!r} isn't linked anywhere in {source['name']}'s Content.")
        selection = selection_for([{"id": str(topic["Id"]), "kind": "assignment", "source_id": str(topic["Id"]), "title": topic["Title"]}], source)
        print("Wizard selection:", selection)
        log = lambda message, level="info": print(f"[{level}] {message}")
        confirmed = await copy_components(page, HOST, target_id, source_id, selection, log, finish=copy)
        result = {"selection": selection, "confirmed": {k: v for k, v in confirmed.items() if k.startswith("wizSelected") and v}}
        if copy:
            after = await wait_for_copies(lambda: target_api.inventory(include_html=False), [("assignment", name)], log)
            made = next(a for a in after["assignments"] if a["Name"] == name)
            grade = next((g for g in after["grades"] if g["Id"] == made.get("GradeItemId")), None)
            checks = {
                "Copy matches the original (instructions, rubric, points, submission settings)": matching_assignment(original, made),
                "Rubric": rubric_signature((made.get("Assessment") or {}).get("Rubrics", [])) == rubric_signature((original.get("Assessment") or {}).get("Rubrics", [])),
                "Instructions": assignment_text(made) == assignment_text(original),
                "Grade item exists": bool(grade),
                "No Content was added": len(list(all_objects(after["modules"]))) == len(list(all_objects(before["modules"]))),
            }
            for label, ok in checks.items(): print(("PASS " if ok else "FAIL ") + label)
            print("Grade item:", grade and {k: grade.get(k) for k in ("Id", "Name", "MaxPoints", "CategoryId")})
            print("Categories before:", [c["Name"] for c in before["categories"]])
            print("Categories after: ", [c["Name"] for c in after["categories"]])
            result.update(copy=made, grade=grade, checks=checks, categories_before=before["categories"], categories_after=after["categories"])
        out = ROOT / "transfer-investigation" / "copy-components-check.json"
        out.parent.mkdir(exist_ok=True); out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
        print("Saved", out)
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_id"); parser.add_argument("target_id"); parser.add_argument("assignment")
    parser.add_argument("--copy", action="store_true", help="really copy (otherwise stop before Finish)")
    parser.add_argument("--merge-categories", action="store_true", help="merge an earlier copy's grade item into the existing category")
    args = parser.parse_args()
    asyncio.run(main(args.source_id, args.target_id, args.assignment, args.copy, args.merge_categories))
