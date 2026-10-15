import asyncio
import copy
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transfer_plan import (SECTIONS, build_plan, category_name, classify, digest, native_required,
                           refresh_items, rubric_signature, validate_plan, validate_recipe, workshop_recipe)
from transfer_files import FileBundle, render_template, remove_sections
from transfer_runner import PlanLock, TransferRunner, load_plan, save_plan
from transfer_api import TransferAPI
from bs4 import BeautifulSoup


def course(id_="1", modules=None):
    return {"id": id_, "name": "Sandbox " + id_, "root": f"/content/enforced/{id_}-sandbox/",
            "modules": modules or [], "assignments": [], "categories": [], "grades": [],
            "setup": {"GradingSystem": "Weighted"}, "versions": ["1.99"]}


def recipe():
    r = workshop_recipe()
    r["workshops"] = [{"title": "Workshop", "hours": 6, "source_titles": ["Workshop"], "outcomes": ["Learn"], "introduction": "Intro", "session": "Session"}]
    return r


def sample_plan():
    source = course(modules=[{"Id": 10, "Type": 0, "Title": "Workshop", "children": [
        {"Id": 11, "Type": 1, "Title": "Slides", "ActivityType": 1, "TopicType": 1, "Url": "/content/enforced/1-sandbox/slides.pdf"}]}])
    plan = build_plan(source, course("2"), recipe(), {"introduction": {"html": "template"}, "session": {"html": "template"}})
    plan["workshops"][0]["mapping_reviewed"] = True
    plan["workshops"][0]["items"][0]["reviewed"] = True
    plan["review_acknowledged"] = True
    return plan


def test_source_target_cannot_be_same():
    with pytest.raises(ValueError, match="different"):
        build_plan(course(), course(), recipe())


def test_category_names_use_supported_brightspace_names():
    from transfer_plan import grade_categories
    r = recipe()
    attendance = grade_categories(r['gradebook'])[1]
    assert attendance['label'] == 'Attendance/Participation' and attendance['name'] == 'Attendance and Participation'
    assert category_name({}, 'Attendance/Participation') == 'Attendance and Participation'
    validate_recipe(r)
    r['gradebook']['categories'][1]['name'] = 'Attendance/Participation'
    with pytest.raises(ValueError, match='supported characters'): validate_recipe(r)


def test_assignment_aliases_cannot_create_duplicate_assignments():
    source = course(modules=[{'Id': 10, 'Type': 0, 'Title': 'Workshop', 'children': [
        {'Id': ident, 'Type': 1, 'Title': 'Assignment', 'ActivityType': 3, 'ToolItemId': 20}
        for ident in (11, 12)]}])
    source['assignments'] = [{'Id': 20, 'Name': 'Assignment', 'Assessment': {'ScoreDenominator': 10}}]
    plan = build_plan(source, course('2'), recipe(), {'introduction': {}, 'session': {}})
    plan['workshops'][0]['mapping_reviewed'] = True; plan['review_acknowledged'] = True
    assert any('already included' in e for e in validate_plan(plan)[0])


def test_ea105_placement_rules():
    r = recipe()
    # The source folder wins over everything else.
    assert classify({"Title": "Read after class", "ActivityType": 1}, ["Workshop", "Before Class"], r)[:2] == ("Before Class", "deterministic")
    # Titles that say before / after.
    assert classify({"Title": "Read before class", "ActivityType": 1, "Url": "/a.pdf"}, ["Workshop"], r)[:2] == ("Before Class", "deterministic")
    assert classify({"Title": "Post workshop reflection", "ActivityType": 1, "Url": "/a.html"}, ["Workshop"], r)[:2] == ("After Class", "deterministic")
    # Assignments go to After Class; files, links, videos and everything else to Class Materials.
    assert classify({"Title": "Anything", "ActivityType": 3}, ["Workshop"], r)[:2] == ("After Class", "deterministic")
    assert classify({"Title": "Slides", "ActivityType": 1, "TopicType": 1, "Url": "/s.pptx"}, ["Workshop"], r)[:2] == ("Class Materials", "deterministic")
    assert classify({"Title": "Video", "ActivityType": 2, "TopicType": 3, "Url": "https://youtu.be/x"}, ["Workshop"], r)[0] == "Class Materials"
    assert classify({"Title": "Scenarios", "ActivityType": 1, "Url": "/s.html"}, ["Workshop"], r)[:2] == ("Class Materials", "deterministic")
    assert classify({"Title": "Scenarios", "ActivityType": 1, "Url": "/s.html"}, ["Workshop"], r)[2].startswith("Rule:")


