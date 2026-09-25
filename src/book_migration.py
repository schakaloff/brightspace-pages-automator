"""Moodle Book chapter discovery and safe Brightspace migration helpers.

Content modules hold chapter pages. Supporting assets live only in Manage Files.
This module deliberately keeps discovery and rendering separate from writes so a
caller can inspect the complete plan before changing a course.
"""

from __future__ import annotations

import hashlib
import html
import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup, Comment


_MOODLE_HOST = "mymoodle.okanagan.bc.ca"
_ASSET_ATTRS = (("a", "href"), ("img", "src"), ("source", "src"),
                ("video", "src"), ("video", "poster"), ("audio", "src"),
                ("embed", "src"), ("object", "data"))


@dataclass(frozen=True)
class BookChapter:
    title: str
    source_url: str
    chapter_id: str
    body_html: str


def _chapter_id(url: str) -> str:
    return (parse_qs(urlparse(url).query).get("chapterid") or ["first"])[0]


def _title(text: str) -> str:
    return re.sub(r"^\s*\d+(?:\.\d+)*\.\s*", "", text or "").strip()


def _clean_chapter(raw_html: str, page_url: str) -> str:
    soup = BeautifulSoup(raw_html, "lxml")
    root = soup.body or soup
    for tag in root.find_all(["script", "noscript", "form", "nav"]):
        tag.decompose()
    for comment in root.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for tag in root.find_all(True):
        for attr in ("href", "src", "poster", "data"):
            value = tag.get(attr)
            if value and not value.startswith(("#", "data:", "javascript:")):
                tag[attr] = urljoin(page_url, value)
    return "".join(str(child) for child in root.contents).strip()


def parse_book_index(markup: str, book_url: str) -> list[tuple[str, str]]:
    """Return chapter title/URL pairs in Moodle's table-of-contents order."""
    soup = BeautifulSoup(markup, "lxml")
    current = soup.select_one("#mod_book-chapter > h3, .book_content > h3")
    if current is None:
        raise ValueError("Moodle Book chapter heading was not found")
    result = [(_title(current.get_text(" ", strip=True)), book_url)]
    book_id = (parse_qs(urlparse(book_url).query).get("id") or [""])[0]
    seen = {_chapter_id(book_url)}
    for anchor in soup.select(".book_toc a[href], [class*=book_toc] a[href]"):
        url = urljoin(book_url, anchor.get("href", ""))
        parsed = urlparse(url)
        if not parsed.path.endswith("/mod/book/view.php"):
            continue
        if (parse_qs(parsed.query).get("id") or [""])[0] != book_id:
            continue
        chapter_id = _chapter_id(url)
        if chapter_id in seen:
            continue
        seen.add(chapter_id)
        result.append((_title(anchor.get_text(" ", strip=True)), url))
    if not result[0][0]:
        raise ValueError("Moodle Book table of contents is incomplete")
    return result


def parse_chapter(markup: str, page_url: str, expected_title: str = "") -> BookChapter:
    soup = BeautifulSoup(markup, "lxml")
    heading = soup.select_one("#mod_book-chapter > h3, .book_content > h3")
    body = soup.select_one("#mod_book-chapter > .no-overflow, .book_content > .no-overflow")
    if heading is None or body is None:
        raise ValueError(f"Moodle Book chapter content was not found: {page_url}")
    title = _title(heading.get_text(" ", strip=True))
    if expected_title and title.casefold() != expected_title.casefold():
        raise ValueError(f"Chapter changed while reading: {expected_title!r} → {title!r}")
    cleaned = _clean_chapter(body.decode_contents(), page_url)
    content = BeautifulSoup(cleaned, "lxml")
    if not cleaned or (not content.get_text(" ", strip=True)
                       and not content.find(["img", "video", "iframe"])):
        raise ValueError(f"Moodle Book chapter is blank: {title}")
    return BookChapter(title, page_url, _chapter_id(page_url), cleaned)


