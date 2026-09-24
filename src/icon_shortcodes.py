"""Convert Moodle Font Awesome shortcodes into portable, accessible cues."""

from __future__ import annotations

import re


_SHORTCODE_RE = re.compile(r"\[(fa-[a-z0-9-]+)\]", re.IGNORECASE)

# Use restrained Unicode where it exists and familiar emoji only where they
# communicate the resource type more clearly. These render in Brightspace
# without loading Font Awesome or another external asset.
_ICONS: dict[str, tuple[str, str]] = {
    "fa-hand-point-right": ("→", "Next"),
    "fa-hand-o-right": ("→", "Next"),
    "fa-arrow-right": ("→", "Next"),
    "fa-arrow-left": ("←", "Previous"),
    "fa-chevron-right": ("›", "Next"),
    "fa-chevron-left": ("‹", "Previous"),
    "fa-check": ("✓", "Complete"),
    "fa-check-circle": ("✓", "Complete"),
    "fa-times": ("×", "Not available"),
    "fa-times-circle": ("×", "Not available"),
    "fa-info-circle": ("ℹ", "Information"),
    "fa-warning": ("⚠", "Warning"),
    "fa-exclamation-triangle": ("⚠", "Warning"),
    "fa-question-circle": ("?", "Question"),
    "fa-book": ("📘", "Reading"),
    "fa-graduation-cap": ("🎓", "Learning"),
    "fa-file": ("📄", "File"),
    "fa-file-text": ("📄", "Document"),
    "fa-folder": ("📁", "Folder"),
    "fa-link": ("🔗", "Link"),
    "fa-download": ("↓", "Download"),
    "fa-search": ("⌕", "Search"),
    "fa-user": ("●", "Person"),
    "fa-users": ("●●", "Group"),
    "fa-calendar": ("▣", "Date"),
    "fa-clock": ("◷", "Time"),
    "fa-clock-o": ("◷", "Time"),
    "fa-bell": ("🔔", "Notice"),
    "fa-comment": ("💬", "Discussion"),
    "fa-envelope": ("✉", "Message"),
    "fa-home": ("⌂", "Home"),
    "fa-cog": ("⚙", "Settings"),
    "fa-wrench": ("⚙", "Tools"),
    "fa-trophy": ("★", "Achievement"),
    "fa-star": ("★", "Important"),
    "fa-lightbulb-o": ("💡", "Tip"),
    "fa-lightbulb": ("💡", "Tip"),
    "fa-lock": ("🔒", "Restricted"),
    "fa-globe": ("◎", "Website"),
    "fa-video": ("▶", "Video"),
    "fa-play": ("▶", "Play"),
    "fa-forward": ("≫", "Continue"),
    "fa-image": ("▧", "Image"),
    "fa-bar-chart": ("▥", "Chart"),
    "fa-list": ("☷", "List"),
    "fa-plus": ("+", "More"),
    "fa-thumbs-up": ("✓", "Recommended"),
    "fa-pencil": ("✎", "Activity"),
    "fa-edit": ("✎", "Activity"),
    "fa-code": ("</>", "Code"),
    "fa-flask": ("⚗", "Experiment"),
    "fa-rocket": ("↗", "Launch"),
}


def replace_fontawesome_shortcodes(source_html: str) -> str:
    """Replace recognized shortcodes in text nodes, leaving code samples alone."""
    if not source_html or not _SHORTCODE_RE.search(source_html):
        return source_html

    from bs4 import BeautifulSoup, NavigableString

    soup = BeautifulSoup(source_html, "html.parser")
    for node in list(soup.find_all(string=_SHORTCODE_RE)):
        if node.parent and node.parent.name in {"code", "pre", "script", "style"}:
            continue
        text = str(node)
        parts = []
        cursor = 0
        for match in _SHORTCODE_RE.finditer(text):
            icon_name = match.group(1).casefold()
            replacement = _ICONS.get(icon_name)
            if replacement is None:
                continue
            if match.start() > cursor:
                parts.append(NavigableString(text[cursor:match.start()]))
            symbol, label = replacement
            span = soup.new_tag("span")
            span["data-bpa-icon"] = icon_name.removeprefix("fa-")
            span["role"] = "img"
            span["aria-label"] = label
            span["style"] = (
                "display:inline-block; margin-right:0.35em; font-weight:700; "
                "line-height:1;"
            )
            span.string = symbol
            parts.append(span)
            cursor = match.end()
        if not parts:
            continue
        if cursor < len(text):
            parts.append(NavigableString(text[cursor:]))
        node.replace_with(*parts)
    return str(soup)
