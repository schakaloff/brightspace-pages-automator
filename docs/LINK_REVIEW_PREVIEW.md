# Try link review

In Check & fix, click **Try new review**. You can also run
`.venv/Scripts/python.exe tools/preview_link_review.py --interactive`.

This is an interactive preview using sample links. It does not scan the supplied
URLs, upload files, call AI, or modify Moodle or Brightspace. Preview drafts stay
in memory; opening the regular app's preview again retains the current session,
but restarting the app resets it. The existing Checker remains available.

- Select individual rows, then **Review changes** for exact destinations and
  opening choices. **Apply to preview** updates sample data only.
- **Edit** opens a dialog for a replacement URL, matching Moodle file URL, or
  local file. Opening behaviour is independent: leave it, new tab, or same tab.
  Cancel leaves the draft unchanged.
- **Show working links** includes healthy links for opening changes. Selections
  survive hiding those rows, and the selected count reports hidden selections.
- **Add action** adds a specific link, shows working links, or resets the sample
  data so you can try the flow again. No-op selections
  and missing replacements must be resolved before review is enabled.
- **Change location** stores preview context for course, section or page scope.
  It does not load a real course or change which sample links are displayed.

Next stage: connect the reviewed flow to authenticated scanning, verified file
matching, exact per-link writes and read-back verification using a test course.
