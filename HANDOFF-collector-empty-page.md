# Handoff — Collector saves an empty page after a failed file insert

> **Resolved 2026-09-25 by design change, not by finding the D2L cause.**
> Reproduced again on ITUS-110 unit 969309: 17 files reported `✓ Inserted`,
> one hit "File chooser trigger not found", and the saved page (read back via
> the API) held none of them — only the text written earlier through the API.
> Insert Stuff *does* upload each file into Manage Files immediately (confirmed
> by HEAD on `/content/enforced/<course>/L1 notes.docx`, modified that minute);
> it is the editor's Save and Close that discards the session.
> `UnitCollector._upload_files` now uses Insert Stuff only as an uploader,
> never saves the editor, HEAD-checks each uploaded file, and writes the links
> through `replace_topic_html` (verified API write). See
> `tests/test_unit_collector_combined_page.py`.

## Problem in one line

`UnitCollector` collects a unit's topics correctly, reports `✓ Saved`, but the Brightspace target page ends up containing only its creation stub (`<p></p>`). All collected content is discarded. Correlates with a failed Insert Stuff step earlier in the same run.

## Where to work

**Only this copy is authoritative:** `C:\dev\brightspace-automator\brightspace-pages-automator` (branch `main`). There are three other worktrees of the same repo on disk with stale code — ignore them. The app is launched with `./dev.bat` from this folder; it has the only working `.venv`.

Run tests: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/ -q`

**Baseline is 26 failed / 305 passed.** Those 26 are pre-existing (this worktree's `.venv` has different package versions than the repo-root one, where the same suite fails only twice). Do not chase them; just don't add more.

## Reproduction

Brightspace sandbox course **10164**, unit **962502** ("Child Development Theories"). Collector tab, auto-create target page on. The unit has 9 topics, 3 of which are files (.pptx x2, .pdf x1). Moodle source course is 174423 (read-only production — do not write to it).

Failure is **intermittent**: same unit, same inputs, roughly half of runs. The variable appears to be which file inserts succeed.

## Evidence

Failing run:

```
Assembling target page...
Inserting 3 file(s)...
  Inserting: Child Development Theories PowerPoint.pptx
  ✗ File chooser trigger not found inside Insert Stuff dialog     <-- file 1 failed
  ⚠ Could not insert ... added link to original file instead.     <-- fallback ran
  Inserting: child development theories summary.pdf
  ↑ Clicked footer Upload button
  ↻ "must select a file" but ... is attached — clicked Upload again (1/3)
  ↩ Overwrite dialog: chose 'Overwrite the existing file' and saved
  ✓ Inserted: child development theories summary.pdf
  Inserting: GraphicOrganizers.pptx
  ... same, succeeded ...
✓ Saved
✗ Brightspace saved an EMPTY page (7 chars, all boilerplate). The 3,796
  characters collected for it were discarded
