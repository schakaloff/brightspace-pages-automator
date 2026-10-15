"""Styling choices for transferred pages: keep, strip, and re-theme without losing content.

Three cleanup modes, each optionally followed by a destination template:

* ``preserve``  - the page is copied as it is (links are still rewritten later).
* ``strip_bpa`` - removes what Page Automator added: its style block, font link,
  layout wrappers, classes, icons and hero label. The author's own formatting stays.
* ``strip_all`` - also removes every other stylesheet, inline style, class, font
  tag, presentational attribute, styling-framework script and layout-only wrapper.

Headings, paragraphs, lists, tables, links, images, embeds, math, accessibility
attributes and data attributes are never removed. Every cleaned page is compared
with the original; if any text, link, image, embed, list or table differs, the
original page is used instead and the result says why.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from content_preservation import _compact_text, _snapshot

MODES = {"preserve": "Preserve existing styling",
         "strip_bpa": "Strip Page Automator styling",
         "strip_all": "Strip all custom styling"}

# Class names Page Automator's restyler has produced, across its versions.
PA_CLASSES = frozenset({
    "main-container", "hero", "hero-decoration", "content-body", "action-card", "link-btn", "video-btn",
    "video-wrapper", "section-header", "side-image", "info-list", "info-row", "info-label", "info-value",
    "schedule-panel", "resource-section", "link-list", "link-row", "link-title", "link-meta", "resource-row",
    "resource-link", "resource-meta", "action-link"})
PA_MARKERS = ("main-container", "content-body")
PA_SELECTOR = re.compile(r"\.(?:" + "|".join(sorted(map(re.escape, PA_CLASSES), key=len, reverse=True)) + r"|bpa-[\w-]+)(?![\w-])")
PA_FONTS = re.compile(r"fonts\.(?:googleapis|gstatic)\.com", re.I)
# Script bundles that only drive a styling framework (accordions, tabs, banners).
FRAMEWORK_SCRIPTS = re.compile(r"Courseware_HTML_Templates|HTML-Template-Library|templates\.lcs\.brightspace\.com", re.I)
SCREEN_READER_CLASSES = frozenset({"sr-only", "visually-hidden", "screen-reader-text", "d2l-offscreen"})
SCREEN_READER_STYLE = (".sr-only,.visually-hidden,.screen-reader-text,.d2l-offscreen{position:absolute;width:1px;"
                       "height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}")

# Attributes that carry content, meaning or accessibility; everything else is presentation.
KEEP_ATTRIBUTES = frozenset({
    "id", "title", "lang", "dir", "role", "hidden", "tabindex", "alt", "href", "src", "srcset", "sizes", "target", "rel",
    "name", "download", "colspan", "rowspan", "scope", "headers", "abbr", "start", "reversed", "type", "value",
    "datetime", "cite", "label", "placeholder", "summary", "loading", "decoding", "for", "open", "controls",
    "poster", "kind", "srclang", "default", "media", "usemap", "coords", "shape", "longdesc"})
SIZED = frozenset({"img", "iframe", "video", "audio", "embed", "object", "canvas", "td", "th", "col", "table"})
KEEP_SIZE = frozenset({"img", "iframe", "video", "embed", "object", "canvas"})
# Embeds and math keep every attribute except class and style.
ATOMIC = frozenset({"iframe", "video", "audio", "object", "embed", "param", "source", "track", "canvas", "math", "svg", "picture"})
BLOCKS = frozenset({"address", "article", "aside", "blockquote", "details", "dialog", "dd", "div", "dl", "dt",
                    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
                    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "ul", "iframe",
                    "video", "audio", "center", "summary"})
LABELS = {
    "pa-style": "Page Automator style block", "pa-font": "Page Automator font link",
    "pa-wrapper": "Page Automator layout wrapper", "pa-class": "Page Automator class",
    "pa-icon": "Page Automator icon", "pa-label": "Page Automator banner label",
    "style": "style block", "stylesheet": "stylesheet link", "script": "styling-framework script",
    "inline": "inline style", "class": "class", "attribute": "presentational attribute",
    "font": "font tag", "wrapper": "layout-only wrapper", "comment": "comment",
}


@dataclass
class StyleResult:
    html: str
    mode: str
    removed: Counter = field(default_factory=Counter)
    ok: bool = True
    reason: str = ""
    templated: bool = False

    @property
    def summary(self):
        if not self.ok: return f"Kept the original page: {self.reason}."
        text = describe(self.removed) if self.removed else "Nothing to remove"
        return text + ("; placed in the destination template" if self.templated else "")


def describe(removed):
    """'1 style block, 37 inline styles, 12 layout-only wrappers'."""
    parts = []
    for key, label in LABELS.items():
        count = removed.get(key, 0)
        if count: parts.append(f"{count} {label}{'' if count == 1 else 'es' if label.endswith(('ss', 'h')) else 's'}")
    return ", ".join(parts)


def _classes(tag):
    value = tag.get("class") or []
    return value.split() if isinstance(value, str) else list(value)


def is_page_automator(markup_or_soup):
    soup = markup_or_soup if isinstance(markup_or_soup, (BeautifulSoup, Tag)) else BeautifulSoup(markup_or_soup or "", "html.parser")
    for tag in soup.find_all(True):
        classes = _classes(tag)
        if any(c in PA_MARKERS or c.startswith("bpa-") for c in classes): return True
        if any(a.startswith("data-bpa-") for a in tag.attrs): return True
    return False


def has_custom_styling(markup):
    soup = BeautifulSoup(markup or "", "html.parser")
    return bool(soup.find(["style", "font"]) or soup.find("link", rel=lambda r: r and "stylesheet" in r)
                or soup.find(attrs={"style": True}) or soup.find(attrs={"class": True}))


def _decorations(soup):
    """Elements Page Automator generated as decoration, not authored content."""
    found = [("pa-icon", t) for t in soup.find_all("span", attrs={"data-bpa-icon": True})]
    if is_page_automator(soup):
        found += [("pa-label", t) for t in soup.find_all(class_="hero-decoration")]
    return found


def _has_text(tag):
    return any(isinstance(c, NavigableString) and not isinstance(c, Comment) and c.strip() for c in tag.children)


def _flatten(tag, removed, key):
    """Remove a wrapper without merging blocks or losing what's inside."""
    if tag.name in ("span", "font"):
        tag.unwrap(); removed[key] += 1; return
    blocks = [c for c in tag.children if isinstance(c, Tag) and c.name in BLOCKS]
    if not _has_text(tag) and not any(isinstance(c, Tag) and c.name not in BLOCKS for c in tag.children):
        tag.unwrap(); removed[key] += 1
    elif not blocks:
        tag.name = "p"; tag.attrs = {k: v for k, v in tag.attrs.items() if k in KEEP_ATTRIBUTES or k.startswith(("aria-", "data-"))}
        removed[key] += 1
    # A wrapper holding both text and blocks stays as a plain container.