async def read_book(context, book_url: str) -> list[BookChapter]:
    page = await context.new_page()
    try:
        response = await page.goto(book_url, wait_until="domcontentloaded", timeout=30000)
        if not response or not response.ok:
            raise RuntimeError(f"Moodle Book returned HTTP {response.status if response else 'no response'}")
        index = parse_book_index(await page.content(), book_url)
        chapters: list[BookChapter] = []
        for title, url in index:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if not response or not response.ok:
                raise RuntimeError(f"Moodle chapter returned HTTP {response.status if response else 'no response'}: {title}")
            chapters.append(parse_chapter(await page.content(), url, title))
        if len(chapters) != len(index):
            raise RuntimeError("Moodle Book chapter count changed while reading")
        return chapters
    finally:
        await page.close()


def chapter_asset_urls(chapter: BookChapter) -> set[str]:
    soup = BeautifulSoup(chapter.body_html, "lxml")
    urls = set()
    for tag_name, attr in _ASSET_ATTRS:
        for tag in soup.find_all(tag_name):
            url = tag.get(attr) or ""
            if urlparse(url).hostname == _MOODLE_HOST and "/pluginfile.php/" in urlparse(url).path:
                urls.add(url)
    return urls


def asset_filename(url: str, same_names: set[str] | None = None) -> str:
    name = unquote(PurePosixPath(urlparse(url).path).name).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:140] or "file"
    if same_names and name.casefold() in same_names:
        stem = PurePosixPath(name).stem
        suffix = PurePosixPath(name).suffix
        return f"{stem}-{hashlib.sha256(url.encode()).hexdigest()[:8]}{suffix}"
    return name


def rewrite_chapter_links(chapter: BookChapter, assets: dict[str, str],
                          activities: dict[str, str], book_pages: dict[str, str],
                          activity_urls: dict[str, str] | None = None,
                          videos: dict[str, str] | None = None,
                          flag_unmatched_lti: bool = False) -> tuple[str, list[str]]:
    """Rewrite source URLs; report Moodle references that lack a safe target."""
    soup = BeautifulSoup(chapter.body_html, "lxml")
    root = soup.body or soup
    unresolved: list[str] = []
    for tag in root.find_all(True):
        if tag.name == "iframe" and tag.get("src") in (videos or {}):
            source = tag["src"]
            replacement = soup.new_tag("video", controls="")
            replacement["style"] = "width:100%;max-width:100%;height:auto"
            media = soup.new_tag("source", src=videos[source])
            media["type"] = mimetypes.guess_type(urlparse(videos[source]).path)[0] or "video/mp4"
            replacement.append(media)
            tag.replace_with(replacement)
            continue
        for attr in ("href", "src", "poster", "data"):
            source = tag.get(attr)
            if not source:
                continue
            parsed = urlparse(source)
            if parsed.hostname != _MOODLE_HOST:
                continue
            if source in assets:
                tag[attr] = assets[source]
            elif "/mod/book/view.php" in parsed.path:
                target = book_pages.get(_chapter_id(source))
                if target:
                    tag[attr] = target
                else:
                    unresolved.append(source)
            elif tag.name == "a" and source in (activity_urls or {}):
                tag[attr] = activity_urls[source]
            elif tag.name == "a" and tag.get_text(" ", strip=True).casefold() in activities:
                tag[attr] = activities[tag.get_text(" ", strip=True).casefold()]
            elif (flag_unmatched_lti and tag.name == "a"
                  and parsed.path.endswith("/mod/lti/view.php")):
                note = soup.new_tag("span")
                note["class"] = "bpa-pending-activity"
                note["role"] = "note"
                note.string = (tag.get_text(" ", strip=True)
                               + " (Brightspace activity link pending)")
                tag.replace_with(note)
                break
            else:
                unresolved.append(source)
    return "".join(str(child) for child in root.contents), sorted(set(unresolved))


def book_style(existing_html: str) -> str:
    """Reuse the former page's colors and type rules without its nested markup."""
    soup = BeautifulSoup(existing_html or "", "lxml")
    style = soup.find("style")
    flag_style = """
      .bpa-pending-activity { display:inline-block; padding:.35rem .6rem;
        border-left:4px solid #b45f06; background:#fff4df; color:#59320a;
        font-weight:600; }
    """
    if style and ".main-container" in style.get_text():
        return style.get_text() + flag_style
    return """
      body { margin:0; background:#f4f5f7; color:#343a40; font-family:Arial,sans-serif; }
      .main-container {max-width:1000px; margin:2rem auto; background:white; border-radius:16px; overflow:hidden;}
      .hero {background:#782434; color:white; padding:2rem 3rem;}
      .hero h1 {margin:0;} .content-body {padding:2rem 3rem;}
      .section-header {color:#782434;} img,video,iframe {max-width:100%;}
    """ + flag_style


