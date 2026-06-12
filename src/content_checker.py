"""
Content Checker:
  1. Fetch Brightspace course TOC via D2L API  (BS URL required)
  2. Scrape Moodle course items — structured    (Moodle URL required)
  3. Compare: exact / fuzzy / missing
  4. Log a verification report

Testable modes:
  - BS URL only   → show all Brightspace modules + topics
  - Moodle only   → show all Moodle items (same output as Style Migrator)
  - Both URLs     → full side-by-side comparison
"""
import asyncio
import difflib
import html as html_module
import re
import threading
from typing import Callable, List, Optional

from playwright.async_api import BrowserContext, Page
from config import SESSION_FILE


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_course_id(url: str) -> Optional[str]:
    for pat in [
        r'/content/(\d+)',
        r'/lessons/(\d+)',
        r'/d2l/home/(\d+)',
        r'/le/(\d+)/',
        r'[?&]ou=(\d+)',
    ]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _flatten_toc(modules: list, parent: str = "") -> list:
    items = []
    for mod in modules:
        title = (mod.get("Title") or "").strip()
        items.append({"kind": "MODULE", "title": title, "parent": parent})
        for topic in mod.get("Structure") or []:
            items.append({
                "kind":    "TOPIC",
                "title":   (topic.get("Title") or "").strip(),
                "type_id": topic.get("TypeIdentifier", ""),
                "module":  title,
            })
        items.extend(_flatten_toc(mod.get("Modules") or [], title))
    return items


def _norm(text: str) -> str:
    """Lowercase + decode HTML entities so &amp; == & in comparisons."""
    return html_module.unescape(text).lower().strip()


def _compare_items(moodle_items: list, bs_flat: list) -> list:
    SKIP = {"LABEL", "FORUM"}

    bs_modules = {_norm(i["title"]): i["title"] for i in bs_flat if i["kind"] == "MODULE"}
    bs_topics  = {_norm(i["title"]): i["title"] for i in bs_flat if i["kind"] == "TOPIC"}
    # Combined pool for non-section items: folders/pages on Moodle may map to either
    bs_all     = {**bs_modules, **bs_topics}

    results = []
    current_section = ""

    for item in moodle_items:
        if item["type"] == "SECTION":
            current_section = item["name"]
            name_l = _norm(item["name"])
            if name_l in bs_modules:
                status, matched = "exact", bs_modules[name_l]
            else:
                close = difflib.get_close_matches(name_l, bs_modules.keys(), n=1, cutoff=0.70)
                if close:
                    score = int(difflib.SequenceMatcher(None, name_l, close[0]).ratio() * 100)
                    status, matched = "fuzzy", (bs_modules[close[0]], score)
                else:
                    status, matched = "missing", None
            results.append({**item, "section": "", "status": status, "matched": matched})
            continue

        if item["type"] in SKIP:
            continue

        name_l = _norm(item["name"])

        if name_l in bs_all:
            results.append({**item, "section": current_section,
                             "status": "exact", "matched": bs_all[name_l]})
            continue

        close = difflib.get_close_matches(name_l, bs_all.keys(), n=1, cutoff=0.75)
        if close:
            score = int(difflib.SequenceMatcher(None, name_l, close[0]).ratio() * 100)
            results.append({**item, "section": current_section,
                             "status": "fuzzy", "matched": bs_all[close[0]], "score": score})
            continue

        results.append({**item, "section": current_section,
                         "status": "missing", "matched": None})

    return results


# ── Moodle structured scraper JS (same logic as style_migrator) ───────────────

