"""General transfer runner: a full offline run against an in-memory Brightspace."""
import copy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import transfer_components
import transfer_outline as o
from test_transfer_structure import STYLE, offline_courses
from transfer_outline_runner import OutlineRunner, styled_markup
from transfer_runner import load_plan, save_plan

PA_HTML = ('<html><head><style>.main-container{x:1}</style></head><body><div class="main-container">'
           '<header class="hero"><h1>Read chapter 2</h1><div class="hero-decoration">OC</div></header>'
           '<div class="content-body"><p>See <a href="notes.pdf">notes</a></p></div></div></body></html>')


def copy_files(target, selection):
    """What Copy Components does with course files: same path in the destination, replacing what's there."""
    for key in selection.get("files", []):
        relative = key.strip("\\").replace("\\", "/")
        source_path = next(k for k in target.source_files if k.lower() == (target.source_state["root"] + relative).lower())
        target.files[target.state["root"] + relative] = target.source_files[source_path]


def fake_components(copies):
    async def copy_components(page, base, target_id, source_id, selection, log=None, stop=None, finish=True):
        copies.append({k: v for k, v in selection.items() if not k.startswith("_")})
        target = page  # the fake destination is passed in as the "page"
        if "dropbox" in selection:
            original = copy.deepcopy(target.source_state["assignments"][0])
            original["Id"] = 900 + len(copies)
            target.state["assignments"].append(original)
        copy_files(target, selection)
        target.writes.append(("copy-components", tuple(selection.get("dropbox", ())), tuple(selection.get("files", ()))))
        return {}
    async def wait_for_copies(fetch, expected, log=None, stop=None, **_):
        return await fetch()
    return copy_components, wait_for_copies


async def fake_link(self, key, parent, row):
    activity = self.activity_ids[row["id"]]
    async def create():
        return await self.target.create_object(parent, {"Title": self.activity_names[row["id"]], "Type": 1, "ActivityType": 3,
            "ToolItemId": int(activity), "IsHidden": True, "Url": f"/d2l/common/dialogs/quickLink/quickLink.d2l?ou=2&rcode={activity}",
            "Description": {"Content": "", "Type": 1}})
    async def find():
        found = [x for x in await self.target.children(parent) if str(x.get("ToolItemId")) == str(activity)]
        return found[0] if found else None
    return await self.operation(key, create, find)


async def setup(tmp_path, monkeypatch, source_html=None):
    source, target = offline_courses()
    if source_html:
        source.files["/content/enforced/1-src/reading.html"] = source_html.encode()
        source.state["modules"][0]["children"][0]["html"] = source_html
    target.page, target.source_state, target.source_files = target, source.state, source.files
    copies = []
    copier, waiter = fake_components(copies)
    monkeypatch.setattr(transfer_components, "copy_components", copier)
    monkeypatch.setattr(transfer_components, "wait_for_copies", waiter)
    monkeypatch.setattr(OutlineRunner, "link_activity", fake_link)
    plan = o.build_outline_plan(await source.inventory(), await target.inventory(), "pattern", {
        "name": "Simple", "sections": [{"title": "Overview", "kind": "introduction"},
                                       {"title": "Readings", "kind": "folder", "keywords": ["read"]},
                                       {"title": "Work", "kind": "folder", "assignments": True, "default": True}],
        "default_is_rule": True})
    plan["templates"] = {k: {"id": k, "course": "2", "url": "/content/enforced/2-tgt/" + k + ".html", "html": STYLE}
                         for k in ("introduction", "session")}
    o.accept_suggestions(plan)
    week = plan["outline"][0]
    o.add_page(plan, week["id"], "placeholder", "Online classroom", index=0, message="Link coming soon.")
    path = tmp_path / "plan.json"
    save_plan(plan, path)
    return source, target, plan, path, copies