def render_chapter(book_title: str, chapter: BookChapter, rewritten_html: str,
                   existing_html: str = "") -> str:
    style = book_style(existing_html)
    marker = html.escape(f"{_chapter_id(chapter.source_url)}", quote=True)
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<style>{style}</style></head><body>"
            f"<div class=\"main-container\" data-bpa-book-chapter=\"{marker}\">"
            f"<div class=\"hero\"><h1>{html.escape(book_title)}</h1></div>"
            f"<div class=\"content-body\"><h2 class=\"section-header\">{html.escape(chapter.title)}</h2>"
            f"<div class=\"action-card\">{rewritten_html}</div></div></div></body></html>")


class BrightspaceBookAPI:
    """Authenticated course-file and Content operations for one course."""

    def __init__(self, page, course_id: str):
        self.page = page
        self.course_id = str(course_id)
        parsed = urlparse(page.url)
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.root = ""

    async def _json(self, path: str, method: str = "GET", payload: dict | None = None):
        return await self.page.evaluate("""async ([path, method, payload]) => {
          const headers = {'Accept':'application/json'};
          if (method !== 'GET') {
            const token=localStorage.getItem('XSRF.Token');
            if (!token) throw new Error('Brightspace anti-forgery token unavailable');
            headers['X-Csrf-Token']=token;
            headers['Content-Type']='application/json';
          }
          const r=await fetch(path,{method,credentials:'include',headers,
              body: payload === null ? undefined : JSON.stringify(payload)});
          const body=await r.text();
          if (!r.ok) throw new Error(`${method} ${path}: HTTP ${r.status} ${body.slice(0,200)}`);
          return body ? JSON.parse(body) : {};
        }""", [path, method, payload])

    async def course_root(self) -> str:
        if not self.root:
            course = await self._json(f"/d2l/api/lp/1.49/courses/{self.course_id}")
            path = str(course.get("Path") or "")
            if not re.fullmatch(r"/content/enforced/[^/]+/?", path):
                raise RuntimeError(f"Unexpected Brightspace course path: {path!r}")
            self.root = path.rstrip("/") + "/"
        return self.root

    async def module_structure(self, module_id: str) -> list[dict]:
        return await self._json(f"/d2l/api/le/1.0/{self.course_id}/content/modules/{module_id}/structure/")

    async def module(self, module_id: str) -> dict:
        return await self._json(f"/d2l/api/le/1.0/{self.course_id}/content/modules/{module_id}")

    async def set_module_hidden(self, module_id: str, hidden: bool) -> None:
        from unit_overview import _module_update_payload, extract_description_html

        current = await self.module(module_id)
        payload = _module_update_payload(current, extract_description_html(current))
        payload["IsHidden"] = bool(hidden)
        await self._json(f"/d2l/api/le/1.0/{self.course_id}/content/modules/{module_id}",
                         "PUT", payload)
        verify = await self.module(module_id)
        if bool(verify.get("IsHidden")) != bool(hidden):
            raise RuntimeError("Brightspace did not save the book folder visibility")

    async def ensure_book_module(self, parent_id: str, title: str) -> dict:
        children = await self.module_structure(parent_id)
        matches = [c for c in children if c.get("Type") == 0 and c.get("Title", "").casefold() == title.casefold()]
        if len(matches) > 1:
            raise RuntimeError(f"More than one Content folder is named {title!r}")
        if matches:
            return matches[0]
        payload = {"Title": title, "ShortTitle": "", "Type": 0,
                   "ModuleStartDate": None, "ModuleEndDate": None, "ModuleDueDate": None,
                   "IsHidden": True, "IsLocked": False, "Description": None}
        created = await self._json(
            f"/d2l/api/le/1.0/{self.course_id}/content/modules/{parent_id}/structure/",
            "POST", payload)
        if created.get("Id"):
            return created
        matches = [c for c in await self.module_structure(parent_id)
                   if c.get("Type") == 0 and c.get("Title", "").casefold() == title.casefold()]
        if len(matches) != 1:
            raise RuntimeError("Book Content folder was created but could not be identified")
        return matches[0]

    async def ensure_files_folder(self, folder: str) -> None:
        path = quote(folder, safe="")
        try:
            await self._json(f"/d2l/api/lp/1.49/{self.course_id}/managefiles/?path={path}")
            return
        except Exception as exc:
            if "HTTP 404" not in str(exc):
                raise
        await self._json(f"/d2l/api/lp/1.49/{self.course_id}/managefiles/folder",
                         "POST", {"RelativePath": folder})
        await self._json(f"/d2l/api/lp/1.49/{self.course_id}/managefiles/?path={path}")

    async def folder_file_names(self, folder: str) -> set[str]:
        path = f"/d2l/api/lp/1.49/{self.course_id}/managefiles/?path={quote(folder, safe='')}"
        names: set[str] = set()
        seen: set[str] = set()
        while path and path not in seen:
            seen.add(path)
            data = await self._json(path)
            names.update(str(item.get("Name") or "") for item in data.get("Objects", [])
                         if item.get("FileSystemObjectType") == 2)
            path = data.get("Next") or ""
        return names

    async def file_exists(self, relative_path: str) -> bool:
        url = f"{self.base}/d2l/api/lp/1.49/{self.course_id}/managefiles/file?path={quote(relative_path, safe='')}"
        response = await self.page.context.request.get(url, timeout=30000, fail_on_status_code=False)
        if response.status == 404:
            return False
        if response.status != 200:
            raise RuntimeError(f"Cannot inspect course file {relative_path}: HTTP {response.status}")
        return True

    async def upload_file(self, folder: str, filename: str, blob: bytes, content_type: str,
                          overwrite: bool = False) -> str:
        """Upload directly to Manage Files, creating no visible Content topic."""
        relative = f"{folder}/{filename}"
        if not overwrite and await self.file_exists(relative):
            return (await self.course_root()) + quote(relative, safe="/")
        token = await self.page.evaluate("() => localStorage.getItem('XSRF.Token') || ''")
        if not token:
            raise RuntimeError("Brightspace anti-forgery token unavailable")
        url = f"{self.base}/d2l/api/lp/1.49/{self.course_id}/managefiles/file/upload"
        start = await self.page.context.request.post(
            url, headers={"X-Csrf-Token": token, "X-Upload-Content-Type": content_type,
                          "X-Upload-Content-Length": str(len(blob)), "X-Upload-File-Name": filename},
            max_redirects=0, fail_on_status_code=False, timeout=60000)
        if start.status != 308:
            raise RuntimeError(f"Manage Files upload start failed: HTTP {start.status}")
        location = start.headers.get("location") or ""
        if not location:
            raise RuntimeError("Manage Files upload returned no upload location")
        upload_url = urljoin(self.base, location)
        if urlparse(upload_url).netloc != urlparse(self.base).netloc:
            raise RuntimeError("Manage Files upload redirected outside Brightspace")
        key = urlparse(upload_url).path.rstrip("/").split("/")[-1]
        transfer = await self.page.context.request.post(
            upload_url, data=blob, headers={"Content-Type": content_type},
            max_redirects=0, fail_on_status_code=False, timeout=120000)
        if transfer.status != 200:
            raise RuntimeError(f"Manage Files data upload failed: HTTP {transfer.status}")
        save = await self.page.context.request.post(
            f"{self.base}/d2l/api/lp/1.49/{self.course_id}/managefiles/file/save?overwriteFile={'true' if overwrite else 'false'}",
            form={"fileKey": key, "relativePath": folder, "name": filename},
            headers={"X-Csrf-Token": token}, fail_on_status_code=False, timeout=60000)
        if save.status != 200:
            raise RuntimeError(f"Manage Files save failed: HTTP {save.status}: {(await save.text())[:160]}")
        if not await self.file_exists(relative):
            raise RuntimeError(f"Uploaded course file was not found: {relative}")
        return (await self.course_root()) + quote(relative, safe="/")

    async def create_chapter_topic(self, module_id: str, folder: str, title: str,
                                   chapter_id: str, source_html: str) -> dict:
        """Create a hidden chapter page and verify the saved HTML."""
        from content_preservation import content_is_preserved
        from unit_overview import BrowserContentAPI

        filename = f"chapter-{chapter_id}.html"
        file_url = await self.upload_file(folder, filename, source_html.encode("utf-8"),
                                          "text/html", overwrite=True)
        descriptor = {
            "Title": title, "ShortTitle": "", "Type": 1, "TopicType": 1,
            "Url": unquote(file_url), "StartDate": None, "EndDate": None, "DueDate": None,
            "IsHidden": True, "IsLocked": False, "OpenAsExternalResource": None,
            "Description": None,
        }
        topic = await self._json(
            f"/d2l/api/le/1.0/{self.course_id}/content/modules/{module_id}/structure/",
            "POST", descriptor)
        if not topic.get("Id"):
            matches = [item for item in await self.module_structure(module_id)
                       if unquote(str(item.get("Url") or "")) == unquote(file_url)]
            if len(matches) != 1:
                raise RuntimeError(f"Chapter topic was created but not identified: {title}")
            topic = matches[0]
        api = BrowserContentAPI(self.page, self.course_id, str(module_id))
        topic_id = topic.get("Id")
        if not topic_id:
            raise RuntimeError(f"Brightspace did not identify new chapter {title!r}")
        readback = await api.get_topic_html(topic_id)
        preserved, reason = content_is_preserved(source_html, readback)
        if not preserved:
            raise RuntimeError(f"Chapter {title!r} failed read-back verification: {reason}")
        return topic

    async def update_generated_chapter(self, module_id: str, folder: str,
                                       chapter: BookChapter, source_html: str,
                                       topic: dict) -> None:
        """Refresh only a hidden page previously generated for this chapter."""
        from content_preservation import content_is_preserved
        from unit_overview import BrowserContentAPI

        api = BrowserContentAPI(self.page, self.course_id, str(module_id))
        current = await api.get_topic_html(topic["Id"])
        marker = f'data-bpa-book-chapter="{chapter.chapter_id}"'
        expected_file = f"/{folder}/chapter-{chapter.chapter_id}.html"
        if (not topic.get("IsHidden") or marker not in current
                or not unquote(str(topic.get("Url") or "")).endswith(expected_file)):
            raise RuntimeError("Existing chapter may have been edited; leaving it unchanged")
        await self.upload_file(folder, f"chapter-{chapter.chapter_id}.html",
                               source_html.encode("utf-8"), "text/html", overwrite=True)
        readback = await api.get_topic_html(topic["Id"])
        preserved, reason = content_is_preserved(source_html, readback)
        if not preserved:
            raise RuntimeError(f"Updated chapter failed read-back verification: {reason}")


