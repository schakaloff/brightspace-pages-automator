"""Split one source page into phase pieces (before / during / after) at its own markers.

Pure and offline. Pieces keep the original HTML blocks verbatim; nothing is
rewritten. Pages without explicit markers are returned whole and unresolved, so a
person (or an AI suggestion the person approves) decides.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

BLOCKS = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "div", "blockquote", "figure", "iframe", "hr")
PHASES = {
    "before": re.compile(r"\b(before\b|pre[- ]?(work|class|workshop|reading|session)\b|prior to\b|prepar(e|ation)\b(?! for)|to prepare\b)", re.I),
    "during": re.compile(r"\b(during\b|zoom (workshop|session)|synchronous (workshop|session)|materials for\b|in[- ]class\b|live (workshop|session)|workshop materials\b|session materials\b|class materials\b)", re.I),
    "after": re.compile(r"\b(after\b|post[- ]?(work|class|workshop|session)\b|follow[- ]?up\b)", re.I),
}
LEAD_WORD_LIMIT = 200
TINY_LEAD_WORDS = 12  # a sub-title such as "Blended Part 1: Mental Health" is not a piece
MARKER_WORDS = re.compile(r"\b(instructions?|workshop|session|class|materials|complete|work)\b", re.I)
PART_HEADING = re.compile(r"^\s*part\s+(\d+|one|two|three|four|five|six)\b", re.I)


def _text(tag):
    return " ".join(tag.get_text(" ", strip=True).split())


def _heading_like(tag):
    """A block that reads as a label, not prose that merely mentions 'after'."""
    text = _text(tag)
    if not text or len(text) > 130: return False
    if re.fullmatch(r"h[1-6]", tag.name): return True
    if tag.name != "p": return False
    bold = " ".join(_text(b) for b in tag.find_all(["strong", "b"]))
    # Prose such as "After viewing the video, please review:" is not a label.
    return len(bold) >= 0.3 * len(text) or (text.endswith(":") and bool(MARKER_WORDS.search(text)))


def lead_label(tag):
    """The label a block starts with: the whole heading, or its opening bold run."""
    if re.fullmatch(r"h[1-6]", tag.name): return _text(tag)
    first = next((c for c in tag.children if not (isinstance(c, NavigableString) and not c.strip())), None)
    if isinstance(first, Tag) and first.name in ("strong", "b"): return _text(first)
    return _text(tag) if _heading_like(tag) else ""


def text_phase(text):
    """The phase a piece of text names, or None. Earliest mention wins."""
    found = [(m.start(), name) for name, pattern in PHASES.items() if (m := pattern.search(text or ""))]
    return min(found)[1] if found else None


def phase_of(tag):
    """'before' | 'during' | 'after' if this block is a phase marker, else None."""
    if not _heading_like(tag): return None
    text = _text(tag)
    found = [name for name, pattern in PHASES.items() if pattern.search(text)]
    if len(found) == 1: return found[0]
    if len(found) > 1:
        # "Instructions for after Synchronous Session" names two phases; the one
        # nearest the start is the instruction, the rest is context.
        return min(found, key=lambda name: PHASES[name].search(text).start())
    return None


CONTAINERS = ("div", "section", "header", "main", "article", "aside", "footer", "nav", "body", "center")
STRUCTURAL = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote", "figure", "pre", "hr", *CONTAINERS)


def _blocks(node):
    """Leaf blocks of `node` in reading order; layout wrappers are descended into."""
    for child in node.children:
        if isinstance(child, NavigableString):
            if child.strip() and not isinstance(child, type(None)) and child.__class__.__name__ == "NavigableString":
                yield child
            continue
        if not isinstance(child, Tag) or child.name in ("script", "style", "head", "title", "meta", "link"):
            continue
        if child.name in CONTAINERS and any(isinstance(c, Tag) and c.name in STRUCTURAL for c in child.children):
            yield from _blocks(child)
        else:
            yield child


def content_blocks(markup):
    """Every block of the page body, in order, so no content can be skipped."""
    soup = BeautifulSoup(markup or "", "html.parser")
    root = soup.body or soup
    title = soup.find("h1")
    title = _text(title) if title else ""
    # The page header (title, logo) belongs to the template, not the content.
    for header in root.find_all("header"): header.decompose()
    return soup, list(_blocks(root)), title


def _words(html):
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split()


def split_page(markup):
    """Return {'title', 'pieces': [{'phase', 'marker', 'html', 'words', 'confidence', 'reason'}]}."""
    soup, blocks, title = content_blocks(markup)
    pieces, current = [], {"phase": "lead", "marker": "", "blocks": []}
    for block in blocks:
        phase = phase_of(block) if isinstance(block, Tag) else None
        if not phase and isinstance(block, Tag) and current.get("part_scoped") and PART_HEADING.match(lead_label(block)):
            # "Part 1: … Instructions for BEFORE" covers Part 1 only; a later
            # unlabelled part has no stated phase.
            pieces.append(current)
            current = {"phase": "unknown", "marker": lead_label(block), "blocks": [block], "part_scoped": True}
            continue
        if phase:
            if current["phase"] == phase and current["blocks"] and all(phase_of(b) for b in current["blocks"] if isinstance(b, Tag)):
                current["blocks"].append(block)  # "Instructions for BEFORE:" then "Complete Before Workshop"
                continue
            if current["blocks"] or current["phase"] != "lead": pieces.append(current)
            current = {"phase": phase, "marker": _text(block), "blocks": [block],
                       "part_scoped": bool(PART_HEADING.match(_text(block)))}
        else:
            current["blocks"].append(block)
    if current["blocks"]: pieces.append(current)
    lead = pieces[0]["blocks"] if pieces and pieces[0]["phase"] == "lead" else []
    if len(pieces) > 1 and lead and all(isinstance(b, Tag) and _heading_like(b) for b in lead) and \
            len(" ".join(_text(b) for b in lead).split()) <= TINY_LEAD_WORDS:
        pieces[1]["blocks"] = pieces[0]["blocks"] + pieces[1]["blocks"]
        pieces = pieces[1:]
    marked = any(p["phase"] not in ("lead", "unknown") for p in pieces)
    result = []
    for piece in pieces:
        html = "".join(str(b) for b in piece["blocks"])
        words = len(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
        if piece["phase"] == "unknown":
            confidence, reason = "unresolved", f"“{piece['marker']}” has no before/during/after label"
        elif piece["phase"] != "lead":
            confidence, reason = "deterministic", f"Page marker: “{piece['marker']}”"
        elif not marked:
            confidence, reason = "unresolved", "No before/during/after markers on this page"
        elif words <= LEAD_WORD_LIMIT:
            confidence, reason = "inferred", "Opening text before the first marker; a draft for the Module Introduction"
        else:
            confidence, reason = "unresolved", "Long opening section before the first marker"
        result.append({"phase": piece["phase"], "marker": piece["marker"], "html": html, "words": words,
                       "confidence": confidence, "reason": reason})
    result = [p for p in result if p["words"]]
    # Nothing may be lost: the pieces must hold exactly the page's words.
    expected = _words(str(soup.body or soup))
    kept = [w for p in result for w in _words(p["html"])]
    if kept != expected:
        return {"title": title, "marked": False, "complete": False,
                "pieces": [{"phase": "lead", "marker": "", "html": markup, "words": len(expected), "confidence": "unresolved",
                            "reason": "The page layout could not be split without risk of losing content; kept whole"}]}
    return {"title": title, "pieces": result, "marked": marked, "complete": True}


def short_label(marker, phase):
    """A readable name for a piece, taken from its own marker."""
    text = re.sub(r"^(workshop\s+)?instructions?\s+for\s+", "", (marker or "").strip(), flags=re.I).strip(" :-–")
    if text: return text[:60]
    return {"lead": "Overview", "before": "Before", "during": "During", "after": "After"}.get(phase, "Part")


# Attributes that carry content rather than presentation.
KEEP_ATTRIBUTES = {
    "a": ("href", "title"), "img": ("src", "alt", "width", "height"),
    "iframe": ("src", "title", "width", "height", "allow", "allowfullscreen", "frameborder"),
    "video": ("src", "controls", "poster", "width", "height"), "audio": ("src", "controls"),
    "source": ("src", "type"), "track": ("src", "kind", "srclang", "label"),
    "td": ("colspan", "rowspan"), "th": ("colspan", "rowspan", "scope"), "ol": ("start", "type"), "li": ("value",),
}
UNWRAP = ("div", "span", "section", "article", "main", "center", "font", "header", "footer", "aside", "nav", "figure", "body", "html")
DROP = ("script", "style", "link", "meta", "head", "title", "noscript", "button", "form", "input", "svg")


def plain_fragment(markup, drop_header=True):
    """Content only: no CSS, classes, inline styles, fonts or layout wrappers."""
    soup = BeautifulSoup(markup or "", "html.parser")
    for comment in soup.find_all(string=lambda s: s.__class__.__name__ == "Comment"): comment.extract()
    if drop_header:
        for header in soup.find_all("header"): header.decompose()  # decorative title banner
    for tag in soup.find_all(DROP): tag.decompose()
    for tag in soup.find_all(True):
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in KEEP_ATTRIBUTES.get(tag.name, ())}
    for tag in soup.find_all(UNWRAP): tag.unwrap()
    for frame in soup.find_all("iframe"):
        if not frame.get("width"): frame["width"], frame["height"] = "560", "315"  # size used to come from CSS
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i"]):
        if not tag.get_text(strip=True) and not tag.find(["img", "iframe", "video", "audio", "br"]): tag.decompose()
    return str(soup).strip()


def plain_document(markup, title, drop_header=True):
    """A minimal, unstyled page for Brightspace."""
    import html as _html
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_html.escape(title)}</title></head>'
            f'<body>{plain_fragment(markup, drop_header)}</body></html>')
