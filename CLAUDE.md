# Brightspace Pages Automator — Project Guide

## What This Project Does

Automates editing HTML content inside Brightspace (D2L) LMS pages using Playwright and Gemini AI.
It has three active tabs:

- **Page Changer tab** — navigates to a Brightspace section or single topic page, clicks Options → Edit, opens the Source Code editor, runs Gemini AI to restyle the HTML using a theme-based prompt, and writes it back. Supports batch processing across all topics in a section.
- **Unit Collector tab** — scrapes all topic pages from a Brightspace unit and combines them into one collapsible HTML file, then writes it to a target blank page.
- **Checker tab** — compares Brightspace course structure (via D2L API) against a Moodle course's item list, reporting exact/fuzzy/missing matches.

---

## Removed: Style Migrator Tab

The **Style Migrator** tab was removed from the GUI in v0.6.0 to streamline the interface.
The underlying code is **fully preserved** in `src/style_migrator.py` and is not deleted.

### What it did
- Took a Brightspace topic URL + a Moodle page URL.
- Opened Brightspace → Options → Edit → Source Code, extracted the HTML.
- Opened the Moodle URL in a new tab, waited for the user to confirm login, then scraped the main content.
- Sent both HTMLs to Gemini 2.0 Flash to produce a Moodle-styled Brightspace page.
- Detected broken Moodle links in the output and showed a link-fixer panel so the user could paste replacement Brightspace URLs before saving.
- Wrote the styled HTML back and clicked Save and Close.

### How to re-add it
Wire `StyleMigrator` from `src/style_migrator.py` back into `gui.py` following the same
worker-thread + queue pattern as the other tabs. Key pieces needed in `gui.py`:
- `self._sm_log_queue = queue.Queue()`
- `self._sm_link_response_queue = queue.Queue()`
- `self._sm_link_entries = {}`
- `self._sm_moodle_ready_event = None`
- `self.after(100, self._sm_poll_log)` in `__init__`
- Add `self._sm_run_btn` to `_any_job_running()`
- Add `sm_bs_url`, `sm_moodle_url`, `primary_color`, `gemini_api_key` back to `_persist_config()`
- Restore `_build_style_migrator_tab`, `_sm_start_run`, `_sm_poll_log`, `_build_link_panel`,
  `_show_link_fixer`, `_hide_link_fixer`, `_apply_links`, `_sm_moodle_ready` methods

---

## Architecture

```
run.bat                  Entry point — activates venv, runs gui.py directly
gui.py                   CustomTkinter GUI with three tabs; spawns worker threads
src/
  automator.py           Page Changer tab logic (PageAutomator class)
  unit_collector.py      Unit Collector tab logic (UnitCollector class)
  content_checker.py     Checker tab logic (ContentChecker class)
  style_migrator.py      PRESERVED — Style Migrator logic (StyleMigrator class, not in GUI)
  page_previewer.py      Style Preview tab logic (PagePreviewer class)
  ai_styler.py           Shared Gemini helper used by Page Changer tab
  browser.py             Shared Playwright launch + login wait loop
  config.py              Shared constants (session file path etc.)
  api_config.py          API config helpers
.env                     GEMINI_API_KEY (loaded by the app at startup)
```

### Shared browser session
All tabs call `browser.launch_browser()` + `wait_for_login()` from `browser.py`. Session cookies are saved to a shared file so login only happens once per machine restart.

### Module reload fix
`gui.py` does `sys.modules.pop('automator', None)` before every import so live edits to `automator.py` take effect without restarting the app.

---

## The D2L Editor — Known Quirks

The Brightspace HTML editor is a D2L web component stack. Every interaction has two problems:

### 1. Toolbar overflow ("chomped" buttons)
When the editor toolbar is too narrow, lower-priority buttons (including **Source Code**) get hidden into a `⋯` overflow menu. Their DOM attribute becomes `data-toolbar-item-state="chomped"`. You must click the three-dots button first to expand the overflow before Source Code becomes clickable.

**Fix implemented:** Before searching for Source Code, run a JS `deepFind` looking for a native `<button>` whose `innerHTML` contains the SVG paths `M2,7`, `M9,7`, `M16,7` (the three-dot icon). Click it, wait 700ms, then search for Source Code.

### 2. TinyMCE iframe pointer-event interception
Even when the Source Code button is visible, Playwright's normal `.click()` fails because the TinyMCE editor `<iframe>` sits inside the same `d2l-htmleditor-editor-container` and intercepts all pointer events at that coordinate.

**Fix implemented:** Instead of `locator.click()`, use `page.evaluate()` to run JS that calls `btn.shadowRoot.querySelector('button').click()` directly on the DOM node. This bypasses Playwright's pointer-event hit testing entirely.

Both fixes are applied in **both** `automator.py` and `style_migrator.py`.

### 3. Shadow DOM everywhere
All D2L components (`d2l-htmleditor-button`, `d2l-button-icon`, etc.) use shadow DOM. A `deepFind(root, fn, depth)` helper is used throughout to traverse shadow roots recursively when standard `querySelector` can't reach the target.

---

## Navigation Flow (Page Changer & Style Migrator)

