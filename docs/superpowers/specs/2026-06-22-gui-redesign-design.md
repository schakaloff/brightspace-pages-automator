# GUI Redesign — Design Spec
**Date:** 2026-06-22  
**Branch:** `feature/gui-redesign`  
**Scope:** Complete GUI layer rewrite from CustomTkinter → PySide6. All backend files untouched.

---

## 1. Goal

Replace the generic CustomTkinter tab interface with a purposeful, editorial dark UI that reflects the tool's actual nature: a 3-step sequential pipeline for Moodle → Brightspace migration. The design must feel like real software, not a Python script with a GUI bolted on.

Anti-goals: no emoji, no gradient cards, no generic AI aesthetics, no decoration that doesn't carry information.

---

## 2. Framework Change

| | Before | After |
|---|---|---|
| GUI | `customtkinter` + `CTkMessagebox` | `PySide6` |
| Icons | Emoji + PIL app icon | QPainter-drawn (vector, DPI-aware) |
| Stylesheets | CTk theme dicts | QSS (CSS-like) |
| Animation | None | `QPropertyAnimation` (hover, status pulse) |
| Font | System default | "Segoe UI" UI / "Cascadia Code" log |

**Dependencies:**
- Add: `PySide6>=6.7`
- Remove: `customtkinter`, `CTkMessagebox`
- Keep: `Pillow` (used by `icon_art.py` and installer assets only)

**PyInstaller `.spec` files** need PySide6 hooks — update both `brightspace_automator.spec` and `brightspace_automator_mac.spec`. All other entry points (`run.bat`, `run.sh`) unchanged.

---

## 3. Color Palette

```python
# Background layers
BG          = "#0d0d12"   # Main window
SIDEBAR     = "#0a0a0f"   # Sidebar panel (slightly darker)
PANEL       = "#13131b"   # Cards, input areas
BORDER      = "#1c1c2a"   # Subtle borders
BORDER_ACT  = "#2a2a3f"   # Hover / focus borders

# Text
TEXT_PRI    = "#dde0ee"   # Primary
TEXT_SEC    = "#636780"   # Secondary / placeholder
TEXT_FAINT  = "#383b50"   # Labels, dividers

# OC Brand (the only two colors that stand out)
OC_TEAL     = "#005F63"   # Primary action (buttons, active state accent)
OC_TEAL_MID = "#007a80"   # Hover
OC_TEAL_BG  = "#002e30"   # Teal-tinted subtle backgrounds
OC_ORANGE   = "#FF8204"   # Active step numbers ONLY — the single pop of color

# Status
DONE        = "#22c55e"   # Green
RUNNING     = "#f59e0b"   # Amber (animated)
LOCKED      = "#252535"   # Muted bg for locked steps
ERROR       = "#ef4444"   # Red

# Log tag colors (same semantic meaning as before)
LOG_INFO    = "#b0bcd4"
LOG_SUCCESS = "#4caf50"
LOG_ERROR   = "#ef5350"
LOG_WARNING = "#f0a500"
LOG_STEP    = "#4dd0e1"
LOG_DIM     = "#333850"
```

---

## 4. Typography

| Role | Font | Size | Weight |
|---|---|---|---|
| UI general | "Segoe UI" (Win) / system-ui (Mac) | 12px | Regular |
| Section header | same | 20px | SemiBold |
| Form label | same | 10px | Bold + uppercase + letter-spacing |
| Step name (sidebar) | same | 13px | SemiBold |
| Step number chip | same | 12px | Bold |
| Button text | same | 13px | SemiBold |
| Log | "Cascadia Code" → "JetBrains Mono" → "Consolas" | 13px default | Regular |

No custom font bundling required. "Cascadia Code" ships with Windows Terminal and is widely installed on modern Windows 11; "Consolas" is the guaranteed fallback.

---

## 5. Layout

**Window:** 960×760 minimum, resizable.

```
┌─────────────────────────────────────────────────────────────┐
│ SIDEBAR (160px fixed)  │  CONTENT AREA (stretches)          │
│                        │                                     │
│  [app logo 32×32]      │  [active step's panel]             │
│  Brightspace           │                                     │
│  Automator             │                                     │
│  ──────────────        │                                     │
│                        │                                     │
│  [1] [○] Checker  ●    │                                     │
│  [2] [○] Collect  ○    │                                     │
│  [3] [○] Restyle  ○    │                                     │
│                        │                                     │
│  ──────────────        │                                     │
│  [★] Settings          │                                     │
└─────────────────────────────────────────────────────────────┘
```

The sidebar is a `QWidget` with fixed width. The content area is a `QStackedWidget` — each step's panel is one page.

---

