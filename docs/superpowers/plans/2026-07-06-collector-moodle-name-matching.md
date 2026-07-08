# Unit Collector Moodle Name-Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix weird names on inserted files/links in the Unit Collector's assembled page by cross-referencing the original Moodle course and using the matched real name for the D2L "link text" field and link labels.

**Architecture:** A new `src/moodle_matcher.py` module holds the pure name-matching logic (testable without a browser) plus the Moodle login/scrape functions (adapted from `kaltura_categorizer.py`'s proven pattern, reusing `content_checker._JS_MOODLE_ITEMS` for scraping and the shared `MOODLE_SESSION_FILE`). `UnitCollector` gets an optional `moodle_url` param; when set, it builds a `label -> corrected_name` map before assembling sections and threads corrected names into file inserts and link labels. `CollectorPanel` gets one new optional text field.

**Tech Stack:** Python 3, Playwright (async), difflib, pytest + pytest-qt (existing test setup).

## Global Constraints

- Moodle URL is optional — empty means zero behavior change (verbatim from spec).
- Matching scope is whole-course, not section-scoped (verbatim from spec).
- Only file inserts and link items get corrected names — HTML topic `<h2>` headers are untouched (verbatim from spec).
- Match threshold: exact normalized match, else `difflib.get_close_matches(..., n=1, cutoff=0.6)` (verbatim from spec).
- Moodle login/scrape failures are non-fatal — log a warning and fall back to original labels for every item, continue the run (verbatim from spec).
- Reuse existing `MOODLE_SESSION_FILE` (`USERDATA_DIR / "moodle_session.json"`) and existing `MainWindow.moodle_username`/`moodle_password` — no new Settings UI (verbatim from spec).

---

### Task 1: Extract pure name-matching helper with tests

**Files:**
- Create: `src/moodle_matcher.py`
- Test: `tests/test_moodle_matcher.py`

**Interfaces:**
- Produces: `normalize_name(text: str) -> str`, `build_name_matcher(moodle_names: list[str]) -> Callable[[str], Optional[str]]` — the returned callable takes a Brightspace label and returns the corrected Moodle name (str) or `None` if no match met the cutoff.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_moodle_matcher.py
import sys
sys.path.insert(0, "src")

from moodle_matcher import normalize_name, build_name_matcher


def test_normalize_name_lowercases_and_unescapes_entities():
    assert normalize_name("Chapter 9 &amp; Review") == "chapter 9 & review"
    assert normalize_name("  Extra Space  ") == "extra space"


def test_build_name_matcher_exact_match():
    matcher = build_name_matcher(["Chapter 9 PowerPoint", "Lecture Notes"])
    assert matcher("chapter 9 powerpoint") == "Chapter 9 PowerPoint"


def test_build_name_matcher_fuzzy_match_above_cutoff():
    matcher = build_name_matcher(["Communicating Effectively PowerPoint"])
    # Brightspace label is a mangled/shortened variant of the Moodle name
    assert matcher("Communicating Effectively PPT") == "Communicating Effectively PowerPoint"


def test_build_name_matcher_no_match_below_cutoff():
    matcher = build_name_matcher(["Totally Unrelated Item"])
    assert matcher("Communicating Effectively PowerPoint") is None


def test_build_name_matcher_empty_names_list_never_matches():
    matcher = build_name_matcher([])
    assert matcher("Anything") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/test_moodle_matcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'moodle_matcher'`

- [ ] **Step 3: Write the implementation**

```python
# src/moodle_matcher.py
import difflib
import html as html_module
from typing import Callable, Optional


def normalize_name(text: str) -> str:
    """Lowercase + decode HTML entities so &amp; == & in comparisons."""
    return html_module.unescape(text).lower().strip()


def build_name_matcher(moodle_names: list) -> Callable[[str], Optional[str]]:
    """Return a function that maps a Brightspace label to its corrected
    Moodle name, or None if nothing matched closely enough.

    Exact normalized match wins outright; otherwise falls back to a fuzzy
    match with a 0.6 similarity cutoff (difflib.get_close_matches).
    """
    norm_to_original = {}
    for name in moodle_names:
        norm_to_original[normalize_name(name)] = name
    norm_keys = list(norm_to_original.keys())

    def matcher(label: str) -> Optional[str]:
        norm_label = normalize_name(label)
        if norm_label in norm_to_original:
            return norm_to_original[norm_label]
        if not norm_keys:
            return None
        close = difflib.get_close_matches(norm_label, norm_keys, n=1, cutoff=0.6)
        if close:
            return norm_to_original[close[0]]
        return None

    return matcher
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/test_moodle_matcher.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/moodle_matcher.py tests/test_moodle_matcher.py
git commit -m "$(cat <<'EOF'
Add pure name-matching helper for Moodle/Brightspace label reconciliation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add Moodle login + scrape functions

**Files:**
- Modify: `src/moodle_matcher.py`

**Interfaces:**
- Consumes: `content_checker._JS_MOODLE_ITEMS` (existing JS string constant, returns `[{type, name, href, hint}, ...]` when evaluated on a Moodle course page — already used by `ContentChecker`).
- Consumes: `kaltura_categorizer.MOODLE_SESSION_FILE` (existing constant path).
- Produces: `async def ensure_moodle_session(moodle_username: str, moodle_password: str, log_fn=None) -> None` — logs in (auto or manual-wait) if no valid session exists, saves to `MOODLE_SESSION_FILE`. Raises `RuntimeError` on failure.
- Produces: `async def scrape_moodle_names(moodle_course_url: str, log_fn=None) -> list` — returns flat list of item name strings (empty list on failure, never raises).

This task has no automated test (Playwright browser interaction — matches this codebase's existing convention: `kaltura_categorizer.py` and `content_checker.py` have no unit tests for their browser-driving methods either). Verification is manual, folded into Task 5's end-to-end check.

- [ ] **Step 1: Add the two functions to `src/moodle_matcher.py`**

```python
# Append to src/moodle_matcher.py

import os
import sys
from playwright.async_api import async_playwright

from kaltura_categorizer import MOODLE_SESSION_FILE

MANUAL_LOGIN_URL = "https://mymoodle.okanagan.bc.ca/login/index.php?saml=off"


def _log(msg, tag="dim", log_fn=None):
    if log_fn:
        log_fn(msg, tag)
    print(f"[moodle matcher] {msg}", file=sys.stderr)


async def ensure_moodle_session(moodle_username: str = "", moodle_password: str = "", log_fn=None) -> None:
    """Log in to Moodle (manual or automatic) and save the session to
    MOODLE_SESSION_FILE, exactly mirroring kaltura_categorizer.login_to_moodle.
    """
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=False, slow_mo=50)
    try:
        context = await browser.new_context()
        page = await context.new_page()

        _log("Opening Moodle manual login…", log_fn=log_fn)
        try:
            await page.goto(MANUAL_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)

        logout_btn = page.locator('button:has-text("Log out")')
        if await logout_btn.count() > 0:
            _log("Clearing existing SSO session…", log_fn=log_fn)
            await logout_btn.first.click()
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)
            await page.goto(MANUAL_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)

        if "loginredirect" in page.url:
            _log("Clearing stale session (loginredirect)…", log_fn=log_fn)
            try:
                logout_btn2 = page.locator('button[type="submit"].btn-primary')
                if await logout_btn2.count() > 0:
                    await logout_btn2.first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    await page.wait_for_timeout(2000)
            except Exception:
                pass
            await page.goto(MANUAL_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)

        if moodle_username and moodle_password:
            _log(f"Filling credentials for {moodle_username}…", log_fn=log_fn)
            await page.evaluate("""([u, p]) => {
                const set = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                const uEl = document.querySelector('#username');
                const pEl = document.querySelector('#password');
                set.call(uEl, u); uEl.dispatchEvent(new Event('input', {bubbles:true}));
                set.call(pEl, p); pEl.dispatchEvent(new Event('input', {bubbles:true}));
                document.querySelector('#loginbtn').click();
            }""", [moodle_username, moodle_password])
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
            if "login" in page.url.lower():
                raise RuntimeError("Login failed — check Moodle credentials in Settings")
            _log("Logged in successfully.", "success", log_fn=log_fn)
        else:
            _log("No credentials set — log in manually in the browser.", "warning", log_fn=log_fn)
            for i in range(120):
                await page.wait_for_timeout(3000)
                if "login" not in page.url.lower():
                    _log("Login detected.", "success", log_fn=log_fn)
                    await page.wait_for_timeout(1500)
                    break
                if i % 10 == 9:
                    _log(f"Waiting for manual login… ({(i + 1) * 3}s)", log_fn=log_fn)
            else:
                raise RuntimeError("Moodle login timed out after 6 minutes")

        await context.storage_state(path=MOODLE_SESSION_FILE)
        _log("Moodle session saved.", "success", log_fn=log_fn)
    finally:
        try:
            await browser.close()
        except Exception:
            pass
        await p.stop()


async def scrape_moodle_names(moodle_course_url: str, log_fn=None) -> list:
    """Scrape every activity/item name in a Moodle course (all sections).
    Returns [] on any failure — callers must treat this as non-fatal.
    """
    from content_checker import _JS_MOODLE_ITEMS

    if not os.path.exists(MOODLE_SESSION_FILE):
        _log("No Moodle session on disk — run ensure_moodle_session first", "warning", log_fn=log_fn)
        return []

    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    try:
        context = await browser.new_context(storage_state=MOODLE_SESSION_FILE)
        page = await context.new_page()
        try:
            await page.goto(moodle_course_url, wait_until="networkidle", timeout=30000)
        except Exception:
            pass

        if "mymoodle.okanagan.bc.ca" not in page.url:
            _log(f"Redirected off Moodle ({page.url[:80]}) — session expired", "warning", log_fn=log_fn)
            return []

        items = await page.evaluate(_JS_MOODLE_ITEMS)
        names = [i["name"] for i in (items or []) if i.get("type") != "SECTION" and i.get("name")]
        _log(f"Scraped {len(names)} Moodle item name(s)", "success", log_fn=log_fn)
        return names
    except Exception as e:
        _log(f"Moodle scrape failed: {e}", "warning", log_fn=log_fn)
        return []
    finally:
        try:
            await browser.close()
        except Exception:
            pass
        await p.stop()
```

- [ ] **Step 2: Sanity-check imports resolve**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); import moodle_matcher"`
Expected: no output, exit code 0 (import succeeds)

- [ ] **Step 3: Re-run Task 1's tests to confirm nothing broke**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/test_moodle_matcher.py -v`
Expected: PASS (5 passed)

- [ ] **Step 4: Commit**

```bash
git add src/moodle_matcher.py
git commit -m "$(cat <<'EOF'
Add Moodle login/scrape functions to moodle_matcher

Reuses kaltura_categorizer's login pattern and content_checker's
Moodle item scraper so Unit Collector can build a name-correction map.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Wire moodle_url through UnitCollector — build match map + fix link labels

**Files:**
- Modify: `src/unit_collector.py:47-81` (`__init__`)
- Modify: `src/unit_collector.py:1049-1167` (`run()`)
- Modify: `src/unit_collector.py:1170-1199` (module-level `run()` wrapper)

**Interfaces:**
- Consumes: `moodle_matcher.ensure_moodle_session`, `moodle_matcher.scrape_moodle_names`, `moodle_matcher.build_name_matcher` (from Task 1/2).
- Produces: `self._name_matcher: Callable[[str], Optional[str]]` attribute on `UnitCollector`, set in `run()` before topic scraping begins. Defaults to a no-op matcher (`lambda label: None`) when `moodle_url` is empty or the scrape fails.

- [ ] **Step 1: Add `moodle_url`, `moodle_username`, `moodle_password` params to `__init__`**

In `src/unit_collector.py`, modify the `__init__` signature (currently ending at line 62-63 with `sso_email`, `sso_password`):

```python
    def __init__(
        self,
        unit_url: str,
        target_url: str,
        theme_name: str,
        theme_colors: dict,
        gemini_api_key: str = "",
        style_reference_html: str = "",
        parallel_pages: int = 3,
        log: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
        bs_username: str = "",
        bs_password: str = "",
        sso_email: str = "",
        sso_password: str = "",
        moodle_url: str = "",
        moodle_username: str = "",
        moodle_password: str = "",
    ):
        self.unit_url = unit_url
        self.target_url = target_url
        self.theme_name = theme_name
        self.theme_colors = theme_colors
        self.gemini_api_key = gemini_api_key
        self.style_reference_html = style_reference_html
        self.parallel_pages = max(1, parallel_pages)
        self._log_fn = log
        self._on_complete = on_complete
        self.bs_username = bs_username
        self.bs_password = bs_password
        self.sso_email = sso_email
        self.sso_password = sso_password
        self.moodle_url = moodle_url.strip()
        self.moodle_username = moodle_username
        self.moodle_password = moodle_password
        self._name_matcher = lambda label: None
        self._clipboard_lock = asyncio.Lock()
        self._link_lock = asyncio.Lock()
        self._dl_dir = Path(tempfile.gettempdir()) / "brightspace_collector"
        self._dl_dir.mkdir(exist_ok=True)
```

- [ ] **Step 2: Add a `_build_name_matcher` method**

Insert this new method right after `__init__` in `src/unit_collector.py` (before `def log`):

```python
    async def _build_name_matcher(self) -> None:
        """Populate self._name_matcher from the Moodle course, if configured.
        Non-fatal on any failure — falls back to a no-op matcher."""
        if not self.moodle_url:
            return
        try:
            import os
            from moodle_matcher import (
                ensure_moodle_session, scrape_moodle_names, build_name_matcher,
                MOODLE_SESSION_FILE,
            )
            if not os.path.exists(MOODLE_SESSION_FILE):
                await ensure_moodle_session(
                    self.moodle_username, self.moodle_password, log_fn=self.log
                )
            names = await scrape_moodle_names(self.moodle_url, log_fn=self.log)
            if not names:
                self.log("⚠ No Moodle names scraped — using Brightspace labels as-is", "warning")
                return
            self._name_matcher = build_name_matcher(names)
            self.log(f"✓ Moodle name matcher ready ({len(names)} item(s))", "success")
        except Exception as e:
            self.log(f"⚠ Moodle matching unavailable: {e} — using Brightspace labels as-is", "warning")
```

Note: `build_name_matcher` doesn't need `MOODLE_SESSION_FILE` — only `ensure_moodle_session`'s existence check does. Import it from `moodle_matcher` alongside the others (it's re-exported there via the `from kaltura_categorizer import MOODLE_SESSION_FILE` line added in Task 2).

- [ ] **Step 3: Call `_build_name_matcher` in `run()` and use it for link labels**

In `src/unit_collector.py`, inside `run()`, right after the topics list is scraped and filtered (after the line `topics = [t for t in topics if t["url"].rstrip("/") != target_path]` and its following `if not topics:` block, i.e. right before the `# ── Phase 1: scrape all topics in parallel ──` comment), add:

```python
            await self._build_name_matcher()

```

Then, in Phase 2's assembly loop, find this existing block:

```python
                elif result["link_url"]:
                    sections.append(
                        f'<p><strong>{safe}:</strong> '
                        f'<a href="{result["link_url"]}">{result["link_url"]}</a></p>\n'
                    )
                    link_count += 1
```

Replace it with:

```python
                elif result["link_url"]:
                    corrected = self._name_matcher(topic["label"])
                    link_label = (corrected or topic["label"]).replace("<", "&lt;").replace(">", "&gt;")
                    sections.append(
                        f'<p><strong>{link_label}:</strong> '
                        f'<a href="{result["link_url"]}">{result["link_url"]}</a></p>\n'
                    )
                    link_count += 1
```

- [ ] **Step 4: Add the same params to the module-level `run()` wrapper**

In `src/unit_collector.py`, modify the trailing module-level `async def run(...)` function to accept and forward the three new params:

```python
async def run(
    unit_url: str,
    target_url: str,
    theme_name: str,
    theme_colors: dict,
    gemini_api_key: str = "",
    style_reference_html: str = "",
    parallel_pages: int = 3,
    log: Callable = None,
    on_complete: Callable = None,
    bs_username: str = "",
    bs_password: str = "",
    sso_email: str = "",
    sso_password: str = "",
    moodle_url: str = "",
    moodle_username: str = "",
    moodle_password: str = "",
) -> None:
    await UnitCollector(
        unit_url=unit_url,
        target_url=target_url,
        theme_name=theme_name,
        theme_colors=theme_colors,
        gemini_api_key=gemini_api_key,
        style_reference_html=style_reference_html,
        parallel_pages=parallel_pages,
        log=log,
        on_complete=on_complete,
        bs_username=bs_username,
        bs_password=bs_password,
        sso_email=sso_email,
        sso_password=sso_password,
        moodle_url=moodle_url,
        moodle_username=moodle_username,
        moodle_password=moodle_password,
    ).run()
```

- [ ] **Step 5: Verify the module still imports cleanly**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); import unit_collector"`
Expected: no output, exit code 0

