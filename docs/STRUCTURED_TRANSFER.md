# Structured Transfer

Copy any Brightspace course into any destination course, reorganized, restyled
and with its activities. EA113 → EA105 was the first use case; nothing in the
feature is specific to it. The EA105 five-part layout is now just one saved
pattern.

## Workflow (Build & style → Structured Transfer)

### Transfer Studio browser editor

**Build & style → Structured Transfer → Plan transfer** now launches Transfer
Studio in the default browser. It starts a local server on `127.0.0.1` at a random
port; keep the desktop app open. The page follows the approved prototype's
colours, starting-point cards and three-column Arrange view.

Connect supports all four starting points and the existing saved/detected
patterns. Scanning uses the app's existing authenticated Playwright worker and
only reads the courses. The main page receives course names, outline rows, counts
and the engine's warnings; it never receives login credentials or session files.
Styling previews receive isolated, sanitized HTML. Login, if needed, happens in
the app's browser window. Course-link errors identify Copy from/Copy into and
stay on Connect; an invalid scan attempt does not leave a banner over Arrange.
The shared browser-worker parser also accepts `/d2l/home/<course-id>` links.
This matters because the desktop authentication wrapper validates the URL again
after Studio accepts it. The regression test now exercises that real wrapper
through both Scan and Create, mocking only browser IO and course operations.

If an imported page's HTML file returns 404, Scan retains the page and logs its
course, title and ID while continuing with the other pages. An included source
page with a missing file blocks Create until its file is restored and rescanned,
or the page is explicitly skipped. Missing files are never replaced with blank
pages. Permission/server errors still stop the scan with page context.

Arrange supports editing the saved draft:

- Click an item or folder to select it. Ctrl/Cmd-click adds to the selection;
  Shift-click selects a range. Checkboxes also toggle individual items.
- **Select an area** lets you drag a box over multiple rows in either column.
  Ctrl/Cmd adds to a selection; Esc cancels. Right-click a folder and choose
  **Select all items in this folder** to select its contents, including nested
  items. The folder's arrow expands/collapses it; its title selects it.
- Right-click items, modules or section folders for their available options.
  **Selection options** offers the same controls without a right-click.
  Shift+F10 opens the menu from a focused item, and arrow keys navigate it.
- Add a module, add/rename section folders, rename eligible content, remove
  selected outline nodes, leave source items out, or include them again.
  Removing a folder returns its contents to the unplaced source list. It never
  deletes anything in Brightspace. **Leave out of transfer** explicitly skips
  source items; **Include again** returns transferable items to the unplaced list.
  Choosing a whole source unit also removes its linked right-hand draft module,
  including automatic pages and folders. Modules are matched by source IDs;
  unrelated modules with the same title are kept. If the linked module contains
  added/edited pages, folders or content from another unit, a confirmation lists
  those changes before anything is saved. Cancel keeps the draft unchanged;
  confirmed removal returns other units' content to unplaced. Existing course
  content is never deleted. Ctrl+Z restores both sides in one step.
  A whole-unit Skip shows **Skipped** and a muted, crossed-out title while
  collapsed, including explicitly skipped empty units. Skipping all individual
  items keeps the draft module and shows **All items skipped**. Partly skipped
  units show the skipped/total item count. Nested folders and Undo/Redo update
  these markers. In older saved drafts, choose whole-unit Skip again to remove
  modules left behind by the previous behaviour; this is not applied silently.
- Drag selected source items/folders into a destination folder, or move a
  destination unit into the gap between units to reorder it. A highlighted line
  marks the insertion point; the top/bottom edge of a destination row also
  inserts before/after it, while a folder's centre drops inside it. Groups keep
  their outline order, and Undo/Redo restores the entire move. Dragging can insert
  source units at these positions too. Existing destination modules retain their
  ordering protection.
  A selection can also move with **Move to folder**. Source folders can become new top-level
  modules. Existing destination modules retain the engine's protections.
