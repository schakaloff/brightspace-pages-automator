# Useful Commands

## Running the App

```powershell
# Launch the GUI (normal use)
.\run.bat

# Or directly
py gui.py
```

---

## Git — Step 0: Make sure you're in the right folder

```powershell
cd brightspace-pages-automator
```
> If you're one level up in `Quiz automator`, git commands will fail or affect the wrong files.

---

# ---------- STEP 1: Get your branch up to date before starting work ----------

git checkout dev
# "checkout" = switch to a different branch.
# This moves you onto the "dev" branch so you can update it.

git pull
# Downloads the latest commits from GitHub AND applies them to your files.
# Since you're on "dev" right now, this updates YOUR dev branch to match GitHub's dev branch.

git checkout nick
# Switch back onto your own branch (replace "nick" with your actual branch name).

git merge dev
# Takes whatever new changes just got pulled into "dev"
# and combines them into your "nick" branch.
# This keeps your branch from falling too far behind everyone else's work.


# ---------- STEP 2: Do your actual coding, then save your changes to git ----------

git add -u
# Stages your changes (tells git "these are the edits I want to save in my next commit").
# -u means: only files that already existed before (modified/deleted), skips brand new files.

git commit -m "add logging to quiz automator"
# Actually saves those staged changes as a labeled checkpoint in git's history.
# The text in quotes is just a short description of what you did — for you and others later.


# ---------- STEP 3: Send your work up to GitHub so it's backed up ----------

git push origin nick
# Uploads your commits from your computer up to GitHub, onto the "nick" branch there.
# "origin" = the nickname git uses for "the GitHub repo" (this is automatic, you don't set it up).

git push -u origin nick
# Same as above, but ONLY needed the very first time you push this branch.
# -u tells git "remember this connection" so next time you can just type "git push" with no extra info.


# ---------- STEP 4: When your feature is done, get it into "dev" ----------
# (Usually done as a Pull Request on GitHub's website instead of these commands —
#  a PR just means "hey team, review my changes before they go into dev".
#  But if doing it manually/locally, it looks like:)

git checkout dev
# Switch onto the "dev" branch.

git pull
# Make sure your local "dev" has the absolute latest from GitHub first.

git merge nick
# Bring all of YOUR branch's changes into "dev" now.

git push
# Upload the newly-updated "dev" branch back to GitHub for everyone else to see.