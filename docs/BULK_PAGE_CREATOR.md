# Create pages in bulk

Open **Build & style → Create pages in bulk**.

1. Paste a Brightspace course or section URL and click **Load sections**. The
   picker includes nested and empty sections. A section URL selects that section;
   a course URL asks you to choose one.
2. Click **Paste titles…** and enter one page title per line. Blank lines are
   ignored. You can add 20 or more pages in one batch.
3. Select a row to enter its content. Use **Plain text** for ordinary paragraphs
   or **HTML** for markup. Empty content creates a blank page. Double-click a
   title to rename it, or use the **Page title** field in the editor. Use the
   arrow buttons to reorder the draft. **Remove** removes a local draft row;
   **Clear all** empties the entire list, including completed rows. Pages already
   created in Brightspace stay in your course. Clearing also updates the saved draft.
4. Use **Preview** to review the selected page's content. This is a content
   preview; Brightspace's final appearance can differ.
5. New pages are hidden from students by default. Uncheck **Hide new pages from
   students** if you want them visible immediately.
6. Click **Create N pages**. Pages are added at the end of the selected section
   in the order shown. **Open section** opens that destination in your browser.

The app saves the draft locally with its settings when you close, start a batch,
and receive creation results. Completed rows show **Created** and are excluded
from later runs. The tool checks for duplicate titles within the batch and in
the destination before it starts. Rename or remove conflicting rows to continue.

**Stop** finishes the current request and stops before the next page. Remaining
rows stay editable. A failed or unverified request stops the batch and marks its
row **Review**. Open the section in Brightspace and check whether that page
exists before removing the row from the draft. If it was not created, add its
title and content again. Later pages have not been sent. Requests are never
automatically retried because a lost response can still have created a page.

After an interrupted app session, unconfirmed rows are also marked for review.
Closing the app during a running creation batch requires stopping it and waiting
for the current request, so confirmed results can be saved first.

This feature uses the saved Brightspace login and does not require an AI key.
Its authenticated HTML upload follows the existing `target_page_creator.py`
recipe and D2L's [Content API multipart upload documentation](https://docs.valence.desire2learn.com/res/content.html).

The editor and page list sit side by side in the roomier default window. At
smaller widths they stack vertically; scroll down to reach the editor and
creation controls. **Activity** expands the detailed log. Errors and warnings
open it automatically. A progress bar tracks verified pages during creation.