- **Undo/Redo**, Ctrl/Cmd+Z, Ctrl+Y and Ctrl/Cmd+Shift+Z restore/reapply whole bulk
  changes. Delete/Backspace removes the current outline selection (or skips a
  source selection). Text fields retain their normal typing shortcuts. A toast
  offers Undo after edits. History holds the last 50 changes while the app stays
  open and survives a page refresh; a new scan starts a new history.

Counts and warnings update after every edit. Module name clashes offer **Add into
the existing module**. Edits save before succeeding, validate the draft revision
to protect against stale tabs, and roll back the entire change on a failure.
Created/running drafts cannot be edited. Changes from the desktop editor refresh
the browser and invalidate its old undo history; closing the desktop view cannot
overwrite newer browser edits.

Right-click a module for **Edit sections**, apply/save a section layout, choose
source modules for suggestions, or add a placeholder/Introduction/Session Overview
page. Generated pages offer **Write page text**. Eligible outline items can move
up/down; source page parts offer **Keep the page whole**. Suggested placements can
be accepted from the toolbar. Name clashes and row-specific warnings offer fixes
or **Show in Arrange** in Review.

**Styling** offers all three cleanup modes, then a look from either course or the
bundled Calm/Classic styles. Each source page can override the transfer setting.
Before/After use the same rendering and preservation checks as creation; generated
pages use their exact shared renderer. Removed/kept lists explain the result. The
iframes are sandboxed with scripts, form submission, external media and linked
stylesheets disabled; this isolation changes the preview only.

**Review** lists the creation steps, activities, skipped items, grade-category
handling and blockers. Blocking issues are shown first; informational notes are
collapsed and explicitly do not block Create. The destination confirmation is
near the top, and the sticky Review footer explains why Create is disabled.
Unplaced-item blockers offer **Find unplaced items** to select/filter the source
row in Arrange. Pages linking to a skipped assignment also block Review. When
exactly one same-named destination assignment exists, **Use the existing destination
assignment** explicitly links the copied page to it, preserving its settings and
rubric even when they differ from the source. The choice saves with the draft and
supports Undo. It does not copy, replace or edit the assignment. Create validates
the chosen assignment against the fresh destination scan. Unresolved links still
stop before writes, with the page name in the saved preparation report.
Note details identify the full folder path; empty generated pages
say their placeholder text starts hidden. Creating requires an explicit confirmation naming the
destination and the current plan revision. **Create** runs the existing engine
through the app's browser worker; HTTP threads never access credentials or drive
Playwright. The screen shows live phases/logs, Stop after the current request,
Stop/Failure reports, Resume with a fresh destination confirmation, readback
results, report export and a link to the destination. Started plans cannot be
edited. The journal remains authoritative; ambiguous requests stop for inspection
rather than repeating a write.

**Open saved transfer** on Connect lists local schema-3 plans, including started
and completed ones. Opening a started plan restores the report and Resume controls.
Plan selection is confined to the saved-plan directory; requests cannot supply
arbitrary file paths. The last Studio plan is remembered by the app.

The Qt fallback remains under **Use in-app planner (edit and create)**. Older
workshop plans are still handled there. Engine limits are explicit in Review:
external tools/unsupported content require manual copying, additional components
(announcements, groups, glossary, appearance, navigation) use Brightspace's own
tools, and activity release conditions require manual review. Discussion/survey
linking failures are reported with Add Existing instructions.

Discussion selection first opens the copied forum. Surveys are under **More →
Surveys**. Activity selection is confined to the QuickLink chooser, so an existing
sidebar item with the same title cannot intercept it. A click whose save cannot
be verified stays pending and stops the run rather than being repeated.

Native Add Existing can create a visible survey Content reference. The runner
hides new references with a separate journaled operation and verifies visibility
on readback. References present before the run are left alone; a later instructor
visibility change to a verified reference stops a re-check instead of undoing it.
Tool availability is separate: copied surveys keep their active state and dates,
and Review flags an active survey even when its Content link starts hidden.

Each successful scan immediately saves a schema-3 plan in the same
`structured-transfers` directory. The app remembers the Studio draft separately
from the Qt plan (`transfer_studio_plan_path`). A failed rescan keeps the previous
saved draft. Refreshing the browser reopens the current Studio draft. If the
launch link expires, click Plan transfer again.

