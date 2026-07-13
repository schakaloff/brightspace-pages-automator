import asyncio
import re
import os
import sys

from playwright.async_api import async_playwright

from config import USERDATA_DIR, SESSION_FILE
from automator import _find_locator_any_frame
from browser import _do_ms_sso_login

KMC_URL = "https://kmc.cap2.ovp.kaltura.com/index.php/kmcng/content/entries/list"
KMC_SESSION_FILE = str(USERDATA_DIR / "kmc_session.json")
MOODLE_SESSION_FILE = str(USERDATA_DIR / "moodle_session.json")


class KalturaCategorizer:

    async def scan_moodle_course(self, moodle_course_url: str, log_fn=None) -> list[dict]:
        """Scrape all kalvidres activity pages in a Moodle course.

        Returns list of {entry_id, name, moodle_url, section_name}.
        """
        def log(msg, tag="dim"):
            if log_fn:
                log_fn(msg, tag)
            print(f"[kaltura scan] {msg}", file=sys.stderr)

        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                if os.path.exists(MOODLE_SESSION_FILE):
                    log(f"Loading Moodle session from {MOODLE_SESSION_FILE}")
                    storage = MOODLE_SESSION_FILE
                else:
                    log("No Moodle session — use 'Login to Moodle' first", "warning")
                    storage = None
                context = await browser.new_context(storage_state=storage)
                page = await context.new_page()

                log(f"Navigating to {moodle_course_url}")
                await page.goto(moodle_course_url, wait_until="networkidle", timeout=30000)
                log(f"Landed on: {page.url}")

                if "mymoodle.okanagan.bc.ca" not in page.url:
                    log(f"Not on Moodle — got redirected to {page.url[:80]} — session may have expired. Use 'Login to Moodle' first.", "error")
                    return []

                # Collect kalvidres links AND mod/page links (pages may embed Kaltura)
                link_data = await page.evaluate("""() => {
                    const map = {};
                    document.querySelectorAll('li.section, li.section.main').forEach(section => {
                        const heading = section.querySelector('.sectionname, h3, h4');
                        const sectionName = heading ? heading.textContent.trim() : '(unnamed section)';
                        section.querySelectorAll(
                            'a[href*="mod/kalvidres/view.php"], a[href*="mod/page/view.php"]'
                        ).forEach(a => {
                            if (!map[a.href]) map[a.href] = sectionName;
                        });
                    });
                    return map;
                }""")

                all_links = list(link_data.keys())
                log(f"Found {len(all_links)} candidate link(s) on course page (kalvidres + pages)")

                if not all_links:
                    title = await page.title()
                    log(f"Page title: {title!r} — check URL is a Moodle course page", "warning")
                    return []

                for i, link in enumerate(all_links, 1):
                    section_name = link_data[link]
                    log(f"[{i}/{len(all_links)}] Visiting {link}")
                    try:
                        await page.goto(link, wait_until="networkidle", timeout=20000)

                        if "mod/kalvidres" in link:
                            # Dedicated Kaltura activity — entryId in iframe src
                            iframe_src = await page.evaluate("""() => {
                                const f = document.querySelector('iframe#contentframe');
                                return f ? f.src : '';
                            }""")
                            if not iframe_src:
                                log(f"  No contentframe iframe — skipping", "warning")
                                continue
                            m = re.search(r'entryid%2F([\w_]+)', iframe_src, re.IGNORECASE)
                            if not m:
                                log(f"  No entryid in iframe src — skipping", "warning")
                                continue
                            entry_id = m.group(1)
                            title = await page.title()
                            name = re.sub(r'\s*\|\s*OCmoodle\s*$', '', title).strip()
                            log(f"  → kalvidres entry_id={entry_id}  name={name!r}", "info")
                            results.append({
                                "entry_id": entry_id,
                                "name": name,
                                "moodle_url": link,
                                "section_name": section_name,
                            })

                        else:
                            # mod/page — scan body HTML for embedded Kaltura entryIds
                            embedded = await page.evaluate("""() => {
                                const found = [];
                                // iframe src pattern: entryId=xxx or entryid/xxx
                                document.querySelectorAll('iframe[src*="kaltura"]').forEach(f => {
                                    const m = f.src.match(/entryid[%2F\/=]+([\w_]+)/i);
                                    if (m) found.push(m[1]);
                                });
                                // script-based: entryId: 'xxx' or entryId: "xxx"
                                document.querySelectorAll('script').forEach(s => {
                                    const matches = [...s.textContent.matchAll(/entryId\s*[=:]\s*['"]([^'"]+)['"]/gi)];
                                    matches.forEach(m => found.push(m[1]));
                                });
                                // also check div id="kaltura_player_NNN" — extract entryId from sibling script
                                document.querySelectorAll('[id^="kaltura_player_"]').forEach(el => {
                                    // entryId already captured by script scan above
                                });
                                return [...new Set(found)];
                            }""")

                            if not embedded:
                                log(f"  No Kaltura embeds found in page — skipping", "dim")
                                continue

                            # Prefer page h1 over browser tab title — tab title includes course prefix
                            page_name = await page.evaluate("""() => {
                                const h1 = document.querySelector(
                                    '.page-header-headings h1, .activity-name h1, h1.h2, h1'
                                );
                                return h1 ? h1.textContent.trim() : '';
                            }""")
                            if not page_name:
                                page_title = await page.title()
                                page_name = re.sub(r'\s*\|\s*OCmoodle\s*$', '', page_title).strip()
                                # strip course prefix "COURSE_CODE: " if present
                                page_name = re.sub(r'^[A-Z0-9_\-]+:\s*', '', page_name).strip()
                            log(f"  Found {len(embedded)} Kaltura embed(s) in page: {page_name!r}", "info")

                            for j, entry_id in enumerate(embedded):
                                name = page_name if len(embedded) == 1 else f"{page_name} ({j+1})"
                                log(f"  → page embed entry_id={entry_id}  name={name!r}", "info")
                                results.append({
                                    "entry_id": entry_id,
                                    "name": name,
                                    "moodle_url": link,
                                    "section_name": section_name,
                                })

                    except Exception as e:
                        log(f"  Error: {repr(e)} — skipping", "warning")
                        continue
            finally:
                await browser.close()
        return results

    async def login_to_moodle(
        self,
        moodle_url: str,
        moodle_username: str = "",
        moodle_password: str = "",
        log_fn=None,
    ) -> None:
        """Log in to Moodle via manual login (?saml=off), save session to MOODLE_SESSION_FILE.

        Replicates content_checker._scrape_moodle login flow exactly.
        If credentials provided: fully automated. Otherwise: waits for user to log in manually.
        """
        def log(msg, tag="dim"):
            if log_fn:
                log_fn(msg, tag)
            print(f"[moodle login] {msg}", file=sys.stderr)

        MANUAL_LOGIN_URL = "https://mymoodle.okanagan.bc.ca/login/index.php?saml=off"

        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=False, slow_mo=50)
        try:
            context = await browser.new_context()
            page = await context.new_page()

            log("Opening Moodle manual login…")
            try:
                await page.goto(MANUAL_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(1000)

            # "Already logged in as X — Log out?" dialog (stale SSO session)
            logout_btn = page.locator('button:has-text("Log out")')
            if await logout_btn.count() > 0:
                log("Clearing existing SSO session…")
                await logout_btn.first.click()
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)
                await page.goto(MANUAL_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)

            # loginredirect=1 — another stale-session variant
            if "loginredirect" in page.url:
                log("Clearing stale session (loginredirect)…")
                try:
                    logout_btn2 = page.locator('button[type="submit"].btn-primary')
                    if await logout_btn2.count() > 0:
                        await logout_btn2.first.click()
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                        await page.wait_for_timeout(2000)
                except Exception:
                    pass
                await page.goto(MANUAL_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)

            if moodle_username and moodle_password:
                log(f"Filling credentials for {moodle_username}…")
                try:
                    await page.evaluate("""([u, p]) => {
                        const set = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        const uEl = document.querySelector('#username');
                        const pEl = document.querySelector('#password');
                        set.call(uEl, u); uEl.dispatchEvent(new Event('input', {bubbles:true}));
                        set.call(pEl, p); pEl.dispatchEvent(new Event('input', {bubbles:true}));
                        document.querySelector('#loginbtn').click();
                    }""", [moodle_username, moodle_password])
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(2000)
                    if "login" in page.url.lower():
                        raise RuntimeError("Login failed — check Moodle credentials in Settings")
                    log("Logged in successfully.", "success")
                except RuntimeError:
                    raise
                except Exception as e:
                    raise RuntimeError(f"Login step failed: {e}")
            else:
                log("No credentials set in Settings — log in manually in the browser.", "warning")
                for i in range(120):
                    await page.wait_for_timeout(3000)
                    if "login" not in page.url.lower():
                        log("Login detected.", "success")
                        await page.wait_for_timeout(1500)
                        break
                    if i % 10 == 9:
                        log(f"Waiting for manual login… ({(i + 1) * 3}s)")
                else:
                    raise RuntimeError("Moodle login timed out after 6 minutes")

            await context.storage_state(path=MOODLE_SESSION_FILE)
            log("Moodle session saved — ready to scan.", "success")
        finally:
            try:
                await browser.close()
            except Exception:
                pass
            await p.stop()

    async def get_bs_modules(
        self,
        bs_url: str,
        bs_username: str = "",
        bs_password: str = "",
        sso_email: str = "",
        sso_password: str = "",
        log_fn=None,
    ) -> list[dict]:
        """Log in to Brightspace (same as unit_collector), then return [{id, title}] via TOC API."""
        def log(msg, tag="dim"):
            if log_fn:
                log_fn(msg, tag)
            print(f"[bs modules] {msg}", file=sys.stderr)

        from content_checker import _extract_course_id
        from browser import launch_browser, wait_for_login

        course_id = _extract_course_id(bs_url)
        if not course_id:
            raise ValueError(f"Could not extract course ID from URL: {bs_url}")
        base_url = "/".join(bs_url.split("/")[:3])
        log(f"Course ID: {course_id}  Base: {base_url}")

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(
                page, context,
                bs_username or None,
                bs_password or None,
                sso_email or None,
                sso_password or None,
            )
            nav_url = f"{base_url}/d2l/le/content/{course_id}/home"
            log(f"Navigating to {nav_url}")
            try:
                await page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            log(f"Landed on: {page.url}")
            result = await page.evaluate(
                """async (courseId) => {
                    const resp = await fetch(
                        `/d2l/api/le/1.0/${courseId}/content/toc`,
                        {credentials: 'include'}
                    );
                    if (!resp.ok) return {error: resp.status + ' ' + resp.statusText, modules: null};
                    const toc = await resp.json();
                    const modules = [];
                    function collect(arr) {
                        for (const m of (arr || [])) {
                            modules.push({id: String(m.ModuleId), title: m.Title || '(unnamed)'});
                            collect(m.Modules);
                        }
                    }
                    collect(toc.Modules || []);
                    return {error: null, modules};
                }""",
                course_id,
            )
            if result is None:
                log("API returned null — unexpected", "error")
                return []
            if result.get("error"):
                log(f"TOC API error: {result['error']}", "error")
                return []
            modules = result.get("modules") or []
            log(f"TOC returned {len(modules)} module(s)")
            return modules
        finally:
            await browser.close()
            await p.stop()

    async def _get_embed_code(self, kmc_page, entry_id: str, log_fn) -> "str | None":
        """Search KMC for entry, click it, click Share & Embed, return embed code."""
        log_fn(f"  KMC: searching {entry_id}…", "dim")
        await kmc_page.goto(KMC_URL, wait_until="networkidle", timeout=20000)

        search = kmc_page.locator("input[type='text']").first
        await search.wait_for(state="visible", timeout=10000)
        await search.click()
        await search.click(click_count=3)
        await search.type(entry_id)
        await kmc_page.keyboard.press("Enter")
        await kmc_page.wait_for_timeout(3000)

        rows = kmc_page.locator("p-table tbody tr, tr.kEntry")
        count = await rows.count()
        log_fn(f"  KMC: {count} row(s) found", "dim")
        if count == 0:
            log_fn(f"  ⚠ Entry {entry_id} not found in KMC", "warning")
            return None

        # Click entry name cell (2nd td) — first td is checkbox
        first_row = rows.first
        name_cell = first_row.locator("td").nth(1)
        if await name_cell.count() > 0:
            await name_cell.click()
        else:
            await first_row.click()
        await kmc_page.wait_for_timeout(2000)

        # Click Share & Embed link on the entry detail page
        try:
            share_link = kmc_page.locator("a:has-text('Share & Embed'), .kPreviewAndEmbedContainer a").first
            await share_link.wait_for(state="visible", timeout=8000)
            await share_link.click()
            log_fn(f"  KMC: clicked Share & Embed", "dim")
        except Exception as e:
            log_fn(f"  ⚠ Share & Embed link not found: {e}", "warning")
            return None
        await kmc_page.wait_for_timeout(2000)

        # Embed code is in textarea inside .kSection.kAlignTop (confirmed via DOM inspection)
        try:
            textarea = kmc_page.locator(".kSection.kAlignTop textarea")
            await textarea.wait_for(state="attached", timeout=10000)
            for _ in range(15):
                code = await textarea.input_value()
                if code and code.strip():
                    log_fn(f"  KMC: embed code captured ({len(code.strip())} chars)", "dim")
                    return code.strip()
                await kmc_page.wait_for_timeout(1000)
        except Exception as e:
            log_fn(f"  ⚠ Textarea read failed: {e}", "warning")

        log_fn(f"  ⚠ Embed textarea empty for {entry_id}", "warning")
        return None

    async def _create_bs_page(
        self,
        bs_page,
        base_url: str,
        course_id: str,
        module_id: str,
        title: str,
        html: str,
        log_fn,
    ) -> bool:
        """Navigate to a Brightspace module, create a new Page, paste embed HTML, save."""
        log_fn(f"  BS: opening module {module_id}…", "dim")

        # Navigate to module view (URL format verified against D2L; adjust if needed)
        module_url = f"{base_url}/d2l/le/lessons/{course_id}/units/{module_id}"
        try:
            await bs_page.goto(module_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log_fn(f"  ⚠ Navigation to module failed: {e}", "warning")
        await bs_page.wait_for_timeout(2000)

        # ── Shadow-DOM helpers ─────────────────────────────────────────────────
        _JS_DEEP_CLICK = """(selector) => {
            function deepFind(root, sel, depth) {
                if (depth === 0) return null;
                const el = root.querySelector(sel);
                if (el) return el;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot, sel, depth - 1);
                        if (f) return f;
                    }
                }
                return null;
            }
            const el = deepFind(document, selector, 10);
            if (!el) return false;
            if (el.shadowRoot) {
                const inner = el.shadowRoot.querySelector('button');
                if (inner) { inner.click(); return true; }
            }
            el.click();
            return true;
        }"""

        async def js_click(selector: str) -> bool:
            frames = [bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]
            for ctx in frames:
                try:
                    if await ctx.evaluate(_JS_DEEP_CLICK, selector):
                        return True
                except Exception:
                    pass
            return False

        # ── Wait for the Lessons page to finish rendering ─────────────────────
        # Bounded wait so we don't click "Create New" before the smart-curriculum
        # toolbar has painted. Ready when any of: create-new-btn exists, a host
        # whose text/aria-label is "Create New", or #content-header exists.
        _JS_LESSONS_READY = """() => {
            function deep(root, depth, fn) {
                if (depth === 0) return false;
                if (fn(root)) return true;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot && deep(c.shadowRoot, depth - 1, fn)) return true;
                }
                return false;
            }
            return deep(document, 10, (r) => {
                if (r.querySelector('d2l-button.create-new-btn')) return true;
                if (r.querySelector('#content-header')) return true;
                for (const el of r.querySelectorAll('d2l-button, button, [role=button]')) {
                    const t = (el.textContent || '').trim();
                    const a = el.getAttribute('aria-label') || '';
                    if (t === 'Create New' || a === 'Create New') return true;
                }
                return false;
            });
        }"""

        async def _lessons_ready() -> bool:
            frames = [bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]
            for ctx in frames:
                try:
                    if await ctx.evaluate(_JS_LESSONS_READY):
                        return True
                except Exception:
                    pass
            return False

        log_fn("  BS: waiting for Lessons page to render…", "dim")
        for _ in range(30):  # bounded ~15s wait (30 × 500ms)
            if await _lessons_ready():
                break
            await bs_page.wait_for_timeout(500)

        # ── Click "Create New" — follow the visible UI path ───────────────────
        # The header is responsive: it shows EITHER a standalone "Create New"
        # d2l-button (wide layout) OR an "Add ▾" dropdown holding a "Create New"
        # menu item (narrow layout). The standalone button is present-but-hidden
        # in the narrow layout, so a querySelector click lands on a display:none
        # element and silently does nothing. Coordinate clicks only match VISIBLE
        # elements (getBoundingClientRect width/height > 0), which fixes that.
        _JS_COORDS = """(selector) => {
            function deepFind(root, sel, depth) {
                if (depth === 0) return null;
                for (const el of root.querySelectorAll(sel)) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                }
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot, sel, depth - 1);
                        if (f) return f;
                    }
                }
                return null;
            }
            return deepFind(document, selector, 12);
        }"""

        # The Lessons toolbar and create menu live inside the smart-curriculum
        # iframe, not the top document — but the frame's URL is unreliable to
        # match on, so search every frame for the VISIBLE element and derive that
        # frame's page offset from its own <iframe> element. Element coords are
        # relative to the frame's viewport; add the offset before clicking with
        # the top-level mouse.
        def _all_frames():
            return [bs_page.main_frame,
                    *[f for f in bs_page.frames if f != bs_page.main_frame]]

        async def _frame_offset(frame):
            if frame == bs_page.main_frame:
                return {"x": 0, "y": 0}
            try:
                fe = await frame.frame_element()
                box = await fe.bounding_box()
                if box:
                    return {"x": box["x"], "y": box["y"]}
            except Exception:
                pass
            return {"x": 0, "y": 0}

        async def _click_visible(selector: str) -> bool:
            for frame in _all_frames():
                try:
                    c = await frame.evaluate(_JS_COORDS, selector)
                except Exception:
                    c = None
                if c:
                    off = await _frame_offset(frame)
                    await bs_page.mouse.click(off["x"] + c["x"], off["y"] + c["y"])
                    return True
            return False

        _JS_TILES = """() => {
            function deep(root, depth) {
                if (depth === 0) return false;
                for (const el of root.querySelectorAll('.material-tile-text')) {
                    if ((el.textContent || '').trim() === 'Page') return true;
                }
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot && deep(c.shadowRoot, depth - 1)) return true;
                }
                return false;
            }
            return deep(document, 15);
        }"""

        async def _tiles_present() -> bool:
            for frame in _all_frames():
                try:
                    if await frame.evaluate(_JS_TILES):
                        return True
                except Exception:
                    pass
            return False

        created = False
        # Path A: standalone visible "Create New" button (wide layout)
        if await _click_visible('d2l-button.create-new-btn'):
            await bs_page.wait_for_timeout(1000)
            created = await _tiles_present()
        # Path B: "Add ▾" dropdown → "Create New" menu item (narrow layout)
        if not created:
            if await _click_visible('d2l-dropdown-button-subtle[text="Add"]'):
                await bs_page.wait_for_timeout(700)
            if await _click_visible('d2l-menu-item#create-new-menu-item'):
                await bs_page.wait_for_timeout(1000)
                created = await _tiles_present()

        if not created:
            # Per-frame visibility of each control so a further failure is
            # self-explaining (which frame holds what).
            _JS_VIS = """(sels) => {
                function deep(root, sel, depth) {
                    if (depth === 0) return null;
                    for (const el of root.querySelectorAll(sel)) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) return 'vis';
                    }
                    let hidden = root.querySelector(sel) ? 'hidden' : null;
                    for (const c of root.querySelectorAll('*')) {
                        if (c.shadowRoot) {
                            const f = deep(c.shadowRoot, sel, depth - 1);
                            if (f === 'vis') return 'vis';
                            if (f) hidden = f;
                        }
                    }
                    return hidden;
                }
                const out = {};
                for (const s of sels) out[s] = deep(document, s, 12) || 'none';
                return out;
            }"""
            sels = ['d2l-button.create-new-btn',
                    'd2l-dropdown-button-subtle[text="Add"]',
                    'd2l-menu-item#create-new-menu-item']
            for idx, frame in enumerate(_all_frames()):
                try:
                    vis = await frame.evaluate(_JS_VIS, sels)
                    log_fn(f"  CN diag f{idx} url={(frame.url or '')[:55]} {vis}", "dim")
                except Exception as e:
                    log_fn(f"  CN diag f{idx} err {e}", "dim")
            log_fn("  ✗ 'Create New' — could not open the create menu", "error")
            return False
        await bs_page.wait_for_timeout(600)

        # ── Click "Page" tile ──────────────────────────────────────────────────
        _JS_PAGE_TILE = """() => {
            function deepFind(root, depth) {
                if (depth === 0) return null;
                // Primary: visible Page tile text inside an add-material tile
                for (const t of root.querySelectorAll('.add-material-tile-inner .material-tile-text')) {
                    const a = t.closest('a');
                    if (a && t.textContent.trim() === 'Page') { a.click(); return true; }
                }
                // Fallback: any <a> or <button> whose visible text is exactly "Page"
                for (const el of root.querySelectorAll('a, button')) {
                    if (el.textContent.trim() === 'Page' && el.offsetParent !== null) {
                        el.click(); return true;
                    }
                }
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        if (deepFind(c.shadowRoot, depth - 1)) return true;
                    }
                }
                return false;
            }
            return deepFind(document, 12);
        }"""

        page_tile_found = False
        for attempt in range(20):
            # Recompute frames each attempt — the Create-New slide-out loads its
            # own iframe after the click, so a list captured once would miss it.
            frames = [bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]
            for ctx in frames:
                try:
                    if await ctx.evaluate(_JS_PAGE_TILE):
                        page_tile_found = True
                        break
                except Exception:
                    pass
            if page_tile_found:
                break
            await bs_page.wait_for_timeout(600)

        if not page_tile_found:
            # Deep diagnostic: distinguish "slide-out never opened" from
            # "tiles behind a closed shadow root / unhydrated declarative template".
            _JS_TILE_DIAG = """() => {
                const out = {lightTiles: 0, deepTiles: 0, slideout: 0,
                             templates: 0, icons: 0, sample: []};
                out.lightTiles = document.querySelectorAll('.material-tile-text').length;
                function walk(root, depth) {
                    if (depth === 0) return;
                    for (const el of root.querySelectorAll('*')) {
                        if (el.classList && el.classList.contains('material-tile-text')) {
                            out.deepTiles++;
                            if (out.sample.length < 12) out.sample.push((el.textContent||'').trim());
                        }
                        if (el.id && /slideOut/i.test(el.id)) out.slideout++;
                        if (el.tagName === 'TEMPLATE' && el.getAttribute('shadowrootmode')) out.templates++;
                        if (el.tagName === 'D2L-ICON-CUSTOM') out.icons++;
                        if (el.shadowRoot) walk(el.shadowRoot, depth - 1);
                    }
                }
                walk(document, 15);
                return out;
            }"""
            for idx, ctx in enumerate([bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]):
                try:
                    d = await ctx.evaluate(_JS_TILE_DIAG)
                    log_fn(f"  Tile diag f{idx}: light={d['lightTiles']} deep={d['deepTiles']} "
                           f"slideout={d['slideout']} tmpl={d['templates']} icons={d['icons']} "
                           f"sample={d['sample']}", "dim")
                except Exception as e:
                    log_fn(f"  Tile diag f{idx}: err {e}", "dim")
            log_fn(f"  Page tile diagnostic: url={bs_page.url}, frames={len(bs_page.frames)}", "dim")
            log_fn("  ✗ 'Page' tile not found", "error")
            return False

        # Wait for editor URL (contains /edit/)
        try:
            await bs_page.wait_for_url("**/edit/**", timeout=15000)
        except Exception:
            await bs_page.wait_for_timeout(4000)
        await bs_page.wait_for_timeout(1500)

        # ── Open Source Code editor ────────────────────────────────────────────
        # Toolbar may have overflow; try direct then via more-options chomper
        source_opened = False
        for _ in range(4):
            if await js_click('button[aria-label="Source Code"]'):
                source_opened = True
                break
            await bs_page.wait_for_timeout(700)

        if not source_opened:
            # Click the "More…" overflow toggle (chomper)
            await js_click('d2l-htmleditor-button-toggle.d2l-htmleditor-toolbar-chomper')
            await bs_page.wait_for_timeout(800)
            for _ in range(4):
                if await js_click('button[aria-label="Source Code"]'):
                    source_opened = True
                    break
                await bs_page.wait_for_timeout(500)

        if not source_opened:
            log_fn("  ✗ Source Code button not found", "error")
            return False

        await bs_page.wait_for_timeout(1000)

        # ── Paste HTML into CodeMirror ─────────────────────────────────────────
        await bs_page.evaluate("(h) => navigator.clipboard.writeText(h)", html)
        await bs_page.wait_for_timeout(300)

        await bs_page.evaluate("""() => {
            function deepFind(root) {
                const el = root.querySelector('[contenteditable="true"].cm-content');
                if (el) return el;
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot);
                        if (f) return f;
                    }
                }
                return null;
            }
            const el = deepFind(document);
            if (el) { el.focus(); el.click(); }
        }""")
        await bs_page.wait_for_timeout(400)
        await bs_page.keyboard.press("Control+a")
        await bs_page.wait_for_timeout(200)
        await bs_page.keyboard.press("Control+v")
        await bs_page.wait_for_timeout(800)

        # ── Close Source Code dialog (Save/Update/OK button) ──────────────────
        for selector in (
            '[data-dialog-action="save"]',
            'd2l-button:has-text("Update")',
            'button:has-text("Update")',
            'd2l-button:has-text("OK")',
            'button:has-text("OK")',
            'd2l-button:has-text("Save")',
            'button:has-text("Save")',
        ):
            _, btn = await _find_locator_any_frame(bs_page, selector, retries=4, delay_ms=400)
            if btn:
                await btn.first.click()
                break
        await bs_page.wait_for_timeout(1500)

        # ── Fill title (after source code dialog closed, before Save and Close) ─
        # Use JS only to locate + focus the input, then Playwright keyboard to type
        # — synthetic JS events don't trigger Lit/D2L property observers.
        title_focused = await bs_page.evaluate("""() => {
            function findTitleInput(root, depth) {
                if (depth === 0) return null;
                for (const inp of root.querySelectorAll('input[type="text"], input:not([type])')) {
                    const label = (inp.getAttribute('aria-label') || '').toLowerCase();
                    if (
                        label.includes('title') ||
                        inp.maxLength === 150 ||
                        inp.value === 'Untitled' ||
                        inp.placeholder === 'Untitled'
                    ) { inp.focus(); inp.select(); return true; }
                }
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        if (findTitleInput(c.shadowRoot, depth - 1)) return true;
                    }
                }
                return false;
            }
            return findTitleInput(document, 12);
        }""")
        if title_focused:
            await bs_page.wait_for_timeout(200)
            await bs_page.keyboard.press("Control+a")
            await bs_page.wait_for_timeout(100)
            await bs_page.keyboard.type(title, delay=30)
            await bs_page.wait_for_timeout(500)
            log_fn(f"  ✓ Title typed: {title}", "dim")
        else:
            log_fn("  ⚠ Title field not found — set manually", "warning")

        # ── Save and Close page ────────────────────────────────────────────────
        for selector in (
            'd2l-button:has-text("Save and Close")',
            'button:has-text("Save and Close")',
            'd2l-button:has-text("Save")',
            'button:has-text("Save")',
        ):
            _, btn = await _find_locator_any_frame(bs_page, selector, retries=6, delay_ms=600)
            if btn:
                await btn.first.click()
                try:
                    await bs_page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass
                await bs_page.wait_for_timeout(2000)
                log_fn(f"  ✓ Saved: {title}", "success")
                return True

        log_fn("  ⚠ Save button not found", "warning")
        return False

    async def embed_entries(
        self,
        entries: list[dict],
        section_map: dict[str, str],
        bs_url: str,
        log_fn,
        kmc_username: str = "",
        kmc_password: str = "",
    ) -> None:
        """For each entry: get KMC embed code → create Brightspace page."""
        from content_checker import _extract_course_id
        course_id = _extract_course_id(bs_url)
        if not course_id:
            raise ValueError(f"Could not extract course ID from URL: {bs_url}")
        base_url = "/".join(bs_url.split("/")[:3])

        async with async_playwright() as p:
            kmc_context, kmc_browser, kmc_page = await self._get_kmc_context(
                p, kmc_username=kmc_username, kmc_password=kmc_password, log_fn=log_fn
            )
            bs_browser = await p.chromium.launch(headless=False, slow_mo=80)
            try:
                storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
                bs_context = await bs_browser.new_context(
                    storage_state=storage,
                    no_viewport=True,
                    permissions=["clipboard-read", "clipboard-write"],
                )
                bs_page = await bs_context.new_page()
                log_fn(f"BS page created, closed={bs_page.is_closed()}", "dim")

                # Verify BS session — navigate to home before starting loop
                bs_home = f"{base_url}/d2l/home"
                log_fn(f"BS: verifying session at {bs_home}…", "dim")
                try:
                    await bs_page.goto(bs_home, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    log_fn(f"BS goto warning: {e}", "dim")
                log_fn(f"BS landed on: {bs_page.url}", "dim")
                if "d2l/home" not in bs_page.url:
                    log_fn("BS session expired — please run 'Fetch BS Modules' again to refresh login", "error")
                    return

                log_fn("✓ Both browsers ready", "success")
                for entry in entries:
                    entry_id = entry["entry_id"]
                    name = entry["name"]
                    section_name = entry.get("section_name", "")
                    module_id = section_map.get(section_name)
                    if not module_id:
                        log_fn(f"⚠ No module mapped for '{section_name}', skipping {name}", "warning")
                        continue
                    try:
                        embed_code = await self._get_embed_code(kmc_page, entry_id, log_fn)
                        if not embed_code:
                            continue
                        ok = await self._create_bs_page(
                            bs_page, base_url, course_id, module_id, name, embed_code, log_fn
                        )
                        if not ok:
                            log_fn(f"✗ Failed to create page: {name}", "error")
                    except Exception as e:
                        log_fn(f"✗ {name}: {e}", "error")
            finally:
                try:
                    await kmc_context.storage_state(path=KMC_SESSION_FILE)
                except Exception:
                    pass
                await kmc_browser.close()
                await bs_browser.close()

    async def _get_kmc_context(
        self,
        playwright,
        kmc_username: str = "",
        kmc_password: str = "",
        log_fn=None,
    ):
        """Return a logged-in KMC browser context.

        Loads kmc_session.json if it exists and is still valid.
        Otherwise opens a visible browser for SSO login. If kmc_username/
        kmc_password are provided, auto-fills the Microsoft SSO form once it
        appears (KMC uses the same SSO tenant as Moodle/Brightspace);
        otherwise waits indefinitely for the user to log in manually.
        """
        def log(msg, tag="dim"):
            if log_fn:
                log_fn(msg, tag)
            print(f"[kmc login] {msg}", file=sys.stderr)

        browser = await playwright.chromium.launch(headless=False)
        try:
            storage = KMC_SESSION_FILE if os.path.exists(KMC_SESSION_FILE) else None
            context = await browser.new_context(storage_state=storage)
            page = await context.new_page()
            # wait_until="networkidle" ensures redirects settle before we check URL
            await page.goto(KMC_URL, wait_until="networkidle", timeout=30000)

            if "kmcng/content/entries/list" not in page.url:
                if kmc_username and kmc_password:
                    log("KMC session expired — attempting SSO auto-login…")
                    _sso_attempted = False
                    for i in range(60):
                        await page.wait_for_timeout(3000)
                        url = page.url
                        if "microsoftonline.com" in url:
                            if not _sso_attempted:
                                _sso_attempted = True
                                await _do_ms_sso_login(page, kmc_username, kmc_password)
                            continue
                        if "kmcng/content/entries/list" in url:
                            break
                    else:
                        raise RuntimeError("KMC SSO login timed out after 3 minutes")
                    log("Logged in to KMC.", "success")
                    # Persist session immediately on success so fresh cookies are
                    # saved even if a later step throws before the final save below.
                    await context.storage_state(path=KMC_SESSION_FILE)
                else:
                    log("No KMC credentials set in Settings — log in manually in the browser.", "warning")
                    await page.wait_for_url("**/kmcng/content/entries/list**", timeout=0)
                await page.wait_for_timeout(2000)

            await context.storage_state(path=KMC_SESSION_FILE)
            # Return page so caller can reuse it — avoids opening KMC a second time
            return context, browser, page
        except Exception:
            await browser.close()
            raise
