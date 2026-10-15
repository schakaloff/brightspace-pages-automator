"""A failed file-link check must remain distinct from a successful empty scan."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from content_checker import ContentChecker


@pytest.mark.parametrize("link_scan_fails", [False, True])
def test_scan_report_marks_file_link_coverage_and_preserves_other_findings(monkeypatch, link_scan_fails):
    import browser
    import content_checker

    runtime = SimpleNamespace(stop=AsyncMock())
    browser_instance = SimpleNamespace(is_connected=lambda: True, close=AsyncMock())
    monkeypatch.setattr(browser, "launch_browser", AsyncMock(return_value=(runtime, browser_instance, object(), object())))
    monkeypatch.setattr(browser, "wait_for_login", AsyncMock())
    result = dict(name="Readings", section="Week 1", type="PAGE", status="missing", hint="modtype_book", href="https://moodle.example/mod/book/view.php?id=7")
    monkeypatch.setattr(content_checker, "_compare_items", lambda *_args: [dict(result)])
    reports = []
    checker = ContentChecker.__new__(ContentChecker)
    for key, value in dict(
        bs_url="https://learn.example/d2l/le/lessons/123", moodle_url="https://moodle.example/course/view.php?id=456",
        bs_username="", bs_password="", sso_email="", sso_password="", full_run=False,
        scan_only=True, order_only=False, books_only=False, link_repair_only=False,
        keep_browser_open=False, stop_flag=[False], operations=None,
        on_complete=MagicMock(), on_report=reports.append, _h5p=SimpleNamespace(), log=MagicMock(),
    ).items():
        setattr(checker, key, value)
    checker._fetch_bs_toc = AsyncMock(return_value=[dict(kind="MODULE", title="Week 1", id=12)])
    checker._scrape_moodle = AsyncMock(return_value=[dict(result)])
    checker._classify_existing_activity_links = MagicMock(return_value=[])
    checker._log_activity_link_report = MagicMock()
    checker._log_report = MagicMock()
    checker._bs_content_scan = AsyncMock(return_value={})
    checker._scan_page_content = AsyncMock(return_value=({}, []))
    checker._scan_broken_file_links = AsyncMock(side_effect=RuntimeError("File check unavailable")) if link_scan_fails else AsyncMock(return_value=[])

    asyncio.run(checker.run())

    assert checker.run_finished
    assert len(reports) == 1
    assert reports[0]["file_link_check"] == ("failed" if link_scan_fails else "complete")
    assert reports[0]["broken_links"] == 0
    assert reports[0]["missing"] == 1
    assert reports[0]["book_findings"] == [dict(name="Readings", section="Week 1")]
    runtime.stop.assert_awaited_once()
    browser_instance.close.assert_awaited_once()


def test_backend_early_exit_is_not_marked_finished(monkeypatch):
    import browser
    runtime = SimpleNamespace(stop=AsyncMock())
    monkeypatch.setattr(browser, "launch_browser", AsyncMock(return_value=(runtime, object(), object(), object())))
    monkeypatch.setattr(browser, "wait_for_login", AsyncMock())
    checker = ContentChecker.__new__(ContentChecker)
    checker.bs_url = "https://learn.example/d2l/le/lessons/123"
    checker.moodle_url = "https://moodle.example/course/view.php?id=456"
    checker.bs_username = checker.bs_password = checker.sso_email = checker.sso_password = ""
    checker._h5p = SimpleNamespace()
    checker.log = MagicMock()
    checker.on_complete = MagicMock()
    checker._fetch_bs_toc = AsyncMock(return_value=[])
    asyncio.run(checker.run())
    assert not checker.run_finished
    runtime.stop.assert_awaited_once()
