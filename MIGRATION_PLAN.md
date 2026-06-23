# Moodle → Brightspace Migration Plan
**Date:** 2026-06-15  
**Status:** Stage 1 complete — tested on EA-112

---

## Overview
Automate the transfer of Moodle course content (files, videos, H5P, external tools) to Brightspace, and re-link everything appropriately inside the assembled course pages Filip is building with the Unit Collector.

---

## Content Buckets

| Bucket | Type | How detected | Transfer method |
|---|---|---|---|
| **Files** | PDFs, DOCX, PPTX, M4A, etc. | `modtype_resource` (top-level) OR `pluginfile.php` links/audio fallback anchors inside label bodies | Download from Moodle → upload to Brightspace file store → re-link |
| **Kaltura Videos** | Lecture recordings | `modtype_kalturamedia` OR `<iframe src="*kaltura*">` in label bodies | Extract entryId → log for manual re-linking in Kaltura/Brightspace |
| **H5P** | Interactive activities | `modtype_hvp` / `modtype_h5pactivity` | Download `.h5p` file → manual upload by educator in Brightspace H5P tool |
| **External Tools** | Cengage, Zoom, Pearson, etc. | `modtype_lti` | Flag only — must be re-linked manually by instructor |
| **External URLs** | Links to outside websites | Any `href` without `pluginfile.php` | Not captured — instructor re-pastes URL into Brightspace manually |
| **Embedded videos** (no entryId) | Unknown video source | `(embedded video)` with no Kaltura entryId | Flag for manual review — source unknown |

---

## Stage 1 — Detect ALL Moodle file & video links ✅ DONE

### What was built
Two scan passes run automatically after the Moodle item list is scraped:

**Pass 1 — Label inline scan** (`_scan_moodle_labels_inline`)
- Runs on the course page without navigating away
- Scans the HTML body of every `modtype_label` activity for `pluginfile.php` links
- Also catches audio/video files via the hidden `<a class="mediafallbacklink">` inside Moodle's video.js player
- Skips ReadSpeaker proxy links (readspeaker/docreader URLs that wrap the real file)
- Attributes each found file to its section and label name

**Pass 2 — Page body scan** (`_scan_moodle_page_bodies`)
- Navigates to each `modtype_page` (PAGE-type) topic
- Scans for `pluginfile.php` links and Kaltura iframes/players
- Logs every page visited with what was found (● hit / ○ empty)

### What IS captured
- Moodle-hosted files: PDFs, DOCX, PPTX, audio (M4A), etc. via `pluginfile.php`
- Kaltura videos embedded in labels (via iframe or KMC player)
- Standalone FILE resources (`modtype_resource`) — shown as top-level items, not embedded

### What is NOT captured (by design)
- External website links (webmd.com, hopkinsmedicine.org, etc.) — instructor re-pastes these
- These will never have `pluginfile.php` in their URL

### Display format
Embedded items appear under their section in the log, indented with `(in: label name)`:
```
── Understanding Child Development
   🏷  LABEL    Independent Study [🎥]
   📄 FILE     Effective Note taking (in: Effective Note taking)
   📄 FILE     Understanding Child Development (in: Effective Note taking)
   📄 FILE     Milestone Moments (in: Effective Note taking)
   📄 FILE     Understanding Child Development (in: Effective Note taking)
   📄 FILE     Audio File (in: Effective Note taking)
   📄 FILE     PDF File (in: Effective Note taking)
```
Note: label names come from the first link inside the label body, not the section heading — this is a known cosmetic issue, does not affect detection.

### Test results (EA-112, 2026-06-15)
- 56 embedded files detected across 27 sections
- 0 embedded videos (no Kaltura in this course)
- 0 page body files (no PAGE-type activities in EA-112 — all content is in labels)

---

## Stage 2 — Download files from Moodle
- Use authenticated Playwright session (already logged in during content check)
- Parallel batch downloads (56+ files expected for EA-112)
- Skip Kaltura and external tools — flag only
- H5P files downloaded separately to their own folder

## Stage 3 — Upload to Brightspace
- Use D2L file management API (`/d2l/api/lp/.../managefiles/`)
- Check whether bulk upload is supported or needs one-at-a-time
- Each upload returns a Brightspace file URL
- Build map: `moodle_url → brightspace_url`

## H5P → Brightspace placement

Each H5P activity gets its own page created in Brightspace (not embedded inline).

**Matched sections (auto):**
- Use the Moodle section → Brightspace module map already built by the Content Checker comparison
- Create a Brightspace page in the matched module, upload the .h5p file

**Unmatched sections (manual fallback):**
- If fuzzy match fails for a section, do NOT skip — flag it
- At end of run: show a popup listing all unmatched H5P items + which section they came from
- Also write to a persistent log file: `logs/YYYY-MM-DD_<course-name>_unmatched.txt`
- Log persists until the next run for that course (next run overwrites it)
- This way if user closes the app the info isn't lost

---