@pytest.mark.asyncio
async def test_outline_runs_end_to_end_and_reruns_without_writes(tmp_path, monkeypatch):
    source, target, plan, path, copies = await setup(tmp_path, monkeypatch)
    assert o.validate_outline(plan)[0] == []
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values()), [k for k, ok in result["validation"].items() if not ok]
    assert source.writes == []
    week = target.state["modules"][0]
    assert week["Title"] == "Week 1" and week["IsHidden"]
    assert [c["Title"] for c in week["children"]] == ["Online classroom", "Overview", "Readings", "Work"]
    folders = {c["Title"]: c for c in week["children"] if c["Type"] == 0}
    assert [c["Title"] for c in folders["Readings"]["children"]] == ["Read chapter 2", "Background"]
    assert [c["Title"] for c in folders["Work"]["children"]] == ["Discussion prompts", "Week 1 Essay"]
    # One Copy Components job: the essay and the course files the pages and file topics need.
    assert copies == [{"dropbox": ["70"], "files": [r"\background.pdf", r"\notes.pdf", r"\prompts.pdf"]}]
    assert target.files["/content/enforced/2-tgt/notes.pdf"] == b"%PDF notes"
    assert "3 copied by Brightspace (no download)" in result["report"]
    essay = folders["Work"]["children"][1]
    assert essay["ActivityType"] == 3 and essay["ToolItemId"] == 901
    reading = next(f for f in target.files.values() if b"See" in f and b"notes" in f)
    assert b"1-src" not in reading and b"/content/enforced/2-tgt/" in reading
    assert "COMPLETE" in result["report"] and "Destination: Target sandbox" in result["report"]
    writes = len(target.writes)
    again = await OutlineRunner(source, target, load_plan(path), path).run("2", True)
    assert all(again["validation"].values())
    assert len(target.writes) == writes, target.writes[writes:]
    assert len(copies) == 1


@pytest.mark.asyncio
async def test_styling_choice_is_applied_and_reported(tmp_path, monkeypatch):
    source, target, plan, path, _ = await setup(tmp_path, monkeypatch, PA_HTML)
    plan["styling"] = {"mode": "strip_bpa", "apply_template": False, "overrides": {}}
    row = plan["items"]["11"]
    preview = styled_markup(plan, row)
    assert preview.ok and "main-container" not in preview.html and "OC" not in preview.html
    save_plan(plan, path)
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values())
    page = next(f for f in target.files.values() if b"See" in f and b"notes" in f)
    assert b"main-container" not in page and b"<h1>Read chapter 2</h1>" in page
    assert "Page Automator style block" in result["styling_report"]["11"]
    assert "Read chapter 2: 1 Page Automator style block" in result["report"]


@pytest.mark.asyncio
async def test_existing_destination_modules_are_only_added_to(tmp_path, monkeypatch):
    source, target = offline_courses()
    target.state["modules"] = [{"Id": 700, "Type": 0, "Title": "Week 1", "IsHidden": False,
                                "children": [{"Id": 701, "Type": 1, "Title": "Teacher notes", "Url": "/x.html"}]}]
    target._index(target.state["modules"], None)
    target.page, target.source_state, target.source_files = target, source.state, source.files
    copier, waiter = fake_components([])
    monkeypatch.setattr(transfer_components, "copy_components", copier)
    monkeypatch.setattr(transfer_components, "wait_for_copies", waiter)
    monkeypatch.setattr(OutlineRunner, "link_activity", fake_link)
    plan = o.build_outline_plan(await source.inventory(), await target.inventory(), "destination")
    o.accept_suggestions(plan)
    path = tmp_path / "plan.json"; save_plan(plan, path)
    assert o.validate_outline(plan)[0] == []
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values()), [k for k, ok in result["validation"].items() if not ok]
    week = target.state["modules"][0]
    assert week["Title"] == "Week 1" and not week["IsHidden"] and len(target.state["modules"]) == 1
    titles = [c["Title"] for c in week["children"]]
    assert titles[0] == "Teacher notes"                              # what was there stays first
    assert titles[1:] == ["Read chapter 2", "Discussion prompts", "Week 1 Essay", "Background"]  # no Readings folder there


