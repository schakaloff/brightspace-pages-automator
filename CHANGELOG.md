# Changelog

Add a new `## X.Y.Z` section here whenever you bump `APP_VERSION` in
`src/app_version.py`.
The matching section is pulled into the GitHub Release notes automatically,
and shown to users in the in-app "Update available" dialog.

## 0.8.11
- Collect topics inside nested folders without falsely reporting them as missing.
  Folder descriptions and document links stay grouped in Brightspace order.
  Folder documents already hosted in course files are linked in place.
- Hidden folders keep all their topics on the hidden instructor page, including
  topics inside visible subfolders. Unreadable visibility, descriptions or
  changed folder membership stop collection with a specific diagnostic.

## 0.8.10
- Restyle checks unusually short AI results for preserved text and resource
  destinations instead of rejecting them by HTML size alone. Missing content
  gets one repair attempt from the original page before the result is rejected.
  Review logs now explain unusable results and identify pages with blank titles.
- Create batches of named pages in a chosen section, edit each page's content,
  preview the draft, and clear the local list with one action. Creation tracks
  verified results and stops on uncertain requests instead of retrying them.
- Restyle any selection of pages from a section using a searchable checklist.
  Batch progress reports saved, failed and skipped pages; Stop lets active
  pages finish before stopping the queue.
- Refreshed the Build & style hub, Check & fix screen, bulk page editor, and
  loading screen with modern light/dark layouts and smaller-window scrolling.
  Check & fix separates the read-only scan from explicitly selected fixes.
- Added reviewed removal of selected Content topics without deleting linked
  files or activities.
- Collected pages keep each topic's authored description beside its content,
  including file descriptions, and verify that styling preserves them.
- Fixed narrow styled pages in Brightspace's student view and separated the
  Calm and Classic styling instructions. Installed apps load the selected
  bundled reference and report missing templates before starting a run.
- The Checker now accepts Moodle section links, can stop an active run, reports
  incomplete scrapes before changing Brightspace, and can order existing pages
  to match Moodle.
- H5P uploads reuse a single cloud inventory, avoid duplicate inserts, verify
  saved pages, and place pages in Moodle order.
- The Collector verifies saved content, preserves links and files when an insert
  fails, keeps hidden instructor material separate, and checks that collected
  topics belong to the selected unit.
- Restyle and Collect offer Calm and Classic page designs. Restyle removes old
  generated resource markers and reports accessibility issues for review.
- Added visual checks for page designs and accessibility checks for generated
  HTML. Fixed the high-DPI icon test and bundled the Classic design on Mac.

## 0.8.9
- Rolled back the strict content guardrails added since September 14. Claude
  styling works as it did before them: Claude sees the real page HTML, and
  pages are no longer rejected for added headings, labels, or layout changes.
- Pasting checks are back to the earlier "did the paste land" check. A paste
  that never lands is still not saved.
- Kept: unit description → Overview pages, YouTube players, skipping
  blueprint units, clearer Claude error messages, and switching to Sonnet
  when Opus refuses.
- Moving a unit description to an Overview page still checks that every
  passage of text and every link, file, and video made it onto the new page
  before the description is cleared. Added headings and reordering are fine.

## 0.8.8
- Claude styling now removes labels invented by the model, such as
  "Download" or template headings like "Assessment", before comparing the
  result with the locally saved page. Headings left empty by that removal are
  dropped, and CSS-drawn text (such as `::after` arrows) is removed too.
- If a styled page still fails the content check (for example, files moved
  out of order), Claude is asked once more with the exact problem. A second
  failure leaves the page unstyled, as before.
- Authored text, file links, URLs, embedded media, lists, and tables remain
  protected and must still match before a styled page can be saved.

## 0.8.7
- When a selected non-default Claude model refuses an otherwise valid styling
  request, the app retries exactly once with Claude Sonnet. Existing content is
  still only saved after the full content-integrity checks pass.
- Refusal messages now identify an available safety category and make clear
  that no existing content was changed.