def strip_page_automator(soup, removed):
    for key, tag in _decorations(soup):
        tag.decompose(); removed[key] += 1
    if not is_page_automator(soup): return
    for style in soup.find_all("style"):
        if PA_SELECTOR.search(style.get_text()):
            style.decompose(); removed["pa-style"] += 1
    for link in soup.find_all("link"):
        if PA_FONTS.search(link.get("href", "")):
            link.decompose(); removed["pa-font"] += 1
    for tag in list(soup.find_all(True)):
        if tag.parent is None and tag is not soup: continue
        classes = _classes(tag)
        ours = [c for c in classes if c in PA_CLASSES or c.startswith("bpa-")]
        for attr in [a for a in tag.attrs if a.startswith("data-bpa-")]: del tag[attr]
        if not ours: continue
        rest = [c for c in classes if c not in ours]
        if rest: tag["class"] = rest
        else: del tag["class"]
        if tag.get("style"): del tag["style"]
        if tag.name in ("div", "span", "header", "main", "section") and not rest and not any(
                a in tag.attrs for a in ("id", "role", "lang", "dir")) and not any(a.startswith("aria-") for a in tag.attrs):
            _flatten(tag, removed, "pa-wrapper")
        else:
            removed["pa-class"] += len(ours)


def _in_atomic(tag):
    return tag.name in ATOMIC or any(p.name in ("math", "svg") for p in tag.parents)