@pytest.mark.asyncio
async def test_existing_activity_with_the_same_name_blocks_unless_identical(tmp_path, monkeypatch):
    source, target, plan, path, copies = await setup(tmp_path, monkeypatch)
    clash = copy.deepcopy(source.state["assignments"][0]); clash.update(Id=950)
    clash["CustomInstructions"] = {"Html": "<p>Different</p>"}
    target.state["assignments"].append(clash)
    with pytest.raises(ValueError, match="already exists"):
        await OutlineRunner(source, target, plan, path).run("2", True)
    assert copies == []
    clash["CustomInstructions"] = {"Html": "<p>Write it.</p>"}       # an identical copy is reused, not copied again
    plan = load_plan(path)
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values()) and not any("dropbox" in c for c in copies)   # only files were copied


@pytest.mark.asyncio
async def test_blocked_plans_and_wrong_confirmation_never_write(tmp_path, monkeypatch):
    source, target, plan, path, _ = await setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Confirm"):
        await OutlineRunner(source, target, plan, path).run("999", True)
    o.unplace(plan, ["12"])
    with pytest.raises(ValueError, match="still need"):
        await OutlineRunner(source, target, plan, path).run("2", True)
    assert target.writes == []


class Gradebook:
    """Categories list their grade items, and empty categories can be deleted, like Brightspace."""
    @staticmethod
    def install(target):
        original = target._json
        async def _json(path, method="GET", payload=None):
            import re as _re
            tail = _re.sub(r"^/d2l/api/(?:le|lp)/[\d.]+/\d+/", "", path)
            if tail == "grades/categories/" and method == "GET":
                return [{**c, "Grades": [{"Id": g["Id"]} for g in target.state["grades"] if g.get("CategoryId") == c["Id"]]}
                        for c in copy.deepcopy(target.state["categories"])]
            found = _re.fullmatch(r"grades/categories/(\d+)", tail)
            if found and method == "DELETE":
                target._write(("DELETE", tail))
                target.state["categories"] = [c for c in target.state["categories"] if str(c["Id"]) != found.group(1)]
                return {}
            return await original(path, method, payload)
        target._json = _json


@pytest.mark.asyncio
async def test_copied_grade_items_join_the_existing_category_and_the_duplicate_goes(tmp_path, monkeypatch):
    source, target = offline_courses()
    source.state["assignments"][0]["GradeItemId"] = 501
    source.state["categories"] = [{"Id": 600, "Name": "Assignments", "Weight": 60, "Grades": [{"Id": 501}]}]
    target.state["categories"] = [{"Id": 800, "Name": "Assignments", "Weight": 70, "ExcludeFromFinalGrade": False},
                                  {"Id": 801, "Name": "Attendance", "Weight": 30, "ExcludeFromFinalGrade": False}]
    target.page, target.source_state, target.source_files = target, source.state, source.files
    Gradebook.install(target)
    async def copy_components(page, base, target_id, source_id, selection, log=None, stop=None, finish=True):
        # What Brightspace really does: a second "Assignments" category, weighted, with the copied grade item.
        made = copy.deepcopy(source.state["assignments"][0]); made.update(Id=901, GradeItemId=991)
        target.state["assignments"].append(made)
        target.state["categories"].append({"Id": 990, "Name": "Assignments", "Weight": 60, "ExcludeFromFinalGrade": False})
        target.state["grades"].append({"Id": 991, "Name": "Week 1 Essay", "GradeType": "Numeric", "MaxPoints": 20,
                                       "CategoryId": 990, "IsHidden": False, "AssociatedTool": {"ToolId": 2000, "ToolItemId": 901}})
        target.writes.append(("copy-components",)); copy_files(target, selection)
        return {}
    async def wait_for_copies(fetch, expected, log=None, stop=None, **_):
        return await fetch()
    monkeypatch.setattr(transfer_components, "copy_components", copy_components)
    monkeypatch.setattr(transfer_components, "wait_for_copies", wait_for_copies)
    monkeypatch.setattr(OutlineRunner, "link_activity", fake_link)
    plan = o.build_outline_plan(await source.inventory(), await target.inventory(), "mirror")
    path = tmp_path / "plan.json"; save_plan(plan, path)
    assert any("already has a “Assignments” grade category" in n for n in o.validate_outline(plan)[1])
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values()), [k for k, ok in result["validation"].items() if not ok]
    grade = next(g for g in target.state["grades"] if g["Id"] == 991)
    assert grade["CategoryId"] == 800 and grade["AssociatedTool"]["ToolItemId"] == 901 and grade["MaxPoints"] == 20
    assert [c["Id"] for c in target.state["categories"]] == [800, 801]
    assert "Week 1 Essay / in the existing “Assignments” category" in result["validation"]
    assert "visible in the gradebook" in result["report"]
    writes = len(target.writes)
    again = await OutlineRunner(source, target, load_plan(path), path).run("2", True)
    assert all(again["validation"].values()) and len(target.writes) == writes