The launch URL contains a one-use secret. The server exchanges it for an
HttpOnly, SameSite=Strict session cookie; the page clears the token from its URL.
Requests require a valid session and the exact localhost Host. Cross-site reads
are rejected; POSTs additionally require the exact Origin and a custom request
header. There is no CORS access. Assets and the licensed Hanken Grotesk font are
bundled under `assets/transfer-studio/`, included by the existing PyInstaller
asset trees. The server closes when the app exits.

Implementation: `src/transfer_studio.py`, `assets/transfer-studio/`, and the
`TransferPanel` queue bridge. The existing transfer model and runner are reused.

Offline verification:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
.venv\Scripts\python.exe tools/check_transfer_studio.py
```

The browser QA tool uses offline fixtures plus recorded inventories from
`transfer-investigation/guided-workshop-plan.json` when present. It tests the
HTTP frontend in installed Chrome (Edge fallback), all starting points, validation,
filtering, refresh, context menus, range/box selection, bulk remove, folder edits,
drag/drop, undo/redo, layouts, styling, confirmation gating, creation, Stop/Resume,
saved-plan reopening, report display, desktop/mobile overflow and axe accessibility. Screenshots go
to `transfer-investigation/studio-ui/`. It closes its server after checking and
never contacts Brightspace. Studio tests cover token/session protection, Host,
Origin, cross-site requests, scan concurrency, persistence, failed scans,
packaged asset paths, shutdown, and the desktop worker bridge. Editing tests cover
atomic bulk changes, overlapping parent/child selections, history, stale tabs,
cycle rejection, failed saves and interaction with desktop edits. Creation tests
use the real runner against the in-memory course, including a single component
copy across Stop/Resume and a finished rerun with zero additional writes. The
source-object cache returns each thread's captured lookup so HTTP previews cannot
change a browser worker's inventory lookup.

Verification on 2026-10-13:

- Full offline suite: 868 passed; the two previously documented link-review tests
  still fail. The final transfer-specific suite passes all 165 tests, including
  protection against a stale in-app save after the browser switches drafts.
- Browser QA passed, including desktop/mobile accessibility and the real offline
  engine's Stop/Resume/report workflow.
- Live Studio scanned both sandboxes (111 source items, 19 pre-existing destination
  modules), then created only a small hidden verification module and placeholder
  page in destination 25707: **Transfer Studio verification 40adf235** (module
  996360, page 996361). All seven checks passed; the source was unchanged. Its plan
  and report are under `transfer-investigation/studio-live/`.
- The normal Windows PyInstaller build passed its isolated offline smoke check:
  Qt panel, authenticated server, scan, static assets/font, built-in looks and real
  page previews. The Windows spec excludes unversioned ICU libraries collected
  from unrelated tools on PATH, which otherwise broke Qt startup. macOS was not
  built on this Windows host.

Additional verification on 2026-10-14:

- 198 related tests passed: 177 transfer tests and 21 structured-transfer tests.
  The final picker fixture includes matching sidebar titles to verify that the
  chooser is isolated. Browser/accessibility QA passed, and the rebuilt Windows
  package passed the isolated Studio startup smoke check.
- Live copying and native linking of one ungraded discussion and one survey passed
  in sandbox 25707, with one component job. Hidden module **Transfer Studio activities
  d416eadf** is **996900**; its discussion Content link **996906** targets discussion
  **21202**, and survey Content link **996910** targets survey **77467**. All 12
  checks passed, including source unchanged, exact tool IDs and hidden Content.
- The final production discussion adapter passed its full forum/topic flow in
  hidden test submodule **996915**, Content link **997064**, using discussion **21202**
  again. These verification objects remain in the sandbox.
- Saved plan/report: `transfer-investigation/activity-live/0780b4d0c269446fbe57a27d89ce6201.json`
  and `.report.txt`. The separate picker check records the recovery inspection of
  its failed test request and the verified final link.

Reproducible verification tools:

```powershell
# Live read-only scan; --create adds only the small hidden sandbox verification.
.venv\Scripts\python.exe tools/check_transfer_studio_live.py

