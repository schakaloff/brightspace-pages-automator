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
cd "C:\Users\300353682\OneDrive - Okanagan College\Desktop\Page Automator\brightspace-pages-automator"
```
> If you're one level up in `Quiz automator`, git commands will fail or affect the wrong files.

---

## Branch Promotion Workflow: `nick → dev → main`

With a backup of `main` first.

### 1. Check where you are

```
git status
git branch --show-current
git remote -v
```

### 2. Fetch latest remote info

```
git fetch origin
```

### 3. Backup current main (recommended)

```
git branch backup-origin-main-2026-07-17 origin/main
git push origin backup-origin-main-2026-07-17
```

Change the date each time.

### 4. Make sure `nick` is current

```
git checkout nick
git pull origin nick
git push origin nick
```

### 5. Fast-forward `dev` to `nick`

```
git checkout dev
git pull origin dev
git merge --ff-only nick
git push origin dev
```

### 6. Fast-forward `main` to `dev`

```
git checkout main
git pull origin main
git merge --ff-only dev
git push origin main
```

### 7. Go back to working branch

```
git checkout nick
```

### If a fast-forward merge fails

```
git merge --ff-only ...
```

Stop there. It means the branches diverged — inspect before merging.