- [ ] **Step 6: Re-run existing test suite to confirm no regressions**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/ -v`
Expected: all tests PASS (same count as before this task, plus Task 1's 5 new ones)

- [ ] **Step 7: Commit**

```bash
git add src/unit_collector.py
git commit -m "$(cat <<'EOF'
Wire optional Moodle name matching into UnitCollector

Builds a label-correction map from the Moodle course (when a URL is
given) and uses it for link-item labels. File-insert wiring follows
in the next commit.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Fill the `#z_k` link-text field on file insert

**Files:**
- Modify: `src/unit_collector.py:606-878` (`_insert_file`)
- Modify: `src/unit_collector.py:938-983` (`_scrape_topic` — thread the corrected name into the file item dict)

**Interfaces:**
- Consumes: `self._name_matcher` (from Task 3).
- Produces: `_insert_file(self, page, file_item)` now reads an optional `file_item["corrected_name"]` key and, if present, fills `#z_k` with it (extension stripped) before the final Insert click.

- [ ] **Step 1: Thread `corrected_name` onto file items in `_scrape_topic`**

In `src/unit_collector.py`, inside `_scrape_topic`, find:

```python
                    if fd:
                        if fd.get("filename", "").lower().endswith(".html.zip"):
                            extracted = self._html_from_zip(fd["path"])
                            if extracted:
                                result["html"] = extracted
                            else:
                                result["file"] = fd
                        else:
                            result["file"] = fd
```

