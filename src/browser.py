"""
Browser launch, session management, and login flow.
Logic copied exactly from brightspace-quiz-automator/src/browser.py — same params,
same session file path (shared), same login-wait loop.
"""
import os

from playwright.async_api import async_playwright, BrowserContext, Page

from config import SESSION_FILE


async def _do_auto_login(page: Page, username: str, password: str) -> bool:
    """
    Attempt to fill and submit the Manual Login form on the D2L login page.
    Returns True if the form was submitted, False if the page wasn't a login page.
    """
    try:
        await page.goto("https://learn.okanagancollege.ca/d2l/login", timeout=15000)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        # Expand the Manual Login accordion
        accordion = page.locator(".d2l-collapsible-panel-header-primary")
        await accordion.wait_for(state="visible", timeout=8000)
        await accordion.click()
        await page.wait_for_timeout(600)
        # Fill credentials
        await page.get_by_label("Username").fill(username)
        await page.get_by_label("Password").fill(password)
        await page.get_by_role("button", name="Log In").click()
        return True
    except Exception as e:
        print(f"  Auto-login attempt failed: {e}")
        return False


async def wait_for_login(
    page: Page,
    context: BrowserContext,
    username: str | None = None,
    password: str | None = None,
) -> None:
    """
    Navigate to Brightspace and wait for login if needed.
    If username/password are provided, auto-fills the Manual Login form.
    If the saved session is still valid the loop exits in ~3 s without user action.
    """
    print("Opening Brightspace...")
    if username and password:
        print("  Attempting auto-login with saved credentials...")
        await _do_auto_login(page, username, password)
    else:
        await page.goto("https://learn.okanagancollege.ca")
        print("─" * 50)
        print("  Log in with your Okanagan College account.")
        print("  Complete any MFA steps (email code, authenticator, etc.).")
        print("  Script continues automatically once you reach the home page.")
        print("─" * 50)
    for i in range(180):
        await page.wait_for_timeout(3000)   # always wait — no path can skip this
        url = page.url
        if i % 10 == 9:
            print(f"  Still waiting... ({(i + 1) * 3}s)  |  {url[:80]}")
        if "microsoftonline.com" in url or "learn.okanagancollege.ca" not in url:
            continue
        try:
            has_login_form = await page.evaluate("() => !!document.querySelector('#userName')")
        except Exception:
            continue  # mid-navigation
        if has_login_form:
            continue
        # No login form — try navigating to home to confirm session is live
        try:
            await page.goto("https://learn.okanagancollege.ca/d2l/home", timeout=15000)
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            await page.wait_for_timeout(2000)
        except Exception:
            continue
        if "/d2l/home" in page.url:
            break
    else:
        raise RuntimeError("Login timed out after 9 minutes")
    print("✓ Logged in — saving session...")
    await page.wait_for_load_state("networkidle", timeout=20000)
    await context.storage_state(path=SESSION_FILE)
    print("✓ Session saved")


async def launch_browser():
    """
    Start Playwright and launch Chromium with saved session (if any).
    Returns (playwright_instance, browser, context, page).
    """
    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=False,
        slow_mo=80,
        args=["--start-maximized"],
    )
    context = await browser.new_context(
        storage_state=SESSION_FILE if os.path.exists(SESSION_FILE) else None,
        no_viewport=True,
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = await context.new_page()
    return p, browser, context, page
