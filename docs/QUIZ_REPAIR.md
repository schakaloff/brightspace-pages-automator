# Fix Brightspace quizzes

Open **Build & style → Repair quiz exports** in MTB Writer. The browser page stays local; keep MTB Writer open while using it.

## Add files in the browser

- **Brightspace course to fix — required:** a Brightspace ZIP containing the quizzes and Question Library. A full-course export also works within the package size limit.
- **Original course — optional:** the original Moodle backup (.mbz) or a trusted Brightspace ZIP. It supplies a comparison. An older backup does not prove the current real-course content is identical.
- **Extra pictures — only if needed:** a ZIP containing missing picture files. Existing files in the main course are used first; pictures can also be recovered from the original Moodle backup when the source is unambiguous.

Click a file button to use the browser's file picker, or drop one file into the corresponding box. No desktop file dialog is requested by this web workflow. Course files are streamed to a local review folder in the app's user-data directory; nothing is sent to an external service. Progress is shown while adding a file. Files up to 4 GB are accepted (saved review JSON up to 32 MB); expanded course data must also meet the existing package limits. Large full-course exports may need to be reduced to Quizzes and Question Library.

## Resolve problems, not every question

Click **Start checking**. The top panel lists what blocks the fixed ZIP. The default **Must fix** filter shows those questions only. You do not need an approval tick for every question.

Compare the version on the left with **Question for your download** on the right. Each pane has its own button under it: **Use this question** (left) or **Keep this question** (right) saves a decision. If a button is greyed out, a line under it says why. A version with blank answers cannot be confirmed as a valid question. **Next problem** moves through the remaining blocking questions. A choice never overrides missing pictures, invalid answers, unsupported types or unsafe layouts.

Formatting-only differences between Moodle and Brightspace are optional layout notes. Actual stored-version conflicts, changed answers, keys, points or missing references still require an explicit decision. Question-type conflicts remain hard blockers. **Layout notes (optional)** can be inspected without forcing approval of all questions.

Expand **Edit this question yourself** to adjust question/answer HTML, preceding material, points and correct choices. **Save my edits** validates and saves the edited question. Existing answer feedback stays attached to its answer position. Matching rows can be reviewed and an existing matching version selected, but adding/removing rows and changing question types are outside this version. Shared question/section edits and custom scoring that cannot be changed safely stop the export.

## Download and continue later

When no blocking problems remain, click **Download fixed quizzes**. A new `_FIXED.zip` downloads through the browser; Windows/browser download settings determine its final location. Existing input files are not overwritten. The app keeps a separate local generated file, and **Download again** retrieves it until the review changes.

The review is saved automatically on this computer. **Save review for later** downloads a JSON copy. **Continue a saved review…** opens it with the browser picker (or drop it on that button). A saved review requires the same locally stored input files; changed files are rejected rather than reusing stale decisions. Reviews from the earlier native-picker version remain compatible when their original files are still available.

For the provided MEDR course, the original Moodle backup recovers 20 pictures, and only Exam 1 questions 1–3 require version decisions: keep Q1's current picture-bearing question, use Q2's three named answers with its picture below the prompt, and use Q3's clean paragraph version. The remaining 486 questions do not require approval ticks. They remain available under **All questions** and optional layout notes.

## Export and privacy checks

Correct Answers, Limited Selections is used for multi-select; matching pairs receive equal credit. A one-point multi-select with two correct answers divides the point into halves; three correct answers divide it into thirds under this policy. Questions, bank records, correct-answer identifiers, feedback and quiz delivery settings are preserved except for explicitly reviewed edits and the approved scoring repairs. Native individual answer containers are retained when options change.

The output is reopened to check ZIP integrity, unchanged manifest and question counts. Blank answers in unused library versions are also retained as a blocker. Input hashes guard against changed source files. All preview scripts and external requests are blocked; previews approximate the supplied HTML rather than reproducing Brightspace exactly.

The service binds to 127.0.0.1 and reuses the authenticated Transfer Studio launcher. Uploads and downloads require the local session; uploads also check origin, host, request size, file name, type and revision. Interrupted file copies are removed, and downloads are streamed rather than buffered as a complete file. Upload folders persist locally so saved reviews can reopen. Original files remain intact.

**Still unverified:** exact live Brightspace appearance, post-import picture delivery and marks awarded to a submitted attempt. This tool does not modify a live course or claim that a candidate package is cleared for the real course.
