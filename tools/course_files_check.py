"""Copy course files server-side with Copy Components and check where they land.

    python tools/course_files_check.py SOURCE_ID TARGET_ID "path/one.pdf" "img/two.png" ...

Copies exactly those course files (paths from the course root) from SOURCE_ID into
TARGET_ID, waits until Brightspace has finished, then reports for each file whether it
arrived at the same path with the same size, and any new files Brightspace made
instead (for example a renamed duplicate). Uses the app's saved Brightspace session.
"""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import SESSION_FILE
from playwright.async_api import async_playwright
from transfer_api import TransferAPI
from transfer_components import copy_components, file_selection

HOST = "https://learn.okanagancollege.ca"


async def listing(api, path=""):
    data = await api._json(f"/d2l/api/lp/1.49/{api.course_id}/managefiles/?path={quote(path)}")
    found = []
    for entry in data.get("Objects", []):
        full = f"{path}/{entry['Name']}" if path else entry["Name"]
        found += await listing(api, full) if entry.get("FileSystemObjectType") == 1 else [full]
    return found


async def fingerprint(context, root, path, small=5_000_000):
    url = HOST + root + quote(path)
    head = await context.request.head(url, max_redirects=0)
    if head.status != 200: return None
    size = int(head.headers.get("content-length", 0))
    if size > small: return (size, "large")
    body = await (await context.request.get(url, max_redirects=0)).body()
    return (size, hashlib.sha256(body).hexdigest()[:12])


async def main(source_id, target_id, paths):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=SESSION_FILE)
        page = await context.new_page()
        await page.goto(HOST + "/d2l/home", wait_until="domcontentloaded")
        source, target = TransferAPI(page, source_id), TransferAPI(page, target_id, source_id=source_id, writable=True)
        details = await target._json(f"/d2l/api/lp/1.49/courses/{target_id}")
        if "sandbox" not in details["Name"].lower(): raise SystemExit(f"{details['Name']} isn't a sandbox; this test only runs in sandboxes.")
        roots = {"source": await source.course_root(), "target": await target.course_root()}
        originals = {path: await fingerprint(context, roots["source"], path) for path in paths}
        before_files = set(await listing(target))
        before = {path: await fingerprint(context, roots["target"], path) for path in paths}
        print("Source files:", originals)
        print("Already in the destination:", {k: v for k, v in before.items() if v})
        selection = file_selection(paths)
        log = lambda message, level="info": print(f"[{level}] {message}", flush=True)
        started = time.time()
        await copy_components(page, HOST, target_id, source_id, selection, log)
        waiting = [path for path in paths if not before[path]]
        while waiting and time.time() - started < 900:
            await asyncio.sleep(10)
            waiting = [path for path in waiting if await fingerprint(context, roots["target"], path) != originals[path]]
            print(f"[info] {len(waiting)} file(s) still copying… ({int(time.time() - started)} s)", flush=True)
        await asyncio.sleep(10)
        after_files = set(await listing(target))
        result = {"seconds": int(time.time() - started), "files": {}, "new_paths": sorted(after_files - before_files)}
        for path in paths:
            now = await fingerprint(context, roots["target"], path)
            same = now == originals[path]
            result["files"][path] = {"source": originals[path], "before": before[path], "after": now, "same_as_source": same}
            print(("PASS " if same else "FAIL ") + f"{path}: arrived at the same path, same size/content ({now})")
        print("New paths in the destination:", result["new_paths"])
        await page.goto(f"{HOST}/d2l/le/conversion/copy/{target_id}/History?ou={target_id}", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        history = await page.evaluate("() => document.body.innerText")
        start = history.find("Copy Course Components")
        result["history_top"] = history[start:start + 400] if start >= 0 else history[:600]
        print("History (newest first):\n" + result["history_top"])
        out = ROOT / "transfer-investigation" / "course-files-check.json"
        out.write_text(json.dumps(result, indent=1), encoding="utf-8")
        print("Saved", out)
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 4: raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
