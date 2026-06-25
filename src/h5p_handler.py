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
    ) -> None:
        self.log = log
        self._eval_in_any_frame = eval_in_any_frame
        self._auto_dismiss = auto_dismiss
        self._confirm = confirm
        self._diagnose = diagnose
        self._verify_topic_in_module = verify_topic_in_module
        self._summary = summary
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

            self.log(f"  [{idx}/{len(h5p_items)}] {name}", "info")

            if save_path.exists():
                self.log(f"    ℹ Already downloaded — skipping", "dim")
                success += 1
                skipped += 1
                continue

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

    async def upload_one(self, tab, h5p_frame, h5p_file, item_name) -> bool:
        """Upload one .h5p to H5P cloud via an already-open content list frame. Returns to list after."""
        try:
            # Check if already exists in cloud content list — skip if found.
            # Re-find the live frame if the stored reference has a stale context.
            name_key = item_name[:25].lower()
            try:
                already_exists = await h5p_frame.evaluate(f"""() => {{
                    var key = {name_key!r};
                    var rows = document.querySelectorAll('tr.content-item');
                    for (var i = 0; i < rows.length; i++) {{
                        var el = rows[i].querySelector('a.fable-title, .content-title, td a');
                        var title = el ? el.textContent.trim().toLowerCase() : '';
                        if (title.includes(key)) return true;
                    }}
                    return false;
                }}""")
            except Exception as frame_err:
                if "context was destroyed" in str(frame_err) or "Target closed" in str(frame_err):
                    h5p_frame = await self.find_list_frame(tab)
                    if not h5p_frame:
                        self.log(f"  ✗ H5P content list frame lost — cannot upload {item_name}", "error")
                        return False
                    already_exists = await h5p_frame.evaluate(f"""() => {{
                        var key = {name_key!r};
                        var rows = document.querySelectorAll('tr.content-item');
                        for (var i = 0; i < rows.length; i++) {{
                            var el = rows[i].querySelector('a.fable-title, .content-title, td a');
                            var title = el ? el.textContent.trim().toLowerCase() : '';
                            if (title.includes(key)) return true;
                        }}
                        return false;
                    }}""")
                else:
                    raise
            if already_exists:
                self.log(f"  ✓ Already in H5P cloud: {item_name} — skipping upload", "dim")
                return True

            await h5p_frame.locator('a.create-content, a[href*="/content/create"]').first.click(timeout=10000)
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
                    await hub_frame.locator('button.h5p-hub-upload-button').first.click()
                fc = await fc_info.value
                await fc.set_files(str(h5p_file))
            except Exception:
                await hub_frame.locator('input[type="file"][accept=".h5p"]').first.set_input_files(str(h5p_file))

            await tab.wait_for_timeout(1000)
            self.log("  → Clicking Use…", "dim")
            await hub_frame.locator('button.h5p-hub-use-button').first.click(timeout=10000)
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
                        self.log(f"    Fix: Brightspace Admin → H5P → Content Type Hub → install '{missing_lib}' → re-run Phase A", "dim")
                        # Try to navigate back to content list
                        try:
                            await frame.go_back(timeout=5000)
                        except Exception:
                            pass
                        await tab.wait_for_timeout(2000)
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
            self.log(f"    frames available: {[f.url[:60] for f in tab.frames]}", "dim")
            for frame in tab.frames:
                try:
                    count = await frame.locator('input.h5peditor-text[maxlength="255"]').count()
                    self.log(f"    frame {frame.url[:50]!r}: found {count} title input(s)", "dim")
                    if count > 0:
                        inp = frame.locator('input.h5peditor-text[maxlength="255"]').first
                        current = await inp.input_value()
                        self.log(f"    current title value: {current!r}", "dim")
                        await inp.click(click_count=3)
                        await inp.fill(item_name)
                        after = await inp.input_value()
                        self.log(f"    title set to: {after!r}", "dim")
                        title_set = True
                        break
                except Exception as te:
                    self.log(f"    frame error: {te}", "dim")
            if not title_set:
                self.log("  ⚠ Title input not found — title not set", "warning")

            self.log("  → Clicking Save…", "dim")
            await editor_frame.locator('label.save-action').first.click(timeout=10000)
            await tab.wait_for_timeout(2000)

            await editor_frame.locator('a.btn-white.btn-folder').first.click(timeout=10000)
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
            self.log(f"  ✗ Upload failed for {item_name}: {e}", "error")
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
                    if await loc.count() > 0:
                        await loc.click(click_count=3)
                        await loc.fill(title)
                        title_filled = True
                        break
                except Exception:
                    pass
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
        await tab.goto(
            f"{bs_base}/d2l/le/lessons/{course_id}/units/{module_id}",
            wait_until="domcontentloaded", timeout=20000,
        )
        await tab.wait_for_timeout(2000)

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
        if not create_ok:
            self.log("  ✗ Create New button not found", "error")
            return None
        await tab.wait_for_timeout(2000)

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
        if not tile_ok:
            self.log("  ✗ Page tile not found", "error")
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

        h5p_frame = None
        for _ in range(10):
            for frame in tab.frames:
                if "h5p.com" in frame.url:
                    h5p_frame = frame
                    break
            if h5p_frame:
                break
            await tab.wait_for_timeout(1000)
        return h5p_frame

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

        if not phase_b_only:
            if not await self._confirm(f"Found {len(this_course)} H5P file(s) for this course. Start Phase A (upload to cloud)?"):
                return

        # ── Phase A: Upload this course's files to H5P cloud ─────────────────
        if phase_b_only:
            self.log("⏭ Phase A skipped — going straight to Phase B", "info")
        else:
            self.log("", "dim")
            self.log("Phase A — uploading all H5P files to cloud…", "step")

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
                    cloud_titles = await h5p_frame.evaluate("""() => {
                        var rows = document.querySelectorAll('tr.content-item');
                        var titles = [];
                        for (var i = 0; i < rows.length; i++) {
                            var el = rows[i].querySelector('a.fable-title, .content-title, td a');
                            if (el) titles.push(el.textContent.trim().toLowerCase());
                        }
                        return titles;
                    }""")
                except Exception:
                    pass  # frame may not be ready yet; upload_one will handle it

                def _in_cloud(name):
                    key = name[:25].lower()
                    return any(key in t for t in cloud_titles)

                to_upload = []
                for item in this_course:
                    if _in_cloud(item["name"]):
                        self.log(f"  ✓ Already in H5P cloud: {item['name']} — skipping upload", "dim")
                        item["upload_skipped"] = True
                    else:
                        to_upload.append(item)

                if not to_upload:
                    self.log("  ✓ All files already in cloud — nothing to upload", "success")
                else:
                    self.log(f"  → {len(to_upload)} file(s) need uploading", "info")
                    for idx, item in enumerate(to_upload, 1):
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

            self.log("✓ Phase A complete — all files uploaded to H5P cloud", "success")

        if not phase_b_only:
            if not await self._confirm("Phase A done. Start Phase B (insert each into BS pages)?"):
                return

        # ── Phase B: Create a BS page per item and insert from cloud list ─────
        self.log("", "dim")
        self.log("Phase B — inserting H5P into Brightspace pages…", "step")
        N = len(matched)
        embedded_count = 0

        for idx, item in enumerate(matched, 1):
            if item.get("upload_failed"):
                self.log(f"  [{idx}/{N}] Skipping {item['name']} (upload failed)", "warning")
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