## 6. Sidebar

### App Header (top of sidebar)

- QPainter-drawn logo (32×32, reuses `draw_app_icon` concept but as QPixmap)
- "Brightspace" in 10px faint text
- "Automator" in 14px semibold
- Thin horizontal divider below

### Step Buttons (`StepButton` — custom `QPushButton` subclass)

Each button is 52px tall, full sidebar width.

```
┌────────────────────────────────────┐
│  [1]  [icon]  Checker         ●   │
└────────────────────────────────────┘
  ↑      ↑       ↑             ↑
  chip  16×16   13px          8×8
  20×14 icon    semibold      status
  OC    QPaint  text          dot
  orange
```

- **Step chip**: 20×14px rounded rect, OC orange text when active/done, TEXT_FAINT when locked
- **Icon**: 16×16, QPainter drawn, inherits color from step state
- **Label**: step name
- **Status dot**: 8×8px circle — green (done), amber (running, animated), empty outline (pending), padlock icon (locked)
- **Active state**: 3px left border in OC_TEAL, PANEL bg, full-opacity text
- **Done state**: no border, PANEL bg, green status dot
- **Locked state**: 40% opacity on entire button, LOCKED bg, padlock icon
- **Hover** (unlocked): bg lightens to `#18182a`, 150ms ease transition via `QPropertyAnimation`

### Settings Button (bottom of sidebar)

Same style as step buttons but without number chip. Uses the 8-point star icon.

---

## 7. QPainter Icons (`src/gui_icons.py`)

All icons drawn at 32×32 canvas, returned as `QIcon` (scales cleanly). Color is passed in so a single function renders for any state.

| Name | Description |
|---|---|
| `icon_checker` | Two overlapping circles (Venn) — represents comparison |
| `icon_collect` | Three horizontal bars, slightly staggered left-to-right |
| `icon_restyle` | Diagonal stroke with small filled square at the tip (brush) |
| `icon_settings` | 8-point star (not the cliché gear — smaller, lighter) |
| `icon_run` | Right-pointing solid triangle |
| `icon_next` | Long arrow shaft + arrowhead pointing right |
| `icon_done` | Circle outline + checkmark path inside |
| `icon_locked` | Rectangle (body) + arc (shackle), padlock |
| `icon_running` | 270° arc sweep — rotated via `QPropertyAnimation` |
| `icon_app` | Window icon: PIL `draw_app_icon()` output converted to `QPixmap` via `ImageQt` — `icon_art.py` unchanged |

---

## 8. Log Widget (`src/gui_log.py` — `LogWidget`)

Custom `QTextEdit` subclass. All tabs share the same class.

### Features

| Feature | Implementation |
|---|---|
| Read-only, selectable | `setReadOnly(True)` |
| Font | Cascadia Code / Consolas fallback, 13px default |
| **Zoom** | `Ctrl+Scroll` (built-in QTextEdit) + `Ctrl++` / `Ctrl+-` |
| **Zoom badge** | Small `QLabel` overlay bottom-right; fades in on zoom change, fades out after 1.5s via `QTimer` |
| **Auto-scroll** | Pinned to bottom by default; releases when user scrolls up; re-pins when scrolled back to bottom |
| **Search** | Hidden by default; `Ctrl+F` reveals; incremental `find()` with highlight |
| Color tags | `QTextCharFormat` per tag — same semantic set as current |
| Clear on new run | `clear()` at run start |

### Color tag map
```
info    → LOG_INFO    (#b0bcd4)
success → LOG_SUCCESS (#4caf50)
error   → LOG_ERROR   (#ef5350)
warning → LOG_WARNING (#f0a500)
step    → LOG_STEP    (#4dd0e1)
dim     → LOG_DIM     (#333850)
```

### Zoom badge appearance
```
┌─────────────────────────────────────┐
│ log text…                           │
│                                     │
│                              [125%] │  ← fades out after 1.5s
└─────────────────────────────────────┘
```

---

## 9. Settings Panel

A full-height panel (same stacking as other tabs) shown when "Settings" is clicked in sidebar.

### Contents

1. **"GEMINI API KEY"** section  
   - Password `QLineEdit` with show/hide toggle button  
   - Loaded on startup in priority order: `.env` → `src/api_config.py` → `user_config.json`  
   - Saved to `user_config.json` debounced 500ms after every keystroke  
   - Exposed as `App.gemini_api_key` property — both Collect and Restyle tabs read this, never have their own key field

2. **"DOWNLOADS FOLDER"** section  
   - Path label (monospace, dim)  
   - "Open Folder" button  
   - (Moved here from the guide/settings tab where it lived before)

