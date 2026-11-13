# Approved Kaltura disclosure controls

Implemented the approved icon preview on 2 November 2026.

- Video groups use existing Tabler right/down chevrons in compact neutral controls, with hover, press and keyboard-focus feedback.
- Destination fields use a matching down chevron without the separate boxed arrow.
- Expanded/collapsed accessible names and tooltips describe the action and source section.
- Existing results refresh their icons and dropdown assets when the app theme changes, without changing expansion, selection or mapping state.
- The dropdown PNGs are rendered from the bundled Tabler source using shared theme colors and included in the existing packaged assets directory. No runtime asset writes or new dependencies are needed.

Production changes: `src/panels/kaltura_panel.py`, the Kaltura refresh call in `gui.py`, and two themed PNG assets. Scan, login, mapping and page-creation logic are unchanged.

`tools/preview_kaltura_controls.py` now renders the actual production controls rather than applying preview-only styles. Its output goes into `implemented/`; the approved comparison images are preserved in this directory.

Verification: mouse and Space-key expansion/collapse, native dropdown opening, live theme refresh while expanded, accessible action names, unchanged selections/mappings, and visual inspection at 860 × 680 and 560 × 620. All 19 tests in `tests/test_gui_panels.py` passed. `git diff --check` passed. No live course was contacted.
