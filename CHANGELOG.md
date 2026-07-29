# Changelog

Add a new `## X.Y.Z` section here whenever you bump `VERSION` in `gui.py`.
The matching section is pulled into the GitHub Release notes automatically,
and shown to users in the in-app "Update available" dialog.

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