## 0.8.6
- Claude styling failures now identify the exception type even when the
  underlying SDK supplies an empty message.
- A response with no HTML text is reported explicitly and is never saved.
- Includes the protected-restyling, unit Overview, YouTube embedding, and
  automatic blueprint-unit skipping updates.

## 0.8.5
- H5P cloud checks now read the whole library instead of only the first page,
  so activities already uploaded are no longer uploaded a second time.
- H5P activities are matched by their full name rather than the first 25
  characters. Items whose names differ only near the end (for example
  "Question 1" and "Question 23") are no longer mistaken for each other, when
  both checking the cloud and when inserting into a page.
- H5P uploads keep the activity's original name, including punctuation, rather
  than the simplified filename.
- An upload that cannot set its title is abandoned instead of being saved
  untitled, since an untitled activity can never be found again.
- Declining the upload prompt no longer cancels the whole H5P run — activities
  already in the cloud are still inserted.
- Cached .h5p files left over from other courses are ignored and now listed in
  the log.
- Added docs/H5P_MANUAL_STEPS.md describing the same flow by hand.

## 0.8.3
- Reworked the self-update restart helper to use a logged Windows batch helper
  that waits for the app to close, runs the installer with Inno relaunch
  enabled, and starts the app again if the installer did not.

## 0.8.2
- Fixed the post-update restart by closing from the main window after the
  installer handoff and relaunching the app from the detached update helper
  after the installer finishes.

## 0.8.1
- Added a visible progress bar to the self-update flow.
- Fixed silent self-updates by launching the installer only after the app has
  fully closed, so the running executable can be replaced.
- Added an "install latest anyway" recovery path from the update badge for
  users whose install says it is current but still behaves like an older build.
- Published a stable latest-installer download link so reinstalling from the
  website always gets the newest Windows build.

## 0.8.0
- Added full Moodle → Brightspace migration pipeline in the Checker tab:
  scrapes Moodle course structure, compares against Brightspace via D2L API,
  downloads missing files and uploads them to the correct Brightspace modules.
- H5P activities are now migrated automatically: Phase A uploads .h5p files
  to the H5P cloud (skipping any already uploaded), Phase B creates a
  Brightspace page per activity and inserts it from the cloud list.
- File cache matching uses token-based fuzzy logic so renamed files
  (e.g. Chapter_001.pptx → Chapter 1 PowerPoint) are correctly detected.
- Number conflict guard prevents fuzzy matches between items that differ only
  by number (e.g. Chapter 6 vs Chapter 9).
- Each uploaded file is verified via the D2L API before showing a checkmark.
- Migration summary shown at the end: per-phase timing, files transferred,
  H5P embeds, and a prompt to clear the downloads folder.
- Tabs reordered to match the natural workflow: Checker → Page Changer →
  Unit Collector → Style Preview.

## 0.7.0
- Replaced custom colour themes with the 9 official Okanagan College brand
  colours: Lake, Sky, Sunset, Peach, Cherry, Cabernet, Lavender, Lilac, and
  Charcoal. Lake is now the default.
- Updated the page layout to match the OC brand style: generous spacing,
  Noto Serif headings inside cards, block-level links, and a dramatic hover
  lift on cards.
- Added a Gemini API key field directly in the Page Changer tab so the key
  can be changed without editing any files.

## 0.6.0
- Removed the Style Migrator tab from the GUI to streamline the interface.
  The underlying code is preserved in `src/style_migrator.py` and can be
  re-wired at any time — see the "Removed: Style Migrator Tab" section in
  CLAUDE.md for the full re-integration checklist.

## 0.5.0
- Added a first-run Chromium installer with progress UI, so installers can
  ship without bundling the ~300MB browser engine.
- Rebranded the app to "Brightspace Pages Automator" with a new "BP" icon,
  to avoid confusion with a similarly named app.
- Fixed a crash on startup in packaged (frozen) builds caused by
  `sys.stdout` being `None` in windowed mode.
- Added in-app self-update checking: on launch, the app checks for a newer
  GitHub release and offers to download and install it.
