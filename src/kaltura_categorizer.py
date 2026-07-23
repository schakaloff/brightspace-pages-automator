import asyncio
import re
import os
import sys
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from config import USERDATA_DIR, SESSION_FILE
from automator import _find_locator_any_frame
from browser import _do_ms_sso_login

KMC_URL = "https://kmc.cap2.ovp.kaltura.com/index.php/kmcng/content/entries/list"
KMC_SESSION_FILE = str(USERDATA_DIR / "kmc_session.json")
MOODLE_SESSION_FILE = str(USERDATA_DIR / "moodle_session.json")


class KalturaCategorizer:

    async def scan_moodle_course(
        self,
        moodle_course_url: str,
        log_fn=None,
        moodle_username: str = "",
        moodle_password: str = "",
        _retry_stale_session: bool = True,
    ) -> list[dict]:
        """Scrape all kalvidres activity pages in a Moodle course.

        Returns list of {entry_id, name, moodle_url, section_name}.
        """
        def log(msg, tag="dim"):
            if log_fn:
                log_fn(msg, tag)
            print(f"[kaltura scan] {msg}", file=sys.stderr)

        def safe_url(page_url: str) -> str:
            parsed = urlparse(page_url)
            host = (parsed.hostname or "").lower()
            query_l = parsed.query.lower()
            if "microsoftonline.com" in host:
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?[redacted]"
            if any(key in query_l for key in ("saml", "signature", "token")):
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?[redacted]"
            return page_url

        def stale_course_reason(page_url: str, title: str = "") -> str | None:
            parsed = urlparse(page_url)
            host = (parsed.hostname or "").lower()
            path = parsed.path.lower()
            title_l = title.lower()
            if "microsoftonline.com" in host:
                return "Moodle redirected to Microsoft SSO"
            if host != "mymoodle.okanagan.bc.ca":
                return f"Moodle navigation ended on {host or 'an unknown host'}"
            if "/login/" in path or path.endswith("/login/index.php"):
                return "Moodle login page opened"
            if "sign in to your account" in title_l:
                return "Microsoft sign-in page opened"
            if "/course/view.php" not in path:
                return "Moodle course page did not open"
            return None

        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                moodle_session_exists = os.path.exists(MOODLE_SESSION_FILE)
                log(f"Moodle session file exists: {'yes' if moodle_session_exists else 'no'} ({MOODLE_SESSION_FILE})")
                if moodle_session_exists:
                    log(f"Moodle: using saved session for scan from {MOODLE_SESSION_FILE}")
                    storage = MOODLE_SESSION_FILE
                else:
                    log("Moodle: no saved session for scan; login is required before scanning", "warning")
                    storage = None
                context = await browser.new_context(storage_state=storage)
                page = await context.new_page()

                # Normalise common Moodle URL variants (e.g. enrol/index.php) to course/view.php
                _id_match = re.search(r"[?&]id=(\d+)", moodle_course_url)
                if _id_match and "course/view.php" not in moodle_course_url:
                    _base = moodle_course_url.split("/enrol")[0].split("/course")[0]
                    moodle_course_url = f"{_base}/course/view.php?id={_id_match.group(1)}"
                    log(f"Using Moodle course page: {moodle_course_url}")

                log(f"Navigating to {moodle_course_url}")
                await page.goto(moodle_course_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                log(f"Moodle: final URL after course navigation: {safe_url(page.url)}")

                # Retry if Moodle bounced us to the enrolment page (common on fresh login)
                for _ in range(4):
                    if "/enrol/" not in page.url:
                        break
                    log("Redirected to enrolment page — retrying course page", "dim")
                    await page.goto(moodle_course_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)
                    log(f"Moodle: final URL after enrolment retry: {safe_url(page.url)}")

                try:
                    title = await page.title()
                except Exception:
                    title = ""
                stale_reason = stale_course_reason(page.url, title)
                if stale_reason:
                    if moodle_session_exists and _retry_stale_session:
                        log(f"Saved Moodle session appears stale: {stale_reason}. Re-authenticating once...", "warning")
                        await browser.close()
                        await self.login_to_moodle(
                            moodle_course_url,
                            moodle_username=moodle_username,
                            moodle_password=moodle_password,
                            log_fn=log_fn,
                        )
                        log("Retrying Moodle scan with refreshed session...", "dim")
                        return await self.scan_moodle_course(
                            moodle_course_url,
                            log_fn=log_fn,
                            moodle_username=moodle_username,
                            moodle_password=moodle_password,
                            _retry_stale_session=False,
                        )
                    log(f"Could not open Moodle course content: {stale_reason}. Use 'Login to Moodle' first.", "error")
                    return []

                if "mymoodle.okanagan.bc.ca" not in page.url:
                    log(f"Not on Moodle — got redirected to {page.url[:80]} — session may have expired. Use 'Login to Moodle' first.", "error")
                    return []

                # Kaltura players can be embedded directly in labels/section summaries on
                # the course page, without a kalvidres/page/book activity link.
                course_embeds = await page.evaluate(r"""() => {
                    const cleanText = value => (value || '').replace(/\s+/g, ' ').trim();
                    const entryIdFromSrc = src => {
                        const match = (src || '').match(/entryid(?:%2F|\/|=|%3D)+([01]_[a-z0-9_]+)/i);
                        return match ? match[1] : '';
                    };
                    const found = [];
                    document.querySelectorAll(
                        'iframe.kaltura-player-iframe, iframe[src*="/filter/kaltura/" i], iframe[src*="kaltura" i]'
                    ).forEach(frame => {
                        const entryId = entryIdFromSrc(frame.getAttribute('src') || frame.src);
                        if (!entryId) return;

                        const section = frame.closest('li.section, li.section.main, [data-for="section"]');
                        const heading = section && (
                            section.querySelector(
                                'h3[data-for="section_title"] > a, h4[data-for="section_title"] > a, '
                                + 'h3.sectionname > a, h4.sectionname > a'
                            )
                            || section.querySelector(
                                'h3[data-for="section_title"], h4[data-for="section_title"], '
                                + 'h3.sectionname, h4.sectionname'
                            )
                        );
                        const sectionName = cleanText(
                            heading ? heading.textContent : '(unnamed section)'
                        );

                        const activity = frame.closest(
                            '.activity-altcontent, .contentwithoutlink, .activity-description'
                        ) || frame.closest('.activity-item, li.activity, .activity-wrapper');
                        let name = '';
                        if (activity) {
                            const copy = activity.cloneNode(true);
                            copy.querySelectorAll(
                                'iframe, script, style, .sr-only, .activity-badges, '
                                + '[data-for="cmAvailabilityInfo"]'
                            ).forEach(el => el.remove());
                            name = cleanText(copy.textContent);
                            if (name.length > 160) name = name.slice(0, 157).trimEnd() + '...';
                        }
                        found.push({ entryId, sectionName, name });
                    });
                    return found;
                }""")

                async def get_course_embed_titles() -> dict[str, str]:
                    """Read media titles from the nested Kaltura player frames."""
                    expected_ids = {embed["entryId"] for embed in course_embeds}
                    titles = {}
                    generic_titles = {
                        "okanagan college",
                        "kaltura",
                        "kaltura - everything video",
                        "the kaltura dynamic video player",
                    }

                    for _ in range(12):
                        frame_locators = page.locator(
                            'iframe.kaltura-player-iframe, '
                            'iframe[src*="/filter/kaltura/" i], '
                            'iframe[src*="kaltura" i]'
                        )
                        for index in range(await frame_locators.count()):
                            iframe = frame_locators.nth(index)
                            src = await iframe.get_attribute("src") or ""
                            match = re.search(
                                r"entryid(?:%2F|/|=|%3D)+([01]_[a-z0-9_]+)",
                                src,
                                re.IGNORECASE,
                            )
                            if not match:
                                continue
                            entry_id = match.group(1)
                            if entry_id not in expected_ids or entry_id in titles:
                                continue

                            handle = await iframe.element_handle()
                            outer_frame = await handle.content_frame() if handle else None
                            if not outer_frame:
                                continue

                            frames_to_check = [outer_frame]
                            for frame in frames_to_check:
                                frames_to_check.extend(frame.child_frames)
                                try:
                                    title = re.sub(r"\s+", " ", await frame.title()).strip()
                                except Exception:
                                    continue
                                if title and title.lower() not in generic_titles:
                                    titles[entry_id] = title
                                    break

                        if len(titles) == len(expected_ids):
                            break
                        await page.wait_for_timeout(1000)

                    return titles

                player_titles = await get_course_embed_titles()
                if course_embeds:
                    log(
                        f"Resolved {len(player_titles)} of {len(course_embeds)} "
                        "course-page video title(s) from Kaltura players"
                    )
                for embed in course_embeds:
                    player_title = player_titles.get(embed["entryId"])
                    if player_title:
                        embed["name"] = player_title

                # Collect kalvidres links, mod/page links, and mod/book links (pages/books may embed Kaltura)
                link_data = await page.evaluate("""() => {
                    const map = {};
                    document.querySelectorAll('li.section, li.section.main').forEach(section => {
                        const heading = section.querySelector('.sectionname, h3, h4');
                        const sectionName = heading ? heading.textContent.trim() : '(unnamed section)';
                        section.querySelectorAll(
                            'a[href*="mod/kalvidres/view.php"], a[href*="mod/page/view.php"], a[href*="mod/book/view.php"]'
                        ).forEach(a => {
                            if (!map[a.href]) map[a.href] = sectionName;
                        });
                    });
                    return map;
                }""")

                all_links = list(link_data.keys())
                log(f"Found {len(course_embeds)} Kaltura embed(s) directly on the course page")
                log(f"Found {len(all_links)} candidate link(s) on course page (kalvidres + pages + books)")

                if not all_links and not course_embeds:
                    title = await page.title()
                    log(f"Page title: {title!r} — check URL is a Moodle course page", "warning")
                    return []

                # entry_id dedupe (same video can appear in multiple book chapters/pages)
                seen_entries = set()
                # chapter URL dedupe (a book's own TOC can list the chapter we're already on)
                seen_chapter_urls = set()

                async def scan_embedded_entries():
                    """Scan the current page's body HTML for embedded Kaltura entryIds."""
                    return await page.evaluate(r"""() => {
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
                        return [...new Set(found)];
                    }""")

                async def get_page_name():
                    """Prefer page h1 over browser tab title — tab title includes course prefix."""
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
                    return page_name

                def record_embeds(embedded, name_base, moodle_url, section_name):
                    added = 0
                    for j, entry_id in enumerate(embedded):
                        if entry_id in seen_entries:
                            continue
                        seen_entries.add(entry_id)
                        added += 1
                        name = name_base if len(embedded) == 1 else f"{name_base} ({j+1})"
                        log(f"  → embed entry_id={entry_id}  name={name!r}", "info")
                        results.append({
                            "entry_id": entry_id,
                            "name": name,
                            "moodle_url": moodle_url,
                            "section_name": section_name,
                        })
                    return added

                for embed in course_embeds:
                    entry_id = embed["entryId"]
                    section_name = embed["sectionName"]
                    name = embed["name"] or f"Kaltura video [{entry_id}]"
                    record_embeds(
                        [entry_id],
                        name,
                        moodle_course_url,
                        section_name,
                    )

                for i, link in enumerate(all_links, 1):
                    section_name = link_data[link]
                    log(f"[{i}/{len(all_links)}] Visiting {link}")
                    try:
                        await page.goto(link, wait_until="domcontentloaded", timeout=20000)
                        await page.wait_for_timeout(1500)

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
                            record_embeds([entry_id], name, link, section_name)

                        else:
                            # mod/page or mod/book — scan body HTML for embedded Kaltura entryIds
                            is_book = "mod/book" in link
                            embedded = await scan_embedded_entries()
                            page_name = await get_page_name()

                            if not embedded:
                                log(f"  No Kaltura embeds found in page — skipping", "dim")
                            else:
                                log(f"  Found {len(embedded)} Kaltura embed(s) in page: {page_name!r}", "info")
                                record_embeds(embedded, page_name, link, section_name)

                            if is_book:
                                # Books can have many chapters, each its own URL — scan each too
                                chapter_urls = await page.evaluate("""() => {
                                    // Use the resolved a.href property (always absolute), not the raw
                                    // attribute text — Moodle's book_toc sidebar links are often written
                                    // as relative hrefs that a CSS attribute selector would miss.
                                    const anchors = document.querySelectorAll(
                                        '.book_toc a, a[href*="chapterid"], a[href*="mod/book/view.php"]'
                                    );
                                    const urls = [...anchors].map(a => a.href.split('#')[0]).filter(href => {
                                        let u;
                                        try { u = new URL(href); } catch (e) { return false; }
                                        return u.pathname.includes('/mod/book/view.php')
                                            && u.searchParams.has('chapterid')
                                            && !u.pathname.includes('/mod/book/tool/')
                                            && !href.includes('wordimport')
                                            && !href.includes('export')
                                            && !href.includes('download');
                                    });
                                    return [...new Set(urls)];
                                }""")
                                new_chapters = [u for u in chapter_urls if u != link and u not in seen_chapter_urls]
                                for u in new_chapters:
                                    seen_chapter_urls.add(u)
                                if new_chapters:
                                    log(f"  Book has {len(new_chapters)} more chapter(s) to scan", "dim")
                                for k, chapter_url in enumerate(new_chapters, 1):
                                    log(f"  [chapter {k}/{len(new_chapters)}] Visiting {chapter_url}")
                                    try:
                                        await page.goto(chapter_url, wait_until="domcontentloaded", timeout=20000)
                                        await page.wait_for_timeout(1500)
                                        chapter_embedded = await scan_embedded_entries()
                                        if not chapter_embedded:
                                            continue
                                        chapter_name = await get_page_name()
                                        log(f"  Found {len(chapter_embedded)} Kaltura embed(s) in chapter: {chapter_name!r}", "info")
                                        record_embeds(chapter_embedded, chapter_name, chapter_url, section_name)
                                    except Exception as e:
                                        log(f"    Error scanning chapter: {repr(e)} — skipping", "warning")
                                        continue

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

        log(f"Moodle session file exists before login: {'yes' if os.path.exists(MOODLE_SESSION_FILE) else 'no'} ({MOODLE_SESSION_FILE})")
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
            log(f"Moodle: URL after opening manual login: {page.url}")
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
                log("Filling saved Moodle credentials...")
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

            log(f"Moodle: final URL before saving session: {page.url}")
            await context.storage_state(path=MOODLE_SESSION_FILE)
            log(f"Moodle session saved to {MOODLE_SESSION_FILE} — ready to scan.", "success")
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

        p, browser, context, page = await launch_browser(log_fn=log)
        try:
            log("Brightspace: calling wait_for_login() for module fetch")
            await wait_for_login(
                page, context,
                bs_username or None,
                bs_password or None,
                sso_email or None,
                sso_password or None,
                log_fn=log,
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
            log_fn(f"  [WARN] Entry {entry_id} not found in KMC", "warning")
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
            log_fn(f"  [WARN] Share & Embed link not found: {e}", "warning")
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
            log_fn(f"  [WARN] Textarea read failed: {e}", "warning")

        log_fn(f"  [WARN] Embed textarea empty for {entry_id}", "warning")
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
            log_fn(f"  [WARN] Navigation to module failed: {e}", "warning")
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
            log_fn("  [ERROR] 'Create New' — could not open the create menu", "error")
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
            log_fn("  [ERROR] 'Page' tile not found", "error")
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
            log_fn("  [ERROR] Source Code button not found", "error")
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
            log_fn(f"  [OK] Title typed: {title}", "dim")
        else:
            log_fn("  [WARN] Title field not found — set manually", "warning")

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
                log_fn(f"  [OK] Saved: {title}", "success")
                return True

        log_fn("  [WARN] Save button not found", "warning")
        return False

    async def embed_entries(
        self,
        entries: list[dict],
        section_map: dict[str, str],
        bs_url: str,
        log_fn,
        kmc_username: str = "",
        kmc_password: str = "",
        bs_username: str = "",
        bs_password: str = "",
        sso_email: str = "",
        sso_password: str = "",
    ) -> None:
        """For each entry: get KMC embed code → create Brightspace page."""
        from content_checker import _extract_course_id
        from browser import wait_for_login
        course_id = _extract_course_id(bs_url)
        if not course_id:
            raise ValueError(f"Could not extract course ID from URL: {bs_url}")
        base_url = "/".join(bs_url.split("/")[:3])

        async with async_playwright() as p:
            kmc_context, kmc_browser, kmc_page = await self._get_kmc_context(
                p,
                kmc_username=kmc_username,
                kmc_password=kmc_password,
                sso_email=sso_email,
                sso_password=sso_password,
                log_fn=log_fn,
            )
            bs_browser = await p.chromium.launch(headless=False, slow_mo=80)
            try:
                bs_session_exists = os.path.exists(SESSION_FILE)
                log_fn(f"Brightspace session file exists for page creation: {'yes' if bs_session_exists else 'no'} ({SESSION_FILE})", "dim")
                if bs_session_exists:
                    log_fn(f"Brightspace: loading saved session for page creation from {SESSION_FILE}", "dim")
                else:
                    log_fn("Brightspace: page creation browser has no saved session to load", "warning")
                storage = SESSION_FILE if bs_session_exists else None
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
                bs_logged_out = (
                    "sessionExpired=1" in bs_page.url
                    or "/d2l/login" in bs_page.url
                    or "d2l/home" not in bs_page.url
                )
                if bs_logged_out:
                    log_fn("Brightspace session appears expired during page creation.", "warning")
                    log_fn("Brightspace: calling wait_for_login() for page creation recovery.", "dim")
                    await wait_for_login(
                        bs_page,
                        bs_context,
                        bs_username or None,
                        bs_password or None,
                        sso_email or None,
                        sso_password or None,
                        log_fn=log_fn,
                    )
                    log_fn(f"Brightspace: final URL after page creation login/session recovery: {bs_page.url}", "dim")
                    if "d2l/home" not in bs_page.url:
                        log_fn(f"Brightspace: session recovery failed; current URL is {bs_page.url}", "error")
                        return

                log_fn("[OK] Both browsers ready", "success")
                for entry in entries:
                    entry_id = entry["entry_id"]
                    name = entry["name"]
                    section_name = entry.get("section_name", "")
                    module_id = section_map.get(section_name)
                    if not module_id:
                        log_fn(f"[WARN] No module mapped for '{section_name}', skipping {name}", "warning")
                        continue
                    try:
                        embed_code = await self._get_embed_code(kmc_page, entry_id, log_fn)
                        if not embed_code:
                            continue
                        ok = await self._create_bs_page(
                            bs_page, base_url, course_id, module_id, name, embed_code, log_fn
                        )
                        if not ok:
                            log_fn(f"[ERROR] Failed to create page: {name}", "error")
                    except Exception as e:
                        log_fn(f"[ERROR] {name}: {e}", "error")
            finally:
                try:
                    await kmc_context.storage_state(path=KMC_SESSION_FILE)
                    log_fn(f"KMC session saved to {KMC_SESSION_FILE}", "dim")
                except Exception:
                    log_fn("KMC session save skipped after page creation because the KMC context was unavailable", "dim")
                await kmc_browser.close()
                await bs_browser.close()

    async def _get_kmc_context(
        self,
        playwright,
        kmc_username: str = "",
        kmc_password: str = "",
        sso_email: str = "",
        sso_password: str = "",
        log_fn=None,
    ):
        """Return a logged-in KMC browser context.

        Loads kmc_session.json if it exists and is still valid.
        Otherwise opens a visible browser for SSO login. If KMC credentials
        or global SSO credentials are provided, auto-fills the Microsoft SSO
        form once it appears (KMC uses the same SSO tenant as Moodle/Brightspace);
        otherwise waits indefinitely for the user to log in manually.
        """
        def log(msg, tag="dim"):
            if log_fn:
                log_fn(msg, tag)
            print(f"[kmc login] {msg}", file=sys.stderr)

        async def current_kmc_state() -> str:
            if "microsoftonline.com" in page.url:
                return "microsoft"
            login_form = page.locator("form.kLoginForm").first
            if await login_form.count() > 0 and await login_form.is_visible():
                return "login"
            if "kmcng/content/entries/list" in page.url:
                entries_ui = page.locator(
                    "p-table, tr.kEntry, input[type='text']"
                )
                for index in range(await entries_ui.count()):
                    if await entries_ui.nth(index).is_visible():
                        return "entries"
            return "loading"

        async def wait_for_kmc_state(timeout_seconds: int | None = 15) -> str:
            attempts = 0
            while timeout_seconds is None or attempts < timeout_seconds:
                if page.is_closed():
                    raise RuntimeError("KMC browser was closed before login completed")
                state = await current_kmc_state()
                if state != "loading":
                    return state
                attempts += 1
                await page.wait_for_timeout(1000)
            return await current_kmc_state()

        async def wait_for_kmc_entries() -> None:
            while True:
                if page.is_closed():
                    raise RuntimeError("KMC browser was closed before login completed")
                if await current_kmc_state() == "entries":
                    return
                await page.wait_for_timeout(1000)

        async def try_native_kmc_login() -> bool:
            form = page.locator("form.kLoginForm").first
            if await form.count() == 0:
                return False
            email_input = form.locator('input:not([type="password"]):not(.kAuth)').first
            password_input = form.locator('input[type="password"]').first
            login_button = form.locator('button:has-text("Login")').first
            if await email_input.count() == 0 or await password_input.count() == 0:
                return False
            log("KMC native login form detected; filling saved KMC credentials")
            await email_input.fill(kmc_username)
            await password_input.fill(kmc_password)
            if await login_button.count() > 0:
                await login_button.click()
            else:
                await password_input.press("Enter")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            for _ in range(30):
                state = await current_kmc_state()
                if state == "entries":
                    return True
                if state == "microsoft":
                    return False
                await page.wait_for_timeout(1000)
            return False

        async def click_kmc_sso_link() -> bool:
            link = page.locator('form.kLoginForm a:has-text("Login with SSO")').first
            if await link.count() == 0:
                return False
            log("KMC native login form detected; opening Login with SSO")
            await link.click()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            return True

        browser = await playwright.chromium.launch(headless=False)
        try:
            kmc_session_exists = os.path.exists(KMC_SESSION_FILE)
            log(f"KMC session file exists: {'yes' if kmc_session_exists else 'no'} ({KMC_SESSION_FILE})")
            if kmc_session_exists:
                log(f"KMC: loading saved session from {KMC_SESSION_FILE}")
            else:
                log("KMC: launching without saved session")
            storage = KMC_SESSION_FILE if kmc_session_exists else None
            context = await browser.new_context(storage_state=storage)
            page = await context.new_page()
            await page.goto(KMC_URL, wait_until="domcontentloaded", timeout=30000)
            kmc_state = await wait_for_kmc_state(timeout_seconds=15)
            log(f"KMC: URL after opening KMC_URL: {page.url}")

            if kmc_state != "entries":
                log(
                    f"KMC: entries UI is not ready after navigation (state: {kmc_state}); "
                    "treating the session as logged out or redirected",
                    "warning",
                )
                ms_username = kmc_username if kmc_username and kmc_password else sso_email
                ms_password = kmc_password if kmc_username and kmc_password else sso_password
                credential_source = "KMC credentials" if kmc_username and kmc_password else "global SSO credentials"
                if ms_username and ms_password:
                    _login_completed = False
                    if kmc_username and kmc_password:
                        try:
                            _login_completed = await try_native_kmc_login()
                        except Exception as e:
                            log(f"KMC native credential login did not complete: {e}", "warning")
                    if not _login_completed and "microsoftonline.com" not in page.url:
                        try:
                            await click_kmc_sso_link()
                        except Exception as e:
                            log(f"KMC Login with SSO click did not complete: {e}", "warning")
                    if _login_completed:
                        log(f"KMC: final URL after native login attempt: {page.url}")
                        log("Logged in to KMC.", "success")
                        await context.storage_state(path=KMC_SESSION_FILE)
                        log(f"KMC session saved to {KMC_SESSION_FILE}", "success")
                    else:
                        log(f"KMC session expired — attempting Microsoft SSO auto-login using {credential_source}...")
                    _sso_attempted = False
                    _sso_completed = False
                    if not _login_completed:
                        for i in range(60):
                            await page.wait_for_timeout(3000)
                            state = await current_kmc_state()
                            if state == "microsoft":
                                if not _sso_attempted:
                                    _sso_attempted = True
                                    log("KMC: Microsoft SSO page detected; submitting saved SSO credentials")
                                    await _do_ms_sso_login(page, ms_username, ms_password)
                                continue
                            if state == "entries":
                                _sso_completed = True
                                break
                        if _sso_completed:
                            log(f"KMC: final URL after SSO attempt: {page.url}")
                            log("Logged in to KMC.", "success")
                            # Persist session immediately on success so fresh cookies are
                            # saved even if a later step throws before the final save below.
                            await context.storage_state(path=KMC_SESSION_FILE)
                            log(f"KMC session saved to {KMC_SESSION_FILE}", "success")
                        else:
                            log(f"KMC: automatic SSO did not complete; final URL was {page.url}", "warning")
                            log("KMC: falling back to manual login and waiting for entries list", "warning")
                            await wait_for_kmc_entries()
                            log(f"KMC: final URL after manual login: {page.url}")
                else:
                    log("No KMC or global SSO credentials set in Settings — log in manually in the browser.", "warning")
                    log("KMC: falling back to manual login and waiting for entries list")
                    await wait_for_kmc_entries()
                    log(f"KMC: final URL after manual login: {page.url}")
                await page.wait_for_timeout(2000)
            else:
                log("KMC: already logged in from saved session", "success")

            log(f"KMC: final URL before saving session: {page.url}")
            await context.storage_state(path=KMC_SESSION_FILE)
            log(f"KMC session saved to {KMC_SESSION_FILE}", "success")
            # Return page so caller can reuse it — avoids opening KMC a second time
            return context, browser, page
        except Exception:
            await browser.close()
            raise
