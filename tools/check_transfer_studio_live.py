"""Live Studio verification, confined to Nick's two sandboxes.

Default: scan both courses read-only. --create: make one small hidden verification
module and placeholder page in 25707 through the real browser Studio workflow.
Uses the app's saved login; never prints or changes credentials/session storage.
"""
import argparse
import asyncio
import copy
import json
import sys
import threading
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from config import SESSION_FILE
from transfer_studio import TransferStudio
from transfer_outline_runner import apply_outline, scan_courses
from transfer_api import TransferAPI
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright, expect

HOST = "https://learn.okanagancollege.ca"
SOURCE, TARGET = "25708", "25707"
DATA = ROOT / "transfer-investigation" / "studio-live"


async def with_saved_session(action):
    async with async_playwright() as pw:
        try: browser = await pw.chromium.launch(channel="msedge", headless=True)
        except Exception: browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(storage_state=SESSION_FILE)
            page = await context.new_page()
            await page.goto(HOST + "/d2l/home", wait_until="domcontentloaded")
            if not page.url.startswith(HOST + "/d2l/"): raise RuntimeError("The saved Brightspace login has expired. Sign in through the app.")
            return await action(page)
        finally: await browser.close()


def main(create):
    workers = []
    def scan(request):
        if (request["source_id"], request["target_id"]) != (SOURCE, TARGET): raise ValueError("Live checks are restricted to Nick's two sandboxes.")
        def run():
            try:
                inventories = asyncio.run(with_saved_session(lambda page: scan_courses(page, SOURCE, TARGET, studio.log)))
                if any("sandbox" not in inventory["name"].casefold() for inventory in inventories): raise ValueError("Both live courses must be named Sandbox.")
                studio.finish_scan(inventories)
            except Exception as exc: studio.finish_scan(error=str(exc))
        worker = threading.Thread(target=run, daemon=True); workers.append(worker); worker.start()
    def apply(request):
        plan = request["plan"]
        if (plan["source"]["id"], plan["target"]["id"]) != (SOURCE, TARGET) or "sandbox" not in plan["target"]["name"].casefold(): raise ValueError("Only the destination sandbox is writable in this check.")
        async def action(page):
            details = await TransferAPI(page, TARGET)._json(f"/d2l/api/lp/1.49/courses/{TARGET}")
            if "sandbox" not in details["Name"].casefold(): raise ValueError("The destination is not a Sandbox.")
            return await apply_outline(page, plan, request["path"], TARGET, True, studio.run_log, studio.stop_event)
        def run():
            try: studio.finish_create(asyncio.run(with_saved_session(action)))
            except Exception as exc: studio.finish_create(error=str(exc))
        worker = threading.Thread(target=run, daemon=True); workers.append(worker); worker.start()
    studio = TransferStudio(DATA, scan, request_create=apply)
    try:
        with sync_playwright() as pw:
            try: browser = pw.chromium.launch(channel="chrome", headless=True)
            except Exception: browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(studio.start())
            page.locator("#source-url").fill(HOST + "/d2l/home/" + SOURCE)
            page.locator("#target-url").fill(HOST + "/d2l/home/" + TARGET)
            page.locator("input[name=start][value=empty]").check()
            page.locator("#scan").click()
            try: expect(page.locator("#arrange")).to_be_visible(timeout=240000)
            except Exception: raise RuntimeError(studio.error or "The live scan did not finish.")
            inventory_counts = {"source_items": len(studio.plan["items"]), "target_modules": len(studio.plan["target"]["modules"])}
            if create:
                title = "Transfer Studio verification " + uuid4().hex[:8]
                page.locator("#add-module").click()
                page.locator("#dialog-text").fill(title)
                page.locator("#edit-form").get_by_role("button", name="Save", exact=True).click()
                expect(page.locator("#destination-tree summary")).to_have_count(1)
                # Leave all real source content out; only the verification page is created.
                page.locator("#source-tree summary").first.click(button="right")
                page.get_by_role("menuitem", name="Select all visible items", exact=True).click()
                page.locator("#selection-actions").click()
                page.get_by_role("menuitem", name="Leave out of transfer", exact=True).click()
                expect(page.locator("#toast-message")).to_contain_text("Left out")
                page.locator("#destination-tree summary").click(button="right")
                page.get_by_role("menuitem", name="Add a placeholder page…", exact=True).click()
                page.locator("#dialog-text").fill("Studio verification page")
                page.locator("#edit-form").get_by_role("button", name="Save", exact=True).click()
                expect(page.locator("#destination-tree .row")).to_have_count(1)
                page.locator("#workflow-next").click()
                expect(page.locator("#styling")).to_be_visible()
                expect(page.locator("#preview-summary")).to_contain_text("Generated page")
                page.locator("#workflow-next").click()
                expect(page.locator("#review")).to_be_visible()
                page.locator("#confirm-create").check()
                page.locator("#workflow-next").click()
                expect(page.locator("#create")).to_be_visible()
                try: expect(page.locator("#run-title")).to_contain_text("Transfer created and checked", timeout=240000)
                except Exception: raise RuntimeError(studio.error or studio.plan.get("report") or "Live creation did not finish.")
                page.screenshot(path=str(DATA / "live-create.png"), full_page=True)
                print(json.dumps({"result": "passed", **inventory_counts, "created_hidden_module": title,
                    "destination": TARGET, "plan": str(studio.path), "checks": studio.plan["validation"]}))
            else:
                print(json.dumps({"result": "passed", "mode": "read-only scan", **inventory_counts, "plan": str(studio.path)}))
            browser.close()
    finally:
        studio.stop()
        for worker in workers: worker.join(timeout=5)
        studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true")
    main(parser.parse_args().create)