# Activity inspection is read-only by default. --login uses the app's saved login.
# --create copies exactly one discussion/survey into a hidden sandbox test module.
.venv\Scripts\python.exe tools/check_transfer_activity_live.py
.venv\Scripts\python.exe tools/check_transfer_activity_live.py --resume transfer-investigation/activity-live/0780b4d0c269446fbe57a27d89ce6201.json

# Build and test an isolated Windows package; no login or live requests in smoke mode.
.venv\Scripts\python.exe -m PyInstaller installer/brightspace_automator.spec --noconfirm --distpath transfer-investigation/studio-build/dist --workpath transfer-investigation/studio-build/work
.venv\Scripts\python.exe tools/check_transfer_studio_bundle.py transfer-investigation/studio-build/dist/BrightspacePagesAutomator/BrightspacePagesAutomator.exe
```

`--studio-smoke` is a hidden offline test entry point. It uses a stub main window,
fake course inventories and a temporary data folder, and exits when its checker
finishes. It neither loads app credentials nor claims the normal app mutex. The
normal app still uses its existing startup and single-instance behavior.

### Existing in-app planner

Five steps, shown top right. One primary button per step, bottom right.
Everything else is in **More** or appears only for what is selected.

1. **Connect.** Paste **Copy from** and **Copy into** links (home, content,
   lessons or `?ou=` links all work). **Scan both courses** only reads.
2. **Starting point.** How the destination outline begins:
   - *Same as the source*: every module and folder, in order, everything placed.
   - *Use a pattern*: one module per source module, each given the pattern's
     submodules and pages. Items the pattern is sure about move in; the rest are
     suggested.
   - *The destination's modules*: the shell's existing modules; content is added
     into them (matched by name).
   - *Start empty*.
3. **Arrange.** Source on the left, destination outline on the right.
   - Drag items, or whole source folders, from left to right; drag within the
     right to reorder or nest. Or select and use **Move to…** / **Copy folder to…**.
   - **+ Module**, double-click to rename, **Add** (submodule, placeholder page,
     Introduction or Session Overview page from a template), **More** (apply a
     pattern, save a layout as a pattern, choose which source modules a module
     takes suggestions from, accept its suggestions, remove).
   - **Skip** leaves an item out; **Include** brings it back. Pages labelled
     before/after can be split into parts; **Keep page whole** reverses that.
   - The footer counts what's left and offers **Accept N suggestions**.
4. **Styling.** One setting for the transfer, with per-page overrides:
   *Preserve existing styling*, *Strip Page Automator styling*, *Strip all
   custom styling*, optionally followed by **Then apply this look** (a styled
   destination or source page, or Page Automator's Calm/Classic theme). Each page
   shows Before/After and what was removed.
5. **Create.** Problems to fix (each with **Show**, and **Use the existing one**
   for module-name clashes), counts, and one confirmation naming the
   destination. Creation can be stopped after the current step and resumed.
   Afterwards the report lists every check and anything to review.

Plans save after every change under `%APPDATA%\BrightspaceAutomator\structured-transfers\`;
the last one reopens with the app. Saved patterns live in `patterns.json` there.

## Patterns

A pattern is the list of parts every module gets, using the section format below.
Build a module the way you want it in Arrange and choose **More → Save this layout
as a pattern**; it's offered for every later transfer. Patterns detected from the
destination's own modules and from the source's shared folders are offered too.

| `kind` | What it creates |
| --- | --- |
| `placeholder` | A page with a `message` (e.g. Online Classroom) |
| `introduction` | A page from the introduction template, with authored text |
| `folder` | A submodule that holds items (`session_overview` adds a Session Overview page) |
| `inline` | No submodule; items sit directly in the module |

Folder options steer suggestions: `aliases` (source folder names that mean this
section), `keywords`, `phase` (before/during/after page labels), `assignments`,
`files`, `default`. With `default_is_rule` the default section is a rule rather
than a suggestion; `split_pages` splits labelled pages.

## Styling rules (`src/transfer_styling.py`)

- **Strip Page Automator styling** removes Page Automator's style block, font
  link, layout wrappers (`main-container`, `hero`, `content-body`, `action-card`,
  `link-btn`, `video-wrapper`, …), its classes and `data-bpa-*` attributes, its
  icons and its banner label. Other CSS and the author's own formatting stay.
- **Strip all custom styling** also removes other style blocks and stylesheet
  links, inline styles, classes, `<font>`, presentational attributes,
  comments, styling-framework scripts (D2L Courseware templates) and layout-only
  wrappers.
- Never removed: text, headings, lists, tables (caption, `scope`, `headers`,
  table `border`), links, images (with `alt`; sizes kept), embeds and iframes
  (all attributes but class/style), math (MathML untouched), `id`, `lang`,
  `dir`, `role`, `aria-*`, `data-*`, other scripts. Screen-reader-only text keeps
  a minimal hiding rule. Wrappers are removed without merging blocks of text.
- Every cleaned page is compared with the original (same words in order, same
  links/media/embeds, same lists and tables, no stylesheet-generated text lost).
  If anything differs, the original page is used and the reason is shown.
- Checked against all 48 pages of the EA-113 sandbox (30 Page Automator pages,
  11 D2L Courseware pages): every page cleans in both modes with the check passing.

## Activities: Copy Components (`src/transfer_components.py`)

This Brightspace release can't attach rubrics through the API, so assignments,
quizzes, discussions and surveys are copied with Brightspace's own Copy
Components wizard, driven by the app:

1. Before anything is written, an activity with the same name already in the
   destination blocks the transfer, unless it's an identical assignment, which is
   reused. Two source activities with one name also block.
2. One wizard run: *Copy Components from another Org Unit*, the source course,
   *Select individual items to copy* for each needed tool, ticking items by their
   Brightspace IDs: the activities, their rubrics and their grade items. Content
   is never selected. "Include associated files" is ticked.
3. The wizard's confirmation page is read back. It must list exactly the planned
   IDs (plus the categories of selected grade items, which Brightspace adds), no
   other component, and no date offset. Otherwise nothing is copied.
4. Finish, then wait (up to 15 minutes) until every copy appears, including surveys;
   match the source tool's own name and use its destination ID; link each into its
   module with Content's Add Existing; rewrite page
   links that pointed at the source activities.

The copy is one journaled step: an interrupted run won't start a second copy.
Survey catalogues are read from the supported LE 1.82 API, including surveys not
linked in Content. An unavailable catalogue differs from an empty one and blocks
survey transfers. Source comparisons omit this newly added catalogue only for old
plans that never recorded it, preserving their original run signatures; new plans
also detect survey definition changes.

### Course files go through Copy Components too (no downloads)

Learned live on the sandboxes (2026-10-13):
- The wizard's **Course Files** step lists files by path from the course root
  (`\img\Preview.png`); paths ignore case, and picking a file adds its folder.
- Copied files land at the **same path** in the destination, contents identical. A
  428 MB video copied server-side in under 20 seconds.
- Same path, same contents: no duplicate is made.
- Same path, **different contents: Brightspace silently replaces the destination's
  file.** (Test: `02_Sample Welcome/06_FAQ.html` in sandbox 25707 now holds EA-113's
  version; the original 9,605-byte file could not be restored.)
- Content topics can also be picked by ID (a topic brings its module), but the app
  builds Content itself so the planned outline is kept.
- The confirmation page's only option is shifting dates; the history page
  (`/d2l/le/conversion/copy/<ou>/History`) shows "Copy Completed: <time>".

So, before any write, the runner checks every course file the transfer needs (size, and
contents for files up to 5 MB) and decides: **new** → copied by Brightspace in the same
job as the activities; **identical** → the destination's copy is used; **different** →
EA-113's version goes into the transfer's own folder (files up to 128 MB) or, if larger,
the transfer stops before writing and asks for one of them to be renamed. Pages are still
read (small text) for styling and link rewriting. Links the Moodle import never filled in
(`$@URLVIEWBYID*…@$`) are listed in Review; their text is kept without the dead link.
Tools: `tools/course_files_check.py` (copy files and check where they land) and
`tools/transfer_one_module.py` (plan and create one module).
Activities keep their own grade items and categories from the source; the app no
longer edits gradebook weights in this flow. External tools, SCORM and other
activities are marked **Manual** and skipped.

## Safety

- The source is only read. All writes are bound to the confirmed destination.
- New modules, pages and links are hidden. Existing destination modules are only
  added to: never renamed, moved, reordered or deleted.
- Every page, file and link is prepared before the first write; an unmappable
  course-file or activity link stops the run before anything changes.
- Files are copied into the destination's own course files and links rewritten.
- Every write is journaled with ownership markers; resuming verifies what exists
  and never duplicates. Rerunning a finished plan makes no writes.
- A recovery snapshot of both inventories is saved before the first write.

## Earlier workshop transfers

Plans made with the earlier workshop screen (schema 1) still open. Created ones
can be resumed or re-checked; unfinished ones are read-only, and **Start a new
transfer** plans them again with the new screen. The workshop planner and runner
(`transfer_plan.py`, `transfer_guided.py`, `transfer_runner.py`) remain for them;
the general transfer is `transfer_outline.py` and `transfer_outline_runner.py`.
The old workshop UI files are kept locally in `transfer-investigation/old-workshop-ui/`.

## Verification status (2026-10-13)

Done:
- 810 offline tests pass: outline planning, styling, wizard selection, a full
  run against an in-memory Brightspace (including resume with zero writes, adding
  into existing modules, blocking on name clashes) and the screen. The two failing
  tests in the full suite are the pre-existing `test_link_review_panel.py` ones.
- Live, read-only: the Copy Components wizard on sandbox 25707 was driven with
  source 25708 up to its confirmation page for an assignment (with rubric, grade
  item and category), a quiz and a discussion topic; the confirmation matched the
  plan exactly. Nothing was copied.
- Screens rendered in light/dark and narrow widths with `tools/preview_transfer.py`.

- Live copy (2026-10-13): `tools/copy_components_check.py 25708 25707 "Anxiety and the
  EA Learning Assignment" --copy` copied one assignment into sandbox 25707. All checks
  passed: instructions, rubric, points and submission settings match, the grade item
  (184459, 15 points) exists, no Content was added.
  **Finding:** Brightspace created a second "Assignments" grade category (184458),
  weighted 60% and counted in the final grade, rather than reusing EA105's existing
  one (weights then totalled 160%). The copied grade item was also visible.