Replace with:

```python
                    if fd:
                        if fd.get("filename", "").lower().endswith(".html.zip"):
                            extracted = self._html_from_zip(fd["path"])
                            if extracted:
                                result["html"] = extracted
                            else:
                                fd["corrected_name"] = self._name_matcher(label)
                                result["file"] = fd
                        else:
                            fd["corrected_name"] = self._name_matcher(label)
                            result["file"] = fd
```

- [ ] **Step 2: Fill `#z_k` in `_insert_file` before the final Insert click**

In `src/unit_collector.py`, inside `_insert_file`, find the comment block right before Step 6 (`# Step 6: Wait for "Insert" button...`):

```python
            # Step 6: Wait for "Insert" button (appears after upload completes) and click it
```

Insert a new step immediately before it:

```python
            # Step 5c: Fill the link-text field with the corrected name (if we have one)
            # so Brightspace shows a readable title instead of the raw file path.
            corrected = file_item.get("corrected_name")
            if corrected:
                display_name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", corrected)
                _JS_FILL_ZK = """(name) => {
                    const el = document.querySelector('#z_k');
                    if (!el) return false;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, name);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }"""
                filled = False
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        if await frame.evaluate(_JS_FILL_ZK, display_name):
                            filled = True
                            break
                    except Exception:
                        pass
                if filled:
                    self.log(f"  ✓ Set link text: {display_name}", "dim")
                else:
                    self.log(f"  ⚠ #z_k field not found — link text left as default", "dim")

            # Step 6: Wait for "Insert" button (appears after upload completes) and click it
```

