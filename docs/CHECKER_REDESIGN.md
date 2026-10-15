**Check & fix: calmer overview and focused review**

Approved visual: `C:/dev/brightspace-automator/checker-ux-review/checker-concept-v2.png`.

The main screen now shows a compact Moodle source → Brightspace destination summary and up to three finding groups. Broken file links, missing content and Moodle Books open focused views with relevant evidence and fix choices. Books are an optional conversion, not automatically an error. Findings with zero relevant items stay out of the overview; page order and dedicated media tools remain available in Other options. The sample link-review experiment has moved there too.

Choose fixes opens the first relevant group. Selections survive navigation between groups, with a count of selected fix types. Review selected fixes opens an in-app confirmation containing both full URLs and the selected operations. Applying requires a separate click. Individual page names are evidence, not a promise of per-page selection; existing file and uncertain-match prompts remain part of the run.

Both course URLs are validated beside their fields, including Moodle sections/enrolment URLs and the Brightspace formats supported by the checker. The compact summary labels section IDs as sections. Course fields and Change are disabled during a run. Editing either URL invalidates the report, confirmation and selections. Reports arriving for a different pair are discarded. Applying also invalidates the old report; a new scan is required afterward.

The action area sits outside the content scroll, remains reachable at smaller sizes and moves directly below short views at larger sizes. Finding rows are native buttons that work with keyboard activation. Visible field labels have buddies and accessible names. Theme colours and existing native icons are reused, with no new dependency.

Run completion is now emitted only after the worker and its browser cleanup finish. The result distinguishes finished, failed, stopped and early exits, displays the recorded file/link/activity outcomes where available, and asks for a verification scan. It does not infer that all selected work succeeded. Books/content/order do not expose a complete structured per-item outcome; activity details and a new scan remain necessary. Stopping may leave earlier changes applied.

The scan report now includes file-link coverage. A failed file-link check shows Not checked and prevents selecting repairs from that report, rather than appearing as zero broken links. Other findings can still be reviewed. Book names and sections are also carried into the focused view.

Main files:

- `src/panels/checker_workflow.py`: native controls and layouts; course ID/scope parsing.
- `src/panels/checker_review.py`: navigation, report display, selections, confirmation and result states.
- `src/panels/checker_panel.py`: existing worker, prompts and cancellation, connected to the review views.
- `src/panels/checker_styles.py`: scoped styles using existing theme tokens.
- `src/content_checker.py`: coverage metadata and explicit completion marker.

Safe visual preview: `.venv/Scripts/python.exe tools/preview_checker.py`. Captures use in-memory config, empty credential stubs, suppressed startup tasks and a disabled worker. Screenshots are saved under `test-results/checker-design/`. Nothing is scanned or applied. The screenshots include both themes, three full-app sizes, group views, confirmation, partial scan, simulated login wait and simulated outcomes.

Validation: 95 relevant tests passed, covering explicit apply dispatch, special Books/order routes, cross-group selections, URL invalidation, late reports, partial file-link coverage, section scope, input validation, keyboard activation, responsive reachability, wrapped result text, worker completion, cancellation, existing GUI/startup/sidebar/log behavior, file repairs, Books, activity links and earlier Settings/Collector work. No live authentication or real course writes were tested. No installed build or installer was produced.
