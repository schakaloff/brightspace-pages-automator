# Brightspace Pages Automator — Project Guide

## What This Project Does

Automates editing HTML content inside Brightspace (D2L) LMS pages using Playwright and Gemini AI.
It has two tabs that will eventually work together:

- **Automator tab** (friend's work) — navigates to a Brightspace page, clicks Options → Edit, opens the Source Code editor, runs Gemini AI to restyle the HTML using a reference style, and writes it back.
- **Style Migrator tab** (your work) — same navigation flow, but pulls content from a Moodle page as the style reference instead of a manually pasted template. Sends both HTMLs to Gemini to produce a Moodle-styled Brightspace page.

**Future goal:** Combine both tabs so the Automator uses the Moodle-scraping pipeline from the Style Migrator to auto-generate style references, producing one unified AI prompt.

---

## Architecture

```
run.bat                  Entry point — activates venv, runs gui.py directly
gui.py                   CustomTkinter GUI with two tabs; spawns worker threads
src/
  automator.py           Automator tab logic (PageAutomator class)
  style_migrator.py      Style Migrator tab logic (StyleMigrator class)
  page_previewer.py      Style Preview tab logic (PagePreviewer class)
  ai_styler.py           Shared Gemini helper used by automator tab
  browser.py             Shared Playwright launch + login wait loop
  config.py              Shared constants (session file path etc.)
  api_config.py          API config helpers
.env                     GEMINI_API_KEY (loaded by the app at startup)
```

### Shared browser session
Both tabs call `browser.launch_browser()` + `wait_for_login()` from `browser.py`. Session cookies are saved to a shared file so login only happens once per machine restart.

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

## Navigation Flow (both tabs)

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
11. Extract HTML from textarea (shadow DOM aware JS)
12. [tab-specific AI call]
13. Write new HTML back to textarea via JS native setter + dispatch events
14. Click OK/Update in dialog
15. Click Save and Close on edit page
```

---

## Tab Differences

| Step | Automator tab | Style Migrator tab |
|---|---|---|
| Style source | Manually pasted HTML template in GUI | Scraped from a Moodle URL in a new browser tab |
| AI prompt | `ai_styler.py` PROMPT_TEMPLATE | `_MIGRATOR_PROMPT` in style_migrator.py |
| Gemini model | gemini-2.0-flash | gemini-2.0-flash |
| After AI | Writes back + leaves browser open | Writes back + clicks Save and Close |

---

## Git Branches

| Branch | Owner | Purpose |
|---|---|---|
| `main` | shared | stable releases |
| `dev` | shared | integration branch |
| `feature/source-code-button-fix` | you | shadow DOM click fix, style migrator fixes |
| friend's branch | friend | automator tab improvements |

**Workflow:** both feature branches → PR into `dev` → test → PR into `main`.

---

## What's Been Fixed (your branch)

- `src/automator.py` — overflow expand + JS shadow DOM click for Source Code button
- `src/style_migrator.py` — same fixes; also where the actual bug was (user was on this tab)
- `gui.py` — `sys.modules.pop('automator', None)` before each worker import so code changes take effect without app restart
- Options button retries increased to 15 × 1s + 3s initial wait for slow page loads
- `.env` — Gemini API key updated

---

## What Still Needs Work

- [ ] Confirm Source Code dialog actually opens end-to-end (still testing)
- [ ] Verify HTML extraction from the dialog textarea works after the JS click
- [ ] Verify AI-restyled HTML writes back correctly and Save and Close succeeds
- [ ] **Combine the two tabs** — Automator should accept a Moodle URL as an optional style source and use the Style Migrator scraping pipeline instead of a manual paste
- [ ] Clean up diagnostic/debug logging added during the click debugging (the `print("automator.py v4 loaded")` line etc.)
- [ ] Add `__pycache__/` to `.gitignore` so compiled bytecode is never committed

---

## Running Locally

```
.\run.bat
```

- First run installs the venv, dependencies, and Playwright Chromium automatically.
- Login happens once; session is saved and reused.
- Use **Style Migrator** tab for Moodle→Brightspace restyling.
- Use **Automator** tab for applying a custom HTML template style.

## Environment

```
GEMINI_API_KEY=your_key_here   # in .env file
```