def _size_from_style(style, prop):
    match = re.search(rf"(?:^|;)\s*{prop}\s*:\s*(\d+(?:\.\d+)?)px", style or "", re.I)
    return str(round(float(match.group(1)))) if match else None


def strip_all(soup, removed):
    strip_page_automator(soup, removed)
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract(); removed["comment"] += 1
    for style in soup.find_all("style"):
        style.decompose(); removed["style"] += 1
    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel") or []).lower()
        if "stylesheet" in rel or "preconnect" in rel and PA_FONTS.search(link.get("href", "")) or link.get("as") in ("style", "font"):
            link.decompose(); removed["stylesheet"] += 1
    for script in soup.find_all("script"):
        if FRAMEWORK_SCRIPTS.search(script.get("src", "")):
            script.decompose(); removed["script"] += 1
    hidden_text = False
    for tag in list(soup.find_all(True)):
        if tag.parent is None: continue
        if tag.name in ("html", "head", "meta", "title", "script", "base"): continue
        style = tag.get("style", "")
        if _in_atomic(tag):
            for attr in ("class", "style"):
                if attr in tag.attrs:
                    del tag[attr]; removed["inline" if attr == "style" else "class"] += 1
            continue
        keep_classes = [c for c in _classes(tag) if c in SCREEN_READER_CLASSES]
        for attr in list(tag.attrs):
            if attr in KEEP_ATTRIBUTES or attr.startswith(("aria-", "data-")): continue
            if attr in ("width", "height") and tag.name in KEEP_SIZE: continue
            if attr == "border" and tag.name == "table": continue
            if attr == "class" and keep_classes:
                if len(keep_classes) < len(_classes(tag)): removed["class"] += 1
                tag["class"] = keep_classes; continue
            del tag[attr]
            removed["inline" if attr == "style" else "class" if attr == "class" else "attribute"] += 1
        hidden_text = hidden_text or bool(keep_classes)
        if tag.name == "img" and style:
            for prop in ("width", "height"):
                size = _size_from_style(style, prop)
                if size and not tag.get(prop): tag[prop] = size
    for frame in soup.find_all(["iframe", "video"]):
        if not frame.get("width") and not frame.get("height"): frame["width"], frame["height"] = "560", "315"
    for tag in list(soup.find_all(["font", "span", "div", "center"])):
        if tag.parent is None or tag.attrs: continue
        _flatten(tag, removed, "font" if tag.name == "font" else "wrapper")
    if hidden_text:
        style = soup.new_tag("style"); style.string = SCREEN_READER_STYLE
        (soup.head or soup.body or soup).insert(0, style)


def _content_matches(expected, actual):
    """Same words in the same order, and identical links, media, embeds, lists and tables."""
    a, b = _snapshot(expected), _snapshot(actual)
    if _compact_text(a.visible_text) != _compact_text(b.visible_text): return False, "some text would change"
    if a.resources != b.resources: return False, "a link, image, file or embed would change"
    if a.atomic_text != b.atomic_text: return False, "an embedded item would change"
    if a.list_table_structure != b.list_table_structure: return False, "a list or table would change"
    if a.generated_css_content and a.generated_css_content != b.generated_css_content:
        return False, "the stylesheet adds visible text that would be lost"
    return True, ""


def clean(markup, mode):
    """Clean one page. Falls back to the original whenever content would change."""
    if mode not in MODES: raise ValueError("Unknown styling option.")
    result = StyleResult(markup or "", mode)
    if mode == "preserve" or not markup: return result
    soup = BeautifulSoup(markup, "html.parser")
    removed = Counter()
    (strip_page_automator if mode == "strip_bpa" else strip_all)(soup, removed)
    cleaned = str(soup)
    expected, actual = BeautifulSoup(markup, "html.parser"), BeautifulSoup(cleaned, "html.parser")
    for _, tag in _decorations(expected): tag.decompose()
    for side in (expected, actual):  # comments are never shown, so they aren't content
        for comment in side.find_all(string=lambda s: isinstance(s, Comment)): comment.extract()
    ok, reason = _content_matches(str(expected), str(actual))
    if not ok:
        result.ok, result.reason = False, reason
        return result
    result.html, result.removed = cleaned, removed
    return result