@pytest.mark.asyncio
async def test_keeping_the_source_categories_leaves_the_copy_alone_and_flags_the_weights(tmp_path, monkeypatch):
    source, target = offline_courses()
    source.state["assignments"][0]["GradeItemId"] = 501
    target.state["categories"] = [{"Id": 800, "Name": "Assignments", "Weight": 100, "ExcludeFromFinalGrade": False}]
    target.page, target.source_state, target.source_files = target, source.state, source.files
    Gradebook.install(target)
    async def copy_components(page, base, target_id, source_id, selection, log=None, stop=None, finish=True):
        made = copy.deepcopy(source.state["assignments"][0]); made.update(Id=901, GradeItemId=991)
        target.state["assignments"].append(made)
        target.state["categories"].append({"Id": 990, "Name": "Assignments", "Weight": 60, "ExcludeFromFinalGrade": False})
        target.state["grades"].append({"Id": 991, "Name": "Week 1 Essay", "CategoryId": 990, "IsHidden": True})
        copy_files(target, selection)
        return {}
    async def wait_for_copies(fetch, expected, log=None, stop=None, **_):
        return await fetch()
    monkeypatch.setattr(transfer_components, "copy_components", copy_components)
    monkeypatch.setattr(transfer_components, "wait_for_copies", wait_for_copies)
    monkeypatch.setattr(OutlineRunner, "link_activity", fake_link)
    plan = o.build_outline_plan(await source.inventory(), await target.inventory(), "mirror")
    plan["grade_categories"] = "keep"
    path = tmp_path / "plan.json"; save_plan(plan, path)
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values())
    assert len(target.state["categories"]) == 2 and next(g for g in target.state["grades"] if g["Id"] == 991)["CategoryId"] == 990
    assert "weights in Target sandbox now total 160%" in result["report"]


@pytest.mark.asyncio
async def test_files_already_in_the_destination_are_reused_or_kept_separately_never_replaced(tmp_path, monkeypatch):
    source, target, plan, path, copies = await setup(tmp_path, monkeypatch)
    target.files["/content/enforced/2-tgt/notes.pdf"] = b"%PDF notes"            # identical: reuse
    target.files["/content/enforced/2-tgt/prompts.pdf"] = b"%PDF EA105's own"     # different: keep separately
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values()), [k for k, ok in result["validation"].items() if not ok]
    assert [c.get("files") for c in copies] == [[r"\background.pdf"]]              # only the new file goes to Brightspace
    assert target.files["/content/enforced/2-tgt/prompts.pdf"] == b"%PDF EA105's own"
    reading = next(f for f in target.files.values() if b"See" in f and b"notes" in f)
    assert b"/content/enforced/2-tgt/notes.pdf" in reading                       # points at the destination's own copy
    kept = [p for p, body in target.files.items() if body == b"%PDF prompts" and "bpa-transfer-" in p]
    assert kept, "EA-113's prompts.pdf should be in the transfer's own folder"
    assert "1 already in the destination, identical; reused" in result["report"]
    assert "kept separately: prompts.pdf" in result["report"]


@pytest.mark.asyncio
async def test_a_large_file_that_would_replace_the_destinations_stops_before_any_write(tmp_path, monkeypatch):
    source, target, plan, path, _ = await setup(tmp_path, monkeypatch)
    target.files["/content/enforced/2-tgt/prompts.pdf"] = b"different"
    import transfer_outline_runner
    monkeypatch.setattr(transfer_outline_runner, "DOWNLOAD_LIMIT", 5)
    with pytest.raises(ValueError, match="can't be copied without replacing it"):
        await OutlineRunner(source, target, plan, path).run("2", True)
    assert target.writes == []


