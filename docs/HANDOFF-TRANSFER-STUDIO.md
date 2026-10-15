# Handoff: Structured Transfer → Transfer Studio (web UI)

Written 2026-10-13 for the next agent. Read this first, then `docs/STRUCTURED_TRANSFER.md`
(full behaviour, safety rules and what was verified live).

**Local journal save interruption, 2026-10-14:** run
`f668f1372a4346f09a8ab0356bd000ce` stopped on Windows error 5 replacing its
local JSON journal. The destination file was writable; a transient reader or
scanner lock is the likely cause. Atomic replacement now retries Windows errors
5/32/33 with bounded backoff, preserving the old journal and complete temporary
file if retries fail. 87 save/Studio/runner checks passed.
The copy stage was journaled as verified. Read-only checks confirmed hidden
modules/folders 998732–998736 (Sexuality and Exceptionalities and its three
sections, then Anxiety in the Workplace). No transferred page operations or
final validation were recorded. The newer temporary journal contained response
998736 and the incomplete report; both versions were backed up under
`transfer-investigation/journal-recovery`, and the newer journal was restored
under the plan lock. Its fifth module remains pending for the engine's own
readback. No live Resume was run. Restart the source app and resume this same
saved transfer with a fresh destination confirmation.

**Skipped-assignment link correction, 2026-10-14:** preparation stopped because
two retained page parts linked to skipped EA113 assignments. Review now identifies
the page and assignment and offers explicit reuse of a same-named destination
assignment. The saved choice rewrites links using the destination's ActivityId;
it leaves the existing assignment, settings and rubric untouched. This choice is
undoable and checked against fresh destination metadata before writes. Other
unmapped course links remain blocked, now with the page name in a saved report.
Nick approved EA105 assignment 51802 for Sexuality and Exceptionalities (source
51597), and 51804 for Mental Health and Self Care (source 51595). Read-only checks
confirmed both QuickLinks redirect to the correct EA105 assignment IDs. No live
Create was run for this correction. Reopen the saved draft after restarting the
desktop app to load the saved choices.
The choices are saved in unstarted draft `f668f1372a4346f09a8ab0356bd000ce`;
its journal remains empty and validation reports no blockers. The original is
backed up under `transfer-investigation/activity-link-fix`. Verification:
140 related tests passed, including desktop/mobile Review and Undo checks;
the offline browser/accessibility QA passed. The packaged executable has not
been rebuilt for this correction; use the source launcher.

**Codex update, 2026-10-14:** discussion and survey copying/linking is now verified
live in the two sandboxes. Surveys have a supported LE 1.82 catalogue API on this
tenant; the old "no survey API" assumption below is superseded. Copies are polled
and mapped by tool ID, title collisions block before any writes, and a survey-only
pending copy cannot be assumed complete. Pre-catalogue saved plans remain resumable.

The native picker now opens the discussion's forum, reaches Surveys through More,
and scopes forum/activity selection to the QuickLink chooser. This prevents a
matching course-sidebar title from intercepting the selection. A clicked activity
whose save cannot be verified stays pending. New Content references are explicitly
hidden and journaled; later instructor visibility edits are left alone.

Live artifacts: `transfer-investigation/activity-live/0780b4d0c269446fbe57a27d89ce6201.json`
and its report. Hidden module **Transfer Studio activities d416eadf**, ID **996900**,
contains discussion link **996906** → discussion **21202**, and survey link **996910**
→ survey **77467**. All 12 readback checks passed. The final production discussion
adapter also passed in hidden test submodule **996915**, link **997064**, using the
same discussion copy. One Copy Components job created the two activities; the
source was unchanged. The verification content remains in sandbox 25707.

Copied tools keep their availability settings. In particular, an active survey
stays active in Surveys even though its Content reference is hidden; Review and
the report now state this distinction. Release conditions, LTI/unsupported tools,
and additional course components still need manual Brightspace review/work.

Final verification on 2026-10-14: **198 related tests passed** (177 transfer
tests plus 21 structured-transfer tests), browser/accessibility QA passed, and
the rebuilt Windows package passed its isolated Studio startup smoke check.
Nick asked to keep scope tight; this pass ends with the activity/visibility fixes
and verification. No additional feature expansion is planned here. Restart the
desktop app to load the updated backend. All changes remain uncommitted.

