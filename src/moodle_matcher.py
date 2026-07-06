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
