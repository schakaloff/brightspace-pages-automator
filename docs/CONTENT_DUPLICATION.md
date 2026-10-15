# Duplicate content

Restart the local app, then open **Build & style → Duplicate content**.
Paste a Brightspace course or section link and click **Load course**.

Select a section to include its nested sections and topics. Expand it to uncheck
individual items. You can select pages, files or links individually, or select
several sections. Search filters the visible list without changing your selection.

Choose a destination in the **same course**, a number of copies (1–20), and a name.
For several selected roots, each keeps its original name plus “Copy”. Collisions
are numbered automatically; child titles and source order are preserved. Individual
topics need a destination section. Root sections can go into Course home.

**Resulting structure** updates with the exact proposed hierarchy, names and counts.
Uncheck **Include everything inside** to copy section shells without their contents.
**Review copies** shows the complete proposal and practical notes. Nothing is written
until you acknowledge the named course and click **Create hidden copies**.

## What is copied

- New sections and topics, with their descriptions. New content is hidden.
- Course-owned HTML pages and files, including HTML/CSS dependencies. Each repeated
  copy has its own file folder, so editing a new page/file leaves the original intact.
  Authored prose, formatting and styles are preserved; this tool does not split or
  restyle pages. Multiple selected pages in one copy share that copy's dependencies.
- External links, including linked videos, retain their URLs.
- Assignments, quizzes and other activities become **ordinary link shortcuts** to the
  existing activity. No new assignment, quiz, grade item, rubric, attempt or submission
  is created. Original activity settings still apply. These shortcuts are not a new
  native graded Content activity and do not recreate native completion tracking.

Content dates and release conditions are not recreated. Links to other existing
Content topics continue to point to those topics. Binary document-internal hyperlinks
are preserved. Review availability and links before publishing.

Content Service file topics and course-local JavaScript dependencies are blocked
rather than copied incompletely. Files referenced from another course are also
blocked. Uncheck affected content or use Brightspace's own copy tools.
Cross-course reorganization remains in **Structured Transfer**.

## Saved plans, verification and recovery

Plans save under `%APPDATA%\BrightspaceAutomator\content-copies\`. The latest plan
restores when the app opens. **Open saved copy plan** starts in this folder.
Each write gets a durable pending record before execution and an independent readback.
New Content objects carry a hidden ownership marker; new files have verified hashes.

After an interruption, resume the same saved plan. Successfully completed steps are
recognized; a lost Content response can be reconciled from its ownership marker.
An uncertain request that cannot be reconciled blocks retry instead of blindly posting
again. Applied settings are frozen. Existing copies that have been edited are never
overwritten. Reload the course to start a separate batch.

The report lists verified new IDs and the saved plan/recovery inventory paths. Recovery
is a metadata snapshot and operation journal, not a full Brightspace export or an
automatic rollback. The tool makes no DELETE or PUT requests. It only adds new Content
objects and files in its own copy folders. Source Content metadata, selected section
membership, source file hashes and copied order are checked before/after execution.

The implementation uses the existing authenticated browser and Manage Files uploader.
The [Brightspace Content API](https://docs.valence.desire2learn.com/res/content.html)
documents creating sections/topics and pointing File topics at uploaded course files.

## Verification

Behavior tests exercise nested/partial selection, independent repeated file copies,
HTML/CSS asset rewriting, preserved prose/styles, activity shortcuts, hidden content,
name collisions, zero-write blockers, interrupted/lost-response recovery, and a
completed rerun with all new writes/uploads disabled. Panel tests cover live preview,
search, naming, course changes, saved-plan locking, final confirmation and responsive
controls. Hub tests cover navigation to the new panel.
All 41 duplication/panel/navigation tests and 73 existing Structured Transfer tests
pass (114 selected tests total).

The screen renderer is `tools/preview_duplicate.py`; it uses example course data and
performs no browser requests. Screenshot previews are not live course inventories.
This duplication feature has not yet been applied to a live Brightspace course.
The new adapter successfully loaded 128 Content objects from Nick Usatenco EA105
Sandbox using GET requests only and built a section-shell proposal without uploads
or Content creation. Visual QA is recorded in the repository's `design-qa.md`.
Claude's Structured Transfer/page-splitting implementation is preserved.