_JS_MOODLE_ITEMS = """() => {
    const TYPES = {
        modtype_resource:     'FILE',
        modtype_assign:       'ASSIGN',
        modtype_quiz:         'QUIZ',
        modtype_url:          'URL',
        modtype_page:         'PAGE',
        modtype_forum:        'FORUM',
        modtype_label:        'LABEL',
        modtype_folder:       'FOLDER',
        modtype_kalturamedia: 'VIDEO',
        modtype_lti:          'VIDEO',
    };

    function labelInfo(activity) {
        const body = activity.querySelector(
            '.contentafterlink, .description, .no-overflow, .labelcontent, .activitybody'
        );
        if (!body) return { type: 'LABEL', name: '(empty label)' };

        const vjsEl  = body.querySelector('.video-js, [id*="videojs_"]');
        const kframe = body.querySelector('iframe[src*="kaltura"]');
        const hasVideo = !!(vjsEl || kframe);
        let entryId = '';
        if (kframe) {
            const m = (kframe.src || '').match(/entryid\\/([^\\/]+)/);
            if (m) entryId = m[1];
        }

        const rawText = body.textContent.trim().replace(/\\s+/g, ' ');
        const isOnlyNoise = /^Video Player is loading/.test(rawText);
        if (isOnlyNoise || (hasVideo && rawText.replace(/Video Player.*/, '').trim().length < 5)) {
            return { type: 'VIDEO', name: entryId ? 'Kaltura video [' + entryId + ']' : '(embedded video)' };
        }

        const cleanText = rawText.replace(/Video Player is loading.*?(Current Time \\d|$)/g, '').trim();
        let name = null;
        const heading = body.querySelector('h1,h2,h3,h4,h5,h6');
        if (heading) { const t = heading.textContent.trim(); if (t.length > 2) name = t; }
        if (!name) {
            const bold = body.querySelector('strong, b');
            if (bold) { const t = bold.textContent.trim(); if (t.length > 2 && t.length < 120) name = t; }
        }
        if (!name) {
            const link = body.querySelector('a');
            if (link) { const t = link.textContent.trim(); name = t.length > 2 ? t : (link.href || null); }
        }
        if (!name && cleanText.length > 2) name = cleanText.slice(0, 80) + (cleanText.length > 80 ? '…' : '');
        if (!name) name = body.querySelector('img') ? '(image)' : '(empty label)';
        if (hasVideo) name += entryId ? ' [🎥 ' + entryId + ']' : ' [🎥]';
        return { type: 'LABEL', name };
    }

    const result = [];
    document.querySelectorAll('li.section, li.section.main').forEach(section => {
        const heading = section.querySelector('.sectionname, h3, h4');
        result.push({
            type: 'SECTION',
            name: heading ? heading.textContent.trim() : '(unnamed section)',
            href: '',
        });
        section.querySelectorAll('li.activity').forEach(activity => {
            const cls = Array.from(activity.classList);
            const matched = cls.find(c => TYPES[c]);
            if (!matched) return;
            let type = TYPES[matched];
            const anchor = activity.querySelector('a');
            let name, href = anchor ? anchor.href : '';
            if (matched === 'modtype_label') {
                const info = labelInfo(activity);
                type = info.type; name = info.name;
            } else {
                const nameEl = activity.querySelector('.instancename, .activityname a, a');
                name = nameEl ? nameEl.textContent.trim().replace(/\\s{2,}.*$/, '').trim() : '(unnamed)';
            }
            result.push({ type, name, href });
        });
    });
    return result;
}"""


# ── ContentChecker ────────────────────────────────────────────────────────────

