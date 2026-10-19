"""Local, authenticated Transfer Studio backed by the existing transfer engine.

HTTP threads never touch Qt or Playwright. A scan request is queued to the app,
which runs its existing browser worker and returns inventories via finish_scan.
"""
from __future__ import annotations

import copy
import json
import secrets
import sys
import threading
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import transfer_outline as outline
from bulk_page_creator import course_id_from_url
from transfer_runner import PlanLock, load_plan, save_plan

HOST = "https://learn.okanagancollege.ca"
MAX_BODY = 131_072
HISTORY_KEYS = ("outline", "items", "skipped", "skipped_units", "unsplit", "templates", "styling", "grade_categories", "activity_link_targets", "review_acknowledged")
HISTORY_LIMIT = 50


def course_id(url):
    parsed = urlsplit(url.strip())
    if parsed.path.startswith("/d2l/home/"):
        # Use the existing validator for host, scheme, port and numeric IDs.
        url = parsed._replace(path=parsed.path.replace("/d2l/home/", "/d2l/le/lessons/", 1)).geturl()
    return course_id_from_url(url)


def asset_directory():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / "transfer-studio"
    return Path(__file__).resolve().parents[1] / "assets" / "transfer-studio"


class StudioConflict(ValueError):
    pass


class UnitSkipConfirmation(ValueError):
    def __init__(self, review):
        self.review = review
        super().__init__("Skipping this unit also removes personal edits from its draft module. Review them first.")


class CourseLinkError(ValueError):
    def __init__(self, field, reason):
        self.field = field
        super().__init__(("Copy from: " if field == "source_url" else "Copy into: ") + str(reason))