@pytest.mark.asyncio
async def test_links_the_moodle_import_left_broken_are_unlinked_and_reported(tmp_path, monkeypatch):
    html = '<p>See <a href="notes.pdf">notes</a> and <a href="$@URLVIEWBYID*2431724@$">the workbook</a>.</p>'
    source, target, plan, path, _ = await setup(tmp_path, monkeypatch, html)
    assert any("already broken in the source" in n for n in o.validate_outline(plan)[1])
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values())
    page = next(f for f in target.files.values() if b"the workbook" in f)
    assert b"$@" not in page and b"the workbook" in page and b'href="/content/enforced/2-tgt/notes.pdf"' in page
    assert "1 link on “Read chapter 2” was already broken in the source" in result["report"]


def test_old_source_scans_remain_compatible_but_new_survey_scans_detect_changes():
    from transfer_runner import source_inventory_matches
    from transfer_plan import digest
    source = {"modules": [{"Id": 1}], "name": "Original"}
    plan = {"source": copy.deepcopy(source), "source_hash": digest(source)}
    fresh = {**source, "surveys": [{"SurveyId": 95, "Name": "Preferences"}]}
    assert source_inventory_matches(plan, fresh)
    assert not source_inventory_matches(plan, {**fresh, "name": "Changed"})
    plan = {"source": copy.deepcopy(fresh), "source_hash": digest(fresh)}
    assert not source_inventory_matches(plan, {**fresh, "surveys": []})


async def activity_setup(tmp_path, monkeypatch):
    """Keep the real runner and link adapter bridge; only the remote UI is faked."""
    from content_checker import ContentChecker
    source, target = offline_courses()
    source.state["modules"][0]["children"] += [
        {"Id": 21, "Type": 1, "Title": "Discussion Content label", "ActivityType": 6, "ToolItemId": 90},
        {"Id": 22, "Type": 1, "Title": "Survey Content label", "ActivityType": 12, "ToolItemId": 95}]
    source.state["discussions"] = [{"TopicId": 90, "Name": "Advice", "ForumId": 9}]
    source.state["surveys"] = [{"SurveyId": 95, "Name": "Preferences"}]
    target.state.update(discussions=[], surveys=[])
    target.page = target; target.context = target
    copies, links = [], []
    async def copier(page, base, target_id, source_id, selection, *args):
        copies.append(copy.deepcopy(selection))
        page.writes.append(("copy-components",))
        for tool, pool, id_key in (("discuss", "discussions", "TopicId"), ("surveys", "surveys", "SurveyId")):
            if tool in selection:
                item = copy.deepcopy(source.state[pool][0]); item[id_key] += 1000
                page.state[pool].append(item)
        return {}
    async def attach(self, context, base, course_id, item):
        links.append(copy.deepcopy(item))
        await context.create_object(item["target_module_id"], {"Title": item["name"], "Type": 1,
            "ActivityType": item["content_activity_type"], "ToolItemId": int(item["activity_id"]), "IsHidden": True,
            "Description": {"Content": "", "Type": 1}, "Url": "/quicklink"})
        return True, None
    monkeypatch.setattr(transfer_components, "copy_components", copier)
    monkeypatch.setattr(ContentChecker, "_attach_existing_activity", attach)
    plan = o.build_outline_plan(await source.inventory(), await target.inventory(), "empty")
    module = o.add_module(plan, title="Activities")
    o.place(plan, ["21", "22"], module["id"])
    o.skip(plan, [row["id"] for row in o.unplaced(plan)])
    path = tmp_path / "activities.json"; save_plan(plan, path)
    return source, target, plan, path, copies, links


@pytest.mark.asyncio
async def test_discussion_and_survey_copy_link_by_id_and_resume_without_duplicates(tmp_path, monkeypatch):
    source, target, plan, path, copies, links = await activity_setup(tmp_path, monkeypatch)
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert all(result["validation"].values())
    assert copies == [{"discuss": ["t90"], "surveys": ["95"]}]
    assert [(item["name"], item["activity_id"]) for item in links] == [("Advice", "1090"), ("Preferences", "1095")]
    writes = len(target.writes)
    await OutlineRunner(source, target, load_plan(path), path).run("2", True)
    assert len(target.writes) == writes and len(copies) == 1 and len(links) == 2
    assert source.writes == []