class ContentChecker:
    def __init__(
        self,
        bs_url:              str,
        moodle_url:          str,
        log:                 Callable[[str, str], None],
        on_complete:         Callable           = None,
        moodle_ready_event:  threading.Event    = None,
        on_moodle_waiting:   Callable           = None,
    ):
        self.bs_url             = bs_url.strip()
        self.moodle_url         = moodle_url.strip()
        self.log                = log
        self.on_complete        = on_complete
        self.moodle_ready_event = moodle_ready_event
        self.on_moodle_waiting  = on_moodle_waiting

    # ── Brightspace TOC ───────────────────────────────────────────────────────

    async def _fetch_bs_toc(self, page: Page, course_id: str) -> Optional[list]:
        self.log(f"Fetching Brightspace content (course {course_id})…", "info")
        try:
            # Navigate to the lessons page so we're on the right domain + session
            await page.goto(
                f"https://learn.okanagancollege.ca/d2l/le/lessons/{course_id}",
                wait_until="domcontentloaded", timeout=20000,
            )
            await page.wait_for_timeout(1500)

            # Fetch TOC for module IDs, then fetch each module's structure in parallel
            self.log("  Fetching module structures via API…", "dim")
            items = await page.evaluate("""async (courseId) => {
                // Step 1: get all module IDs from TOC
                const tocResp = await fetch(
                    `/d2l/api/le/1.0/${courseId}/content/toc`,
                    { headers: { 'Accept': 'application/json' } }
                );
                if (!tocResp.ok) return null;
                const toc = await tocResp.json();

                function collectModules(modules, parent) {
                    const out = [];
                    for (const m of (modules || [])) {
                        const id    = m.ModuleId ?? m.Id ?? m.id ?? null;
                        const title = (m.Title || m.title || '').trim();
                        if (id != null) out.push({ id, title, parent });
                        out.push(...collectModules(m.Modules || [], title));
                    }
                    return out;
                }
                const modules = collectModules(toc.Modules || [], '');
                if (!modules.length) return [];

                // Step 2: fetch all module structures in parallel
                const results = await Promise.all(modules.map(async mod => {
                    try {
                        const r = await fetch(
                            `/d2l/api/le/1.0/${courseId}/content/modules/${mod.id}/structure/`,
                            { headers: { 'Accept': 'application/json' } }
                        );
                        return { mod, children: r.ok ? await r.json() : [] };
                    } catch { return { mod, children: [] }; }
                }));

                // Step 3: flatten — topics have a non-empty Url; sub-modules don't
                const items = [];
                for (const { mod, children } of results) {
                    items.push({ kind: 'MODULE', title: mod.title, id: mod.id });
                    for (const c of (children || [])) {
                        const url = c.Url || c.url || '';
                        if (!url) continue; // sub-module, already in modules list
                        const title = (c.Title || c.title || '').trim();
                        const id    = c.Id ?? c.id ?? c.TopicId ?? null;
                        if (title) items.push({ kind: 'TOPIC', title, module: mod.title, url, id });
                    }
                }
                return items;
            }""", course_id)

            if not items:
                self.log("✗ API returned nothing — check course ID or permissions", "error")
                return None

            n_mod   = sum(1 for i in items if i["kind"] == "MODULE")
            n_topic = sum(1 for i in items if i["kind"] == "TOPIC")
            self.log(f"✓ Brightspace: {n_mod} modules, {n_topic} topics", "success")
            return items

        except Exception as e:
            self.log(f"✗ Fetch error: {e}", "error")
            return None

    async def _bs_content_scan(
        self, page: "Page", course_id: str, bs_flat: list, missing_results: list
    ) -> dict:
        """
        For remaining ❌ items, type each name into the BS Lessons search bar and
        report which BS topic the search returns as the top hit.
        Uses Playwright's built-in shadow-DOM piercing instead of manual JS traversal.
        Returns {moodle_name: bs_topic_title_found}.
        """
        searchable = [
            r for r in missing_results
            if r["status"] == "missing"
            and r["type"] not in {"VIDEO", "LABEL"}
            and len(r["name"].strip()) > 3
            and r["name"] not in {"(embedded video)", "(image)"}
        ]
        if not searchable:
            return {}

        # Make sure we're on the lessons page — Moodle scraping may have changed focus
        lessons_url = f"https://learn.okanagancollege.ca/d2l/le/lessons/{course_id}"
        if f"/lessons/{course_id}" not in page.url:
            self.log("  Navigating back to lessons page for search…", "dim")
            await page.goto(lessons_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

        # d2l-input-search[placeholder="Search titles, descriptions"] wraps a plain
        # <input> inside its shadow root.  Playwright's CSS piercing only goes one
        # level deep, so we use evaluate_handle to cross the shadow boundary in JS
        # and return a real ElementHandle we can call fill() / press() on.
        js_handle = await page.evaluate_handle("""() => {
            const comp = document.querySelector(
                'd2l-input-search[placeholder="Search titles, descriptions"]'
            );
            return comp?.shadowRoot?.querySelector('input') ?? null;
        }""")
        input_el = js_handle.as_element()
        if not input_el:
            self.log("  ⚠ Lessons search bar not found — skipping search scan", "warning")
            return {}

        self.log(f"  Search bar found — querying {len(searchable)} item(s)…", "dim")
        found = {}

        for r in searchable:
            query = r["name"].strip()[:50]
            self.log(f"  🔎 \"{query}\"", "dim")

            try:
                await input_el.fill("")
                await input_el.fill(query)
                await input_el.press("Enter")   # triggers dropdown in D2L Lessons
                await page.wait_for_timeout(1600)

                # Collect whatever autocomplete options appeared (shadow-DOM aware)
                texts = await page.evaluate("""() => {
                    const out = [];
                    function gather(root, depth=0) {
                        if (depth > 10 || !root) return;
                        for (const el of (root.querySelectorAll?.('*') || [])) {
                            const role = el.getAttribute?.('role') || '';
                            if (role === 'option' || role === 'menuitem') {
                                const t = (el.getAttribute('label') || el.textContent || '').trim();
                                if (t && t.length > 1 && t.length < 300 && !out.includes(t))
                                    out.push(t);
                            }
                            if (el.shadowRoot) gather(el.shadowRoot, depth + 1);
                        }
                    }
                    gather(document.body);
                    return out.slice(0, 10);
                }""")

                await input_el.fill("")         # clear for next query
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)

            except Exception as exc:
                self.log(f"       ⚠ error: {exc}", "warning")
                continue

            if not texts:
                self.log("       no dropdown results appeared", "dim")
                continue

            q_lower        = query.lower()
            best_candidate = None
            for candidate in texts:
                ratio  = difflib.SequenceMatcher(None, q_lower, candidate.lower()).ratio()
                pct    = int(ratio * 100)
                chosen = ratio >= 0.60 and best_candidate is None
                if chosen:
                    best_candidate = candidate
                self.log(f"       {'→ ' if chosen else '  '}[{pct:3d}%] {candidate}", "dim")

            if best_candidate:
                found[r["name"]] = best_candidate
            else:
                self.log("       no result met the 60 % threshold", "dim")

        return found

    async def _scan_page_content(
        self, page: "Page", course_id: str, bs_flat: list, missing_results: list
    ) -> tuple:
        """
        Fetch raw HTML for every BS topic that has an id.
        Pass 1 – text scan: look for missing Moodle item names inside topic text.
        Pass 2 – link scan: flag any <a href> pointing back to Moodle.

        Returns:
            found_in_content : {moodle_name: bs_topic_title}
            moodle_links     : [{"topic", "text", "href"}]
        """
        # Make sure we're still on a Brightspace page so fetch() calls have the right cookies
        if "learn.okanagancollege.ca" not in page.url:
            await page.goto(
                f"https://learn.okanagancollege.ca/d2l/le/lessons/{course_id}",
                wait_until="domcontentloaded", timeout=20000,
            )
            await page.wait_for_timeout(1500)

        # Build module → topics map and collect all fetchable topics
        mod_of = {}           # topic title → module title
        current_mod = None
        topics_with_id = []
        for item in bs_flat:
            if item["kind"] == "MODULE":
                current_mod = item["title"]
            elif item["kind"] == "TOPIC" and item.get("id") and current_mod:
                mod_of[item["title"]] = current_mod
                topics_with_id.append(item)

        if not topics_with_id:
            self.log("  ⚠ No topic IDs in API response — skipping page content scan", "warning")
            self.log("    (D2L may not return Id field; link scan unavailable)", "dim")
            return {}, []

        # Items still missing after search-bar scan (skip video / unnamed labels)
        still_missing = [
            r for r in missing_results
            if r["status"] == "missing"
            and r["type"] not in {"VIDEO", "LABEL"}
            and len(r["name"].strip()) > 3
            and r["name"] not in {"(embedded video)", "(image)"}
        ]
        search_names = [r["name"].lower() for r in still_missing]

        total = len(topics_with_id)
        self.log(f"  Fetching {total} topic pages — text scan + Moodle link check…", "info")

        MOODLE_HOST = "mymoodle.okanagan.bc.ca"
        BATCH       = 15
        found_in_content: dict  = {}
        moodle_links:     list  = []

        for batch_start in range(0, total, BATCH):
            batch = topics_with_id[batch_start : batch_start + BATCH]
            pairs = [[t["title"], t["id"]] for t in batch]
            self.log(f"    topics {batch_start + 1}–{min(batch_start + BATCH, total)} / {total}…", "dim")

            results = await page.evaluate("""async ([courseId, pairs]) => {
                return await Promise.all(pairs.map(async ([title, topicId]) => {
                    try {
                        const r = await fetch(
                            `/d2l/api/le/1.0/${courseId}/content/topics/${topicId}/file`,
                            { credentials: 'include' }
                        );
                        if (!r.ok) return { title, skip: true };

                        const ct = (r.headers.get('content-type') || '').toLowerCase();
                        if (!ct.includes('text/html')) return { title, skip: true };

                        const html  = await r.text();
                        const div   = document.createElement('div');
                        div.innerHTML = html;

                        const text  = (div.textContent || '').replace(/\\s+/g, ' ').toLowerCase();
                        const links = [];
                        div.querySelectorAll('a[href]').forEach(a => {
                            const href = a.getAttribute('href') || '';
                            if (href) links.push({ text: a.textContent.trim().slice(0, 120), href });
                        });

                        return { title, text, links };
                    } catch (e) {
                        return { title, skip: true };
                    }
                }));
            }""", [course_id, pairs])

            for res in (results or []):
                if not res or res.get("skip"):
                    continue

                topic_title = res["title"]
                text        = res.get("text", "")
                links       = res.get("links", [])

                # Text scan — look for missing Moodle item names in page body
                for name_l in search_names:
                    original = still_missing[search_names.index(name_l)]["name"]
                    if original not in found_in_content and name_l[:30] in text:
                        found_in_content[original] = topic_title

                # Link scan — flag Moodle-domain hrefs
                for link in links:
                    href = link.get("href", "")
                    if MOODLE_HOST in href:
                        moodle_links.append({
                            "topic": topic_title,
                            "text":  link["text"],
                            "href":  href,
                        })

        return found_in_content, moodle_links

    def _log_link_report(self, moodle_links: list) -> None:
        """Log the Moodle-link-in-BS report section."""
        self.log("", "dim")
        self.log("─" * 52, "dim")
        if not moodle_links:
            self.log("🔗 Link scan: no Moodle links found in any BS page", "success")
            return

        # Group by topic
        by_topic: dict = {}
        for entry in moodle_links:
            by_topic.setdefault(entry["topic"], []).append(entry)

        self.log(f"🔗 Link scan: {len(moodle_links)} Moodle link(s) in {len(by_topic)} topic(s)", "warning")
        self.log("   These links point back to Moodle and may need re-hosting:", "dim")
        self.log("", "dim")
        for topic_title, entries in by_topic.items():
            self.log(f"   📄 {topic_title}", "step")
            for e in entries:
                label = e["text"] or "(no text)"
                self.log(f"      ❌ \"{label}\"", "error")
                self.log(f"         {e['href']}", "dim")

    def _log_bs_toc(self, bs_flat: list) -> None:
        self.log("─" * 52, "dim")
        current_mod = None
        for item in bs_flat:
            if item["kind"] == "MODULE":
                self.log("", "dim")
                self.log(f"── {item['title']}", "step")
                current_mod = item["title"]
            else:
                self.log(f"   {item['title']}", "info")
        self.log("", "dim")
        n_mod   = sum(1 for i in bs_flat if i["kind"] == "MODULE")
        n_topic = sum(1 for i in bs_flat if i["kind"] == "TOPIC")
        self.log(f"✓ {n_mod} modules, {n_topic} topics", "success")

    # ── Moodle scraper ────────────────────────────────────────────────────────

    async def _scrape_moodle(self, context: BrowserContext) -> Optional[list]:
        self.log("Opening Moodle in new tab…", "info")
        tab = await context.new_page()
        try:
            try:
                await tab.goto(self.moodle_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await tab.wait_for_timeout(1500)

            if "login" in tab.url.lower():
                self.log("  Moodle login required — log in in the browser.", "info")
                for i in range(120):
                    await tab.wait_for_timeout(3000)
                    if i % 10 == 9:
                        self.log(f"  Waiting for Moodle login… ({(i+1)*3}s)", "dim")
                    if "login" not in tab.url.lower():
                        self.log("✓ Moodle login detected", "success")
                        await tab.wait_for_timeout(1500)
                        break
                else:
                    self.log("✗ Moodle login timed out", "error")
                    return None

            try:
                await context.storage_state(path=SESSION_FILE)
            except Exception:
                pass

            self.log("─" * 52, "dim")
            self.log(f"  Moodle loaded at: {tab.url}", "dim")
            self.log("  Navigate to the course page, then click", "info")
            self.log("  ✅ Ready — Scrape Now  in the app.", "info")
            self.log("─" * 52, "dim")

            if self.on_moodle_waiting:
                self.on_moodle_waiting()
            if self.moodle_ready_event:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.moodle_ready_event.wait)

            if "course/view.php" not in tab.url:
                self.log("  Not on a course page — navigating to provided URL…", "dim")
                try:
                    await tab.goto(self.moodle_url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass

            try:
                await tab.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            await tab.wait_for_timeout(1000)

            self.log(f"  Scraping: {tab.url}", "dim")
            if "course/view.php" not in tab.url:
                self.log("⚠ Still not on a course/view.php page", "warning")

            try:
                items = await tab.evaluate(_JS_MOODLE_ITEMS)
            except Exception as e:
                self.log(f"✗ Scrape failed: {e}", "error")
                await tab.close()
                return None

            await tab.close()
            n_items = sum(1 for i in items if i["type"] != "SECTION")
            n_sec   = sum(1 for i in items if i["type"] == "SECTION")
            self.log(f"✓ Moodle: {n_items} items across {n_sec} sections", "success")
            return items

        except Exception as e:
            self.log(f"✗ Moodle scrape error: {e}", "error")
            try:
                await tab.close()
            except Exception:
                pass
            return None

    def _log_moodle_items(self, items: list) -> None:
        _ICONS = {
            "FILE": "📄", "ASSIGN": "📝", "QUIZ": "🧪", "URL": "🔗",
            "PAGE": "📖", "FORUM": "💬", "LABEL": "🏷 ", "FOLDER": "📁", "VIDEO": "🎥",
        }
        self.log("─" * 52, "dim")
        for item in items:
            if item["type"] == "SECTION":
                self.log("", "dim")
                self.log(f"── {item['name']}", "step")
            else:
                icon = _ICONS.get(item["type"], "  ")
                self.log(f"   {icon} {item['type']:<7}  {item['name']}", "info")
        self.log("", "dim")
        n_items = sum(1 for i in items if i["type"] != "SECTION")
        n_sec   = sum(1 for i in items if i["type"] == "SECTION")
        self.log(f"✓ {n_items} items across {n_sec} sections", "success")

    # ── Comparison report ─────────────────────────────────────────────────────

    def _log_report(self, results: list) -> None:
        _ICONS = {
            "FILE": "📄", "ASSIGN": "📝", "QUIZ": "🧪", "URL": "🔗",
            "PAGE": "📖", "FOLDER": "📁", "VIDEO": "🎥", "SECTION": "──",
        }
        counts = {"exact": 0, "fuzzy": 0, "found_in_search": 0, "found_in_content": 0, "missing": 0}

        self.log("─" * 52, "dim")
        for r in results:
            icon = _ICONS.get(r["type"], "  ")

            if r["type"] == "SECTION":
                self.log("", "dim")
                if r["status"] == "exact":
                    self.log(f"✅ {icon} {r['name']}", "step")
                elif r["status"] == "fuzzy":
                    matched, score = r["matched"]
                    self.log(f"⚠️  {icon} {r['name']}  →  \"{matched}\" ({score}%)", "warning")
                else:
                    self.log(f"❌ {icon} {r['name']}", "error")
                continue

            status = r["status"]
            counts[status] = counts.get(status, 0) + 1

            if status == "exact":
                self.log(f"   ✅ {icon} {r['name']}", "success")
            elif status == "fuzzy":
                self.log(f"   ⚠️  {icon} {r['name']}", "warning")
                self.log(f"        → \"{r['matched']}\" ({r['score']}%)", "dim")
            elif status == "found_in_search":
                self.log(f"   🔍 {icon} {r['name']}", "warning")
                self.log(f"        found via search: \"{r['matched']}\"", "dim")
            elif status == "found_in_content":
                self.log(f"   📑 {icon} {r['name']}", "warning")
                self.log(f"        found inside page: \"{r['matched']}\"", "dim")
            else:
                self.log(f"   ❌ {icon} {r['name']}", "error")

        total = sum(counts.values())
        self.log("", "dim")
        self.log("─" * 52, "dim")
        self.log(
            f"✅ {counts['exact']} exact   "
            f"⚠️  {counts['fuzzy']} fuzzy   "
            f"🔍 {counts['found_in_search']} via search   "
            f"📑 {counts['found_in_content']} in content   "
            f"❌ {counts['missing']} missing   "
            f"({total} items checked)",
            "step",
        )

    # ── Main flow ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login

        bs_only     = bool(self.bs_url)     and not self.moodle_url
        moodle_only = bool(self.moodle_url) and not self.bs_url
        full        = bool(self.bs_url)     and bool(self.moodle_url)

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context)

            bs_flat      = None
            moodle_items = None

            # ── Brightspace TOC ───────────────────────────────────────────────
            if not moodle_only:
                course_id = _extract_course_id(self.bs_url)
                if not course_id:
                    self.log(f"✗ Could not extract course ID from: {self.bs_url}", "error")
                    self.log("  Supported formats:", "dim")
                    self.log("    /d2l/le/content/<id>/home", "dim")
                    self.log("    /d2l/le/lessons/<id>", "dim")
                    self.log("    /d2l/home/<id>", "dim")
                    if self.on_complete:
                        self.on_complete()
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                    return

                self.log("─" * 52, "dim")
                bs_flat = await self._fetch_bs_toc(page, course_id)
                if not bs_flat:
                    if self.on_complete:
                        self.on_complete()
                    return

                if bs_only:
                    self._log_bs_toc(bs_flat)
                    if self.on_complete:
                        self.on_complete()
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                    return

            # ── Moodle scrape ─────────────────────────────────────────────────
            if not bs_only:
                self.log("─" * 52, "dim")
                moodle_items = await self._scrape_moodle(context)
                if not moodle_items:
                    if self.on_complete:
                        self.on_complete()
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                    return

                if moodle_only:
                    self._log_moodle_items(moodle_items)
                    if self.on_complete:
                        self.on_complete()
                    while browser.is_connected():
                        await asyncio.sleep(0.5)
                    return

            # ── Compare ───────────────────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Comparing Moodle items against Brightspace…", "info")
            results = _compare_items(moodle_items, bs_flat)

            # ── Search-bar scan for still-missing items ───────────────────────
            missing = [r for r in results if r["status"] == "missing"]
            if missing:
                self.log("─" * 52, "dim")
                found_via_search = await self._bs_content_scan(
                    page, course_id, bs_flat, missing
                )
                if found_via_search:
                    for r in results:
                        if r["status"] == "missing" and r["name"] in found_via_search:
                            r["status"]  = "found_in_search"
                            r["matched"] = found_via_search[r["name"]]

            # ── Page content scan (text search + Moodle link detector) ────────
            self.log("─" * 52, "dim")
            still_missing = [r for r in results if r["status"] == "missing"]
            found_in_content, moodle_links = await self._scan_page_content(
                page, course_id, bs_flat, still_missing
            )
            if found_in_content:
                for r in results:
                    if r["status"] == "missing" and r["name"] in found_in_content:
                        r["status"]  = "found_in_content"
                        r["matched"] = found_in_content[r["name"]]

            self._log_report(results)
            self._log_link_report(moodle_links)

            if self.on_complete:
                self.on_complete()
            while browser.is_connected():
                await asyncio.sleep(0.5)

        except Exception as e:
            self.log(f"✗ Unexpected error: {e}", "error")
            if self.on_complete:
                self.on_complete()
            raise
        finally:
            if browser.is_connected():
                await browser.close()
            await p.stop()
