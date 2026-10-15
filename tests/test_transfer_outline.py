"""General transfer outline: starting points, editing, patterns, suggestions and checks."""
import copy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import transfer_outline as o
from test_transfer_structure import source_course


def target_course(modules=()):
    return {"id": "2", "name": "Target shell", "root": "/content/enforced/2-tgt/", "modules": list(modules),
            "assignments": [], "categories": [], "grades": [], "setup": {"GradingSystem": "Weighted"}, "versions": ["1.99"]}


def titles(nodes):
    return [n["title"] if n["kind"] != "item" else "·" + n["item"] for n in nodes]


def errors(plan):
    return o.validate_outline(plan)[0]


def test_rows_cover_every_source_topic_with_kind_and_method():
    rows = o.make_rows(source_course())
    assert list(rows) == ["11", "12", "13", "15"]
    assert [(r["kind"], r["method"]) for r in rows.values()] == [
        ("page", "content"), ("file", "content"), ("assignment", "components"), ("file", "content")]
    assert rows["15"]["path"] == ["Week 1", "Readings"] and rows["15"]["top"] == "10"


@pytest.mark.parametrize("activity, kind, method", [
    (4, "quiz", "components"), (5, "discussion", "components"), (12, "survey", "components"),
    (7, "tool", "manual"), (10, "checklist", "manual"), (99, "other", "manual")])
def test_activity_kinds(activity, kind, method):
    assert o.activity_kind({"ActivityType": activity}) == kind and o.copy_method(kind) == method


def test_mirror_places_everything_in_the_same_folders():
    plan = o.build_outline_plan(source_course(), target_course(), "mirror")
    week = plan["outline"][0]
    assert titles(plan["outline"]) == ["Week 1"] and week["sources"] == ["10"]
    assert titles(week["children"]) == ["·11", "·12", "·13", "Readings"]
    assert titles(week["children"][3]["children"]) == ["·15"]
    assert not o.unplaced(plan) and errors(plan) == []


def test_empty_start_leaves_everything_to_place_and_blocks_creation():
    plan = o.build_outline_plan(source_course(), target_course(), "empty")
    assert plan["outline"] == [] and len(o.unplaced(plan)) == 4
    assert errors(plan) == ["4 items still need a place, or Skip."]


def test_missing_source_page_blocks_only_when_it_is_included():
    source = source_course()
    page = source['modules'][0]['children'][0]
    page.pop('html')
    page['html_error'] = 'The page file is missing in Brightspace (404).'
    plan = o.build_outline_plan(source, target_course(), 'mirror')
    assert any('Read chapter 2' in message and 'missing' in message and 'Skip' in message for message in errors(plan))
    o.skip(plan, ['11'])
    assert errors(plan) == []


def test_same_source_and_destination_is_refused():
    with pytest.raises(ValueError):
        o.build_outline_plan(source_course(), {**target_course(), "id": "1"})


def test_pattern_start_builds_sections_and_places_confident_items():
    plan = o.build_outline_plan(source_course(), target_course(), "pattern", o.ea105_pattern())
    week = plan["outline"][0]
    assert titles(week["children"]) == ["Online Classroom", "Module Introduction", "Before Class", "Class Materials", "After Class"]
    after = week["children"][4]
    assert titles(after["children"]) == ["·13"]  # assignments go to After Class
    assert titles(week["children"][3]["children"])[0] == "Session Overview"
    assert not o.unplaced(plan) and errors(plan) == []


def test_pattern_is_configurable_and_unsure_items_are_only_suggested():
    pattern = {"name": "Readings first", "sections": [
        {"title": "Readings", "kind": "folder", "keywords": ["read"]},
        {"title": "Work", "kind": "folder", "assignments": True}]}
    plan = o.build_outline_plan(source_course(), target_course(), "pattern", pattern)
    week = plan["outline"][0]
    readings, work = week["children"]
    assert titles(readings["children"]) == ["·15"]          # its source folder is called Readings
    assert titles(work["children"]) == ["·13"]
    waiting = {r["id"]: r for r in o.unplaced(plan)}
    assert waiting["11"]["suggested"] == readings["id"]     # "Read chapter 2": keyword, not certain
    assert waiting["12"]["suggested"] == readings["id"]     # no evidence: first section, for review
    assert o.accept_suggestions(plan) == 2 and not o.unplaced(plan)


