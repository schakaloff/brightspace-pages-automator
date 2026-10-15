"""Preview-first, journaled target execution. No destructive rollback."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from transfer_api import TransferAPI, choose_templates
from transfer_files import FileBundle, placeholder, reference_key, render_template
from transfer_plan import (SKIP, all_objects, assignment_for, assignment_grading, build_plan, category_for_role, digest,
                           grade_categories, native_required, norm, recipe_sections, rubric_signature,
                           session_section, validate_plan)


class TransferRequestRejected(RuntimeError):
    """A known failure before a content mutation, safe to try again."""


def source_inventory_matches(plan, inventory):
    """Keep pre-survey-catalogue plans resumable without changing their signatures."""
    observed = dict(inventory)
    if "surveys" not in plan["source"]: observed.pop("surveys", None)
    return digest(observed) == plan["source_hash"]


def save_plan(plan, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    # Windows readers/virus scanners can briefly deny replacement even though
    # both files are writable. Retry the rename, never rewrite the destination
    # in place: the last durable journal must remain intact on any failure.
    for attempt in range(6):
        try:
            os.replace(temp, path)
            return
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                raise  # Keep the complete .tmp file available for recovery.
            time.sleep(0.05 * 2 ** attempt)


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("schema") not in (1, 3) or not isinstance(plan.get("journal"), dict):
        raise ValueError("Unsupported saved transfer plan.")
    return plan


def run_signature(plan):
    return digest({"source": plan["source_hash"], "target": plan["target"]["id"],
                   "recipe": plan["recipe"], "workshops": plan["workshops"], "templates": plan["templates"]})


class PlanLock:
    """OS lock released on process exit; concurrent runs cannot duplicate writes."""
    def __init__(self, path): self.path = Path(path).with_suffix(".lock")
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0); self.handle.write(b"0"); self.handle.flush(); self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError("This saved transfer is already running.")
        return self
    def __exit__(self, *exc): self.handle.close()


class TransferRunner:
    def __init__(self, source, target, plan, path, log=None, stop=None):
        self.source, self.target, self.plan = source, target, plan
        self.path, self.log, self.stop = Path(path), log or (lambda *a: None), stop
        self.journal = plan["journal"]
        self.prepared = {}
        self.created = {}
        self.categories = {}
        self.activity_urls = {}
        self.bundle = FileBundle(source.download, urlparse(target.base).hostname, plan["target"]["root"],
                                 "bpa-transfer-" + plan["id"], [source.course_id])

    def stopped(self):
        if self.stop and self.stop.is_set(): raise InterruptedError("Stopped after the current request; resume this saved plan.")

    def persist(self): save_plan(self.plan, self.path)

    def marker(self, key): return f"bpa-transfer:{self.plan['id']}:{key}"

    async def operation(self, key, action, find=None):
        self.stopped()
        previous = self.journal.get(key)
        # A durable 'pending' entry precedes every mutation. Never blindly
        # repeat POSTs whose response might have been lost.
        if previous and previous.get("state") != "rejected":
            if find:
                found = await find()
                if found:
                    self.journal[key] = {"state": "verified", "result": found}
                    self.persist()
                    return found
            if previous["state"] == "verified":
                if find: raise RuntimeError(f"Previously created object changed or disappeared: {key}")
                return previous["result"]
            raise RuntimeError(f"Uncertain previous request {key}; inspect Brightspace and the journal before continuing.")
        self.journal[key] = {"state": "pending"}
        self.persist()
        try:
            result = await action()
        except Exception as exc:
            message = str(exc).split("Call log:")[0].strip()
            if isinstance(exc, TransferRequestRejected) or any(f"HTTP {status}" in message for status in (400, 403, 404, 405)):
                self.journal[key] = {"state": "rejected", "error": message}
                self.persist()
            raise
        # Save the server response before the independent verification read.
        # A failed read must not lose the identifier of a successful POST.
        self.journal[key]["result"] = result or {}
        self.persist()
        if find:
            result = await find()
            if not result: raise RuntimeError(f"Created object could not be verified: {key}")
        self.journal[key] = {"state": "verified", "result": result or {}}
        self.persist()
        self.log(f"Verified: {key}", "success")
        return result or {}

    async def ensure_object(self, key, parent, title, module=False, url=None):
        marker = self.marker(key)
        async def find():
            obj = await self.target.find_owned(parent, marker)
            if obj and (obj.get("Title") != title or obj.get("Type") != (0 if module else 1)
                        or not obj.get("IsHidden") or not module and obj.get("Url") != url):
                raise ValueError(f"Previously transferred content was edited: {title}. Leaving it unchanged.")
            return obj
        existing = await find()
        if existing and key not in self.journal:
            raise ValueError("Ownership marker exists without its saved journal; recover the original plan.")
        async def create():
            conflicts = [o for o in await self.target.children(parent) if norm(o["Title"]) == norm(title)]
            if conflicts: raise ValueError(f"Existing content conflicts with {title!r}; leaving it unchanged.")
            payload = {"Title": title, "ShortTitle": "", "Type": 0 if module else 1,
                       "IsHidden": True, "IsLocked": False,
                       # Brightspace removes comment-only rich text. A hidden
                       # span survives sanitization and gives reruns an owner.
                       "Description": {"Content": f'<p style="display:none" aria-hidden="true">{marker}</p>', "Type": 1}}
            if module:
                payload.update(ModuleStartDate=None, ModuleEndDate=None, ModuleDueDate=None)
            else:
                payload.update(TopicType=1 if url and url.startswith(self.plan["target"]["root"]) else 3,
                               Url=url, StartDate=None, EndDate=None, DueDate=None, OpenAsExternalResource=False)
            return await self.target.create_object(parent, payload)
        return await self.operation(key, create, find)

    async def prepare(self):
        """Materialize rewritten pages/dependencies locally; fail before writes."""
        target_assignments = {str(a["Id"]): a for a in self.plan["target"]["assignments"]}
        # HTML often embeds an assignment quicklink as well as its Content row.
        # Map exact source quicklinks to placeholders, then bind the URL returned
        # by Brightspace's verified native Add Existing step before uploading.
        planned_assignments = {str(assignment_for(self.plan, row)["Id"])
                               for workshop in self.plan["workshops"] for row in workshop["items"]
                               if row["section"] != "Skip" and row["source"].get("ActivityType") == 3
                               and assignment_for(self.plan, row)}
        for obj in all_objects(self.plan["source"]["modules"]):
            if obj.get("ActivityType") != 3 or not obj.get("Url"): continue
            activity = assignment_for(self.plan, {"source": obj, "title": obj["Title"]})
            source_id = str(activity["Id"]) if activity else str(obj.get("ToolItemId"))
            if source_id in planned_assignments:
                self.bundle.activity_map[reference_key(urljoin(self.source.base, obj["Url"]))] = "https://" + urlparse(self.target.base).hostname + "/bpa-transfer-assignment/" + source_id + "/"
        assignments = {}
        for wi, workshop in enumerate(self.plan["workshops"]):
            self.stopped()
            base = f"w{wi}"
            kinds = []
            for section in recipe_sections(self.plan["recipe"]):
                if section["kind"] == "placeholder":
                    self.prepared[base + ":" + section["key"]] = await self.bundle.page(
                        placeholder(section["title"], section["message"]), self.target.base + self.plan["target"]["root"])
                elif section["kind"] == "introduction":
                    kinds.append("introduction")
                if section["session_overview"]:
                    kinds.append("session")
            for kind in kinds:
                template = self.plan["templates"][kind]
                markup = render_template(template["html"], kind, workshop, self.plan["recipe"])
                self.prepared[base + ":" + kind] = await self.bundle.page(markup, urljoin(self.target.base, template["url"]))
            for row in workshop["items"]:
                if row["section"] == "Skip": continue
                item, key = row["source"], base + ":item:" + row["id"]
                if item.get("ActivityType") == 3:
                    a = assignment_for(self.plan, row)
                    if row.get("target_assignment_id"):
                        target = target_assignments.get(str(row["target_assignment_id"]))
                        if target is None: raise ValueError(f"Target assignment ID {row['target_assignment_id']} is not in the selected course. Rescan.")
                        if norm(target["Name"]) != norm(a["Name"]): raise ValueError("Native-copy assignment title differs; select the correct target assignment.")
                        expected = [rubric_signature(r) for r in (a.get("Assessment") or {}).get("Rubrics", [])]
                        actual = [rubric_signature(r) for r in (target.get("Assessment") or {}).get("Rubrics", [])]
                        if expected != actual: raise ValueError(f"{a['Name']}: native-copy rubrics differ or are missing. Resolve in Brightspace.")
                        for field in ("CustomInstructions",):
                            from transfer_guided import assignment_text
                            original_text, copied_text = assignment_text(a), assignment_text(target)
                            if original_text != copied_text: raise ValueError(f"{a['Name']}: native-copy instructions differ. Review before linking.")
                        self.bundle.check(json.dumps(target))
                        if row.get('grade_item') is False and target.get('GradeItemId'):
                            raise ValueError(f"{a['Name']}: the copied task already has a grade item; review it in Brightspace.")
                        assignments[key] = {"source": a, "native": target, "grade_item": row.get('grade_item', True)}
                    elif native_required(a, self.plan["source"]["versions"]):
                        raise ValueError(f"{a['Name']}: native Copy Components and target assignment mapping required.")
                    else:
                        if any(norm(x["Name"]) == norm(a["Name"]) for x in target_assignments.values()) and key + ":assignment" not in self.journal:
                            raise ValueError(f"{a['Name']}: assignment already exists. Enter its target assignment ID.")
                        instructions = await self.bundle.page((a.get("CustomInstructions") or {}).get("Html", ""), self.source.base + self.plan["source"]["root"])
                        if "/bpa-transfer-assignment/" in instructions:
                            raise ValueError("Assignment instructions link to another transferred assignment; copy these assignments natively before mapping.")
                        assignments[key] = {"source": a, "instructions": instructions, "grade_item": row.get('grade_item', True)}
                elif row.get("piece") or item.get("html") is not None:
                    from transfer_split import plain_document
                    if row.get("piece"):
                        markup = plain_document(row["piece"]["html"], row["title"])
                    elif self.plan["recipe"].get("page_style") == "plain":
                        markup = plain_document(item["html"], row["title"])
                    else:
                        markup = item["html"]
                    self.prepared[key] = await self.bundle.page(markup, urljoin(self.source.base, item["Url"]))
                elif item.get("TopicType") == 1:
                    if not item.get("Url"): raise ValueError(f"{row['title']}: source file URL unavailable; resolve the file in Brightspace.")
                    source_url = urljoin(self.source.base, item["Url"])
                    if not self.bundle.split_course_file(source_url): raise ValueError("File topic does not refer to course-owned content.")
                    self.prepared[key] = {"url": await self.bundle.add(source_url)}
                else:
                    url = item.get("Url") or ""
                    if not urlparse(urljoin(self.source.base, url)).scheme in ("http", "https"):
                        raise ValueError("Unsupported resource URL.")
                    self.bundle.check(url)
                    self.prepared[key] = {"url": await self.bundle.add(urljoin(self.source.base, url))}
        self.assignments = assignments

    async def upload(self, key, filename, body, type_):
        if type_.split(";")[0] in ("text/html", "text/css", "application/xhtml+xml"):
            text = body.decode("utf-8")
            for source_id, url in self.activity_urls.items():
                text = text.replace("https://" + urlparse(self.target.base).hostname + "/bpa-transfer-assignment/" + source_id + "/", url)
            if "/bpa-transfer-assignment/" in text:
                raise ValueError("An assignment reference has no verified target Content link.")
            self.bundle.check(text)
            body = text.encode("utf-8")
        relative = self.bundle.folder + "/" + filename
        expected = hashlib.sha256(body).hexdigest()
        async def find():
            if not await self.target.file_exists(relative): return None
            if await self.target.file_hash(relative) != expected:
                raise ValueError(f"Target file was changed or conflicts with this transfer: {relative}")
            return {"url": self.plan["target"]["root"] + relative, "hash": expected}
        if key not in self.journal and await find():
            # Deterministic path under this plan's unique folder can reconcile
            # an upload completed before the local process persisted a response.
            self.journal[key] = {"state": "verified", "result": await find()}; self.persist()
        async def action():
            await self.target.upload_file(self.bundle.folder, filename, body, type_, overwrite=False)
            return {}
        return (await self.operation(key, action, find))["url"]

    async def page(self, key, parent, title):
        content = self.prepared[key]
        if isinstance(content, dict): url = content["url"]
        else: url = await self.upload(key + ":file", hashlib.sha256(key.encode()).hexdigest()[:24] + ".html", content.encode(), "text/html")
        return await self.ensure_object(key, parent, title, url=url)

    async def gradebook(self):
        spec = self.plan["recipe"].get("gradebook", {})
        self.categories = {}
        if not spec.get("enabled"): return
        api = self.target
        current = await api._json(f"/d2l/api/le/1.98/{api.course_id}/grades/categories/")
        categories = grade_categories(spec)
        wanted = {c["name"]: (c["label"], c["weight"]) for c in categories}
        roles = {c["label"]: c["role"] for c in categories}
        for name in wanted:
            if sum(c["Name"] == name for c in current) > 1: raise ValueError(f"Duplicate grade category: {name}")
        # Existing extra empty categories are retained and excluded, never deleted.
        desired = [(wanted.get(c["Name"], (c["Name"], 0))[0], c, wanted.get(c["Name"], (None, 0))[1]) for c in current]
        desired.extend((label, None, weight) for name, (label, weight) in wanted.items() if not any(c["Name"] == name for c in current))
        for label, existing, weight in desired:
            active = label in roles
            name = next(c["name"] for c in categories if c["label"] == label) if active else existing["Name"]
            payload = {"Name": name, "ShortName": (existing or {}).get("ShortName", ""),
                "CanExceedMax": False, "ExcludeFromFinalGrade": not active, "StartDate": None, "EndDate": None,
                "Weight": weight, "MaxPoints": 100, "AutoPoints": False,
                "WeightDistributionType": spec.get("assignment_distribution", 1) if roles.get(label) == "assignments" else 0,
                "NumberOfHighestToDrop": 0, "NumberOfLowestToDrop": 0}
            if existing:
                payload.update({k: existing[k] for k in ("CanExceedMax", "StartDate", "EndDate", "MaxPoints", "AutoPoints", "NumberOfHighestToDrop", "NumberOfLowestToDrop") if k in existing})
            if existing and existing.get("Grades") and not active: raise ValueError("An extra category gained grade items. Rescan before applying.")
            key = "category:" + label
            async def find(name=name, payload=payload):
                matches = [c for c in await api._json(f"/d2l/api/le/1.98/{api.course_id}/grades/categories/") if c["Name"] == name]
                if len(matches) > 1: raise ValueError("Duplicate grade categories.")
                if matches and all(matches[0].get(k) == payload[k] for k in ("Weight", "ExcludeFromFinalGrade", "WeightDistributionType")):
                    return matches[0]
            async def action(existing=existing, payload=payload):
                route = f"/d2l/api/le/1.98/{api.course_id}/grades/categories/" + (str(existing["Id"]) if existing else "")
                return await api._json(route, "PUT" if existing else "POST", payload)
            self.categories[label] = await self.operation(key, action, find)

    def category_id(self, role):
        """Target category ID for a recipe role, once gradebook() has run."""
        spec = category_for_role(self.plan["recipe"].get("gradebook", {}), role)
        found = spec and getattr(self, "categories", {}).get(spec["label"])
        return found["Id"] if found else None

    def expected_children(self, wi, workshop):
        """Titles each unit must contain, in order, for this recipe."""
        titles = []
        for section in recipe_sections(self.plan["recipe"]):
            if section["kind"] != "inline":
                titles.append(section["title"]); continue
            if section["session_overview"]: titles.append("Session Overview")
            titles.extend(row["title"] for row in workshop["items"] if row["section"] == section["title"])
        return titles

    async def numeric_grade(self, key, name, points, category, weight=None, existing_id=None):
        api = self.target
        payload = {"Name": name[:128], "ShortName": "", "GradeType": "Numeric", "MaxPoints": points,
                   "CanExceedMaxPoints": False, "IsBonus": False, "ExcludeFromFinalGradeCalculation": False,
                   "GradeSchemeId": None, "CategoryId": category, "Description": {"Content": "", "Type": 1}, "IsHidden": True}
        if weight is not None: payload["Weight"] = weight
        if existing_id:
            existing = await api._json(f"/d2l/api/le/1.89/{api.course_id}/grades/{existing_id}")
            if existing.get("GradeType") != "Numeric": raise ValueError("Assignment grade item is not Numeric; review the gradebook manually.")
            for field in ("Name", "ShortName", "CanExceedMaxPoints", "IsBonus", "ExcludeFromFinalGradeCalculation", "GradeSchemeId", "IsHidden"):
                if field in existing: payload[field] = existing[field]
            payload["Description"] = {"Content": (existing.get("Description") or {}).get("Html", ""), "Type": 1}
        async def find():
            matches = [g for g in await api._json(f"/d2l/api/le/1.89/{api.course_id}/grades/")
                       if str(g["Id"]) == str(existing_id) or not existing_id and g["Name"] == payload["Name"]]
            if len(matches) > 1: raise ValueError("Duplicate grade items; review required.")
            if matches and matches[0].get("CategoryId") == category and matches[0].get("MaxPoints") == points and (weight is None or matches[0].get("Weight") == weight):
                return matches[0]
        async def action():
            if not existing_id:
                matches = [g for g in await api._json(f"/d2l/api/le/1.89/{api.course_id}/grades/") if g["Name"] == payload["Name"]]
                if matches: raise ValueError(f"Existing grade item conflicts: {name}")
            route = f"/d2l/api/le/1.89/{api.course_id}/grades/" + (str(existing_id) if existing_id else "")
            return await api._json(route, "PUT" if existing_id else "POST", payload)
        return await self.operation(key, action, find)

    async def assignment(self, key, parent):
        data, api = self.assignments[key], self.target
        original = data["source"]
        current = data.get("native")
        marker = self.marker(key + ":assignment")
        async def find():
            matches = [a for a in await api._json(f"/d2l/api/le/1.82/{api.course_id}/dropbox/folders/")
                       if marker in (a.get("CustomInstructions") or {}).get("Html", "")]
            if len(matches) > 1: raise ValueError("Duplicate transferred assignments.")
            return matches[0] if matches else None
        if not current:
            async def action():
                payload = {k: original.get(k) for k in ("Name", "DropboxType", "SubmissionType", "CompletionType", "IsAnonymous")}
                payload.update(CategoryId=None, CustomInstructions={"Content": data["instructions"] + f'<p style="display:none" aria-hidden="true">{marker}</p>', "Type": 1},
                               Availability=None, GroupTypeId=None, DueDate=None, DisplayInCalendar=False,
                               NotificationEmail=None, IsHidden=True,
                               Assessment={"ScoreDenominator": (original.get("Assessment") or {}).get("ScoreDenominator")}, GradeItemId=None)
                return await api._json(f"/d2l/api/le/1.82/{api.course_id}/dropbox/folders/", "POST", payload)
            current = await self.operation(key + ":assignment", action, find)
        # Native copies already retain rubric associations; automatic rubric
        # association is intentionally blocked on older tenants at preflight.
        if (original.get("Assessment") or {}).get("Rubrics") and not data.get("native"):
            raise ValueError("Rubric transfer requires native Copy Components on this release.")
        home = self.category_id("assignments") if data.get('grade_item') is not False else None
        if data.get('grade_item') is False and current.get('GradeItemId'):
            raise ValueError(f"{original['Name']}: this task now has a grade item; review it in Brightspace.")
        if home:
            points = (original.get("Assessment") or {}).get("ScoreDenominator")
            if not points or points <= 0: raise ValueError(f"{original['Name']}: supply assignment maximum points before grading.")
            grade = await self.numeric_grade(key + ":grade", original["Name"], points, home, existing_id=current.get("GradeItemId"))
            async def associate():
                payload = {k: current.get(k) for k in ("CategoryId", "Name", "Availability", "GroupTypeId", "DueDate", "DisplayInCalendar",
                           "NotificationEmail", "IsHidden", "IsAnonymous", "DropboxType", "SubmissionType", "CompletionType", "AllowOnlyUsersWithSpecialAccess")}
                payload["CustomInstructions"] = {"Content": (current.get("CustomInstructions") or {}).get("Html", ""), "Type": 1}
                payload["Assessment"] = {"ScoreDenominator": points}
                payload["GradeItemId"] = grade["Id"]
                return await api._json(f"/d2l/api/le/1.82/{api.course_id}/dropbox/folders/{current['Id']}", "PUT", payload)
            async def verify_grade():
                a = await api._json(f"/d2l/api/le/1.82/{api.course_id}/dropbox/folders/{current['Id']}")
                if a.get("GradeItemId") == grade["Id"]: return a
            current = await self.operation(key + ":grade-association", associate, verify_grade)
        topic = await self.link_assignment(key, parent, current)
        self.activity_urls[str(original["Id"])] = urljoin(api.base, topic["Url"])
        return topic

    async def link_assignment(self, key, parent, assignment):
        """Reuse the live-verified native Add Existing adapter, by target ID.

        A hand-built URL would create a generic link instead of a Dropbox
        activity. Native linking preserves Brightspace's grade/activity metadata.
        """
        from content_checker import ContentChecker
        async def find():
            matches = [o for o in await self.target.children(parent) if o.get("ActivityType") == 3
                       and str(o.get("ToolItemId")) == str(assignment["Id"])]
            if len(matches) > 1: raise ValueError("Duplicate assignment Content links.")
            return matches[0] if matches else None
        if key not in self.journal and await find():
            # Existing target assignment selected explicitly; record its link.
            self.journal[key] = {"state": "verified", "result": await find()}; self.persist()
        async def action():
            self.target.guard()
            checker = object.__new__(ContentChecker)
            checker.log = self.log
            ok, reason = await checker._attach_existing_activity(self.target.page.context, self.target.base, self.target.course_id,
                {"target_module_id": parent, "activity_id": assignment["Id"], "activity_kind": "ASSIGN", "name": assignment["Name"]})
            if not ok:
                error = f"Assignment Add Existing did not verify: {reason}"
                if reason in ("Add Existing control was not available", "Assignments option was not available", "exact activity selection was unavailable or ambiguous"):
                    raise TransferRequestRejected(error)
                raise RuntimeError(error)
            return {}
        return await self.operation(key, action, find)

    async def validate_after(self):
        source = await self.source.inventory(include_html=True)
        target = await self.target.inventory(include_html=True)
        checks = {"Source inventory and scanned HTML unchanged": source_inventory_matches(self.plan, source)}
        for url, file in self.bundle.files.items():
            original, _ = await self.source.download(url)
            checks["Dependency source unchanged / " + file["filename"]] = hashlib.sha256(original).hexdigest() == file["source_hash"]
        objects = {str(o["Id"]): o for o in all_objects(target["modules"])}
        counts = {"workshops": 0, "required children": 0, "session overviews": 0, "files": len(self.bundle.files), "assignments": 0, "rubrics": 0}
        sections = {s["title"]: s for s in recipe_sections(self.plan["recipe"])}
        for wi, w in enumerate(self.plan["workshops"]):
            key = f"w{wi}"
            root_id = str(self.journal.get(key, {}).get("result", {}).get("Id", ""))
            root = objects.get(root_id)
            ordered = [o["Title"] for o in (root or {}).get("children", [])]
            checks[w["title"] + " / structure and order"] = bool(root and root["Title"] == w["title"] and ordered == self.expected_children(wi, w))
            if root: counts["workshops"] += 1; counts["required children"] += len(sections)
            for suffix in [s for s in ("introduction", "session") if key + ":" + s in self.journal]:
                id_ = str(self.journal.get(key + ":" + suffix, {}).get("result", {}).get("Id", ""))
                page = objects.get(id_, {})
                markup = page.get("html", "")
                forbidden = self.plan["recipe"].get("remove_" + suffix + "_sections", [])
                checks[w["title"] + " / " + suffix] = bool(markup) and not any(norm(s) in norm(BeautifulSoup(markup, "html.parser").get_text(" ")) for s in forbidden)
                if suffix == "session" and markup: counts["session overviews"] += 1
            for row in w["items"]:
                if row["section"] == "Skip": continue
                item_key = key + ":item:" + row["id"]
                topic = objects.get(str(self.journal.get(item_key, {}).get("result", {}).get("Id", "")), {})
                section = sections[row["section"]]
                expected_parent = root_id if section["kind"] == "inline" else \
                    self.journal.get(key + ":" + section["key"], {}).get("result", {}).get("Id")
                checks[row["title"] + " / destination"] = bool(topic) and str(topic.get("ParentModuleId")) == str(expected_parent)
                if row["source"].get("ActivityType") == 3:
                    original = assignment_for(self.plan, row)
                    data = self.assignments[item_key]
                    assignment_id = data.get("native", {}).get("Id") or self.journal[item_key + ":assignment"]["result"]["Id"]
                    matches = [a for a in target["assignments"] if str(a["Id"]) == str(assignment_id)]
                    a = matches[0] if len(matches) == 1 else {}
                    rubrics = (a.get("Assessment") or {}).get("Rubrics", [])
                    checks[row["title"] + " / assignment and rubrics"] = bool(a) and rubric_signature(rubrics) == rubric_signature((original.get("Assessment") or {}).get("Rubrics", []))
                    from transfer_guided import assignment_text
                    checks[row["title"] + " / instructions and assessment"] = bool(a) and assignment_text(a) == assignment_text(original) and (a.get("Assessment") or {}).get("ScoreDenominator") == (original.get("Assessment") or {}).get("ScoreDenominator")
                    checks[row["title"] + " / submission settings"] = bool(a) and all(a.get(field) == original.get(field) for field in ("DropboxType", "SubmissionType", "CompletionType", "IsAnonymous"))
                    if a: counts["assignments"] += 1; counts["rubrics"] += len(rubrics)
                    if self.category_id("assignments") and assignment_grading(self.plan, row):
                        grade = next((g for g in target["grades"] if g["Id"] == a.get("GradeItemId")), {})
                        checks[row["title"] + " / grade category"] = grade.get("CategoryId") == self.category_id("assignments")
                    elif row.get('grade_item') is False:
                        checks[row['title'] + ' / not in gradebook'] = bool(a) and not a.get('GradeItemId')
                if topic.get("html"):
                    self.bundle.check(topic["html"])
                if topic.get("IsBroken"):
                    checks[row["title"] + " / resource resolves"] = False
        for key, entry in self.journal.items():
            if key.startswith("dependency:") or key.endswith(":file"):
                result = entry.get("result", {})
                relative = result.get("url", "").removeprefix(self.plan["target"]["root"])
                checks[key + " / file readback"] = bool(relative) and await self.target.file_hash(relative) == result.get("hash")
        checks["All planned operations verified"] = all(entry.get("state") == "verified" for entry in self.journal.values())
        grades = self.plan["recipe"].get("gradebook", {})
        if grades.get("enabled"):
            active = {c["Name"]: c["Weight"] for c in target["categories"] if not c.get("ExcludeFromFinalGrade")}
            checks["Gradebook weights"] = active == {c["name"]: c["weight"] for c in grade_categories(grades)} and target["setup"]["GradingSystem"] == "Weighted"
            for item in grades.get("attendance_items", []):
                matches = [g for g in target["grades"] if g["Name"] == item["name"]]
                checks[item["name"] + " / attendance weighting"] = len(matches) == 1 and matches[0].get("CategoryId") == self.category_id("attendance") and matches[0].get("Weight") == item["weight"] and matches[0].get("MaxPoints") == item["max_points"]
        markers = [((o.get("Description") or {}).get("Html", "")) for o in objects.values()]
        own = [m for m in markers if "bpa-transfer:" + self.plan["id"] in m]
        checks["No duplicate transfer markers"] = len(own) == len(set(own))
        return checks, counts

    async def run(self, confirmed_target_id, sandbox_acknowledged):
        if confirmed_target_id != self.target.course_id or not sandbox_acknowledged:
            raise ValueError("Confirm the exact target sandbox course ID before applying.")
        if self.target.course_id != self.plan["target"]["id"] or self.source.course_id != self.plan["source"]["id"]:
            raise ValueError("Selected courses differ from the saved plan.")
        self.target.guard()
        errors, warnings = validate_plan(self.plan)
        if errors: raise ValueError("Plan is blocked:\n" + "\n".join(errors))
        signature = run_signature(self.plan)
        if self.plan.get("run_signature") and self.plan["run_signature"] != signature:
            raise ValueError("An applied plan cannot be edited. Resume the unchanged saved plan.")
        fresh_source = await self.source.inventory()
        if not source_inventory_matches(self.plan, fresh_source): raise ValueError("Source changed since scanning. Build a fresh plan.")
        fresh_target = await self.target.inventory()
        if fresh_target["name"] != self.plan["target"]["name"] or fresh_target["root"] != self.plan["target"]["root"]:
            raise ValueError("Target course identity or file root changed. Rescan.")
        if not self.journal and digest(fresh_target) != digest(self.plan["target"]): raise ValueError("Target changed since scanning. Refresh the preview before applying.")
        self.log(f"TARGET: {fresh_target['name']} ({self.target.course_id}); hidden content only.", "warning")
        self.log("Preparing self-contained files before course writes…", "info")
        await self.prepare()
        # Native fallback remains required even on future tenants until tested.
        for data in self.assignments.values():
            if (data["source"].get("Assessment") or {}).get("Rubrics") and not data.get("native"):
                raise ValueError("Copy assignments with rubrics natively, then map the target assignment IDs.")
        self.plan["run_signature"] = signature
        if not self.journal:
            backup = self.path.with_suffix(".recovery.json")
            save_plan({"source": fresh_source, "target": fresh_target, "plan_id": self.plan["id"]}, backup)
            self.plan["backup"] = str(backup)
        self.plan["prepared_files"] = len(self.bundle.files); self.persist()
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
            await self.gradebook()
            workshop_roots = []
            structure = recipe_sections(self.plan["recipe"])
            for wi, workshop in enumerate(self.plan["workshops"]):
                key = f"w{wi}"
                root = await self.ensure_object(key, None, workshop["title"], module=True)
                # `layout` is the unit's final child order. Inline sections are
                # filled with their items once those items exist.
                layout, sections = [], {}
                for section in structure:
                    if section["kind"] == "placeholder":
                        layout.append(await self.page(key + ":" + section["key"], root["Id"], section["title"]))
                    elif section["kind"] == "introduction":
                        layout.append(await self.page(key + ":introduction", root["Id"], section["title"]))
                    elif section["kind"] == "folder":
                        folder = await self.ensure_object(key + ":" + section["key"], root["Id"], section["title"], module=True)
                        layout.append(folder); sections[section["title"]] = folder["Id"]
                        if section["session_overview"]:
                            await self.page(key + ":session", folder["Id"], "Session Overview")
                    else:
                        sections[section["title"]] = root["Id"]
                        if section["session_overview"]:
                            layout.append(await self.page(key + ":session", root["Id"], "Session Overview"))
                        layout.append(("inline", section["title"]))
                workshop_roots.append((key, root, layout, sections, workshop))
                for row in workshop["items"]:
                    if row["section"] == SKIP: continue
                    if row["section"] not in sections: raise ValueError(f"{row['title']}: {row['section']!r} is not a section of this structure.")
                    item_key, parent = key + ":item:" + row["id"], sections[row["section"]]
                    if row["source"].get("ActivityType") == 3: self.created[item_key] = await self.assignment(item_key, parent)
            for original, file in self.bundle.files.items():
                await self.upload("dependency:" + digest(original)[:24], file["filename"], file["body"], file["type"])
            for key, root, layout, sections, workshop in workshop_roots:
                for row in workshop["items"]:
                    if row["section"] != SKIP and row["source"].get("ActivityType") != 3:
                        self.created[key + ":item:" + row["id"]] = await self.page(key + ":item:" + row["id"], sections[row["section"]], row["title"])
                required = []
                for entry in layout:
                    if isinstance(entry, tuple):
                        required.extend(self.created[key + ":item:" + row["id"]] for row in workshop["items"] if row["section"] == entry[1])
                    else:
                        required.append(entry)
                if [o["Id"] for o in await self.target.children(root["Id"])] != [o["Id"] for o in required]:
                    # Idempotent ordering of owned objects only. Readback below
                    # detects unexpected children rather than hiding them.
                    for obj in required: await self.target.order(obj["Id"])
            for index, item in enumerate(self.plan["recipe"].get("gradebook", {}).get("attendance_items", [])):
                await self.numeric_grade("attendance:" + str(index), item["name"], item["max_points"],
                                         self.category_id("attendance"), item["weight"])
            checks, counts = await self.validate_after()
            status = "COMPLETE WITH REVIEW NOTES" if all(checks.values()) else "VALIDATION FAILED"
            self.plan["validation"] = checks
            self.plan["counts"] = counts
            grade_spec = self.plan["recipe"].get("gradebook", {})
            grade_lines = [f"{c['name']}: {c['weight']}%" for c in grade_categories(grade_spec)] if grade_spec.get("enabled") else ["Gradebook changes disabled for this plan."]
            self.plan["report"] = "\n".join([f"STRUCTURED TRANSFER — {status}",
                f"Target: {fresh_target['name']} ({self.target.course_id})", "Created content is hidden for instructor review.",
                *[f"{name}: {count}" for name, count in counts.items()], "Files count includes resource dependencies; generated HTML pages are additional.",
                "", "Gradebook:", *grade_lines, "", "Validation:",
                *[f"{'PASS' if ok else 'FAIL'} {name}" for name, ok in checks.items()], "", "Review required:",
                *["- " + note for note in warnings], "", "Verified target objects (IDs):",
                *[f"{key}: {entry['result']['Id']}" for key, entry in self.journal.items() if entry.get("result", {}).get("Id")],
                "", "Saved plan and operation journal: " + str(self.path.resolve()), "Recovery inventory: " + str(Path(self.plan["backup"]).resolve())])
        except BaseException as exc:
            self.plan["report"] = f"STRUCTURED TRANSFER — INCOMPLETE\nTarget: {fresh_target['name']} ({self.target.course_id})\n{exc}\nResume the same saved plan. Created objects remain hidden.\nRecovery: {self.plan['backup']}"
            self.persist()
            self.path.with_suffix(".report.txt").write_text(self.plan["report"], encoding="utf-8")
            raise
        self.persist()
        self.path.with_suffix(".report.txt").write_text(self.plan["report"], encoding="utf-8")
        return self.plan


async def scan_transfer(page, source_id, target_id, recipe=None, log=None, stop=None):
    if source_id == target_id: raise ValueError("Source and target must be different courses.")
    source = await TransferAPI(page, source_id).inventory(log, stop)
    target = await TransferAPI(page, target_id).inventory(log, stop)
    if recipe is None:
        from transfer_guided import build_guided_plan
        return build_guided_plan(source, target)
    return build_plan(source, target, recipe, choose_templates(target, recipe, source))


async def with_transfer_browser(url, credentials, action, log=None, stop=None):
    # Reuse the existing cancellable authentication and browser lifetime.
    from bulk_page_creator import _with_browser
    return await _with_browser(url, credentials, lambda api: action(api.page), log, stop)


async def apply_transfer(page, plan, path, confirmed_target_id, sandbox_acknowledged, log=None, stop=None):
    with PlanLock(path):
        disk = load_plan(path)
        if disk["id"] != plan["id"] or digest(disk["journal"]) != digest(plan["journal"]):
            raise ValueError("The saved transfer journal changed. Reopen the saved plan.")
        source = TransferAPI(page, plan["source"]["id"])
        target = TransferAPI(page, plan["target"]["id"], source_id=plan["source"]["id"], writable=True)
        return await TransferRunner(source, target, plan, path, log, stop).run(confirmed_target_id, sandbox_acknowledged)