- [ ] **Step 3: Add the `re` import if not already present**

Check the top of `src/unit_collector.py` for an existing `import re`. If absent, add it next to the existing imports:

```python
import re
```

Run: `grep -n "^import re" /home/apelsinchik/antigravity/brightspace-page-automator/src/unit_collector.py`
Expected: either already present, or add it under the existing `import asyncio` / `import tempfile` block at the top of the file.

- [ ] **Step 4: Verify the module still imports cleanly**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); import unit_collector"`
Expected: no output, exit code 0

- [ ] **Step 5: Re-run test suite**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/ -v`
Expected: all PASS, same count as Task 3

- [ ] **Step 6: Commit**

```bash
git add src/unit_collector.py
git commit -m "$(cat <<'EOF'
Fill D2L link-text field with corrected name on file insert

Insert Stuff's #z_k field defaults to the raw file path when left
blank. When a Moodle match was found, fill it with the readable name
before clicking Insert.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Add Moodle URL field to Collector GUI panel

**Files:**
- Modify: `src/panels/collector_panel.py`
- Test: `tests/test_gui_panels.py`

**Interfaces:**
- Consumes: `UnitCollector`/`run()`'s new `moodle_url`, `moodle_username`, `moodle_password` params (Task 3); `MainWindow.moodle_username`/`moodle_password` (already exist, per `gui.py:148-153`).
- Produces: `CollectorPanel._moodle_entry: QLineEdit` — new widget, readable via `.text()`.

