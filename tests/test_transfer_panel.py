"""Structured Transfer screen: steps, arranging, styling, create gating and older plans."""
import copy
import os
import sys
from pathlib import Path

os.environ.setdefault("BPA_SIMPLE_PREVIEW", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
import transfer_outline as o
from test_transfer_outline import target_course
from test_transfer_structure import source_course
from test_transfer_styling import PA_PAGE


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMainWindow
    import panels.transfer_panel as module
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    window = QMainWindow(); window.load_config = lambda: {}; window.chromium_ready = True
    result = module.TransferPanel(window); qtbot.addWidget(result)
    yield result
    result._timer.stop()
    result.close_studio()


def test_studio_launch_reuses_server_and_fallback_opens_draft(panel, monkeypatch):
    from panels import transfer_panel as module
    urls = []
    monkeypatch.setattr(module.webbrowser, "open", lambda url: urls.append(url) or True)
    panel.open_studio()
    server = panel._studio._server
    panel.open_studio()
    assert len(urls) == 2 and urls[0] != urls[1] and panel._studio._server is server
    panel._studio.scan({"source_url": "https://learn.okanagancollege.ca/d2l/home/1",
                        "target_url": "https://learn.okanagancollege.ca/d2l/home/2"})
    # Replace only the browser worker, exercising the GUI-thread queue bridge.
    def worker(action, kind):
        assert kind == "studio_scanned"
        panel._busy = True
        panel._queue.put((kind, (source_course(), target_course())))
    monkeypatch.setattr(panel, "_worker", worker)
    panel._poll()
    assert not panel._busy and panel._studio.path.exists()
    panel.open_studio_in_app()
    assert panel._plan["id"] == panel._studio.plan["id"] and panel._step == 2


def test_studio_scan_is_rejected_while_app_worker_is_busy(panel, monkeypatch):
    from panels import transfer_panel as module
    monkeypatch.setattr(module.webbrowser, "open", lambda url: True)
    panel.open_studio()
    panel._studio.scan({"source_url": "https://learn.okanagancollege.ca/d2l/home/1",
                        "target_url": "https://learn.okanagancollege.ca/d2l/home/2"})
    panel._busy = True
    panel._poll()
    assert panel._busy and not panel._studio.scanning and "busy" in panel._studio.error
    panel._busy = False


def test_studio_scan_runs_in_existing_browser_worker(panel, qtbot, monkeypatch):
    import threading
    from panels import transfer_panel as module
    import transfer_outline_runner
    monkeypatch.setattr(module.webbrowser, "open", lambda url: True)
    main_thread = threading.get_ident()
    calls = []
    async def browser(url, credentials, action, log, stop):
        assert threading.get_ident() != main_thread
        assert credentials["bs_username"] == "offline-test-user"
        calls.append(url)
        return await action("offline-page")
    async def scan_courses(page, source_id, target_id, log, stop):
        assert (page, source_id, target_id) == ("offline-page", "1", "2")
        log("Reading course modules…")
        return source_course(), target_course()
    monkeypatch.setattr(module, "with_transfer_browser", browser)
    monkeypatch.setattr(transfer_outline_runner, "scan_courses", scan_courses)
    panel._mw.bs_username = "offline-test-user"
    panel.open_studio()
    panel._studio.scan({"source_url": "https://learn.okanagancollege.ca/d2l/home/1",
                        "target_url": "https://learn.okanagancollege.ca/d2l/home/2"})
    qtbot.waitUntil(lambda: panel._studio.path is not None)
    assert calls == ["https://learn.okanagancollege.ca/d2l/home/1"]
    assert not panel._busy and not panel._studio.scanning
    assert "Reading course modules…" in panel._studio.snapshot()["messages"]
    assert "offline-test-user" not in str(panel._studio.snapshot())


@pytest.mark.parametrize("switch_draft", [False, True])
def test_desktop_state_save_cannot_overwrite_browser_edits(panel, monkeypatch, switch_draft):
    from panels import transfer_panel as module
    monkeypatch.setattr(module.webbrowser, "open", lambda url: True)
    panel.open_studio()
    panel._studio.scan({"source_url": "https://learn.okanagancollege.ca/d2l/home/1",
                        "target_url": "https://learn.okanagancollege.ca/d2l/home/2"})
    panel._queue.get_nowait()  # Finish this scan directly, without a live worker.
    panel._studio.finish_scan((source_course(), target_course()))
    panel.open_studio_in_app()
    studio = panel._studio
    studio.edit({"plan_id": studio.plan["id"], "revision": studio.revision, "action": "rename",
                 "entries": [{"side": "destination", "id": studio.plan["outline"][0]["id"]}],
                 "args": {"title": "Edited in browser"}})
    previous_path = panel._path
    if switch_draft:
        studio.scan({"source_url": "https://learn.okanagancollege.ca/d2l/home/1",
                     "target_url": "https://learn.okanagancollege.ca/d2l/home/2"})
        panel._queue.get_nowait()
        studio.finish_scan((source_course(), target_course()))
        assert studio.path != previous_path
    panel.save_state()
    assert panel._plan["outline"][0]["title"] == "Edited in browser"
    from transfer_runner import load_plan
    assert load_plan(previous_path)["outline"][0]["title"] == "Edited in browser"
    if not switch_draft: assert studio.snapshot()["history"]["undo"]


def test_browser_create_uses_existing_worker_and_keeps_credentials_private(panel, qtbot, monkeypatch):
    from panels import transfer_panel as module
    import transfer_outline_runner
    from transfer_runner import save_plan
    monkeypatch.setattr(module.webbrowser, "open", lambda url: True)
    panel._mw.bs_username = "offline-creation-user"
    panel.open_studio()
    studio = panel._studio
    studio.scan({"source_url": "https://learn.okanagancollege.ca/d2l/home/1", "target_url": "https://learn.okanagancollege.ca/d2l/home/2"})
    # The scan is completed directly; discard only its queued request.
    panel._queue.get_nowait()
    studio.finish_scan((source_course(), target_course()))
    calls = []
    async def browser(url, credentials, action, log, stop):
        assert url.endswith("/d2l/home/2") and credentials["bs_username"] == "offline-creation-user"
        assert stop is studio.stop_event
        return await action("offline-page")
    async def apply(page, plan, path, target_id, acknowledged, log, stop):
        assert (page, target_id, acknowledged) == ("offline-page", "2", True)
        calls.append(plan["id"]); log("Reading everything back…")
        plan.update(run_signature="offline-created", validation={"Offline check": True}, report="STRUCTURED TRANSFER — COMPLETE")
        save_plan(plan, path); return plan
    monkeypatch.setattr(module, "with_transfer_browser", browser)
    monkeypatch.setattr(transfer_outline_runner, "apply_outline", apply)
    studio.create({"plan_id": studio.plan["id"], "revision": studio.revision, "target_id": "2", "confirmed": True})
    qtbot.waitUntil(lambda: studio.run_status == "complete")
    assert calls == [studio.plan["id"]] and not panel._busy and not panel._creating
    assert "offline-creation-user" not in str(studio.snapshot())


def scanned(panel, source=None, target=None):
    panel._scan = (source or source_course(), target or target_course())
    panel.go(1)


def test_studio_home_urls_scan_and_create_through_real_browser_wrapper(panel, qtbot, monkeypatch):
    """Mock browser IO, retaining the real worker/authentication-wrapper plumbing."""
    import browser as browser_module
    import transfer_outline_runner
    from types import SimpleNamespace
    from panels import transfer_panel as module
    from transfer_runner import save_plan
    visited, signed_in = [], []
    class Page:
        url = ""
        async def goto(self, url, **kwargs):
            self.url = url; visited.append(url)
    page = Page()
    async def close(): pass
    async def launch(log_fn=None):
        return SimpleNamespace(stop=close), SimpleNamespace(close=close), SimpleNamespace(close=close), page
    async def login(page, context, username, password, sso_email, sso_password, log_fn=None):
        signed_in.append(username)
    async def scan(page, source_id, target_id, log, stop):
        assert (source_id, target_id) == ("1", "2")
        return source_course(), target_course()
    async def create(page, plan, path, target_id, acknowledged, log, stop):
        assert page.url.endswith("/d2l/home/2") and target_id == "2" and acknowledged
        plan.update(run_signature="offline-created", validation={"Offline check": True}, report="STRUCTURED TRANSFER — COMPLETE")
        save_plan(plan, path); return plan
    monkeypatch.setattr(module.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(browser_module, "launch_browser", launch)
    monkeypatch.setattr(browser_module, "wait_for_login", login)
    monkeypatch.setattr(transfer_outline_runner, "scan_courses", scan)
    monkeypatch.setattr(transfer_outline_runner, "apply_outline", create)
    panel._mw.bs_username = "offline-wrapper-user"
    panel.open_studio(); studio = panel._studio
    studio.scan({"source_url": "https://learn.okanagancollege.ca/d2l/home/1",
                 "target_url": "https://learn.okanagancollege.ca/d2l/home/2"})
    qtbot.waitUntil(lambda: studio.path is not None or bool(studio.error))
    assert not studio.error, studio.error
    studio.create({"plan_id": studio.plan["id"], "revision": studio.revision, "target_id": "2", "confirmed": True})
    qtbot.waitUntil(lambda: studio.run_status in ("complete", "failed"))
    assert studio.run_status == "complete", studio.error
    assert visited == ["https://learn.okanagancollege.ca/d2l/home/1", "https://learn.okanagancollege.ca/d2l/home/2"]
    assert signed_in == ["offline-wrapper-user"] * 2


def test_steps_unlock_in_order_and_one_primary_button(panel):
    assert panel._step == 0 and panel._primary.text() == "Scan both courses" and not panel._primary.isEnabled()
    panel.connect.source.setText("https://learn.okanagancollege.ca/d2l/home/1")
    panel.connect.target.setText("https://learn.okanagancollege.ca/d2l/home/1")
    assert not panel._primary.isEnabled()                      # same course twice
    panel.connect.target.setText("https://learn.okanagancollege.ca/d2l/home/2")
    assert panel._primary.isEnabled()
    assert [b.isEnabled() for b in panel._step_buttons] == [True, False, False, False, False]
    panel.go(3)
    assert panel._step == 0                                     # can't skip ahead without a scan


def test_starting_point_builds_an_outline_from_a_pattern(panel):
    scanned(panel)
    assert panel._step == 1 and panel._primary.text() == "Build outline"
    index = panel.start.pattern.findText("EA105 workshop layout")
    panel.start.pattern.setCurrentIndex(index)
    assert "Online Classroom · Module Introduction" in panel.start.pattern_detail.text()
    panel._primary_clicked()
    assert panel._step == 2 and o.is_outline(panel._plan)
    week = panel._plan["outline"][0]
    assert [c["title"] for c in week["children"]][:2] == ["Online Classroom", "Module Introduction"]
    assert panel._path.exists()                                  # saved immediately


def test_mirror_start_places_everything(panel):
    scanned(panel)
    panel.start.cards["mirror"][1].setChecked(True)
    panel._primary_clicked()
    assert not o.unplaced(panel._plan) and "Every item is placed" in panel._status.text()


def test_arrange_moves_skips_and_accepts_suggestions(panel):
    scanned(panel)
    panel.start.cards["empty"][1].setChecked(True); panel._primary_clicked()
    page = panel.arrange
    page.add_top_module()
    unit = panel._plan["outline"][0]
    assert unit["title"] == "New module"
    page.dropped([["row", "11"], ["row", "12"]], unit["id"], None)      # drag two items in
    assert [n["item"] for n in unit["children"]] == ["11", "12"]
    page.dropped([["folder", "14"]], unit["id"], None)                    # drag a whole folder in
    assert unit["children"][-1]["title"] == "Readings"
    assert page.select_row("13")
    page.skip_selected()
    assert "13" in panel._plan["skipped"] and not o.unplaced(panel._plan)
    page.select_row("13"); assert page.skip_button.text() == "Include"
    page.skip_selected(); assert "13" not in panel._plan["skipped"]
    o.set_sources(panel._plan, unit["id"], ["10"]); panel.changed()
    assert not panel._secondary.isHidden() and panel._secondary.text().startswith("Accept 1 suggestion")
    panel._secondary_clicked()
    assert not o.unplaced(panel._plan)


def test_outline_drag_reorders_and_rename_and_errors_are_shown(panel, qtbot):
    scanned(panel)
    panel.start.cards["mirror"][1].setChecked(True); panel._primary_clicked()
    page = panel.arrange
    week = panel._plan["outline"][0]
    readings = week["children"][-1]
    page.dropped([["node", readings["id"]]], week["id"], 0)
    assert week["children"][0]["id"] == readings["id"]
    assert page.select_node(readings["id"])
    page.outline.currentItem().setText(0, "Course readings"); qtbot.wait(10)
    assert readings["title"] == "Course readings"
    page.select_node(readings["id"])
    page.outline.currentItem().setText(0, ""); qtbot.wait(10)          # rejected, explained, redrawn
    assert readings["title"] == "Course readings" and "name" in panel._status.text().lower()
    page.select_node(readings["id"])
    assert page.outline.currentItem().text(0) == "Course readings"
    page.dropped([["row", "11"]], None, None)
    assert "Drop items into a module" in panel._status.text()


def test_template_pages_get_text_and_patterns_can_be_saved(panel, monkeypatch):
    scanned(panel)
    panel.start.cards["mirror"][1].setChecked(True); panel._primary_clicked()
    week = panel._plan["outline"][0]
    panel.arrange.add_page(week["id"], "introduction")
    intro = next(c for c in week["children"] if c["kind"] == "template")
    assert panel._plan["templates"]["introduction"]
    o.set_text(panel._plan, intro["id"], "Welcome", ["Explain"]); panel.changed()
    panel.save_pattern(o.pattern_from_module(panel._plan, week["id"], "Mirror layout"))
    assert panel.patterns()[0]["name"] == "Mirror layout"


def test_styling_choice_previews_and_saves(panel):
    source = source_course(); source["modules"][0]["children"][0]["html"] = PA_PAGE
    scanned(panel, source)
    panel.start.cards["mirror"][1].setChecked(True); panel._primary_clicked()
    panel.go(3)
    page = panel.styling
    assert page.pages.count() == 1 and "No change" in page.pages.item(0).text()
    page.radios["strip_bpa"][1].setChecked(True)
    assert panel._plan["styling"]["mode"] == "strip_bpa"
    assert "Cleaned" in page.pages.item(0).text() and "Page Automator style block" in page.summary.text()
    page.override.setCurrentIndex(page.override.findData("preserve"))
    assert panel._plan["styling"]["overrides"] == {"11": "preserve"}
    page.apply_template.setChecked(True)
    assert panel._plan["styling"]["apply_template"] and panel._plan["styling"]["template"]["id"]
    assert o.load_patterns  # saved plan holds the styling choice
    from transfer_runner import load_plan
    assert load_plan(panel._path)["styling"]["overrides"] == {"11": "preserve"}


def test_create_needs_a_clean_plan_and_confirmation(panel):
    scanned(panel)
    panel.start.cards["empty"][1].setChecked(True); panel._primary_clicked()
    panel.go(4)
    assert panel._primary.text() == "Create transfer" and not panel._primary.isEnabled()
    assert "to fix first" in panel._status.text()
    panel.create.confirm.setChecked(True)
    assert not panel._primary.isEnabled()                                # still blocked: items to place
    panel.show_issue({"text": "4 items still need a place, or Skip.", "node": None, "row": None})
    assert panel._step == 2 and panel.arrange.only_waiting.isChecked()
    o.skip(panel._plan, list(panel._plan["items"])); panel.changed()
    panel.go(4)
    assert not panel._primary.isEnabled()                                # editing unticks the confirmation
    panel.create.confirm.setChecked(True)
    assert panel._primary.isEnabled() and "Add this as hidden content to Target shell" in panel.create.confirm.text()


def test_created_transfer_shows_its_report_and_cannot_be_edited(panel):
    scanned(panel)
    panel.start.cards["mirror"][1].setChecked(True); panel._primary_clicked()
    plan = panel._plan
    plan.update(run_signature="x", report="STRUCTURED TRANSFER — COMPLETE", validation={"A": True, "B": True})
    panel.set_plan(plan, panel._path); panel.go(4)
    assert panel.create.report.isVisible() is not None and panel.create.report_title.text() == "Transfer created"
    assert panel._primary.text() == "Open in Brightspace" and not panel._editable()
    panel.go(2)
    assert not panel.arrange.add_module_button.isVisible()


def test_older_workshop_plans_open_read_only(panel, tmp_path):
    from transfer_plan import build_plan, workshop_recipe
    recipe = workshop_recipe(); recipe["workshops"] = recipe["workshops"][:1]
    legacy = build_plan(source_course(), target_course(), recipe)
    panel.set_plan(legacy, tmp_path / "legacy.json"); panel.go(2)
    assert panel._step == 5 and panel._primary.text() == "Start a new transfer"
    legacy["run_signature"] = "x"; panel.go(2)
    assert panel._primary.text() == "Resume / re-check"
    panel._secondary_clicked()
    assert panel._plan is None and panel._step == 0


def test_copied_pages_can_be_renamed_in_the_outline(panel, qtbot):
    scanned(panel)
    panel.start.cards["mirror"][1].setChecked(True); panel._primary_clicked()
    week = panel._plan["outline"][0]
    reading = next(c for c in week["children"] if c.get("item") == "11")
    assert panel.arrange.select_node(reading["id"])
    panel.arrange.outline.currentItem().setText(0, "Chapter 2 reading"); qtbot.wait(10)
    assert panel._plan["items"]["11"]["title"] == "Chapter 2 reading"