def _unique_activity_targets(bs_flat: list[dict]) -> dict[str, str]:
    grouped: dict[str, set[str]] = {}
    for item in bs_flat:
        if item.get("kind") != "TOPIC" or not item.get("title") or not item.get("id"):
            continue
        key = item["title"].strip().casefold()
        url = f"/d2l/le/lessons/{{course_id}}/topics/{item['id']}"
        grouped.setdefault(key, set()).add(url)
    return {key: next(iter(urls)) for key, urls in grouped.items() if len(urls) == 1}


async def _moodle_asset(context, url: str) -> tuple[bytes, str, str]:
    from rebuild_helpers import extract_pluginfile_url, validate_download_response

    response = await context.request.get(url, timeout=120000, fail_on_status_code=False)
    body = await response.body()
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type.startswith("text/html"):
        target = extract_pluginfile_url(body.decode("utf-8", errors="replace"), response.url)
        if target:
            response = await context.request.get(target, timeout=120000, fail_on_status_code=False)
            body = await response.body()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    valid, reason = validate_download_response(response.status, content_type, response.url, body)
    if not valid:
        raise RuntimeError(f"Moodle asset failed: {reason}: {url}")
    if content_type.startswith("text/html"):
        raise RuntimeError(f"Moodle returned HTML for a file: {url}")
    return (body, content_type or mimetypes.guess_type(urlparse(response.url).path)[0]
            or "application/octet-stream", response.url)


