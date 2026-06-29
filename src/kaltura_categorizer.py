import asyncio
import re
import os
import sys

from playwright.async_api import async_playwright

from config import USERDATA_DIR, SESSION_FILE
from automator import _find_locator_any_frame

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

                # Collect all kalvidres links — original flat approach (proven reliable)
                links = await page.evaluate("""() => {
                    return [...document.querySelectorAll('a[href*="mod/kalvidres/view.php"]')]
                        .map(a => a.href);
                }""")
                links = list(dict.fromkeys(links))  # deduplicate, preserve order
                log(f"Found {len(links)} kalvidres link(s) on course page")

                if not links:
                    # Debug: show page title so we know what we landed on
                    title = await page.title()
                    log(f"Page title: {title!r} — check URL is a Moodle course page", "warning")
                    return []

                # Best-effort: map each link to its Moodle section name
                link_to_section = await page.evaluate("""() => {
                    const map = {};
                    document.querySelectorAll('li.section, li.section.main').forEach(section => {
                        const heading = section.querySelector('.sectionname, h3, h4');
                        const sectionName = heading ? heading.textContent.trim() : '(unnamed section)';
                        section.querySelectorAll('a[href*="mod/kalvidres/view.php"]').forEach(a => {
                            map[a.href] = sectionName;
                        });
                    });
                    return map;
                }""")

                link_items = [
                    {"href": link, "section_name": link_to_section.get(link, "")}
                    for link in links
                ]

                for i, item in enumerate(link_items, 1):
                    link = item["href"]
                    section_name = item["section_name"]
                    log(f"[{i}/{len(link_items)}] Visiting {link}")
                    try:
                        await page.goto(link, wait_until="networkidle", timeout=20000)

                        iframe_src = await page.evaluate("""() => {
                            const f = document.querySelector('iframe#contentframe');
                            return f ? f.src : '';
                        }""")
                        if not iframe_src:
                            log(f"  No contentframe iframe found — skipping", "warning")
                            continue
                        m = re.search(r'entryid%2F([\w_]+)', iframe_src, re.IGNORECASE)
                        if not m:
                            log(f"  No entryid in iframe src: {iframe_src[:80]!r} — skipping", "warning")
                            continue
                        entry_id = m.group(1)

                        title = await page.title()
                        name = re.sub(r'\s*\|\s*OCmoodle\s*$', '', title).strip()
                        log(f"  → entry_id={entry_id}  name={name!r}  section={section_name!r}", "info")

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

        # ── Click "Create New" (in smart-curriculum iframe) ───────────────────
        # d2l-button has shadowRoot, so js_click clicks its inner <button>
        if not await js_click('d2l-button[aria-label="Create New"]'):
            log_fn("  ✗ 'Create New' button not found", "error")
            return False
        await bs_page.wait_for_timeout(1200)

        # ── Click "Page" tile ──────────────────────────────────────────────────
        # Confirmed selector: a.add-material-tile with .material-tile-text === "Page"
        _JS_PAGE_TILE = """() => {
            const frames = [document];
            for (const f of document.querySelectorAll('iframe')) {
                try { if (f.contentDocument) frames.push(f.contentDocument); } catch(e) {}
            }
            for (const doc of frames) {
                for (const a of doc.querySelectorAll('a.add-material-tile')) {
                    const t = a.querySelector('.material-tile-text');
                    if (t && t.textContent.trim() === 'Page') { a.click(); return true; }
                }
            }
            return false;
        }"""

        page_tile_found = False
        for ctx in [bs_page, *[f for f in bs_page.frames if f != bs_page.main_frame]]:
            try:
                if await ctx.evaluate(_JS_PAGE_TILE):
                    page_tile_found = True
                    break
            except Exception:
                pass

        if not page_tile_found:
            log_fn("  ✗ 'Page' tile not found", "error")
            return False

        # Wait for editor URL (contains /edit/)
        try:
            await bs_page.wait_for_url("**/edit/**", timeout=15000)
        except Exception:
            await bs_page.wait_for_timeout(4000)
        await bs_page.wait_for_timeout(1500)

        # ── Fill title ─────────────────────────────────────────────────────────
        # Title input is "Untitled" placeholder inside shadow DOM
        title_filled = await bs_page.evaluate("""(t) => {
            function deepFind(root, depth) {
                if (depth === 0) return null;
                for (const inp of root.querySelectorAll('input')) {
                    if (inp.value === 'Untitled' || inp.placeholder === 'Untitled') return inp;
                }
                for (const c of root.querySelectorAll('*')) {
                    if (c.shadowRoot) {
                        const f = deepFind(c.shadowRoot, depth - 1);
                        if (f) return f;
                    }
                }
                return null;
            }
            const inp = deepFind(document, 12);
            if (!inp) return false;
            inp.focus();
            inp.select();
            inp.value = '';
            inp.dispatchEvent(new Event('input', {bubbles:true}));
            inp.value = t;
            inp.dispatchEvent(new Event('input', {bubbles:true}));
            inp.dispatchEvent(new Event('change', {bubbles:true}));
            return true;
        }""", title)
        if title_filled:
            log_fn(f"  ✓ Title: {title}", "dim")
        else:
            log_fn("  ⚠ Title field not found — set manually", "warning")

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
    ) -> None:
        """For each entry: get KMC embed code → create Brightspace page."""
        from content_checker import _extract_course_id
        course_id = _extract_course_id(bs_url)
        if not course_id:
            raise ValueError(f"Could not extract course ID from URL: {bs_url}")
        base_url = "/".join(bs_url.split("/")[:3])

        async with async_playwright() as p:
            kmc_context, kmc_browser = await self._get_kmc_context(p)
            bs_browser = await p.chromium.launch(headless=False, slow_mo=80)
            try:
                storage = SESSION_FILE if os.path.exists(SESSION_FILE) else None
                bs_context = await bs_browser.new_context(
                    storage_state=storage,
                    no_viewport=True,
                    permissions=["clipboard-read", "clipboard-write"],
                )
                kmc_page = await kmc_context.new_page()
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

    async def _get_kmc_context(self, playwright):
        """Return a logged-in KMC browser context.

        Loads kmc_session.json if it exists and is still valid.
        Otherwise opens a visible browser for manual SSO login, waits
        for the KMC entries list page to load, then saves the session.
        """
        browser = await playwright.chromium.launch(headless=False)
        try:
            storage = KMC_SESSION_FILE if os.path.exists(KMC_SESSION_FILE) else None
            context = await browser.new_context(storage_state=storage)
            page = await context.new_page()
            # wait_until="networkidle" ensures redirects settle before we check URL
            await page.goto(KMC_URL, wait_until="networkidle", timeout=30000)

            # If not on entries list (expired session / SSO redirect), wait indefinitely
            if "kmcng/content/entries/list" not in page.url:
                await page.wait_for_url("**/kmcng/content/entries/list**", timeout=0)
                # Let the SPA finish rendering before saving session
                await page.wait_for_timeout(2000)

            await context.storage_state(path=KMC_SESSION_FILE)
            await page.close()
            return context, browser
        except Exception:
            await browser.close()
            raise
