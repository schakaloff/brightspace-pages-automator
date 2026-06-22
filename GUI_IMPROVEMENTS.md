# GUI Improvement Ideas

Captured from design discussion — implement when ready.

---

## 1. Step interconnection (tabs feel like a workflow)

**Goal:** Users shouldn't be able to run tabs out of order accidentally.

- [ ] **Number the tabs** — `1 · Checker`, `2 · Unit Collector`, `3 · Page Changer`
- [ ] **Lock tabs 2 and 3** until Checker has completed at least once this session  
      (grey them out, tooltip: "Run Checker first")
- [ ] **"→ Next Step" button** at the bottom of each tab's log area when the run finishes successfully  
      — jumps the user to the next tab automatically
- [ ] **Status badges on tabs** — small ✓ appears on the tab header once that phase is done

---

## 2. Key pause points (most important — do not automate these away)

| # | Where | Why it must pause |
|---|---|---|
| 1 | Moodle course page | User must confirm they're on the right course before scraping |
| 2 | H5P instructor role | If role switch failed silently, all H5P downloads will fail with no error |
| 3 | File checklist | Some files may be intentionally absent — user picks what to include |
| 4 | Phase A → Phase B | H5P is in cloud but not Brightspace yet — verify uploads before embedding |
| 5 | Before Unit Collector | Collector overwrites the target page — content must be fully correct first |

---

## 3. Downloads folder visibility

**Done (v0.8.x):** Guide tab shows the downloads path and an "Open" button.

**Could also add:**
- [ ] Show path + Open button directly in the Checker tab log area after the run completes
- [ ] Show file/H5P counts in the Guide tab (live, not just the path)

---

## 4. Visual guide

**Done (v0.8.x):**
- `WORKFLOW_GUIDE.html` — standalone shareable HTML flowchart (OC branded)
- Guide tab in app — 3 step cards + button to open the HTML guide in browser

**Could improve:**
- [ ] Auto-open the Guide tab on first launch (new users land on the guide, not Checker)
- [ ] Add a "What's New" section to the guide that matches CHANGELOG.md
