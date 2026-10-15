"""Real HTTP boundary tests using the existing offline course inventories."""
import copy
import http.client
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from transfer_studio import TransferStudio, course_id, asset_directory
from transfer_runner import load_plan
from test_transfer_structure import source_course
from test_transfer_outline import target_course


@pytest.fixture
def studio(tmp_path):
    requests = []
    server = TransferStudio(tmp_path, requests.append)
    server.start()
    server.requests = requests
    yield server
    server.close()


def request(studio, path, method="GET", body=None, cookie=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", urlsplit(studio.origin).port, timeout=3)
    all_headers = dict(headers or {})
    if cookie: all_headers["Cookie"] = cookie
    if body is not None:
        body = json.dumps(body)
        all_headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body, all_headers)
    response = connection.getresponse()
    status, response_headers, data = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return status, response_headers, data


def login(studio):
    url = urlsplit(studio.start())
    status, headers, body = request(studio, "/?" + url.query)
    assert status == 200 and b"Transfer Studio" in body
    return headers["Set-Cookie"].split(";", 1)[0]


def scan(studio, cookie, **changes):
    body = {"source_url": "https://learn.okanagancollege.ca/d2l/home/1",
            "target_url": "https://learn.okanagancollege.ca/d2l/le/lessons/2", "start": "mirror"}
    body.update(changes)
    return request(studio, "/api/scan", "POST", body, cookie,
                   {"Origin": studio.origin, "X-Studio-Request": "1"})


def test_one_use_token_session_and_local_asset_boundary(studio):
    url = urlsplit(studio.start())
    launch = "/?" + url.query
    status, headers, _ = request(studio, launch)
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    assert status == 200 and "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert request(studio, launch, cookie=cookie)[0] == 403
    for path in ("/", "/api/state", "/studio.js", "/studio.css", "/HankenGrotesk.ttf"):
        assert request(studio, path)[0] == 403
        assert request(studio, path, cookie=cookie)[0] == 200
    assert request(studio, "/../src/config.py", cookie=cookie)[0] == 404
    assert request(studio, "/api/create", "POST", {}, cookie, {"Origin": studio.origin, "X-Studio-Request": "1"})[0] == 400
    assert request(studio, "/api/delete-course", "POST", {}, cookie, {"Origin": studio.origin, "X-Studio-Request": "1"})[0] == 404


