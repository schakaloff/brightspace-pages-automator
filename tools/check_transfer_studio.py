"""Offline browser QA using recorded inventories; never connects to Brightspace.

Run: .venv/Scripts/python.exe tools/check_transfer_studio.py [output-directory]
Uses installed Chrome, then Edge. Saves desktop/mobile screenshots and checks
the real HTTP frontend, navigation, validation, all starts, and accessibility.
"""
import copy
import json
import sys
import tempfile
import threading
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright, expect
from test_transfer_structure import source_course
from test_transfer_outline import target_course
from transfer_studio import TransferStudio
from transfer_runner import load_plan
from test_transfer_structure import offline_courses
from test_transfer_outline_runner import fake_components, fake_link, PA_HTML
from transfer_outline_runner import OutlineRunner
from transfer_outline import source_of
import transfer_components
import pytest


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "transfer-investigation" / "studio-ui"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="studio-qa-") as data_dir:
        source, target = offline_courses()
        source.state["modules"][0]["children"][0]["html"] = PA_HTML
        source.files["/content/enforced/1-src/reading.html"] = PA_HTML.encode()
        target.page, target.source_state, target.source_files = target, source.state, source.files
        inventories = (source.state, target.state)
        copies = []
        patches = pytest.MonkeyPatch()
        copier, waiter = fake_components(copies)
        patches.setattr(transfer_components, "copy_components", copier)
        patches.setattr(transfer_components, "wait_for_copies", waiter)
        patches.setattr(OutlineRunner, "link_activity", fake_link)
        pause_once = [True]
        def create(request):
            def run():
                def log(message, level="info"):
                    studio.run_log(message, level)
                    if "Creating modules" in message and pause_once[0]:
                        pause_once[0] = False
                        studio.stop_event.wait(5)  # Let the browser exercise Stop.
                try:
                    result = asyncio.run(OutlineRunner(source, target, request["plan"], request["path"], log, studio.stop_event).run(request["target_id"], True))
                    studio.finish_create(result)
                except Exception as exc: studio.finish_create(error=str(exc))
            threading.Thread(target=run, daemon=True).start()
        studio = TransferStudio(data_dir, lambda request: studio.finish_scan(copy.deepcopy(inventories)), request_create=create)
        try:
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(channel="chrome", headless=True)
                except Exception:
                    browser = pw.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                axe = ROOT / "node_modules" / "axe-core" / "axe.min.js"
                if axe.exists(): context.add_init_script(path=str(axe))
                page = context.new_page()
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                page.goto(studio.start())
                expect(page.locator("#footer-status")).to_contain_text("Local preview")
                assert page.url == studio.origin + "/", "Launch token was not cleared"
                page.locator("#source-url").fill("https://learn.okanagancollege.ca/d2l/home/1")
                page.locator("#target-url").fill("https://learn.okanagancollege.ca/d2l/home/1")
                page.locator("#scan").click()
                expect(page.locator("#error")).to_contain_text("different courses")
                page.locator("#target-url").fill("https://learn.okanagancollege.ca/d2l/home/2")
                for start in ("mirror", "empty", "destination", "pattern"):
                    page.locator(f"input[name=start][value={start}]").check()
                    if start == "pattern": page.locator("#pattern").select_option(label="Introduction, then content")
                    page.locator("#scan").click()
                    expect(page.locator("#arrange")).to_be_visible()
                    assert studio.plan["start"] == start
                    assert page.locator("#source-tree summary").filter(has_text="Week 1").count() == 1
                    page.locator("#waiting-filter").click()
                    if start == "mirror": expect(page.locator("#source-tree")).to_contain_text("Every item has a place")
                    page.locator("#waiting-filter").click()
                    page.reload()
                    expect(page.locator("#arrange")).to_be_visible()
                    if start == "pattern": assert page.locator("#pattern").input_value() == "Introduction, then content"
                    page.locator("#back").click()

                # Exercise actual mouse/keyboard controls against the HTTP edit API.
                page.locator("input[name=start][value=mirror]").check()
                page.locator("#scan").click()
                expect(page.locator("#arrange")).to_be_visible()
                destination = page.locator("#destination-tree")
                # Use the real drop handlers to reorder units and selected groups.
                for title in ('B', 'C', 'D'):
                    page.evaluate('async (title) => await mutate("add_module", {title})', title)
                units = {n['title']: n['id'] for n in studio.plan['outline']}
                destination.locator('details').evaluate_all('(nodes) => nodes.forEach(n => n.open = false)')
                unit_d = destination.locator(f'summary[data-id="{units["D"]}"]')
                before_b = destination.locator(f':scope > .drop-slot[data-before="{units["B"]}"]')
                unit_d.drag_to(before_b)
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['Week 1', 'D', 'B', 'C'])
                page.keyboard.press('Control+z')
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['Week 1', 'B', 'C', 'D'])
                destination.locator(f'summary[data-id="{units["Week 1"]}"]').click()
                destination.locator(f'summary[data-id="{units["C"]}"]').click(modifiers=['Control'])
                destination.locator(f'summary[data-id="{units["Week 1"]}"]').drag_to(
                    destination.locator(f':scope > .drop-slot[data-before="{units["D"]}"]'))
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['B', 'Week 1', 'C', 'D'])
                page.keyboard.press('Control+z')
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['Week 1', 'B', 'C', 'D'])
                # The top edge inserts before a unit; its centre still nests inside it.
                page.locator('#clear-selection').click()
                unit_d.drag_to(destination.locator(f'summary[data-id="{units["B"]}"]'), target_position={'x': 100, 'y': 2})
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['Week 1', 'D', 'B', 'C'])
                page.keyboard.press('Control+z')
                unit_d.drag_to(destination.locator(f'summary[data-id="{units["B"]}"]'))
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['Week 1', 'B', 'C'])
                expect(destination.locator(f'details[data-id="{units["B"]}"] summary[data-id="{units["D"]}"]')).to_have_count(1)
                page.keyboard.press('Control+z')
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['Week 1', 'B', 'C', 'D'])
                page.evaluate('async (ids) => await mutate("remove", {}, ids.map(id => ({side:"destination",id})))', [units[t] for t in ('B', 'C', 'D')])
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['Week 1'])
                destination.locator('details').evaluate_all('(nodes) => nodes.forEach(n => n.open = true)')
                # Whole-unit skips stay visible even when the unit is collapsed.
                source_week = page.locator('#source-tree summary[data-id="10"]')
                source_week.locator('.disclosure').click()
                source_week.click(button="right")
                page.get_by_role("menuitem", name="Leave out of transfer", exact=True).click()
                expect(source_week.locator('.status')).to_have_text('Skipped')
                expect(destination.locator('summary')).to_have_count(0)
                assert source_week.evaluate('(e) => e.classList.contains("skipped-item") && !e.parentElement.open')
                assert source_week.locator('.node-label').evaluate('(e) => getComputedStyle(e).textDecorationLine.includes("line-through")')
                expect(page.locator('#source-tree summary[data-id="14"] .status')).to_have_text('Skipped')
                page.screenshot(path=str(output / 'desktop-skipped-unit.png'), full_page=True)
                page.keyboard.press("Control+z")
                expect(source_week.locator('.status')).to_have_count(0)
                assert not source_week.evaluate('(e) => e.classList.contains("skipped-item")')
                source_week.locator('.disclosure').click()
                page.locator('#source-tree .row[data-id="11"]').click(button="right")
                page.get_by_role("menuitem", name="Leave out of transfer", exact=True).click()
                expect(source_week.locator('.status')).to_have_text('1 of 4 skipped')
                expect(page.locator('#source-tree .row[data-id="11"] .status')).to_have_text('Skipped')
                assert not source_week.evaluate('(e) => e.classList.contains("skipped-item")')
                page.keyboard.press("Control+z")
                expect(source_week.locator('.status')).to_have_count(0)
                source_week.click(button='right')
                page.get_by_role('menuitem', name='Select all items in this folder', exact=True).click()
                page.locator('#selection-actions').click()
                page.get_by_role('menuitem', name='Leave out of transfer', exact=True).click()
                expect(source_week.locator('.status')).to_have_text('All items skipped')
                expect(destination.locator(':scope > details > summary .node-label')).to_have_text(['Week 1'])
                page.keyboard.press('Control+z')
                expect(source_week.locator('.status')).to_have_count(0)
                reading = destination.locator(".row").filter(has_text="Read chapter 2")
                reading.click(button="right")
                page.get_by_role("menuitem", name="Remove from outline", exact=True).click()
                expect(reading).to_have_count(0)
                expect(page.locator("#source-tree .row").filter(has_text="Read chapter 2")).to_contain_text("Unplaced")
                page.keyboard.press("Control+z")
                expect(reading).to_have_count(1)
                page.keyboard.press("Control+y")
                expect(reading).to_have_count(0)
                page.locator("#undo").click()
                expect(reading).to_have_count(1)

                reading.click()
                destination.locator(".row").filter(has_text="Discussion prompts").click(modifiers=["Shift"])
                expect(page.locator("#selection-count")).to_contain_text("2 selected")
                reading.click()
                prompts = destination.locator(".row").filter(has_text="Discussion prompts")
                prompts.click(modifiers=["Control"])
                expect(page.locator("#selection-count")).to_contain_text("2 selected")
                prompts.click(button="right")
                expect(page.locator("#selection-count")).to_contain_text("2 selected")
                page.get_by_role("menuitem", name="Remove from outline", exact=True).click()
                expect(reading).to_have_count(0); expect(prompts).to_have_count(0)
                page.locator("#toast-undo").click()
                expect(reading).to_have_count(1); expect(prompts).to_have_count(1)

                week = destination.locator("summary").filter(has_text="Week 1")
                week.click(button="right")
                page.get_by_role("menuitem", name="Select all items in this folder", exact=True).click()
                expect(page.locator("#selection-count")).to_contain_text("4 selected")
                page.keyboard.press("Delete")
                expect(destination.locator(".row")).to_have_count(0)
                page.locator("#undo").click()
                expect(destination.locator(".row")).to_have_count(4)

                week.click(button="right")
                page.get_by_role("menuitem", name="Add a section folder…", exact=True).click()
                page.locator("#dialog-text").fill("Practice")
                page.locator("#edit-form").get_by_role("button", name="Save", exact=True).click()
                practice = destination.locator("summary").filter(has_text="Practice")
                expect(practice).to_have_count(1)
                practice.click(button="right")
                page.get_by_role("menuitem", name="Rename…", exact=True).click()
                page.locator("#dialog-text").fill("Exercises")
                page.locator("#edit-form").get_by_role("button", name="Save", exact=True).click()
                expect(destination.locator("summary").filter(has_text="Exercises")).to_have_count(1)
                page.locator("#undo").click()
                expect(practice).to_have_count(1)

                page.locator("#clear-selection").click()
                page.locator("#area-select").click()
                a, b = reading.bounding_box(), prompts.bounding_box()
                page.mouse.move(a["x"] + 65, a["y"] + 3)
                page.mouse.down()
                page.mouse.move(b["x"] + 175, b["y"] + b["height"] - 3, steps=6)
                page.mouse.up()
                expect(page.locator("#selection-count")).to_contain_text("2 selected")
                page.keyboard.press("Delete")
                expect(reading).to_have_count(0); expect(prompts).to_have_count(0)
                page.keyboard.press("Control+z")
                expect(reading).to_have_count(1); expect(prompts).to_have_count(1)

                source_reading = page.locator("#source-tree .row").filter(has_text="Read chapter 2")
                source_reading.drag_to(practice)
                expect(practice.locator("xpath=..").locator(".row").filter(has_text="Read chapter 2")).to_have_count(1)
                source_week = page.locator("#source-tree summary").filter(has_text="Week 1")
                source_week.click(button="right")
                page.get_by_role("menuitem", name="Leave out of transfer", exact=True).click()
                expect(page.locator('#skip-unit-dialog')).to_be_visible()
                expect(page.locator('#skip-unit-details')).to_contain_text('Practice')
                page.screenshot(path=str(output / 'desktop-skip-confirmation.png'), full_page=True)
                if axe.exists():
                    result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}})")
                    assert not result['violations'], result['violations']
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Skip confirmation overflow'
                page.screenshot(path=str(output / 'mobile-skip-confirmation.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.locator('#skip-unit-cancel').click()
                expect(page.locator('#source-tree .row .status').filter(has_text='Skipped')).to_have_count(0)
                source_week.click(button='right')
                page.get_by_role('menuitem', name='Leave out of transfer', exact=True).click()
                page.locator('#skip-unit-confirm').click()
                expect(page.locator("#source-tree .row .status").filter(has_text="Skipped")).to_have_count(4)
                expect(destination.locator('summary')).to_have_count(0)
                page.locator("#undo").click()
                expect(page.locator("#source-tree .status").filter(has_text="Skipped")).to_have_count(0)
                page.reload()
                expect(page.locator("#undo")).to_be_enabled()
                # Styling, saved layouts, Review, Stop, Resume and report against
                # the real engine with FakeCourse, never a live course.
                page.locator("#destination-tree summary").filter(has_text="Week 1").click(button="right")
                page.get_by_role("menuitem", name="Save sections as a layout…", exact=True).click()
                page.locator("#dialog-text").fill("QA layout")
                page.locator("#edit-form").get_by_role("button", name="Save", exact=True).click()
                expect(page.locator("#toast-message")).to_contain_text("Saved the layout")
                page.locator("#workflow-next").click()
                expect(page.locator("#styling")).to_be_visible()
                page.locator("input[name=style-mode][value=strip_bpa]").check()
                expect(page.locator("#preview-summary")).to_contain_text("Page Automator")
                page.locator("input[name=style-mode][value=strip_bpa]").focus()
                page.keyboard.press("Control+z")
                expect(page.locator("input[name=style-mode][value=preserve]")).to_be_checked()
                page.keyboard.press("Control+y")
                expect(page.locator("input[name=style-mode][value=strip_bpa]")).to_be_checked()
                expect(page.frame_locator("#before-preview").locator(".main-container")).to_have_count(1)
                expect(page.frame_locator("#after-preview").locator(".main-container")).to_have_count(0)
                page.locator("#apply-look").check()
                expect(page.locator("#preview-summary")).to_contain_text("template")
                page.locator("#apply-look").uncheck()
                page.locator("#workflow-next").click()
                expect(page.locator("#review")).to_be_visible()
                expect(page.locator("#workflow-next")).to_be_disabled()
                page.locator("#confirm-create").check()
                page.locator("#workflow-next").click()
                expect(page.locator("#create")).to_be_visible()
                expect(page.locator("#run-log")).to_contain_text("Creating modules", timeout=15000)
                page.locator("#stop-create").click()
                expect(page.locator("#run-title")).to_contain_text("Transfer stopped", timeout=15000)
                page.locator("#confirm-resume").check()
                page.locator("#resume-create").click()
                expect(page.locator("#run-title")).to_contain_text("Transfer created and checked", timeout=20000)
                expect(page.locator("#transfer-report")).to_contain_text("COMPLETE")
                assert not source.writes and len(copies) == 1
                page.screenshot(path=str(output / "desktop-create.png"), full_page=True)
                page.reload()
                expect(page.locator("#create")).to_be_visible()
                expect(page.locator("#transfer-report")).to_contain_text("COMPLETE")
                completed_key = studio.path.stem
                page.locator("#connect-step").click()
                # Keep the established navigation check while moving through
                # the new steps after the run is finished.
                assert studio.snapshot()["run"]["status"] == "complete"
                page.locator("#target-url").fill("https://learn.okanagancollege.ca/d2l/home")
                page.locator("#scan").click()
                expect(page.locator("#target-url")).to_have_attribute("aria-invalid", "true")
                page.locator("#arrange-step").click()
                expect(page.locator("#error")).to_be_hidden()
                page.locator("#connect-step").click()
                page.locator("#open-saved").click()
                page.locator("#dialog-place").select_option(completed_key)
                page.locator("#edit-form").get_by_role("button", name="Save", exact=True).click()
                expect(page.locator("#create")).to_be_visible()
                expect(page.locator("#transfer-report")).to_contain_text("COMPLETE")
                page.locator("#connect-step").click()
                expect(page.locator("#error")).to_be_hidden()

                recorded = ROOT / "transfer-investigation" / "guided-workshop-plan.json"
                if recorded.exists():
                    saved = load_plan(recorded)
                    inventories = (saved["source"], saved["target"])
                    page.locator("#source-url").fill(f"https://learn.okanagancollege.ca/d2l/home/{inventories[0]['id']}")
                    page.locator("#target-url").fill(f"https://learn.okanagancollege.ca/d2l/home/{inventories[1]['id']}")
                page.locator("input[name=start][value=mirror]").check()
                violations = []
                for width, height, size in ((1440, 1000, "desktop"), (390, 844, "mobile")):
                    page.set_viewport_size({"width": width, "height": height})
                    for step in ("connect", "arrange"):
                        if step == "arrange":
                            page.locator("#scan").click()
                            expect(page.locator("#arrange")).to_be_visible()
                        page.evaluate("document.fonts.ready")
                        assert page.evaluate('document.fonts.check(\'16px "Hanken Grotesk"\')'), "Bundled font did not load"
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow: {size} {step}"
                        page.screenshot(path=str(output / f"{size}-{step}.png"), full_page=True)
                        if axe.exists():
                            result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}})")
                            violations.extend({"view": f"{size}-{step}", "id": v["id"], "nodes": [n["target"] for n in v["nodes"]]} for v in result["violations"])
                        if size == "desktop" and step == "arrange":
                            page.locator("#destination-tree summary").first.click(button="right")
                            page.screenshot(path=str(output / "desktop-context-menu.png"), full_page=True)
                            if axe.exists():
                                result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}})")
                                violations.extend({"view": "context-menu", "id": v["id"]} for v in result["violations"])
                            page.keyboard.press("Escape")
                            page.locator("#workflow-next").click()
                            expect(page.locator("#styling")).to_be_visible()
                            representative = next((p for p in studio.plan["items"].values() if p["kind"] == "page" and "main-container" in source_of(studio.plan, p).get("html", "")), None)
                            if representative: page.locator(f'#style-pages button[data-page="{representative["id"]}"]').click()
                            page.locator("input[name=style-mode][value=strip_bpa]").check()
                            expect(page.locator("#preview-summary")).to_contain_text("Page Automator")
                            expect(page.frame_locator("#before-preview").locator(".main-container")).to_have_count(1)
                            expect(page.frame_locator("#after-preview").locator(".main-container")).to_have_count(0)
                            expect(page.frame_locator("#after-preview").locator("h1")).to_have_count(1)
                            page.screenshot(path=str(output / "desktop-styling.png"), full_page=True)
                            page.locator("#workflow-next").click()
                            expect(page.locator("#review")).to_be_visible()
                            page.screenshot(path=str(output / "desktop-review.png"), full_page=True)
                            if axe.exists():
                                for target_view in ("styling", "review"):
                                    page.locator("#" + target_view + "-step").click()
                                    result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}})")
                                    violations.extend({"view": target_view, "id": v["id"], "nodes": [n["target"] for n in v["nodes"]]} for v in result["violations"])
                            page.locator("#arrange-step").click()
                    page.locator("#back").click()
                # Check the completed run/report at desktop and mobile widths.
                for width, height, size in ((1440,1000,"desktop"), (390,844,"mobile")):
                    page.set_viewport_size({"width": width, "height": height})
                    # Use the same public endpoint as the saved-plan dialog.
                    page.evaluate("async (key) => {const r=await fetch('/api/open',{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Request':'1'},body:JSON.stringify({key})}); const data=await r.json(); if(!r.ok)throw Error(data.error); state=data.state; renderState(); setView('create');}", completed_key)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Run/report overflow"
                    expect(page.locator("#footer-status")).to_contain_text("Created and checked")
                    page.screenshot(path=str(output / f"{size}-create.png"), full_page=True)
                    if axe.exists():
                        result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}})")
                        violations.extend({"view": size + "-create", "id": v["id"], "nodes": [n["target"] for n in v["nodes"]]} for v in result["violations"])
                assert not errors, errors
                assert all(url.startswith(studio.origin + "/") for url in requests), "External network request"
                assert not violations, violations
                browser.close()
                print(json.dumps({"result": "passed", "screenshots": str(output), "accessibility": "passed" if axe.exists() else "axe-core unavailable"}))
        finally:
            studio.close()
            patches.undo()


if __name__ == "__main__":
    main()