```
1. goto(url)
2. wait for iframe (up to 8s) + wait 3s for smart-curriculum to render
3. _find_locator_any_frame('d2l-button-icon.content-options-btn', retries=15, delay=1s)
4. btn.click()  → Options menu opens
5. _find_locator_any_frame('d2l-menu-item#optEdit')
6. edit_btn.click()  → navigates to edit page
7. wait for domcontentloaded + 800ms
8. JS: click three-dots overflow button (if present)
9. _find_locator_any_frame('d2l-htmleditor-button[cmd="d2l-source-code"]')
10. JS shadow DOM click on inner <button>  → Source Code dialog opens
11. Extract HTML from CodeMirror editor (shadow DOM aware JS)
12. [tab-specific AI call]
13. Write new HTML back via JS (CM6 view dispatch or execCommand fallback)
14. Click OK/Update in dialog
15. Click Save and Close on edit page
```

---

## Tab Differences

| Step | Page Changer tab | Style Migrator (preserved, not in GUI) |
|---|---|---|
| Style source | Theme prompt file (`prompts/<theme>.txt`) + `templates/style_reference.html` | Scraped from a Moodle URL in a new browser tab |
| AI prompt | `ai_styler.py` per-theme prompt | `_MIGRATOR_PROMPT` in style_migrator.py |
| Gemini model | gemini-2.5-flash | gemini-2.0-flash |
| Batch support | Yes — section URL scrapes all topics, user picks start + count | No — single page only |
| After AI | Writes back + leaves browser open | Writes back + link fixer + clicks Save and Close |

---

## Migration Context — How Brightspace Gets Content

Okanagan College migrates courses from Moodle to Brightspace using Brightspace's built-in
**Moodle backup import tool** (upload the `.mbz` backup, Brightspace imports it).

This import is imperfect:
- Most content (files, pages, quizzes) transfers across, but layout and styling is lost
- **LTI tools almost always fail** — Cengage, Kaltura, Top Hat, Pearson, etc. don't transfer
- H5P activities don't transfer (Brightspace H5P is a separate manual upload)
- Some file links inside label/page HTML still point back to `mymoodle.okanagan.bc.ca`

So Brightspace already has a version of the course — it's not empty. The job of this tool is:
1. **Find what's missing or broken** (Checker tab comparison)
2. **Re-host files** that are linked from Brightspace topics but still point to Moodle
3. **Flag LTIs/H5P** that need manual instructor attention
4. **Style the pages** to match OC's Brightspace theme (Page Changer / Style Migrator tabs)

When scanning for "what needs downloading," the source of truth is **Brightspace's HTML** —
scan each topic for `mymoodle.okanagan.bc.ca` hrefs, download only those files, re-upload
to Brightspace, and replace the URLs. Do NOT blindly download everything from Moodle.

---

## Git Branches

| Branch | Owner | Purpose |
|---|---|---|
| `main` | shared | stable releases |
| `dev` | shared | integration branch |

---

## What's Been Fixed

- `src/automator.py` — overflow expand + JS shadow DOM click for Source Code button
- `src/style_migrator.py` — same fixes; CodeMirror 6 extraction via `.cm-content` traversal
- `gui.py` — `sys.modules.pop('automator', None)` before each worker import so code changes take effect without app restart
- Options button retries increased to 15 × 1s + 3s initial wait for slow page loads
- Style Migrator tab removed from GUI in v0.6.0 (code preserved in `src/style_migrator.py`)

---

## Migration Pipeline Status (as of 2026-06-17)

### ✅ Done
- Moodle scrape: sections, items, accordions (Bootstrap .card structure detected + displayed)
- H5P download: role-switch → settings → JS checkbox → Save → Reuse → download
  - Covers mod/hvp and mod/h5pactivity; fresh page per item; pause point before downloads
  - Downloaded files go to `downloads/h5p/<name>.h5p`
- Brightspace TOC fetch + comparison log (exact / fuzzy / missing / found_in_search / found_in_content)
- Moodle link scan: every Brightspace topic HTML fetched via API, Moodle hrefs collected
  with topic_id so they can be patched back
- Re-link method built (`_relink_moodle_files`): download → upload → HTML patch → PUT back
  - **NOT YET TESTED against a real course** — this is the immediate next thing to run

### 🔜 Next (in order)
1. **Test re-link**: run Checker with real BS URL + "Re-link files" ticked, paste log output
2. **H5P Brightspace upload**: need user to walk through Create New → Page → H5P upload
   flow in browser and share HTML — then automate Steps 8+ from H5P_DOWNLOAD_STEPS.txt
3. **Accordion file downloads**: files inside accordion cards use mod/resource/view.php
   links (not pluginfile.php) — _download_moodle_files currently skips these
4. **Log file**: logs/YYYY-MM-DD.txt per run (mentioned in H5P_DOWNLOAD_STEPS.txt)
5. **Unit Collector (Filip)** runs last — after all links and files are correct

### Key decisions made
- Files embedded as links in Moodle labels usually don't transfer via the backup import
- Re-link approach: scan BS HTML for mymoodle URLs → check if file already in BS
  (notify user if yes) → if not, download from Moodle + bulk upload to matching section
- H5P gets its own new Brightspace Page per activity in the matched section
- Unit Collector assembles AFTER re-link and H5P pages are in place

## What Still Needs Work (original items)

- [ ] Verify Unit Collector batch flow works end-to-end
- [ ] Clean up diagnostic/debug logging in `automator.py` if any remains

---

## Running Locally

```
.\run.bat
```

- First run installs the venv, dependencies, and Playwright Chromium automatically.
- Login happens once; session is saved and reused.
- Use **Page Changer** tab for AI-restyling one or more Brightspace pages.
- Use **Unit Collector** tab to combine a full unit into one collapsible page.
- Use **Checker** tab to verify Moodle → Brightspace migration completeness.

## Environment

```
GEMINI_API_KEY=your_key_here   # in .env file
```