3. **"ABOUT"** section  
   - App version  
   - "Open Full Visual Guide" button (opens `WORKFLOW_GUIDE.html` in browser)

---

## 10. Checker Tab

### Input area
- BRIGHTSPACE URL field
- MOODLE URL field  
- Three checkboxes: Re-link files, Upload PDFs, Upload H5P
- `Run Check` button (OC_TEAL, full-width, 42px, semibold, run icon left)
- `Phase B — H5P Upload` button (purple `#7C3AED`, right-aligned, labeled clearly)

### Pause point buttons
- `Ready — Scrape Now` button: appears in place (full-width, green) when Moodle pause hits
- `Ready — Download H5P` + `Skip H5P` buttons: appear side by side when H5P pause hits
- All implemented as `show()`/`hide()` on pre-built widgets (no more `pack_forget`)

### After successful run
- `Continue to Unit Collector →` button fades in below log (OC_TEAL, full-width, 38px)
- Downloads path shown as a dim one-liner below the log: `Downloaded to: C:\...\downloads`
- Step 2 badge updates from padlock → empty circle (unlocked)

---

## 11. Unit Collector Tab

### Input area
- UNIT URL field
- TARGET PAGE URL field
- PAGE THEME swatches (same OC theme palette, single `_build_theme_swatches()` helper)
- PARALLEL PAGES spinner (1–10)
- `Collect & Assemble` button (OC_TEAL, full-width)

No Gemini API key field — reads from `App.gemini_api_key`.

### After successful run
- `Continue to Page Changer →` button fades in below log
- Step 3 badge updates from padlock → empty circle

---

## 12. Page Changer Tab

### Input area
- PAGE THEME swatches (shared helper)
- BRIGHTSPACE URL field
- `Start` button (OC_TEAL, full-width)

No Gemini API key field — reads from `App.gemini_api_key`.

### During run
- Batch dialog (`QDialog`) for selecting start page + count — same logic, PySide6 widgets

---

## 13. Step Locking Logic

- **App start**: Steps 2 and 3 locked (padlock icon, 40% opacity)
- **Checker completes successfully**: worker emits `__SUCCESS__` into the queue (in addition to `__DONE__`). `__SUCCESS__` is what triggers the unlock — not heuristic log scanning. The worker already emits `__DONE__` on any exit; `__SUCCESS__` is only emitted on the happy path.
  - Step 2 unlocks: `StepButton` animates opacity 40% → 100% over 300ms
  - Status dot: padlock → empty circle
- **Collector completes** (same `__SUCCESS__` signal): Step 3 unlocks
- Lock state is **session-only** — not persisted to disk

---

## 14. Code Structure

### New files
```
src/gui_icons.py      QPainter icon drawing functions
src/gui_log.py        LogWidget (QTextEdit subclass)
src/gui_sidebar.py    StepButton, Sidebar widget
src/gui_styles.py     QSS stylesheet string + color constants
```

### gui.py (slim orchestrator)
- Creates `QApplication`, `MainWindow`
- Instantiates Sidebar + QStackedWidget
- Connects sidebar button clicks to stack switching
- Owns `gemini_api_key` property (read/write)
- Owns worker thread launch methods (same queue/thread pattern as now)
- Routes queue messages to the correct panel

### Deduplication
| Duplication | Fix |
|---|---|
| Theme swatch block (copy-pasted in 2 tabs) | `_build_theme_swatches(parent, var, frames)` shared helper |
| `_make_log_box` + `_make_log_box_grid` | Replaced by `LogWidget` class |
| `_chk_start_run` + `_chk_start_phase_b` worker (~80% same) | `_chk_run_worker(phase_b=False)` |
| Gemini key in Page Changer + Unit Collector | Single `App.gemini_api_key` property, Settings panel only |

---

## 15. What Does NOT Change

- `src/automator.py`, `src/browser.py`, `src/config.py`, `src/content_checker.py`, `src/unit_collector.py`, `src/style_migrator.py`, `src/ai_styler.py`, `src/chromium_setup.py`, `src/icon_art.py`, `src/update_checker.py`
- `.env` / `api_config.py` / `user_config.json` format
- `run.bat` / `run.sh`
- Queue/worker thread pattern — identical logic, PySide6 uses `QTimer` for polling instead of `after()`
- Log tag semantics
- OC brand theme swatches and `PAGE_THEMES` dict
- All dialog logic (file checklist, pages dialog, update dialog) — rewritten in PySide6 widgets but same UX

---

## 16. Branch Strategy

- Branch: `feature/gui-redesign`
- Existing `main` and `dev` are completely untouched
- Anyone can `git checkout feature/gui-redesign && python gui.py` to preview
- Merge only after sign-off