# ── Destination templates ───────────────────────────────────────────────

def _container(soup):
    """(content container, title heading) of a template page."""
    for selector, hero_selector in ((".courseware-layouts-content-wrapper", ".courseware-headers-hero"),
                                    (".content-body", ".hero"), ("main", "header")):
        box = soup.select_one(selector)
        if box is not None:
            hero = soup.select_one(hero_selector)
            return box, hero.find(["h1", "h2"]) if hero else None
    return soup.body or soup, None


def apply_template(markup, template_html, title):
    """Put a page's content inside a template page's head, banner and content area."""
    page = BeautifulSoup(markup or "", "html.parser")
    soup = BeautifulSoup(template_html, "html.parser")
    box, heading = _container(soup)
    keep = None
    if heading is not None and box in heading.parents:  # a banner inside the content area stays
        keep = heading
        while keep.parent is not box: keep = keep.parent
    for child in list(box.children):
        if child is not keep: child.extract()
    if heading is not None:
        heading.clear(); heading.append(title)
    body = page.body or page
    nodes = [c for c in body.children if not (isinstance(c, Tag) and c.name in ("head", "title", "meta", "link", "style"))]
    first = next((c for c in nodes if isinstance(c, Tag) or str(c).strip()), None)
    if heading is not None and isinstance(first, Tag) and first.name in ("h1", "h2") and \
            " ".join(first.get_text(" ").split()).casefold() == " ".join(title.split()).casefold():
        nodes.remove(first)  # the template banner already shows the page title
    for node in nodes: box.append(node.extract() if hasattr(node, "extract") else node)
    if page.head and soup.head:
        for tag in page.head.find_all(["script", "style"]): soup.head.append(tag.extract())
    if soup.title: soup.title.string = title
    return str(soup)


def style_page(markup, mode, title="", template_html=None):
    """Clean a page, then (if a template is given and cleanup succeeded) re-theme it."""
    result = clean(markup, mode)
    if template_html and result.ok:
        result.html = apply_template(result.html, template_html, title)
        result.templated = True
    return result


def piece_document(page_html, piece_html, title):
    """A whole document for one part of a split page, keeping the page's head."""
    soup = BeautifulSoup(page_html or "", "html.parser")
    body = soup.body
    if body is None:
        return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head><body>{piece_html}</body></html>'
    body.clear()
    body.append(BeautifulSoup(piece_html, "html.parser"))
    if soup.title: soup.title.string = title
    return str(soup)


# ── Built-in themes ─────────────────────────────────────────────────────

def builtin_themes():
    """Page Automator's own themes, usable as a destination style."""
    from style_presets import STYLE_PRESETS, load_style_reference
    themes = []
    for key, label in STYLE_PRESETS.items():
        try:
            markup = load_style_reference(key)
        except OSError:
            continue
        soup = BeautifulSoup(markup, "html.parser")
        box, _ = _container(soup)
        for child in list(box.children): child.extract()
        for sample in soup.find_all(class_="hero-decoration"): sample.decompose()
        themes.append({"id": "theme-" + key, "label": "Page Automator: " + label.split(" (")[0], "html": str(soup), "builtin": True})
    return themes


def template_choices(target, source=None):
    """Pages that can lend their look: destination pages with a stylesheet, then built-in themes."""
    from transfer_plan import all_objects
    found = []
    for course, side in ((target, "Destination"), (source, "Source")):
        if not course: continue
        for obj in all_objects(course.get("modules", [])):
            markup = obj.get("html")
            if obj.get("Type") != 1 or not markup: continue
            soup = BeautifulSoup(markup, "html.parser")
            if not (soup.find("style") or soup.find("link", rel=lambda r: r and "stylesheet" in r)): continue
            found.append({"id": str(obj["Id"]), "course": course["id"], "url": obj.get("Url"), "html": markup,
                          "label": f"{obj['Title']} ({side})"})
    return found + builtin_themes()