- [ ] **Step 1: Write the failing test**

In `tests/test_gui_panels.py`, add:

```python
def test_collector_panel_has_moodle_url_field(qtbot):
    from unittest.mock import MagicMock
    from gui_panels import CollectorPanel
    mw = MagicMock(); mw.chromium_ready = False; mw.load_config.return_value = {}
    panel = CollectorPanel(mw); qtbot.addWidget(panel)
    assert panel._moodle_entry.text() == ""
    panel._moodle_entry.setText("https://mymoodle.okanagan.bc.ca/course/view.php?id=123")
    assert panel._moodle_entry.text() == "https://mymoodle.okanagan.bc.ca/course/view.php?id=123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/test_gui_panels.py::test_collector_panel_has_moodle_url_field -v`
Expected: FAIL with `AttributeError: 'CollectorPanel' object has no attribute '_moodle_entry'`

- [ ] **Step 3: Add the field in `_build()`**

In `src/panels/collector_panel.py`, find:

```python
        layout.addWidget(_form_label("TARGET PAGE URL  (empty Brightspace page you created)"))
        layout.addSpacing(4)
        self._target_entry = QLineEdit()
        self._target_entry.setPlaceholderText("https://learn.okanagancollege.ca/d2l/le/content/…/topics/…/View")
        self._target_entry.setFixedHeight(40)
        layout.addWidget(self._target_entry)
        layout.addSpacing(12)
```

