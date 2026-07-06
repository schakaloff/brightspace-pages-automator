# Kaltura Categorizer — Design Spec
**Date:** 2026-06-25  
**Branch:** kaltura  
**Scope:** Phase 1 — KMC categorization only (Brightspace embedding is a future phase)

---

## Problem

Moodle → Brightspace import drops all Kaltura videos. Each video must be manually found in the Kaltura Management Console (KMC) and assigned the Brightspace course ID as a category before it appears in Brightspace. This tool automates that process.

---

## User Workflow

1. Open **Kaltura tab** in the app
2. Enter Moodle course URL + Brightspace course ID
3. Click **Scan Moodle** → app scrapes all Kaltura video activities
4. Review checklist of found videos, uncheck any to skip
5. Click **Categorize Selected** → app logs into KMC and categorizes each video
6. Review log output

---

## Architecture

### New files
- `src/kaltura_categorizer.py` — Playwright backend logic
- `src/panels/kaltura_panel.py` — PySide6 GUI panel

### Modified files
- `gui.py` — add KalturaPanel to QStackedWidget
- `src/gui_sidebar.py` — add Kaltura sidebar button + icon
- `src/gui_icons.py` — add Kaltura icon (video camera or similar)

---

## Backend: `src/kaltura_categorizer.py`

### Class: `KalturaCategorizer`

**Session storage:**  
`kmc_session.json` saved in `USERDATA_DIR` (same pattern as `session.json` for Moodle/Brightspace).

**Method: `scan_moodle_course(moodle_course_url) -> list[dict]`**

1. Launch Playwright with saved `session.json` (Moodle session)
2. Navigate to Moodle course page
3. Find all links to `mod/kalvidres/view.php`
4. For each link, navigate to it and extract:
   - `entry_id`: parse `entryid%2F([\w_]+)` from iframe src
   - `name`: page `<title>` tag (strip ` | OCmoodle` suffix)
   - `moodle_url`: the kalvidres URL
5. Return list of `{entry_id, name, moodle_url}`

**Method: `categorize_entries(entries, brightspace_course_id, log_fn)`**

1. Launch Playwright; load `kmc_session.json` if exists, else navigate to KMC login URL and wait for user to log in (same pattern as Moodle SSO login in `browser.py`), then save session
2. For each entry:
   a. Navigate to `https://kmc.cap2.ovp.kaltura.com/index.php/kmcng/content/entries/list`
   b. Search for entry ID in search box, wait for results
   c. Click `p-checkbox` to select the row
   d. Open Actions dropdown → click "Add To New Category / Playlist" → "Add To New Category"
   e. In category search input, type `brightspace_course_id`, wait for match, select it
   f. Confirm/submit
   g. Call `log_fn(f"✓ {entry['name']}")` on success, `log_fn(f"✗ {entry['name']}: {err}")` on failure
3. Save updated KMC session

---

## GUI Panel: `src/panels/kaltura_panel.py`

### Layout (top → bottom)

```
[Moodle Course URL: ___________________________]
[Brightspace Course ID: _______]   [Scan Moodle]

── Found Videos ──────────────────────────────
[✓] 0_e35b5e8b — 115 Nov 20 Instruments
[✓] 0_abc12345 — 115 Nov 24 Chapter 1 Lesson 2
...
[Select All]  [Deselect All]

[Categorize Selected]

── Log ───────────────────────────────────────
✓ 115 Nov 20 Instruments
✗ 115 Nov 24 Chapter 1 Lesson 2: timeout
```

### State machine
- **Idle**: only top inputs + Scan button enabled
- **Scanning**: spinner, inputs disabled
- **Ready**: checklist visible, Categorize button enabled
- **Running**: Categorize button disabled, log streaming
- **Done**: all buttons re-enabled

### Worker thread pattern
Same `QThread` + queue pattern as `CollectorPanel` and `RestylePanel`. `KalturaCategorizer` methods run in worker thread; results/logs emitted via signals to GUI.

---

## Session Handling

KMC uses SSO (same Microsoft SSO as Moodle). On first run, app opens visible browser, waits for user to log in, saves `kmc_session.json`. Subsequent runs load saved session; if expired, repeats login flow.

---

## Data Flow

```
Moodle course URL
       │
       ▼
scan_moodle_course()
       │  session.json (Moodle)
       ▼
[{entry_id, name, moodle_url}, ...]
       │
       │  user reviews + checks
       ▼
categorize_entries()
       │  kmc_session.json
       ▼
KMC: search → select → Actions → Add To New Category → course ID
       │
       ▼
log output
```

---

## Out of Scope (Phase 2)

- Embedding categorized videos into specific Brightspace topic pages
- Matching Moodle activity names to Brightspace topic names
