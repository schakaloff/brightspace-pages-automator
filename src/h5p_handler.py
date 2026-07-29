from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from js_helpers import DEEP_FIND_JS, _norm

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page


class H5PHandler:
    def __init__(
        self,
        log: Callable[[str, str], None],
        eval_in_any_frame: Callable,
        auto_dismiss: Callable,
        confirm: Callable,
        diagnose: Callable,
        verify_topic_in_module: Callable,
        summary: dict,
        notify: Optional[Callable] = None,
        should_stop: Optional[Callable] = None,
    ) -> None:
        self.log = log
        self._eval_in_any_frame = eval_in_any_frame
        self._auto_dismiss = auto_dismiss
        self._confirm = confirm
        self._diagnose = diagnose
        self._verify_topic_in_module = verify_topic_in_module
        self._summary = summary
        self._notify = notify
        self._should_stop = should_stop or (lambda: False)
        self._DEEP_FIND_JS = DEEP_FIND_JS

    async def enable_downloads(self, context, items: list) -> None:
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

        save_dir = Path(__file__).parent.parent / "downloads" / "h5p"
        save_dir.mkdir(parents=True, exist_ok=True)

        success = skipped = 0
        for idx, item in enumerate(h5p_items, 1):
            name      = item["name"]
            url       = item["href"]
            safe_name = re.sub(r'[^\w\s\-]', '', name).strip()[:80]
            save_path = save_dir / f"{safe_name}.h5p"

            self.log(f"  [{idx}/{len(h5p_items)}] Preparing H5P download: {name}", "step")
            self.log(f"      href: {url}", "detail")
            self.log(f"      save path: {save_path}", "detail")

            if save_path.exists():
                self.log(f"    ℹ Already downloaded — skipping", "dim")
                success += 1
                skipped += 1
                continue

            tab = await context.new_page()
            try:
                # Step 1: navigate to H5P activity
                self.log("      opening H5P activity page", "detail")
                await tab.goto(url, wait_until="domcontentloaded", timeout=20000)
                self.log(f"      loaded: {tab.url}", "detail")
                await tab.wait_for_timeout(1000)

                # Step 2: navigate to Settings (strip &return=1 so Save and display goes to view.php)
                self.log(f"    → Going to Settings…", "dim")
                settings = tab.locator('a[href*="modedit.php?update="]')
                settings_count = await settings.count()
                self.log(f"      settings links found: {settings_count}", "detail")
                if settings_count == 0:
                    self.log(f"    ⚠ No Settings link — check teacher access", "warning")
                    continue
                settings_href = await settings.first.get_attribute("href")
                settings_href = re.sub(r'&return=\d+', '', settings_href)
                self.log(f"      settings href: {settings_href}", "detail")
                await tab.goto(settings_href, wait_until="domcontentloaded", timeout=15000)
                await tab.wait_for_timeout(800)

                # Step 3 & 4: enable Allow download via JS — works regardless of
                # whether the section is collapsed (checkbox is in DOM either way).
                # Covers all three known field IDs across Moodle H5P module types.
                self.log(f"    → Checking Allow download checkbox…", "dim")
                cb_result = await tab.evaluate("""() => {
                    const ids = ['id_export', 'id_enabledownload', 'id_displayopt_export'];
                    for (const id of ids) {
                        const cb = document.getElementById(id);
                        if (cb) {
                            const was = cb.checked;
                            cb.checked = true;
                            if (!was) cb.dispatchEvent(new Event('change', { bubbles: true }));
                            return { found: true, id, wasChecked: was };
                        }
                    }
                    return { found: false };
                }""")
                if not cb_result or not cb_result.get("found"):
                    self.log(f"    ⚠ Allow download checkbox not found", "warning")
                    continue
                if cb_result.get("wasChecked"):
                    self.log(f"    ✓ Already enabled", "dim")
                else:
                    self.log(f"    ✓ Download enabled", "success")

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
        new_downloads = success - skipped
        if skipped:
            self.log(
                f"  H5P: {new_downloads} downloaded, {skipped} already cached ({success}/{len(h5p_items)} total)",
                "success",
            )
        else:
            self.log(f"  H5P: {success}/{len(h5p_items)} downloaded", "success")
        if new_downloads > 0:
            self.log(f"  Saved to: downloads/h5p/", "dim")

    async def open_interactives(self, tab, for_quiz: bool = False) -> bool:
        df = self._DEEP_FIND_JS
        try:
            # Use Playwright native click so the browser properly fires focus/blur events.
            # JS .click() bypasses the browser's focus machinery — the toolbar stays inactive.
            editor_clicked = False
            if for_quiz:
                try:
                    await tab.locator("d2l-activity-text-editor").click(timeout=5000)
                    editor_clicked = True
                except Exception:
                    pass
            if not editor_clicked:
                for sel in (
                    "d2l-htmleditor [contenteditable='true']",
                    "[contenteditable='true']",
                    "d2l-htmleditor",
                ):
                    try:
                        await tab.locator(sel).first.click(timeout=3000)
                        editor_clicked = True
                        break
                    except Exception:
                        continue
            if not editor_clicked:
                self.log("  ⚠ Could not click editor body — toolbar may not activate", "warn")
            await tab.wait_for_timeout(1500)

            if for_quiz:
                # Quiz editor: open via Creator+ Authoring Tools → H5P menu item.
                creator_js = f"""() => {{
                    {df}
                    var btn = deepFind(document, function(e) {{
                        if (e.tagName !== 'BUTTON') return false;
                        var aria = (e.getAttribute && e.getAttribute('aria-label') || '').toLowerCase();
                        return aria.includes('creator+') || aria.includes('authoring tools');
                    }});
                    if (!btn) return false;
                    btn.click(); return true;
                }}"""
                creator_found = False
                for _ in range(3):
                    creator_found = await self._eval_in_any_frame(tab, creator_js)
                    if creator_found:
                        break
                    await tab.wait_for_timeout(1000)

                if not creator_found:
                    self.log("  Toolbar chomped — clicking More Actions…", "dim")
                    chomper_js = f"""() => {{
                        {df}
                        var chomper = deepFind(document, function(e) {{
                            if ((e.tagName || '').toUpperCase() !== 'D2L-HTMLEDITOR-BUTTON-TOGGLE') return false;
                            return ((e.getAttribute && e.getAttribute('class')) || '').includes('chomper');
                        }});
                        if (!chomper) return false;
                        var inner = chomper.shadowRoot && chomper.shadowRoot.querySelector('button');
                        if (inner) {{ inner.click(); return true; }}
                        chomper.click(); return true;
                    }}"""
                    await self._eval_in_any_frame(tab, chomper_js)
                    await tab.wait_for_timeout(700)
                    for attempt in range(8):
                        creator_found = await self._eval_in_any_frame(tab, creator_js)
                        if creator_found:
                            break
                        self.log(f"  … waiting for toolbar (attempt {attempt + 1}/8)…", "dim")
                        await tab.wait_for_timeout(1000)

                if not creator_found:
                    self.log("  ✗ Creator+ Authoring Tools button not found", "error")
                    await self._diagnose(tab, ["creator", "authoring", "toolbar", "htmleditor"])
                    return False
                await tab.wait_for_timeout(1000)

                found = await self._eval_in_any_frame(tab, f"""() => {{
                    {df}
                    var item = deepFind(document, function(e) {{
                        return e.tagName && e.tagName.toUpperCase() === 'D2L-HTMLEDITOR-MENU-ITEM'
                            && e.getAttribute && e.getAttribute('cmd') === 'h5p';
                    }});
                    if (!item) return false;
                    var inner = item.shadowRoot ? item.shadowRoot.querySelector('button') : null;
                    if (inner) {{ inner.click(); return true; }}
                    item.click();
                    return true;
                }}""")

                if not found:
                    self.log("  ✗ Interactives (h5p) menu item not found", "error")
                    await self._diagnose(tab, ["h5p", "interactive", "creator", "menu-item"])
                    return False

            else:
                # Page editor: use Insert Stuff button (cmd="d2l-isf") — always visible, not chomped.
                self.log("  → Clicking Insert Stuff toolbar button…", "dim")
                isf_js = f"""() => {{
                    {df}
                    var btn = deepFind(document, function(e) {{
                        if ((e.tagName || '').toUpperCase() !== 'D2L-HTMLEDITOR-BUTTON') return false;
                        return e.getAttribute && e.getAttribute('cmd') === 'd2l-isf';
                    }});
                    if (!btn) return false;
                    var inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
                    if (inner) {{ inner.click(); return true; }}
                    btn.click(); return true;
                }}"""
                isf_found = False
                for attempt in range(6):
                    isf_found = await self._eval_in_any_frame(tab, isf_js)
                    if isf_found:
                        break
                    self.log(f"  … waiting for Insert Stuff button (attempt {attempt + 1}/6)…", "dim")
                    await tab.wait_for_timeout(1000)

                if not isf_found:
                    self.log("  ✗ Insert Stuff button not found", "error")
                    await self._diagnose(tab, ["insert stuff", "isf", "d2l-isf", "toolbar"])
                    return False

                self.log("  ✓ Insert Stuff dialog opened — looking for H5P provider…", "dim")
                await tab.wait_for_timeout(2000)

                # The Insert Stuff dialog lists providers; find and click the H5P one.
                h5p_provider_found = False
                for attempt in range(5):
                    try:
                        # H5P usually appears as a link or list item in the dialog
                        isf_frame = tab.frame_locator('iframe[class*="insert"], iframe[title*="Insert"], iframe[src*="isf"], d2l-dialog iframe').first
                        h5p_link = isf_frame.locator('a, li, button').filter(has_text="H5P").first
                        self.log(
                            f"  → Clicking H5P provider in Insert Stuff "
                            f"(attempt {attempt + 1}/5, url={tab.url[:100]})",
                            "dim",
                        )
                        await h5p_link.wait_for(state="visible", timeout=3000)
                        await h5p_link.click(timeout=3000)
                        h5p_provider_found = True
                        self.log("  ✓ H5P provider selected", "dim")
                        break
                    except Exception:
                        pass
                    await tab.wait_for_timeout(1000)

                if not h5p_provider_found:
                    self.log("  ✗ H5P provider not found in Insert Stuff dialog", "error")
                    await self._diagnose(tab, ["h5p", "insert", "provider", "lti"])
                    return False

            await tab.wait_for_timeout(2000)
            return True
        except Exception as e:
            self.log(f"  ✗ _h5p_open_interactives error: {e}", "error")
            return False

    async def find_list_frame(self, tab):
        """Return the first live h5p.com frame that has the content list loaded."""
        for _ in range(15):
            for frame in tab.frames:
                if "h5p.com" not in frame.url:
                    continue
                try:
                    has_list = await frame.evaluate(
                        "() => document.querySelectorAll('tr.content-item, a.create-content, a[href*=\"/content/create\"]').length > 0"
                    )
                    if has_list:
                        return frame
                except Exception:
                    pass
            await tab.wait_for_timeout(1000)
        return None

    async def _click_when_ready(self, owner, selector: str, label: str, timeout: int = 10000) -> bool:
        """Click a locator after visible/enabled checks, with enough context to debug timeouts."""
        url = getattr(owner, "url", "") or ""
        loc = owner.locator(selector).first
        self.log(f"  → {label}", "dim")
        self.log(f"    selector: {selector}", "dim")
        if url:
            self.log(f"    frame/page URL: {url[:140]}", "dim")

        async def state_snapshot():
            try:
                count = await owner.locator(selector).count()
            except Exception:
                count = "?"
            try:
                visible = await loc.is_visible(timeout=1000)
            except Exception:
                visible = "?"
            try:
                enabled = await loc.is_enabled(timeout=1000)
            except Exception:
                enabled = "?"
            return count, visible, enabled

        count, visible, enabled = await state_snapshot()
        self.log(f"    state before click: count={count}, visible={visible}, enabled={enabled}", "dim")
        try:
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.scroll_into_view_if_needed(timeout=timeout)
            if not await loc.is_enabled(timeout=2000):
                self.log(f"  ✗ {label} is visible but disabled", "error")
                return False
            await loc.click(timeout=timeout)
            return True
        except Exception as e:
            count, visible, enabled = await state_snapshot()
            self.log(
                f"  ✗ {label} failed: {str(e).splitlines()[0]} "
                f"(count={count}, visible={visible}, enabled={enabled})",
                "error",
            )
            return False

    async def _recover_list(self, tab, h5p_frame, list_url: str) -> None:
        """After a failed upload, get back to a live content list so the next
        item doesn't try to click inside a dead editor page.

        NEVER GET /lti/launch: that endpoint is POST-only and a GET returns
        405 ("The GET method is not supported for route lti/launch") — which
        replaces the frame with an error page and cascades the failure to
        every later item. If the captured URL isn't a real content-list URL
        (e.g. it's the LTI launch/interstitial), re-discover the live list
        frame instead of navigating to it.
        """
        try:
            if "/lti/launch" in list_url or "/content" not in list_url:
                await self.find_list_frame(tab)
                return
            await h5p_frame.goto(list_url, timeout=15000)
            await h5p_frame.locator('tr.content-item').first.wait_for(timeout=8000)
        except Exception:
            await tab.wait_for_timeout(1000)

    async def upload_one(self, tab, h5p_frame, h5p_file, item_name) -> bool:
        """Upload one .h5p to H5P cloud via an already-open content list frame. Returns to list after."""
        list_url = h5p_frame.url
        try:
            # Check if already exists in cloud content list — skip if found.
            # Re-find the live frame if the stored reference has a stale context.
            name_key = re.sub(r'\s+', ' ', item_name).strip().lower()[:25]
            duplicate_scan_js = f"""() => {{
                var key = {name_key!r};
                var rows = Array.from(document.querySelectorAll('tr.content-item'));
                var titleCount = 0;
                function norm(s) {{
                    return (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                }}
                function check(el) {{
                    if (!el) return null;
                    var title = norm(el.textContent);
                    if (!title || title === 'add content' || title === 'insert' || title === 'edit') return null;
                    titleCount++;
                    return key && title.includes(key) ? title : null;
                }}
                for (var i = 0; i < rows.length; i++) {{
                    var links = rows[i].querySelectorAll(
                        'a.fable-title, .content-title, td a, a[href*="/content/"]'
                    );
                    for (var j = 0; j < links.length; j++) {{
                        var matched = check(links[j]);
                        if (matched) return {{ found: true, rowCount: rows.length, titleCount: titleCount, matched: matched }};
                    }}
                }}
                if (!titleCount) {{
                    var fallback = document.querySelectorAll(
                        'a.fable-title, .content-title, tr.content-item td a, a[href*="/content/"]'
                    );
                    for (var k = 0; k < fallback.length; k++) {{
                        var fallbackMatched = check(fallback[k]);
                        if (fallbackMatched) {{
                            return {{ found: true, rowCount: rows.length, titleCount: titleCount, matched: fallbackMatched }};
                        }}
                    }}
                }}
                return {{ found: false, rowCount: rows.length, titleCount: titleCount, matched: '' }};
            }}"""
            try:
                duplicate_scan = await h5p_frame.evaluate(duplicate_scan_js)
            except Exception as frame_err:
                if "context was destroyed" in str(frame_err) or "Target closed" in str(frame_err):
                    h5p_frame = await self.find_list_frame(tab)
                    if not h5p_frame:
                        self.log(f"  ✗ H5P content list frame lost — cannot upload {item_name}", "error")
                        return False
                    list_url = h5p_frame.url
                    duplicate_scan = await h5p_frame.evaluate(duplicate_scan_js)
                else:
                    raise
            self.log(
                f"  Duplicate check for '{item_name}': key='{name_key}', "
                f"{duplicate_scan.get('rowCount', 0)} row(s), {duplicate_scan.get('titleCount', 0)} title(s)",
                "dim",
            )
            if duplicate_scan.get("found"):
                self.log(
                    f"  ✓ Already in H5P cloud: {item_name} "
                    f"(matched='{duplicate_scan.get('matched', '')[:100]}') — skipping upload",
                    "dim",
                )
                return True
            if duplicate_scan.get("titleCount", 0) == 0:
                self.log(
                    f"  ⚠ No H5P cloud titles visible during duplicate check for {item_name}; upload may duplicate it.",
                    "warning",
                )

            if not await self._click_when_ready(
                h5p_frame,
                'a.create-content, a[href*="/content/create"]',
                "Clicking H5P Add Content",
            ):
                return False
            await tab.wait_for_timeout(2500)

            editor_frame = None
            for _ in range(10):
                for frame in tab.frames:
                    if "h5p.com" in frame.url and "content" in frame.url:
                        editor_frame = frame
                        break
                if editor_frame:
                    break
                await tab.wait_for_timeout(1000)
            if not editor_frame:
                self.log("  ✗ H5P editor frame lost after Add Content", "error")
                return False

            hub_frame = None
            for _ in range(10):
                for frame in tab.frames:
                    try:
                        if await frame.evaluate("() => !!document.querySelector('a#h5p-hub-upload')"):
                            hub_frame = frame
                            break
                    except Exception:
                        pass
                if hub_frame:
                    break
                await tab.wait_for_timeout(1000)
            if not hub_frame:
                self.log("  ✗ H5P hub frame not found", "error")
                return False

            await hub_frame.evaluate("""() => {
                var el = document.querySelector('a#h5p-hub-upload');
                if (el) el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
            }""")
            await tab.wait_for_timeout(800)

            try:
                async with tab.expect_file_chooser(timeout=5000) as fc_info:
                    if not await self._click_when_ready(
                        hub_frame,
                        'button.h5p-hub-upload-button',
                        "Clicking H5P upload file button",
                        timeout=5000,
                    ):
                        raise RuntimeError("H5P upload file button was not ready")
                fc = await fc_info.value
                await fc.set_files(str(h5p_file))
            except Exception:
                self.log("  → Upload button did not open file chooser; setting hidden file input directly", "dim")
                await hub_frame.locator('input[type="file"][accept=".h5p"]').first.set_input_files(str(h5p_file))

            await tab.wait_for_timeout(1000)
            self.log("  → Clicking Use…", "dim")
            if not await self._click_when_ready(
                hub_frame,
                'button.h5p-hub-use-button',
                "Clicking H5P Use button",
            ):
                return False
            self.log("  → Waiting for H5P editor to load after Use…", "dim")
            await tab.wait_for_timeout(6000)

            # Check for validation error (missing H5P library on the cloud platform)
            for frame in tab.frames:
                try:
                    err = await frame.evaluate("""() => {
                        var el = document.querySelector('.h5p-error-report, .error-report, [class*="error"]');
                        return el ? el.textContent.trim() : null;
                    }""")
                    if err and ("validat" in err.lower() or "missing" in err.lower() or "library" in err.lower()):
                        # Parse the missing library name from the error if possible
                        lib_match = re.search(r'missing main library\s+([\w\.\s]+\d+\.\d+)', err, re.IGNORECASE)
                        missing_lib = lib_match.group(1).strip() if lib_match else "unknown library"
                        self.log(f"  ✗ H5P upload failed — missing content type: {missing_lib}", "error")
                        self.log(f"    Fix: Brightspace Admin → H5P → Content Type Hub → install '{missing_lib}' → re-run Phase A", "warning")
                        await self._recover_list(tab, h5p_frame, list_url)
                        return False
                except Exception:
                    pass

            dismissed = await self._auto_dismiss(tab, ["skip", "proceed without"])
            if dismissed:
                self.log("  → Dismissed skip/grade dialog", "dim")
                await tab.wait_for_timeout(500)

            # Set content title — search all frames for the input
            self.log("  → Setting title…", "dim")
            title_set = False
            title_selectors = [
                'input.h5peditor-text[maxlength="255"]',
                'input.h5peditor-text[name*="title"]',
                'input[name*="title"]',
                'input[id*="title"]',
            ]
            self.log(f"    frames available: {[f.url[:60] for f in tab.frames]}", "dim")
            for frame in tab.frames:
                if "h5p.com" not in frame.url:
                    continue
                try:
                    counts = []
                    for sel in title_selectors:
                        counts.append(f"{sel}={await frame.locator(sel).count()}")
                    self.log(f"    frame {frame.url[:50]!r}: title candidates {', '.join(counts)}", "dim")
                    for sel in title_selectors:
                        if await frame.locator(sel).count() == 0:
                            continue
                        inp = frame.locator(sel).first
                        await inp.wait_for(state="visible", timeout=3000)
                        current = await inp.input_value()
                        self.log(f"    using title selector {sel}; current value: {current!r}", "dim")
                        await inp.click(click_count=3)
                        await inp.fill(item_name)
                        after = await inp.input_value()
                        self.log(f"    title set to: {after!r}", "dim")
                        title_set = True
                        break
                    if title_set:
                        break
                except Exception as te:
                    self.log(f"    frame error: {te}", "dim")
            if not title_set:
                self.log("  ⚠ Title input not found — title not set", "warning")

            self.log("  → Clicking Save…", "dim")
            if not await self._click_when_ready(
                editor_frame,
                'label.save-action',
                "Clicking H5P Save",
            ):
                return False
            await tab.wait_for_timeout(2000)

            if not await self._click_when_ready(
                editor_frame,
                'a.btn-white.btn-folder',
                "Returning to H5P content list",
            ):
                return False
            # Wait for content list to fully reload before returning — prevents
            # "Execution context was destroyed" on the next upload's evaluate call
            try:
                await editor_frame.wait_for_url("**/content**", timeout=8000)
                await editor_frame.locator('tr.content-item').first.wait_for(timeout=8000)
            except Exception:
                await tab.wait_for_timeout(3000)

            self.log(f"  ✓ Uploaded: {item_name}", "success")
            return True
        except Exception as e:
            # First line only — full Playwright call logs drown the GUI log
            self.log(f"  ✗ Upload failed for {item_name}: {str(e).splitlines()[0]}", "error")
            await self._recover_list(tab, h5p_frame, list_url)
            return False

    async def insert_from_list(self, tab, h5p_frame, item_name) -> bool:
        """Find item by name in H5P content list and insert into BS editor."""
        df = self._DEEP_FIND_JS
        try:
            self.log(f"  → Finding '{item_name}' in content list…", "dim")
            name_key = item_name[:25].lower()
            clicked = False
            for _ in range(5):
                try:
                    result = await h5p_frame.evaluate(f"""() => {{
                        var key = {name_key!r};
                        var rows = document.querySelectorAll('tr.content-item');
                        for (var i = 0; i < rows.length; i++) {{
                            var el = rows[i].querySelector('a.fable-title, .content-title, td a');
                            var title = el ? el.textContent.trim().toLowerCase() : '';
                            if (title.includes(key)) {{
                                var btn = rows[i].querySelector('button.lti-inserter, button');
                                if (btn) {{ btn.click(); return title; }}
                            }}
                        }}
                        var all = document.querySelectorAll('button.lti-inserter');
                        if (all.length === 1) {{ all[0].click(); return 'only-one'; }}
                        return null;
                    }}""")
                    if result:
                        self.log(f"  ✓ Clicked Insert for: {result}", "dim")
                        clicked = True
                        break
                except Exception:
                    pass
                await tab.wait_for_timeout(1000)

            if not clicked:
                self.log(f"  ✗ '{item_name}' not found in H5P content list", "error")
                return False

            # Click Insert in dialog footer (d2l-button[data-dialog-action="insert"])
            await tab.wait_for_timeout(1500)
            self.log("  → Clicking Insert in dialog footer…", "dim")
            for _ in range(5):
                try:
                    ok = await tab.evaluate(f"""async () => {{
                        {df}
                        var btn = deepFind(document, function(e) {{
                            if ((e.tagName || '').toUpperCase() !== 'D2L-BUTTON') return false;
                            return e.getAttribute('data-dialog-action') === 'insert';
                        }});
                        if (!btn) return false;
                        var inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
                        (inner || btn).click();
                        return true;
                    }}""")
                    if ok:
                        break
                except Exception:
                    pass
                await tab.wait_for_timeout(1000)

            # "Add Grade Item" dialog appears after Insert — dismiss it
            await tab.wait_for_timeout(1500)
            self.log("  → Checking for 'Add Grade Item' dialog…", "dim")
            dismissed = await self._auto_dismiss(tab, ["proceed without grade item", "proceed without", "skip"])
            if dismissed:
                self.log("  → Dismissed 'Add Grade Item' dialog", "dim")
                await tab.wait_for_timeout(1000)
                # After dismissing, a plain <button class="d2l-button" primary>Insert</button>
                # appears in the Interactives dialog — search all frames for it
                self.log("  → Clicking Insert again after grade item dismiss…", "dim")
                second_insert_clicked = False
                for attempt in range(8):
                    for frame in tab.frames:
                        try:
                            clicked = await frame.evaluate(f"""() => {{
                                {df}
                                // plain <button class="d2l-button"> with text Insert
                                var btns = document.querySelectorAll('button.d2l-button, button[primary]');
                                for (var i = 0; i < btns.length; i++) {{
                                    if ((btns[i].textContent || '').trim().toLowerCase() === 'insert') {{
                                        btns[i].click();
                                        return true;
                                    }}
                                }}
                                // also try deepFind for d2l-button custom element with data-dialog-action
                                var btn = deepFind(document, function(e) {{
                                    if ((e.tagName || '').toUpperCase() !== 'D2L-BUTTON') return false;
                                    return e.getAttribute('data-dialog-action') === 'insert';
                                }});
                                if (btn) {{
                                    var inner = btn.shadowRoot && btn.shadowRoot.querySelector('button');
                                    (inner || btn).click();
                                    return true;
                                }}
                                return false;
                            }}""")
                            if clicked:
                                self.log(f"  ✓ Second Insert clicked (frame: {frame.url[:50]})", "dim")
                                second_insert_clicked = True
                                break
                        except Exception:
                            pass
                    if second_insert_clicked:
                        break
                    await tab.wait_for_timeout(800)
                if not second_insert_clicked:
                    self.log("  ⚠ Second Insert button not found", "warning")
            else:
                self.log("  → No grade item dialog found (ok)", "dim")

            return True
        except Exception as e:
            self.log(f"  ✗ _h5p_insert_from_list error: {e}", "error")
            return False

    async def upload_to_cloud(self, tab, h5p_file) -> tuple:
        df = self._DEEP_FIND_JS
        try:
            # "Import Existing Interactive" only appears in the quiz Creator+ flow — skip if absent.
            self.log("  → Checking for 'Import Existing Interactive' button…", "dim")
            await tab.evaluate(f"""async () => {{
                {df}
                var btn = deepFind(document, function(e) {{
                    var tag = e.tagName && e.tagName.toUpperCase();
                    if (tag !== 'D2L-BUTTON' && tag !== 'BUTTON') return false;
                    return (e.textContent || '').trim() === 'Import Existing Interactive';
                }});
                if (btn) btn.click();
            }}""")
            await tab.wait_for_timeout(1500)

            # Find the H5P frame by URL — the frame selector differs between Creator+ and Insert Stuff paths.
            self.log("  → Waiting for H5P LTI frame…", "dim")
            h5p_frame = None
            for _ in range(10):
                for frame in tab.frames:
                    if "h5p.com" in frame.url:
                        h5p_frame = frame
                        break
                if h5p_frame:
                    break
                await tab.wait_for_timeout(1000)

            if not h5p_frame:
                self.log("  ✗ H5P LTI frame not found (no h5p.com frame appeared)", "error")
                return (None, None)

            self.log(f"  ✓ H5P frame found: {h5p_frame.url[:60]}…", "dim")
            self.log("  → Clicking 'Add Content'…", "dim")
            await h5p_frame.locator('a.create-content, a[href*="/content/create"]').first.click(timeout=10000)

            # Frame navigates to /content/create — re-find it by URL.
            self.log("  → Waiting for H5P editor to load…", "dim")
            await tab.wait_for_timeout(2500)
            h5p_frame = None
            for _ in range(10):
                for frame in tab.frames:
                    if "h5p.com" in frame.url and "content" in frame.url:
                        h5p_frame = frame
                        break
                if h5p_frame:
                    break
                await tab.wait_for_timeout(1000)
            if not h5p_frame:
                self.log("  ✗ H5P editor frame not found after Add Content", "error")
                return (None, None)
            self.log(f"  ✓ H5P editor frame: {h5p_frame.url[:60]}…", "dim")

            # The H5P hub editor renders in a child iframe of the H5P frame.
            # Scan ALL tab.frames to find the one that actually contains the Upload tab.
            self.log("  → Finding H5P hub editor frame (Upload tab)…", "dim")
            hub_frame = None
            for _ in range(10):
                for frame in tab.frames:
                    try:
                        has_it = await frame.evaluate("() => !!document.querySelector('a#h5p-hub-upload')")
                        if has_it:
                            hub_frame = frame
                            break
                    except Exception:
                        pass
                if hub_frame:
                    break
                await tab.wait_for_timeout(1000)
            if not hub_frame:
                self.log("  ✗ H5P hub editor frame not found", "error")
                return (None, None)
            self.log(f"  ✓ Hub frame found: {hub_frame.url[:60]}…", "dim")

            self.log("  → Clicking 'Upload' tab…", "dim")
            await hub_frame.evaluate("""() => {
                var el = document.querySelector('a#h5p-hub-upload');
                if (el) el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
            }""")
            await tab.wait_for_timeout(800)

            self.log("  → Selecting file to upload…", "dim")
            # The upload panel has a styled button over a hidden file input.
            # Try triggering the file chooser via the visible button first.
            try:
                async with tab.expect_file_chooser(timeout=5000) as fc_info:
                    await hub_frame.locator('button.h5p-hub-upload-button').first.click()
                fc = await fc_info.value
                await fc.set_files(str(h5p_file))
            except Exception:
                # Fallback: set file directly on hidden input
                await hub_frame.locator('input[type="file"][accept=".h5p"]').first.set_input_files(str(h5p_file))

            # After file is chosen, the "Use" button activates — click it.
            self.log("  → Clicking 'Use'…", "dim")
            await tab.wait_for_timeout(1000)
            await hub_frame.locator('button.h5p-hub-use-button').first.click(timeout=10000)

            self.log("  → Uploading (waiting for H5P editor)…", "dim")
            await tab.wait_for_timeout(6000)

            self.log("  → Saving…", "dim")
            # "Save and Insert" closes the Insert Stuff dialog and embeds H5P into the BS editor.
            # After it's clicked the dialog frame detaches — do NOT try to interact with it further.
            saved_and_inserted = False
            try:
                await h5p_frame.locator('button.lti-inserter.visible-for-many').first.click(timeout=5000)
                self.log("  ✓ Clicked 'Save and Insert' — H5P embedded in page editor", "dim")
                saved_and_inserted = True
            except Exception:
                pass

            if not saved_and_inserted:
                # Plain Save: stays in H5P.com, then we navigate back to content list.
                try:
                    await h5p_frame.locator('label.save-action').first.click(timeout=10000)
                    self.log("  ✓ Clicked 'Save'", "dim")
                except Exception as e:
                    self.log(f"  ✗ Save failed: {e}", "error")
                    return (None, None)
                await tab.wait_for_timeout(3000)

                content_id = None
                for frame in tab.frames:
                    if "h5p.com" in frame.url and "/content/" in frame.url:
                        m = re.search(r'/content/(\d+)', frame.url)
                        if m:
                            content_id = m.group(1)
                            break
                if not content_id:
                    try:
                        edit_href = await h5p_frame.locator('a.edit-link[href*="/content/"]').first.get_attribute("href", timeout=3000)
                        if edit_href:
                            m = re.search(r'/content/(\d+)', edit_href)
                            if m:
                                content_id = m.group(1)
                    except Exception:
                        pass

                self.log("  → Returning to H5P library…", "dim")
                await h5p_frame.locator('a.btn-white.btn-folder').first.click()
                await tab.wait_for_timeout(1500)

                h5p_type = ""
                try:
                    if content_id:
                        type_el = h5p_frame.locator(f'tr.content-item:has(button[onclick*="{content_id}"]) span.item-content-type')
                        h5p_type = (await type_el.text_content(timeout=5000) or "").strip()
                    else:
                        type_el = h5p_frame.locator('tr.content-item span.item-content-type').first
                        h5p_type = (await type_el.text_content(timeout=5000) or "").strip()
                except Exception:
                    h5p_type = ""

                return (content_id, h5p_type)

            # "Save and Insert" path: dialog is closed, H5P is now in the BS editor.
            # Return content_id=None so caller knows to skip insert_existing.
            await tab.wait_for_timeout(2000)
            return (None, "Page")

        except Exception as e:
            self.log(f"  ✗ _h5p_upload_to_cloud error: {e}", "error")
            return (None, None)

    async def insert_existing(self, tab, content_id) -> bool:
        df = self._DEEP_FIND_JS
        try:
            lti_frame = tab.frame_locator('[data-test-id="lti-launch-frame"]')
            if content_id:
                await lti_frame.locator(f'button[onclick*="{content_id}"]').first.click(timeout=5000)
            else:
                await lti_frame.locator('td.moves-to-bulk-menu button:has-text("Insert")').first.click(timeout=5000)
            await tab.wait_for_timeout(1500)

            await tab.evaluate(f"""async () => {{
                {df}
                var btn = deepFind(document, function(e) {{
                    return e.tagName && e.tagName.toUpperCase() === 'D2L-BUTTON'
                        && e.getAttribute && e.getAttribute('data-dialog-action') === 'insert';
                }});
                if (!btn) return;
                var inner = btn.shadowRoot ? btn.shadowRoot.querySelector('button') : null;
                if (inner) inner.click();
                else btn.click();
            }}""")
            await tab.wait_for_timeout(1500)
            return True
        except Exception as e:
            self.log(f"  ✗ _h5p_insert_existing error: {e}", "error")
            return False

    async def finalize(self, tab, title: str, is_quiz: bool) -> bool:
        df = self._DEEP_FIND_JS
        try:
            # Fill title — prefer exact maxlength match, fall back to any d2l-input
            self.log(f"  → Setting title: {title!r}…", "dim")
            title_filled = False
            for sel in [
                'input.d2l-input[maxlength="256"]' if is_quiz else 'input.d2l-input[maxlength="150"]',
                'input.d2l-input[maxlength="256"]',
                'input.d2l-input[maxlength="150"]',
                'input.d2l-input',
            ]:
                try:
                    loc = tab.locator(sel).first
                    count = await tab.locator(sel).count()
                    visible = await loc.is_visible(timeout=1000) if count else False
                    enabled = await loc.is_enabled(timeout=1000) if count else False
                    self.log(
                        f"    title selector {sel}: count={count}, visible={visible}, enabled={enabled}",
                        "dim",
                    )
                    if count > 0:
                        await loc.wait_for(state="visible", timeout=5000)
                        await loc.click(click_count=3, timeout=5000)
                        await loc.fill(title, timeout=5000)
                        title_filled = True
                        break
                except Exception as title_err:
                    self.log(f"    title selector {sel} failed: {str(title_err).splitlines()[0]}", "dim")
            if not title_filled:
                self.log("  ⚠ Title input not found", "warning")
            await tab.wait_for_timeout(500)

            # Save and Close — use d2l-button.d2l-desktop (documented selector)
            self.log("  → Clicking Save and Close…", "dim")
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
                self.log("  ⚠ d2l-button.d2l-desktop not found — Save and Close may have failed", "warning")

            # After Save and Close: "Add Grade Item" dialog → click "Proceed Without Grade Item"
            await tab.wait_for_timeout(2000)
            dismissed = await self._auto_dismiss(tab, ["proceed without grade item", "proceed without"])
            if dismissed:
                self.log("  → Auto-dismissed 'Add Grade Item' dialog", "dim")
            await tab.wait_for_timeout(2000)
            return True
        except Exception as e:
            self.log(f"  ✗ _h5p_finalize error: {e}", "error")
            return False

    async def open_editor_and_get_frame(self, tab, bs_base, course_id, module_id):
        """Navigate to a module, Create New Page, open Insert Stuff → H5P. Returns h5p_frame or None."""
        df = self._DEEP_FIND_JS
        unit_url = f"{bs_base}/d2l/le/lessons/{course_id}/units/{module_id}"
        self.log(f"  → Opening H5P editor target: course={course_id}, bs_module_id={module_id}", "dim")
        self.log(f"    Unit URL: {unit_url}", "dim")
        await tab.goto(
            unit_url,
            wait_until="domcontentloaded", timeout=20000,
        )
        await tab.wait_for_timeout(2000)

        # Poll instead of single-shot: D2L renders these lazily and a fixed
        # 2s sleep intermittently misses them ("Page tile not found").
        create_ok = False
        for _ in range(15):
            create_ok = await self._eval_in_any_frame(tab, f"""() => {{
                {df}
                var btn = deepFind(document, function(e) {{
                    var tag = (e.tagName || '').toUpperCase();
                    if (tag !== 'D2L-BUTTON') return false;
                    return (e.classList && e.classList.contains('create-new-btn'))
                        || ((e.getAttribute && e.getAttribute('aria-label') || '').includes('Create New'));
                }});
                if (!btn) return false;
                var inner = btn.shadowRoot ? btn.shadowRoot.querySelector('button') : null;
                (inner || btn).click(); return true;
            }}""")
            if create_ok:
                break
            await tab.wait_for_timeout(1000)
        if not create_ok:
            self.log("  ✗ Create New button not found", "error")
            return None
        await tab.wait_for_timeout(2000)

        tile_ok = False
        for _ in range(15):
            tile_ok = await self._eval_in_any_frame(tab, f"""() => {{
                {df}
                var el = deepFind(document, function(e) {{
                    return (e.tagName || '').toUpperCase() === 'A'
                        && e.classList && e.classList.contains('add-material-tile')
                        && (e.getAttribute('href') || '').includes('loadActivity/file/');
                }});
                if (!el) return false;
                el.click(); return true;
            }}""")
            if tile_ok:
                break
            await tab.wait_for_timeout(1000)
        if not tile_ok:
            self.log("  ✗ Page tile not found", "error")
            await self._diagnose(tab, ["add-material", "loadActivity", "tile", "create"])
            await self._diagnose_page_tile_failure(tab, unit_url)
            return None

        try:
            await tab.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await tab.wait_for_timeout(1500)

        dismissed = await self._auto_dismiss(tab, ["proceed without grade item", "proceed without"])
        if dismissed:
            self.log("  → Auto-dismissed 'Proceed Without Grade Item'", "dim")
            await tab.wait_for_timeout(800)

        ok = await self.open_interactives(tab, for_quiz=False)
        if not ok:
            self.log("  ✗ Could not open Insert Stuff / H5P", "error")
            return None

        # Grab the H5P frame ONLY once its content list has actually loaded.
        # Grabbing by URL substring alone can return a frame still parked at
        # /lti/launch when the LTI launch degrades (e.g. a third-party-cookie
        # or expired-session hiccup on h5p.com). That poisoned frame later
        # makes _recover_list GET /lti/launch -> 405, cascading the failure to
        # every remaining item. find_list_frame verifies the list is present.
        h5p_frame = await self.find_list_frame(tab)
        if not h5p_frame:
            parked = any(
                "h5p.com" in f.url and "/lti/launch" in f.url for f in tab.frames
            )
            if parked:
                self.log("  ✗ H5P launched but the content list never loaded "
                         "(frame stuck at /lti/launch).", "error")
                self.log("    Usually a third-party-cookie or expired H5P session "
                         "issue — reopen the tool / re-login, then retry.", "warning")
            else:
                self.log("  ✗ H5P content list frame not found", "error")
        return h5p_frame

    async def _diagnose_page_tile_failure(self, tab, unit_url: str) -> None:
        """Log targeted state when Create New reports success but the Page tile is absent."""
        self.log("  🔍 Page tile failure diagnostics:", "dim")
        self.log(f"    Intended unit URL: {unit_url}", "dim")
        self.log(f"    Current tab URL: {tab.url}", "dim")
        self.log(f"    Frame count: {len(tab.frames)}", "dim")
        for fi, frame in enumerate(tab.frames[:8]):
            label = "main" if fi == 0 else f"frame[{fi}]"
            self.log(f"    [{label}] {frame.url}", "dim")

        scan_js = """() => {
            function textOf(e) {
                return (e && e.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 90);
            }
            function hrefOf(e) {
                return (e && e.getAttribute && e.getAttribute('href') || '').slice(0, 160);
            }
            function clsOf(e) {
                return (e && e.getAttribute && e.getAttribute('class') || '').slice(0, 120);
            }
            function isVisible(e) {
                try {
                    var r = e.getBoundingClientRect();
                    var s = window.getComputedStyle(e);
                    return !!(r.width || r.height) && s.visibility !== 'hidden' && s.display !== 'none';
                } catch (_) {
                    return null;
                }
            }
            function walk(root, visit, depth) {
                if (!root || depth <= 0) return;
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var i = 0; i < all.length; i++) {
                    var e = all[i];
                    visit(e);
                    if (e.shadowRoot) walk(e.shadowRoot, visit, depth - 1);
                }
            }

            var creates = [];
            var tiles = [];
            var pageHints = [];
            walk(document, function(e) {
                var tag = (e.tagName || '').toUpperCase();
                var aria = (e.getAttribute && e.getAttribute('aria-label') || '');
                var cls = clsOf(e);
                var href = hrefOf(e);
                var txt = textOf(e);
                if (creates.length < 6 && (
                    cls.indexOf('create-new-btn') !== -1 ||
                    aria.toLowerCase().indexOf('create new') !== -1 ||
                    txt.toLowerCase().indexOf('create new') !== -1
                )) {
                    creates.push({tag: tag, aria: aria, cls: cls, txt: txt, visible: isVisible(e)});
                }
                if (tiles.length < 12 && tag === 'A' && cls.indexOf('add-material-tile') !== -1) {
                    tiles.push({tag: tag, href: href, cls: cls, txt: txt, visible: isVisible(e)});
                }
                if (pageHints.length < 12 && (
                    href.indexOf('loadActivity/file/') !== -1 ||
                    cls.indexOf('add-material-tile') !== -1 ||
                    txt.toLowerCase() === 'page'
                )) {
                    pageHints.push({tag: tag, href: href, cls: cls, txt: txt, visible: isVisible(e)});
                }
            }, 10);

            return {
                title: document.title,
                location: location.href,
                creates: creates,
                tiles: tiles,
                pageHints: pageHints
            };
        }"""

        for fi, frame in enumerate(tab.frames[:8]):
            label = "main" if fi == 0 else f"frame[{fi}]"
            try:
                info = await frame.evaluate(scan_js)
            except Exception as e:
                self.log(f"    [{label}] diagnostic scan failed: {e}", "dim")
                continue

            self.log(f"    [{label}] title='{info.get('title', '')}' location='{info.get('location', '')}'", "dim")
            creates = info.get("creates") or []
            tiles = info.get("tiles") or []
            hints = info.get("pageHints") or []
            self.log(
                f"    [{label}] create candidates={len(creates)}, "
                f"add-material tiles={len(tiles)}, page/loadActivity hints={len(hints)}",
                "dim",
            )
            for item in (creates[:3] + tiles[:6] + hints[:6]):
                self.log(
                    "      "
                    f"<{item.get('tag','')}> visible={item.get('visible')} "
                    f"class='{item.get('cls','')}' href='{item.get('href','')}' "
                    f"text='{item.get('txt','')}' aria='{item.get('aria','')}'",
                    "dim",
                )

    async def embed_in_brightspace(self, context, page, moodle_items, bs_flat, bs_base, course_id) -> None:
        from urllib.parse import urlparse

        h5p_dir = Path(__file__).parent.parent / "downloads" / "h5p"
        h5p_files = list(h5p_dir.glob("*.h5p"))
        if not h5p_files:
            self.log("  ⚠ No .h5p files found in downloads/h5p/ — skipping embed", "warning")
            return

        section_map: dict = {}
        current_section = ""
        for item in moodle_items:
            if item["type"] == "SECTION":
                current_section = item["name"]
            elif item["type"] == "EXTERNAL" and ("hvp" in item.get("hint", "") or "h5p" in item.get("hint", "")):
                safe = re.sub(r'[^\w\s\-]', '', item["name"]).strip()[:80]
                section_map[safe] = current_section

        bs_mod_map: dict = {}
        bs_mod_orig: dict = {}
        for item in bs_flat:
            if item["kind"] == "MODULE" and item.get("id"):
                nk = _norm(item["title"])
                bs_mod_map[nk] = item["id"]
                bs_mod_orig[nk] = item["title"]

        assignments = []
        for f in h5p_files:
            name = f.stem
            moodle_section = section_map.get(name, "")
            bs_module_title = None
            bs_module_id = None
            if moodle_section:
                close = difflib.get_close_matches(_norm(moodle_section), list(bs_mod_map.keys()), n=1, cutoff=0.55)
                if close:
                    bs_module_title = bs_mod_orig[close[0]]
                    bs_module_id = bs_mod_map[close[0]]
            assignments.append({
                "file": f, "name": name,
                "moodle_section": moodle_section,
                "bs_module_title": bs_module_title,
                "bs_module_id": bs_module_id,
            })

        mod_order = {item["id"]: i for i, item in enumerate(bs_flat) if item["kind"] == "MODULE" and item.get("id")}
        assignments.sort(key=lambda a: mod_order.get(a["bs_module_id"], 9999))

        matched = [a for a in assignments if a["bs_module_id"]]
        self.log("", "dim")
        self.log(f"🎮 H5P Phase 2: {len(assignments)} files  ({len(matched)} matched to BS modules)", "step")
        self.log("   Matched count is Brightspace module matching; H5P cloud status is checked next.", "dim")
        for a in assignments:
            self.log(f"   {a['name']}  →  {a['bs_module_title'] or '⚠ no match'}", "info")

        # Only upload files that actually belong to this Moodle course.
        # Files with no moodle_section are leftovers from other courses in the downloads folder.
        this_course = [a for a in assignments if a["moodle_section"]]
        other_course = [a for a in assignments if not a["moodle_section"]]
        if other_course:
            self.log(f"  ↷ Skipping {len(other_course)} file(s) not found in this Moodle course:", "dim")
            for a in other_course:
                self.log(f"      {a['name']}", "dim")

        phase_b_only = getattr(self, "h5p_phase_b_only", False)

        # ── Phase A: Upload this course's files to H5P cloud ─────────────────
        # The cloud is scanned BEFORE asking permission to upload. Asking first
        # meant a resumed run — where everything is already in the cloud — was
        # gated behind a prompt whose "no" abandoned Phase B as well.
        if phase_b_only:
            self.log("⏭ Phase A skipped — going straight to Phase B", "info")
        else:
            self.log("", "dim")
            self.log("Phase A — checking H5P cloud…", "step")

        if not phase_b_only:
            first = next((a for a in matched), None)
            if not first:
                self.log("  ✗ No matched modules — cannot open editor", "error")
                return

            upload_tab = await context.new_page()
            try:
                h5p_frame = await self.open_editor_and_get_frame(
                    upload_tab, bs_base, course_id, first["bs_module_id"]
                )
                if not h5p_frame:
                    self.log("  ✗ Could not open H5P content list for upload phase", "error")
                    return

                # Bulk-scan the entire content list once — no per-item navigation needed.
                cloud_titles = []
                try:
                    cloud_scan = await h5p_frame.evaluate("""() => {
                        var rows = Array.from(document.querySelectorAll('tr.content-item'));
                        var titles = [];
                        var seen = {};
                        function addTitle(el) {
                            if (!el) return;
                            var txt = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (!txt) return;
                            var low = txt.toLowerCase();
                            if (low === 'add content' || low === 'insert' || low === 'edit') return;
                            if (!seen[low]) {
                                seen[low] = true;
                                titles.push(txt);
                            }
                        }
                        for (var i = 0; i < rows.length; i++) {
                            var links = rows[i].querySelectorAll(
                                'a.fable-title, .content-title, td a, a[href*="/content/"]'
                            );
                            for (var j = 0; j < links.length; j++) addTitle(links[j]);
                        }
                        if (!titles.length) {
                            var fallback = document.querySelectorAll(
                                'a.fable-title, .content-title, tr.content-item td a, a[href*="/content/"]'
                            );
                            for (var k = 0; k < fallback.length; k++) addTitle(fallback[k]);
                        }
                        return { rowCount: rows.length, titleCount: titles.length, titles: titles };
                    }""")
                    cloud_titles = cloud_scan.get("titles") or []
                    self.log(
                        f"  H5P cloud scan: {cloud_scan.get('rowCount', 0)} row(s), "
                        f"{len(cloud_titles)} title(s)",
                        "dim",
                    )
                    if cloud_titles:
                        for sample in cloud_titles[:5]:
                            self.log(f"    cloud sample: {sample[:100]}", "dim")
                    else:
                        self.log(
                            "  ⚠ H5P cloud scan returned no titles; existing uploads may not be detected.",
                            "warning",
                        )
                except Exception as e:
                    self.log(f"  ⚠ H5P cloud scan failed: {str(e).splitlines()[0]}", "warning")
                    # Frame may not be ready yet; upload_one still has a per-item duplicate check.

                def _in_cloud(name):
                    key = re.sub(r'\s+', ' ', name).strip().lower()[:25]
                    for title in cloud_titles:
                        title_key = re.sub(r'\s+', ' ', title).strip().lower()
                        if key and key in title_key:
                            return True, key, title
                    return False, key, None

                to_upload = []
                for item in this_course:
                    found, key, matched_title = _in_cloud(item["name"])
                    if found:
                        self.log(
                            f"  ✓ Already in H5P cloud: {item['name']} "
                            f"(key='{key}', matched='{matched_title[:100]}') — skipping upload",
                            "dim",
                        )
                        item["upload_skipped"] = True
                    else:
                        self.log(
                            f"  ? Not found in H5P cloud: {item['name']} "
                            f"(key='{key}', scanned {len(cloud_titles)} title(s))",
                            "dim",
                        )
                        to_upload.append(item)

                if not to_upload:
                    self.log("  ✓ All files already in cloud — nothing to upload", "success")
                else:
                    if not await self._confirm(
                        f"{len(to_upload)} of {len(this_course)} H5P file(s) still need "
                        "uploading. Upload them now?"
                    ):
                        self.log("⏹ Upload declined — stopping H5P phase", "warning")
                        return
                    self.log(f"  → Uploading {len(to_upload)} file(s)…", "info")
                    for idx, item in enumerate(to_upload, 1):
                        if self._should_stop():
                            self.log("⏸ Stopped by user — aborting H5P upload phase", "warning")
                            return
                        self.log(f"  [{idx}/{len(to_upload)}] Uploading: {item['name']}…", "info")
                        ok = await self.upload_one(upload_tab, h5p_frame, item["file"], item["name"])
                        if not ok:
                            self.log(f"    ✗ Upload failed — will skip insert for this item", "warning")
                            item["upload_failed"] = True
            finally:
                try:
                    await upload_tab.close()
                except Exception:
                    pass

            a_fail = [i for i in this_course if i.get("upload_failed")]
            a_ok_n = len(this_course) - len(a_fail)
            if a_fail:
                self.log(f"Phase A done: {a_ok_n} in cloud, {len(a_fail)} failed", "warning")
            else:
                self.log(f"✓ Phase A complete — {a_ok_n} file(s) in H5P cloud", "success")

        if not phase_b_only:
            if not await self._confirm("Phase A done. Start Phase B (insert each into BS pages)?"):
                return

        # ── Phase B: Create a BS page per item and insert from cloud list ─────
        self.log("", "dim")
        self.log("Phase B — inserting H5P into Brightspace pages…", "step")
        N = len(matched)
        embedded_count = 0

        for idx, item in enumerate(matched, 1):
            if self._should_stop():
                self.log("⏸ Stopped by user — aborting H5P insert phase", "warning")
                break
            if item.get("upload_failed"):
                self.log(f"  [{idx}/{N}] Skipping {item['name']} (upload failed)", "warning")
                self._summary["h5p_failed"].append((item["name"], item.get("bs_module_title") or "?"))
                continue

            name = item["name"]
            bs_module_title = item["bs_module_title"]
            bs_module_id = item["bs_module_id"]
            self.log(f"  [{idx}/{N}] {name}  →  {bs_module_title}", "info")

            # Check live via API — stale bs_flat misses topics added in earlier iterations
            already_in_bs = await self._verify_topic_in_module(
                page, course_id, bs_module_id, name
            )
            if already_in_bs:
                self.log(f"    ✓ Already in Brightspace — skipping", "dim")
                self._summary["h5p_inserted"].append((name, bs_module_title))
                embedded_count += 1
                continue

            tab = await context.new_page()
            try:
                h5p_frame = await self.open_editor_and_get_frame(
                    tab, bs_base, course_id, bs_module_id
                )
                if not h5p_frame:
                    self.log(f"    ✗ Could not open H5P content list — skipping", "error")
                    self._summary["h5p_failed"].append((name, bs_module_title))
                    continue

                ok = await self.insert_from_list(tab, h5p_frame, name)
                if not ok:
                    self.log(f"    ✗ Insert failed — skipping", "error")
                    self._summary["h5p_failed"].append((name, bs_module_title))
                    continue

                ok = await self.finalize(tab, name, is_quiz=False)
                if not ok:
                    self.log(f"    ⚠ Finalize had errors", "warning")

                # Verify via API that the topic actually landed
                confirmed = await self._verify_topic_in_module(
                    page, course_id, bs_module_id, name
                )
                if confirmed:
                    self.log(f"    ✓ Done + verified: {name} → {bs_module_title}", "success")
                    self._summary["h5p_inserted"].append((name, bs_module_title))
                else:
                    self.log(f"    ⚠ Inserted but not confirmed in module via API", "warning")
                    self._summary["h5p_failed"].append((name, bs_module_title))
                embedded_count += 1

            except Exception as e:
                self.log(f"    ✗ Error on {name}: {e}", "error")
                self._summary["h5p_failed"].append((name, bs_module_title))
            finally:
                try:
                    await tab.close()
                except Exception:
                    pass

        self.log("", "dim")
        self.log(f"✅ H5P embed complete: {embedded_count}/{N} inserted", "success")

        if self._notify:
            inserted = self._summary.get("h5p_inserted", [])
            failed   = self._summary.get("h5p_failed", [])
            lines = [f"Inserted into Brightspace: {len(inserted)}"]
            lines += [f"  ✓ {n}" for n, _ in inserted]
            if failed:
                lines.append(f"\nNot inserted: {len(failed)}")
                lines += [f"  ✗ {n}" for n, _ in failed]
                lines.append("\nFailed items usually mean a missing content type "
                             "on H5P.com — see the log for the exact library name.")
            self._notify("H5P results", "\n".join(lines))