def test_rebinding_and_cross_site_requests_are_rejected(studio):
    cookie = login(studio)
    assert request(studio, "/api/state", cookie=cookie, headers={"Host": "attacker.example"})[0] == 403
    assert request(studio, "/api/state", cookie=cookie, headers={"Origin": "https://attacker.example"})[0] == 403
    assert request(studio, "/api/state", cookie=cookie, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert request(studio, "/api/scan", "POST", {}, cookie)[0] == 403
    assert request(studio, "/api/scan", "POST", {}, cookie, {"Origin": studio.origin})[0] == 403
    assert not studio.requests


@pytest.mark.parametrize("changes", [
    {"source_url": "https://attacker.example/d2l/home/1"},
    {"source_url": "https://learn.okanagancollege.ca:444/d2l/home/1"},
    {"target_url": "https://learn.okanagancollege.ca/d2l/home/1"},
    {"start": "unknown"}, {"source_url": None}, {"pattern": {}},
])
def test_invalid_scan_never_dispatches(studio, changes):
    assert scan(studio, login(studio), **changes)[0] == 400
    assert not studio.scanning and not studio.requests


@pytest.mark.parametrize("start", ["mirror", "empty", "destination", "pattern"])
def test_scan_uses_engine_saves_and_exposes_only_display_data(studio, start):
    cookie = login(studio)
    status, _, _ = scan(studio, cookie, start=start, pattern="Introduction, then content")
    assert status == 202 and studio.scanning
    assert studio.requests == [{"source_id": "1", "target_id": "2", "url": "https://learn.okanagancollege.ca/d2l/home/1"}]
    assert scan(studio, cookie)[0] == 409
    original = source_course()
    studio.finish_scan((copy.deepcopy(original), target_course()))
    assert not studio.scanning and not studio.error
    saved = load_plan(studio.path)
    assert saved["start"] == start and saved["source"] == original and not saved["journal"]
    status, _, data = request(studio, "/api/state", cookie=cookie)
    display = json.loads(data)["plan"]
    assert status == 200 and display["start"] == start
    assert display["counts"]["unplaced"] == (4 if start in ("empty", "destination") else 0)
    assert "html" not in data.decode() and "journal" not in data.decode() and "password" not in data.decode()
    assert display["source_nodes"][0]["children"][0]["title"] == saved["items"]["11"]["title"]
    if start == "pattern": assert display["pattern"] == "Introduction, then content"
    reopened = TransferStudio(studio.data_dir, lambda r: None, saved_path=studio.path)
    assert reopened.snapshot()["plan"]["id"] == saved["id"]


def test_failed_rescan_keeps_previous_saved_draft(studio):
    cookie = login(studio)
    scan(studio, cookie)
    studio.finish_scan((source_course(), target_course()))
    original = load_plan(studio.path)
    scan(studio, cookie)
    studio.finish_scan(error="Sign in to Brightspace and try again.")
    assert studio.error == "Sign in to Brightspace and try again."
    assert load_plan(studio.path) == original
    assert scan(studio, cookie)[0] == 202


def test_close_invalidates_sessions_and_releases_port(studio):
    port = urlsplit(studio.origin).port
    cookie = login(studio)
    studio.close()
    assert not studio.session(cookie) and not studio._thread.is_alive()
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_frozen_asset_location(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert asset_directory() == tmp_path / "assets" / "transfer-studio"


@pytest.mark.parametrize("path", ["/d2l/home/25708", "/d2l/le/lessons/25708", "/d2l/le/content/25708/Home", "/d2l/lms?ou=25708"])
def test_course_link_forms(path):
    assert course_id("https://learn.okanagancollege.ca" + path) == "25708"


def ready(studio):
    cookie = login(studio)
    scan(studio, cookie)
    studio.finish_scan((source_course(), target_course()))
    return cookie


def edit_request(studio, cookie, action, entries=None, args=None, **changes):
    state = studio.snapshot()
    body = {"plan_id": studio.plan["id"], "revision": state["revision"], "action": action,
            "entries": entries or [], "args": args or {}}
    body.update(changes)
    status, _, data = request(studio, "/api/edit", "POST", body, cookie,
                              {"Origin": studio.origin, "X-Studio-Request": "1"})
    return status, json.loads(data)


def entry(id_, side="destination"):
    return {"side": side, "id": id_}


def test_bulk_remove_parent_and_child_is_one_undoable_edit(studio):
    import transfer_outline as o
    cookie = ready(studio)
    original = copy.deepcopy(studio.plan)
    root = studio.plan["outline"][0]
    status, result = edit_request(studio, cookie, "remove", [entry(root["id"]), entry(root["children"][0]["id"])])
    assert status == 200 and not studio.plan["outline"]
    assert len(o.unplaced(studio.plan)) == 4
    assert result["state"]["history"]["undo"] and not result["state"]["history"]["redo"]
    assert load_plan(studio.path) == studio.plan
    assert edit_request(studio, cookie, "undo")[0] == 200
    assert studio.plan == original
    assert edit_request(studio, cookie, "redo")[0] == 200
    assert not studio.plan["outline"]
    assert edit_request(studio, cookie, "undo")[0] == 200
    assert studio.plan == original


def test_source_folder_skip_and_include_expand_descendants(studio):
    cookie = ready(studio)
    assert edit_request(studio, cookie, "skip", [entry("10", "source"), entry("11", "source")])[0] == 200
    assert set(studio.plan["skipped"]) == {"11", "12", "13", "15"}
    assert studio.snapshot()["plan"]["counts"]["unplaced"] == 0
    assert edit_request(studio, cookie, "include", [entry("10", "source")])[0] == 200
    assert not studio.plan["skipped"] and studio.snapshot()["plan"]["counts"]["unplaced"] == 4
    assert edit_request(studio, cookie, "undo")[0] == 200
    assert len(studio.plan["skipped"]) == 4


def test_source_folder_skip_badges_cover_nested_and_partial_skips_and_history(studio):
    cookie = ready(studio)
    edit_request(studio, cookie, "skip", [entry("11", "source")])
    root = studio.snapshot()["plan"]["source_nodes"][0]
    assert (root["status"], root["skipped_count"], root["item_count"]) == ("Partly skipped", 1, 4)
    edit_request(studio, cookie, "skip", [entry("14", "source")])
    root = studio.snapshot()["plan"]["source_nodes"][0]
    assert root["skipped_count"] == 2 and root["children"][-1]["status"] == "Skipped"
    edit_request(studio, cookie, "skip", [entry("10", "source")])
    assert studio.snapshot()["plan"]["source_nodes"][0]["status"] == "Skipped"
    edit_request(studio, cookie, "undo")
    assert studio.snapshot()["plan"]["source_nodes"][0]["status"] == "Partly skipped"
    edit_request(studio, cookie, "redo")
    assert studio.snapshot()["plan"]["source_nodes"][0]["status"] == "Skipped"
    edit_request(studio, cookie, "include", [entry("10", "source")])
    root = studio.snapshot()["plan"]["source_nodes"][0]
    assert root["status"] == "" and root["skipped_count"] == 0


def test_folder_creation_rename_and_move_use_engine(studio):
    import transfer_outline as o
    cookie = ready(studio)
    root = studio.plan["outline"][0]
    assert edit_request(studio, cookie, "add_module", args={"parent": root["id"], "title": "Practice"})[0] == 200
    folder = studio.plan["outline"][0]["children"][-1]
    assert edit_request(studio, cookie, "rename", [entry(folder["id"])], {"title": "Exercises"})[0] == 200
    assert edit_request(studio, cookie, "place", [entry("11", "source"), entry("12", "source")], {"parent": folder["id"]})[0] == 200
    assert o.placements(studio.plan)["11"] == [folder["id"]]
    assert o.placements(studio.plan)["12"] == [folder["id"]]
    assert o.find(studio.plan, folder["id"])[0]["title"] == "Exercises"
    assert edit_request(studio, cookie, "undo")[0] == 200
    assert o.placements(studio.plan)["11"] == [root["id"]]
    assert edit_request(studio, cookie, "add_module", args={"title": "Other"})[0] == 200
    other = studio.plan["outline"][-1]
    assert edit_request(studio, cookie, "place", [entry(folder["id"])], {"parent": other["id"]})[0] == 200
    assert o.find(studio.plan, folder["id"])[1]["id"] == other["id"]
    assert not studio.snapshot()["history"]["redo"]


def test_copy_nested_source_selection_does_not_duplicate_content(studio):
    import transfer_outline as o
    cookie = ready(studio)
    status, _ = edit_request(studio, cookie, "place", [entry("10", "source"), entry("14", "source"), entry("15", "source")], {"parent": None})
    assert status == 200
    assert all(len(holders) == 1 for holders in o.placements(studio.plan).values())
    assert studio.plan["outline"][-1]["title"] == "Week 1"


def test_between_module_drop_preserves_group_order_in_both_directions_and_undo(studio):
    cookie = ready(studio)
    for title in ("B", "C", "D", "E"):
        assert edit_request(studio, cookie, "add_module", args={"title": title})[0] == 200
    ids = {n["title"]: n["id"] for n in studio.plan["outline"]}
    before = copy.deepcopy(studio.plan)
    # Selection order differs from outline order; moving forward uses a fixed anchor.
    assert edit_request(studio, cookie, "place", [entry(ids["C"]), entry(ids["Week 1"])],
                        {"parent": None, "before": ids["E"]})[0] == 200
    assert [n["title"] for n in studio.plan["outline"]] == ["B", "D", "Week 1", "C", "E"]
    assert edit_request(studio, cookie, "undo")[0] == 200 and studio.plan == before
    assert edit_request(studio, cookie, "place", [entry(ids["E"]), entry(ids["D"])],
                        {"parent": None, "before": ids["Week 1"]})[0] == 200
    assert [n["title"] for n in studio.plan["outline"]] == ["D", "E", "Week 1", "B", "C"]
    assert edit_request(studio, cookie, "undo")[0] == 200 and studio.plan == before
    # Dropping on a selected boundary must keep the group and its children intact.
    assert edit_request(studio, cookie, "place", [entry(ids["Week 1"]), entry(ids["B"])],
                        {"parent": None, "before": ids["Week 1"]})[0] == 200
    assert studio.plan["outline"] == before["outline"]


def test_source_unit_can_be_inserted_between_destination_units(studio):
    cookie = ready(studio)
    edit_request(studio, cookie, "add_module", args={"title": "Last"})
    last = studio.plan["outline"][-1]["id"]
    assert edit_request(studio, cookie, "place", [entry("14", "source")], {"parent": None, "before": last})[0] == 200
    assert [n["title"] for n in studio.plan["outline"]] == ["Week 1", "Readings", "Last"]


@pytest.mark.parametrize("position", ["missing", 4])
def test_invalid_insertion_position_leaves_the_plan_unchanged(studio, position):
    cookie = ready(studio)
    before = copy.deepcopy(studio.plan)
    node = before["outline"][0]
    assert edit_request(studio, cookie, "place", [entry(node["id"])], {"parent": None, "before": position})[0] == 400
    assert studio.plan == before and not studio._undo


@pytest.mark.parametrize("action,entries,args", [
    ("remove", [entry("missing")], {}),
    ("skip", [entry("11", "source"), entry("missing", "source")], {}),
    ("remove", [entry("11", "source"), entry("missing")], {}),
    ("rename", [entry("missing")], {"title": "New"}),
    ("add_module", [], {"title": ""}),
    ("unknown", [], {}),
])
def test_invalid_bulk_edits_leave_plan_and_history_unchanged(studio, action, entries, args):
    cookie = ready(studio)
    before = copy.deepcopy(studio.plan)
    revision = studio.revision
    assert edit_request(studio, cookie, action, entries, args)[0] == 400
    assert studio.plan == before and load_plan(studio.path) == before
    assert studio.revision == revision and not studio._undo


def test_cycle_move_is_atomic_and_does_not_drop_nodes(studio):
    cookie = ready(studio)
    original = copy.deepcopy(studio.plan)
    root = studio.plan["outline"][0]
    sub = root["children"][-1]
    assert edit_request(studio, cookie, "place", [entry(root["id"])], {"parent": sub["id"]})[0] == 400
    assert studio.plan == original


def test_stale_other_tab_created_and_busy_plans_cannot_be_edited(studio):
    cookie = ready(studio)
    root = studio.plan["outline"][0]
    revision = studio.revision
    assert edit_request(studio, cookie, "rename", [entry(root["id"])], {"title": "Changed"})[0] == 200
    assert edit_request(studio, cookie, "remove", [entry(root["id"])], revision=revision)[0] == 409
    studio.app_busy = True
    assert edit_request(studio, cookie, "undo")[0] == 409
    studio.app_busy = False
    studio.plan["run_signature"] = "created"
    assert edit_request(studio, cookie, "undo")[0] == 409


def test_external_desktop_edit_refreshes_state_and_invalidates_history(studio):
    from transfer_runner import save_plan
    cookie = ready(studio)
    root = studio.plan["outline"][0]
    edit_request(studio, cookie, "rename", [entry(root["id"])], {"title": "Browser"})
    disk = load_plan(studio.path)
    disk["outline"][0]["title"] = "Desktop"
    save_plan(disk, studio.path)
    state = studio.snapshot()
    assert state["plan"]["outline"][0]["title"] == "Desktop"
    assert not state["history"]["undo"] and not state["history"]["redo"]


def test_save_failure_preserves_plan_history_and_revision(studio, monkeypatch):
    import transfer_studio
    cookie = ready(studio)
    original = copy.deepcopy(studio.plan)
    revision = studio.revision
    def fail(*args): raise OSError("Disk is full")
    monkeypatch.setattr(transfer_studio, "save_plan", fail)
    root = studio.plan["outline"][0]
    status, result = edit_request(studio, cookie, "remove", [entry(root["id"])])
    assert status == 400 and "Disk is full" in result["error"]
    assert studio.plan == original and studio.revision == revision and not studio._undo


def test_styling_real_preview_override_and_history(studio):
    from test_transfer_styling import PA_PAGE
    cookie = login(studio)
    source = source_course(); source["modules"][0]["children"][0]["html"] = PA_PAGE
    scan(studio, cookie); studio.finish_scan((source, target_course()))
    original = copy.deepcopy(studio.plan)
    assert edit_request(studio, cookie, "styling", args={"mode": "strip_bpa"})[0] == 200
    preview = studio.page_preview("11")
    assert preview["ok"] and preview["removed"] and "main-container" not in preview["after"]
    look = studio.snapshot()["plan"]["looks"][0]["key"]
    assert edit_request(studio, cookie, "styling", args={"look": look, "apply_template": True})[0] == 200
    assert studio.page_preview("11")["templated"]
    assert edit_request(studio, cookie, "styling", args={"row": "11", "override": "preserve"})[0] == 200
    assert studio.page_preview("11")["after"] == PA_PAGE
    for _ in range(3): assert edit_request(studio, cookie, "undo")[0] == 200
    assert studio.plan == original


def test_preview_cannot_run_scripts_or_request_external_resources(studio):
    cookie = login(studio)
    source = source_course()
    source["modules"][0]["children"][0]["html"] = '<script>fetch("/api/create")</script><form action="/api/create"></form><img src="https://example.com/beacon"><a href="/api/state" onclick="bad()">Link</a><iframe src="https://example.com"></iframe><style>p{color:red}</style><p>Text</p>'
    scan(studio, cookie); studio.finish_scan((source, target_course()))
    status, headers, data = request(studio, "/api/preview?row=11&side=before", cookie=cookie)
    assert status == 200 and "sandbox" in headers["Content-Security-Policy"] and "default-src 'none'" in headers["Content-Security-Policy"]
    assert b"<script" not in data and b"onclick" not in data and b"src=" not in data and b"href=" not in data
    assert b"color:red" in data and b"Text" in data
    assert request(studio, "/api/preview?row=11&side=after")[0] == 403
    assert request(studio, "/api/page?row=12", cookie=cookie)[0] == 400


def test_layouts_authoring_and_generated_preview_use_existing_engine(studio):
    import transfer_outline as o
    cookie = ready(studio)
    root = studio.plan["outline"][0]
    entries = [entry(root["id"])]
    assert edit_request(studio, cookie, "add_module", args={"parent": root["id"], "title": "Practice"})[0] == 200
    assert edit_request(studio, cookie, "save_pattern", entries, {"title": "My layout"})[0] == 200
    assert o.load_patterns(studio.data_dir / "patterns.json")[0]["name"] == "My layout"
    assert edit_request(studio, cookie, "apply_pattern", entries, {"pattern": "My layout"})[0] == 200
    assert edit_request(studio, cookie, "add_page", entries, {"kind": "introduction", "title": "Welcome"})[0] == 200
    node = next(n for n, _, _ in o.walk(studio.plan["outline"]) if n["kind"] == "template")
    assert edit_request(studio, cookie, "set_text", [entry(node["id"])], {"introduction": "Welcome to this module.", "outcomes": ["Explain the topic."]})[0] == 200
    preview = studio.page_preview("node:" + node["id"])
    assert "Welcome to this module." in preview["after"] and "Explain the topic." in preview["after"]
    assert "node:" + node["id"] in [p["id"] for p in studio.snapshot()["plan"]["pages"]]


def test_bad_course_link_identifies_the_field(studio):
    status, _, data = scan(studio, login(studio), target_url="https://learn.okanagancollege.ca/d2l/home")
    assert status == 400 and json.loads(data)["field"] == "target_url"


def create_request(studio, cookie, **changes):
    body = {"plan_id": studio.plan["id"], "revision": studio.revision, "target_id": studio.plan["target"]["id"], "confirmed": True}
    body.update(changes)
    status, _, data = request(studio, "/api/create", "POST", body, cookie, {"Origin": studio.origin, "X-Studio-Request": "1"})
    return status, json.loads(data)


def test_creation_requires_confirmation_current_revision_and_clean_plan(studio):
    cookie = ready(studio)
    requests = []; studio.request_create = requests.append
    assert create_request(studio, cookie, confirmed=False)[0] == 400
    assert create_request(studio, cookie, target_id="wrong")[0] == 400
    assert create_request(studio, cookie, revision=-1)[0] == 409
    root = studio.plan["outline"][0]
    edit_request(studio, cookie, "remove", [entry(root["id"])])
    assert create_request(studio, cookie)[0] == 400 and not requests


@pytest.mark.asyncio
async def test_http_create_runs_real_offline_engine_and_report(tmp_path, monkeypatch):
    from test_transfer_outline_runner import setup
    from transfer_outline_runner import OutlineRunner
    source, target, plan, path, copies = await setup(tmp_path, monkeypatch)
    requests = []
    studio = TransferStudio(tmp_path, lambda r: None, saved_path=path, request_create=requests.append)
    try:
        cookie = login(studio)
        assert create_request(studio, cookie)[0] == 202
        assert create_request(studio, cookie)[0] == 409
        assert edit_request(studio, cookie, "undo")[0] == 409
        payload = requests.pop()
        result = await OutlineRunner(source, target, payload["plan"], path, studio.run_log, studio.stop_event).run("2", True)
        studio.finish_create(result)
        state = studio.snapshot()
        assert state["run"]["status"] == "complete" and state["run"]["phase"] == 6
        assert all(state["plan"]["validation"].values()) and "COMPLETE" in state["plan"]["report"]
        assert b"COMPLETE" in request(studio, "/api/report", cookie=cookie)[2]
        assert not source.writes and len(copies) == 1
        assert create_request(studio, cookie)[0] == 400
    finally: studio.close()


@pytest.mark.asyncio
async def test_http_stop_resume_keeps_single_component_copy_and_no_duplicate_writes(tmp_path, monkeypatch):
    from test_transfer_outline_runner import setup
    from transfer_outline_runner import OutlineRunner
    source, target, plan, path, copies = await setup(tmp_path, monkeypatch)
    requests = []
    studio = TransferStudio(tmp_path, lambda r: None, saved_path=path, request_create=requests.append)
    try:
        cookie = login(studio); create_request(studio, cookie)
        def stop_after_copy(message, level="info"):
            studio.run_log(message, level)
            if "Creating modules" in message:
                assert request(studio, "/api/stop", "POST", {}, cookie, {"Origin": studio.origin, "X-Studio-Request": "1"})[0] == 200
        with pytest.raises(InterruptedError):
            await OutlineRunner(source, target, requests.pop()["plan"], path, stop_after_copy, studio.stop_event).run("2", True)
        studio.finish_create(error="Stopped after current request")
        assert studio.snapshot()["run"]["status"] == "stopped" and studio.plan["run_signature"]
        assert create_request(studio, cookie)[0] == 202
        result = await OutlineRunner(source, target, requests.pop()["plan"], path, studio.run_log, studio.stop_event).run("2", True)
        studio.finish_create(result)
        assert studio.run_status == "complete" and len(copies) == 1 and not source.writes
        writes = len(target.writes)
        result = await OutlineRunner(source, target, load_plan(path), path).run("2", True)
        assert all(result["validation"].values()) and len(target.writes) == writes
        reopened = TransferStudio(tmp_path, lambda r: None, saved_path=path)
        assert reopened.snapshot()["run"]["status"] == "complete"
    finally: studio.close()


def test_saved_plan_library_open_and_path_boundary(studio):
    cookie = ready(studio)
    first_key = studio.path.stem
    scan(studio, cookie, start="empty"); studio.finish_scan((source_course(), target_course()))
    status, _, data = request(studio, "/api/plans", cookie=cookie)
    plans = json.loads(data)["plans"]
    assert status == 200 and len(plans) == 2
    headers = {"Origin": studio.origin, "X-Studio-Request": "1"}
    assert request(studio, "/api/open", "POST", {"key": "../outside"}, cookie, headers)[0] == 400
    assert request(studio, "/api/open", "POST", {"key": first_key}, cookie, headers)[0] == 200
    assert studio.plan["start"] == "mirror"
    studio.running = True
    assert request(studio, "/api/open", "POST", {"key": first_key}, cookie, headers)[0] == 409
    studio.running = False