def test_destination_start_uses_existing_modules_without_renaming_or_moving_them():
    shell = [{"Id": 500, "Type": 0, "Title": "Week 1", "children": [{"Id": 501, "Type": 0, "Title": "Readings", "children": []}]},
             {"Id": 600, "Type": 0, "Title": "Course Resources", "children": []}]
    plan = o.build_outline_plan(source_course(), target_course(shell), "destination")
    week, resources = plan["outline"]
    assert week["existing_id"] == "500" and week["sources"] == ["10"] and resources["sources"] == []
    readings = week["children"][0]
    assert readings["existing_id"] == "501" and titles(readings["children"]) == ["·15"]
    assert {"·11", "·12", "·13"} <= set(titles(week["children"]))
    with pytest.raises(ValueError): o.rename(plan, week["id"], "Other")
    with pytest.raises(ValueError): o.move(plan, readings["id"], None)


def test_editing_modules_items_and_skips():
    plan = o.build_outline_plan(source_course(), target_course(), "empty")
    unit = o.add_module(plan, None, "Unit 1")
    sub = o.add_module(plan, unit["id"], "Readings")
    o.place(plan, ["15", "11"], sub["id"])
    o.place(plan, ["12"], unit["id"])
    o.skip(plan, ["13"])
    assert titles(unit["children"]) == ["Readings", "·12"] and titles(sub["children"]) == ["·15", "·11"]
    assert not o.unplaced(plan) and errors(plan) == []
    o.place(plan, ["11"], unit["id"], index=0)               # moving an item replaces its old place
    assert titles(unit["children"]) == ["·11", "Readings", "·12"] and titles(sub["children"]) == ["·15"]
    o.move(plan, sub["id"], unit["id"], 0)
    assert titles(unit["children"]) == ["Readings", "·11", "·12"]
    o.rename(plan, sub["id"], "  Course   readings ")
    assert sub["title"] == "Course readings"
    o.place(plan, ["13"], sub["id"])                         # a skipped item can be placed again
    assert "13" not in plan["skipped"]
    o.remove(plan, sub["id"])                                 # its items go back to the unplaced list
    assert {r["id"] for r in o.unplaced(plan)} == {"15", "13"}
    with pytest.raises(ValueError): o.move(plan, unit["id"], unit["id"])
    with pytest.raises(ValueError): o.rename(plan, unit["id"], "")
    note = o.add_page(plan, unit["id"], "placeholder", "Note")
    with pytest.raises(ValueError): o.move(plan, note["id"], None)    # only modules at the top level


def test_whole_source_folder_can_be_copied_into_a_module():
    plan = o.build_outline_plan(source_course(), target_course(), "empty")
    unit = o.add_module(plan, None, "Unit")
    node = o.place_source_module(plan, "14", unit["id"])
    assert node["title"] == "Readings" and titles(node["children"]) == ["·15"]


def test_manual_activities_start_skipped_and_cannot_be_placed():
    source = source_course()
    source["modules"][0]["children"].append({"Id": 16, "Type": 1, "Title": "Kaltura", "ActivityType": 7})
    plan = o.build_outline_plan(source, target_course(), "mirror")
    assert "16" in plan["skipped"] and "16" not in o.placements(plan)
    with pytest.raises(ValueError, match="Brightspace"):
        o.place(plan, ["16"], plan["outline"][0]["id"])


def test_name_clashes_with_the_destination_and_between_siblings_block():
    shell = [{"Id": 500, "Type": 0, "Title": "Week 1", "children": []}]
    plan = o.build_outline_plan(source_course(), target_course(shell), "mirror")
    assert any("already in the destination" in e for e in errors(plan))
    o.rename(plan, plan["outline"][0]["id"], "Week 1 (copied)")
    assert errors(plan) == []
    o.add_module(plan, None, "Week 1 (copied)")
    assert any("used twice" in e for e in errors(plan))


