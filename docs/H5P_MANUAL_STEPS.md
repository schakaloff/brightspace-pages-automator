# Moving an H5P activity from Moodle to Brightspace — by hand

These are the same steps the tool performs, written out so you can do one
yourself, check the tool's work, or fix a single item that failed.

Doing one activity by hand takes about 3 minutes. You need teacher/editing
access in the Moodle course and instructor access in the Brightspace course.

There are three parts:

1. Download the activity from Moodle as a file.
2. Upload that file into the H5P library (this is a separate cloud service that
   Brightspace connects to).
3. Create a Brightspace page and place the activity on it.

---

## Part 1 — Download the activity from Moodle

Moodle hides the download button until you turn it on, so this is two steps.

1. Open the Moodle course and click the H5P activity you want.
2. On the activity page, open **Settings** (top of the page).
3. Scroll down and tick **Allow download**. It may be inside a collapsed
   section called *H5P options* or *Display options* — open the section if you
   don't see it.
4. Click **Save and display** at the bottom. You'll land back on the activity.
5. Scroll to the bottom of the activity itself. A small **Reuse** button appears
   in the bottom-left corner of the activity box.
   - If a *Data Reset* message pops up first, click **OK** and carry on.
   - If there's no Reuse button, the Allow download tick didn't save. Go back to
     Settings and check it again.
6. Click **Reuse**, then click **Download as an .h5p file**.
7. The file lands in your Downloads folder. Its name will be the activity's
   name with any punctuation removed — for example
   `Drag & Drop: Abbreviations` saves as `Drag  Drop Abbreviations.h5p`.
   That's normal. **Keep a note of the real name with punctuation** — you'll
   type it in later.

Repeat for each activity, or do them in a batch before moving on.

---

## Part 2 — Upload the file to the H5P library

The H5P library is reached from inside a Brightspace page editor. There's no
separate website to log into.

1. In Brightspace, open the course and go to **Content**.
2. Open the module (unit) where the activity belongs.
3. Click **Create New**, then choose **Page**.
4. In the page editor, click the **Insert Stuff** button in the toolbar.
   If the toolbar looks cut off, click the **⋯** (three dots) at the end of it
   to see the hidden buttons.
5. In the list of options that appears, choose **H5P**.
6. You now see the H5P library — a list of every activity already uploaded for
   the college.

   **Before uploading, search for the activity's name in the search box.**
   The list runs to several pages, so scrolling is not a reliable way to check.
   If it's already there, skip to Part 3.

7. Click **Add Content**.
8. Click the **Upload** tab, choose your `.h5p` file, then click **Use**.
9. **Type the activity's name in the title box.** This is the step people miss,
   and it matters more than it looks: the title is the only way to find the
   activity again. An untitled upload cannot be placed on a page and cannot be
   found later — it just sits in the library as clutter.

   Type the **real name including punctuation** (`Drag & Drop: Abbreviations`),
   not the filename version.

10. Click **Save**.

---

## Part 3 — Put the activity on a Brightspace page

Carry straight on from Part 2 — you're already in the right place.

1. Click the folder/back button to return to the library list.
2. Find your activity and click **Insert** on its row.

   Check the **whole name**, not just the start. Names like
   *Case Study - Question 1*, *Question 2&3*, and *Question 5* look identical
   until the last few characters, and picking the wrong one puts the wrong
   activity on a correctly-titled page — a mistake that's easy to make and hard
   to spot afterwards.

3. Click **Insert** again in the bottom corner of the dialog.
4. A box appears asking about a grade item. Click
   **Proceed Without Grade Item**.
5. Click **Insert** once more if the dialog is still open.
6. Type the page title at the top — use the activity's name, so the page is
   easy to match up later.
7. Click **Save and Close**.

The activity is now live on the page. Open the page as a student to confirm it
loads and is the right one.

---

## When something goes wrong

**"Missing main library H5P.ImagePair 1.4"** (or another library name) when you
click Use.
The activity uses a content type the college's H5P setup doesn't have
installed. You can't fix this yourself — ask an admin to add it under
**Brightspace Admin → H5P → Content Type Hub**, then redo Part 2 for that one
activity.

**The activity uploaded but you can't find it in the list.**
It probably saved without a title. Look for a blank row in the library, open
it, give it the proper name, and save.

**There's already a page with that name, but it's empty.**
Courses imported from Moodle bring across page names without their contents.
Delete the empty page first, then create yours — otherwise you end up with two
pages with the same name and no way to tell them apart in the list.

**You're not sure whether a page has a working activity on it.**
Open the page and look. An empty imported page shows the heading and nothing
else; a working one shows the activity itself.

---

## Doing a whole course

The tool exists because this adds up: a 40-activity course is roughly two hours
by hand, and the two easy mistakes above (untitled uploads, near-identical
names) get more likely the longer you go. If you're doing more than a handful,
run the tool and use these steps to spot-check its work or to finish off
anything it reported as failed.
