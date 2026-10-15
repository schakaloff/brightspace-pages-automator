"""Explicit links to existing destination assignments; no activity mutations."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlencode

from bs4 import BeautifulSoup
from transfer_files import reference_key
from transfer_plan import all_objects, norm

HOST = "https://learn.okanagancollege.ca"


def assignment_url(course, assignment):
    """Build a destination QuickLink from the assignment's own ActivityId."""
    identity = urlsplit(assignment.get("ActivityId") or "")
    prefix = "/activities/dropbox/"
    code = identity.path[len(prefix):] if identity.path.startswith(prefix) else ""
    if identity.scheme == "https" and identity.netloc == "ids.brightspace.com" and re.fullmatch(r"[\w-]+", code, re.ASCII):
        return HOST + "/d2l/common/dialogs/quickLink/quickLink.d2l?" + urlencode({"ou": course["id"], "type": "dropbox", "rcode": code})
    # Older catalogues may omit ActivityId; a linked Content topic is authoritative.
    urls = {urljoin(HOST, o["Url"]) for o in all_objects(course["modules"]) if o.get("ActivityType") == 3 and
            str(o.get("ToolItemId")) == str(assignment["Id"]) and o.get("Url")}
    if len(urls) == 1:
        url = urls.pop()
        from urllib.parse import parse_qs
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.netloc == urlsplit(HOST).netloc and parse_qs(parsed.query).get("ou") == [str(course["id"])]: return url
    raise ValueError(f"{assignment['Name']}: the destination assignment link is unavailable. Scan both courses again.")


def references(plan):
    """Known assignment links in planned source pages, including split-page parts."""
    from transfer_outline import placements, source_of
    placed = placements(plan)
    index = {}
    for row in plan["items"].values():
        if row["kind"] != "assignment": continue
        obj = source_of(plan, row)
        if obj.get("Url") and obj.get("ToolItemId"):
            key = reference_key(urljoin(HOST, obj["Url"]))
            current = index.get(key)
            if current is None or row["id"] in placed: index[key] = row
    result = []
    for id_ in placed:
        page = plan["items"].get(id_)
        if not page or page["kind"] != "page": continue
        obj = source_of(plan, page)
        markup = page["piece"]["html"] if page.get("piece") else obj.get("html", "")
        found = set()
        for link in BeautifulSoup(markup, "html.parser").find_all("a", href=True):
            key = reference_key(urljoin(urljoin(HOST, obj.get("Url") or plan["source"]["root"]), link["href"]))
            activity = index.get(key)
            if activity and activity["id"] not in placed and key not in found:
                result.append((page, activity, key)); found.add(key)
    return result


def existing_assignment(plan, source_id, target_id, target=None):
    target = target or plan["target"]
    original = next((a for a in plan["source"]["assignments"] if str(a["Id"]) == str(source_id)), None)
    chosen = next((a for a in target["assignments"] if str(a["Id"]) == str(target_id)), None)
    if not original or not chosen or norm(original["Name"]) != norm(chosen["Name"]):
        raise ValueError("The chosen destination assignment is missing or has a different name. Scan both courses again.")
    return assignment_url(target, chosen)


def rewrites(plan, target=None):
    from transfer_outline import source_of
    chosen = plan.get("activity_link_targets", {})
    result = {}
    for row in plan["items"].values():
        if row["kind"] != "assignment": continue
        obj = source_of(plan, row)
        if not obj.get("Url") or not obj.get("ToolItemId"): continue
        source_id = str(source_of(plan, row)["ToolItemId"])
        destination = chosen.get(source_id)
        if destination:
            key = reference_key(urljoin(HOST, obj["Url"]))
            try: result[key] = existing_assignment(plan, source_id, destination, target)
            except ValueError as exc: raise ValueError(f"{row['title']}: {exc}") from None
    return result


def issues(plan):
    from transfer_outline import source_of
    result = []
    for page, activity, _ in references(plan):
        source_id = str(source_of(plan, activity)["ToolItemId"])
        original = next((a for a in plan["source"]["assignments"] if str(a["Id"]) == source_id), None)
        name = original["Name"] if original else activity["title"]
        issue = {"level": "error", "row": page["id"], "node": None}
        destination = plan.get("activity_link_targets", {}).get(source_id)
        if destination:
            try: existing_assignment(plan, source_id, destination)
            except ValueError as exc: issue["text"] = f"{page['title']}: {exc}"
            else:
                issue.update(level="note", text=f"{page['title']}: its “{name}” link will open the existing destination assignment, keeping that assignment's settings and rubric.")
        else:
            issue["text"] = f"{page['title']} links to “{name}”, which is not included in this transfer. Choose an existing destination assignment or Skip this page."
            candidates = [a for a in plan["target"]["assignments"] if norm(a["Name"]) == norm(name)]
            if len(candidates) == 1:
                try: assignment_url(plan["target"], candidates[0])
                except ValueError: pass
                else: issue.update(fix="use_existing_link", link_row=activity["id"], target_id=str(candidates[0]["Id"]))
        result.append(issue)
    return result
