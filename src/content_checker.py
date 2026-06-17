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
import os
import re
import threading
from pathlib import Path
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


# ── External tool detection ───────────────────────────────────────────────────

_EXTERNAL_TOOLS = {
    "access pearson":   "Access Pearson resource present - will need to be re-linked",
    "aktiv":            "Aktiv (Top Hat) resource present - will need to be re-linked",
    "top hat":          "Aktiv (Top Hat) resource present - will need to be re-linked",
    "cengage":          "Cengage resource present - will need to be re-linked",
    "electude":         "Electude resource present - will need to be re-linked",
    "hls":              "HLS videos to be connected during staging",
    "harris learning":  "HLS videos to be connected during staging",
    "kaltura":          "Kaltura resource present - will need to be re-linked",
    "macmillan":        "Macmillan Learning resource present - will need to be re-linked",
    "mcgraw":           "McGraw Hill resource present - will need to be re-linked",
    "myokanaganmath":   "MyOkanaganMath resource present - will need to be re-linked",
    "myokanagan":       "MyOkanaganMath resource present - will need to be re-linked",
    "stukent":          "Stukent resource present - will need to be re-linked",
    "wileyplus":        "WileyPlus resource present - will need to be re-linked",
    "wiris":            "Wiris Quizzes resource present - will need to be re-linked",
    "zoom":             "Zoom will need to be re-linked",
    "h5p":              "H5P will need to be manually uploaded by educators",
    "media collection": "Media collection resource present - will need to be relinked",
    "turnitin":         "Turnitin resource present - will need to be relinked",
    "poodll":           "Poodll resource present - will need to be relinked",
}

def _detect_external_tool(name: str, hint: str = "") -> Optional[tuple]:
    """Return (tool_label, warning_message) if the name or Moodle module class matches a known tool."""
    # H5P is identified by its modtype class, not its name
    if "hvp" in hint or "h5p" in hint:
        return "H5P", _EXTERNAL_TOOLS["h5p"]
    name_l = name.lower()
    for keyword, message in _EXTERNAL_TOOLS.items():
        if keyword in name_l:
            return keyword.title(), message
    return None