**Desktop integration correction, 2026-10-14:** Nick reproduced "The URL needs a
Brightspace course ID" with valid `/d2l/home/25708` and `/d2l/home/25707` links
when running `dev.bat`. Studio accepted these, but `with_transfer_browser` passed
them into `bulk_page_creator.course_id_from_url`, which did not accept home paths.
The shared parser now accepts home links. The new regression keeps the real Qt
worker and `with_transfer_browser`/`_with_browser` chain and mocks browser IO only;
it reproduced the error before the fix and covers both Scan and Create. Earlier
live tools and worker mocks bypassed that parser, so their passes did not cover
this path. Reopen Plan transfer after the dev launcher restarts the app.

**Codex update, 2026-10-13:** the core browser workflow is implemented end to end:
Connect → Arrange → Styling → Review → Create. Arrange has modern selection/context
controls, Undo/Redo, saved section layouts, generated-page authoring, suggestions,
warning fixes and ordering. Styling has real isolated Before/After previews and
page overrides. Review gates the existing runner with destination confirmation;
Create shows live logs, Stop/Resume, readback results and exportable reports.
Saved transfers reopen in the browser; Qt remains a fallback. URL errors now stay
on Connect and identify the invalid field. Credentials remain in the app worker.

Verification: 868 offline tests pass (the same two known link-review failures),
all 165 transfer-specific tests pass, and desktop/mobile browser/accessibility
checks pass. Stop/Resume used one component job and no duplicate writes in the
offline engine. A live sandbox check created one hidden module/page in 25707:
**Transfer Studio verification 40adf235**, IDs 996360/996361; all seven readback
checks passed and the source was unchanged. A normal Windows PyInstaller package
passed its offline Studio smoke test after fixing an ICU dependency collision.
The final regression check also protects browser edits when Studio switches to
a different draft while the in-app view still holds the previous one.

Remaining engine limits are explicit in Review: LTI/unsupported content and the
additional course components use manual Brightspace tools, and release conditions
require manual review. Live coverage includes one ungraded discussion and one
survey; graded discussions and broader course combinations need wider coverage.
macOS packaging was not tested here. See `STRUCTURED_TRANSFER.md` for
the current workflow, verification commands and artifact locations. Existing
user work remains uncommitted.

## What Nick wants

**Two-sided whole-unit Skip, 2026-10-14:** a source-folder Skip now removes linked
draft modules and their automatic generated content in the same Undo transaction.
Matching uses `sources`/mirrored `source_module` IDs, not titles. Individual-item
skips keep their modules. Custom additions/renames/text/source mappings and
foreign-unit items require a revision-bound confirmation; Cancel changes nothing,
and confirmed removal returns foreign content to unplaced. All edits remain local
to the draft, including when a linked module represents existing Brightspace
content. `custom`/`auto_generated` markers protect additions, and old patterned
drafts recognise unedited generated pages from their pattern. Explicit unit skips
are stored in `skipped_units` (part of Undo history), allowing empty-unit badges;
including/placing source content clears the corresponding unit markers. The UI
distinguishes Skipped from All items skipped. Old saved skips are not migrated
destructively: reapply a whole-unit Skip to remove previously retained scaffolding.
All 109 focused tests and offline browser/accessibility QA passed, including
desktop/mobile confirmation, Cancel, Undo, source identity, foreign content and
existing-destination protection. No live course writes were performed.

**Between-unit drag correction, 2026-10-14:** the old browser drop handlers only
accepted folder centres or appended at the root. Destination gaps and row edges
now provide visible insertion lines. `place` supports a validated `before` node
anchor; sequential moves recompute its index to preserve group order in either
direction. Source units can also insert there. Revision/Undo/cycle/existing-unit
guards remain. All 77 Studio/outline tests and the offline browser/accessibility
QA passed, including native gap/edge drops, nesting, group reorder and Undo.

**Skipped-unit indication, 2026-10-14:** source folders aggregate their item skip
state, including nested folders and split-page rows. A fully skipped unit shows
a Skipped badge and a muted, crossed-out title while collapsed; partly skipped
units show a skipped/total count without crossing out the whole unit. Individual
skipped items use the same marking. Undo/Redo and Include again recalculate it.
Empty units are not labelled skipped just because they contain no items.
All 42 Studio tests passed, along with offline browser/accessibility QA covering
collapsed/nested badges, partial skips and keyboard Undo. No course writes.

**Review clarity correction, 2026-10-14:** Nick's screenshots showed repeated
informational notes burying the destination checkbox and obscuring why Create
was disabled. Review now separates blockers from collapsed notes, puts the
confirmation near the top, and keeps the disabled reason beside Create in a
sticky footer. Unplaced blockers have a Find action in Arrange. Generated-page
notes identify their folder path and say placeholder pages start hidden. No
blocking checks were relaxed. 72 Studio/outline tests and two desktop/mobile
browser regressions passed; the existing offline workflow/accessibility QA also
passed. No live course writes occurred.

