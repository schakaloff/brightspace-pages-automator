"""
Browser launch, session management, and login flow.
Logic copied exactly from brightspace-quiz-automator/src/browser.py — same params,
same session file path (shared), same login-wait loop.
"""
import os

from playwright.async_api import async_playwright, BrowserContext, Page

from config import SESSION_FILE


async def wait_for_login(page: Page, context: BrowserContext) -> None:
    """
    Navigate to Brightspace and wait for login if needed.
    If the saved session is still valid the loop exits in ~3 s without user action.
    Identical behaviour to quiz automator's _wait_for_login.
    """
    print("Opening Brightspace...")
    await page.goto("https://learn.okanagancollege.ca")
    print("─" * 50)
    print("  Log in with your Okanagan College account.")
    print("  Complete any MFA steps (email code, authenticator, etc.).")
    print("  Script continues automatically once you reach the home page.")
    print("─" * 50)
    for i in range(180):
        url = page.url
        if "learn.okanagancollege.ca" in url and "microsoftonline.com" not in url:
            try:
                has_login_form = await page.evaluate("() => !!document.querySelector('#userName')")
            except Exception:
                continue  # mid-navigation, keep waiting
            if has_login_form:
                continue
            try:
                await page.goto("https://learn.okanagancollege.ca/d2l/home", timeout=15000)
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                await page.wait_for_timeout(2000)
            except Exception:
                pass
            if "/d2l/home" in page.url:
                break
        await page.wait_for_timeout(3000)
        if i % 10 == 0 and i > 0:
            print(f"  Still waiting... ({i * 3}s)  |  {page.url[:80]}")
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
    )
    page = await context.new_page()
    return p, browser, context, page
