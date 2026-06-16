# Changelog

Add a new `## X.Y.Z` section here whenever you bump `VERSION` in `gui.py`.
The matching section is pulled into the GitHub Release notes automatically,
and shown to users in the in-app "Update available" dialog.

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