def test_without_the_default_rule_unmatched_items_need_review():
    r = recipe(); r["default_is_rule"] = False
    assert classify({"Title": "Scenarios", "ActivityType": 1, "Url": "/s.html"}, ["Workshop"], r)[1] == "unresolved"


def test_inferred_review_and_global_ack_are_blocking():
    plan = sample_plan()
    row = plan["workshops"][0]["items"][0]
    row["reviewed"] = False
    assert any("Slides" in error for error in validate_plan(plan)[0])
    row.update(section="Skip", reviewed=True)
    assert not validate_plan(plan)[0]
    plan["review_acknowledged"] = False
    assert any("Acknowledge" in error for error in validate_plan(plan)[0])


def test_source_integrity_and_target_collision_block_before_writes():
    plan = sample_plan()
    plan["target"]["modules"] = [{"Id": 99, "Title": "Workshop", "Type": 0}]
    assert any("already has" in e for e in validate_plan(plan)[0])
    plan["source"]["modules"][0]["Title"] = "Changed"
    assert any("edited" in e for e in validate_plan(plan)[0])


def test_rubric_ids_never_reused_and_missing_api_needs_native_copy():
    original = {"Assessment": {"Rubrics": [{"RubricId": 10, "Name": "R", "CriteriaGroups": [{"Id": 100, "Name": "Part 1"}]}]}}
    copied = copy.deepcopy(original)
    copied["Assessment"]["Rubrics"][0]["RubricId"] = 22
    copied["Assessment"]["Rubrics"][0]["CriteriaGroups"][0]["Id"] = 200
    assert rubric_signature(original) == rubric_signature(copied)
    assert native_required(original, ["1.99"])


def test_correcting_source_mapping_invalidates_review():
    plan = sample_plan(); w = plan["workshops"][0]
    w["source_module_ids"] = []
    refresh_items(plan, w)
    assert w["items"] == [] and not plan["review_acknowledged"]
    assert validate_plan(plan)[0]


def test_unknown_module_and_modified_item_are_rejected():
    plan = sample_plan(); w = plan["workshops"][0]
    w["source_module_ids"] = ["999"]
    assert any("Unknown" in e for e in validate_plan(plan)[0])
    w["items"][0]["source"] = {"Id": 11, "Title": "Replacement"}
    assert any("differs" in e for e in validate_plan(plan)[0])


TEMPLATE = '''<!doctype html><html><head><title>Template</title><link rel="stylesheet" href="/shared/style.css"></head><body><div class="courseware-layouts-content-wrapper"><div class="courseware-headers-hero"><h1>Old title</h1></div><h2>Introduction</h2><p>Example</p><p><strong>In this module, you will explore:</strong></p><ul><li>Old content</li></ul><div class="d2l-element"><div class="card-body"><div class="d2l-callout-title">Learning Outcomes</div><div><ul><li>Example outcome</li></ul></div></div></div><h2>Completion Check</h2><p>Remove</p><h2>Engagement Activities</h2><p>Remove</p></div><footer>Brand</footer></body></html>'''


def test_template_preserves_style_and_replaces_example_content():
    r = recipe(); w = r["workshops"][0]
    intro = render_template(TEMPLATE, "introduction", w, r)
    assert '/shared/style.css' in intro and "card-body" in intro and "Brand" in intro
    assert "In this module" not in intro and "Example outcome" not in intro
    assert "<li>Learn</li>" in intro
    session = render_template(TEMPLATE, "session", w, r)
    assert "Completion Check" not in session and "Engagement Activities" not in session and "Example" not in session
    assert "Session plan" in session and "Brand" in session