class TransferStudio:
    def __init__(self, data_dir, request_scan, source_url="", target_url="", saved_path=None, request_create=None, on_open=None):
        self.data_dir = Path(data_dir)
        self.request_scan = request_scan
        self.request_create = request_create
        self.on_open = on_open
        self.source_url, self.target_url = source_url, target_url
        self.plan = self.path = None
        self.scanning = False
        self.error = ""
        self.messages = []
        self._pending = None
        self._lock = threading.RLock()
        self._launch_tokens = set()
        self._sessions = set()
        self._closed = False
        self._server = None
        self._thread = None
        self._undo, self._redo = [], []
        self.revision = 0
        self.app_busy = False
        self._disk_stamp = None
        self.running = False
        self.stop_event = threading.Event()
        self.run_status = "idle"
        self.run_messages = []
        self.phase = 0
        self.run_sequence = 0
        self.error_scope = "connect"
        self._looks_cache = (None, [])
        if saved_path:
            try:
                plan = load_plan(saved_path)
                if outline.is_outline(plan):
                    self.plan, self.path = plan, Path(saved_path)
                    self._disk_stamp = self.path.stat().st_mtime_ns
                    self.source_url = f"{HOST}/d2l/home/{plan['source']['id']}"
                    self.target_url = f"{HOST}/d2l/home/{plan['target']['id']}"
                    self.run_status = self._saved_status(plan)
            except (OSError, ValueError, KeyError):
                self.error = "The saved Studio draft could not be opened. Scan both courses to start again."

    def start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Transfer Studio has closed.")
            if self._server is None:
                self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
                self._server.daemon_threads = True
                self._server.studio = self
                self.origin = f"http://127.0.0.1:{self._server.server_port}"
                self.cookie_name = f"studio_{self._server.server_port}"
                self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
                self._thread.start()
            # Outstanding launches are invalidated when the app opens a new tab.
            token = secrets.token_urlsafe(32)
            self._launch_tokens = {token}
            return f"{self.origin}/?token={token}"

    def close(self):
        with self._lock:
            self._closed = True
            self.stop_event.set()
            server, self._server = self._server, None
            self._sessions.clear()
            self._launch_tokens.clear()
        if server:
            server.shutdown()
            server.server_close()
            self._thread.join(timeout=2)

    def session(self, cookie):
        try:
            cookies = SimpleCookie(cookie or "")
            value = cookies.get(self.cookie_name)
            with self._lock:
                return bool(value and value.value in self._sessions and not self._closed)
        except CookieError:
            return False

    def redeem(self, token):
        with self._lock:
            if not token or token not in self._launch_tokens or self._closed:
                return None
            self._launch_tokens.remove(token)
            session = secrets.token_urlsafe(32)
            self._sessions.add(session)
            return session

    def scan(self, body):
        with self._lock:
            if self.scanning or self.running or self.app_busy:
                raise StudioConflict("The app is already working. Wait for it to finish.")
            source_url, target_url = body.get("source_url"), body.get("target_url")
            if not isinstance(source_url, str) or not isinstance(target_url, str):
                raise ValueError("Enter both Brightspace course links.")
            ids = []
            for field, url in (("source_url", source_url), ("target_url", target_url)):
                try: ids.append(course_id(url))
                except ValueError as exc: raise CourseLinkError(field, exc) from exc
            source_id, target_id = ids
            if source_id == target_id:
                raise ValueError("Copy from and Copy into must be different courses.")
            start = body.get("start", "mirror")
            if start not in outline.STARTS:
                raise ValueError("Choose a starting point.")
            pattern_name = body.get("pattern", "")
            if not isinstance(pattern_name, str):
                raise ValueError("Choose a pattern.")
            self.source_url, self.target_url = source_url.strip(), target_url.strip()
            self._pending = (start, pattern_name)
            self.scanning, self.error, self.messages = True, "", ["Waiting for the desktop app to scan both courses…"]
            self.error_scope = "connect"
            try:
                self.request_scan({"source_id": source_id, "target_id": target_id, "url": self.source_url})
            except Exception:
                self.scanning = False
                self._pending = None
                raise

    def log(self, message, level="info"):
        with self._lock:
            self.messages = [*self.messages[-49:], str(message)]

    def finish_scan(self, inventories=None, error=None):
        with self._lock:
            if self._closed or not self.scanning:
                return
            try:
                if error:
                    raise ValueError(error)
                source, target = inventories
                start, name = self._pending
                patterns = outline.pattern_choices(source, target, outline.load_patterns(self.data_dir / "patterns.json"))
                pattern = next((p for p in patterns if p["name"] == name), None)
                if start == "pattern" and pattern is None:
                    raise ValueError("That pattern is no longer available. Choose another and scan again.")
                plan = outline.build_outline_plan(source, target, start, pattern)
                outline.ensure_templates(plan, pattern)
                path = self.data_dir / (plan["id"] + ".json")
                save_plan(plan, path)
                self.plan, self.path = plan, path
                self._disk_stamp = path.stat().st_mtime_ns
                self._undo.clear(); self._redo.clear(); self.revision += 1
                self.run_status, self.run_messages, self.phase = "idle", [], 0
                self.messages.append("Both courses scanned. Your draft is saved; nothing changed in Brightspace.")
                self.error = ""
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.scanning = False
                self._pending = None

    @staticmethod
    def _saved_status(plan):
        if plan.get("report") and plan.get("validation") and all(plan["validation"].values()): return "complete"
        return "paused" if plan.get("run_signature") else "idle"

    def _looks(self):
        from transfer_styling import template_choices
        key = (self.plan["id"], self.plan["source_hash"])
        if self._looks_cache[0] != key:
            self._looks_cache = (key, template_choices(self.plan["target"], self.plan["source"]))
        return self._looks_cache[1]

    @staticmethod
    def _look_key(look):
        return str(look.get("course", "builtin")) + ":" + look["id"]

    def page_preview(self, row_id):
        from transfer_outline_runner import styled_markup, generated_markup
        from transfer_styling import piece_document, LABELS
        with self._lock:
            self._sync_disk()
            if self.plan and row_id.startswith("node:"):
                node, parent, _ = outline.find(self.plan, row_id[5:])
                markup = generated_markup(self.plan, node, parent)
                return {"title": node["title"], "before": markup, "after": markup, "summary": "Generated page uses its chosen template. Edit its text in Arrange.",
                        "ok": True, "templated": False, "removed": [], "kept": ["The generated page text and template"], "mode": "preserve"}
            if not self.plan or row_id not in outline.placements(self.plan): raise ValueError("Choose a placed page.")
            row = self.plan["items"][row_id]
            if row["kind"] != "page": raise ValueError("Choose a page.")
            original = outline.source_of(self.plan, row).get("html", "")
            if row.get("piece"): original = piece_document(original, row["piece"]["html"], row["title"])
            result = styled_markup(self.plan, row)
            return {"title": row["title"], "before": original, "after": result.html,
                    "summary": result.summary, "ok": result.ok, "templated": result.templated,
                    "removed": [f"{n} {LABELS.get(k, k)}" for k, n in result.removed.items()],
                    "kept": ["Words, headings, lists and tables", "Links, images, media and embeds", "Accessibility attributes and math"],
                    "mode": result.mode}

    def preview_document(self, row_id, side):
        """An isolated visual preview: no scripts, credentialed requests or links."""
        from bs4 import BeautifulSoup
        if side not in ("before", "after"): raise ValueError("Choose Before or After.")
        soup = BeautifulSoup(self.page_preview(row_id)[side], "html.parser")
        for tag in soup.select("script,base,meta,link,form,object,embed,iframe"):
            if tag.name in ("iframe", "object", "embed"):
                note = soup.new_tag("p"); note.string = "[Media/embed is kept in the transfer; disabled in this preview.]"; tag.replace_with(note)
            else: tag.decompose()
        for tag in soup.find_all(True):
            for attr in list(tag.attrs):
                if attr.lower().startswith("on") or attr.lower() in ("href", "srcdoc", "action", "formaction", "srcset", "background", "poster"): del tag[attr]
            if tag.has_attr("src") and not (tag.name == "img" and str(tag["src"]).startswith("data:image/")):
                del tag["src"]
        return str(soup).encode("utf-8")

    def create(self, body):
        with self._lock:
            if not self.plan or not self.request_create: raise ValueError("Open Transfer Studio from the desktop app to create this transfer.")
            if self.running or self.scanning or self.app_busy: raise StudioConflict("The app is already working.")
            with PlanLock(self.path):
                self._sync_disk()
                if body.get("plan_id") != self.plan["id"] or body.get("revision") != self.revision: raise StudioConflict("The draft changed. Review it again before creating.")
                if body.get("confirmed") is not True or body.get("target_id") != self.plan["target"]["id"]: raise ValueError("Confirm the named destination course first.")
                errors, _ = outline.validate_outline(self.plan)
                if errors: raise ValueError("Fix the blocking issues before creating: " + " ".join(errors))
                if self._saved_status(self.plan) == "complete": raise ValueError("This transfer is already complete. Open the report or the destination.")
                self.plan["review_acknowledged"] = True
                save_plan(self.plan, self.path); self._disk_stamp = self.path.stat().st_mtime_ns
                self.stop_event.clear()
                self.running, self.run_status, self.run_messages, self.phase, self.run_sequence = True, "queued", [], 0, 0
                self.error, self.error_scope = "", "create"
                self._undo.clear(); self._redo.clear(); self.revision += 1
                request = {"plan": copy.deepcopy(self.plan), "path": self.path, "target_id": self.plan["target"]["id"]}
            # Release the file lock before the browser worker acquires it.
            try: self.request_create(request)
            except Exception:
                self.running = False; self.run_status = "failed"
                raise

    def run_log(self, message, level="info"):
        with self._lock:
            text = str(message)
            self.run_status = "stopping" if self.stop_event.is_set() else "running"
            for index, phrase in enumerate(("Preparing pages", "Copying activities", "Creating modules", "Adding pages", "Reading everything"), 1):
                if phrase in text: self.phase = max(self.phase, index)
            if text.startswith("Verified: "):
                key = text[len("Verified: "):]
                label = self._readable_check(key)
                text = "Checked: " + ("transfer course-file folder" if key == "files-folder" else "copied activities and course files" if key == "components" else "supporting file" if key.startswith("dependency:") else label)
            self.run_sequence += 1
            self.run_messages = [*self.run_messages[-499:], {"text": text, "level": level, "sequence": self.run_sequence}]

    def _readable_check(self, label):
        key, separator, detail = label.partition(" / ")
        file_check = key.endswith(":file")
        base = key[:-5] if file_check else key
        if self.plan and base.startswith("node:"):
            try:
                node = outline.find(self.plan, base[5:])[0]
                key = ("file for “" if file_check else "“") + node.get("title", "page") + "”"
            except KeyError: pass
        elif self.plan and base.startswith("item:"):
            row = self.plan["items"].get(base[5:])
            if row: key = ("file for “" if file_check else "“") + row["title"] + "”"
        return key + separator + detail

    def stop(self):
        with self._lock:
            if self.running: self.stop_event.set(); self.run_status = "stopping"

    def finish_create(self, result=None, error=None):
        with self._lock:
            if self.path and self.path.exists(): self.plan = load_plan(self.path)
            if result is not None: self.plan = result
            self._disk_stamp = self.path.stat().st_mtime_ns
            self.running, self.app_busy = False, False
            self.run_status = "stopped" if error and self.stop_event.is_set() else "failed" if error else self._saved_status(self.plan)
            if not error and self.run_status != "complete": self.run_status = "review"
            self.error = str(error) if error and self.run_status != "stopped" else ""
            self.error_scope = "create"
            if self.run_status == "complete": self.phase = 6
            self.revision += 1

    def saved_plans(self):
        plans = []
        paths = sorted(self.data_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
        for path in paths:
            if path.resolve().parent != self.data_dir.resolve(): continue
            try:
                plan = load_plan(path)
                if outline.is_outline(plan): plans.append({"key": path.stem, "label": plan["source"]["name"] + " → " + plan["target"]["name"], "status": self._saved_status(plan), "updated": path.stat().st_mtime})
            except (OSError, ValueError, AttributeError, KeyError): continue
        return plans

    def open_plan(self, body):
        with self._lock:
            if self.running or self.scanning or self.app_busy: raise StudioConflict("Wait for the app to finish before opening another transfer.")
            key = body.get("key")
            if not isinstance(key, str) or not key.strip() or len(key) > 120 or any(c in key for c in "/\\:") or key in (".", ".."): raise ValueError("Choose a saved transfer.")
            path = self.data_dir / (key + ".json")
            if path.resolve().parent != self.data_dir.resolve(): raise ValueError("Choose a transfer in the saved-plan folder.")
            try: plan = load_plan(path)
            except (AttributeError, ValueError): raise ValueError("This file is not a saved transfer.") from None
            if not outline.is_outline(plan): raise ValueError("This older plan opens in the in-app planner.")
            self.plan, self.path = plan, path
            self._disk_stamp = path.stat().st_mtime_ns
            self.source_url = f"{HOST}/d2l/home/{plan['source']['id']}"
            self.target_url = f"{HOST}/d2l/home/{plan['target']['id']}"
            self.run_status, self.run_messages, self.phase = self._saved_status(plan), [], 0
            self._undo.clear(); self._redo.clear(); self.error = ""; self.revision += 1
            if self.on_open: self.on_open(path)

    def _sync_disk(self):
        """A desktop edit or run invalidates browser history, never its journal."""
        if self.path and self.path.exists():
            stamp = self.path.stat().st_mtime_ns
            if stamp != self._disk_stamp:
                plan = load_plan(self.path)
                if not outline.is_outline(plan) or plan["id"] != self.plan["id"]:
                    raise StudioConflict("The saved draft changed. Reopen Plan transfer.")
                if plan == self.plan:
                    self._disk_stamp = stamp
                    return
                self.plan, self._disk_stamp = plan, stamp
                if not self.running: self.run_status = self._saved_status(plan)
                self._undo.clear(); self._redo.clear(); self.revision += 1

    def _selection(self, plan, entries):
        if not isinstance(entries, list) or not entries or len(entries) > 1000:
            raise ValueError("Select one or more items first.")
        if any(not isinstance(e, dict) or e.get("side") not in ("source", "destination") or not isinstance(e.get("id"), str) for e in entries):
            raise ValueError("Invalid selection.")
        if len({e["side"] for e in entries}) != 1:
            raise ValueError("Select items in one column at a time.")
        side = entries[0]["side"]
        ids = list(dict.fromkeys(e["id"] for e in entries))
        if side == "destination":
            # Keep only outermost selected nodes, so a parent and its children
            # are never removed or moved twice during a single transaction.
            parents = {n["id"]: parent["id"] if parent else None for n, parent, _ in outline.walk(plan["outline"])}
            if any(id_ not in parents for id_ in ids): raise ValueError("An outline item no longer exists.")
            selected = set(ids)
            outermost = []
            for id_ in ids:
                parent = parents[id_]
                while parent and parent not in selected: parent = parents[parent]
                if parent is None: outermost.append(id_)
            return side, outermost
        objects = outline.source_objects(plan)
        if any(id_ not in plan["items"] and id_ not in objects for id_ in ids):
            raise ValueError("An item is no longer in this scan.")
        return side, ids

    def _source_rows(self, plan, ids):
        rows = []
        objects = outline.source_objects(plan)
        for id_ in ids:
            if id_ in plan["items"]:
                rows.append(id_)
            else:
                obj = objects[id_]
                source_ids = {str(n["Id"]) for n in self._objects_below(obj) if n.get("Type") == 1}
                rows.extend(r["id"] for r in plan["items"].values() if r["source_id"] in source_ids)
        return list(dict.fromkeys(rows))

    def edit(self, body):
        """Atomic, version-checked edits using the existing outline rules."""
        with self._lock:
            if not self.plan or not self.path:
                raise ValueError("Scan both courses first.")
            if self.scanning or self.running or self.app_busy:
                raise StudioConflict("The app is busy. Wait for the current task to finish.")
            with PlanLock(self.path):
                self._sync_disk()
                if body.get("plan_id") != self.plan["id"] or body.get("revision") != self.revision:
                    raise StudioConflict("This draft changed in another window. Your view has been refreshed; try again.")
                if self.plan.get("run_signature"):
                    raise StudioConflict("This transfer has already been created; its draft cannot change.")
                action = body.get("action")
                before = {k: copy.deepcopy(self.plan[k]) for k in HISTORY_KEYS if k in self.plan}
                working = {**self.plan, **copy.deepcopy(before)}
                undo, redo = list(self._undo), list(self._redo)
                if action in ("undo", "redo"):
                    stack, other = (undo, redo) if action == "undo" else (redo, undo)
                    if not stack: raise ValueError(f"Nothing to {action}.")
                    saved, label = stack.pop()
                    other.append((before, label))
                    working.update(copy.deepcopy(saved))
                    for key in HISTORY_KEYS:
                        if key not in saved: working.pop(key, None)
                    message = ("Undid: " if action == "undo" else "Redid: ") + label
                else:
                    if action in ("add_module", "accept_suggestions", "styling", "grade_categories"):
                        side, ids = None, []
                    else:
                        side, ids = self._selection(working, body.get("entries"))
                    args = body.get("args", {})
                    if not isinstance(args, dict): raise ValueError("Invalid options.")
                    if action == "remove":
                        if side != "destination": raise ValueError("Remove items from the destination outline.")
                        for id_ in ids: outline.remove(working, id_)
                        message = f"Removed {len(ids)} {'item' if len(ids) == 1 else 'items'} from the outline"
                    elif action in ("skip", "include"):
                        if side != "source": raise ValueError("Choose items in the source column.")
                        rows = self._source_rows(working, ids)
                        units = [id_ for id_ in ids if outline.source_objects(working).get(id_, {}).get("Type") == 0]
                        unit_ids = {str(o["Id"]) for id_ in units for o in self._objects_below(outline.source_objects(working)[id_]) if o.get("Type") == 0}
                        removal = {"modules": [], "protected": []}
                        if action == "skip":
                            removal = outline.unit_skip_review(working, units, rows)
                            if removal["protected"] and args.get("confirmed_custom") is not True:
                                raise UnitSkipConfirmation(removal)
                        if action == "include":
                            rows = [id_ for id_ in rows if working["items"][id_]["method"] != "manual"]
                        (outline.skip if action == "skip" else outline.unplace)(working, rows)
                        if action == "skip": working["skipped_units"] = sorted(set(working.get("skipped_units", [])) | unit_ids)
                        else: working["skipped_units"] = [id_ for id_ in working.get("skipped_units", []) if id_ not in unit_ids]
                        for module in removal["modules"]: outline.remove(working, module["id"])
                        message = ("Left out " if action == "skip" else "Included ") + str(len(rows)) + " source items"
                        if removal["modules"]: message += f" and removed {len(removal['modules'])} linked draft unit(s)"
                    elif action == "rename":
                        if side != "destination" or len(ids) != 1: raise ValueError("Choose one destination item to rename.")
                        outline.rename(working, ids[0], args.get("title"))
                        message = "Renamed an outline item"
                    elif action == "reorder":
                        if side != "destination" or len(ids) != 1 or args.get("direction") not in ("up", "down"): raise ValueError("Choose one outline item and direction.")
                        node, parent, siblings = outline.find(working, ids[0])
                        position = siblings.index(node)
                        outline.move(working, ids[0], parent["id"] if parent else None, position - 1 if args["direction"] == "up" else position + 2)
                        message = "Reordered the outline item"
                    elif action == "add_module":
                        outline.add_module(working, args.get("parent"), args.get("title", "New module"))
                        message = "Added a folder" if args.get("parent") else "Added a module"
                    elif action == "place":
                        parent = args.get("parent")
                        if parent is not None and not isinstance(parent, str): raise ValueError("Choose a destination folder.")
                        if parent:
                            container = outline.find(working, parent)[0]
                            if container["kind"] != "module": raise ValueError("Choose a destination folder.")
                            siblings = container["children"]
                        else:
                            siblings = working["outline"]
                        before_id = args.get("before")
                        if before_id is not None:
                            if not isinstance(before_id, str) or before_id not in {n["id"] for n in siblings}:
                                raise ValueError("The insertion position is no longer in that folder.")
                            # If the drop boundary belongs to a selected node, use
                            # the next stationary sibling as a stable anchor.
                            selected_rows = set(self._source_rows(working, ids)) if side == "source" else set()
                            start = next(i for i, n in enumerate(siblings) if n["id"] == before_id)
                            before_id = next((n["id"] for n in siblings[start:] if
                                              not (side == "destination" and n["id"] in ids) and
                                              not (n["kind"] == "item" and n["item"] in selected_rows)), None)
                        def insertion_index():
                            return next(i for i, n in enumerate(siblings) if n["id"] == before_id) if before_id else None
                        if side == "source":
                            objects = outline.source_objects(working)
                            # Copy folders with their hierarchy; place loose rows together.
                            folder_ids = [id_ for id_ in ids if id_ not in working["items"] and objects[id_].get("Type") == 0]
                            folder_ids = [id_ for id_ in folder_ids if not any(id_ != other and any(str(o["Id"]) == id_ for o in self._objects_below(objects[other])) for other in folder_ids)]
                            covered = {str(o["Id"]) for id_ in folder_ids for o in self._objects_below(objects[id_])}
                            loose = [id_ for id_ in ids if id_ in working["items"] and working["items"][id_]["source_id"] not in covered]
                            for id_ in folder_ids:
                                node = outline.place_source_module(working, id_, parent)
                                if before_id: outline.move(working, node["id"], parent, insertion_index())
                            if loose:
                                if parent is None: raise ValueError("Choose a module for these items.")
                                outline.place(working, loose, parent, insertion_index())
                        else:
                            if parent in ids: raise ValueError("A folder cannot move inside itself.")
                            # Preserve the outline order, even when selection order differs.
                            ordered = [n["id"] for n, _, _ in outline.walk(working["outline"]) if n["id"] in ids]
                            for id_ in ordered: outline.move(working, id_, parent, insertion_index())
                        message = "Placed the selection in the outline"
                    elif action == "use_existing":
                        if side != "destination": raise ValueError("Choose a destination module.")
                        for id_ in ids: outline.use_existing(working, id_)
                        message = "Added into the existing module"
                    elif action == "accept_suggestions":
                        outline.accept_suggestions(working)
                        message = "Accepted suggested placements"
                    elif action == "styling":
                        from transfer_styling import MODES
                        mode = args.get("mode", working["styling"]["mode"])
                        if mode not in MODES: raise ValueError("Choose a styling mode.")
                        styling = working["styling"]
                        styling["mode"] = mode
                        if "apply_template" in args:
                            if not isinstance(args["apply_template"], bool): raise ValueError("Choose whether to apply a look.")
                            styling["apply_template"] = args["apply_template"]
                        if "look" in args:
                            look = next((p for p in self._looks() if self._look_key(p) == args["look"]), None)
                            if look is None: raise ValueError("Choose an available look.")
                            styling["template"] = copy.deepcopy(look)
                        if mode == "preserve": styling["apply_template"] = False
                        if styling.get("apply_template") and not styling.get("template"): raise ValueError("Choose a look first.")
                        if "row" in args:
                            row = working["items"].get(args["row"])
                            override = args.get("override")
                            if not row or row["kind"] != "page" or override not in (*MODES, None): raise ValueError("Choose a page and styling override.")
                            if override is None: styling["overrides"].pop(row["id"], None)
                            else: styling["overrides"][row["id"]] = override
                        message = "Updated page styling"
                    elif action == "apply_pattern":
                        if side != "destination" or len(ids) != 1: raise ValueError("Choose one module.")
                        choices = outline.pattern_choices(working["source"], working["target"], outline.load_patterns(self.data_dir / "patterns.json"))
                        pattern = next((p for p in choices if p["name"] == args.get("pattern")), None)
                        if pattern is None: raise ValueError("Choose a saved layout.")
                        outline.apply_pattern(working, ids[0], pattern)
                        message = "Applied the section layout"
                    elif action == "save_pattern":
                        if side != "destination" or len(ids) != 1: raise ValueError("Choose one module.")
                        pattern = outline.pattern_from_module(working, ids[0], args.get("title"))
                        saved = [p for p in outline.load_patterns(self.data_dir / "patterns.json") if outline.norm(p["name"]) != outline.norm(pattern["name"])]
                        save_plan([pattern, *saved], self.data_dir / "patterns.json")
                        return "Saved the layout “" + pattern["name"] + "”"
                    elif action == "add_page":
                        if side != "destination" or len(ids) != 1: raise ValueError("Choose a module for the page.")
                        outline.add_page(working, ids[0], args.get("kind"), args.get("title"), message=args.get("message"))
                        message = "Added a generated page"
                    elif action == "set_text":
                        if side != "destination" or len(ids) != 1: raise ValueError("Choose one generated page.")
                        outline.set_text(working, ids[0], args.get("introduction", ""), args.get("outcomes", []), args.get("session", ""))
                        message = "Updated the generated page text"
                    elif action == "join_page":
                        if side != "source" or len(ids) != 1: raise ValueError("Choose one split page part.")
                        outline.join_page(working, working["items"][ids[0]]["source_id"])
                        message = "Kept the source page whole"
                    elif action == "set_sources":
                        if side != "destination" or len(ids) != 1: raise ValueError("Choose one module.")
                        sources = args.get("sources")
                        if not isinstance(sources, list) or any(not isinstance(id_, str) or outline.source_objects(working).get(id_, {}).get("Type") != 0 for id_ in sources): raise ValueError("Choose source modules.")
                        outline.set_sources(working, ids[0], sources)
                        message = "Updated the module's source suggestions"
                    elif action == "grade_categories":
                        if args.get("mode") not in ("merge", "keep"): raise ValueError("Choose how to handle grade categories.")
                        working["grade_categories"] = args["mode"]
                        message = "Updated grade category handling"
                    elif action == "use_existing_link":
                        from transfer_activity_links import existing_assignment
                        if side != "source" or len(ids) != 1 or working["items"].get(ids[0], {}).get("kind") != "assignment":
                            raise ValueError("Choose one source assignment link.")
                        row = working["items"][ids[0]]
                        source_id = str(outline.source_of(working, row).get("ToolItemId"))
                        target_id = args.get("target_id")
                        if not isinstance(target_id, str): raise ValueError("Choose a destination assignment.")
                        existing_assignment(working, source_id, target_id)
                        working.setdefault("activity_link_targets", {})[source_id] = target_id
                        message = "Linked pages to the existing destination assignment"
                    else:
                        raise ValueError("Unknown edit action.")
                    after = {k: working[k] for k in HISTORY_KEYS if k in working}
                    if before == after: return "The draft is already set up that way."
                    undo = [*undo[-(HISTORY_LIMIT - 1):], (before, message)]
                    redo = []
                working["review_acknowledged"] = False
                save_plan(working, self.path)
                self.plan = working
                self._disk_stamp = self.path.stat().st_mtime_ns
                self._undo, self._redo = undo, redo
                self.revision += 1
                self.error = ""
                return message

    @staticmethod
    def _objects_below(obj):
        yield obj
        for child in obj.get("children", []):
            yield from TransferStudio._objects_below(child)

    def snapshot(self):
        """Only display data crosses HTTP: no page HTML, auth, files or journals."""
        with self._lock:
            self._sync_disk()
            plan = self.plan
            patterns = outline.pattern_choices(plan["source"], plan["target"], outline.load_patterns(self.data_dir / "patterns.json")) if plan else outline.load_patterns(self.data_dir / "patterns.json") + [outline.ea105_pattern(), outline.simple_pattern()]
            result = {"source_url": self.source_url, "target_url": self.target_url,
                      "scanning": self.scanning, "error": self.error, "messages": list(self.messages),
                      "patterns": [{"name": p["name"], "sections": [s["title"] for s in p["sections"]]} for p in patterns],
                      "plan": None, "revision": self.revision, "busy": self.app_busy,
                      "history": {"undo": self._undo[-1][1] if self._undo else "", "redo": self._redo[-1][1] if self._redo else ""}}
            result.update(error_scope=self.error_scope, run={"active": self.running, "status": self.run_status, "phase": self.phase,
                          "messages": list(self.run_messages), "stop_requested": self.stop_event.is_set()})
            if plan is None:
                return result
            placed, skipped = outline.placements(plan), set(plan["skipped"])
            skipped_units = set(plan.get("skipped_units", []))
            def nodes(entries):
                answer = []
                for node in entries:
                    row = plan["items"].get(node.get("item"), {})
                    answer.append({"id": node["id"], "kind": row.get("kind", node["kind"]),
                                   "title": row.get("title", node.get("title", "")),
                                   "existing": bool(node.get("existing_id")), "children": nodes(node.get("children", []))})
                    answer[-1]["row_id"] = node.get("item")
                    answer[-1]["renamable"] = not node.get("existing_id") and (node["kind"] != "item" or row.get("method") == "content")
                    answer[-1]["text"] = node.get("text")
                    answer[-1]["template"] = node.get("template")
                    answer[-1]["sources"] = node.get("sources", [])
                return answer
            rows_by_source = {}
            for row in plan["items"].values():
                rows_by_source.setdefault(row["source_id"], []).append(row)
            def source_nodes(entries):
                answer = []
                for obj in entries:
                    if obj.get("Type") == 0:
                        children = source_nodes(obj.get("children", []))
                        total = sum(child.get("item_count", 1) for child in children)
                        left_out = sum(child.get("skipped_count", int(child.get("status") == "Skipped")) for child in children)
                        answer.append({"id": str(obj["Id"]), "title": obj["Title"], "kind": "module", "children": children,
                                       "item_count": total, "skipped_count": left_out,
                                       "unit_skipped": str(obj["Id"]) in skipped_units,
                                       "status": "Skipped" if str(obj["Id"]) in skipped_units or total and left_out == total else "Partly skipped" if left_out else ""})
                    else:
                        for row in rows_by_source.get(str(obj["Id"]), []):
                            answer.append({"id": row["id"], "title": row["title"], "kind": row["kind"], "children": [],
                                           "status": "Skipped" if row["id"] in skipped else "Placed" if row["id"] in placed else "Unplaced",
                                           "suggestion": row.get("reason", ""), "method": row["method"]})
                            answer[-1]["split"] = bool(row.get("piece"))
                return answer
            result["plan"] = {"id": plan["id"], "start": plan["start"],
                              "pattern": next((n["pattern"]["name"] for n in plan["outline"] if n.get("pattern")), ""),
                              "source": {k: plan["source"][k] for k in ("id", "name")},
                              "target": {k: plan["target"][k] for k in ("id", "name")},
                              "source_nodes": source_nodes(plan["source"]["modules"]), "outline": nodes(plan["outline"]),
                              "counts": {**outline.counts(plan), "unplaced": len(outline.unplaced(plan))},
                              "issues": outline.check(plan), "locked": bool(plan.get("run_signature"))}
            result["plan"].update(styling={k: copy.deepcopy(v) for k, v in plan["styling"].items() if k != "template"},
                look=self._look_key(plan["styling"]["template"]) if plan["styling"].get("template") else "",
                looks=[{"key": self._look_key(p), "label": p["label"]} for p in self._looks()],
                pages=[{"id": id_, "title": row["title"], "override": plan["styling"]["overrides"].get(id_)} for id_, row in plan["items"].items() if id_ in placed and row["kind"] == "page"],
                skipped=[{"title": plan["items"][id_]["title"], "kind": plan["items"][id_]["kind"], "manual": plan["items"][id_]["method"] == "manual"} for id_ in plan["skipped"] if id_ in plan["items"]],
                activities=[{"title": row["title"], "kind": row["kind"]} for id_, row in plan["items"].items() if id_ in placed and row["method"] == "components"],
                suggestions=sum(bool(r.get("suggested")) for r in outline.unplaced(plan)),
                grade_categories=plan.get("grade_categories", "merge"), report=plan.get("report", ""), validation={self._readable_check(k): v for k, v in plan.get("validation", {}).items()})
            result["plan"]["pages"].extend({"id": "node:" + n["id"], "title": n["title"], "override": None, "generated": True} for n, _, _ in outline.walk(plan["outline"]) if n["kind"] in ("placeholder", "template"))
            return copy.deepcopy(result)


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # In particular, never log the launch token.

    def _send(self, code, body, content_type="application/json", cookie=None, preview=False):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox" if preview else "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", f"{self.server.studio.cookie_name}={cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, allow_launch=False):
        studio = self.server.studio
        if self.headers.get("Host") != urlsplit(studio.origin).netloc:
            self._send(403, {"error": "Invalid host."}); return False, None
        origin = self.headers.get("Origin")
        if origin and origin != studio.origin or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._send(403, {"error": "Open Transfer Studio from the desktop app."}); return False, None
        query = parse_qs(urlsplit(self.path).query)
        # A URL bearing a token must redeem it, even if a session cookie exists.
        if allow_launch and "token" in query:
            token = query["token"][0] if len(query["token"]) == 1 else None
            session = studio.redeem(token)
            if session:
                return True, session
        elif studio.session(self.headers.get("Cookie")):
            return True, None
        self._send(403, {"error": "This launch link has expired. Use Plan transfer in the desktop app."})
        return False, None

    def do_GET(self):
        path = urlsplit(self.path).path
        ok, cookie = self._authorized(allow_launch=path == "/")
        if not ok:
            return
        if path == "/api/state":
            try: self._send(200, self.server.studio.snapshot())
            except (ValueError, OSError) as exc: self._send(409, {"error": str(exc)})
            return
        if path in ("/api/page", "/api/preview"):
            try:
                query = parse_qs(urlsplit(self.path).query)
                row = query.get("row", [""])[0]
                if path == "/api/page":
                    data = self.server.studio.page_preview(row)
                    data.pop("before"); data.pop("after")
                    self._send(200, data)
                else:
                    self._send(200, self.server.studio.preview_document(row, query.get("side", ["after"])[0]), "text/html; charset=utf-8", preview=True)
            except (ValueError, KeyError) as exc: self._send(400, {"error": str(exc)})
            return
        if path == "/api/report":
            state = self.server.studio.snapshot()
            self._send(200, (state.get("plan") or {}).get("report", "No report yet.").encode("utf-8"), "text/plain; charset=utf-8")
            return
        if path == "/api/plans":
            self._send(200, {"plans": self.server.studio.saved_plans()}); return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/HankenGrotesk.ttf": ("HankenGrotesk.ttf", "font/ttf"),
                  "/studio.css": ("studio.css", "text/css; charset=utf-8"),
                  "/studio-workflow.js": ("studio-workflow.js", "text/javascript; charset=utf-8"),
                  "/studio.js": ("studio.js", "text/javascript; charset=utf-8")}
        if path not in assets:
            self._send(404, {"error": "Not found."}); return
        name, mime = assets[path]
        try:
            body = (asset_directory() / name).read_bytes()
        except OSError:
            self._send(500, {"error": "Transfer Studio assets are missing from this installation."}); return
        self._send(200, body, mime, cookie)

    def do_POST(self):
        # Drain bounded request bodies before rejecting them. Closing a socket
        # with unread bytes can discard the 403 response on Windows.
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request size.")
            raw = self.rfile.read(length)
        except (ValueError, OSError) as exc:
            self._send(400, {"error": str(exc)}); return
        ok, _ = self._authorized()
        if not ok:
            return
        if self.headers.get("Origin") != self.server.studio.origin or self.headers.get("X-Studio-Request") != "1":
            self._send(403, {"error": "Invalid request origin."}); return
        route = urlsplit(self.path).path
        if route not in ("/api/scan", "/api/edit", "/api/create", "/api/stop", "/api/open"):
            self._send(404, {"error": "Not found."}); return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Send a JSON request.")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Send a JSON object.")
            if route == "/api/scan":
                self.server.studio.scan(body)
                self._send(202, {"scanning": True})
            elif route == "/api/edit":
                message = self.server.studio.edit(body)
                self._send(200, {"message": message, "state": self.server.studio.snapshot()})
            elif route == "/api/create":
                self.server.studio.create(body)
                self._send(202, {"state": self.server.studio.snapshot()})
            elif route == "/api/open":
                self.server.studio.open_plan(body)
                self._send(200, {"state": self.server.studio.snapshot()})
            else:
                self.server.studio.stop()
                self._send(200, {"state": self.server.studio.snapshot()})
        except CourseLinkError as exc:
            self._send(400, {"error": str(exc), "field": exc.field, "scope": "connect"})
        except UnitSkipConfirmation as exc:
            self._send(409, {"error": str(exc), "confirmation": exc.review})
        except StudioConflict as exc:
            self._send(409, {"error": str(exc)})
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            self._send(500, {"error": "The desktop app could not start the scan. Try opening Studio again."})