Insert a new field right after it (before the `par_row` block):

```python
        layout.addWidget(_form_label("MOODLE COURSE URL  (optional — fixes weird file/link names)"))
        layout.addSpacing(4)
        self._moodle_entry = QLineEdit()
        self._moodle_entry.setPlaceholderText("https://mymoodle.okanagan.bc.ca/course/view.php?id=…")
        self._moodle_entry.setFixedHeight(40)
        layout.addWidget(self._moodle_entry)
        layout.addSpacing(12)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/test_gui_panels.py::test_collector_panel_has_moodle_url_field -v`
Expected: PASS

- [ ] **Step 5: Pass the field's value through `_start_run`**

In `src/panels/collector_panel.py`, in `_start_run`, find:

```python
        unit_url   = self._unit_entry.text().strip()
        target_url = self._target_entry.text().strip()
```

Replace with:

```python
        unit_url   = self._unit_entry.text().strip()
        target_url = self._target_entry.text().strip()
        moodle_url = self._moodle_entry.text().strip()
```

Then find the `collector_run(...)` call inside `worker()`:

```python
                asyncio.run(collector_run(
                    unit_url=unit_url,
                    target_url=target_url,
                    theme_name=theme_name,
                    theme_colors=theme_colors,
                    gemini_api_key=self._mw.gemini_api_key,
                    style_reference_html=style_reference_html,
                    parallel_pages=parallel,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    bs_username=self._mw.bs_username,
                    bs_password=self._mw.bs_password,
                    sso_email=self._mw.sso_email,
                    sso_password=self._mw.sso_password,
                ))
```

