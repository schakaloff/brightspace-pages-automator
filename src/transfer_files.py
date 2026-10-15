"""Template editing and recursive, self-contained course-file bundles."""
from __future__ import annotations

import hashlib
import html
import mimetypes
import posixpath
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from transfer_plan import norm

URL_ATTRIBUTES = ("href", "src", "poster", "data", "background", "action")
CSS_URL = re.compile(r'''url\(\s*(["']?)(.*?)\1\s*\)|@import\s+(["'])(.*?)\3''', re.I)


def reference_key(url):
    p = urlsplit(html.unescape(url))
    query = sorted((k.lower(), v.lower() if k.lower() in ("rcode", "type") else v) for k, v in parse_qsl(p.query))
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))


def remove_sections(soup, labels):
    """Remove a named section, bounded by the next heading of equal rank.

    Also supports the development shell's paragraph/strong explore label.
    Ambiguous nested containers are rejected instead of deleting broad content.
    """
    labels = {norm(label) for label in labels}
    for node in list(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p"])):
        if node.parent is None or norm(node.get_text(" ", strip=True)) not in labels:
            continue
        rank = int(node.name[1]) if node.name.startswith("h") else 7
        siblings = list(node.next_siblings)
        node.decompose()
        for sibling in siblings:
            if not getattr(sibling, "name", None):
                continue
            if sibling.name.startswith("h") and sibling.name[1:].isdigit() and int(sibling.name[1]) <= rank:
                break
            if rank == 7 and (sibling.name not in ("ul", "ol", "p") or
                             sibling.name == "p" and sibling.get_text(strip=True)):
                break
            sibling.decompose()


def render_template(markup, kind, workshop, recipe):
    soup = BeautifulSoup(markup, "html.parser")
    wrapper = soup.select_one(".courseware-layouts-content-wrapper")
    if wrapper is None:
        raise ValueError("Template lacks the development-shell content wrapper. Choose a compatible template.")
    hero = wrapper.select_one(".courseware-headers-hero")
    if hero is not None:
        hero.extract()
    wrapper.clear()
    if hero is not None:
        wrapper.append(hero)
        heading = hero.find("h1")
        if heading: heading.string = workshop["title"] if kind == "introduction" else "Session Overview"
    else:
        heading = soup.new_tag("h1")
        heading.string = workshop["title"] if kind == "introduction" else "Session Overview"
        wrapper.append(heading)
    if soup.title:
        soup.title.string = workshop["title"] if kind == "introduction" else "Session Overview"
    if kind == "introduction":
        # Retain the established learning-outcome card classes and nesting.
        original = BeautifulSoup(markup, "html.parser")
        card_title = original.find(class_="d2l-callout-title")
        card = card_title.find_parent(class_="d2l-element") if card_title else None
        h = soup.new_tag("h2"); h.string = "Introduction"; wrapper.append(h)
        p = soup.new_tag("p"); p.string = workshop.get("introduction") or "Introduction to be supplied."; wrapper.append(p)
        if card is None:
            raise ValueError("Introduction template lacks its Learning Outcomes callout.")
        card.extract()
        title = card.find(class_="d2l-callout-title")
        title.string = "Learning Outcomes"
        body = title.find_next_sibling("div")
        if body is None: raise ValueError("Learning Outcomes template slot is ambiguous.")
        body.clear()
        ul = soup.new_tag("ul")
        for outcome in workshop.get("outcomes") or ["Learning outcomes to be supplied."]:
            li = soup.new_tag("li"); li.string = str(outcome); ul.append(li)
        body.append(ul); wrapper.append(card)
        remove_sections(soup, recipe.get("remove_introduction_sections", []))
    else:
        h = soup.new_tag("h2"); h.string = "Session plan"; wrapper.append(h)
        from transfer_plan import session_section
        home = (session_section(recipe) or {}).get("title", "workshop")
        for paragraph in (workshop.get("session") or f"Session Overview to be supplied. Review the {home} resources below.").split("\n"):
            p = soup.new_tag("p"); p.string = paragraph; wrapper.append(p)
        remove_sections(soup, recipe.get("remove_session_sections", []))
    return str(soup)


MOODLE_PLACEHOLDER = re.compile(r"\$@[A-Z]+\*[^@]*@\$|%24@[A-Z]+\*[^@]*@%24", re.I)


def broken_moodle_links(markup):
    """Links the Moodle import never filled in ("$@URLVIEWBYID*123@$"); they lead nowhere."""
    soup = BeautifulSoup(markup or "", "html.parser")
    return [tag for tag in soup.find_all(True) for attr in URL_ATTRIBUTES
            if tag.get(attr) and MOODLE_PLACEHOLDER.search(str(tag[attr]))]


def unlink_moodle_placeholders(markup):
    """(markup, count): keep each broken link's text, drop the address that leads nowhere."""
    soup = BeautifulSoup(markup or "", "html.parser")
    count = 0
    for tag in list(soup.find_all(True)):
        for attr in URL_ATTRIBUTES:
            if tag.get(attr) and MOODLE_PLACEHOLDER.search(str(tag[attr])):
                count += 1
                if tag.name == "a": tag.unwrap(); break
                del tag[attr]
    return (str(soup) if count else markup), count


def placeholder(title, message):
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title></head><body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>'


def transform_references(text, base_url, convert, content_type="text/html"):
    def css(text):
        def replace(match):
            if match.group(2) is not None:
                return f'url("{convert(urljoin(base_url, match.group(2)))}")'
            return '@import "' + convert(urljoin(base_url, match.group(4))) + '"'
        return CSS_URL.sub(replace, text)
    if "css" in content_type:
        return css(text)
    soup = BeautifulSoup(text, "html.parser")
    document_base = soup.find("base", href=True)
    if document_base:
        raise ValueError("HTML <base> changes link resolution; remove/review it before transfer.")
    for tag in soup.find_all(True):
        for attr in URL_ATTRIBUTES:
            if tag.get(attr):
                value = str(tag[attr])
                if not value.startswith("#"):
                    tag[attr] = convert(urljoin(base_url, value))
        if tag.get("srcset"):
            if "data:" in tag["srcset"]:
                raise ValueError("Embedded data srcset needs manual review.")
            tag["srcset"] = ", ".join(
                " ".join([convert(urljoin(base_url, parts[0])), *parts[1:]])
                for entry in tag["srcset"].split(",") if (parts := entry.split()))
        if tag.get("style"):
            tag["style"] = css(tag["style"])
    for style in soup.find_all("style"):
        style.string = css(style.get_text())
    return str(soup)


class FileBundle:
    """Prepare every dependency locally BEFORE any target mutation.

    Names are deterministic within a saved plan. Cycles are supported through
    preallocated paths. All enforced-course URLs become target-owned URLs.
    """
    def __init__(self, download, host, target_root, folder, forbidden_course_ids):
        self.download = download
        self.host = host
        self.target_root = target_root
        self.folder = folder
        self.forbidden_course_ids = forbidden_course_ids
        self.files = {}
        self.paths = {}
        self.activity_map = {}
        # Files Brightspace copies itself (Copy Components) or that the destination already
        # has, identical: original URL -> destination path. Never downloaded.
        self.server = {}

    def activity_reference(self, ref):
        return self.activity_map.get(reference_key(ref), ref)

    def split_course_file(self, url):
        parsed = urlsplit(url)
        if parsed.hostname != self.host or not parsed.path.startswith("/content/enforced/"):
            return None
        path = unquote(parsed.path)
        prefix = "/".join(path.split("/")[:4]) + "/"
        canonical = posixpath.normpath(path)
        if not canonical.startswith(prefix) or "\\" in path or "\x00" in path:
            raise ValueError("Unsafe course-file path.")
        return urlunsplit((parsed.scheme, parsed.netloc, quote(canonical, safe="/"), "", ""))

    async def add(self, url, body=None, content_type=None):
        original = self.split_course_file(url)
        if not original:
            return url
        if original in self.server:
            self.paths[original] = self.server[original]
        if original in self.paths:
            parsed = urlsplit(url)
            return self.paths[original] + ("?" + parsed.query if parsed.query else "") + ("#" + parsed.fragment if parsed.fragment else "")
        path = urlsplit(original).path
        suffix = re.sub(r"[^.a-zA-Z0-9]", "", path.rsplit("/", 1)[-1].rsplit(".", 1)[-1])[:12]
        filename = hashlib.sha256(original.encode()).hexdigest()[:24] + ("." + suffix if suffix else ".bin")
        self.paths[original] = self.target_root + self.folder + "/" + filename
        if body is None:
            body, content_type = await self.download(original)
        original_hash = hashlib.sha256(body).hexdigest()
        content_type = (content_type or mimetypes.guess_type(path)[0] or "application/octet-stream").split(";")[0]
        textual = content_type in ("text/html", "application/xhtml+xml", "text/css") or suffix in ("html", "htm", "css")
        if suffix in ("js", "mjs"):
            raise ValueError("Course-local scripts need manual review; relative module imports cannot be safely rewritten.")
        if textual:
            text = body.decode("utf-8-sig")
            found = set()
            def discover(ref):
                if self.split_course_file(ref): found.add(ref)
                return ref
            transform_references(text, original, discover, content_type)
            for dependency in sorted(found):
                await self.add(dependency)
            def convert(ref):
                root = self.split_course_file(ref)
                if root:
                    p = urlsplit(ref)
                    return self.paths[root] + ("?" + p.query if p.query else "") + ("#" + p.fragment if p.fragment else "")
                return self.activity_reference(ref)
            text = transform_references(text, original, convert, content_type)
            self.check(text)
            body = text.encode("utf-8")
        self.files[original] = {"filename": filename, "body": body, "type": content_type,
                                "hash": hashlib.sha256(body).hexdigest(), "source_hash": original_hash}
        return await self.add(url)

    async def page(self, markup, original_url, *, label=None):
        try:
            return await self._page(markup, original_url)
        except (ValueError, RuntimeError) as exc:
            raise ValueError(f"{label or original_url}: {exc}") from None

    async def _page(self, markup, original_url):
        found = set()
        def discover(ref):
            if self.split_course_file(ref): found.add(ref)
            return ref
        transform_references(markup, original_url, discover)
        for url in sorted(found):
            await self.add(url)
        def convert(ref):
            original = self.split_course_file(ref)
            if original:
                p = urlsplit(ref)
                return self.paths[original] + ("?" + p.query if p.query else "") + ("#" + p.fragment if p.fragment else "")
            return self.activity_reference(ref)
        result = transform_references(markup, original_url, convert)
        self.check(result)
        return result

    def check(self, text):
        decoded = unquote(html.unescape(text))
        for match in re.findall(r"/content/enforced/[^\s\"'<>)]*", decoded, re.I):
            if not match.startswith(self.target_root):
                raise ValueError(f"Unrewritten source-course file reference: {match[:120]}")
        for course in self.forbidden_course_ids:
            if re.search(rf"(?:/d2l/[^\s\"'<>]*[/=]|[?&]ou=){re.escape(str(course))}(?:[/&#?\"'<>]|$)", decoded):
                raise ValueError("Cross-course activity/style link needs target mapping before transfer.")
