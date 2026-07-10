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
import time
from pathlib import Path
from typing import Callable, List, Optional

from playwright.async_api import BrowserContext, Page
from config import SESSION_FILE
from js_helpers import DEEP_FIND_JS
from h5p_handler import H5PHandler
from content_matcher import _norm, _numbers_conflict, _digitize, _containment_match, _detect_external_tool, _compare_items, _EXTERNAL_TOOLS, _WORD_NUMS


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


# ── Moodle structured scraper JS (same logic as style_migrator) ───────────────

_JS_MOODLE_ITEMS = """() => {
    const TYPES = {
        modtype_resource:     'FILE',
        modtype_assign:       'ASSIGN',
        modtype_quiz:         'QUIZ',
        modtype_url:          'URL',
        modtype_page:         'PAGE',
        modtype_book:         'PAGE',
        modtype_forum:        'FORUM',
        modtype_label:        'LABEL',
        modtype_folder:       'FOLDER',
        modtype_kalturamedia: 'EXTERNAL',
        modtype_kalvidres:    'VIDEO',
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
        let kframeTitle = '';
        if (kframe) {
            const m = (kframe.src || '').match(/entryid\\/([^\\/]+)/);
            if (m) entryId = m[1];
            kframeTitle = kframe.getAttribute('title') || '';
        }

        const rawText = body.textContent.trim().replace(/\\s+/g, ' ');
        const isOnlyNoise = /^Video Player is loading/.test(rawText);
        if (isOnlyNoise || (hasVideo && rawText.replace(/Video Player.*/, '').trim().length < 5)) {
            return { type: 'VIDEO', name: kframeTitle || (entryId ? 'Kaltura video [' + entryId + ']' : 'Kaltura Video') };
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
            name: (heading && heading.textContent.trim()) || '(unnamed section)',
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
        h5p_ready_event:      threading.Event    = None,
        on_h5p_waiting:       Callable           = None,
        file_checklist_event: threading.Event    = None,
        on_file_checklist:    Callable           = None,
        confirm_fn:           Optional[Callable] = None,
        notify_fn:            Optional[Callable] = None,
        bs_username:          str               = "",
        bs_password:          str               = "",
        sso_email:            str               = "",
        sso_password:         str               = "",
        moodle_username:      str               = "",
        moodle_password:      str               = "",
        verbose:              bool              = False,
    ):
        self.bs_url                 = bs_url.strip()
        self.moodle_url             = moodle_url.strip()
        self._verbose               = verbose
        self.stop_flag              = [False]
        self.log                    = self._make_log_filter(log)
        self.on_complete            = on_complete
        self.moodle_ready_event     = moodle_ready_event
        self.on_moodle_waiting      = on_moodle_waiting
        self.h5p_ready_event        = h5p_ready_event
        self.on_h5p_waiting         = on_h5p_waiting
        self.file_checklist_event   = file_checklist_event
        self.on_file_checklist      = on_file_checklist
        self.file_checklist_result  = []
        self.confirm_fn             = confirm_fn
        self.notify_fn              = notify_fn
        self.do_h5p_embed           = False
        self.bs_username            = bs_username
        self.bs_password            = bs_password
        self.sso_email              = sso_email
        self.sso_password           = sso_password
        self.moodle_username        = moodle_username
        self.moodle_password        = moodle_password
        self._summary               = {}
        self._h5p = H5PHandler(
            log=self.log,
            eval_in_any_frame=self._eval_in_any_frame,
            auto_dismiss=self._auto_dismiss,
            confirm=self._confirm,
            diagnose=self._diagnose,
            verify_topic_in_module=self._verify_topic_in_module,
            summary=self._summary,
            notify=self._notify,
            should_stop=lambda: self.stop_flag[0],
        )

    def _make_log_filter(self, log_fn: Callable) -> Callable:
        """Wrap log function to filter out verbose messages."""
        def filtered_log(msg: str, tag: str = "info"):
            if not self._verbose and tag in ("dim", "info"):
                return
            log_fn(msg, tag)
        return filtered_log

    def _notify(self, title: str, text: str) -> None:
        """Fire-and-forget popup to the GUI (no answer expected). Thread-safe —
        the panel's notify_fn just drops a message on the log queue."""
        if self.notify_fn:
            self.notify_fn(title, text)

    async def _confirm(self, msg: str) -> bool:
        if not self.confirm_fn:
            return True
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.confirm_fn, msg)

    async def _eval_in_any_frame(self, tab, js: str) -> bool:
        """Run js in each frame until one returns truthy. Returns True if any frame matched."""
        for frame in tab.frames:
            try:
                result = await frame.evaluate(js)
                if result:
                    return True
            except Exception:
                pass
        return False

    async def _diagnose(self, tab, keywords: list) -> None:
        """
        Scan all frames + shadow DOM for interactive elements matching keywords.
        Only logs buttons/links/menu-items with a meaningful aria/cmd/text attribute.
        Capped at 10 results so the log stays readable.
        """
        kw_js = "[" + ", ".join(f'"{k.lower()}"' for k in keywords) + "]"
        scan_js = f"""() => {{
            var keywords = {kw_js};
            var INTERACTIVE = {{'BUTTON':1,'A':1,'INPUT':1,'D2L-BUTTON':1,
                'D2L-HTMLEDITOR-MENU-ITEM':1,'D2L-HTMLEDITOR-BUTTON':1,
                'D2L-HTMLEDITOR-BUTTON-MENU':1,'D2L-BUTTON-ICON':1}};
            function matches(e) {{
                var tag  = (e.tagName || '').toUpperCase();
                if (!INTERACTIVE[tag]) return false;
                var aria = (e.getAttribute && e.getAttribute('aria-label') || '').toLowerCase();
                var cmd  = (e.getAttribute && e.getAttribute('cmd') || '').toLowerCase();
                var txt  = (e.textContent || '').trim().toLowerCase().slice(0, 60);
                if (!aria && !cmd && !txt) return false;
                var str  = aria + ' ' + cmd + ' ' + txt;
                return keywords.some(function(k) {{ return str.includes(k); }});
            }}
            function walk(root, hits, depth) {{
                if (!root || depth <= 0 || hits.length >= 10) return;
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var i = 0; i < all.length && hits.length < 10; i++) {{
                    var e = all[i];
                    if (matches(e)) {{
                        hits.push({{
                            tag:  e.tagName,
                            aria: e.getAttribute && e.getAttribute('aria-label') || '',
                            cmd:  e.getAttribute && e.getAttribute('cmd') || '',
                            txt:  (e.textContent || '').trim().slice(0, 50)
                        }});
                    }}
                    if (e.shadowRoot) walk(e.shadowRoot, hits, depth - 1);
                }}
            }}
            var hits = [];
            walk(document, hits, 10);
            return hits;
        }}"""

        self.log(f"  🔍 Diagnose (keywords: {keywords}):", "dim")
        for fi, frame in enumerate(tab.frames):
            try:
                hits = await frame.evaluate(scan_js)
                label = "main" if fi == 0 else f"frame[{fi}]"
                if hits:
                    for h in hits:
                        aria = f" aria='{h['aria']}'" if h['aria'] else ""
                        cmd  = f" cmd='{h['cmd']}'" if h['cmd'] else ""
                        txt  = f" txt='{h['txt']}'" if h['txt'] else ""
                        self.log(f"    [{label}] <{h['tag']}>{aria}{cmd}{txt}", "dim")
            except Exception:
                pass

    async def _auto_dismiss(self, tab, texts: list) -> bool:
        """
        Automatically click any dialog button whose text matches one of `texts`.
        Checks all frames and shadow DOM. Returns True if something was clicked.
        Used to silently dismiss known popups (e.g. 'Proceed Without Grade Item').
        """
        df = self._DEEP_FIND_JS
        texts_lower = [t.lower() for t in texts]
        for frame in tab.frames:
            try:
                clicked = await frame.evaluate(f"""() => {{
                    {df}
                    var texts = {texts_lower!r};
                    var btn = deepFind(document, function(e) {{
                        var tag = (e.tagName || '').toUpperCase();
                        if (tag !== 'D2L-BUTTON' && tag !== 'BUTTON') return false;
                        var txt = (e.textContent || '').trim().toLowerCase();
                        return texts.some(function(t) {{ return txt.includes(t); }});
                    }});
                    if (!btn) return false;
                    var inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
                    if (inner) {{ inner.click(); return true; }}
                    btn.click(); return true;
                }}""")
                if clicked:
                    return True
            except Exception:
                pass
        return False

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
            and r["name"] not in {"(embedded video)", "Kaltura Video", "(image)"}
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
            and r["name"] not in {"(embedded video)", "Kaltura Video", "(image)"}
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


    # ── Missing file download + upload ────────────────────────────────────────

    async def _offer_missing_file_download(
        self,
        context: "BrowserContext",
        bs_page: "Page",
        course_id: str,
        results: list,
        bs_flat: list,
    ) -> None:
        import json as _json

        bs_module_by_title = {i["title"]: i for i in (bs_flat or []) if i["kind"] == "MODULE"}
        section_to_bs: dict = {}
        for r in results:
            if r.get("type") == "SECTION" and r.get("matched"):
                matched  = r["matched"]
                bs_title = matched[0] if isinstance(matched, tuple) else matched
                bs_mod   = bs_module_by_title.get(bs_title, {})
                section_to_bs[r["name"]] = {"id": bs_mod.get("id"), "title": bs_title}

        missing_files = [
            {
                "name":            r["name"],
                "section":         r.get("section", ""),
                "href":            r["href"],
                "bs_module_id":    section_to_bs.get(r.get("section", ""), {}).get("id"),
                "bs_module_title": section_to_bs.get(r.get("section", ""), {}).get(
                    "title", r.get("section", "")
                ),
            }
            for r in results
            if r.get("status") == "missing"
            and r.get("type") == "FILE"
            and r.get("href")
        ]

        if not missing_files:
            return

        self.log(f"📥 {len(missing_files)} missing FILE(s) — waiting for your selection…", "step")
        self.on_file_checklist(_json.dumps(missing_files))

        if self.file_checklist_event:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.file_checklist_event.wait)

        selected = list(self.file_checklist_result) if self.file_checklist_result else []
        if not selected:
            if not getattr(self, "do_pdf_upload", True):
                self.log("  ↷ Skipped — PDF upload disabled.", "dim")
                return
            # Checkbox on but Skip All clicked — scan cache and upload what's already there
            save_dir = Path("downloads") / "files" / course_id

            def _cache_match(item_name: str, file_stem: str) -> bool:
                """Token overlap match: handles 'Chapter 1 PowerPoint' vs 'Chapter_001.pptx'."""
                def tokens(s):
                    parts = re.findall(r'\d+|[a-z]+', s.lower())
                    result = set()
                    for p in parts:
                        try:
                            result.add(int(p))  # "001" == "1"
                        except ValueError:
                            result.add(p)
                    return result - {'and', 'or', 'the', 'to', 'for', 'of', 'in', 'a'}

                t_item = tokens(item_name)
                t_file = tokens(file_stem)
                if not t_item:
                    return False
                return len(t_item & t_file) / len(t_item) >= 0.5

            cached_from_disk = []
            if save_dir.exists():
                cached_files = list(save_dir.iterdir())
                for f in missing_files:
                    for cf in cached_files:
                        if _cache_match(f["name"], cf.stem):
                            entry = dict(f)
                            entry["cached_path"] = str(cf)
                            cached_from_disk.append(entry)
                            break
            if cached_from_disk:
                self.log(f"  → {len(cached_from_disk)} cached file(s) found — uploading to Brightspace…", "dim")
                await self._download_and_upload_missing(context, bs_page, course_id, cached_from_disk)
            else:
                self.log("  ↷ No cached files found in downloads folder.", "dim")
            return

        await self._download_and_upload_missing(context, bs_page, course_id, selected)

    async def _download_and_upload_missing(
        self,
        context: "BrowserContext",
        bs_page: "Page",
        course_id: str,
        files: list,
    ) -> None:
        from collections import defaultdict

        # Per-course subfolder so re-runs and multiple courses stay separate
        save_dir = Path("downloads") / "files" / course_id
        save_dir.mkdir(parents=True, exist_ok=True)

        self.log("─" * 52, "dim")
        self.log(f"📥 Downloading {len(files)} file(s) from Moodle…", "step")
        self.log(f"   Saving to: downloads/files/{course_id}/", "dim")

        # ── Phase 1: download all files ────────────────────────────────────────
        # Download tabs are completely separate from the Brightspace page so a
        # crashed/closed Moodle tab can never affect the BS upload later.
        downloaded: list = []   # list of (local_path, file_info_dict)
        for idx, f in enumerate(files, 1):
            if self.stop_flag[0]:
                self.log("⏸ Stopped by user — aborting file downloads", "warning")
                return
            name  = f["name"]
            href  = f["href"]
            self.log(f"  [{idx}/{len(files)}] {name}", "info")

            # Fast-path: file already identified from local cache — skip Moodle entirely
            if f.get("cached_path"):
                local = Path(f["cached_path"])
                if local.exists():
                    # Rename to match the Moodle item name so Brightspace shows
                    # the correct title (e.g. "Chapter 9 PowerPoint.pptx" not "Chapter_009.pptx")
                    safe_name = re.sub(r'[<>:"/\\|?*]', '', f["name"]).strip()
                    correct = local.parent / (safe_name + local.suffix)
                    if correct != local:
                        if not correct.exists():
                            import shutil
                            shutil.copy2(str(local), str(correct))
                        local = correct
                    self.log(f"    ℹ Using cached: {local.name}", "dim")
                    downloaded.append((local, f))
                else:
                    self.log(f"    ✗ Cached path missing: {local}", "error")
                continue

            tab = await context.new_page()
            try:
                try:
                    await tab.goto(href, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass
                await tab.wait_for_timeout(300)

                dl_href = href
                wk = tab.locator('.resourceworkaround a[href*="pluginfile.php"]')
                if await wk.count() > 0:
                    dl_href = await wk.first.get_attribute("href")
                    self.log(f"    → Intermediate page — following download link", "dim")
                elif "pluginfile.php" in tab.url:
                    dl_href = tab.url
                else:
                    any_pf = tab.locator('a[href*="pluginfile.php"]')
                    if await any_pf.count() > 0:
                        dl_href = await any_pf.first.get_attribute("href")
                        self.log(f"    → Found pluginfile link on page", "dim")

                if "pluginfile.php" in dl_href and "forcedownload" not in dl_href:
                    dl_href += ("&" if "?" in dl_href else "?") + "forcedownload=1"

                async with tab.expect_download(timeout=30000) as dl_info:
                    try:
                        await tab.goto(dl_href, wait_until="domcontentloaded", timeout=20000)
                    except Exception as _nav_err:
                        if "Download is starting" not in str(_nav_err):
                            raise

                dl       = await dl_info.value
                filename = dl.suggested_filename
                local    = save_dir / filename

                if local.exists():
                    self.log(f"    ℹ Already cached: {filename} — reusing", "dim")
                else:
                    await dl.save_as(str(local))
                    self.log(f"    ✓ Downloaded: {filename}", "success")

                # Rename to Moodle item name so Brightspace topic title is correct
                safe_name = re.sub(r'[<>:"/\\|?*]', '', f["name"]).strip()
                correct = save_dir / (safe_name + local.suffix)
                if correct != local and not correct.exists():
                    import shutil
                    shutil.copy2(str(local), str(correct))
                if correct.exists():
                    local = correct

                downloaded.append((local, f))
            except Exception as e:
                self.log(f"    ✗ {e}", "error")
            finally:
                try:
                    await tab.close()
                except Exception:
                    pass

        if not downloaded:
            self.log("  ↷ Nothing downloaded.", "dim")
            return

        # ── Phase 2: upload to Brightspace via browser UI ──────────────────────
        self.log(f"", "dim")
        self.log(f"⬆ Uploading {len(downloaded)} file(s) to Brightspace…", "step")

        from urllib.parse import urlparse
        parsed = urlparse(self.bs_url)
        bs_base = f"{parsed.scheme}://{parsed.netloc}"

        # Group files by module so we open one BS tab per module
        by_module: dict = defaultdict(list)
        no_module: list = []
        for local, fi in downloaded:
            mod_id = fi.get("bs_module_id")
            if mod_id:
                by_module[mod_id].append((local, fi))
            else:
                no_module.append((local, fi))

        ok_count = fail_count = 0

        for mod_id, items in by_module.items():
            if self.stop_flag[0]:
                self.log("⏸ Stopped by user — aborting file uploads", "warning")
                return
            mod_title = items[0][1].get("bs_module_title", "?")
            self.log(f"  📁 {mod_title} ({len(items)} file(s))", "info")

            upload_ok = await self._upload_files_to_bs_module_ui(
                context,
                bs_base,
                course_id,
                mod_id,
                mod_title,
                [local for local, _ in items],
            )
            if not upload_ok:
                for _, fi in items:
                    self.log(f"    ✗ {fi['name']} — browser upload failed", "error")
                    self._summary["files_failed"].append((fi["name"], mod_title))
                    fail_count += 1
                continue

            for _, fi in items:
                name = fi["name"]
                if await self._verify_topic_in_module(bs_page, course_id, mod_id, name):
                    self.log(f"    ✓ {name}", "success")
                    self._summary["files_uploaded"].append((name, mod_title))
                    ok_count += 1
                else:
                    self.log(f"    ✗ {name} — upload not found in module after browser upload", "error")
                    self._summary["files_failed"].append((name, mod_title))
                    fail_count += 1

        for local, fi in no_module:
            self.log(f"  ⚠ {fi['name']} — no BS module match, saved to downloads/", "warning")
            self._summary["files_failed"].append((fi["name"], "no module match"))
            fail_count += 1

        self.log(f"⬆ Done: {ok_count} uploaded, {fail_count} failed", "step")

    # ─────────────────────────────────────────────────────────────────────────
    _DEEP_FIND_JS = DEEP_FIND_JS

    async def _create_missing_units(
        self, context, bs_base: str, course_id: str, section_names: list
    ) -> int:
        """Create Brightspace units for Moodle sections that have no BS module.
        Lessons page → 'New Unit' (#generate-unit-btn) → 'Create Unit' (#createUnit)
        → same title+Save editor pattern as page creation. Returns created count."""
        df = self._DEEP_FIND_JS
        created = 0
        for name in section_names:
            if self.stop_flag[0]:
                self.log("⏸ Stopped by user — aborting unit creation", "warning")
                break
            tab = await context.new_page()
            try:
                await tab.goto(
                    f"{bs_base}/d2l/le/lessons/{course_id}",
                    wait_until="domcontentloaded", timeout=20000,
                )
                await tab.wait_for_timeout(2000)

                # 1) "New Unit" dropdown opener (shadow DOM, lazily rendered — poll)
                opened = False
                for _ in range(15):
                    opened = await self._eval_in_any_frame(tab, f"""() => {{
                        {df}
                        var host = deepFind(document, function(e) {{
                            return (e.id || '') === 'generate-unit-btn';
                        }});
                        if (!host) return false;
                        var sub = host.shadowRoot ? host.shadowRoot.querySelector('d2l-button-subtle') : null;
                        var target = sub || host;
                        var inner = target.shadowRoot ? target.shadowRoot.querySelector('button') : null;
                        (inner || target).click();
                        return true;
                    }}""")
                    if opened:
                        break
                    await tab.wait_for_timeout(1000)
                if not opened:
                    self.log(f"  ✗ 'New Unit' button not found — cannot create {name!r}", "error")
                    continue
                await tab.wait_for_timeout(800)

                # 2) "Create Unit" menu item in the dropdown
                create_clicked = False
                for _ in range(5):
                    create_clicked = await self._eval_in_any_frame(tab, f"""() => {{
                        {df}
                        var item = deepFind(document, function(e) {{
                            return (e.id || '') === 'createUnit';
                        }});
                        if (!item) return false;
                        var inner = item.shadowRoot ? item.shadowRoot.querySelector('div.d2l-menu-item-text') : null;
                        (inner || item).click();
                        return true;
                    }}""")
                    if create_clicked:
                        break
                    await tab.wait_for_timeout(1000)
                if not create_clicked:
                    self.log(f"  ✗ 'Create Unit' menu item not found — cannot create {name!r}", "error")
                    continue
                try:
                    await tab.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                await tab.wait_for_timeout(1500)

                # 3) Unit editor — same title + Save pattern as page creation.
                # Poll: the editor shows a d2l-skeletize skeleton first and the
                # real input appears late on cold loads (first unit missed it).
                title_filled = False
                for _ in range(15):
                    for sel in ['input.d2l-input[maxlength="150"]', 'input.d2l-input']:
                        try:
                            loc = tab.locator(sel).first
                            if await loc.count() > 0:
                                await loc.click(click_count=3)
                                await loc.fill(name)
                                title_filled = True
                                break
                        except Exception:
                            pass
                    if title_filled:
                        break
                    await tab.wait_for_timeout(1000)
                if not title_filled:
                    self.log(f"  ✗ Unit title input not found for {name!r}", "error")
                    continue
                await tab.wait_for_timeout(500)

                saved = await tab.evaluate(f"""async () => {{
                    {df}
                    var btn = deepFind(document, function(e) {{
                        return (e.tagName || '').toUpperCase() === 'D2L-BUTTON'
                            && e.classList && e.classList.contains('d2l-desktop');
                    }});
                    if (!btn) return false;
                    var inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
                    (inner || btn).click();
                    return true;
                }}""")
                if not saved:
                    self.log(f"  ⚠ Save button not found for {name!r}", "warning")
                    continue
                await tab.wait_for_timeout(2000)
                self.log(f"  ✓ Created unit: {name}", "success")
                created += 1
            except Exception as e:
                self.log(f"  ✗ Unit creation failed for {name!r}: {str(e).splitlines()[0]}", "error")
            finally:
                try:
                    await tab.close()
                except Exception:
                    pass
        return created

    async def _verify_topic_in_module(
        self, bs_page, course_id: str, module_id, expected_name: str
    ) -> bool:
        """Check via the in-browser D2L API whether a topic with this exact
        (normalized) title exists in the module. Substring matching is not used —
        it false-positives on short unrelated titles, which made H5P Phase B
        skip every insert as "already in Brightspace"."""
        try:
            topics = await bs_page.evaluate(
                """async ([courseId, moduleId]) => {
                    const resp = await fetch(
                        `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
                        { credentials: 'include' }
                    );
                    if (!resp.ok) return null;
                    return await resp.json();
                }""",
                [str(course_id), str(module_id)],
            )
            if not topics:
                return False
            name_norm = re.sub(r'[^\w]', '', expected_name).lower()
            for topic in topics:
                title_norm = re.sub(r'[^\w]', '', topic.get('Title', '')).lower()
                if title_norm == name_norm:
                    return True
            return False
        except Exception:
            return False

    # NOTE: Replaced by two-step API approach (_upload_file_to_brightspace +
    # _create_bs_file_topic). Kept as reference. Do not delete.
    # async def _upload_files_to_bs_module_ui(
    #     self,
    #     context: "BrowserContext",
    #     bs_base: str,
    #     course_id: str,
    #     module_id,
    #     module_title: str,
    #     file_paths: list,
    # ) -> bool:
    #     """
    #     Upload files to a Brightspace module via the 'Add Existing' UI.
    #     Navigates to course content → activates module → clicks Add Existing →
    #     sets files in the drag-and-drop uploader → waits for networkidle.
    #     Returns True if the upload appeared to succeed.
    #     """
    #     tab = await context.new_page()
    #     try:
    #         # Navigate directly to the module — same URL pattern as H5P Phase B.
    #         # No TOC click needed; lands right on the module page.
    #         module_url = f"{bs_base}/d2l/le/lessons/{course_id}/units/{module_id}"
    #         await tab.goto(module_url, wait_until="domcontentloaded", timeout=30000)
    #         await tab.wait_for_timeout(2000)
    #
    #         # ── find the frame containing "Add Existing" ─────────────────────
    #         # The content area is rendered in a child iframe — the main page only
    #         # has the navbar. Scan all frames every 500ms for up to 10s.
    #         add_frame = None
    #         for _ in range(20):
    #             for frame in tab.frames:
    #                 try:
    #                     count = await frame.locator(
    #                         'd2l-button[aria-label="Add Existing"], d2l-button.add-existing-btn'
    #                     ).count()
    #                     if count > 0:
    #                         add_frame = frame
    #                         break
    #                 except Exception:
    #                     pass
    #             if add_frame:
    #                 break
    #             await tab.wait_for_timeout(500)
    #
    #         if not add_frame:
    #             self.log(f"    ✗ 'Add Existing' not found in any frame for '{module_title}'", "error")
    #             self.log(f"      Frames loaded: {[f.url[:80] for f in tab.frames]}", "dim")
    #             return False
    #
    #         await add_frame.locator(
    #             'd2l-button[aria-label="Add Existing"], d2l-button.add-existing-btn'
    #         ).first.click(timeout=5000)
    #         await tab.wait_for_timeout(1000)
    #
    #         # ── Step 3: file input also lives in a frame ──────────────────────
    #         file_frame = None
    #         for _ in range(16):
    #             for frame in tab.frames:
    #                 try:
    #                     if await frame.locator('input[type="file"]').count() > 0:
    #                         file_frame = frame
    #                         break
    #                 except Exception:
    #                     pass
    #             if file_frame:
    #                 break
    #             await tab.wait_for_timeout(500)
    #
    #         if not file_frame:
    #             self.log(f"    ✗ File input not found in any frame after 8s", "error")
    #             return False
    #
    #         try:
    #             async with tab.expect_file_chooser(timeout=8000) as fc_info:
    #                 await file_frame.locator('input[type="file"]').first.click()
    #             fc = await fc_info.value
    #             await fc.set_files([str(p) for p in file_paths])
    #         except Exception as fc_err:
    #             self.log(f"    ✗ File chooser not triggered: {fc_err}", "error")
    #             return False
    #
    #         # ── Step 4: wait for D2L to upload and return to content page ────
    #         await tab.wait_for_load_state("networkidle", timeout=60000)
    #         await tab.wait_for_timeout(1000)
    #         return True
    #
    #     except Exception as e:
    #         self.log(f"    ✗ Browser UI upload error: {e}", "error")
    #         return False
    #     finally:
    #         try:
    #             await tab.close()
    #         except Exception:
    #             pass

    async def _upload_files_to_bs_module_ui(
        self,
        context: "BrowserContext",
        bs_base: str,
        course_id: str,
        module_id,
        module_title: str,
        file_paths: list,
    ) -> bool:
        """
        Upload files to a Brightspace module through the visible browser UI.
        This follows the instructor flow instead of posting to D2L APIs directly.
        """
        tab = await context.new_page()
        try:
            module_url = f"{bs_base}/d2l/le/lessons/{course_id}/units/{module_id}"
            await tab.goto(module_url, wait_until="domcontentloaded", timeout=30000)
            await tab.wait_for_timeout(2000)

            add_selector = 'd2l-button[aria-label="Add Existing"], d2l-button.add-existing-btn'
            add_frame = None
            for _ in range(20):
                for frame in tab.frames:
                    try:
                        loc = frame.locator(add_selector)
                        if await loc.count() > 0 and await loc.first.is_visible():
                            add_frame = frame
                            break
                    except Exception:
                        pass
                if add_frame:
                    break
                await tab.wait_for_timeout(500)

            if not add_frame:
                self.log(f"    ✗ 'Add Existing' not found in any frame for '{module_title}'", "error")
                self.log(f"      Frames loaded: {[f.url[:80] for f in tab.frames]}", "dim")
                return False

            await add_frame.locator(add_selector).first.click(timeout=5000)
            await tab.wait_for_timeout(1000)

            file_frame = None
            for _ in range(20):
                for frame in tab.frames:
                    try:
                        if await frame.locator('input[type="file"]').count() > 0:
                            file_frame = frame
                            break
                    except Exception:
                        pass
                if file_frame:
                    break
                await tab.wait_for_timeout(500)

            if not file_frame:
                self.log("    ✗ File input not found after opening Add Existing", "error")
                return False

            try:
                async with tab.expect_file_chooser(timeout=10000) as fc_info:
                    await file_frame.locator('input[type="file"]').first.click()
                fc = await fc_info.value
                await fc.set_files([str(p) for p in file_paths])
            except Exception as fc_err:
                self.log(f"    ✗ File chooser not triggered: {fc_err}", "error")
                return False

            try:
                await tab.wait_for_load_state("networkidle", timeout=60000)
            except Exception:
                pass
            await tab.wait_for_timeout(2000)
            return True

        except Exception as e:
            self.log(f"    ✗ Browser UI upload error: {e}", "error")
            return False
        finally:
            try:
                await tab.close()
            except Exception:
                pass

    async def _create_bs_file_topic(
        self, bs_page: "Page", course_id: str, module_id, title: str, file_url: str
    ) -> bool:
        result = await bs_page.evaluate(
            """async ([courseId, moduleId, title, fileUrl]) => {
                try {
                    const resp = await fetch(
                        `/d2l/api/le/1.0/${courseId}/content/modules/${moduleId}/structure/`,
                        {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            credentials: 'include',
                            body: JSON.stringify({
                                Title: title,
                                ShortTitle: '',
                                Type: 1,
                                TopicType: 1,
                                Url: fileUrl,
                                IsHidden: false,
                                IsLocked: false
                            })
                        }
                    );
                    const body = await resp.text().catch(() => '');
                    return { ok: resp.ok, status: resp.status, body: body.slice(0, 500) };
                } catch (e) {
                    return { ok: false, status: 0, body: String(e) };
                }
            }""",
            [course_id, str(module_id), title, file_url],
        )
        if result and result.get("ok"):
            return True
        self.log(
            f"    ✗ Topic API ({result.get('status','?')}): {result.get('body','')}", "error"
        )
        return False

    async def _relink_moodle_files(
        self, context: "BrowserContext", bs_page: "Page",
        course_id: str, moodle_links: list
    ) -> None:
        """
        Moodle link re-hosting disabled — requires browser UI refactor (D2L API removed).
        To re-enable: navigate to topic, open TinyMCE editor, replace URLs manually.
        """
        self.log("", "dim")
        self.log("🔗 Re-link step skipped (requires browser UI implementation)", "warning")
        if not moodle_links:
            return
        self.log(f"  {len(moodle_links)} Moodle link(s) would need manual re-hosting in Brightspace", "dim")

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
                moodle_user = self.moodle_username
                moodle_pass = self.moodle_password
                sso_pass    = self.sso_password or moodle_pass

                async def _click_manual_login():
                    self.log("  Navigating to Manual Login form…", "info")
                    try:
                        await tab.goto(
                            "https://mymoodle.okanagan.bc.ca/login/index.php?saml=off",
                            wait_until="domcontentloaded", timeout=15000,
                        )
                        await tab.wait_for_timeout(1000)
                        # "Already logged in as X — log out?" dialog (SSO session still active)
                        logout_btn = tab.locator('button:has-text("Log out")')
                        if await logout_btn.count() > 0:
                            self.log("  Clearing existing SSO session (Log out)…", "info")
                            await logout_btn.first.click()
                            await tab.wait_for_load_state("domcontentloaded", timeout=15000)
                            await tab.wait_for_timeout(1000)
                            await tab.goto(
                                "https://mymoodle.okanagan.bc.ca/login/index.php?saml=off",
                                wait_until="domcontentloaded", timeout=15000,
                            )
                            await tab.wait_for_timeout(1000)
                        return True
                    except Exception:
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
                            await pwd_input.fill(sso_pass)
                            await tab.locator('#idSIButton9').click()
                            await tab.wait_for_load_state("domcontentloaded", timeout=15000)
                            await tab.wait_for_timeout(2000)
                            # "Stay signed in?" prompt
                            if "microsoftonline.com" in tab.url:
                                stay_no = tab.locator('#idBtn_Back')
                                if await stay_no.count() > 0:
                                    self.log("  Dismissing 'Stay signed in?' prompt…", "info")
                                    await stay_no.click()
                                    await tab.wait_for_load_state("domcontentloaded", timeout=10000)
                                    await tab.wait_for_timeout(1500)
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
                            await tab.evaluate("""([u, p]) => {
                                const set = Object.getOwnPropertyDescriptor(
                                    window.HTMLInputElement.prototype, 'value').set;
                                const uEl = document.querySelector('#username');
                                const pEl = document.querySelector('#password');
                                set.call(uEl, u); uEl.dispatchEvent(new Event('input', {bubbles:true}));
                                set.call(pEl, p); pEl.dispatchEvent(new Event('input', {bubbles:true}));
                                document.querySelector('#loginbtn').click();
                            }""", [moodle_user, moodle_pass])
                            await tab.wait_for_load_state("domcontentloaded", timeout=15000)
                            await tab.wait_for_timeout(2000)
                            self.log("✓ Moodle login complete", "success")
                        except Exception as e:
                            self.log(f"✗ Auto-login failed: {e}", "error")
                            return None
                    else:
                        self.log("  Moodle credentials not set in Settings — log in manually.", "warning")
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

            # Normalise common Moodle URL variants to course/view.php
            import re as _re
            _course_url = self.moodle_url or tab.url
            _id_match = _re.search(r"[?&]id=(\d+)", _course_url)
            if _id_match and "course/view.php" not in _course_url:
                _base = _course_url.split("/enrol")[0].split("/course")[0]
                _course_url = f"{_base}/course/view.php?id={_id_match.group(1)}"
                self.log(f"  Normalised Moodle URL → {_course_url}", "dim")

            # Auto-navigate to the course page before pausing. A freshly
            # established post-login session often bounces the FIRST hit to
            # enrol/index.php; re-navigating to the same URL then opens the course.
            for _attempt in range(4):
                if "course/view.php" in tab.url and "enrol" not in tab.url:
                    break
                self.log(f"  Navigating to Moodle course page… (try {_attempt + 1})", "dim")
                try:
                    await tab.goto(_course_url, wait_until="domcontentloaded", timeout=15000)
                    await tab.wait_for_timeout(1500)
                except Exception:
                    pass

            self.log("─" * 52, "dim")
            self.log(f"  Moodle loaded at: {tab.url}", "dim")
            self.log("  Verify this is the correct course, then click", "info")
            self.log("  ✅ Ready — Scrape Now  in the app.", "info")
            self.log("─" * 52, "dim")

            if self.on_moodle_waiting:
                self.on_moodle_waiting()
            if self.moodle_ready_event:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.moodle_ready_event.wait)

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
            # (2) navigate to each PAGE topic for its body HTML,
            # (3) navigate into each FOLDER activity to list its files
            inline_embedded = await self._scan_moodle_labels_inline(tab, items)
            embedded        = await self._scan_moodle_page_bodies(tab, items)
            folder_files    = await self._scan_moodle_folders(tab, items)

            # DEBUG: Show what _scan_moodle_folders returned
            print(f"\n[DEBUG] After _scan_moodle_folders(): {len(folder_files)} items returned")
            for ff in folder_files:
                print(f"[DEBUG]   Item: {ff.get('name', 'N/A')} | parent_topic={ff.get('parent_topic', 'N/A')} | embedded={ff.get('embedded', 'MISSING')}")

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

            # Deduplicate embedded lists across both scanners (same href+parent → keep first)
            _seen_emb: set = set()
            _deduped_emb: list = []
            for _e in flat_embedded + embedded + folder_files:
                _key = (_e.get("href") or _e.get("name"), _e.get("parent_topic", ""))
                if _key and _key not in _seen_emb:
                    _seen_emb.add(_key)
                    _deduped_emb.append(_e)

            # Insert embedded/folder files right after their parent item so they
            # inherit the parent's section. Appending them at the end misattributes
            # them all to the last section (the empty "Topic 14" ghost).
            _by_parent: dict = {}
            _orphan_emb: list = []
            for _e in _deduped_emb:
                _parent = _e.get("parent_topic", "")
                if _parent:
                    _by_parent.setdefault(_parent, []).append(_e)
                else:
                    _orphan_emb.append(_e)
            _placed: list = []
            for _it in ordered:
                _placed.append(_it)
                if _it.get("name") in _by_parent:
                    _placed.extend(_by_parent.pop(_it["name"]))
            for _leftover in _by_parent.values():
                _orphan_emb.extend(_leftover)
            items = _placed + _orphan_emb
            await self._enrich_kaltura_titles(tab.context, _deduped_emb)

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
            h5p_skipped = (
                getattr(self, "h5p_skip_flag", None)
                and self.h5p_skip_flag[0]
            )
            if h5p_skipped:
                self.log("  ⏭ H5P download skipped.", "dim")
            else:
                await self._h5p.enable_downloads(context, items)

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
                    try:
                        await tab.goto(href, wait_until="domcontentloaded", timeout=20000)
                    except Exception as e:
                        if "Download is starting" not in str(e):
                            raise

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

    # H5P methods removed — delegated to self._h5p (H5PHandler)



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

                    // ── Section summary (.summarytext) — videos/files embedded in the section intro ──
                    const summaryEl = section.querySelector('.summarytext, .section-summary');
                    if (summaryEl) {
                        const labelName = '(section summary)';
                        summaryEl.querySelectorAll('a[href*="pluginfile.php"]').forEach(a => {
                            const href = a.href || '';
                            if (href.includes('readspeaker') || href.includes('docreader')) return;
                            const text = a.textContent.trim() || href.split('/').pop().split('?')[0];
                            if (href) found.push({ type: 'FILE', name: text, href, embedded: true, section: secName, parent_topic: labelName });
                        });
                        summaryEl.querySelectorAll('iframe[src*="kaltura"]').forEach(f => {
                            const src = f.src || '';
                            const frameTitle = f.getAttribute('title') || '';
                            const m = src.match(/entryid\\/([^\\/&?]+)/i) || src.match(/entry_id=([^&]+)/i) || src.match(/\\/([01]_[a-z0-9]+)(?:\\/|$)/i);
                            const entryId = m ? m[1] : '';
                            const name = frameTitle || (entryId ? 'Kaltura video [' + entryId + ']' : 'Kaltura Video');
                            found.push({ type: 'VIDEO', name, href: src, entryId, embedded: true, section: secName, parent_topic: labelName });
                        });
                        summaryEl.querySelectorAll('.video-js').forEach(el => {
                            const setupStr = el.getAttribute('data-setup-lazy') || el.getAttribute('data-setup') || '{}';
                            try {
                                const setup = JSON.parse(setupStr);
                                const ytSrc = (setup.sources || []).find(s => s.src && s.src.includes('youtube.com'));
                                if (ytSrc) {
                                    const m = ytSrc.src.match(/[?&]v=([^&]+)/);
                                    const videoId = m ? m[1] : '';
                                    const title = el.getAttribute('title') || '';
                                    const name = title || (videoId ? 'YouTube video [' + videoId + ']' : '(embedded video)');
                                    found.push({ type: 'VIDEO', name, href: ytSrc.src, embedded: true, section: secName, parent_topic: labelName });
                                }
                            } catch(e) {}
                        });
                        summaryEl.querySelectorAll('iframe[src*="youtube.com"], iframe[src*="youtube-nocookie.com"]').forEach(f => {
                            if (f.closest('.video-js')) return;
                            const src = f.src || '';
                            const m = src.match(/\\/embed\\/([^?&\\/]+)/);
                            const videoId = m ? m[1] : '';
                            const title = f.getAttribute('title') || '';
                            const name = title || (videoId ? 'YouTube video [' + videoId + ']' : '(embedded video)');
                            found.push({ type: 'VIDEO', name, href: videoId ? 'https://www.youtube.com/watch?v=' + videoId : src, embedded: true, section: secName, parent_topic: labelName });
                        });
                    }

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
                            const frameTitle = f.getAttribute('title') || '';
                            const m   = src.match(/entryid\\/([^\\/&?]+)/i)
                                     || src.match(/entry_id=([^&]+)/i)
                                     || src.match(/\\/([01]_[a-z0-9]+)(?:\\/|$)/i);
                            const entryId = m ? m[1] : '';
                            const name    = frameTitle || (entryId ? 'Kaltura video [' + entryId + ']' : 'Kaltura Video');
                            found.push({
                                type: 'VIDEO', name, href: src, entryId,
                                embedded: true, section: secName, parent_topic: labelName
                            });
                        });

                        // video.js YouTube players in labels
                        body.querySelectorAll('.video-js').forEach(el => {
                            const setupStr = el.getAttribute('data-setup-lazy') || el.getAttribute('data-setup') || '{}';
                            try {
                                const setup = JSON.parse(setupStr);
                                const ytSrc = (setup.sources || []).find(s => s.src && s.src.includes('youtube.com'));
                                if (ytSrc) {
                                    const m = ytSrc.src.match(/[?&]v=([^&]+)/);
                                    const videoId = m ? m[1] : '';
                                    const title = el.getAttribute('title') || '';
                                    const name = title || (videoId ? 'YouTube video [' + videoId + ']' : '(embedded video)');
                                    found.push({
                                        type: 'VIDEO', name, href: ytSrc.src,
                                        embedded: true, section: secName, parent_topic: labelName
                                    });
                                }
                            } catch(e) {}
                        });

                        // Plain YouTube iframes in labels not inside a video.js container
                        body.querySelectorAll('iframe[src*="youtube.com"], iframe[src*="youtube-nocookie.com"]').forEach(f => {
                            if (f.closest('.video-js')) return;
                            const src = f.src || '';
                            const m = src.match(/\\/embed\\/([^?&\\/]+)/);
                            const videoId = m ? m[1] : '';
                            const title = f.getAttribute('title') || '';
                            const name = title || (videoId ? 'YouTube video [' + videoId + ']' : '(embedded video)');
                            found.push({
                                type: 'VIDEO', name, href: videoId ? 'https://www.youtube.com/watch?v=' + videoId : src,
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
                        const frameTitle = f.getAttribute('title') || '';
                        const m   = src.match(/entryid\\/([^\\/&?]+)/i)
                                 || src.match(/entry_id=([^&]+)/i)
                                 || src.match(/\\/([01]_[a-z0-9]+)(?:\\/|$)/i);
                        const entryId = m ? m[1] : '';
                        const name    = frameTitle || (entryId ? 'Kaltura video [' + entryId + ']' : 'Kaltura Video');
                        found.push({ type: 'VIDEO', name, href: src, entryId, embedded: true });
                    });

                    // video.js / KMC players that don't use iframe
                    document.querySelectorAll('[id*="kaltura_player"], [id*="kplayer"]').forEach(el => {
                        const entryId = el.getAttribute('data-entry-id') || '';
                        const name    = entryId ? 'Kaltura video [' + entryId + ']' : '(embedded kaltura player)';
                        found.push({ type: 'VIDEO', name, href: '', entryId, embedded: true });
                    });

                    // video.js YouTube players (vjs-youtube / data-setup-lazy with youtube src)
                    document.querySelectorAll('.video-js').forEach(el => {
                        const setupStr = el.getAttribute('data-setup-lazy') || el.getAttribute('data-setup') || '{}';
                        try {
                            const setup = JSON.parse(setupStr);
                            const ytSrc = (setup.sources || []).find(s => s.src && s.src.includes('youtube.com'));
                            if (ytSrc) {
                                const m = ytSrc.src.match(/[?&]v=([^&]+)/);
                                const videoId = m ? m[1] : '';
                                const title = el.getAttribute('title') || '';
                                const name = title || (videoId ? 'YouTube video [' + videoId + ']' : '(embedded video)');
                                found.push({ type: 'VIDEO', name, href: ytSrc.src, embedded: true });
                            }
                        } catch(e) {}
                    });

                    // Plain YouTube iframes not already inside a video.js container
                    document.querySelectorAll('iframe[src*="youtube.com"], iframe[src*="youtube-nocookie.com"]').forEach(f => {
                        if (f.closest('.video-js')) return;
                        const src = f.src || '';
                        const m = src.match(/\\/embed\\/([^?&\\/]+)/);
                        const videoId = m ? m[1] : '';
                        const title = f.getAttribute('title') || '';
                        const name = title || (videoId ? 'YouTube video [' + videoId + ']' : '(embedded video)');
                        found.push({ type: 'VIDEO', name, href: videoId ? 'https://www.youtube.com/watch?v=' + videoId : src, embedded: true });
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

    async def _scan_moodle_folders(self, tab, items: list) -> list:
        """
        Visit each FOLDER activity and scrape the files listed inside it.
        Returns a flat list of FILE items (embedded=True) to append to the main list.
        Moodle folder pages list files as <a href="...pluginfile.php..."> links.
        """
        section_of: dict = {}
        current_section = ""
        for item in items:
            if item["type"] == "SECTION":
                current_section = item["name"]
            else:
                section_of[item.get("href", "")] = current_section

        folders = [
            i for i in items
            if i["type"] == "FOLDER" and i.get("href") and not i.get("embedded")
        ]
        if not folders:
            return []

        self.log(f"  Scanning {len(folders)} folder(s) for files…", "info")
        # DEBUG: Log each folder being scanned
        for folder in folders:
            print(f"[DEBUG] _scan_moodle_folders: Processing folder '{folder['name']}'")
            print(f"[DEBUG]   → href: {folder.get('href', 'N/A')}")
            print(f"[DEBUG]   → section: {section_of.get(folder['href'], 'N/A')}")
        found = []

        for folder in folders:
            url     = folder["href"]
            section = section_of.get(url, "")
            try:
                await tab.goto(url, wait_until="domcontentloaded", timeout=20000)
                await tab.wait_for_timeout(600)

                files = await tab.evaluate("""() => {
                    const out = [];
                    document.querySelectorAll('a[href*="pluginfile.php"]').forEach(a => {
                        const href = a.href || '';
                        const name = a.textContent.trim() || href.split('/').pop().split('?')[0];
                        if (href && name) out.push({ type: 'FILE', name, href });
                    });
                    return out;
                }""")

                if files:
                    self.log(f"    📁 {folder['name']}  →  {len(files)} file(s)", "info")
                    # DEBUG: Log each extracted file with embedded flag
                    for f in files:
                        print(f"[DEBUG] _scan_moodle_folders: Extracted file from '{folder['name']}'")
                        print(f"[DEBUG]   → file: {f.get('name', 'N/A')}")
                        print(f"[DEBUG]   → href: {f.get('href', 'N/A')[:80]}...")
                else:
                    self.log(f"    ○ {folder['name']}  (empty or no direct files)", "dim")
                    print(f"[DEBUG] _scan_moodle_folders: Folder '{folder['name']}' returned 0 files")

                for f in files:
                    f["section"]      = section
                    f["parent_topic"] = folder["name"]
                    found.append(f)

            except Exception as e:
                self.log(f"    ✗ {folder['name']}: {e}", "warning")

        # Deduplicate by href
        seen:  set  = set()
        dedup: list = []
        for r in found:
            key = r.get("href") or r.get("name")
            if key and key not in seen:
                seen.add(key)
                dedup.append(r)

        return dedup

    async def _enrich_kaltura_titles(self, context, items: list) -> None:
        """Navigate to each Kaltura VIDEO URL in a new tab to extract the player title."""
        targets = [
            r for r in items
            if r.get("type") == "VIDEO"
            and r.get("href")
            and "kaltura" in r.get("href", "").lower()
            and r.get("name") == "Kaltura Video"
        ]
        if not targets:
            return

        self.log(f"  Fetching titles for {len(targets)} Kaltura video(s)…", "dim")
        page = await context.new_page()
        try:
            for item in targets:
                try:
                    await page.goto(item["href"], wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_selector('[data-plugin-name="titleLabel"]', timeout=6000)
                    title = await page.evaluate("""() => {
                        const el = document.querySelector('[data-plugin-name="titleLabel"]');
                        return el ? (el.getAttribute('title') || el.textContent.trim()) : '';
                    }""")
                    if title:
                        item["name"] = title
                        self.log(f"    🎥 {title}", "dim")
                except Exception:
                    pass
        finally:
            await page.close()

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

        # Group embedded items by parent_topic so they can be shown inline
        from collections import defaultdict
        embedded_by_parent: dict = defaultdict(list)
        section_desc_by_section: dict = defaultdict(list)
        folder_names = set()
        for r in results:
            if r.get("embedded"):
                if r.get("type") == "FOLDER":
                    folder_names.add(r["name"])
                pt = r.get("parent_topic", "")
                if pt == "(section summary)":
                    section_desc_by_section[r.get("section", "")].append(r)
                else:
                    embedded_by_parent[pt].append(r)

        # Group extracted FILE items under their folder name (parent_topic).
        # These are regular compared results (not embedded), so gather them
        # separately from results — they render nested under the FOLDER row.
        files_by_parent: dict = defaultdict(list)
        for r in results:
            if r.get("embedded"):
                continue
            pt = r.get("parent_topic", "")
            if pt in folder_names:
                files_by_parent[pt].append(r)

        def _fmt_status_label(status: str) -> str:
            """Convert status to a short label."""
            labels = {
                "exact": "EXACT",
                "fuzzy": "FUZZY",
                "found_in_search": "FOUND",
                "found_in_content": "IN PAGE",
                "missing": "MISSING",
            }
            return labels.get(status, status.upper())

        def _fmt_folder_child(child: dict, indent: str, is_last: bool = True) -> None:
            """Format a FILE extracted from a folder, showing its match status."""
            status = child.get("status", "missing")
            counts[status] = counts.get(status, 0) + 1
            icons = {
                "exact": "✅", "fuzzy": "⚠️ ", "missing": "❌",
                "found_in_search": "🔍", "found_in_content": "📑",
            }
            s_icon = icons.get(status, "  ")
            label = _fmt_status_label(status)
            connector = "└─ " if is_last else "├─ "
            tag = "success" if status == "exact" else ("error" if status == "missing" else "warning")
            self.log(f"{indent}{connector}{s_icon} {label:<10} {child['name']}", tag)

        def _fmt_embedded(emb: dict, indent: str, is_last: bool = True) -> None:
            """Format an embedded item (file/video inside a folder or page)."""
            href  = emb.get("href", "")
            entry = emb.get("entryId", "")
            icon  = "📄" if emb.get("type") == "FILE" else "🎥"
            connector = "└─ " if is_last else "├─ "
            if entry:
                extra = f"  [entryId: {entry}]"
            elif "youtube.com" in href:
                extra = f"  [{href}]"
            else:
                extra = ""
            self.log(f"{indent}{connector}{icon} {emb['name']}{extra}", "dim")

        current_section_name = ""

        self.log("─" * 52, "dim")
        for r in results:
            icon = _ICONS.get(r["type"], "  ")

            if r["type"] == "SECTION":
                current_section_name = r["name"]
                self.log("", "dim")
                if r["status"] == "exact":
                    self.log(f"✅ {icon} {r['name']}", "step")
                elif r["status"] == "fuzzy":
                    matched_val = r.get("matched")
                    if isinstance(matched_val, (tuple, list)) and len(matched_val) >= 2:
                        matched, score = matched_val[0], matched_val[1]
                        self.log(f"⚠️  {icon} {r['name']}  →  \"{matched}\" ({score}%)", "warning")
                    elif isinstance(matched_val, (tuple, list)) and len(matched_val) == 1:
                        self.log(f"⚠️  {icon} {r['name']}  →  \"{matched_val[0]}\"", "warning")
                    elif matched_val:
                        self.log(f"⚠️  {icon} {r['name']}  →  {matched_val}", "warning")
                    else:
                        self.log(f"⚠️  {icon} {r['name']}", "warning")
                else:
                    self.log(f"❌ {icon} {r['name']}", "error")
                # Section-description embedded items appear right below the header
                for emb in section_desc_by_section.get(current_section_name, []):
                    _fmt_embedded(emb, "   ")
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

            # Show FOLDER items as visual containers (don't count them)
            if r.get("embedded"):
                if r.get("type") == "FOLDER":
                    folder_children = files_by_parent.get(r["name"], [])
                    self.log(f"   ├─ 📁 FOLDER  {r['name']}", "step")
                    for idx, child in enumerate(folder_children):
                        is_last = (idx == len(folder_children) - 1)
                        _fmt_folder_child(child, "   │  ", is_last)
                continue

            # Skip FILE items that are already shown under their FOLDER
            if r.get("type") == "FILE" and r.get("parent_topic") in folder_names:
                continue

            # Skip items already rendered inside an accordion card
            if _norm(r["name"]) in accordion_consumed:
                continue

            status = r["status"]
            counts[status] = counts.get(status, 0) + 1

            # Determine tree connector (├─ or └─) and status label
            embedded_items = embedded_by_parent.get(r["name"], [])
            has_children = len(embedded_items) > 0

            # Status icon mapping
            status_icons = {
                "exact": "✅", "fuzzy": "⚠️ ", "missing": "❌",
                "found_in_search": "🔍", "found_in_content": "📑"
            }
            status_icon = status_icons.get(status, "  ")
            status_label = _fmt_status_label(status)

            # Format main item with status label
            if status == "fuzzy":
                matched_val = r.get("matched")
                if isinstance(matched_val, (tuple, list)) and len(matched_val) >= 2:
                    matched, score = matched_val[0], matched_val[1]
                    self.log(f"   ├─ {status_icon} {status_label:<10} {r['name']}", "warning")
                    self.log(f"      │   → \"{matched}\" ({score}%)", "dim")
                elif isinstance(matched_val, (tuple, list)) and len(matched_val) == 1:
                    matched = matched_val[0]
                    self.log(f"   ├─ {status_icon} {status_label:<10} {r['name']}", "warning")
                    self.log(f"      │   → \"{matched}\"", "dim")
                elif matched_val:
                    self.log(f"   ├─ {status_icon} {status_label:<10} {r['name']}", "warning")
                    self.log(f"      │   → {matched_val}", "dim")
                else:
                    self.log(f"   ├─ {status_icon} {status_label:<10} {r['name']}", "warning")
            elif status in ("found_in_search", "found_in_content"):
                desc = "found via search" if status == "found_in_search" else "found inside page"
                matched_val = r.get("matched", "")
                self.log(f"   ├─ {status_icon} {status_label:<10} {r['name']}", "warning")
                if matched_val:
                    self.log(f"      │   {desc}: \"{matched_val}\"", "dim")
            else:
                connector = "├─" if has_children else "└─"
                tag = "success" if status == "exact" else ("warning" if status == "found_in_search" else "error")
                self.log(f"   {connector} {status_icon} {status_label:<10} {r['name']}", tag)

            # Show any embedded content found inside this activity
            for idx, emb in enumerate(embedded_items):
                is_last = (idx == len(embedded_items) - 1)
                indent_prefix = "      " if status in ("fuzzy", "found_in_search", "found_in_content") else "   "
                _fmt_embedded(emb, indent_prefix, is_last)

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

        # Embedded content is now shown inline under each section above

    # ── Final summary ─────────────────────────────────────────────────────────

    def _log_final_summary(self, results: list) -> None:
        s = self._summary
        total_elapsed = time.time() - s["start_time"]

        self.log("", "dim")
        self.log("═" * 52, "dim")
        self.log("📊 MIGRATION SUMMARY", "step")
        self.log("═" * 52, "dim")

        # ── Timing breakdown ──────────────────────────────────────────────────
        self.log("", "dim")
        self.log("⏱ Time per phase:", "info")
        for phase, secs in s["timings"].items():
            mins, sec = divmod(int(secs), 60)
            label = f"{mins}m {sec:02d}s" if mins else f"{sec}s"
            self.log(f"   {phase:<30} {label}", "dim")
        mins, sec = divmod(int(total_elapsed), 60)
        self.log(f"   {'TOTAL':<30} {mins}m {sec:02d}s", "info")

        # ── Comparison results ────────────────────────────────────────────────
        if results:
            exact   = [r for r in results if r["status"] == "exact"]
            fuzzy   = [r for r in results if r["status"] == "fuzzy"]
            missing = [r for r in results if r["status"] == "missing"]
            self.log("", "dim")
            self.log("🔍 Comparison:", "info")
            self.log(f"   ✅ {len(exact)} exact matches", "success")
            if fuzzy:
                self.log(f"   ⚠️  {len(fuzzy)} fuzzy matches (similar name, may need review):", "warning")
                for r in fuzzy:
                    matched_title = r["matched"][0] if isinstance(r["matched"], tuple) else r["matched"]
                    score = r.get("score", "?")
                    self.log(f"      • {r['name']}  →  \"{matched_title}\" ({score}%)", "dim")
            if missing:
                self.log(f"   ❌ {len(missing)} still missing after all steps:", "error")
                for r in missing:
                    self.log(f"      • [{r.get('section','?')}] {r['name']}", "dim")

        # ── File uploads ──────────────────────────────────────────────────────
        if s["files_uploaded"] or s["files_failed"]:
            self.log("", "dim")
            self.log("📄 File uploads:", "info")
            for name, mod in s["files_uploaded"]:
                self.log(f"   ✅ {name}  →  {mod}", "success")
            for name, mod in s["files_failed"]:
                self.log(f"   ❌ {name}  →  {mod}", "error")

        # ── H5P embeds ────────────────────────────────────────────────────────
        if s["h5p_inserted"] or s["h5p_failed"]:
            self.log("", "dim")
            self.log("🎮 H5P embeds:", "info")
            for name, mod in s["h5p_inserted"]:
                self.log(f"   ✅ {name}  →  {mod}", "success")
            for name, mod in s["h5p_failed"]:
                self.log(f"   ❌ {name}  →  {mod}", "error")

        self.log("", "dim")
        self.log("═" * 52, "dim")

    async def _prompt_clear_downloads(self, course_id: str) -> None:
        """Ask the user if they want to delete the downloads folder for this course."""
        import shutil
        downloads_dir = Path("downloads") / "files" / course_id
        h5p_dir       = Path("downloads") / "h5p"

        files_count = len(list(downloads_dir.iterdir())) if downloads_dir.exists() else 0
        h5p_count   = len(list(h5p_dir.glob("*.h5p")))   if h5p_dir.exists()       else 0

        if files_count == 0 and h5p_count == 0:
            return

        msg = (
            f"Clear downloads to free up space?\n\n"
            f"  • downloads/files/{course_id}/  ({files_count} file(s))\n"
            f"  • downloads/h5p/                ({h5p_count} .h5p file(s))\n\n"
            f"These are cached copies — re-running will re-download them from Moodle."
        )
        confirmed = await self._confirm(msg)
        if confirmed:
            if downloads_dir.exists():
                shutil.rmtree(str(downloads_dir))
            if h5p_dir.exists():
                shutil.rmtree(str(h5p_dir))
            self.log("🗑 Downloads cleared.", "success")
        else:
            self.log("↷ Downloads kept.", "dim")

    # ── Main flow ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        from browser import launch_browser, wait_for_login

        bs_only     = bool(self.bs_url)     and not self.moodle_url
        moodle_only = bool(self.moodle_url) and not self.bs_url
        full        = bool(self.bs_url)     and bool(self.moodle_url)

        # Initialise summary collector used by sub-functions throughout the run
        self._summary = {
            "start_time":     time.time(),
            "timings":        {},
            "files_uploaded": [],
            "files_failed":   [],
            "h5p_inserted":   [],
            "h5p_failed":     [],
        }
        self._h5p._summary = self._summary

        p, browser, context, page = await launch_browser()
        try:
            await wait_for_login(page, context, self.bs_username or None, self.bs_password or None, self.sso_email or None, self.sso_password or None)

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
                t0 = time.time()
                bs_flat = await self._fetch_bs_toc(page, course_id)
                self._summary["timings"]["Brightspace TOC fetch"] = time.time() - t0
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
                t0 = time.time()
                moodle_items = await self._scrape_moodle(context)
                self._summary["timings"]["Moodle scrape"] = time.time() - t0
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

            # ── Compare + scans ───────────────────────────────────────────────
            self.log("─" * 52, "dim")
            self.log("Comparing Moodle items against Brightspace…", "info")

            # Mark FOLDER items as embedded so they're skipped from comparison.
            # Their extracted FILE items will be compared instead.
            for item in moodle_items:
                if item.get("type") == "FOLDER":
                    item["embedded"] = True

            # DEBUG: Show all items before comparison, focusing on Chapter 2/3 folders
            print(f"\n[DEBUG] Before _compare_items(): {len(moodle_items)} total items")
            for item in moodle_items:
                if "chapter 2" in item.get("name", "").lower() or "chapter 3" in item.get("name", "").lower():
                    print(f"[DEBUG] CHAPTER ITEM: {item}")
                elif item.get("parent_topic") and ("chapter 2" in item.get("parent_topic", "").lower() or "chapter 3" in item.get("parent_topic", "").lower()):
                    print(f"[DEBUG] FROM CHAPTER FOLDER: {item}")
                elif item.get("type") == "FOLDER":
                    print(f"[DEBUG] FOLDER: name='{item.get('name')}' | embedded={item.get('embedded', 'MISSING')}")

            t0 = time.time()
            results = _compare_items(moodle_items, bs_flat)

            # ── Create missing units ──────────────────────────────────────────
            missing_secs = [
                r["name"] for r in results
                if r.get("type") == "SECTION" and r["status"] == "missing"
            ]
            if missing_secs:
                self.log("─" * 52, "dim")
                self.log(f"📦 {len(missing_secs)} Moodle section(s) have no Brightspace unit", "step")
                for n in missing_secs:
                    self.log(f"   • {n}", "info")
                if await self._confirm(
                    f"{len(missing_secs)} Moodle section(s) are missing in Brightspace:\n\n"
                    + "\n".join(f"• {n}" for n in missing_secs)
                    + "\n\nCreate them as units?"
                ):
                    from urllib.parse import urlparse as _up
                    _p = _up(self.bs_url)
                    n_created = await self._create_missing_units(
                        context, f"{_p.scheme}://{_p.netloc}", course_id, missing_secs
                    )
                    self.log(f"📦 Units created: {n_created}/{len(missing_secs)}", "step")
                    if n_created:
                        bs_flat = await self._fetch_bs_toc(page, course_id)
                        # Mark FOLDER items as embedded (re-comparison after unit creation)
                        for item in moodle_items:
                            if item.get("type") == "FOLDER":
                                item["embedded"] = True
                        # DEBUG: Show items before re-comparison
                        print(f"\n[DEBUG] RE-COMPARING after unit creation: {len(moodle_items)} items")
                        for item in moodle_items:
                            if "chapter 2" in item.get("name", "").lower() or "chapter 3" in item.get("name", "").lower():
                                print(f"[DEBUG] CHAPTER ITEM: {item}")
                            elif item.get("parent_topic") and ("chapter 2" in item.get("parent_topic", "").lower() or "chapter 3" in item.get("parent_topic", "").lower()):
                                print(f"[DEBUG] FROM CHAPTER FOLDER: {item}")
                        results = _compare_items(moodle_items, bs_flat)

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
            self._summary["timings"]["Comparison + page scan"] = time.time() - t0

            self._log_report(results)
            self._log_link_report(moodle_links)

            # DEBUG: show file item counts
            moodle_files = [r for r in results if r.get("type") == "FILE"]
            missing_files = [r for r in results if r.get("status") == "missing" and r.get("type") == "FILE"]
            self.log(f"DEBUG: {len(moodle_files)} FILE items in results, {len(missing_files)} marked missing", "dim")

            if self.on_file_checklist and getattr(self, "do_pdf_upload", True):
                t0 = time.time()
                await self._offer_missing_file_download(context, page, course_id, results, bs_flat)
                self._summary["timings"]["File download + upload"] = time.time() - t0
            elif not getattr(self, "do_pdf_upload", True):
                self.log("⏭ PDF upload skipped (checkbox off)", "dim")

            if self.stop_flag[0]:
                self.log("⏸ Stopped by user — skipping remaining phases", "warning")
                if self.on_complete:
                    self.on_complete()
                return

            if moodle_links and getattr(self, "do_relink", False):
                t0 = time.time()
                await self._relink_moodle_files(context, page, course_id, moodle_links)
                self._summary["timings"]["Moodle link re-link"] = time.time() - t0

            if self.stop_flag[0]:
                self.log("⏸ Stopped by user — skipping remaining phases", "warning")
                if self.on_complete:
                    self.on_complete()
                return

            if getattr(self, "do_h5p_embed", False) and moodle_items and bs_flat:
                from urllib.parse import urlparse
                parsed  = urlparse(self.bs_url)
                bs_base = f"{parsed.scheme}://{parsed.netloc}"
                t0 = time.time()
                try:
                    await self._h5p.embed_in_brightspace(context, page, moodle_items, bs_flat, bs_base, course_id)
                except Exception as e:
                    self.log(f"✗ H5P embed error: {e}", "error")
                    import traceback
                    self.log(f"  Traceback: {traceback.format_exc()}", "dim")
                self._summary["timings"]["H5P embed (Phase B)"] = time.time() - t0

            # ── Final summary + cleanup prompt ────────────────────────────────
            self._log_final_summary(results)
            # TODO: Fix async dialog handling — skipped for now
            # await self._prompt_clear_downloads(course_id)

            self.log("", "dim")
            self.log("✓ Check complete — browsers kept open for your comparison", "success")
            self.log("  Close the browser windows when done.", "dim")

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
            # Keep browsers open for user comparison — don't auto-close
            # if browser.is_connected():
            #     await browser.close()
            await p.stop()
