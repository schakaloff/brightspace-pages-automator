"""Transfer one source module into a destination with the general Structured Transfer.

Uses the app's saved Brightspace session (sign in through the app first).

    python tools/transfer_one_module.py SOURCE_ID TARGET_ID "Module title"
        Scans both courses (read only), plans the module and prints the plan.

    python tools/transfer_one_module.py SOURCE_ID TARGET_ID "Module title" --create
        Plans it again and creates it in TARGET_ID (hidden), then prints the report.

Options: --layout ea105|none (default ea105), --styling preserve|strip_bpa|strip_all
(default strip_bpa). The plan is saved in transfer-investigation/one-module-plan.json
and can be resumed by running the same --create command again.
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import SESSION_FILE
from playwright.async_api import async_playwright
import transfer_outline as o
from transfer_outline_runner import apply_outline, scan_courses
from transfer_plan import norm
from transfer_runner import load_plan, save_plan

HOST = "https://learn.okanagancollege.ca"
PLAN = ROOT / "transfer-investigation" / "one-module-plan.json"


def plan_for(source, target, title, layout, styling):
    module = [m for m in source["modules"] if m.get("Type") == 0 and norm(m["Title"]) == norm(title)]
    if len(module) != 1: raise SystemExit(f"{len(module)} source modules are called {title!r}.")
    plan = o.build_outline_plan(source, target, "empty")
    node = o.add_module(plan, None, module[0]["Title"])
    o.set_sources(plan, node["id"], [str(module[0]["Id"])])
    if layout == "ea105": o.apply_pattern(plan, node["id"], o.ea105_pattern())
    o.accept_suggestions(plan, [r["id"] for r in o.unplaced(plan) if r["top"] == str(module[0]["Id"])])
    o.skip(plan, [r["id"] for r in o.unplaced(plan)])  # everything from other modules stays behind
    plan["styling"]["mode"] = styling
    return plan


async def main(source_id, target_id, title, create, layout, styling):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=SESSION_FILE)
        page = await context.new_page()
        await page.goto(HOST + "/d2l/home", wait_until="domcontentloaded")
        log = lambda message, level="info": print(f"[{level}] {message}", flush=True)
        if create and PLAN.exists() and load_plan(PLAN).get("run_signature"):
            plan = load_plan(PLAN)  # resume the plan that was already started
        else:
            source, target = await scan_courses(page, source_id, target_id, log)
            plan = plan_for(source, target, title, layout, styling)
            PLAN.parent.mkdir(exist_ok=True); save_plan(plan, PLAN)
        print(o.preview_text(plan))
        errors, _ = o.validate_outline(plan)
        if create and not errors:
            result = await apply_outline(page, plan, PLAN, target_id, True, log)
            print("\n" + result["report"])
        elif create:
            print("\nNot created: fix the items listed above first.")
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_id"); parser.add_argument("target_id"); parser.add_argument("module")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--layout", choices=("ea105", "none"), default="ea105")
    parser.add_argument("--styling", choices=("preserve", "strip_bpa", "strip_all"), default="strip_bpa")
    args = parser.parse_args()
    asyncio.run(main(args.source_id, args.target_id, args.module, args.create, args.layout, args.styling))