def test_section_removal_is_bounded_and_handles_paragraph_heading():
    soup = BeautifulSoup('<h2>Keep</h2><p>Text</p><h2>Completion Check</h2><p>Remove</p><h2>Next</h2><p>Retain</p>', "html.parser")
    remove_sections(soup, ["Completion Check"])
    assert "Remove" not in str(soup) and "Retain" in str(soup)


@pytest.mark.asyncio
async def test_recursive_html_css_assets_and_cycles_are_target_owned():
    host = "https://learn.okanagancollege.ca"
    blobs = {
        host + "/content/enforced/1-source/a.css": (b'@import "b.css"; .hero{background:url("images/banner.png")}', "text/css"),
        host + "/content/enforced/1-source/b.css": (b'@import "a.css";', "text/css"),
        host + "/content/enforced/1-source/images/banner.png": (b'png', "image/png"),
        host + "/content/enforced/1-source/notes.pdf": (b'%PDF-test', "application/pdf"),
    }
    calls = []
    async def download(url): calls.append(url); return blobs[url]
    bundle = FileBundle(download, "learn.okanagancollege.ca", "/content/enforced/2-target/", "transfer", ["1"])
    result = await bundle.page('<link href="a.css"><a href="notes.pdf#page=2">Notes</a><img srcset="images/banner.png 1x, images/banner.png 2x"><style>.x{background:url(images/banner.png)}</style>', host + "/content/enforced/1-source/index.html")
    assert len(calls) == 4 and len(bundle.files) == 4
    assert "1-source" not in result and "#page=2" in result
    assert "/content/enforced/2-target/transfer/" in result
    for f in bundle.files.values():
        if f["type"] == "text/css": assert b'1-source' not in f["body"] and b'2-target' in f["body"]


@pytest.mark.asyncio
async def test_activity_links_block_instead_of_leaking_source_course():
    async def download(url): raise AssertionError("No downloads")
    bundle = FileBundle(download, "learn.okanagancollege.ca", "/content/enforced/2-target/", "transfer", ["1"])
    with pytest.raises(ValueError, match="Cross-course"):
        await bundle.page('<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&type=quiz">Quiz</a>', "https://learn.okanagancollege.ca/content/enforced/1-source/index.html")


def test_absolute_dot_segments_normalized_within_course_only():
    bundle = FileBundle(None, "learn.okanagancollege.ca", "/content/enforced/2-target/", "transfer", ["1"])
    assert bundle.split_course_file("https://learn.okanagancollege.ca/content/enforced/1-source/Content/../notes%20one.pdf") == "https://learn.okanagancollege.ca/content/enforced/1-source/notes%20one.pdf"
    with pytest.raises(ValueError): bundle.split_course_file("https://learn.okanagancollege.ca/content/enforced/1-source/../../secret")


@pytest.mark.asyncio
async def test_source_api_rejects_every_write_without_browser_call():
    class Page:
        url = "https://learn.okanagancollege.ca/d2l/home"
        async def evaluate(self, *a): raise AssertionError("No browser call allowed")
    api = TransferAPI(Page(), "1")
    with pytest.raises(PermissionError): await api._json("/d2l/api/le/1.82/1/content/root/", "POST", {})
    with pytest.raises(PermissionError): await api.upload_file("a", "b", b'bytes', "text/plain")
    with pytest.raises(ValueError): TransferAPI(Page(), "1", source_id="1", writable=True)
    target = TransferAPI(Page(), "2", source_id="1", writable=True)
    with pytest.raises(PermissionError): await target._json("/d2l/api/le/1.82/1/content/root/", "POST", {})


