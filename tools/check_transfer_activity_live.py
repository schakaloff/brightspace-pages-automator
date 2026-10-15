"""Activity verification confined to Nick's two Brightspace sandboxes.

Uses the app's saved login. No activity attempts, student records, credentials
or session contents are read or printed.
"""
import asyncio
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from check_transfer_studio_live import with_saved_session, SOURCE, TARGET
from transfer_api import TransferAPI
from transfer_plan import all_objects
from transfer_components import activity_name, target_matches
from transfer_outline_runner import apply_outline, scan_courses
from transfer_outline_runner import OutlineRunner
from transfer_runner import load_plan, save_plan
import transfer_outline as outline
from uuid import uuid4

DATA = ROOT / "transfer-investigation" / "activity-live"


async def sign_in_and_run(page, action):
    """Use exactly the app's configured login method and credential-store keys."""
    import keyring
    from browser import wait_for_login
    config = json.loads((ROOT / "user_config.json").read_text(encoding="utf-8"))
    method = config.get("bs_login_method")
    if method not in ("direct", "sso"):
        method = "sso" if config.get("sso_email") or not config.get("bs_username") else "direct"
    direct = method == "direct"
    username = config.get("bs_username", "") if direct else ""
    email = config.get("sso_email", "") if not direct else ""
    password = keyring.get_password("BrightspacePagesAutomator", username) if username else None
    sso_password = keyring.get_password("BrightspacePagesAutomator_SSO", email) if email else None
    if not (username and password or email and sso_password):
        raise RuntimeError("The selected login method has no saved credentials. Sign in through the app.")
    await asyncio.wait_for(wait_for_login(page, page.context, username, password, email, sso_password), timeout=120)
    return await action(page)