def test_items_in_existing_modules_cannot_clash_with_existing_topics():
    shell = [{"Id": 500, "Type": 0, "Title": "Week 1", "children": [{"Id": 502, "Type": 1, "Title": "Background"}]}]
    plan = o.build_outline_plan(source_course(), target_course(shell), "destination")
    o.place(plan, ["15"], plan["outline"][0]["id"])
    issues = o.check(plan)
    clash = [i for i in issues if "Background" in i["text"] and i["level"] == "error"]
    assert clash and clash[0]["node"]


def test_template_pages_need_a_template_and_note_missing_text():
    plan = o.build_outline_plan(source_course(), target_course(), "mirror")
    intro = o.add_page(plan, plan["outline"][0]["id"], "introduction", "Module Introduction", index=0)
    assert plan["templates"]["introduction"]["builtin"] and errors(plan) == []   # found automatically
    del plan["templates"]["introduction"]
    assert any("page template" in e for e in errors(plan))
    o.ensure_templates(plan)
    assert errors(plan) == []
    assert any("no text yet" in n for n in o.validate_outline(plan)[1])
    o.set_text(plan, intro["id"], "Welcome", ["Explain", " "])
    assert intro["text"] == {"introduction": "Welcome", "session": "", "outcomes": ["Explain"]}
    assert not any("no text yet" in n for n in o.validate_outline(plan)[1])


def test_tampered_source_scan_and_missing_rows_block():
    plan = o.build_outline_plan(source_course(), target_course(), "mirror")
    plan["source"]["name"] = "Edited"
    assert any("scan" in e for e in errors(plan))


def test_patterns_round_trip_and_bad_files_are_ignored(tmp_path):
    plan = o.build_outline_plan(source_course(), target_course(), "pattern", o.ea105_pattern())
    saved = o.pattern_from_module(plan, plan["outline"][0]["id"], "My layout")
    assert [s["title"] for s in saved["sections"]] == ["Online Classroom", "Module Introduction", "Before Class",
                                                      "Class Materials", "After Class"]
    assert next(s for s in saved["sections"] if s["title"] == "Class Materials")["session_overview"]
    path = tmp_path / "patterns.json"
    o.save_patterns(path, [saved])
    assert o.load_patterns(path) == [saved]
    path.write_text('[{"name": "broken", "sections": []}, 5]', encoding="utf-8")
    assert o.load_patterns(path) == [] and o.load_patterns(tmp_path / "missing.json") == []
    names = [p["name"] for p in o.pattern_choices(source_course(), target_course(), [saved])]
    assert names[0] == "My layout" and "EA105 workshop layout" in names and "Introduction, then content" in names


def test_applying_a_pattern_to_one_module_keeps_its_other_children():
    plan = o.build_outline_plan(source_course(), target_course(), "mirror")
    week = plan["outline"][0]
    o.apply_pattern(plan, week["id"], o.simple_pattern())
    assert titles(week["children"])[0] == "Module Introduction"
    assert "Readings" in titles(week["children"]) and not o.unplaced(plan)


PHASED = ('<h1>Week 1</h1><h2>Before the workshop</h2><p>Read the chapter carefully.</p>'
          '<h2>After the workshop</h2><p>Write a short reflection.</p>')


def test_labelled_pages_split_into_parts_and_can_be_kept_whole():
    source = source_course()
    source["modules"][0]["children"][0]["html"] = PHASED
    plan = o.build_outline_plan(source, target_course(), "pattern", o.ea105_pattern())
    parts = [r for r in plan["items"].values() if r["source_id"] == "11"]
    assert [r["id"] for r in parts] == ["11#1", "11#2"]
    week = plan["outline"][0]
    before, after = week["children"][2], week["children"][4]
    assert "·11#1" in titles(before["children"]) and "·11#2" in titles(after["children"])
    assert errors(plan) == []
    o.join_page(plan, "11")
    assert "11" in plan["items"] and "11#1" not in plan["items"] and plan["unsplit"] == ["11"]
    assert [r["id"] for r in o.unplaced(plan)] == ["11"]


