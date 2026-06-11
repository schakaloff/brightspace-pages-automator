"""
Page automator — hardcoded sequence:
  1. Navigate to the topic URL
  2. Find the smart-curriculum iframe → click Options → click Edit
  3. Wait for the edit page to load
  4. Find d2l-htmleditor-button[cmd="d2l-source-code"] → click it
  5. Signal on_complete() so the GUI re-enables immediately
  6. Keep browser open until the user closes it
"""
import asyncio
from typing import Callable

from playwright.async_api import Page


async def _find_locator_any_frame(page: Page, selector: str, retries: int = 6, delay_ms: int = 700):
    """
    Search for a CSS selector across the main page and all iframes.
    Retries up to `retries` times with `delay_ms` between attempts.
    Returns (frame_or_page, locator) or (None, None).
    """
    for _ in range(max(retries, 1)):
        for ctx in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                loc = ctx.locator(selector)
                if await loc.count() > 0:
                    return ctx, loc
            except Exception:
                pass
        if _ < retries - 1:
            await page.wait_for_timeout(delay_ms)
    return None, None


async def run(
    url: str,
    log: Callable[[str, str], None],
    on_complete: Callable = None,
) -> None:
    """
    Full automation: navigate → Options → Edit → Source Code.

    on_complete() is called after Source Code is clicked so the GUI re-enables
    immediately. The browser stays open in the background.
    """
    from browser import launch_browser, wait_for_login

    p, browser, context, page = await launch_browser()
    try:
        await wait_for_login(page, context)

        # ── Navigate ──────────────────────────────────────────────────────────
        log("─" * 52, "dim")
        log("Navigating to:", "info")
        log(f"  {url}", "step")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        log("✓ Page loaded", "success")

        # ── Step 1: Click Options ─────────────────────────────────────────────
        log("─" * 52, "dim")
        log("Looking for Options button...", "info")
        try:
            await page.wait_for_selector('iframe', timeout=5000)
        except Exception:
            pass

        _, btn = await _find_locator_any_frame(
            page, 'd2l-button-icon.content-options-btn', retries=7
        )
        if btn is None:
            log("✗ Options button not found", "error")
            log("  Frames on page:", "dim")
            for f in page.frames:
                log(f"    {f.url[:90]}", "dim")
            if on_complete:
                on_complete()
            while browser.is_connected():
                await asyncio.sleep(0.5)
            return

        await btn.first.scroll_into_view_if_needed()
        await btn.first.click()
        log("✓ Options menu opened", "success")

        # ── Step 2: Click Edit ────────────────────────────────────────────────
        log("─" * 52, "dim")
        log("Waiting for Edit menu item...", "info")

        edit_frame, edit_btn = await _find_locator_any_frame(
            page, 'd2l-menu-item#optEdit', retries=8, delay_ms=500
        )
        if edit_btn is None:
            log("✗ Edit menu item not found", "error")
            if on_complete:
                on_complete()
            while browser.is_connected():
                await asyncio.sleep(0.5)
            return

        await edit_btn.first.wait_for(state="visible", timeout=4000)
        await edit_btn.first.click()
        log("✓ Edit clicked — waiting for edit page...", "success")

        # ── Step 3: Wait for edit page ────────────────────────────────────────
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(800)

        # ── Step 4: Click Source Code ─────────────────────────────────────────
        log("─" * 52, "dim")
        log("Looking for Source Code button...", "info")

        _, src_btn = await _find_locator_any_frame(
            page, 'd2l-htmleditor-button[cmd="d2l-source-code"]', retries=8, delay_ms=700
        )
        if src_btn is None:
            log("✗ Source Code button not found", "error")
            if on_complete:
                on_complete()
            while browser.is_connected():
                await asyncio.sleep(0.5)
            return

        await src_btn.first.scroll_into_view_if_needed()
        await src_btn.first.click()
        log("✓ Source Code clicked", "success")

        # ── Done ──────────────────────────────────────────────────────────────
        log("─" * 52, "dim")
        log("✓  Done — enter a new URL and click Start to run again.", "success")
        log("  Close the Chromium window when you are finished.", "dim")
        if on_complete:
            on_complete()

        while browser.is_connected():
            await asyncio.sleep(0.5)

        log("Browser closed.", "dim")

    except Exception:
        if on_complete:
            on_complete()
        raise

    finally:
        if browser.is_connected():
            await browser.close()
        await p.stop()
