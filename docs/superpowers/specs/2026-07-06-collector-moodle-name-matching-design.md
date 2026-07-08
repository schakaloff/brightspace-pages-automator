# Unit Collector — Moodle Name Matching for Files/Links

## Problem

When Unit Collector inserts a downloaded file via Insert Stuff, the D2L dialog's
link-text field (`#z_k`) is left blank, so Brightspace defaults the visible link
text to the raw file path, e.g.:

```
/content/enforced/10268-EA-121-S01-70430.202531_Migrate/Communicating PowerPoint.pptx
```

Link topics have the same problem — the label shown on the assembled page comes
from whatever Brightspace's `d2l-list-item-nav` label attribute holds, which can
be mangled by the mbz import.

The fix: cross-reference the original Moodle course to recover the correct
human-readable name, and use that name both to fill `#z_k` on file insert and
as the label text for link items.

## Scope

- Applies to **file inserts** and **link items** only. HTML topic pages keep
  their existing label (real page titles, not affected by this bug).
- Matching is against **all items in the Moodle course**, not scoped to a
  specific section — we don't reliably know which Moodle section maps to a
  given Brightspace unit.
- Moodle URL is **optional**. If not provided, behavior is unchanged (use
  existing Brightspace-derived label).

## Design

### 1. Moodle login (reuse `kaltura_categorizer.py` pattern exactly)

- New method on `UnitCollector` (or a small standalone helper reused from
  `kaltura_categorizer.login_to_moodle`): opens a visible browser at
  `https://mymoodle.okanagan.bc.ca/login/index.php?saml=off`.
  - If `moodle_username`/`moodle_password` provided (from Settings, already
    exposed via `MainWindow.moodle_username`/`moodle_password`): auto-fill and
    submit.
  - Otherwise: poll `page.url` for up to 6 minutes waiting for manual login.
- On success, save `context.storage_state()` to the existing
  `MOODLE_SESSION_FILE` (`USERDATA_DIR / "moodle_session.json"`) — shared with
  the Kaltura categorizer, so a session logged in from either tab works for
  both.
- If `MOODLE_SESSION_FILE` already exists, skip login and reuse it directly
  when scraping; only fall back to interactive login if the scrape lands
  outside `mymoodle.okanagan.bc.ca` (session expired).

### 2. Moodle scrape

- Launch a **headless** context with `storage_state=MOODLE_SESSION_FILE`.
- Navigate to the given Moodle course URL.
- Reuse `content_checker._JS_MOODLE_ITEMS` verbatim (already returns
  `[{type, name, href, hint}, ...]` across all sections) to get the flat item
  list. Collect `.name` for every non-SECTION entry into `moodle_names: list[str]`.

### 3. Matching

- Reuse `content_checker._norm()` (lowercase + HTML-unescape) for comparison.
- For each Brightspace file/link label:
  - Exact normalized match → use it.
  - Else `difflib.get_close_matches(norm_label, moodle_names_normalized, n=1, cutoff=0.6)`.
  - Above cutoff → use matched Moodle name (original casing) as the corrected
    label. Below cutoff → keep the original Brightspace label unchanged.

### 4. Applying the corrected name

- **File insert** (`UnitCollector._insert_file`): after the upload completes
  (existing "Upload" click / overwrite-dialog handling) and before the final
  "Insert" button click, locate the link-text input (`input#z_k` inside the
  Insert Stuff dialog frame) and fill it with the corrected name (extension
  stripped) via Playwright `fill()`.
- **Link item**: in `UnitCollector.run()`, when building the
  `<p><strong>{label}:</strong> <a href="...">...</a></p>` line, use the
  corrected name in place of `topic["label"]`.

### 5. GUI

- `CollectorPanel`: add an optional "MOODLE COURSE URL" `QLineEdit` between
  the Target Page URL field and the Parallel Pages row.
- `_start_run()`: pass `moodle_url=self._moodle_entry.text().strip()` through
  to `collector_run(...)`.
- `UnitCollector.__init__` / `run()` (module-level `run()` too): add optional
  `moodle_url: str = ""` param. Reuses existing `moodle_username`/
  `moodle_password` already threaded through `MainWindow` (no new Settings
  fields needed — Checker tab already has these).
- If `moodle_url` is empty, skip all of the above — zero behavior change.

## Error handling

- Moodle login/scrape failures are non-fatal to the overall collector run:
  log a warning and fall back to unmatched (original) labels for every
  file/link item, then continue the run as normal.
- Individual match misses (below cutoff) just keep the original label —
  not an error, expected for genuinely new/renamed content.

## Out of scope

- No changes to HTML topic page `<h2>` headers.
- No section-scoped matching.
- No new Settings UI (moodle creds already exist).
