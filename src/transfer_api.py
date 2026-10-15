"""Authenticated, target-guarded adapter around existing Brightspace APIs."""
from __future__ import annotations

import hashlib
import asyncio
import re
from urllib.parse import quote, urljoin, urlparse

from book_migration import BrightspaceBookAPI
from unit_overview import BrowserContentAPI
from transfer_plan import all_objects


class TransferAPI(BrightspaceBookAPI):
    def __init__(self, page, course_id, *, source_id=None, writable=False):
        super().__init__(page, course_id)
        if not str(course_id).isdigit() or source_id is not None and not str(source_id).isdigit():
            raise ValueError("Invalid course identifier.")
        self.source_id = str(source_id) if source_id is not None else str(course_id)
        self.writable = writable
        if writable and self.source_id == self.course_id:
            raise ValueError("Source course is read only.")

    def guard(self):
        if not self.writable or self.source_id == self.course_id:
            raise PermissionError("Writes are allowed only on the explicitly selected target.")

    async def _json(self, path, method="GET", payload=None):
        if method != "GET":
            self.guard()
        if not re.match(rf"^/d2l/api/(?:le|lp)/[\d.]+/{self.course_id}/", path) and path != f"/d2l/api/lp/1.49/courses/{self.course_id}":
            raise PermissionError("API request is outside the bound course.")
        return await self.page.evaluate("""async ([path,method,payload]) => {
            const headers={Accept:'application/json'};
            if(method!=='GET') {
                const token=localStorage.getItem('XSRF.Token');
                if(!token) throw new Error('Brightspace security token missing. Sign in again.');
                headers['X-Csrf-Token']=token; headers['Content-Type']='application/json';
            }
            const r=await fetch(path,{method,credentials:'include',redirect:'error',cache:'no-store',headers,
                body:payload===null?undefined:JSON.stringify(payload)});
            const text=await r.text();
            if(!r.ok) throw new Error(`${method} ${path}: HTTP ${r.status} ${text.slice(0,160)}`);
            if(text && !(r.headers.get('content-type')||'').includes('json')) throw new Error('Non-JSON response; sign in again.');
            return text?JSON.parse(text):{};
        }""", [path, method, payload])

    async def upload_file(self, *args, **kwargs):
        self.guard()
        try:
            return await super().upload_file(*args, **kwargs)
        except Exception as exc:
            raise RuntimeError(str(exc).split("Call log:")[0].strip()) from None

    async def download(self, url):
        parsed = urlparse(urljoin(self.base, url))
        if parsed.scheme != "https" or parsed.netloc != urlparse(self.base).netloc or parsed.username:
            raise ValueError("Course-file download must stay on the authenticated Brightspace host.")
        try:
            response = await self.page.context.request.get(parsed.geturl(), max_redirects=0, max_retries=2, timeout=60000)
        except Exception as exc:
            raise RuntimeError(str(exc).split("Call log:")[0].strip()) from None
        if response.status != 200:
            raise RuntimeError(f"Course-file download: HTTP {response.status}: {parsed.path}")
        body = await response.body()
        type_ = response.headers.get("content-type", "application/octet-stream")
        if not body or len(body) > 128 * 1024 * 1024:
            raise ValueError("Course file is empty or exceeds the 128 MB transfer limit.")
        if b'id="userName"' in body[:10000] or b'login.microsoftonline.com' in body[:10000]:
            raise RuntimeError("Course-file response was a login page.")
        return body, type_

    async def file_info(self, url):
        """(HTTP status, size in bytes) of a course file, without downloading it."""
        parsed = urlparse(urljoin(self.base, url))
        if parsed.scheme != "https" or parsed.netloc != urlparse(self.base).netloc:
            raise ValueError("Course files must be on the authenticated Brightspace host.")
        try:
            response = await self.page.context.request.head(parsed.geturl(), max_redirects=0, timeout=60000)
        except Exception as exc:
            raise RuntimeError(str(exc).split("Call log:")[0].strip()) from None
        # Brightspace answers a missing course file with a redirect to its error page, not a 404.
        if response.status in (301, 302, 303, 307) and "/d2l/error/404" in (response.headers.get("location") or ""):
            return 404, 0
        return response.status, int(response.headers.get("content-length") or 0)

    async def inventory(self, log=None, stop=None, include_html=True):
        details = await self._json(f"/d2l/api/lp/1.49/courses/{self.course_id}")
        root = await self.course_root()
        roots = await self._json(f"/d2l/api/le/1.82/{self.course_id}/content/root/")
        seen = set()
        async def walk(entries):
            if not isinstance(entries, list): raise ValueError("Invalid content inventory.")
            result = []
            for entry in entries:
                if stop and stop.is_set(): raise InterruptedError("Scan stopped.")
                entry = dict(entry)
                id_ = str(entry["Id"])
                if id_ in seen: raise ValueError("Repeated content ID in course outline.")
                seen.add(id_)
                if entry["Type"] == 0:
                    entry["children"] = await walk(await self._json(f"/d2l/api/le/1.82/{self.course_id}/content/modules/{id_}/structure/"))
                result.append(entry)
            return result
        modules = await walk(roots)
        if include_html:
            limit = asyncio.Semaphore(4)
            async def read_file(entry):
                async with limit:
                    if stop and stop.is_set(): raise InterruptedError("Scan stopped.")
                    id_, url = str(entry["Id"]), entry.get("Url") or ""
                    if not url:
                        full = await self._json(f"/d2l/api/le/1.82/{self.course_id}/content/topics/{id_}")
                        url = full.get("Url") or ""
                        entry["Url"] = url
                    if re.search(r"\.html?(?:[?#]|$)", url, re.I):
                        try:
                            entry["html"] = await BrowserContentAPI(self.page, self.course_id, str(entry.get("ParentModuleId", 0))).get_topic_html(id_)
                        except Exception as exc:
                            message = str(exc).splitlines()[0]
                            context = f"{details['Name']}: {entry['Title']} (page {id_})"
                            if not re.search(r"GET topic file 404\b", message):
                                raise ValueError(f"{context}: could not read the page. {message}") from None
                            # An imported outline may refer to files absent from its ZIP.
                            # Retain the row for review; never substitute empty HTML.
                            entry["html_error"] = "The page file is missing in Brightspace (404)."
                            if log: log(f"{context}: {entry['html_error']} Restore its file or Skip it in the transfer.", "warning")
            results = await asyncio.gather(*(read_file(e) for e in all_objects(modules) if e.get("Type") == 1 and e.get("ActivityType") == 1), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException): raise result
        assignments = await self._json(f"/d2l/api/le/1.82/{self.course_id}/dropbox/folders/")
        categories = await self._json(f"/d2l/api/le/1.98/{self.course_id}/grades/categories/")
        grades = await self._json(f"/d2l/api/le/1.89/{self.course_id}/grades/")
        setup = await self._json(f"/d2l/api/le/1.82/{self.course_id}/grades/setup/")
        if not all(isinstance(x, list) for x in (assignments, categories, grades)):
            raise ValueError("Incomplete assignment/grade inventory. Nothing can be applied.")
        versions = await self.page.evaluate("""async () => {
            const r=await fetch('/d2l/api/versions/',{credentials:'include',redirect:'error'});
            if(!r.ok) throw new Error('Could not discover API versions.');
            return (await r.json()).find(p=>p.ProductCode==='le')?.SupportedVersions || [];
        }""")
        from transfer_components import activity_lists
        activities = await activity_lists(self)
        if log: log(f"Scanned {details['Name']}: {len(seen)} content objects, {len(assignments)} assignments.", "info")
        return {"id": self.course_id, "name": details["Name"], "root": root,
                "modules": modules, "assignments": assignments, "categories": categories,
                "grades": grades, "setup": setup, "versions": versions, **activities}

    async def children(self, parent):
        if parent is None:
            return await self._json(f"/d2l/api/le/1.82/{self.course_id}/content/root/")
        return await self._json(f"/d2l/api/le/1.82/{self.course_id}/content/modules/{parent}/structure/")

    async def create_object(self, parent, payload):
        self.guard()
        path = "content/root/" if parent is None else f"content/modules/{parent}/structure/"
        return await self._json(f"/d2l/api/le/1.82/{self.course_id}/{path}", "POST", payload)

    async def hide_topic(self, topic_id):
        """Hide an owned native Content reference without changing its tool or dates."""
        self.guard()
        path = f"/d2l/api/le/1.82/{self.course_id}/content/topics/{topic_id}"
        topic = await self._json(path)
        payload = {k: topic[k] for k in ("Title", "ShortTitle", "Type", "TopicType", "Url", "StartDate", "EndDate",
                                       "DueDate", "IsLocked", "OpenAsExternalResource") if k in topic}
        description = topic.get("Description")
        payload["Description"] = ({"Content": description.get("Html", ""), "Type": 1} if description is not None else None)
        payload["IsHidden"] = True
        return await self._json(path, "PUT", payload)

    async def find_owned(self, parent, marker):
        matches = [o for o in await self.children(parent) if marker in (o.get("Description") or {}).get("Html", "")]
        if len(matches) > 1: raise ValueError("Duplicate transfer ownership markers; manual review required.")
        return matches[0] if matches else None

    async def file_hash(self, relative):
        body, _ = await self.download(f"{self.base}/d2l/api/lp/1.49/{self.course_id}/managefiles/file?path={quote(relative, safe='')}")
        return hashlib.sha256(body).hexdigest()

    async def order(self, object_id):
        self.guard()
        await self._json(f"/d2l/api/le/1.82/{self.course_id}/content/order/objectId/{object_id}?position=last", "POST")


def choose_templates(target, recipe, source=None):
    results = {}
    for kind in ("introduction", "session"):
        spec = recipe.get("templates", {})
        if spec.get(kind + '_course') == 'builtin':
            from transfer_guided import standard_template
            results[kind] = standard_template(target, kind)
            continue
        id_ = str(spec.get(kind + "_id", ""))
        course = source if spec.get(kind + "_course") == "source" else target
        if course is None: raise ValueError("Template source course is unavailable.")
        matches = [o for o in all_objects(course["modules"]) if o.get("Type") == 1 and
                   (str(o["Id"]) == id_ if id_ else o["Title"].strip().casefold() == spec.get(kind + "_title", "").strip().casefold())]
        if len(matches) == 1 and matches[0].get("html"):
            results[kind] = {"id": str(matches[0]["Id"]), "course": course["id"], "url": matches[0].get("Url"), "html": matches[0]["html"]}
    return results