- Grade categories are now handled: Review notes say what each copied grade item's
  category will do; by default ("Put copied grade items into …'s categories with the
  same name") the runner moves each copied grade item into the destination's
  same-named category and removes the duplicate only if the copy created it and it is
  now empty (journaled; the category list from before the copy is kept in the journal).
  Otherwise the report flags category weights that no longer total 100% and copied
  grade items that are visible. Live (2026-10-13, `--merge-categories`): grade item
  184459 moved into "Assignments" 184211, still linked to assignment 51705 with the same
  name and points; duplicate 184458 removed; weights back to 70/30. The test assignment
  51705 (hidden) and its grade item remain in the sandbox.

Not yet done:
- Broad live coverage of graded discussions and other course/activity combinations.
  The discussion and survey sample above is now verified with the new runner.
- Full-course transfers at larger scale and macOS packaging remain unverified.

## Platform sources

- [Content and ordering](https://docs.valence.desire2learn.com/res/content.html)
- [Course files](https://docs.valence.desire2learn.com/res/managefiles.html)
- [Assignments](https://docs.valence.desire2learn.com/res/dropbox.html)
- [Rubrics](https://docs.valence.desire2learn.com/res/assessment.html)
- [Grade objects/categories/setup](https://docs.valence.desire2learn.com/res/grade.html)
- [Course copy jobs](https://docs.valence.desire2learn.com/res/course.html)
- [Survey catalogue and settings](https://docs.valence.desire2learn.com/res/survey.html)

Rubric association routes need LE 1.100, which this tenant lacks; never invent
undocumented requests. Grade category names can't contain `/`. Manage Files folder
names are limited to 50 characters. Brightspace strips comment-only rich text, so
ownership markers are hidden paragraphs.
