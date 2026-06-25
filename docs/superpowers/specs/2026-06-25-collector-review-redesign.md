# Collector Tab — Review & Grouping Redesign

**Date:** 2026-06-25  
**Branch:** nick  
**Status:** Approved

---

## Problem

The current Collect tab requires significant manual work:

- User must pre-create a blank Brightspace page before running
- All content is dumped into one page regardless of volume — no way to split
- No review step between "collected" and "published"
- Original source topics are left in place; user must delete them manually
- YouTube/H5P links appear as plain `<a>` tags, not embedded video

The goal is to reduce manual steps to only the decisions that require human judgement.

---

## Revised Flow

```
1. User enters unit URL → clicks "Collect & Assemble"
2. Phase 1 — Scrape (existing logic, runs in background)
     · Log shown in panel
     · Topics classified: html / file / link / video
3. PAUSE — Review UI replaces the input form
     · Gemini call: topic labels + types + char_counts → recommended page groupings
     · Gemini suggestion pre-applied to drag UI
     · User adjusts groups, renames pages, approves
4. Phase 2 — Publish (per group, in order)
     · Auto-create Brightspace page via Playwright
     · Write collected content to it (existing write logic)
5. PAUSE — "Open Brightspace and review the result"
     · User checks pages look correct
     · Clicks "Looks good — clean up originals"
6. Phase 3 — Cleanup
     · Delete original scraped topics from the unit
     · Skip: quizzes, assignments, H5P, discussions (stay untouched)
7. Done
```

---

## Review UI

Appears in the same `CollectorPanel` after scraping completes. The input form is hidden.

### Layout

- **Header:** "Review & Group — [Unit Name]"
- **Columns:** one per suggested page group, horizontally scrollable if many
- **Topic cards:** draggable, show:
  - Topic label
  - Type badge: `html` / `file` / `link` / `video`
  - Char count for html topics (dim text)
- **Per-group controls:**
  - Rename (inline edit on group header)
  - Delete group (moves its topics to an "Ungrouped" bin)
  - Add new group button
- **Footer:** "Publish Pages" button (disabled until all topics are in a group)
- **Ungrouped bin:** topics user has removed from all groups — not published, not deleted

---

## Gemini Grouping Call

**Input** (sent to Gemini, ~200 tokens for typical section):
```json
[
  {"label": "Introduction", "type": "html", "chars": 1840},
  {"label": "Week 1 Video", "type": "video", "chars": 0},
  {"label": "Assignment Brief", "type": "file", "chars": 0},
  ...
]
```

**Output** (structured JSON):
```json
{
  "groups": [
    {"name": "Page 1 — Introduction", "indices": [0, 2]},
    {"name": "Page 2 — Videos", "indices": [1]}
  ]
}
```

Gemini decides grouping based on: content type mix, estimated reading length, video count per page. Full HTML is NOT sent — keeps token cost minimal.

---

## Page Auto-Creation

Current flow requires user to pre-create a blank Brightspace page. Replace with Playwright automation:

1. Navigate to the unit's content area
2. Click "New" → "Create a File" (or equivalent D2L action)
3. Set the page title to the group name from the review UI
4. Save → capture the new page URL
5. Use that URL as the write target (existing `_source_code_append` + `_save_and_close`)

Exact D2L selector path needs to be discovered during implementation (shadow DOM traversal as per existing patterns).

---

## Cleanup Phase

After user confirms result in Brightspace:

- Delete each original scraped topic via Playwright (Options → Delete)
- **Skip list** (never deleted):
  - Type: `quiz`, `dropbox`, `discussion`, `survey`, `assignment`, `checklist`, `lti`
  - Type: `h5p` (if detectable)
  - Any topic the user placed in the "Ungrouped" bin during review

---

## Video Embedding (Future)

YouTube/H5P links currently render as plain `<a>` tags in assembled HTML. Desired: embedded player iframe.

- Detect YouTube URLs in link topics → replace with `<iframe>` embed
- H5P: flag for manual attention (embedding requires LTI — out of scope for this pass)
- If a group has >2 video embeds, Gemini recommendation should split them to separate pages

This is a follow-on improvement, not part of this spec's implementation scope.

---

## Out of Scope

- Color/theme UI improvements (separate design review)
- Style Migrator re-integration
- Cross-unit batch collection

---

## Files Affected

| File | Change |
|------|--------|
| `src/panels/collector_panel.py` | Add Review UI (drag/group widget, phase state machine) |
| `src/unit_collector.py` | Add page auto-creation, cleanup phase, split write-per-group |
| `src/ai_styler.py` or new `src/ai_grouper.py` | Gemini grouping call (labels-only, structured output) |
