"""Compare course content before and after it is written or restyled.

`content_is_equivalent` is strict and is used for read-backs of HTML the app
wrote itself. `content_is_preserved` is lenient and is used for AI-styled
output, which may add headings or reorder layout but must keep every authored
passage and every link, image, file, and embed.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Tag


_PRESENTATION_ATTRIBUTES = {"class", "style"}
_URL_ATTRIBUTES = {
    "action", "cite", "data", "formaction", "href", "longdesc", "poster",
    "src", "srcset", "xlink:href",
}
_CONTENT_ATTRIBUTES = _URL_ATTRIBUTES | {
    "alt", "aria-description", "aria-label", "aria-valuetext", "download",
    "label", "placeholder", "title", "value",
}
_CONTENT_ATTR_HINTS = (
    "content", "entry", "file", "h5p", "href", "kaltura", "media",
    "resource", "src", "url", "video",
)
_ATOMIC_TAGS = {
    "audio", "canvas", "embed", "iframe", "math", "object", "svg", "video",
}
_ATOMIC_MARKERS = ("h5p", "kaltura")
_NON_VISIBLE_PARENTS = {"head", "noscript", "script", "style", "template"}


def _normalise_text(value: str, *, casefold: bool = False, unicode_form: str = "NFC") -> str:
    value = html.unescape(value or "")
    value = unicodedata.normalize(unicode_form, value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.casefold() if casefold else value


def _normalise_heading(value: str) -> tuple[str, bool]:
    value = _normalise_text(value, casefold=True, unicode_form="NFKC")
    had_ellipsis = bool(re.search(r"(?:\.{3,}|\u2026)\s*$", value))
    value = re.sub(r"(?:\.{3,}|\u2026)\s*$", "", value).rstrip()
    return value, had_ellipsis


def first_meaningful_text_block(source_html: str) -> str:
    """Return the first authored block without treating a wrapper as the block."""
    soup = BeautifulSoup(source_html or "", "lxml")
    root = soup.body or soup
    for tag in root.find_all(
        ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td", "th"]
    ):
        text = _normalise_text(tag.get_text(" ", strip=True))
        if text:
            return text

    # Some imported pages contain only nested spans/divs. Prefer the deepest
    # meaningful div so a whole page wrapper is never mistaken for one block.
    divs = root.find_all("div")
    for tag in reversed(divs):
        text = _normalise_text(tag.get_text(" ", strip=True))
        if text:
            return text
    return _normalise_text(root.get_text(" ", strip=True))


def generated_title_matches_leading_content(title: str, source_html: str) -> bool:
    """Conservatively detect a generated title duplicated by the first block."""
    title_text, title_was_truncated = _normalise_heading(title)
    first_text, _ = _normalise_heading(first_meaningful_text_block(source_html))
    if not title_text or not first_text:
        return False
    if title_text == first_text:
        return True
    if not first_text.startswith(title_text):
        return False

    # Prefix matching is intentionally limited to substantial titles. A short
    # title such as "Files" must not hide a real section heading by accident.
    if len(title_text) < 24 or len(title_text.split()) < 4:
        return False
    if title_was_truncated:
        return True
    if len(first_text) == len(title_text):
        return True
    return not first_text[len(title_text)].isalnum()


def add_generated_heading(title: str, source_html: str) -> str:
    """Prepend a safe heading unless it duplicates leading authored content."""
    if not title or generated_title_matches_leading_content(title, source_html):
        return source_html
    return f"<h2>{html.escape(title)}</h2>\n{source_html}"


def _attribute_text(value) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def _has_atomic_marker(tag: Tag) -> bool:
    own_values = " ".join(
        [tag.name or ""] + [f"{key}={_attribute_text(value)}" for key, value in tag.attrs.items()]
    ).casefold()
    if any(marker in own_values for marker in _ATOMIC_MARKERS):
        return True
    if tag.name == "script":
        return any(marker in tag.get_text().casefold() for marker in _ATOMIC_MARKERS)
    return False


def _is_atomic(tag: Tag) -> bool:
    return tag.name in _ATOMIC_TAGS or _has_atomic_marker(tag)


@dataclass(frozen=True)
class _ContentSnapshot:
    visible_text: str
    visible_parts: tuple[str, ...]
    resources: tuple[tuple[str, str, str], ...]
    atomic_text: tuple[tuple[str, str], ...]
    generated_css_content: tuple[str, ...]
    list_table_structure: tuple[str, ...]


def _is_content_attribute(tag: Tag, attribute: str, value: str) -> bool:
    name = attribute.casefold()
    if name in _CONTENT_ATTRIBUTES:
        return True
    if tag.name in _ATOMIC_TAGS and name not in _PRESENTATION_ATTRIBUTES | {"height", "width"}:
        return True
    if name == "id" and any(marker in value.casefold() for marker in _ATOMIC_MARKERS):
        return True
    return (name.startswith("data-") or name.startswith("data_")) and any(
        hint in name for hint in _CONTENT_ATTR_HINTS
    )


def _snapshot(source_html: str) -> _ContentSnapshot:
    soup = BeautifulSoup(source_html or "", "lxml")
    root = soup.body or soup
    visible_parts = []
    for text_node in root.find_all(string=True):
        parent_name = text_node.parent.name.casefold() if text_node.parent and text_node.parent.name else ""
        if parent_name not in _NON_VISIBLE_PARENTS:
            text = _normalise_text(str(text_node))
            if text:
                visible_parts.append(text)

    resources = []
    atomic_text = []
    css_sources = [tag.get_text() for tag in soup.find_all("style")]
    css_sources.extend(
        _attribute_text(tag.attrs["style"])
        for tag in root.find_all(True)
        if "style" in tag.attrs
    )
    generated_css_content = []
    for css in css_sources:
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        for match in re.finditer(r"(?:^|[;{])\s*content\s*:\s*([^;}]+)", css, re.IGNORECASE):
            value = _normalise_text(match.group(1))
            if value.casefold() not in {"", "''", '""', "none", "normal"}:
                generated_css_content.append(value)
    for tag in root.find_all(True):
        if tag.name in {"script", "style"} and not _is_atomic(tag):
            continue
        for attribute in sorted(tag.attrs):
            value = _attribute_text(tag.attrs[attribute])
            if _is_content_attribute(tag, attribute, value):
                resources.append((tag.name.casefold(), attribute.casefold(), value))
        if _is_atomic(tag):
            non_visible = []
            for text_node in tag.find_all(string=True):
                parent_name = text_node.parent.name.casefold() if text_node.parent and text_node.parent.name else ""
                if parent_name in {"script", "style"}:
                    value = _normalise_text(str(text_node))
                    if value:
                        non_visible.append(value)
            if non_visible:
                atomic_text.append((tag.name.casefold(), " ".join(non_visible)))
    return _ContentSnapshot(
        " ".join(visible_parts),
        tuple(visible_parts),
        tuple(resources),
        tuple(atomic_text),
        tuple(generated_css_content),
        tuple(
            tag.name.casefold()
            for tag in root.find_all(
                ["ol", "ul", "li", "table", "caption", "colgroup", "thead", "tbody", "tfoot", "tr", "th", "td"]
            )
        ),
    )


def _normalise_url_parts(value: str):
    value = unicodedata.normalize("NFC", html.unescape(value or "")).strip()
    parsed = urlsplit(value)
    return parsed, unquote(parsed.path), parsed.query, unquote(parsed.fragment)


def _urls_equivalent(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    ep, epath, equery, efragment = _normalise_url_parts(expected)
    ap, apath, aquery, afragment = _normalise_url_parts(actual)
    if (equery, efragment) != (aquery, afragment):
        return False
    if ep.scheme and ap.scheme:
        return (
            ep.scheme.casefold(), ep.netloc.casefold(), epath
        ) == (
            ap.scheme.casefold(), ap.netloc.casefold(), apath
        )
    if not ep.scheme and ap.scheme:
        return epath.startswith("/") and apath == epath
    if ep.scheme and not ap.scheme:
        return apath.startswith("/") and epath == apath
    return epath == apath


def _srcsets_equivalent(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if "data:" in expected.casefold() or "data:" in actual.casefold():
        return False

    def split(value: str) -> list[tuple[str, str]]:
        result = []
        for item in value.split(","):
            bits = item.strip().split(None, 1)
            if bits:
                result.append((bits[0], bits[1] if len(bits) > 1 else ""))
        return result

    expected_items = split(expected)
    actual_items = split(actual)
    return len(expected_items) == len(actual_items) and all(
        descriptor_a == descriptor_b and _urls_equivalent(url_a, url_b)
        for (url_a, descriptor_a), (url_b, descriptor_b) in zip(expected_items, actual_items)
    )


def content_is_equivalent(expected_html: str, actual_html: str) -> tuple[bool, str]:
    """Compare content semantics while ignoring presentation and harmless HTML changes."""
    expected = _snapshot(expected_html)
    actual = _snapshot(actual_html)
    if expected.visible_text != actual.visible_text:
        return False, "visible text changed, was duplicated, removed, or reordered"
    if len(expected.resources) != len(actual.resources):
        return False, "a link, image, file, or embedded resource was added or removed"
    for index, (left, right) in enumerate(zip(expected.resources, actual.resources), start=1):
        left_tag, left_attr, left_value = left
        right_tag, right_attr, right_value = right
        if (left_tag, left_attr) != (right_tag, right_attr):
            return False, f"resource {index} changed type or order"
        if left_attr == "srcset":
            same = _srcsets_equivalent(left_value, right_value)
        elif left_attr in _URL_ATTRIBUTES:
            same = _urls_equivalent(left_value, right_value)
        else:
            same = _normalise_text(left_value) == _normalise_text(right_value)
        if not same:
            return False, f"protected {left_attr} value changed at resource {index}"
    if expected.atomic_text != actual.atomic_text:
        return False, "a Kaltura, H5P, or complex embed payload changed"
    if expected.generated_css_content != actual.generated_css_content:
        return False, "CSS-generated visible content changed"
    if expected.list_table_structure != actual.list_table_structure:
        return False, "list or table structure changed"
    return True, ""


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def content_is_preserved(original_html: str, styled_html: str) -> tuple[bool, str]:
    """Lenient check for AI-styled output.

    Every authored text passage and every link, image, file, or embed URL from
    the original must still be present. Added headings or labels, reordering,
    and layout changes are allowed.
    """
    original = _snapshot(original_html)
    styled = _snapshot(styled_html)

    styled_text = _compact_text(styled.visible_text)
    for part in original.visible_parts:
        if _compact_text(part) not in styled_text:
            preview = part if len(part) <= 60 else part[:57] + "..."
            return False, f"text is missing: {preview!r}"

    styled_urls = [
        value for _tag, attribute, value in styled.resources
        if attribute in _URL_ATTRIBUTES - {"srcset"}
    ]
    for _tag, attribute, value in original.resources:
        if attribute not in _URL_ATTRIBUTES - {"srcset"}:
            continue
        match = next((i for i, url in enumerate(styled_urls) if _urls_equivalent(value, url)), None)
        if match is None:
            return False, f"link, image, file, or embed is missing: {value}"
        del styled_urls[match]
    return True, ""