async def migrate_book(context, bs_page, course_id: str, parent_id: str,
                       book_title: str, book_url: str, bs_flat: list[dict],
                       moodle_items: list[dict], log, should_stop=lambda: False,
                       flag_unmatched_lti: bool = False) -> dict:
    """Create/reuse chapter pages and publish a verified, complete Book.

    Unresolved references leave the new folder hidden. After all chapters
    verify, the old combined page is retained as a hidden backup.
    """
    chapters = await read_book(context, book_url)
    if should_stop():
        return {"created": 0, "hidden": len(chapters), "stopped": True}
    api = BrightspaceBookAPI(bs_page, course_id)
    await api.course_root()
    book_module = await api.ensure_book_module(str(parent_id), book_title)
    module_id = str(book_module["Id"])
    await api.ensure_files_folder(book_title)
    log(f"📘 {book_title}: {len(chapters)} Moodle chapters; Content folder {module_id}", "step")
    stored_names = await api.folder_file_names(book_title)

    all_assets = sorted(set().union(*(chapter_asset_urls(ch) for ch in chapters)))
    names = [asset_filename(url).casefold() for url in all_assets]
    duplicates = {name for name in names if names.count(name) > 1}
    asset_map: dict[str, str] = {}
    failed_assets: dict[str, str] = {}
    for index, url in enumerate(all_assets, 1):
        if should_stop():
            return {"created": 0, "hidden": len(chapters), "stopped": True}
        name = asset_filename(url, duplicates)
        try:
            if name in stored_names:
                asset_map[url] = (await api.course_root()) + quote(f"{book_title}/{name}", safe="/")
                log(f"  ↷ [{index}/{len(all_assets)}] Reusing {name}", "dim")
            else:
                blob, content_type, _ = await _moodle_asset(context, url)
                asset_map[url] = await api.upload_file(book_title, name, blob, content_type)
                stored_names.add(name)
                log(f"  ✓ [{index}/{len(all_assets)}] Stored {name}", "success")
        except Exception as exc:
            failed_assets[url] = str(exc).splitlines()[0]
            log(f"  ✗ [{index}/{len(all_assets)}] {name}: {failed_assets[url]}", "error")

    target_names = _unique_activity_targets(bs_flat)
    target_names = {key: value.format(course_id=course_id) for key, value in target_names.items()}
    source_activities: dict[str, str] = {}
    for item in moodle_items:
        name = str(item.get("name") or "").strip().casefold()
        href = str(item.get("href") or "")
        if href and name in target_names:
            source_activities[href] = target_names[name]

    existing = [item for item in bs_flat if item.get("kind") == "TOPIC"
                and item.get("title", "").casefold() == book_title.casefold()
                and item.get("module") != book_title]
    style_html = ""
    if len(existing) == 1 and existing[0].get("id"):
        from unit_overview import BrowserContentAPI
        try:
            style_html = await BrowserContentAPI(bs_page, course_id, module_id).get_topic_html(existing[0]["id"])
        except Exception:
            pass
    if style_html:
        old_links = [(re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).casefold(), tag.get("href"))
                     for tag in BeautifulSoup(style_html, "lxml").find_all("a", href=True)]
        for chapter in chapters:
            for tag in BeautifulSoup(chapter.body_html, "lxml").find_all("a", href=True):
                href = tag["href"]
                if urlparse(href).hostname != _MOODLE_HOST or href in source_activities:
                    continue
                label = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).casefold()
                candidates = {target for old_label, target in old_links
                              if label and old_label and (old_label.startswith(label) or label.startswith(old_label))
                              and urlparse(target or "").hostname == urlparse(api.base).hostname}
                if len(candidates) == 1:
                    source_activities[href] = next(iter(candidates))

    # The old importer copied Kaltura videos into course files. Its video order
    # follows Moodle's chapter order, so reuse them when the counts agree.
    video_map: dict[str, str] = {}
    if style_html:
        old = BeautifulSoup(style_html, "lxml")
        old_video_urls = [tag.get("src") for tag in old.select("video source[src]")]
        moodle_iframes = [tag.get("src") for chapter in chapters
                          for tag in BeautifulSoup(chapter.body_html, "lxml").select(
                              'iframe[src*="/filter/kaltura/lti_launch.php"]')]
        if old_video_urls and len(old_video_urls) == len(moodle_iframes):
            video_map = dict(zip(moodle_iframes, old_video_urls))
            log(f"  ↳ Reusing {len(video_map)} videos from the existing Brightspace page", "info")

    # Moodle resource links are real files even though the link itself ends in
    # view.php. Host the binary once in Manage Files and point the chapter at it.
    resource_urls = sorted({tag.get("href") for chapter in chapters
                            for tag in BeautifulSoup(chapter.body_html, "lxml").select(
                                'a[href*="/mod/resource/view.php"]') if tag.get("href")})
    for url in resource_urls:
        if should_stop():
            break
        try:
            blob, content_type, final_url = await _moodle_asset(context, url)
            filename = asset_filename(final_url)
            asset_map[url] = await api.upload_file(book_title, filename, blob, content_type)
            log(f"  ✓ Linked Moodle resource through course files: {filename}", "success")
        except Exception as exc:
            failed_assets[url] = str(exc).splitlines()[0]
            log(f"  ✗ Resource link: {failed_assets[url]}", "error")

    from unit_overview import BrowserContentAPI
    content_api = BrowserContentAPI(bs_page, course_id, module_id)
    created = reused = hidden = flagged = 0
    for chapter in chapters:
        if should_stop():
            break
        current = [item for item in await api.module_structure(module_id)
                   if item.get("Type") == 1 and item.get("Title", "").casefold() == chapter.title.casefold()]
        if len(current) > 1:
            log(f"  ✗ Ambiguous existing chapter: {chapter.title}", "error")
            hidden += 1
            continue
        rewritten, unresolved = rewrite_chapter_links(
            chapter, asset_map, target_names, {}, source_activities, video_map,
            flag_unmatched_lti=flag_unmatched_lti)
        flagged += rewritten.count('class="bpa-pending-activity"')
        unresolved = sorted(set(unresolved) | set(chapter_asset_urls(chapter)) & set(failed_assets))
        if unresolved:
            hidden += 1
            log(f"  ⚠ {chapter.title}: {len(unresolved)} Moodle link(s) need review; page will stay hidden", "warning")
            for url in unresolved:
                log(f"      ↳ {url[:180]}", "dim")
        rendered = render_chapter(book_title, chapter, rewritten, style_html)
        try:
            if current:
                from content_preservation import content_is_preserved
                saved = await content_api.get_topic_html(current[0]["Id"])
                preserved, _ = content_is_preserved(rendered, saved)
                if not preserved:
                    await api.update_generated_chapter(module_id, book_title, chapter,
                                                       rendered, current[0])
                reused += 1
                log(f"  ↷ Verified chapter page: {chapter.title}", "success")
            else:
                await api.create_chapter_topic(module_id, book_title, chapter.title,
                                               chapter.chapter_id, rendered)
                created += 1
                log(f"  ✓ Chapter page: {chapter.title}", "success")
        except Exception as exc:
            if not unresolved:
                hidden += 1
            log(f"  ✗ Chapter {chapter.title}: {str(exc).splitlines()[0]}", "error")
    if (not should_stop() and hidden == 0 and created + reused == len(chapters)):
        from unit_overview import BrowserContentAPI

        structure = await api.module_structure(module_id)
        actual = [str(item.get("Title") or "") for item in structure if item.get("Type") == 1]
        expected = [chapter.title for chapter in chapters]
        if actual != expected:
            raise RuntimeError(f"Book chapter order did not verify: {actual!r}")
        if bool((await api.module(module_id)).get("IsHidden", False)):
            parent_hidden = bool((await api.module(str(parent_id))).get("IsHidden", False))
            for item in structure:
                if item.get("Type") == 1:
                    await content_api.set_topic_hidden(item["Id"], parent_hidden)
            await api.set_module_hidden(module_id, parent_hidden)
            if existing and len(existing) == 1 and existing[0].get("id"):
                old_api = BrowserContentAPI(bs_page, course_id, str(parent_id))
                await old_api.set_topic_hidden(existing[0]["id"], True)
                log("  ✓ Original combined page kept as a hidden backup", "success")
            log(f"  ✓ Published {len(chapters)} chapter pages in Moodle order", "success")
    return {"created": created, "reused": reused, "hidden": hidden,
            "flagged": flagged, "assets": len(asset_map), "asset_failures": failed_assets,
            "module_id": module_id, "stopped": bool(should_stop())}