def test_duplicate_links_to_the_same_activity_block():
    source = source_course()
    source["modules"][0]["children"].append({"Id": 17, "Type": 1, "Title": "Essay again", "ActivityType": 3, "ToolItemId": 70})
    plan = o.build_outline_plan(source, target_course(), "mirror")
    assert any("same activity" in e for e in errors(plan))
    o.skip(plan, ["17"])
    assert errors(plan) == []


def test_applied_plans_cannot_change():
    plan = o.build_outline_plan(source_course(), target_course(), "mirror")
    plan["run_signature"] = "x"
    with pytest.raises(ValueError, match="already been created"):
        o.add_module(plan, None, "More")


def test_counts_and_preview():
    plan = o.build_outline_plan(source_course(), target_course(), "mirror")
    assert o.counts(plan) == {"modules": 2, "pages": 1, "files and links": 2, "activities": 1, "skipped": 0}
    text = o.preview_text(plan)
    assert "Week 1" in text and "  - Read chapter 2 [Page]" in text and "Readings" in text


def test_activities_already_in_the_destination_block_unless_identical():
    source = source_course()
    target = target_course()
    target["assignments"] = [{**copy.deepcopy(source["assignments"][0]), "Id": 900, "CustomInstructions": {"Html": "<p>Other</p>"}}]
    plan = o.build_outline_plan(source, target, "mirror")
    assert any("already exists in the destination" in e for e in errors(plan))
    plan["target"]["assignments"][0]["CustomInstructions"] = {"Html": "<p>Write it.</p>"}
    assert errors(plan) == [] and any("identical; that copy will be used" in n for n in o.validate_outline(plan)[1])


def test_two_activities_with_one_name_block():
    source = source_course()
    source["modules"][0]["children"].append({"Id": 18, "Type": 1, "Title": "Essay (copy)", "ActivityType": 3, "ToolItemId": 71})
    source["assignments"].append({**copy.deepcopy(source["assignments"][0]), "Id": 71})
    plan = o.build_outline_plan(source, target_course(), "mirror")
    assert any("Two assignments are called" in e for e in errors(plan))


def test_a_new_module_can_be_merged_into_the_existing_one():
    shell = [{"Id": 500, "Type": 0, "Title": "Week 1", "children": [{"Id": 501, "Type": 0, "Title": "Readings", "children": []}]}]
    plan = o.build_outline_plan(source_course(), target_course(shell), "mirror")
    clash = next(i for i in o.check(plan) if i.get("fix") == "use_existing")
    o.use_existing(plan, clash["node"])
    week = plan["outline"][0]
    assert week["existing_id"] == "500" and week["children"][-1]["existing_id"] == "501"
    assert errors(plan) == []
    with pytest.raises(ValueError): o.use_existing(plan, week["id"])


def test_cut_off_titles_are_repaired_from_the_page_heading_and_items_can_be_renamed():
    source = source_course()
    page = source["modules"][0]["children"][0]
    page["Title"] = "BLENDED PART 1: MENTAL HEALTH BLENDED WORKSHOP INS..."
    page["html"] = "<h1>Mental Health</h1><h2>Blended Part 1: Mental Health</h2><strong>Blended Workshop Instructions</strong><p>Text</p>"
    plan = o.build_outline_plan(source, target_course(), "mirror")
    row = plan["items"]["11"]
    assert row["title"] == "Blended Part 1: Mental Health" and row["repaired_from"].startswith("BLENDED")
    assert any("cut off in the source" in n for n in o.validate_outline(plan)[1])
    node = next(n for n, _, _ in o.walk(plan["outline"]) if n.get("item") == "11")
    o.rename(plan, node["id"], "Part 1 · Mental Health")
    assert row["title"] == "Part 1 · Mental Health"
    essay = next(n for n, _, _ in o.walk(plan["outline"]) if n.get("item") == "13")
    with pytest.raises(ValueError, match="Activities keep"): o.rename(plan, essay["id"], "Other")
    assert o.repaired_title({"Title": "Short title", "html": "<h1>Short title</h1>"}) is None    # not cut off
    assert o.repaired_title({"Title": "No match he...", "html": "<h1>Different</h1>"}) is None
