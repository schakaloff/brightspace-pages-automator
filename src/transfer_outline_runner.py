"""Create a general transfer plan in the destination. Additive, hidden, journaled, resumable.

Order of work, so nothing is written until everything is ready:
1. Prepare every page, file and template locally (styling cleanup included) and
   check that every course-file and activity link can be rewritten.
2. Copy the planned activities with Brightspace's Copy Components (one job).
3. Create the destination modules, then link the copied activities into them.
4. Upload files and pages, put each module's children in the planned order.
5. Read everything back and write the report.

Existing destination modules are only added to, never renamed, moved or
reordered. Nothing is deleted. Every write is journaled so an interrupted run
resumes without duplicates.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit

from transfer_files import placeholder, reference_key, render_template, transform_references, unlink_moodle_placeholders
from transfer_outline import counts, source_of, validate_outline, walk
from transfer_plan import all_objects, digest, rubric_signature
from transfer_runner import TransferRequestRejected, TransferRunner, save_plan, source_inventory_matches
from transfer_styling import MODES, piece_document, style_page

LINK_TOOLS = {"assignment": ("ASSIGN", 3, "Assignments", "Assignment"), "quiz": ("QUIZ", 4, "Quizzes", "Quiz"),
              "discussion": ("DISCUSS", 6, "Discussions", "Discussion"), "survey": ("SURVEY", 12, "Surveys", "Survey")}
PLACEHOLDER_PATH = "/bpa-transfer-assignment/"
SAME_FILE_CHECK_LIMIT = 5_000_000          # files up to this size are compared byte for byte
DOWNLOAD_LIMIT = 128 * 1024 * 1024         # largest file the app itself will move


def outline_signature(plan):
    fields = {"source": plan["source_hash"], "target": plan["target"]["id"], "outline": plan["outline"],
                   "items": plan["items"], "skipped": plan["skipped"], "styling": plan["styling"],
                   "grade_categories": plan.get("grade_categories", "merge"),
                   "templates": {k: v.get("id") for k, v in plan.get("templates", {}).items()}}
    if plan.get("activity_link_targets"): fields["activity_link_targets"] = plan["activity_link_targets"]
    return digest(fields)


def _row_style(plan, row):
    styling = plan.get("styling", {})
    mode = styling.get("overrides", {}).get(row["id"], styling.get("mode", "preserve"))
    if mode not in MODES: raise ValueError(f"{row['title']}: unknown styling option.")
    template = styling.get("template") if styling.get("apply_template") and mode != "preserve" else None
    return mode, (template or {}).get("html")


def styled_markup(plan, row):
    """The page HTML that will be created for one row, and what styling did to it."""
    obj = source_of(plan, row)
    markup = piece_document(obj.get("html", ""), row["piece"]["html"], row["title"]) if row.get("piece") else obj.get("html", "")
    mode, template = _row_style(plan, row)
    return style_page(markup, mode, row["title"], template)


def generated_markup(plan, node, parent=None):
    """Shared generated-page rendering for creation and browser previews."""
    if node["kind"] == "placeholder": return placeholder(node["title"], node["message"])
    if node["kind"] != "template": raise ValueError("Choose a generated page.")
    pattern = node.get("pattern") or {}
    ancestor = parent
    from transfer_outline import find
    while ancestor:
        if ancestor.get("pattern"):
            pattern = ancestor["pattern"]; break
        ancestor = find(plan, ancestor["id"])[1]
    recipe = {"schema": 2, "sections": [{"title": (parent or {}).get("title", "this module"), "kind": "folder", "session_overview": True}],
              "remove_introduction_sections": pattern.get("remove_introduction_sections", []),
              "remove_session_sections": pattern.get("remove_session_sections", [])}
    unit = {"title": (parent or {}).get("title", node["title"]) if node["template"] == "introduction" else node["title"], **node.get("text", {})}
    return render_template(plan["templates"][node["template"]]["html"], node["template"], unit, recipe)


class OutlineRunner(TransferRunner):
    def __init__(self, source, target, plan, path, log=None, stop=None):
        super().__init__(source, target, plan, path, log, stop)
        self.notes = []
        self.styling_report = {}
        self.activity_ids = {}
        self.objects = {}

    # ── Preparation ────────────────────────────────────────────────────
    def placed_rows(self):
        return [self.plan["items"][n["item"]] for n, _, _ in walk(self.plan["outline"])
                if n["kind"] == "item" and n["item"] in self.plan["items"]]

    def page_markup(self, row):
        """A page's final HTML before links are rewritten: styled, with dead Moodle links unlinked."""
        result = styled_markup(self.plan, row)
        self.styling_report[row["id"]] = result.summary
        if not result.ok: self.notes.append(f"{row['title']}: {result.summary}")
        # Review already lists these pages (transfer_outline.check), so no second note here.
        markup, _ = unlink_moodle_placeholders(result.html)
        return markup

    async def plan_files(self):
        """Decide how each course file the transfer needs reaches the destination. Nothing is written.

        new in the destination        -> Brightspace copies it (Copy Components), same path
        same path, same contents      -> the destination's own copy is used
        same path, different contents -> downloaded into this transfer's folder (Brightspace
                                         would silently replace the destination's file)
        """
        needed = set()
        def discover(ref):
            original = self.bundle.split_course_file(ref)
            if original: needed.add(original)
            return ref
        for row in self.placed_rows():
            if row["method"] != "content": continue
            obj = source_of(self.plan, row)
            if row["kind"] == "page":
                transform_references(self.page_markup(row), urljoin(self.source.base, obj["Url"]), discover)
            elif obj.get("TopicType") == 1 and obj.get("Url"):
                discover(urljoin(self.source.base, obj["Url"]))
        self.styling_report, self.notes = {}, []  # page_markup runs again for real in prepare()
        source_root, target_root = self.plan["source"]["root"], self.plan["target"]["root"]
        self.file_plan, self.server_files = {}, []
        for original in sorted(needed):
            path = unquote(urlsplit(original).path)
            if not path.lower().startswith(source_root.lower()): continue
            relative = path[len(source_root):]
            if relative.lower().endswith((".html", ".htm", ".css")):
                self.file_plan[relative] = "page"  # small text that needs its own links rewritten
                continue
            status, size = await self.source.file_info(original)
            if status != 200: raise ValueError(f"The source file {relative} can't be opened (HTTP {status}). Fix or remove the link in the source.")
            target_url = self.target.base + target_root + quote(relative, safe="/")
            there, there_size = await self.target.file_info(target_url)
            if there == 404:
                decision = "copy"
            elif there == 200 and there_size == size and (size > SAME_FILE_CHECK_LIMIT or
                    hashlib.sha256((await self.source.download(original))[0]).digest() ==
                    hashlib.sha256((await self.target.download(target_url))[0]).digest()):
                decision = "same"
            elif size <= DOWNLOAD_LIMIT:
                decision = "separate"
            else:
                raise ValueError(f"{self.plan['target']['name']} already has a different file called {relative}. "
                                 f"The {size / 1e6:.0f} MB version from {self.plan['source']['name']} can't be copied without "
                                 "replacing it. Rename one of them in Brightspace, then try again.")
            self.file_plan[relative] = decision
            if decision in ("copy", "same"):
                self.bundle.server[original] = target_root + quote(relative, safe="/")
            if decision == "copy":
                self.server_files.append((relative, size))

    async def prepare(self):
        from transfer_activity_links import rewrites
        self.bundle.activity_map.update(rewrites(self.plan, getattr(self, "fresh_target", None)))
        host = "https://" + urlparse(self.target.base).hostname
        for row in self.placed_rows():
            if row["method"] == "components":
                url = source_of(self.plan, row).get("Url")
                if url: self.bundle.activity_map[reference_key(urljoin(self.source.base, url))] = host + PLACEHOLDER_PATH + row["id"] + "/"
        self.log("Checking which files Brightspace can copy itself…", "info")
        await self.plan_files()
        root_url = self.target.base + self.plan["target"]["root"]
        for node, parent, _ in walk(self.plan["outline"]):
            self.stopped()
            key = "node:" + node["id"]
            if node["kind"] == "placeholder":
                self.prepared[key] = await self.bundle.page(generated_markup(self.plan, node, parent), root_url, label=node["title"])
            elif node["kind"] == "template":
                template = self.plan["templates"][node["template"]]
                markup = generated_markup(self.plan, node, parent)
                self.prepared[key] = await self.bundle.page(markup, urljoin(self.target.base, template.get("url") or self.plan["target"]["root"]), label=node["title"])
            elif node["kind"] == "item":
                row = self.plan["items"][node["item"]]
                if row["method"] != "content": continue
                obj, item_key = source_of(self.plan, row), "item:" + row["id"]
                if row["kind"] == "page":
                    self.prepared[item_key] = await self.bundle.page(self.page_markup(row), urljoin(self.source.base, obj["Url"]), label=row["title"])
                elif obj.get("TopicType") == 1:
                    if not obj.get("Url"): raise ValueError(f"{row['title']}: the source file's address is missing; fix it in Brightspace.")
                    source_url = urljoin(self.source.base, obj["Url"])
                    if not self.bundle.split_course_file(source_url): raise ValueError(f"{row['title']}: this file isn't stored in the source course.")
                    self.prepared[item_key] = {"url": await self.bundle.add(source_url)}
                else:
                    url = obj.get("Url") or ""
                    if urlparse(urljoin(self.source.base, url)).scheme not in ("http", "https"):
                        raise ValueError(f"{row['title']}: unsupported link address.")
                    self.bundle.check(url)
                    self.prepared[item_key] = {"url": await self.bundle.add(urljoin(self.source.base, url))}
        self.assignments = {}

    # ── Activities ─────────────────────────────────────────────────────
    def component_rows(self):
        return [r for r in self.placed_rows() if r["method"] == "components"]

    async def files_arrived(self):
        sizes = []
        for relative, size in self.server_files:
            url = self.target.base + self.plan["target"]["root"] + quote(relative, safe="/")
            sizes.append(await self.target.file_info(url) == (200, size))
        return all(sizes)

    async def copy_activities(self, fresh_target):
        """One Copy Components job: the planned activities plus the course files Brightspace copies itself."""
        from transfer_components import activity_name, copy_components, file_selection, selection_for, target_matches, wait_for_copies
        rows = self.component_rows()
        if not rows and not self.server_files: return
        if "categories-before" not in self.journal:
            # Remembered so a resumed run still knows which categories the copy added.
            self.journal["categories-before"] = {"state": "verified", "result": {
                "ids": [str(c["Id"]) for c in fresh_target.get("categories", [])]}}
            self.persist()
        names = {r["id"]: activity_name(r, getattr(self, "fresh_source", self.plan["source"])) for r in rows}
        reuse, needed = {}, []
        for row in rows:
            found = target_matches(row["kind"], names[row["id"]], fresh_target)
            if found and "components" not in self.journal:
                same = self._matching_existing(row, names[row["id"]], fresh_target)
                if not same:
                    raise ValueError(f"{names[row['id']]} already exists in {fresh_target['name']}. Rename or remove it there, then scan again.")
                reuse[row["id"]] = same
            else:
                needed.append(row)
        expected = [(r["kind"], names[r["id"]]) for r in needed]
        async def fetch():
            return await self.target.inventory(include_html=False)
        copied = {"copied": [r["id"] for r in needed], "files": [f for f, _ in self.server_files]}
        async def find():
            target = await fetch()
            if all(target_matches(k, n, target) for k, n in expected) and await self.files_arrived():
                return copied
            return None
        if needed or self.server_files:
            selection = {**(selection_for(needed, self.plan["source"]) if needed else {}),
                         **(file_selection([f for f, _ in self.server_files]) if self.server_files else {})}
            async def action():
                self.target.guard()
                await copy_components(self.target.page, self.target.base, self.target.course_id, self.source.course_id,
                                      selection, self.log, self.stop)
                await wait_for_copies(fetch, expected, self.log, self.stop)
                waited = 0
                while not await self.files_arrived():
                    if waited >= 900: raise RuntimeError("Brightspace hasn't finished copying the course files after 15 minutes. "
                                                         "Check Import/Export/Copy Components → View History, then resume.")
                    self.stopped()
                    self.log("Waiting for Brightspace to finish copying course files…", "info")
                    await asyncio.sleep(10); waited += 10
                return copied
            await self.operation("components", action, find)
        if not rows: return
        target = await fetch()
        for row in rows:
            if row["id"] in reuse:
                self.activity_ids[row["id"]] = reuse[row["id"]]; continue
            ids = target_matches(row["kind"], names[row["id"]], target)
            if len(ids) != 1:
                raise RuntimeError(f"{names[row['id']]}: found {len(ids)} copies in the destination; check them in Brightspace.")
            else:
                self.activity_ids[row["id"]] = ids[0]
        self.activity_names = names
        self.activity_target = target
        await self.merge_categories()

    def copied_grade_ids(self, target):
        ids = {}
        for row in self.component_rows():
            activity = self.activity_ids.get(row["id"])
            pool = {"assignment": ("assignments", "Id"), "quiz": ("quizzes", "QuizId")}.get(row["kind"])
            if not activity or not pool: continue
            found = next((a for a in target.get(pool[0], []) if str(a.get(pool[1])) == str(activity)), {})
            if found.get("GradeItemId"): ids[row["id"]] = str(found["GradeItemId"])
        return ids

    async def merge_categories(self):
        """Put copied grade items into the destination's same-named categories; remove the empty duplicates."""
        from transfer_components import category_plan, move_grade_item, remove_empty_category
        before = set(self.journal.get("categories-before", {}).get("result", {}).get("ids", []))
        target = await self.target.inventory(include_html=False)
        self.grade_ids = self.copied_grade_ids(target)
        merge = self.plan.get("grade_categories", "merge") == "merge"
        moves, _ = category_plan(target, before, list(self.grade_ids.values())) if merge else ([], [])
        api = self.target
        for move in moves:
            async def action(move=move):
                await move_grade_item(api, move["grade"], move["to"])
                return dict(move)
            async def find(move=move):
                grade = await api._json(f"/d2l/api/le/1.89/{api.course_id}/grades/{move['grade']}")
                return dict(move) if str(grade.get("CategoryId")) == move["to"] else None
            await self.operation("grade-category:" + move["grade"], action, find)
            self.log(f"Moved {move['name']} into the existing “{move['category']}” category.", "info")
        emptied = {e["result"]["from"] for k, e in self.journal.items() if k.startswith("grade-category:") and e.get("result", {}).get("from")}
        categories = await api._json(f"/d2l/api/le/1.98/{api.course_id}/grades/categories/")
        for category in categories:
            cid = str(category["Id"])
            if cid not in emptied or cid in before or category.get("Grades"): continue
            async def action(cid=cid):
                return await remove_empty_category(api, cid)
            async def find(cid=cid):
                listed = await api._json(f"/d2l/api/le/1.98/{api.course_id}/grades/categories/")
                return None if any(str(c["Id"]) == cid for c in listed) else {"removed": cid}
            await self.operation("remove-category:" + cid, action, find)
            self.log(f"Removed the empty duplicate “{category['Name']}” category the copy created.", "info")
        categories = await api._json(f"/d2l/api/le/1.98/{api.course_id}/grades/categories/")
        if any(not c.get("ExcludeFromFinalGrade") for c in categories):
            total = sum(c.get("Weight") or 0 for c in categories if not c.get("ExcludeFromFinalGrade"))
            if abs(total - 100) > 0.01:
                self.notes.append(f"Grade category weights in {target['name']} now total {total:g}%. Adjust them in Grades → Setup.")
        grades = {str(g["Id"]): g for g in (await self.target.inventory(include_html=False))["grades"]}
        visible = [g["Name"] for gid, g in grades.items() if gid in set(self.grade_ids.values()) and not g.get("IsHidden")]
        if visible:
            self.notes.append(f"{len(visible)} copied grade item{'s are' if len(visible) != 1 else ' is'} visible in the gradebook "
                              "(the activities themselves are hidden): " + ", ".join(visible) + ".")

    def _matching_existing(self, row, name, target):
        """An identical copy that's already in the destination (assignments only)."""
        if row["kind"] != "assignment": return None
        from transfer_guided import matching_assignment
        tool_id = str(source_of(self.plan, row).get("ToolItemId"))
        original = next((a for a in self.plan["source"].get("assignments", []) if str(a["Id"]) == tool_id), None)
        same = [a for a in target.get("assignments", []) if original and matching_assignment(original, a)]
        return str(same[0]["Id"]) if len(same) == 1 else None

    async def link_activity(self, key, parent, row):
        """Link a copied activity into a module with Content's own Add Existing."""
        from content_checker import ContentChecker
        kind, content_type, tool, singular = LINK_TOOLS[row["kind"]]
        activity_id, name = self.activity_ids[row["id"]], self.activity_names[row["id"]]
        forum = next((d.get("ForumName") for d in self.activity_target.get("discussions", [])
                      if row["kind"] == "discussion" and str(d.get("TopicId")) == str(activity_id)), None)
        async def find():
            matches = [o for o in await self.target.children(parent) if o.get("ActivityType") == content_type and
                       (str(o.get("ToolItemId")) == str(activity_id) if activity_id else o.get("Title", "").strip() == name.strip())]
            if len(matches) > 1: raise ValueError(f"{name} is linked more than once in the same module.")
            return matches[0] if matches else None
        if key not in self.journal and await find():
            self.journal[key] = {"state": "verified", "result": await find()}; self.persist()
        async def action():
            self.target.guard()
            checker = object.__new__(ContentChecker)
            checker.log = self.log
            ok, reason = await checker._attach_existing_activity(self.target.page.context, self.target.base, self.target.course_id, {
                "target_module_id": parent, "activity_id": activity_id, "activity_kind": kind, "name": name,
                "content_activity_type": content_type, "tool_name": tool, "tool_singular": singular, "forum_name": forum})
            if not ok:
                message = f"{name}: Add Existing didn't verify ({reason})."
                safe = {"Add Existing control was not available", f"{tool} option was not available",
                        "exact activity selection was unavailable or ambiguous"}
                if reason in safe: raise TransferRequestRejected(message)
                raise RuntimeError(message)  # A clicked activity may still be saving: never repeat blindly.
            return {}
        try:
            linked = await self.operation(key, action, find)
            before = set(self.journal.get("content-before", {}).get("result", {}).get("ids", []))
            if str(linked["Id"]) not in before:
                visibility_key = key + ":visibility"
                async def hidden():
                    obj = await find()
                    return obj if obj and obj.get("IsHidden") else None
                async def hide():
                    return await self.target.hide_topic(linked["Id"])
                if linked.get("IsHidden") and visibility_key not in self.journal:
                    self.journal[visibility_key] = {"state": "verified", "result": linked}; self.persist()
                else:
                    linked = await self.operation(visibility_key, hide, hidden)
                self.journal[key]["result"] = linked; self.persist()
            return linked
        except TransferRequestRejected as exc:
            if row["kind"] in ("discussion", "survey"):
                # Copied, but this picker isn't verified for every layout: say so instead of stopping.
                self.notes.append(f"{name} was copied but couldn't be linked automatically. In Brightspace, open "
                                  f"the module and use Add Existing → {tool} to add it. ({exc})")
                return None
            raise

    # ── Creation ───────────────────────────────────────────────────────
    async def build(self, nodes, parent_id, existing_parent=False):
        """Create modules and link activities; pages wait until files are uploaded."""
        for node in nodes:
            self.stopped()
            if node["kind"] == "module":
                if node.get("existing_id"):
                    found = [o for o in await self.target.children(parent_id) if str(o["Id"]) == node["existing_id"] and o.get("Type") == 0]
                    if not found: raise ValueError(f"{node['title']} is no longer in the destination. Scan again.")
                    obj = found[0]
                else:
                    obj = await self.ensure_object("node:" + node["id"], parent_id, node["title"], module=True)
                self.objects[node["id"]] = obj
                await self.build(node["children"], obj["Id"], bool(node.get("existing_id")))
            elif node["kind"] == "item":
                row = self.plan["items"][node["item"]]
                if row["method"] == "components":
                    self.objects[node["id"]] = await self.link_activity("item:" + row["id"], parent_id, row)

    async def fill(self, nodes, parent_id, existing_parent=False):
        """Pages, files and links, then the planned order inside each module."""
        for node in nodes:
            self.stopped()
            if node["kind"] == "module":
                await self.fill(node["children"], self.objects[node["id"]]["Id"], bool(node.get("existing_id")))
            elif node["kind"] in ("placeholder", "template"):
                self.objects[node["id"]] = await self.page("node:" + node["id"], parent_id, node["title"])
            elif node["kind"] == "item":
                row = self.plan["items"][node["item"]]
                if row["method"] == "content":
                    self.objects[node["id"]] = await self.page("item:" + row["id"], parent_id, row["title"])
        ours = [self.objects[n["id"]] for n in nodes if self.objects.get(n["id"]) and not n.get("existing_id")]
        current = [o["Id"] for o in await self.target.children(parent_id)]
        wanted = [o["Id"] for o in ours]
        if existing_parent:
            present = [i for i in current if i in wanted]
            in_order = present == wanted and current[-len(wanted):] == wanted if wanted else True
        else:
            in_order = [i for i in current if i in wanted] == wanted and current[:len(wanted)] == wanted
        if not in_order:
            # Moves only objects this transfer created; existing ones keep their places.
            for obj in ours: await self.target.order(obj["Id"])

    async def run(self, confirmed_target_id, sandbox_acknowledged):
        if confirmed_target_id != self.target.course_id or not sandbox_acknowledged:
            raise ValueError("Confirm the destination before creating.")
        if self.target.course_id != self.plan["target"]["id"] or self.source.course_id != self.plan["source"]["id"]:
            raise ValueError("The selected courses differ from the saved plan.")
        self.target.guard()
        errors, notes = validate_outline(self.plan)
        if errors: raise ValueError("Fix these first:\n" + "\n".join(errors))
        signature = outline_signature(self.plan)
        if self.plan.get("run_signature") and self.plan["run_signature"] != signature:
            raise ValueError("This transfer was already started with a different plan. Resume the saved plan unchanged.")
        fresh_source = await self.source.inventory()
        if not source_inventory_matches(self.plan, fresh_source):
            raise ValueError("The source course changed since it was scanned. Scan both courses again.")
        self.fresh_source = fresh_source
        fresh_target = await self.target.inventory()
        self.fresh_target = fresh_target
        if fresh_target["name"] != self.plan["target"]["name"] or fresh_target["root"] != self.plan["target"]["root"]:
            raise ValueError("The destination course changed identity. Scan again.")
        from transfer_components import activity_name, target_matches
        for row in self.component_rows():
            if row["kind"] == "survey":
                tool_id = str(source_of(self.plan, row).get("ToolItemId"))
                if fresh_target.get("surveys") is None or not any(str(s.get("SurveyId")) == tool_id for s in fresh_source.get("surveys") or []):
                    raise ValueError(f"{row['title']}: the source or destination survey catalogue is unavailable. Scan again or leave this survey out.")
            name = activity_name(row, fresh_source)
            if "components" not in self.journal and target_matches(row["kind"], name, fresh_target) and not self._matching_existing(row, name, fresh_target):
                raise ValueError(f"{name} already exists in {fresh_target['name']}. Rename or remove it there, then scan again.")
        self.log(f"Copying into {fresh_target['name']} ({self.target.course_id}). New content is hidden.", "warning")
        self.log("Preparing pages and files before any change…", "info")
        try:
            await self.prepare()
        except Exception as exc:
            self.log(f"Preparation stopped: {exc}", "error")
            self.plan["report"] = (f"STRUCTURED TRANSFER — PREPARATION STOPPED\nDestination: {fresh_target['name']} ({self.target.course_id})\n"
                                   f"{exc}\nNo new course changes were made by this attempt.\n"
                                   "Return to Review to resolve the issue. Earlier work, if any, remains recorded in the saved journal.")
            self.persist()
            self.path.with_suffix(".report.txt").write_text(self.plan["report"], encoding="utf-8")
            raise
        was_started = bool(self.plan.get("run_signature"))
        self.plan["run_signature"] = signature
        if not self.journal:
            backup = self.path.with_suffix(".recovery.json")
            save_plan({"source": fresh_source, "target": fresh_target, "plan_id": self.plan["id"]}, backup)
            self.plan["backup"] = str(backup)
        if "content-before" not in self.journal:
            before = self.plan["target"] if was_started else fresh_target
            self.journal["content-before"] = {"state": "verified", "result": {
                "ids": [str(obj["Id"]) for obj in all_objects(before["modules"])]}}
        self.plan["styling_report"] = self.styling_report
        self.persist()
        try:
            async def folder():
                await self.target.ensure_files_folder(self.bundle.folder)
                return {"folder": self.bundle.folder}
            async def find_folder():
                try:
                    await self.target._json(f"/d2l/api/lp/1.49/{self.target.course_id}/managefiles/?path={self.bundle.folder}")
                    return {"folder": self.bundle.folder}
                except Exception as exc:
                    if "HTTP 404" in str(exc): return None
                    raise
            await self.operation("files-folder", folder, find_folder)
            self.log("Copying activities with Copy Components…", "info")
            await self.copy_activities(fresh_target)
            self.log("Creating modules and linking activities…", "info")
            await self.build(self.plan["outline"], None)
            for row in self.component_rows():
                topic = self.journal.get("item:" + row["id"], {}).get("result") or {}
                if topic.get("Url"): self.activity_urls[row["id"]] = urljoin(self.target.base, topic["Url"])
            for original, file in self.bundle.files.items():
                await self.upload("dependency:" + digest(original)[:24], file["filename"], file["body"], file["type"])
            self.log("Adding pages and files…", "info")
            await self.fill(self.plan["outline"], None, True)
            self.log("Reading everything back…", "info")
            checks, totals = await self.validate_after()
            status = "COMPLETE" if all(checks.values()) else "CHECKS FAILED"
            self.plan.update(validation=checks, counts=totals)
            self.plan["report"] = self.report(status, fresh_target, checks, totals, notes)
        except BaseException as exc:
            self.plan["report"] = (f"STRUCTURED TRANSFER — INCOMPLETE\nDestination: {fresh_target['name']} ({self.target.course_id})\n"
                                   f"{exc}\nResume the same saved plan. Anything created so far is hidden.\n"
                                   f"Recovery snapshot: {self.plan.get('backup', '')}")
            self.persist()
            self.path.with_suffix(".report.txt").write_text(self.plan["report"], encoding="utf-8")
            raise
        self.persist()
        self.path.with_suffix(".report.txt").write_text(self.plan["report"], encoding="utf-8")
        return self.plan

    def report(self, status, target, checks, totals, notes):
        lines = [f"STRUCTURED TRANSFER — {status}", f"Destination: {target['name']} ({self.target.course_id})",
                 "New Content is hidden until you publish it. Activities keep their tool availability settings.", "",
                 *[f"{name.capitalize()}: {count}" for name, count in totals.items()], ""]
        if self.styling_report:
            lines.append(f"Styling: {MODES[self.plan['styling'].get('mode', 'preserve')]}"
                         + (" + destination template" if self.plan["styling"].get("apply_template") else ""))
            lines.extend(f"  {self.plan['items'][r]['title']}: {s}" for r, s in self.styling_report.items())
            lines.append("")
        plan = getattr(self, "file_plan", {})
        if plan:
            how = {"copy": "copied by Brightspace (no download)", "same": "already in the destination, identical; reused",
                   "separate": "the destination has a different file with this name; kept separately", "page": "page files, links rewritten"}
            lines.append("Course files:")
            for kind, label in how.items():
                names = [f for f, d in plan.items() if d == kind]
                if names: lines.append(f"  {len(names)} {label}" + (": " + ", ".join(names) if kind == "separate" else ""))
            lines.append("")
        lines.extend(["Checks:", *[f"{'PASS' if ok else 'FAIL'} {name}" for name, ok in checks.items()]])
        review = [*self.notes, *notes]
        if review: lines.extend(["", "Please review:", *[f"- {n}" for n in dict.fromkeys(review)]])
        lines.extend(["", "Created objects (IDs):",
                      *[f"{key}: {entry['result']['Id']}" for key, entry in self.journal.items() if (entry.get("result") or {}).get("Id")],
                      "", "Saved plan and journal: " + str(self.path.resolve())])
        if self.plan.get("backup"): lines.append("Recovery snapshot: " + str(Path(self.plan["backup"]).resolve()))
        return "\n".join(lines)

    async def validate_after(self):
        source = await self.source.inventory(include_html=True)
        target = await self.target.inventory(include_html=True)
        checks = {"Source course unchanged": source_inventory_matches(self.plan, source)}
        objects = {str(o["Id"]): o for o in all_objects(target["modules"])}
        original_ids = set(self.journal.get("content-before", {}).get("result", {}).get("ids", []))
        def children_of(parent_id):
            if parent_id is None: return target["modules"]
            return objects.get(str(parent_id), {}).get("children", [])
        for node, parent, _ in walk(self.plan["outline"]):
            created = self.objects.get(node["id"])
            if not created: continue
            label = node.get("title") or self.plan["items"].get(node.get("item"), {}).get("title", "")
            parent_id = self.objects[parent["id"]]["Id"] if parent else None
            checks[f"{label} / in place"] = any(str(o["Id"]) == str(created["Id"]) for o in children_of(parent_id))
            if not node.get("existing_id") and str(created["Id"]) not in original_ids:
                checks[f"{label} / hidden"] = bool(objects.get(str(created["Id"]), {}).get("IsHidden"))
        for node, parent, _ in walk(self.plan["outline"]):
            if node["kind"] != "module" or node["id"] not in self.objects: continue
            ours = [str(self.objects[c["id"]]["Id"]) for c in node["children"] if self.objects.get(c["id"]) and not c.get("existing_id")]
            actual = [str(o["Id"]) for o in children_of(self.objects[node["id"]]["Id"]) if str(o["Id"]) in ours]
            checks[f"{node['title']} / order"] = actual == ours
        names = getattr(self, "activity_names", {})
        for row in self.component_rows():
            linked = self.journal.get("item:" + row["id"], {}).get("result") or {}
            actual = objects.get(str(linked.get("Id")), {})
            name = names.get(row["id"], row["title"])
            checks[f"{name} / link to the copied activity"] = bool(actual) and actual.get("ActivityType") == LINK_TOOLS[row["kind"]][1] and str(actual.get("ToolItemId")) == str(self.activity_ids.get(row["id"]))
            if row["kind"] != "assignment" or not self.activity_ids.get(row["id"]): continue
            from transfer_guided import assignment_text
            tool_id = str(source_of(self.plan, row).get("ToolItemId"))
            original = next((a for a in source["assignments"] if str(a["Id"]) == tool_id), {})
            copy = next((a for a in target["assignments"] if str(a["Id"]) == str(self.activity_ids[row["id"]])), {})
            name = names.get(row["id"], row["title"])
            checks[f"{name} / rubric"] = bool(copy) and rubric_signature((copy.get("Assessment") or {}).get("Rubrics", [])) == \
                rubric_signature((original.get("Assessment") or {}).get("Rubrics", []))
            checks[f"{name} / instructions and points"] = bool(copy) and assignment_text(copy) == assignment_text(original) and \
                (copy.get("Assessment") or {}).get("ScoreDenominator") == (original.get("Assessment") or {}).get("ScoreDenominator")
            if original.get("GradeItemId"):
                checks[f"{name} / grade item"] = bool(copy.get("GradeItemId")) and any(g["Id"] == copy["GradeItemId"] for g in target["grades"])
            moved = self.journal.get("grade-category:" + str(copy.get("GradeItemId")), {}).get("result")
            if moved:
                grade = next((g for g in target["grades"] if str(g["Id"]) == moved["grade"]), {})
                checks[f"{name} / in the existing “{moved['category']}” category"] = str(grade.get("CategoryId")) == moved["to"]
        for key, entry in self.journal.items():
            if key.endswith(":file") or key.startswith("dependency:"):
                result = entry.get("result") or {}
                relative = result.get("url", "").removeprefix(self.plan["target"]["root"])
                checks[f"{key} / file readback"] = bool(relative) and await self.target.file_hash(relative) == result.get("hash")
        for relative, size in getattr(self, "server_files", []):
            url = self.target.base + self.plan["target"]["root"] + quote(relative, safe="/")
            checks[f"{relative} / copied by Brightspace"] = await self.target.file_info(url) == (200, size)
        checks["Every step verified"] = all(entry.get("state") == "verified" for entry in self.journal.values())
        markers = [(o.get("Description") or {}).get("Html", "") for o in objects.values()]
        own = [m for m in markers if "bpa-transfer:" + self.plan["id"] in m]
        checks["No duplicates"] = len(own) == len(set(own))
        totals = counts(self.plan)
        totals.pop("skipped", None)
        return checks, totals


async def apply_outline(page, plan, path, confirmed_target_id, acknowledged, log=None, stop=None):
    from transfer_api import TransferAPI
    from transfer_runner import PlanLock, load_plan
    with PlanLock(path):
        disk = load_plan(path)
        if disk["id"] != plan["id"] or digest(disk["journal"]) != digest(plan["journal"]):
            raise ValueError("The saved transfer journal changed. Reopen the saved plan.")
        source = TransferAPI(page, plan["source"]["id"])
        target = TransferAPI(page, plan["target"]["id"], source_id=plan["source"]["id"], writable=True)
        return await OutlineRunner(source, target, plan, path, log, stop).run(confirmed_target_id, acknowledged)


async def scan_courses(page, source_id, target_id, log=None, stop=None):
    """Read-only inventories of both courses."""
    from transfer_api import TransferAPI
    if str(source_id) == str(target_id): raise ValueError("Copy from and Copy into must be different courses.")
    source = await TransferAPI(page, source_id).inventory(log, stop)
    target = await TransferAPI(page, target_id).inventory(log, stop)
    return source, target