**Missing-file scan correction, 2026-10-14:** Nick's re-imported EA105 backup
(`D2LExport_25707_Nick Usatenco EA105 Sandbox_202610953.zip`) has 47 HTML
resource references in its manifest but zero `.html`/`.htm` files in the archive.
This can restore the outline without restoring the page bodies. Studio previously
aborted the entire scan on the first topic-file 404. The inventory now retains
that page with `html_error`, names the course/page/ID in its warning, and reads
the remaining content. Both planners block an included missing page until it is
restored and rescanned or explicitly skipped; they never create blank HTML for
it. Other HTTP failures still stop scanning with course/page context. Nick will
import another course copy himself; no live restoration/deletion was performed.

A general course-to-course transfer (EA-113 → EA105 was only the first test). He wants it
**easy to understand and modern — "Brightspace but a lot better in UX/UI"**: calm screens,
one primary action per step, plain-language options that say what they do and when to pick
them, a live "what will happen" summary, warnings with one-click fixes, undo. No cockpit of
buttons. Show a mockup before building new UI when the design is open.

The next big piece is **Transfer Studio**: a real web interface. The app's "Plan transfer"
button (Build & style hub → Structured Transfer) should start a local server and **open the
page in Chrome / the default browser** — *not* inside the app window (Nick finds the
Qt-drawn screens don't look nice).

## Design reference

`docs/design/transfer-studio-prototype.html` — the clickable prototype Nick approved
(also at https://claude.ai/artifact/DepjVbkxMkDoyDJGMJ65UY). It is in a "Design Component"
format (`<x-dc>`, `{{holes}}`, `<sc-for>`/`<sc-if>`, a `class Component extends DCLogic`
script), so it won't run on its own — read it for layout, wording, colours and behaviour:

- Steps: Connect → Arrange → Styling → Review → Create (starting-point choice sits on Connect).
- Starting point: four options, each with "Best when…" and a mini preview of the outline.
- Arrange: source course left, destination outline right, "What will happen" panel; drag
  items **and whole folders**; a "drop here to make a new module" area; "Edit sections"
  per module (custom named sections, apply/save layouts); copy-name buttons; Undo/Redo +
  Ctrl+Z/Ctrl+Y + "Removed … · Undo" toast; warnings with fixes (name clash → add into
  existing, release condition dropped → include the form, items to place → accept suggestions).
- Styling: 3 modes + "then apply this look", page list, before/after preview, removed/kept lists.
- Review: numbered "exactly what will happen", "Also copy" extras marked Adds/Replaces,
  confirm checkbox, Create disabled until blockers are fixed.
- Colours: bg #F4F5F2, surface #FFFFFF, ink #17212B, muted #5B6672, border #E2E5E0,
  accent #0B6B66, warn #7A4300 on #FFF4E0, info #2D4E8A on #EAF0FB. Font: Hanken Grotesk.

## Architecture to build

1. **Local server** started by the panel's button: bind `127.0.0.1` only, random port,
   one-time token in the URL (reject requests without it — other sites/programs must not
   reach it). Open with `webbrowser.open(...)`. Stop when the app closes. Brightspace login
   stays in the app (Playwright session); the page never sees credentials.
2. **Static front end** bundled with the app (no CDN; must work offline / in the
   PyInstaller build). Plain HTML/CSS/JS or a small vendored library.
3. **JSON API** that wraps the existing, tested backend — don't reimplement logic:
   - scan: `transfer_outline_runner.scan_courses` (runs in the app's browser worker, like
     `TransferPanel._scan_clicked` does via `with_transfer_browser`)
   - plan/edit: everything in `src/transfer_outline.py` (`build_outline_plan`, `add_module`,
     `add_page`, `rename`, `move`, `place`, `place_source_module`, `skip`, `unplace`,
     `apply_pattern`, `use_existing`, `accept_suggestions`, `join_page`, `set_sources`,
     `pattern_from_module`, `load/save_patterns`, `check`, `validate_outline`, `counts`)
   - styling preview: `transfer_outline_runner.styled_markup`, `transfer_styling.template_choices`
   - create/resume: `transfer_outline_runner.apply_outline` (stream its log messages as progress)
   - undo/redo: keep a stack of plan snapshots server-side (the plan is plain JSON).
   - Save the plan after every change (`transfer_runner.save_plan`, under
     `%APPDATA%\BrightspaceAutomator\structured-transfers\`), as the Qt screen does.
4. Keep the existing Qt screen (`src/panels/transfer_panel.py` + `transfer_arrange.py`,
   `transfer_pages.py`) working as a fallback until the web version is proven.

Plan model gaps the prototype needs (add to `transfer_outline.py` with tests):
- "Edit sections": rename/remove a section folder, add a custom-named section — today
  sections are just module nodes, so `add_module(parent)`/`rename`/`remove` already work;
  expose them per module. Layouts = patterns (`pattern_from_module`, `apply_pattern`).
- Undo history (server side).

## Backend map (all in `src/`)

| File | Role |
| --- | --- |
| `transfer_outline.py` | Plan model (schema 3): rows, outline nodes, editing, patterns, suggestions, `check()` notes/blockers, title repair for cut-off Moodle titles |
| `transfer_outline_runner.py` | Creates the plan in Brightspace: prepare (styling, file plan), one Copy Components job, modules, Add Existing links, pages, order, grade-category merge, readback checks, report |
| `transfer_components.py` | Drives Brightspace's Copy Components wizard (exact IDs, verifies confirmation page before Finish), course-file selection, grade-category plan/move/remove |
| `transfer_styling.py` | Preserve / strip Page Automator / strip all, template look, content-preservation check |
| `transfer_files.py` | Link rewriting, file bundle (`server` map = files Brightspace copies itself), Moodle placeholder unlinking |
| `transfer_api.py` | Brightspace API adapter; `file_info` (HEAD; missing file = 302 → /d2l/error/404) |
| `transfer_plan.py`, `transfer_guided.py`, `transfer_runner.py` | Older workshop (schema 1) planner/runner — kept so old saved plans resume; outline runner subclasses `TransferRunner` |

## Known facts (learned live — don't relearn the hard way)

- Copy Components keys: assignments/rubrics/grades/quizzes by ID, discussions `t<topicId>`
  (forum `f<id>` added automatically), course files by path `\folder\file.ext`
  (case-insensitive; folder added automatically). Grade items bring their category.
- Copy Components **always creates a new same-named grade category** (weighted, counted) —
  the runner moves copied grade items into the destination's existing category and deletes
  the empty duplicate.
- Course files copy server-side to the **same path**; identical existing file → no duplicate;
  **different existing file → Brightspace silently overwrites it** (the runner never sends
  those; it keeps the source version in the transfer folder).
- Historical finding, superseded by the 2026-10-14 update: no Valence API for surveys; survey linking was by exact title. Discussion/survey linking via
  Add Existing is **not yet verified live** (failures become report notes).
- Moodle-import leftovers: titles cut off with "..." (repaired from the page heading),
  links like `$@URLVIEWBYID*123@$` (unlinked, text kept).

## Testing

```
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```
Set `QT_QPA_PLATFORM=offscreen` for Qt tests. Transfer tests: `test_transfer_outline*.py`,
`test_transfer_components.py`, `test_transfer_styling.py`, `test_transfer_panel.py`,
`test_transfer_structure.py` (has the in-memory `FakeCourse`). Two failures in
`test_link_review_panel.py` are pre-existing and unrelated.

Live tools (use the app's saved session, `config.SESSION_FILE`):
- `tools/transfer_one_module.py SRC TGT "Module" [--create]` — plan/create one module
- `tools/copy_components_check.py`, `tools/course_files_check.py` — wizard experiments
- `tools/preview_transfer.py` — screenshots of the Qt screen

**Live writes are allowed only in Nick's sandboxes**: 25708 "Nick Usatenco EA-113 Sandbox"
(source) and 25707 "Nick Usatenco EA105 Sandbox" (destination). Check the course name
contains "Sandbox" before writing; never write to any other course without asking.
Current sandbox state: module "Mental Health and Self Care" (unit 996134) created by the
transfer (46/46 checks); test assignment 51705 "Anxiety and the EA Learning Assignment";
`02_Sample Welcome/06_FAQ.html` was overwritten by the file-clash test.

## Suggested order

1. Server + token + "Plan transfer" opens the browser; Connect/scan; Arrange showing the
   real scanned courses (read-only).
2. Editing in Arrange (drag/drop, folders, sections/layouts, rename, skip, undo, warnings panel).
3. Styling with real before/after previews.
4. Review + Create with live progress and the report; resume.
5. Make the web page the default; keep Qt as fallback.

Still open in the engine: live test of discussion/survey linking ("Supports in Schools" has
2 discussions), external tool (LTI) links via Copy Components "External Learning Tool Links",
the "Also copy" extras, release-condition warnings. Nothing is committed to git yet.
