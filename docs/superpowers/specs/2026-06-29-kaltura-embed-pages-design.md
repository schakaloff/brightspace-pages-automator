# Kaltura Embed → Brightspace Pages

**Date:** 2026-06-29
**Supersedes:** 2026-06-25-kaltura-categorizer-design.md (Phase 1 KMC categorization replaced by this approach)

## Overview

For each Kaltura video found in a Moodle course, automatically create a new Brightspace page containing the Kaltura embed iframe. The page is created in the Brightspace module that the user maps to the Moodle section where the video lives.

## Workflow (Three Phases)

### Phase 1 — Scan Moodle
Same as before, extended: extract entry ID, title, Moodle URL, **and section name** for each `kalvidres` activity. Uses `li.section` DOM pattern (same as `content_checker.py`).

### Phase 2 — Map Sections
After scan, a "Map Sections" area appears in the GUI:
- "Fetch BS Modules" button hits D2L TOC API to get `[{id, title}]` for all modules in the Brightspace course.
- One row per unique Moodle section name, each with a QComboBox of BS module names.
- "Create Pages" button stays disabled until every Moodle section has a selected BS module.

### Phase 3 — Create Pages
For each selected entry:
1. **KMC browser**: search entry_id → click row to open entry detail → click "Share & Embed" link (`.kPreviewAndEmbedContainer a`) → read embed code from `textarea` via `input_value()`.
2. **Brightspace browser**: navigate to course content → find mapped module → click `button[aria-label="Create New"]` (shadow DOM, deepFind) → click Page tile (`div.add-material-tile-inner` containing "Page") → TinyMCE editor opens → click Source Code (existing `automator.py` flow) → paste embed HTML → click OK → enter title (entry name) → Save and Close.

## Backend (`src/kaltura_categorizer.py`)

### `scan_moodle_course` — extend result dict
```python
# Before: {entry_id, name, moodle_url}
# After:  {entry_id, name, moodle_url, section_name}
```
JS change: wrap `kalvidres` link collection inside section loop, capture heading text.

### New: `get_bs_modules(bs_url: str) -> list[dict]`
- Async. Launches headless BS browser (reuses existing BS session file).
- Calls D2L TOC API: `GET /d2l/api/le/1.0/{courseId}/content/toc` via `page.evaluate`.
- Returns `[{"id": "...", "title": "..."}]`.
- Course ID extracted from URL with existing `_extract_course_id` pattern.

### Replace `categorize_entries` → `embed_entries`
```python
async def embed_entries(
    self,
    entries: list[dict],          # {entry_id, name, section_name, ...}
    section_map: dict[str, str],  # {moodle_section_name: bs_module_id}
    bs_url: str,
    log_fn,
) -> None
```

**KMC sub-flow (per entry):**
1. `page.goto(KMC_URL)` → search field → type entry_id → Enter → wait
2. Click first result row (not checkbox) to open entry detail
3. Click `.kPreviewAndEmbedContainer a` ("Share & Embed")
4. `embed_code = await page.locator('textarea').first.input_value()`

**Brightspace sub-flow (per entry):**
1. Navigate to `/d2l/le/content/{courseId}/home`
2. deepFind `button[aria-label="Create New"]` in shadow DOM → click
3. deepFind `div.add-material-tile-inner:has-text("Page")` → click
4. Wait for TinyMCE editor to load
5. Existing Source Code editor flow (deepFind toolbar button → shadow click → paste HTML via CM6 dispatch)
6. After pasting: fill title input with `entry["name"]`
7. Click Save and Close

## GUI (`src/panels/kaltura_panel.py`)

### Input changes
- "Brightspace Course ID" input → **"Brightspace Course URL"** (full URL needed for navigation and module fetch)

### New: Section mapping area (hidden until scan succeeds)
- QFrame revealed after `__SCAN_DONE__` sentinel
- Contains:
  - "Fetch BS Modules" button → worker thread → populates dropdowns
  - QGridLayout: left column = Moodle section label, right column = QComboBox
  - Sentinels: `__MODULES_DONE__:{json}` → populate combos, `__MODULES_FAIL__` → log error
- "Create Pages" button (was "Categorize Selected") enabled only when all combos have a selection

### Updated sentinel handling
| Sentinel | Action |
|----------|--------|
| `__SCAN_DONE__:{json}` | populate checklist, reveal mapping area |
| `__SCAN_FAIL__` | clear list, hide mapping area, disable create |
| `__MODULES_DONE__:{json}` | populate section→module combos |
| `__MODULES_FAIL__` | log error |
| `__CAT_DONE__` | re-enable buttons |

## Selectors Reference

| Element | Selector / Method |
|---------|-------------------|
| KMC search field | `input[type='text']` first |
| KMC entry row | `tr.kEntry` or `p-table tbody tr` first |
| Share & Embed link | `.kPreviewAndEmbedContainer a` |
| Embed textarea | `textarea` first |
| BS Create New button | `button[aria-label="Create New"]` (shadow DOM deepFind) |
| BS Page tile | `div.add-material-tile-inner` containing SVG `#htmldoc` or text "Page" |
| BS Source Code button | existing automator.py flow (`d2l-htmleditor-button[cmd="d2l-source-code"]`) |

## Error Handling
- KMC entry not found → log warning, skip entry
- BS module not reachable → log error, skip entry
- Embed code empty → log warning, skip entry
- KMC and BS each get their own browser; KMC session saved to `kmc_session.json` at end

## Out of Scope
- Deduplication (don't check if page already exists)
- Kaltura REST API approach (superseded by KMC Share & Embed)
- Setting video title or description in Kaltura
