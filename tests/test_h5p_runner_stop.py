"""Cancellation releases H5P browser resources at every pipeline stage."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "src")


@pytest.mark.parametrize("phase", ["login", "toc", "moodle", "units", "embed"])
def test_cancel_h5p_pipeline_closes_browser(monkeypatch, phase):
    import browser as browser_module
    import h5p_runner

    async def scenario():
        started = asyncio.Event()
        stop_flag = [False]
        completed = MagicMock()
        browser = MagicMock()
        browser.close = AsyncMock()
        driver = MagicMock()
        driver.stop = AsyncMock()
        checker = MagicMock()
        checker.stop_flag = stop_flag

        async def stage(name, result):
            if name == phase:
                started.set()
                await asyncio.sleep(3600)
            return result

        async def launch():
            return driver, browser, object(), object()

        checker._fetch_bs_toc = lambda *args: stage("toc", [{"id": 1}])
        checker._scrape_moodle = lambda *args: stage("moodle", [{"name": "Activity"}])
        checker._ensure_h5p_destination_units = lambda *args: stage("units", [{"id": 1}])
        checker._h5p.embed_in_brightspace = lambda *args: stage("embed", None)
        monkeypatch.setattr(h5p_runner, "ContentChecker", lambda **kwargs: checker)
        monkeypatch.setattr(browser_module, "launch_browser", launch)
        monkeypatch.setattr(browser_module, "wait_for_login", lambda *args: stage("login", None))
        task = asyncio.create_task(h5p_runner.run_h5p_only(
            "https://example.org/d2l/le/content/123/home",
            "https://example.org/course/view.php?id=123", MagicMock(),
            on_complete=completed, stop_flag=stop_flag,
        ))
        await asyncio.wait_for(started.wait(), timeout=2)
        stop_flag[0] = True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        browser.close.assert_awaited_once()
        driver.stop.assert_awaited_once()
        completed.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["launch", "context", "page"])
def test_cancel_during_browser_startup_releases_driver(monkeypatch, phase):
    import browser as browser_module

    async def scenario():
        started = asyncio.Event()
        browser = MagicMock()
        browser.close = AsyncMock()
        context = MagicMock()
        driver = MagicMock()
        driver.stop = AsyncMock()

        async def stage(name, result):
            if phase == name:
                started.set()
                await asyncio.sleep(3600)
            return result

        driver.chromium.launch = lambda **kwargs: stage("launch", browser)
        browser.new_context = lambda **kwargs: stage("context", context)
        context.new_page = lambda: stage("page", object())
        manager = MagicMock()
        manager.start = AsyncMock(return_value=driver)
        monkeypatch.setattr(browser_module, "async_playwright", lambda: manager)
        task = asyncio.create_task(browser_module.launch_browser())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        driver.stop.assert_awaited_once()
        if phase == "launch":
            browser.close.assert_not_awaited()
        else:
            browser.close.assert_awaited_once()

    asyncio.run(scenario())


def test_driver_stops_even_if_browser_cleanup_fails(monkeypatch):
    import browser as browser_module
    import h5p_runner

    browser = MagicMock()
    browser.close = AsyncMock(side_effect=RuntimeError("Browser close failed"))
    driver = MagicMock()
    driver.stop = AsyncMock()
    checker = MagicMock()
    checker.stop_flag = [False]
    checker._fetch_bs_toc = AsyncMock(return_value=[])
    monkeypatch.setattr(h5p_runner, "ContentChecker", lambda **kwargs: checker)
    monkeypatch.setattr(browser_module, "launch_browser", AsyncMock(
        return_value=(driver, browser, object(), object())
    ))
    monkeypatch.setattr(browser_module, "wait_for_login", AsyncMock())
    with pytest.raises(RuntimeError, match="Browser close failed"):
        asyncio.run(h5p_runner.run_h5p_only(
            "https://example.org/d2l/le/content/123/home",
            "https://example.org/course/view.php?id=123", MagicMock(),
        ))
    driver.stop.assert_awaited_once()