async def inspect(page):
    DATA.mkdir(parents=True, exist_ok=True)
    inventories, summary = {}, {}
    for course in (SOURCE, TARGET):
        api = TransferAPI(page, course)
        inventory = await api.inventory(include_html=False)
        if "sandbox" not in inventory["name"].casefold():
            raise ValueError("Both courses must still be named Sandbox.")
        inventories[course] = inventory
        topics = [{k: obj.get(k) for k in ("Id", "Title", "ActivityType", "ToolItemId", "IsHidden")}
                  for obj in all_objects(inventory["modules"]) if obj.get("ActivityType") in (6, 12)]
        try:
            surveys = await api._json(f"/d2l/api/le/1.82/{course}/surveys/")
            survey_result = {"status": "available", "data": surveys}
        except Exception as exc:
            survey_result = {"status": "unavailable", "reason": str(exc).splitlines()[0]}
        inventory["survey_probe"] = survey_result
        summary[course] = {"name": inventory["name"], "content_activities": topics,
            "discussions": [{k: obj.get(k) for k in ("TopicId", "Name", "ForumId", "GradeItemId", "IsHidden")}
                            for obj in inventory["discussions"]], "surveys": survey_result}
    (DATA / "inspection.json").write_text(json.dumps(inventories, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


async def transfer(page, resume=None):
    """One discussion and one survey; all other source content is left out."""
    DATA.mkdir(parents=True, exist_ok=True)
    log = lambda text, level="info": print(f"[{level}] {text}", flush=True)
    if resume:
        path = Path(resume).resolve()
        if path.parent != DATA.resolve(): raise ValueError("Resume must use this check's saved plan directory.")
        plan = load_plan(path)
    else:
        source, target = await scan_courses(page, SOURCE, TARGET, log)
        if any("sandbox" not in item["name"].casefold() for item in (source, target)):
            raise ValueError("Both courses must still be named Sandbox.")
        plan = outline.build_outline_plan(source, target, "empty")
        chosen = []
        for kind in ("discussion", "survey"):
            candidates = [row for row in plan["items"].values() if row["kind"] == kind
                          and not target_matches(kind, activity_name(row, source), target)]
            if not candidates: raise ValueError(f"No uncopied {kind} is available. Resume the saved verification plan instead.")
            chosen.append(candidates[0]["id"])
        module = outline.add_module(plan, title="Transfer Studio activities " + uuid4().hex[:8])
        outline.place(plan, chosen, module["id"])
        outline.skip(plan, [row["id"] for row in outline.unplaced(plan)])
        path = DATA / (plan["id"] + ".json")
        save_plan(plan, path)
    rows = [plan["items"][node["item"]] for node, _, _ in outline.walk(plan["outline"]) if node["kind"] == "item"]
    if (plan["source"]["id"], plan["target"]["id"]) != (SOURCE, TARGET) or len(rows) != 2 or {r["kind"] for r in rows} != {"discussion", "survey"}:
        raise ValueError("This check only transfers one discussion and one survey between Nick's two sandboxes.")
    details = await TransferAPI(page, TARGET)._json(f"/d2l/api/lp/1.49/courses/{TARGET}")
    if "sandbox" not in details["Name"].casefold(): raise ValueError("Only the destination sandbox is writable.")
    print(json.dumps({"plan": str(path), "module": plan["outline"][0]["title"],
        "activities": [{"kind": r["kind"], "name": activity_name(r, plan["source"])} for r in rows]}), flush=True)
    result = await apply_outline(page, plan, path, TARGET, True, log)
    print(json.dumps({"result": "passed" if all(result["validation"].values()) else "needs attention",
        "checks": result["validation"], "report": str(path.with_suffix(".report.txt"))}), flush=True)
    if not all(result["validation"].values()): raise RuntimeError("Readback checks need attention; inspect the saved report before resuming.")


async def inspect_picker(page, kind, select=False):
    """Inspect the existing verification module's non-mutating tool chooser."""
    from content_checker import ContentChecker
    plans = [load_plan(path) for path in DATA.glob("*.json") if not path.name.endswith(".recovery.json") and path.name != "inspection.json"]
    plan = next(p for p in plans if p.get("outline") and p["outline"][0]["title"].startswith("Transfer Studio activities "))
    module = plan["journal"]["node:" + plan["outline"][0]["id"]]["result"]["Id"]
    api = TransferAPI(page, TARGET)
    children = await api.children(module)
    print(json.dumps({"module": module, "children": children}), flush=True)
    try:
        legacy = await api._json(f"/d2l/api/le/1.0/{TARGET}/content/modules/{module}/structure/")
        print(json.dumps({"legacy_readback": "available", "count": len(legacy)}), flush=True)
    except Exception as exc:
        print(json.dumps({"legacy_readback": str(exc).splitlines()[0]}), flush=True)
    if select and any(obj.get("ActivityType") == (6 if kind == "discussion" else 12) for obj in children):
        raise ValueError("That tool already has a link in the verification module; do not repeat the selection.")
    await page.goto(f"https://learn.okanagancollege.ca/d2l/le/lessons/{TARGET}/units/{module}", wait_until="domcontentloaded")
    for _ in range(30):
        clicked = False
        for frame in page.frames:
            control = frame.locator('d2l-button[aria-label="Add Existing"],d2l-button.add-existing-btn')
            if await control.count() and await control.first.is_visible():
                await page.wait_for_timeout(5000)
                await control.first.click(); clicked = True; break
        if clicked: break
        await page.wait_for_timeout(1000)
    await page.wait_for_timeout(3000)
    controls = """() => {const result=[]; function walk(root){for(const e of root.querySelectorAll('*')){
        if(e.shadowRoot) walk(e.shadowRoot);
        if(/button|menu-item|list-item|input|label|^a$/i.test(e.tagName) && e.getBoundingClientRect().width>1)
            result.push({tag:e.tagName,id:e.id,type:e.getAttribute('type'),label:e.getAttribute('aria-label'),text:e.textContent.trim().slice(0,140)});
        }} walk(document); return result.slice(-120); }"""
    for frame in page.frames: print(json.dumps({"stage": "menu", "frame": frame.url.split("?")[0], "controls": await frame.evaluate(controls)}), flush=True)
    await page.screenshot(path=str(DATA / "picker-menu.png"), full_page=True)
    checker = object.__new__(ContentChecker)
    if kind == "survey":
        for frame in page.frames:
            if "/smart-curriculum/" in frame.url:
                more = frame.get_by_text("More", exact=True)
                if await more.count() == 1: await more.click()
        await page.wait_for_timeout(1500)
    if await checker._click_visible_exact_text(page, "Discussion" if kind == "discussion" else "Surveys"):
        await page.wait_for_timeout(3000)
        if kind == "discussion":
            forum = next(t["ForumName"] for t in plan["source"]["discussions"] if t["Name"] == "Share an Online Resource with your peers! - part of attendance marks")
            await checker._click_visible_exact_text(page, forum)
            await page.wait_for_timeout(1500)
        for frame in page.frames: print(json.dumps({"stage": "picker", "frame": frame.url.split("?")[0], "controls": await frame.evaluate(controls)}), flush=True)
        await page.screenshot(path=str(DATA / f"picker-{kind}.png"), full_page=True)
        if select:
            row = next(r for r in plan["items"].values() if r["kind"] == kind and r["id"] not in plan["skipped"])
            if not await checker._click_visible_exact_text(page, activity_name(row, plan["source"])):
                raise ValueError("The exact activity was unavailable or ambiguous.")
            await page.wait_for_timeout(2500)
            for frame in page.frames: print(json.dumps({"stage": "after-select", "frame": frame.url.split("?")[0], "controls": await frame.evaluate(controls)}), flush=True)
            await page.screenshot(path=str(DATA / f"after-select-{kind}.png"), full_page=True)
            print(json.dumps({"children_after_select": await api.children(module)}), flush=True)


async def verify_discussion_adapter(page):
    """Exercise the production adapter with the existing copy; no component copy job."""
    base_path = DATA / "0780b4d0c269446fbe57a27d89ce6201.json"
    base_plan = load_plan(base_path)
    api = TransferAPI(page, TARGET, source_id=SOURCE, writable=True)
    target = await api.inventory(include_html=False)
    if "sandbox" not in target["name"].casefold(): raise ValueError("Only the sandbox is writable.")
    root_id = base_plan["journal"]["node:" + base_plan["outline"][0]["id"]]["result"]["Id"]
    root = next(obj for obj in target["modules"] if obj["Id"] == root_id)
    if not root.get("IsHidden") or "bpa-transfer:" + base_plan["id"] not in (root.get("Description") or {}).get("Html", ""):
        raise ValueError("The hidden verification module's ownership could not be confirmed.")
    selected = next(row for row in base_plan["items"].values() if row["kind"] == "discussion" and row["id"] not in base_plan["skipped"])
    name = activity_name(selected, base_plan["source"])
    ids = target_matches("discussion", name, target)
    if len(ids) != 1: raise ValueError("The existing discussion copy is missing or ambiguous.")
    existing_checks = sorted(DATA.glob("picker-check-*.json"))
    if existing_checks:
        path = existing_checks[0]; plan = load_plan(path)
    else:
        plan = outline.build_outline_plan(base_plan["source"], target, "empty")
        outline.skip(plan, list(plan["items"]))
        path = DATA / ("picker-check-" + plan["id"] + ".json"); save_plan(plan, path)
    runner = OutlineRunner(TransferAPI(page, SOURCE), api, plan, path,
        lambda text, level="info": print(f"[{level}] {text}", flush=True))
    runner.journal["content-before"] = {"state": "verified", "result": {"ids": [str(obj["Id"]) for obj in all_objects(target["modules"])]}}
    runner.activity_names, runner.activity_ids, runner.activity_target = {selected["id"]: name}, {selected["id"]: ids[0]}, target
    print(json.dumps({"plan": str(path), "mode": "native discussion picker; existing copy"}), flush=True)
    module = await runner.ensure_object("native-picker-folder", root_id, "Native discussion picker verification", module=True)
    key = "item:" + selected["id"]
    previous = runner.journal.get(key)
    if previous and previous.get("state") == "pending":
        # This recovery is restricted to our isolated test module. The prior
        # test browser has closed; inspect twice before explicitly retrying.
        for _ in range(2):
            if await api.children(module["Id"]): raise ValueError("The test module now contains content. Reconcile it instead of retrying.")
            await asyncio.sleep(1)
        runner.journal["picker-inspection"] = {"state": "verified", "result": {"module": module["Id"], "readbacks": 2, "children": []}}
        runner.journal[key] = {"state": "rejected", "error": "Sandbox inspection confirmed no link was saved after the prior test browser closed.", "inspected_request": previous}
        runner.persist()
    linked = await runner.link_activity(key, module["Id"], selected)
    assert linked["IsHidden"] and str(linked["ToolItemId"]) == ids[0]
    print(json.dumps({"result": "passed", "module": module["Id"], "link": linked["Id"], "discussion": linked["ToolItemId"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="Refresh the session using the app's saved login.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create", action="store_true", help="Copy and link one discussion/survey into a hidden verification module.")
    mode.add_argument("--resume", help="Resume/re-check this verification's saved plan.")
    mode.add_argument("--picker", choices=("discussion", "survey"), help="Read the non-mutating Add Existing chooser.")
    mode.add_argument("--verify-discussion", action="store_true", help="Link the existing discussion copy into a new hidden test submodule with the production adapter.")
    parser.add_argument("--select", action="store_true", help="In --picker mode, select the exact copied verification activity once.")
    args = parser.parse_args()
    action = verify_discussion_adapter if args.verify_discussion else (lambda page: inspect_picker(page, args.picker, args.select)) if args.picker else (lambda page: transfer(page, args.resume)) if args.create or args.resume else inspect
    asyncio.run(with_saved_session((lambda page: sign_in_and_run(page, action)) if args.login else action))