## Stage 4 — Re-linking report
- Use Moodle section → Brightspace module mapping from Content Checker comparison
- For each file: show which Brightspace module it belongs in + new URL
- For Kaltura: show entryId + which section it was in
- For H5P: list separately with "needs manual upload by educator"
- For external tools: already handled by existing external tools report

---

## Hybrid approach (backup plan for HTML generation)

Instead of sending full course pages to Gemini, use a two-layer approach:

**Layer 1 — Rules (free, instant):** Handle everything at the Moodle structure level
- Sections, standalone FILE resources, quizzes, external tools, forums
- These are always the same across all courses (`modtype_*` classes never change)

**Layer 2 — Gemini (cheap fallback):** Handle only the label body HTML
- Send one small label chunk at a time, not the whole page
- Provide one example label from the same course as style reference
- Gemini matches that style for the rest of the labels in that course
- Massively fewer tokens vs current approach; less likely to 503

**Why not fine-tune our own model:**
- Needs 50-100 example pairs, GPU hardware, hours of training
- Overkill — the problem is a template problem, not an intelligence problem
- Rule-based + prompt is faster, cheaper, and just as consistent

---

## Open questions
- Does D2L file API allow bulk upload or one-at-a-time?
- Are Brightspace file URLs stable enough to hardcode in HTML?
- How does Filip's Unit Collector structure pages — does he keep section headings as collapsible blocks? (Determines whether we can auto-insert file links or just report them)
- For Kaltura: does the college's Brightspace instance have Kaltura integrated? If yes, entryId is enough to re-embed. If no, videos need re-uploading.

---

## Filip's Unit Collector (context)
Filip is building a tab that:
- Takes a Brightspace unit URL + a target empty page
- Scrapes all topic pages from that unit
- Assembles them into one combined collapsible HTML page
- Writes it back to Brightspace via the source code editor

**Integration point:** File link replacement slots in after HTML assembly but before write-back. The `moodle_url → brightspace_url` map gets applied to the assembled HTML before it's posted.

---

## Session log

### 2026-06-15
- Content Checker: external tool detection (Kaltura, H5P, LTI) — done
- Moodle auto-login flow (Manual Login → Microsoft SSO or credentials) — done
- Migration plan written — done

### 2026-06-15 (continued)
- Stage 1 built and tested on EA-112 — done
- Two-pass scan: label inline + page body navigation
- 56 embedded files found across 27 sections
- Confirmed: external URLs are intentionally not captured
- **Next:** Stage 2 — download files from Moodle using authenticated session

### 2026-06-16
- Style Preview tab fully implemented and pushed to origin/nick
- **Next:** Stage 2 — download files from Moodle using authenticated session

### 2026-06-17 — H5P download fully working + re-link architecture decided

**H5P download (all working):**
- Auto role-switch: clicks user menu → Switch role to... → Instructor before downloads
- Checkbox fix: JS-based check (no collapsible expansion needed), covers id_export /
  id_enabledownload / id_displayopt_export across both mod/hvp and mod/h5pactivity
- Fresh page per H5P item (context isolation, no browser crash cascade)
- Pause point: "Ready — Download H5P" button appears after scrape so user can verify
  browser state before downloads start
- All 13 H5P files downloaded successfully on real course test

**Re-link architecture decided:**
- Brightspace already has course imported from Moodle backup, but embedded file links
  (PDFs, DOCX, etc.) often still point to mymoodle.okanagan.bc.ca — especially
  files that were embedded as links inside label HTML (not standalone FILE activities)
- Standalone FILE activities sometimes transfer correctly via the import tool
- **Approach:**
  1. Scan each Brightspace topic HTML for mymoodle.okanagan.bc.ca hrefs (already built)
  2. For each Moodle URL found: check if a file with the same name already exists in
     Brightspace file store → if yes, notify user and let them verify → if no, download
     from Moodle and bulk-upload to the matching Brightspace section
  3. Replace all Moodle URLs in topic HTML with new Brightspace URLs
- `_relink_moodle_files` method is built but not yet tested against a real course
- `moodle_links` scan already collects {topic, topic_id, text, href} per Moodle link found
- D2L manage-files upload endpoint used: POST /d2l/api/lp/1.0/{courseId}/managefiles/file/
- Topic HTML update: GET + string-replace + PUT /d2l/api/le/1.0/{courseId}/content/topics/{id}/file

**H5P Brightspace upload (next after re-link):**
- Each H5P gets its own new Page in the matched Brightspace section
- Step 8: Create New button in section → d2l-button.create-new-btn (shadow DOM)
- Step 9: Click Page option → TBD (need HTML from user walkthrough)
- Unmatched sections → popup + log file at end of run
- OC Brightspace H5P type unknown (built-in D2L vs LTI) — need user to show upload flow

**Unit Collector (Filip) runs LAST:**
- After all files are re-linked and H5P pages are created
- Assembles topics into one combined collapsible page per section
- File link replacement slots in after HTML assembly

**Pending decisions:**
- Does OC Brightspace have H5P as built-in tool or LTI? (affects upload automation)
- Log file (logs/YYYY-MM-DD.txt) — build after re-link is confirmed working
