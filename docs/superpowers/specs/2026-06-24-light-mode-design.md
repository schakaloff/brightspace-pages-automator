# Light Mode Design

**Date:** 2026-06-24  
**Status:** Approved  

## Summary

Add an instant light/dark theme toggle to the Brightspace Pages Automator GUI. The toggle lives in the Settings panel. The chosen theme is persisted in `user_config.json` and applied on next launch.

---

## Approach

Theme dict + rebuild stylesheet on toggle (Option A).

- Two color dicts (`DARK`, `LIGHT`) live in `gui_styles.py`
- A mutable `current` dict holds the active theme colors
- `set_theme(name)` updates `current` in-place
- `get_stylesheet()` builds the QSS string from `current`
- Sidebar's `paintEvent` reads colors from `gui_styles.current[...]` instead of imported constants

---

## Files Changed

### `src/gui_styles.py`
- Replace flat color constants with two dicts: `DARK` and `LIGHT`
- Add `current = dict(DARK)` as the mutable active theme
- Add `set_theme(name: str)` — updates `current` from the chosen dict
- Replace `APP_STYLESHEET` f-string with `get_stylesheet() -> str` function
- Keep module-level aliases (`BG = current["BG"]` etc.) only where needed for sidebar compat — or just update sidebar imports directly

### `src/gui_sidebar.py`
- Replace the 6 imported color constants used in `paintEvent` with reads from `gui_styles.current[...]`
- No structural changes

### `src/gui_panels.py` — `SettingsPanel`
- Add an "Appearance" section at the top of the settings form
- Two `QPushButton` radio-style buttons: "Dark" and "Light"
- Active button gets `border: 1px solid OC_TEAL`; inactive gets `variant="secondary"` style
- Clicking calls `self.window().set_theme(name)`

### `gui.py` — `MainWindow`
- Add `set_theme(name: str)` method:
  1. `gui_styles.set_theme(name)`
  2. `QApplication.instance().setStyleSheet(gui_styles.get_stylesheet())`
  3. `self._sidebar.update()` — repaint custom-drawn sidebar elements
  4. `self.save_config({"theme": name})`
- In `__init__`, after `_build_ui()`, read `load_config().get("theme", "dark")` and call `set_theme()`

---

## Light Palette

| Token | Dark | Light |
|---|---|---|
| BG | `#0d0d12` | `#f5f6fa` |
| SIDEBAR | `#0a0a0f` | `#ecedf3` |
| PANEL | `#13131b` | `#ffffff` |
| BORDER | `#1c1c2a` | `#d0d3e0` |
| BORDER_ACT | `#2a2a3f` | `#b0b5cc` |
| TEXT_PRI | `#dde0ee` | `#1a1b2e` |
| TEXT_SEC | `#636780` | `#6b6f87` |
| TEXT_FAINT | `#383b50` | `#b0b3c4` |
| OC_TEAL | `#005F63` | `#005F63` |
| OC_TEAL_MID | `#007a80` | `#007a80` |
| OC_TEAL_BG | `#002e30` | `#cce8e9` |
| OC_ORANGE | `#FF8204` | `#FF8204` |
| DONE | `#22c55e` | `#16a34a` |
| RUNNING | `#f59e0b` | `#d97706` |
| LOCKED_BG | `#252535` | `#e8e9f0` |
| ERROR | `#ef4444` | `#dc2626` |
| LOG_INFO | `#b0bcd4` | `#374151` |
| LOG_SUCCESS | `#4caf50` | `#15803d` |
| LOG_ERROR | `#ef5350` | `#b91c1c` |
| LOG_WARNING | `#f0a500` | `#b45309` |
| LOG_STEP | `#4dd0e1` | `#0e7490` |
| LOG_DIM | `#333850` | `#d1d5db` |

---

## Out of Scope

- Automatic OS-follow (system palette detection)
- Per-panel theme overrides
- Any backend (`automator.py`, `browser.py`, etc.) changes