```

Immediately preceding **successful** run: identical except **no file hit the "File chooser trigger not found" path**, and the page saved correctly (`✓ Text done: 1 pages, 3 links`, verification passed).

Key data points:

| Fact | Meaning |
|---|---|
| Saved page is exactly **7 chars** = `<p></p>` | The creation stub. Page was never written at all — not even partially. |
| Earlier browser-side read showed **224 chars** | Same stub wrapped in D2L's `<html><head>...` boilerplate. Same state, different read path. |
| No `⚠ Failed to append a section` in the log | Every `_source_code_append` for the text sections reported success. |
| No `✗ A Brightspace dialog is still open` | The new pre-save guard did **not** fire, so no modal was detectable when Save was clicked. |
| 2 of 3 files reported `✓ Inserted` | The editor was reachable and functioning after the failure. |

## Ruled out

- **"Editor had not finished loading."** The old code blamed a load race. It is not one: the read-back returns the identical byte count on all 3 retries across 10+ seconds, and the content is exactly the stub.
- **Stuck modal stealing the Save click.** This was the leading theory and it is wrong for this run — `_dialog_still_open()` was checked immediately before the Save click and returned False.
- **Bad selectors matching a dialog's "Save".** Already fixed; `save_and_close` now only matches the full string `"Save and Close"`.
- **Claude / styling.** Failure happens before styling is reached.
- **The API key.** Unrelated 401 earlier in the session, since resolved.

## Code map

| Thing | Location |
|---|---|
| Orchestration: append sections, insert files, save | `src/unit_collector.py` -> `_collect_into()` |
| Appends one HTML chunk via clipboard into the Source Code dialog | `src/unit_collector.py` -> `_source_code_append()` |
| Insert Stuff flow; failing path is the `upload_trigger is None` branch | `src/unit_collector.py:1012` -> `_insert_file()` |
| Best-effort modal dismissal (does not verify) | `src/unit_collector.py` -> `_close_any_dialog()` |
| Shared save + verify, used by Collector **and** Restyle | `src/editor_save.py` |
| Creates the blank target page; the `<p></p>` stub originates here | `src/target_page_creator.py` -> `_JS_CREATE` |

Order inside `_collect_into()`:

1. `_navigate_to_edit(tab, target_url)`
2. `_source_code_append()` per text section
3. wait 3s
4. `_source_code_append("<h2>Files</h2>")`
5. `_insert_file()` per file; **on failure -> `_source_code_append(<link>)` fallback**
6. `save_and_close()` -> `verify_topic_saved()`

## Leading hypotheses

**H1 — the fallback `_source_code_append` after a failed insert commits an empty or wrong editor, wiping the page.** Step 5's fallback opens the Source Code dialog while the editor may still be recovering from the dismissed Insert Stuff modal. `_source_code_append` computes `len_before` from whatever CodeMirror instance it finds; if it finds an empty or unrelated one, `len_before = 0`, the paste of a ~100-char link satisfies the `len_after >= len_before + len * 0.9` check, and `_close_source_dialog()` clicks OK — committing that editor.

*Against H1:* the final page is 7 chars, not ~100. If the fallback had committed its own content, the link should be present.

**H2 — the whole editor session is writing to a different document than the one saved.** Would explain a pristine stub surviving: the appends land somewhere that is never persisted. The correlation with the Insert Stuff failure would be that the dismissed modal leaves the editor bound to a detached iframe.

**H3 — Save and Close silently discards.** D2L may refuse the commit (validation error left over from the half-finished Insert Stuff transaction) while still closing the page. Nothing currently reads D2L's response.

## Do this first

**Log the editor's actual content immediately before Save.** One line resolves H1/H2/H3 in a single run, and nothing currently does it:

```python
# in _collect_into(), immediately before save_and_close(...)
pre_save = await self._read_editor_full_text(tab)
self.log(f"DEBUG pre-save editor holds {len(pre_save)} chars: {pre_save[:200]!r}", "dim")
```

Then:

- ~3,796 chars present -> content was in the editor; the **save** is losing it (H3)
- ~100 chars (just the link) -> the fallback wiped it (H1)
- 0 chars -> wrong/detached editor all along (H2)

Second, make `_close_any_dialog()` verify and return a bool, and have the `_insert_file` fallback refuse to run `_source_code_append` unless the modal is confirmed gone.

## Do not undo (fixed this session, all verified)

1. `src/editor_save.py` — single shared save path for Collector and Restyle. Refuses to click while a modal is open; only matches `"Save and Close"`; reads the topic back through the D2L API to prove content landed. **This is what surfaced the bug — without it the run reports `✓ Saved` and lies.**
2. Hidden-topic routing — topics marked hidden in Brightspace go to a separate `"<Unit> — Combined (Instructor)"` page that is hidden *before* being filled and verified after. `split_by_visibility()` + `tests/test_unit_collector_visibility.py`. Safety rule: an unknown/unreadable topic id is treated as hidden.
3. Overwrite dialog wait 5s -> 120s, and it no longer reports success when it clicked nothing. Large uploads (30 MB) need well over 5s.
4. Overwrite radio is selected by label text as well as `value === '2'` — the old code could silently leave "Create a new file" selected, making duplicates instead of overwriting.
5. Retry of the spurious `"You must select at least one file"` error (Brightspace shows it with the file plainly attached; clicking Upload again clears it).
6. `tests/conftest.py` — autouse fixture that stubs `keyring`. **Critical:** `tests/test_gui_panels.py::test_key_field_text_changed_emits_signal` types into the settings key field, which starts a 500 ms debounce that writes to the real Windows Credential Manager. Running the suite previously destroyed the developer's live Anthropic API key, surfacing much later as an HTTP 401.
7. `_JS` in `_scrape_topics` made a raw string — `FILE_HINT_RE`'s `\b` was being parsed as a backspace character, so file-type detection ran on a wrong regex.

## Known-separate issues (do not fix here)

- Claude rewrites structure it is told to preserve: destroys the `data-bpa-youtube-id` embed wrapper, duplicates videos, nests `<iframe>` inside `<h4>`, drops `<a href>`, returns a full `<!DOCTYPE html>` document instead of a fragment. Planned fix is an output validator comparing pre/post invariants.
- `[fa-comment]`-style Font Awesome shortcodes from Moodle render as literal text on the page.
- The same bare-`has-text("Save")` pattern still exists in `kaltura_categorizer.py`, `page_previewer.py`, `style_migrator.py`.