@pytest.mark.asyncio
async def test_journal_reconciles_lost_response_and_never_duplicates(tmp_path):
    plan = sample_plan()
    class API:
        base = "https://learn.okanagancollege.ca"
        course_id = "2"
        async def download(self, url): raise AssertionError("No downloads")
    runner = TransferRunner(API(), API(), plan, tmp_path / 'plan.json')
    calls = []
    async def write(): calls.append("write"); raise RuntimeError("lost response")
    async def found(): return {"Id": 10}
    with pytest.raises(RuntimeError): await runner.operation("module", write, found)
    assert load_plan(tmp_path / 'plan.json')["journal"]["module"]["state"] == "pending"
    assert await runner.operation("module", write, found) == {"Id": 10}
    assert calls == ["write"]
    async def missing(): return None
    with pytest.raises(RuntimeError, match="disappeared"):
        await runner.operation("module", write, missing)
    runner.journal["unknown"] = {"state": "pending"}
    with pytest.raises(RuntimeError, match="Uncertain"):
        await runner.operation("unknown", write, missing)
    assert calls == ["write"]


def test_plan_lock_prevents_concurrent_run(tmp_path):
    with PlanLock(tmp_path / 'plan.json'):
        with pytest.raises(RuntimeError, match="already running"):
            with PlanLock(tmp_path / 'plan.json'): pass


@pytest.mark.asyncio
async def test_post_identifier_survives_failed_verification_read(tmp_path):
    plan = sample_plan()
    class API:
        base = 'https://learn.okanagancollege.ca'
        course_id = '2'
        async def download(self, url): raise AssertionError('No downloads')
    runner = TransferRunner(API(), API(), plan, tmp_path / 'plan.json')
    writes = []
    async def create(): writes.append(1); return {'Id': 123}
    async def failed_read(): raise RuntimeError('Read temporarily unavailable')
    with pytest.raises(RuntimeError, match='temporarily'):
        await runner.operation('root', create, failed_read)
    saved = load_plan(tmp_path / 'plan.json')['journal']['root']
    assert saved == {'state': 'pending', 'result': {'Id': 123}}
    async def recovered(): return {'Id': 123}
    assert await runner.operation('root', create, recovered) == {'Id': 123}
    assert writes == [1]


@pytest.mark.asyncio
async def test_assignment_quicklinks_use_exact_mapping_and_other_links_block():
    from transfer_files import reference_key
    base = 'https://learn.okanagancollege.ca'
    source = base + '/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&type=dropbox&rcode=abc'
    bundle = FileBundle(None, 'learn.okanagancollege.ca', '/content/enforced/2-target/', 'transfer', ['1'])
    bundle.activity_map[reference_key(source)] = base + '/bpa-transfer-assignment/42/'
    rewritten = await bundle.page('<a href="/d2l/common/dialogs/quickLink/quickLink.d2l?type=Dropbox&amp;rcode=ABC&amp;ou=1">Assignment</a>', base)
    assert '/bpa-transfer-assignment/42/' in rewritten and 'ou=1' not in rewritten
    with pytest.raises(ValueError, match='Cross-course'):
        await bundle.page('<a href="' + source.replace('abc','another') + '">Other</a>', base)


@pytest.mark.asyncio
async def test_native_picker_ignores_accessibility_copy_and_refuses_real_duplicates():
    from playwright.async_api import async_playwright
    from content_checker import ContentChecker
    async with async_playwright() as p:
        if not Path(p.chromium.executable_path).exists(): pytest.skip('Local Chromium is not installed')
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''<style>.d2l-offscreen{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(1px,1px,1px,1px)}</style>
                <a class="d2l-offscreen"><span class="d2l-offscreen">Workshop Assignment</span></a>
                <div onclick="window.selected=true">Workshop Assignment</div>''')
            checker = object.__new__(ContentChecker)
            assert await checker._click_visible_exact_text(page, 'Workshop Assignment')
            assert await page.evaluate('window.selected') is True
            await page.set_content('<button onclick="window.selected=true">Same title</button><button onclick="window.selected=true">Same title</button>')
            await page.evaluate('window.selected=false')
            assert not await checker._click_visible_exact_text(page, 'Same title')
            assert await page.evaluate('!!window.selected') is False
        finally:
            await browser.close()