@pytest.mark.asyncio
async def test_survey_catalogue_unavailable_stops_before_any_write(tmp_path, monkeypatch):
    source, target, plan, path, copies, _ = await activity_setup(tmp_path, monkeypatch)
    target.state["surveys"] = None
    with pytest.raises(ValueError, match="survey catalogue is unavailable"):
        await OutlineRunner(source, target, plan, path).run("2", True)
    assert target.writes == [] and copies == []


@pytest.mark.asyncio
async def test_survey_name_clash_after_scanning_stops_before_any_write(tmp_path, monkeypatch):
    source, target, plan, path, copies, _ = await activity_setup(tmp_path, monkeypatch)
    target.state["surveys"] = [{"SurveyId": 999, "Name": "Preferences"}]
    with pytest.raises(ValueError, match="already exists"):
        await OutlineRunner(source, target, plan, path).run("2", True)
    assert target.writes == [] and copies == []


@pytest.mark.asyncio
async def test_resuming_survey_only_pending_copy_does_not_assume_it_finished(tmp_path, monkeypatch):
    source, target, plan, path, copies, _ = await activity_setup(tmp_path, monkeypatch)
    o.skip(plan, ["21"])
    plan["journal"]["components"] = {"state": "pending"}
    save_plan(plan, path)
    runner = OutlineRunner(source, target, plan, path)
    runner.server_files = []
    with pytest.raises(RuntimeError, match="Uncertain previous request components"):
        await runner.copy_activities(await target.inventory())
    assert copies == [] and target.writes == []


@pytest.mark.asyncio
async def test_unverified_activity_click_stays_pending_and_is_not_repeated(tmp_path, monkeypatch):
    from content_checker import ContentChecker
    source, target, plan, path, copies, _ = await activity_setup(tmp_path, monkeypatch)
    clicks = []
    async def delayed_link(*args):
        clicks.append(1)
        return False, "Content link could not be verified after Add Existing"
    monkeypatch.setattr(ContentChecker, "_attach_existing_activity", delayed_link)
    with pytest.raises(RuntimeError, match="could not be verified"):
        await OutlineRunner(source, target, plan, path).run("2", True)
    assert load_plan(path)["journal"]["item:21"]["state"] == "pending"
    with pytest.raises(RuntimeError, match="Uncertain previous request item:21"):
        await OutlineRunner(source, target, load_plan(path), path).run("2", True)
    assert len(clicks) == 1 and len(copies) == 1


@pytest.mark.asyncio
async def test_new_native_survey_link_is_hidden_and_visibility_is_journaled(tmp_path, monkeypatch):
    from content_checker import ContentChecker
    source, target, plan, path, copies, _ = await activity_setup(tmp_path, monkeypatch)
    original_attach = ContentChecker._attach_existing_activity
    async def attach(self, context, base, course_id, item):
        result = await original_attach(self, context, base, course_id, item)
        if item["activity_kind"] == "SURVEY":
            next(obj for obj in context.objects.values() if obj.get("ToolItemId") == int(item["activity_id"]))["IsHidden"] = False
        return result
    async def hide(topic_id):
        target.writes.append(("hide-topic", topic_id))
        target.objects[str(topic_id)]["IsHidden"] = True
        return copy.deepcopy(target.objects[str(topic_id)])
    monkeypatch.setattr(ContentChecker, "_attach_existing_activity", attach)
    monkeypatch.setattr(target, "hide_topic", hide, raising=False)
    result = await OutlineRunner(source, target, plan, path).run("2", True)
    assert result["validation"]["Survey Content label / hidden"]
    assert result["journal"]["item:22:visibility"]["state"] == "verified"
    writes = len(target.writes)
    await OutlineRunner(source, target, load_plan(path), path).run("2", True)
    assert len(target.writes) == writes and len(copies) == 1
    target.objects[str(result["journal"]["item:22"]["result"]["Id"])]["IsHidden"] = False
    with pytest.raises(RuntimeError, match="changed or disappeared: item:22:visibility"):
        await OutlineRunner(source, target, load_plan(path), path).run("2", True)
    assert len(target.writes) == writes  # An instructor's later visibility change is left alone.