Replace with:

```python
                asyncio.run(collector_run(
                    unit_url=unit_url,
                    target_url=target_url,
                    theme_name=theme_name,
                    theme_colors=theme_colors,
                    gemini_api_key=self._mw.gemini_api_key,
                    style_reference_html=style_reference_html,
                    parallel_pages=parallel,
                    log=lambda msg, tag="info": q.put((msg, tag)),
                    on_complete=on_done,
                    bs_username=self._mw.bs_username,
                    bs_password=self._mw.bs_password,
                    sso_email=self._mw.sso_email,
                    sso_password=self._mw.sso_password,
                    moodle_url=moodle_url,
                    moodle_username=self._mw.moodle_username,
                    moodle_password=self._mw.moodle_password,
                ))
```

- [ ] **Step 6: Run the full GUI test file to confirm no regressions**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/test_gui_panels.py -v`
Expected: all PASS, including the new test

- [ ] **Step 7: Commit**

```bash
git add src/panels/collector_panel.py tests/test_gui_panels.py
git commit -m "$(cat <<'EOF'
Add optional Moodle Course URL field to Unit Collector panel

Wires the field through to UnitCollector's new moodle_url param;
leaving it blank preserves the existing behavior exactly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: End-to-end manual verification

**Files:** None (manual verification only — no code changes)

- [ ] **Step 1: Run the full automated test suite one more time**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && .venv/bin/pytest tests/ -v`
Expected: all PASS

- [ ] **Step 2: Launch the app**

Run: `cd /home/apelsinchik/antigravity/brightspace-page-automator && source .venv/bin/activate && python gui.py`

- [ ] **Step 3: Manual check — Moodle URL left blank**

Go to the Unit Collector tab, leave "MOODLE COURSE URL" blank, run a Collect & Assemble against a real unit with at least one file topic. Confirm behavior is unchanged from before this feature (file link text still whatever it was previously — no regression, no crash from the new blank-moodle-url path).

- [ ] **Step 4: Manual check — Moodle URL filled in**

Fill in "MOODLE COURSE URL" with the Moodle course that the unit was migrated from. Run Collect & Assemble again against a unit containing at least one file topic and one link topic whose Brightspace label looks mangled (e.g. contains the raw filename or path fragments).

Confirm:
- If no Moodle session cached yet, a visible browser opens to Moodle login; log in manually; the collector continues automatically afterward.
- Log shows `✓ Moodle name matcher ready (N item(s))`.
- The assembled target page's inserted file items show the corrected human-readable name as their link text (inspect via Source Code editor — the `<a>` text should no longer be the raw `/content/enforced/.../filename.ext` path).
- The assembled page's link-item labels (the `<strong>` text before each external link) show the corrected name where a match was found.

- [ ] **Step 5: Manual check — Moodle scrape failure is non-fatal**

Repeat with a deliberately wrong Moodle URL (e.g. a 404 course id). Confirm the log shows a warning (`⚠ Moodle matching unavailable...` or `⚠ No Moodle names scraped...`) and the rest of the run completes normally using original Brightspace labels — no exception, no partial/crashed run.

No commit for this task (verification only).
