# Brightspace Pages Automator — Project Guide

# Claude Code System Guidelines
- Do NOT spawn background subagents or parallel task agents ask for permission if you do want to spawn them.
- Complete all file modifications, reading, and terminal execution directly within the main session.
- If a task requires subtask decomposition, print the plan to the main terminal first.


## What This Project Does

Automates editing HTML content inside Brightspace (D2L) LMS pages using Playwright and Gemini AI.
Three active tabs:

- **Checker tab** — compares Brightspace course structure (via D2L API) against a Moodle course's item list, reporting exact/fuzzy/missing matches.
- **Unit Collector tab** — scrapes all topic pages from a Brightspace unit and combines them into one collapsible HTML file, then writes it to a target blank page.
- **Page Changer tab** — navigates to a Brightspace section or single topic page, clicks Options → Edit, opens the Source Code editor, runs Gemini AI to restyle the HTML using a theme-based prompt, and writes it back. Supports batch processing.

---

## Architecture

```
run.bat / run.sh         Entry point — activates venv, runs gui.py
gui.py                   PySide6 GUI (feature/gui-redesign) — sidebar + QStackedWidget panels
src/
  gui_panels.py          CheckerPanel, CollectorPanel, RestylePanel, SettingsPanel
  gui_sidebar.py         Sidebar + StepButton widgets
  gui_styles.py          QSS stylesheet + color constants
  gui_icons.py           QPainter icon library
  gui_log.py             LogWidget (zoomable, auto-scroll)
  automator.py           Page Changer logic (PageAutomator class)
  unit_collector.py      Unit Collector logic (UnitCollector class)
  content_checker.py     Checker logic (ContentChecker class)
  style_migrator.py      PRESERVED (not in GUI) — Style Migrator logic
  ai_styler.py           Shared Gemini helper
  browser.py             Playwright launch + auto-login (Brightspace, SSO, Moodle)
  config.py              Shared constants
  api_config.py          API config helpers
.env                     GEMINI_API_KEY
```

### GUI branch note
`feature/gui-redesign` is a full rewrite from CustomTkinter → PySide6. All `src/` backend files are read-only from the GUI's perspective. Credentials stored via `keyring`, exposed as properties on `MainWindow` and passed to each worker.

### Style Migrator
Removed from GUI in v0.6.0. Code preserved in `src/style_migrator.py`. To re-add, wire `StyleMigrator` into `gui.py` with the same worker-thread + queue pattern as other panels.

---

## The D2L Editor — Known Quirks

### 1. Toolbar overflow ("chomped" buttons)
When the toolbar is too narrow, Source Code gets hidden into a `⋯` overflow menu (`data-toolbar-item-state="chomped"`). Fix: JS `deepFind` for a `<button>` whose `innerHTML` contains SVG paths `M2,7`, `M9,7`, `M16,7` (three-dot icon) → click it → wait 700ms → then find Source Code.

### 2. TinyMCE iframe pointer-event interception
Playwright's `.click()` fails — the TinyMCE `<iframe>` intercepts pointer events. Fix: `page.evaluate()` calling `btn.shadowRoot.querySelector('button').click()` directly.

### 3. Shadow DOM everywhere
All D2L components use shadow DOM. A `deepFind(root, fn, depth)` helper traverses shadow roots recursively. Both fixes above are applied in `automator.py` and `style_migrator.py`.

---

## Navigation Flow (Page Changer)

```
1. goto(url)
2. wait for iframe (up to 8s) + wait 3s for smart-curriculum to render
3. _find_locator_any_frame('d2l-button-icon.content-options-btn', retries=15, delay=1s)
4. btn.click()  → Options menu opens
5. _find_locator_any_frame('d2l-menu-item#optEdit') → edit_btn.click()
6. wait for domcontentloaded + 800ms
7. JS: click three-dots overflow button (if present)
8. _find_locator_any_frame('d2l-htmleditor-button[cmd="d2l-source-code"]')
9. JS shadow DOM click on inner <button>  → Source Code dialog opens
10. Extract HTML from CodeMirror editor (shadow DOM aware JS)
11. AI call → write new HTML back via JS (CM6 view dispatch or execCommand fallback)
12. Click OK/Update → Click Save and Close
```

---

## Migration Context

Okanagan College imports Moodle courses into Brightspace via `.mbz` backup. The import loses styling, LTI tools (Cengage, Kaltura, etc.), H5P activities, and leaves some links pointing to `mymoodle.okanagan.bc.ca`. This tool:
1. **Finds missing/broken content** (Checker tab)
2. **Re-hosts Moodle-linked files** in Brightspace and patches the URLs
3. **Flags LTIs/H5P** for manual attention
4. **Restyls pages** to OC's Brightspace theme

Source of truth for what needs downloading: Brightspace HTML — scan for `mymoodle.okanagan.bc.ca` hrefs, download only those. See `docs/MIGRATION_STATUS.md` for current pipeline status and next steps.

---

## Running Locally

```bash
# Windows
.\run.bat

# Linux/Mac
source venv/bin/activate && python gui.py
```

First run installs venv, dependencies, and Playwright Chromium automatically. Login is saved and reused across sessions.

## Environment

```
GEMINI_API_KEY=your_key_here   # in .env file
```

---

## Playwright — Token Efficiency (IMPORTANT)

Always use `browser_evaluate` (targeted JS) for data extraction — **never** `browser_snapshot`.

- Snapshots return the full accessibility tree (50–60 KB+) and burn tokens fast.
- `browser_evaluate` returns only what you ask for (usually under 1 KB).

```js
// Good — extract exactly what you need
page.evaluate(() =>
  [...document.querySelectorAll('a[href*="pluginfile"]')]
    .map(a => ({ name: a.textContent.trim(), url: a.href }))
)

// Bad — returns entire page accessibility tree
browser_snapshot()
```

Only use `browser_snapshot` when you genuinely need to discover unknown page structure or find element refs to click on. Switch to `browser_evaluate` as soon as you know what to look for.