def _compare_items(moodle_items: list, bs_flat: list) -> list:
    SKIP = {"LABEL", "FORUM"}

    bs_modules = {_norm(i["title"]): i["title"] for i in bs_flat if i["kind"] == "MODULE"}
    bs_topics  = {_norm(i["title"]): i["title"] for i in bs_flat if i["kind"] == "TOPIC"}
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
            # Accordion labels carry structure we want to display — pass them through
            if item["type"] == "LABEL" and item.get("accordion_cards") is not None:
                results.append({**item, "section": current_section, "status": "label_accordion", "matched": None})
            continue

        # External tools are flagged separately — don't try to match them in BS
        if item["type"] == "EXTERNAL":
            detected = _detect_external_tool(item["name"], item.get("hint", ""))
            tool_label, warning = detected if detected else ("External Tool", "External tool - will need to be re-linked")
            results.append({**item, "section": current_section,
                             "status": "external", "matched": warning, "tool_label": tool_label})
            continue

        # Embedded items (found inside page bodies) — preserve as-is, no BS comparison
        if item.get("embedded"):
            results.append({**item, "status": "embedded"})
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
        modtype_kalturamedia: 'EXTERNAL',
        modtype_lti:          'EXTERNAL',
        modtype_hvp:          'EXTERNAL',
        modtype_h5pactivity:  'EXTERNAL',
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
            result.push({ type, name, href, hint: matched });
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
        h5p_ready_event:     threading.Event    = None,
        on_h5p_waiting:      Callable           = None,
    ):
        self.bs_url             = bs_url.strip()
        self.moodle_url         = moodle_url.strip()
        self.log                = log
        self.on_complete        = on_complete
        self.moodle_ready_event = moodle_ready_event
        self.on_moodle_waiting  = on_moodle_waiting
        self.h5p_ready_event    = h5p_ready_event
        self.on_h5p_waiting     = on_h5p_waiting

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
                        if (!r.ok) return { title, topicId, skip: true };

                        const ct = (r.headers.get('content-type') || '').toLowerCase();
                        if (!ct.includes('text/html')) return { title, topicId, skip: true };

                        const html  = await r.text();
                        const div   = document.createElement('div');
                        div.innerHTML = html;

                        const text  = (div.textContent || '').replace(/\\s+/g, ' ').toLowerCase();
                        const links = [];
                        div.querySelectorAll('a[href]').forEach(a => {
                            const href = a.getAttribute('href') || '';
                            if (href) links.push({ text: a.textContent.trim().slice(0, 120), href });
                        });

                        return { title, topicId, text, links };
                    } catch (e) {
                        return { title, topicId, skip: true };
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
                topic_id = res.get("topicId")
                for link in links:
                    href = link.get("href", "")
                    if MOODLE_HOST in href:
                        moodle_links.append({
                            "topic":    topic_title,
                            "topic_id": topic_id,
                            "text":     link["text"],
                            "href":     href,
                        })

        return found_in_content, moodle_links

    async def _upload_file_to_brightspace(
        self, bs_page: "Page", course_id: str, local_path: "Path"
    ) -> Optional[str]:
        """
        Upload a local file to the Brightspace course file store via the
        manage-files API.  Returns the URL string to embed in HTML, or None on failure.
        Files over 8 MB are skipped (base64 transport limit through CDP).
        """
        import base64, mimetypes

        data = local_path.read_bytes()
        if len(data) > 8 * 1024 * 1024:
            self.log(f"    ⚠ Skipped (file too large: {len(data)//1024} KB)", "warning")
            return None

        b64      = base64.b64encode(data).decode()
        mime     = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        filename = local_path.name

        result = await bs_page.evaluate("""async ([courseId, b64, filename, mimeType]) => {
            try {
                const binary = atob(b64);
                const bytes  = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                const blob = new Blob([bytes], { type: mimeType });

                const form = new FormData();
                form.append('file', blob, filename);

                const resp = await fetch(
                    `/d2l/api/lp/1.0/${courseId}/managefiles/file/`,
                    { method: 'POST', body: form, credentials: 'include' }
                );

                const bodyText = await resp.text().catch(() => '');
                return { status: resp.status, ok: resp.ok, body: bodyText.slice(0, 500) };
            } catch (e) {
                return { status: 0, ok: false, body: String(e) };
            }
        }""", [course_id, b64, filename, mime])

        if not result or not result.get("ok"):
            status = result.get("status", "?") if result else "?"
            body   = result.get("body", "")   if result else ""
            self.log(f"    ✗ Upload failed ({status}): {body}", "error")
            return None

        return f"/content/enforced/{course_id}/{filename}"

    async def _relink_moodle_files(
        self, context: "BrowserContext", bs_page: "Page",
        course_id: str, moodle_links: list
    ) -> None:
        """
        For every Moodle URL found in Brightspace topic HTML:
          1. Download the file from Moodle (fresh authenticated tab)
          2. Upload it to the Brightspace course file store
          3. Fetch each affected topic's HTML, replace old URL → new URL, PUT back
        """
        if not moodle_links:
            return

        self.log("", "dim")
        self.log("─" * 52, "dim")
        self.log("🔗 Re-linking Moodle files in Brightspace…", "step")

        # Group by href so each unique file is downloaded only once
        by_href: dict = {}
        for entry in moodle_links:
            by_href.setdefault(entry["href"], []).append(entry)

        unique_topics = {e["topic_id"] for e in moodle_links if e.get("topic_id")}
        self.log(
            f"  {len(by_href)} unique file(s) across {len(unique_topics)} topic(s)",
            "info",
        )

        save_dir = Path(__file__).parent.parent / "downloads" / "relink"
        save_dir.mkdir(parents=True, exist_ok=True)

        # ── 1. Download from Moodle + upload to Brightspace ──────────────────
        url_map: dict = {}   # moodle_href → brightspace_url
        moodle_tab = await context.new_page()
        try:
            for idx, (href, entries) in enumerate(by_href.items(), 1):
                label = entries[0]["text"] or href.split("/")[-1].split("?")[0][:60]
                self.log(f"  [{idx}/{len(by_href)}] {label}", "info")
                try:
                    async with moodle_tab.expect_download(timeout=20000) as dl_info:
                        await moodle_tab.goto(href, wait_until="domcontentloaded", timeout=20000)
                    dl       = await dl_info.value
                    filename = dl.suggested_filename or re.sub(r"[^\w\s\-.]", "", label).strip()[:80]
                    local    = save_dir / filename
                    await dl.save_as(str(local))
                    self.log(f"    ↓ {filename}", "dim")

                    bs_url = await self._upload_file_to_brightspace(bs_page, course_id, local)
                    if bs_url:
                        url_map[href] = bs_url
                        self.log(f"    ↑ {bs_url}", "dim")
                except Exception as e:
                    self.log(f"    ✗ {e}", "error")
        finally:
            await moodle_tab.close()

        if not url_map:
            self.log("  ⚠ No files uploaded — re-link skipped", "warning")
            return

        # ── 2. Patch each affected topic's HTML and PUT it back ──────────────
        # Build per-topic map: topic_id → {title, href→bs_url replacements}
        topics: dict = {}
        for entry in moodle_links:
            tid = entry.get("topic_id")
            if not tid or entry["href"] not in url_map:
                continue
            if tid not in topics:
                topics[tid] = {"title": entry["topic"], "replacements": {}}
            topics[tid]["replacements"][entry["href"]] = url_map[entry["href"]]

        self.log("", "dim")
        self.log(f"  Patching {len(topics)} topic(s)…", "info")
        ok_count = 0
        for topic_id, info in topics.items():
            self.log(f"  📄 {info['title']}", "step")
            replacements = info["replacements"]
            try:
                result = await bs_page.evaluate(
                    """async ([courseId, topicId, replacements]) => {
                    // GET current HTML
                    const getR = await fetch(
                        `/d2l/api/le/1.0/${courseId}/content/topics/${topicId}/file`,
                        { credentials: 'include' }
                    );
                    if (!getR.ok) return { error: `GET ${getR.status}` };

                    let html = await getR.text();
                    let replaced = 0;
                    for (const [oldUrl, newUrl] of Object.entries(replacements)) {
                        const escaped = oldUrl.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
                        const before  = html;
                        html = html.replace(new RegExp(escaped, 'g'), newUrl);
                        if (html !== before) replaced++;
                    }
                    if (replaced === 0) return { skipped: true };

                    // PUT updated HTML back
                    const blob = new Blob([html], { type: 'text/html' });
                    const form = new FormData();
                    form.append('file', blob, 'index.html');
                    const putR = await fetch(
                        `/d2l/api/le/1.0/${courseId}/content/topics/${topicId}/file`,
                        { method: 'PUT', body: form, credentials: 'include' }
                    );
                    const putBody = await putR.text().catch(() => '');
                    return putR.ok
                        ? { ok: true, replaced }
                        : { error: `PUT ${putR.status}: ${putBody.slice(0, 200)}` };
                }""",
                    [course_id, topic_id, replacements],
                )

                if result and result.get("ok"):
                    self.log(f"    ✅ {result['replaced']} link(s) re-hosted", "success")
                    ok_count += 1
                elif result and result.get("skipped"):
                    self.log("    ○ no matching links in HTML", "dim")
                else:
                    self.log(f"    ✗ {(result or {}).get('error', 'unknown')}", "error")
            except Exception as e:
                self.log(f"    ✗ {e}", "error")

        self.log("", "dim")
        self.log(
            f"✅ Re-link done — {len(url_map)}/{len(by_href)} uploaded, "
            f"{ok_count}/{len(topics)} topics patched",
            "success",
        )

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

            if "login" in tab.url.lower() or "course" not in tab.url.lower():
                try:
                    from api_config import MOODLE_USERNAME as moodle_user, MOODLE_PASSWORD as moodle_pass
                except ImportError:
                    moodle_user, moodle_pass = "", ""

                async def _click_manual_login():
                    try:
                        link = tab.locator('a[href*="saml=off"]')
                        if await link.count() > 0:
                            self.log("  Clicking Manual Login…", "info")
                            await link.first.click()
                            await tab.wait_for_load_state("domcontentloaded", timeout=10000)
                            await tab.wait_for_timeout(2000)
                            return True
                    except Exception:
                        pass
                    return False

                async def _handle_microsoft_sso():
                    """Handle Microsoft account picker + password page."""
                    if "microsoftonline.com" not in tab.url:
                        return
                    self.log("  Microsoft SSO detected — selecting account…", "info")

                    # Account picker: find the OC tile (NUsatenco@okanagan.bc.ca pattern)
                    try:
                        await tab.wait_for_timeout(2000)
                        # Click the tile containing the OC email domain
                        clicked = await tab.evaluate("""() => {
                            function findAndClick(root) {
                                for (const el of root.querySelectorAll('*')) {
                                    const text = el.textContent || '';
                                    if (text.includes('okanagan.bc.ca') && el.children.length === 0) {
                                        // Walk up to find clickable parent
                                        let p = el;
                                        for (let i = 0; i < 6; i++) {
                                            if (!p) break;
                                            if (p.tagName === 'DIV' && (p.getAttribute('role') === 'button'
                                                    || p.onclick || p.getAttribute('tabindex') === '0')) {
                                                p.click(); return true;
                                            }
                                            p = p.parentElement;
                                        }
                                        el.click(); return true;
                                    }
                                }
                                return false;
                            }
                            return findAndClick(document);
                        }""")
                        if clicked:
                            await tab.wait_for_load_state("domcontentloaded", timeout=10000)
                            await tab.wait_for_timeout(2000)
                    except Exception:
                        pass

                    # Password page
                    if "microsoftonline.com" in tab.url:
                        self.log("  Entering Microsoft password…", "info")
                        try:
                            pwd_input = tab.locator('#i0118')
                            await pwd_input.wait_for(state="visible", timeout=8000)
                            await pwd_input.fill(moodle_pass)
                            await tab.locator('#idSIButton9').click()
                            await tab.wait_for_load_state("domcontentloaded", timeout=15000)
                            await tab.wait_for_timeout(2000)
                        except Exception as e:
                            self.log(f"  ⚠ Microsoft password step failed: {e}", "warning")

                # Step 1: click Manual Login
                await _click_manual_login()

                # Step 2a: Microsoft SSO appeared (50/50 chance)
                await _handle_microsoft_sso()

                # Step 2b: loginredirect=1 — stale session, log out then click Manual Login again
                if "loginredirect" in tab.url:
                    self.log("  Clearing stale session (Log out)…", "info")
                    try:
                        logout_btn = tab.locator('button[type="submit"].btn-primary')
                        if await logout_btn.count() > 0:
                            await logout_btn.first.click()
                            await tab.wait_for_load_state("domcontentloaded", timeout=10000)
                            await tab.wait_for_timeout(2000)
                    except Exception:
                        pass
                    await _click_manual_login()
                    await _handle_microsoft_sso()

                # Step 3: Moodle manual login form (username + password)
                if "saml=off" in tab.url or ("login" in tab.url.lower() and "microsoftonline" not in tab.url):
                    if moodle_user and moodle_pass:
                        self.log("  Filling Moodle credentials…", "info")
                        try:
                            await tab.locator('#username').fill(moodle_user)
                            await tab.locator('#password').fill(moodle_pass)
                            await tab.locator('#loginbtn').click()
                            await tab.wait_for_load_state("domcontentloaded", timeout=15000)
                            await tab.wait_for_timeout(2000)
                            self.log("✓ Moodle login complete", "success")
                        except Exception as e:
                            self.log(f"✗ Auto-login failed: {e}", "error")
                            return None
                    else:
                        self.log("  Credentials not set in api_config.py — log in manually.", "warning")
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

            # Deep scan: (1) scan label bodies on the course page itself,
            # (2) then navigate to each PAGE topic for its body HTML
            inline_embedded = await self._scan_moodle_labels_inline(tab, items)
            embedded        = await self._scan_moodle_page_bodies(tab, items)

            # Accordion LABEL items must be inserted after their SECTION item so
            # _compare_items sees them in section order (appending at end misattributes
            # them all to the last section).
            accordions   = [i for i in inline_embedded if i.get("accordion_cards") is not None]
            flat_embedded = [i for i in inline_embedded if i.get("accordion_cards") is None]

            acc_by_section: dict = {}
            for a in accordions:
                acc_by_section.setdefault(a.get("section", ""), []).append(a)

            # Count accordion items per section so we can skip the matching
            # plain LABEL items the main scraper already created for them.
            acc_counts: dict = {sec: len(lst) for sec, lst in acc_by_section.items()}
            skipped_labels: dict = {}

            ordered: list = []
            _cur_sec = ""
            for item in items:
                if item["type"] == "SECTION":
                    _cur_sec = item["name"]
                    ordered.append(item)
                    ordered.extend(acc_by_section.get(item["name"], []))
                elif (item["type"] == "LABEL"
                      and item.get("accordion_cards") is None
                      and skipped_labels.get(_cur_sec, 0) < acc_counts.get(_cur_sec, 0)):
                    # Superseded by accordion item in same section — drop it
                    skipped_labels[_cur_sec] = skipped_labels.get(_cur_sec, 0) + 1
                else:
                    ordered.append(item)

            items = ordered + flat_embedded + embedded

            # If there are H5P items, pause so the user can check the browser
            # (e.g. switch out of preview mode) before downloads start.
            h5p_pending = [
                i for i in items
                if i.get("type") == "EXTERNAL"
                and ("hvp" in i.get("hint", "") or "h5p" in i.get("hint", ""))
                and i.get("href")
            ]
            if h5p_pending:
                self.log("", "dim")
                self.log("─" * 52, "dim")
                self.log(f"🎮 {len(h5p_pending)} H5P file(s) to download.", "step")
                # Auto-switch to Instructor role so edit controls appear
                await self._switch_to_instructor_role(tab)

            if h5p_pending and self.on_h5p_waiting:
                self.log(
                    "  Verify the browser looks right, then click"
                    " ✅ Ready — Download H5P in the app.",
                    "info",
                )
                self.on_h5p_waiting()
                if self.h5p_ready_event:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self.h5p_ready_event.wait)

            # H5P: enable download on each activity so files can be fetched
            await self._enable_h5p_downloads(context, items)

            # Stage 2: download all embedded files from Moodle
            await self._download_moodle_files(tab, items)

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

    async def _download_moodle_files(self, tab, items: list) -> None:
        """
        Download all pluginfile.php FILE items (embedded + top-level resources)
        using the already-authenticated Playwright session.
        Saves to downloads/files/<section>/<filename>.
        """
        file_items = [
            i for i in items
            if i.get("type") == "FILE"
            and i.get("href")
            and "pluginfile.php" in i.get("href", "")
        ]

        if not file_items:
            return

        self.log("", "dim")
        self.log("─" * 52, "dim")
        self.log(f"📥 Downloading {len(file_items)} files from Moodle…", "step")

        save_root = Path(__file__).parent.parent / "downloads" / "files"
        save_root.mkdir(parents=True, exist_ok=True)

        success = 0
        for idx, item in enumerate(file_items, 1):
            name    = item.get("name", "file")
            href    = item["href"]
            section = re.sub(r'[^\w\s\-]', '', item.get("section", "unsorted")).strip()[:60] or "unsorted"

            self.log(f"  [{idx}/{len(file_items)}] {name}", "info")
            try:
                section_dir = save_root / section
                section_dir.mkdir(parents=True, exist_ok=True)

                async with tab.expect_download(timeout=20000) as dl_info:
                    await tab.goto(href, wait_until="domcontentloaded", timeout=20000)

                download  = await dl_info.value
                suggested = download.suggested_filename or re.sub(r'[^\w\s\-.]', '', name).strip()[:80]
                save_path = section_dir / suggested

                # avoid overwriting if duplicate filename in same section
                if save_path.exists():
                    stem, suffix = save_path.stem, save_path.suffix
                    save_path = section_dir / f"{stem}_{idx}{suffix}"

                await download.save_as(str(save_path))
                self.log(f"    💾 {suggested}", "success")
                success += 1

            except Exception as e:
                self.log(f"    ✗ Failed: {e}", "error")

        self.log("", "dim")
        self.log(f"  Files: {success}/{len(file_items)} downloaded", "success")
        if success > 0:
            self.log(f"  Saved to: downloads/files/", "dim")

    async def _switch_to_instructor_role(self, tab) -> None:
        """
        Click through Moodle's Switch role to → Instructor flow.
        Required so teacher edit controls (including H5P download checkbox)
        are visible. Safe to call when already in Instructor role — Moodle
        just re-applies and redirects back.
        """
        self.log("  → Switching to Instructor role…", "dim")
        try:
            # Open user menu dropdown
            toggle = tab.locator('#user-menu-toggle')
            if await toggle.count() == 0:
                self.log("  ⚠ User menu not found — role switch skipped", "warning")
                return
            await toggle.first.click()
            await tab.wait_for_timeout(600)

            # Find the "Switch role to..." link and get its href
            switch_link = tab.locator('a[href*="switchrole.php"]').first
            if await switch_link.count() == 0:
                self.log("  ✓ No switchrole link found — skipping", "dim")
                # Close the dropdown by pressing Escape
                await tab.keyboard.press("Escape")
                return
            href = await switch_link.get_attribute("href")
            await tab.goto(href, wait_until="domcontentloaded", timeout=15000)
            await tab.wait_for_timeout(800)

            # Click the Instructor role button on the selection page
            instructor_btn = tab.locator('button:has-text("Instructor")')
            if await instructor_btn.count() == 0:
                self.log("  ⚠ Instructor button not found on role page", "warning")
                return
            await instructor_btn.first.click()
            await tab.wait_for_load_state("domcontentloaded", timeout=15000)
            await tab.wait_for_timeout(1000)
            self.log("  ✓ Switched to Instructor role", "success")
        except Exception as e:
            self.log(f"  ⚠ Role switch failed: {e}", "warning")

    async def _enable_h5p_downloads(self, context, items: list) -> None:
        """
        For each H5P activity: open Settings, tick Allow download, Save and display.
        Each item gets a fresh page so a crashed/stalled tab can't affect the rest.
        """
        h5p_items = [
            i for i in items
            if i.get("type") == "EXTERNAL"
            and ("hvp" in i.get("hint", "") or "h5p" in i.get("hint", ""))
            and i.get("href")
        ]

        if not h5p_items:
            return

        self.log("", "dim")
        self.log("─" * 52, "dim")
        self.log(f"🎮 H5P activities found: {len(h5p_items)}", "step")
        self.log("  Enabling download on each…", "dim")

        success = 0
        for idx, item in enumerate(h5p_items, 1):
            name = item["name"]
            url  = item["href"]
            self.log(f"  [{idx}/{len(h5p_items)}] {name}", "info")
            tab = await context.new_page()
            try:
                # Step 1: navigate to H5P activity
                await tab.goto(url, wait_until="domcontentloaded", timeout=20000)
                await tab.wait_for_timeout(1000)

                # Step 2: navigate to Settings (strip &return=1 so Save and display goes to view.php)
                self.log(f"    → Going to Settings…", "dim")
                settings = tab.locator('a[href*="modedit.php?update="]')
                if await settings.count() == 0:
                    self.log(f"    ⚠ No Settings link — check teacher access", "warning")
                    continue
                settings_href = await settings.first.get_attribute("href")
                settings_href = re.sub(r'&return=\d+', '', settings_href)
                await tab.goto(settings_href, wait_until="domcontentloaded", timeout=15000)
                await tab.wait_for_timeout(800)

                # Step 3: expand ALL collapsed sections on the settings page
                # (section IDs are collapseElement-0, -1, -2 … vary by module type)
                collapsed = tab.locator('[id^="collapseElement-"][aria-expanded="false"]')
                n_collapsed = await collapsed.count()
                if n_collapsed > 0:
                    self.log(f"    → Expanding {n_collapsed} collapsed section(s)…", "dim")
                    for i in range(n_collapsed):
                        try:
                            await collapsed.nth(i).click()
                            await tab.wait_for_timeout(300)
                        except Exception:
                            pass

                # Step 4: check Allow download if not already checked
                # mod/hvp           → #id_export
                # mod/h5pactivity   → #id_enabledownload  OR  #id_displayopt_export
                self.log(f"    → Checking Allow download checkbox…", "dim")
                checkbox = tab.locator(
                    '#id_export, #id_enabledownload, #id_displayopt_export'
                )
                if await checkbox.count() == 0:
                    self.log(f"    ⚠ Allow download checkbox not found", "warning")
                    continue
                if not await checkbox.first.is_checked():
                    await checkbox.first.check()
                    self.log(f"    ✓ Download enabled", "success")
                else:
                    self.log(f"    ✓ Already enabled", "dim")

                # Step 5: Save and display (scroll into view — button is below the fold)
                self.log(f"    → Clicking Save and display…", "dim")
                save_btn = tab.locator('#id_submitbutton')
                if await save_btn.count() == 0:
                    self.log(f"    ⚠ Save and display button not found", "warning")
                    continue
                await save_btn.first.scroll_into_view_if_needed()
                await tab.wait_for_timeout(500)
                await save_btn.first.click()
                try:
                    await tab.wait_for_url(lambda u: "modedit.php" not in u, timeout=15000)
                except Exception:
                    self.log(f"    ⚠ Save didn't navigate away — still on {tab.url[:80]}", "warning")
                    continue
                self.log(f"    ✓ Saved — on: {tab.url[:60]}", "dim")
                await tab.wait_for_timeout(2000)

                # Step 6: click Reuse button — scroll first (H5P iframe lazy-loads when visible)
                await tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await tab.wait_for_timeout(3000)

                # Dismiss "Data Reset" dialog if it appears
                for frame in tab.frames:
                    try:
                        ok_btn = frame.locator('.h5p-dialog-ok-button')
                        if await ok_btn.count() > 0:
                            self.log(f"    → Dismissing Data Reset dialog…", "dim")
                            await ok_btn.first.click()
                            await tab.wait_for_timeout(800)
                            break
                    except Exception:
                        pass

                reuse_clicked = False
                for frame in tab.frames:
                    try:
                        btn = frame.locator('li.h5p-export button')
                        if await btn.count() > 0:
                            await btn.first.click()
                            reuse_clicked = True
                            break
                    except Exception:
                        pass

                if not reuse_clicked:
                    self.log(f"    ⚠ Reuse button not found in any frame", "warning")
                    continue

                # Step 7: wait for download dialog then click "Download as an .h5p file"
                save_dir = Path(__file__).parent.parent / "downloads" / "h5p"
                save_dir.mkdir(parents=True, exist_ok=True)
                safe_name = re.sub(r'[^\w\s\-]', '', name).strip()[:80]
                save_path = save_dir / f"{safe_name}.h5p"

                dl_frame = None
                for _ in range(10):
                    await tab.wait_for_timeout(500)
                    for frame in tab.frames:
                        try:
                            if await frame.locator('.h5p-download-button').count() > 0:
                                dl_frame = frame
                                break
                        except Exception:
                            pass
                    if dl_frame:
                        break

                if not dl_frame:
                    self.log(f"    ⚠ Download dialog did not appear", "warning")
                else:
                    try:
                        async with tab.expect_download(timeout=15000) as dl_info:
                            await dl_frame.locator('.h5p-download-button').first.click()
                        download = await dl_info.value
                        await download.save_as(str(save_path))
                        self.log(f"    💾 Saved: {safe_name}.h5p", "success")
                        success += 1
                    except Exception as e:
                        self.log(f"    ✗ Download failed: {e}", "error")

            except Exception as e:
                self.log(f"    ✗ Failed: {e}", "error")
            finally:
                try:
                    await tab.close()
                except Exception:
                    pass

        self.log("", "dim")
        self.log(f"  H5P: {success}/{len(h5p_items)} downloaded", "success")
        if success > 0:
            self.log(f"  Saved to: downloads/h5p/", "dim")

    async def _scan_moodle_labels_inline(self, tab, items: list) -> list:
        """
        Scan the current course page (already loaded) for pluginfile.php links
        and Kaltura iframes embedded inside label activity blocks.
        Also detects Bootstrap accordion structure and stores raw body HTML.
        Labels render their content inline — they have no separate page to navigate to.
        """
        self.log("  Scanning label bodies on course page…", "dim")
        try:
            results = await tab.evaluate("""() => {
                const found = [];

                function linkType(href) {
                    if (!href) return 'URL';
                    if (href.includes('pluginfile.php') || href.includes('mod/resource/view.php')) return 'FILE';
                    if (href.includes('mod/assign/view.php'))   return 'ASSIGN';
                    if (href.includes('mod/quiz/view.php'))     return 'QUIZ';
                    if (href.includes('mod/hvp/view.php') || href.includes('mod/h5pactivity/view.php')) return 'EXTERNAL';
                    if (href.includes('mod/url/view.php'))      return 'URL';
                    return 'URL';
                }

                document.querySelectorAll('li.section, li.section.main').forEach(section => {
                    const secEl   = section.querySelector('.sectionname, h3, h4');
                    const secName = secEl ? secEl.textContent.trim() : '';

                    section.querySelectorAll('li.activity.modtype_label').forEach(label => {
                        const nameEl    = label.querySelector('.instancename, .activityname a, a');
                        const labelName = nameEl ? nameEl.textContent.trim().replace(/\\s{2,}.*$/, '').trim() : '(label)';
                        const body      = label.querySelector(
                            '.contentafterlink, .description, .no-overflow, .labelcontent, .activitybody'
                        );
                        if (!body) return;

                        // ── Accordion detection ──────────────────────────────
                        const cards = body.querySelectorAll('.card');
                        if (cards.length > 0) {
                            const accordion_cards = [];
                            cards.forEach(card => {
                                const headerEl = card.querySelector('.card-header button, .card-header h5, .card-header');
                                const title    = headerEl ? headerEl.textContent.trim().replace(/\\s+/g, ' ') : '(card)';
                                const links    = [];
                                card.querySelectorAll('a[href]').forEach(a => {
                                    const href = a.href || '';
                                    if (!href) return;
                                    if (href.includes('readspeaker') || href.includes('docreader')) return;
                                    const name = a.textContent.trim()
                                              || a.getAttribute('title')
                                              || a.getAttribute('aria-label');
                                    if (!name) return; // icon-only / invisible links — skip
                                    links.push({ name, href, type: linkType(href) });
                                });
                                accordion_cards.push({ title, links });
                            });
                            found.push({
                                type: 'LABEL', name: '(accordion)',
                                section: secName, parent_topic: labelName,
                                accordion_cards,
                                body_html: body.innerHTML,
                            });
                            return; // don't also scan this label for flat pluginfile links
                        }

                        // ── Flat label: scan for pluginfile.php links ────────
                        body.querySelectorAll('a[href*="pluginfile.php"]').forEach(a => {
                            const href = a.href || '';
                            if (href.includes('readspeaker') || href.includes('docreader')) return;
                            const text = a.textContent.trim() || href.split('/').pop().split('?')[0];
                            if (href) found.push({
                                type: 'FILE', name: text, href,
                                embedded: true, section: secName, parent_topic: labelName
                            });
                        });
                        body.querySelectorAll('iframe[src*="kaltura"]').forEach(f => {
                            const src = f.src || '';
                            const m   = src.match(/entryid\\/([^\\/&?]+)/i)
                                     || src.match(/entry_id=([^&]+)/i)
                                     || src.match(/\\/([01]_[a-z0-9]+)(?:\\/|$)/i);
                            const entryId = m ? m[1] : '';
                            const name    = entryId ? 'Kaltura video [' + entryId + ']' : '(embedded video)';
                            found.push({
                                type: 'VIDEO', name, href: src, entryId,
                                embedded: true, section: secName, parent_topic: labelName
                            });
                        });
                    });
                });
                return found;
            }""")
        except Exception as e:
            self.log(f"  ⚠ Label inline scan failed: {e}", "warning")
            return []

        # Deduplicate flat embedded items (accordion items are always unique)
        seen, dedup = set(), []
        for r in results:
            if r.get("accordion_cards") is not None:
                dedup.append(r)
                continue
            key = r.get("href") or r.get("name")
            if key and key not in seen:
                seen.add(key)
                dedup.append(r)

        files      = sum(1 for r in dedup if r["type"] == "FILE" and not r.get("accordion_cards"))
        videos     = sum(1 for r in dedup if r["type"] == "VIDEO")
        accordions = sum(1 for r in dedup if r.get("accordion_cards") is not None)
        if dedup:
            self.log(f"  Labels: {files} embedded file(s), {videos} embedded video(s), {accordions} accordion(s)", "dim")
        return dedup

    async def _scan_moodle_page_bodies(self, tab, items: list) -> list:
        """
        Visit each PAGE topic and scan its HTML body for:
          - pluginfile.php links  → embedded FILE
          - Kaltura iframes       → embedded VIDEO with entryId
        Returns a flat list of additional items to append to the main list.
        """
        # Build section context so embedded items know where they came from
        section_of = {}
        current_section = ""
        for item in items:
            if item["type"] == "SECTION":
                current_section = item["name"]
            else:
                section_of[item.get("href", "")] = current_section

        # LABELs are inline on the course page — handled by _scan_moodle_labels_inline
        # FILE hrefs point to direct downloads, not scannable HTML pages
        pages_to_scan = [
            i for i in items
            if i["type"] == "PAGE" and i.get("href") and not i.get("embedded")
        ]

        if not pages_to_scan:
            return []

        self.log(f"  Scanning {len(pages_to_scan)} page(s) for embedded files and videos…", "info")
        found = []
        visit_log: list[tuple[str, str, list]] = []  # (section, name, hits)

        for item in pages_to_scan:
            url     = item["href"]
            section = section_of.get(url, "")
            hits    = []
            try:
                await tab.goto(url, wait_until="domcontentloaded", timeout=20000)
                await tab.wait_for_timeout(800)

                results = await tab.evaluate("""() => {
                    const found = [];

                    // pluginfile.php links
                    document.querySelectorAll('a[href*="pluginfile.php"]').forEach(a => {
                        const href = a.href || '';
                        const text = a.textContent.trim() || href.split('/').pop().split('?')[0];
                        if (href) found.push({ type: 'FILE', name: text, href, embedded: true });
                    });

                    // Kaltura iframes — extract entryId from src
                    document.querySelectorAll('iframe[src*="kaltura"]').forEach(f => {
                        const src = f.src || '';
                        const m   = src.match(/entryid\\/([^\\/&?]+)/i)
                                 || src.match(/entry_id=([^&]+)/i)
                                 || src.match(/\\/([01]_[a-z0-9]+)(?:\\/|$)/i);
                        const entryId = m ? m[1] : '';
                        const name    = entryId ? 'Kaltura video [' + entryId + ']' : '(embedded video)';
                        found.push({ type: 'VIDEO', name, href: src, entryId, embedded: true });
                    });

                    // video.js / KMC players that don't use iframe
                    document.querySelectorAll('[id*="kaltura_player"], [id*="kplayer"]').forEach(el => {
                        const entryId = el.getAttribute('data-entry-id') || '';
                        const name    = entryId ? 'Kaltura video [' + entryId + ']' : '(embedded kaltura player)';
                        found.push({ type: 'VIDEO', name, href: '', entryId, embedded: true });
                    });

                    return found;
                }""")

                for r in results:
                    r["section"]      = section
                    r["parent_topic"] = item["name"]
                    found.append(r)
                    hits.append(r)

            except Exception as e:
                hits = [{"type": "ERROR", "name": str(e)}]

            visit_log.append((section, item["name"], hits))

        # Deduplicate by href
        seen  = set()
        dedup = []
        for r in found:
            key = r.get("href") or r.get("name")
            if key and key not in seen:
                seen.add(key)
                dedup.append(r)

        files  = sum(1 for r in dedup if r["type"] == "FILE")
        videos = sum(1 for r in dedup if r["type"] == "VIDEO")
        if dedup:
            self.log(f"  Found {files} embedded file(s), {videos} embedded video(s)", "info")

        # Per-page visit log
        self.log("", "dim")
        self.log("  Page scan log:", "dim")
        for section, name, hits in visit_log:
            sec_label = f"[{section}] " if section else ""
            if not hits:
                self.log(f"    ○ {sec_label}{name}", "dim")
            else:
                icons = {"FILE": "📄", "VIDEO": "🎥", "ERROR": "✗"}
                hit_strs = []
                for h in hits:
                    icon = icons.get(h["type"], "?")
                    hit_strs.append(f"{icon} {h['name']}")
                self.log(f"    ● {sec_label}{name}  →  {',  '.join(hit_strs)}", "info")

        return dedup

    def _log_moodle_items(self, items: list) -> None:
        _ICONS = {
            "FILE": "📄", "ASSIGN": "📝", "QUIZ": "🧪", "URL": "🔗",
            "PAGE": "📖", "FORUM": "💬", "LABEL": "🏷 ", "FOLDER": "📁",
            "VIDEO": "🎥", "EXTERNAL": "🔌",
        }
        top_level = [i for i in items if not i.get("embedded")]
        embedded  = [i for i in items if i.get("embedded")]

        # Group embedded items by section for inline display
        from collections import defaultdict
        emb_by_section: dict = defaultdict(list)
        for e in embedded:
            emb_by_section[e.get("section", "")].append(e)

        self.log("─" * 52, "dim")
        current_sec = ""
        for item in top_level:
            if item["type"] == "SECTION":
                # Before moving to the next section, flush embedded items for the current one
                for e in emb_by_section.get(current_sec, []):
                    icon   = _ICONS.get(e["type"], "  ")
                    parent = e.get("parent_topic", "")
                    entry  = e.get("entryId", "")
                    extra  = f"  [entryId: {entry}]" if entry else ""
                    label  = f" (in: {parent})" if parent else ""
                    self.log(f"   {icon} {e['type']:<7}  {e['name']}{extra}{label}", "dim")
                current_sec = item["name"]
                self.log("", "dim")
                self.log(f"── {item['name']}", "step")
            elif item["type"] == "LABEL" and item.get("accordion_cards") is not None:
                parent = item.get("parent_topic", "")
                label_line = f"   🏷  [accordion: {parent}]" if parent else "   🏷  [accordion]"
                self.log(label_line, "dim")
                for card in item["accordion_cards"]:
                    self.log(f"       📂 {card['title']}", "info")
                    for lnk in card.get("links", []):
                        lnk_icon = _ICONS.get(lnk.get("type", "URL"), "🔗")
                        self.log(f"          {lnk_icon} {lnk['name']}", "dim")
            elif item["type"] == "EXTERNAL":
                detected = _detect_external_tool(item["name"], item.get("hint", ""))
                tool_label, warning = detected if detected else ("External Tool", "External tool - will need to be re-linked")
                self.log(f"   🔌 EXTERNAL  {item['name']}", "warning")
                self.log(f"      → Detected as: {tool_label}", "dim")
                self.log(f"      ⚠ {warning}", "warning")
            else:
                icon = _ICONS.get(item["type"], "  ")
                self.log(f"   {icon} {item['type']:<7}  {item['name']}", "info")

        # Flush embedded items for the last section
        for e in emb_by_section.get(current_sec, []):
            icon   = _ICONS.get(e["type"], "  ")
            parent = e.get("parent_topic", "")
            entry  = e.get("entryId", "")
            extra  = f"  [entryId: {entry}]" if entry else ""
            label  = f" (in: {parent})" if parent else ""
            self.log(f"   {icon} {e['type']:<7}  {e['name']}{extra}{label}", "dim")

        self.log("", "dim")
        n_items = sum(1 for i in top_level if i["type"] != "SECTION")
        n_sec   = sum(1 for i in top_level if i["type"] == "SECTION")
        self.log(f"✓ {n_items} items across {n_sec} sections  (+{len(embedded)} embedded)", "success")

    # ── Comparison report ─────────────────────────────────────────────────────

    def _log_report(self, results: list) -> None:
        _ICONS = {
            "FILE": "📄", "ASSIGN": "📝", "QUIZ": "🧪", "URL": "🔗",
            "PAGE": "📖", "FOLDER": "📁", "VIDEO": "🎥", "EXTERNAL": "🔌", "SECTION": "──",
        }
        counts = {"exact": 0, "fuzzy": 0, "found_in_search": 0, "found_in_content": 0, "missing": 0}
        external_items = []

        # Build name→result lookup so accordion cards can show status inline
        result_by_name = {_norm(r["name"]): r for r in results
                          if r["type"] not in ("SECTION", "LABEL") and not r.get("embedded")}

        # Names consumed inside an accordion — suppress from flat list to avoid duplicates
        accordion_consumed: set = set()
        for r in results:
            if r.get("status") == "label_accordion":
                for card in r.get("accordion_cards", []):
                    for lnk in card.get("links", []):
                        accordion_consumed.add(_norm(lnk["name"]))

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

            # ── Accordion label — nested card display ──────────────────────────
            if r.get("status") == "label_accordion":
                self.log(f"   🏷  [accordion]", "dim")
                for card in r.get("accordion_cards", []):
                    self.log(f"       📂 {card['title']}", "info")
                    for lnk in card.get("links", []):
                        matched_r  = result_by_name.get(_norm(lnk["name"]))
                        lnk_icon   = _ICONS.get(matched_r["type"] if matched_r else lnk.get("type", "URL"), "🔗")
                        if matched_r:
                            st = matched_r["status"]
                            if st == "exact":
                                self.log(f"          ✅ {lnk_icon} {lnk['name']}", "success")
                                counts["exact"] += 1
                            elif st == "fuzzy":
                                self.log(f"          ⚠️  {lnk_icon} {lnk['name']}", "warning")
                                self.log(f"               → \"{matched_r['matched']}\" ({matched_r['score']}%)", "dim")
                                counts["fuzzy"] += 1
                            elif st == "found_in_search":
                                self.log(f"          🔍 {lnk_icon} {lnk['name']}", "warning")
                                counts["found_in_search"] += 1
                            elif st == "found_in_content":
                                self.log(f"          📑 {lnk_icon} {lnk['name']}", "warning")
                                counts["found_in_content"] += 1
                            elif st == "external":
                                self.log(f"          🔌 {lnk_icon} {lnk['name']}", "warning")
                                external_items.append(matched_r)
                            else:
                                self.log(f"          ❌ {lnk_icon} {lnk['name']}", "error")
                                counts["missing"] += 1
                        else:
                            # External URL or unknown — show dimmed
                            self.log(f"          🌐 {lnk['name']}", "dim")
                continue

            if r["status"] == "external":
                external_items.append(r)
                self.log(f"   🔌 {r['name']}", "warning")
                continue

            # Embedded items are shown in their own summary block at the bottom
            if r.get("embedded"):
                continue

            # Skip items already rendered inside an accordion card
            if _norm(r["name"]) in accordion_consumed:
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

        # ── External tools summary ─────────────────────────────────────────────
        if external_items:
            self.log("", "dim")
            self.log("─" * 52, "dim")
            self.log(f"🔌 External Tools Detected ({len(external_items)})", "warning")
            self.log("   These will need manual attention after migration:", "dim")
            self.log("", "dim")
            seen_warnings: set = set()
            for r in external_items:
                warning     = r["matched"]
                tool_label  = r.get("tool_label", "External Tool")
                section     = r.get("section", "")
                prefix      = f"[{section}] " if section else ""
                self.log(f"   🔌 {prefix}{r['name']}", "warning")
                self.log(f"      → Detected as: {tool_label}", "dim")
                if warning not in seen_warnings:
                    self.log(f"      ⚠ {warning}", "warning")
                    seen_warnings.add(warning)

        # ── Embedded files / videos summary ────────────────────────────────────
        embedded = [r for r in results if r.get("embedded")]
        if embedded:
            emb_files  = [r for r in embedded if r["type"] == "FILE"]
            emb_videos = [r for r in embedded if r["type"] == "VIDEO"]
            self.log("", "dim")
            self.log("─" * 52, "dim")
            self.log(f"📎 Embedded Content Found ({len(embedded)} items: {len(emb_files)} files, {len(emb_videos)} videos)", "step")
            self.log("   Discovered inside page bodies — need migration:", "dim")
            self.log("", "dim")
            for r in emb_files:
                section = r.get("section", "")
                parent  = r.get("parent_topic", "")
                loc     = f"[{section} › {parent}]" if section and parent else f"[{parent or section}]"
                self.log(f"   📄 {loc}  {r['name']}", "info")
            for r in emb_videos:
                section  = r.get("section", "")
                parent   = r.get("parent_topic", "")
                loc      = f"[{section} › {parent}]" if section and parent else f"[{parent or section}]"
                entry    = r.get("entryId", "")
                extra    = f"  [entryId: {entry}]" if entry else "  ⚠ no entryId"
                self.log(f"   🎥 {loc}  {r['name']}{extra}", "info")

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

            if moodle_links and getattr(self, "do_relink", False):
                await self._relink_moodle_files(context, page, course_id, moodle_links)

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
