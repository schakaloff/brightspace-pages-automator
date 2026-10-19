"""Section dividers an instructor can select and delete in Brightspace.

The D2L editor only selects an <hr> when the click lands on the element
itself, and a stock rule is 2px tall, so a click a pixel off lands in the
text beside it and Delete does nothing. Transparent borders widen the hit
area to 18px while the visible line stays a 2px neutral gray stroke.
"""

import re
from html.parser import HTMLParser

EDITABLE_HR_STYLE = (
    "box-sizing: content-box; height: 2px; margin: 16px 0; padding: 0; "
    "border: 0; border-top: 8px solid transparent; "
    "border-bottom: 8px solid transparent; background-color: #cdd5dc; "
    "background-clip: padding-box;"
)

_ATTRIBUTE = re.compile(r"""\s+([^\s=/>]+)(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?""")
_LITERAL_TAGS = {"script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext"}


class _DividerParser(HTMLParser):
    def __init__(self, markup: str):
        super().__init__(convert_charrefs=False)
        self.line_starts = [0, *[match.end() for match in re.finditer("\n", markup)]]
        self.edits = []
        self.literal_tag = None

    def handle_starttag(self, tag, attrs):
        self._start_tag(tag, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._start_tag(tag, self_closing=True)

    def _start_tag(self, tag, self_closing):
        if self.literal_tag:
            return
        if tag in _LITERAL_TAGS and not self_closing:
            self.literal_tag = tag
        if tag != "hr":
            return
        raw = self.get_starttag_text()
        attributes = raw[3:-2] if self_closing else raw[3:-1]
        # Match whole attributes so text such as data-note="old style=red"
        # is preserved rather than mistaken for a separate style attribute.
        attributes = _ATTRIBUTE.sub(
            lambda match: "" if match.group(1).casefold() == "style" else match.group(),
            attributes,
        ).rstrip()
        line, column = self.getpos()
        start = self.line_starts[line - 1] + column
        self.edits.append((start, start + len(raw), f'<hr{attributes} style="{EDITABLE_HR_STYLE}">'))

    def handle_endtag(self, tag):
        if tag == self.literal_tag and tag != "plaintext":
            self.literal_tag = None


def make_dividers_easy_to_select(markup: str) -> str:
    """Give every <hr> the wide click area, keeping its other attributes."""
    if not markup or "<hr" not in markup.lower():
        return markup

    parser = _DividerParser(markup)
    parser.feed(markup)
    parser.close()
    if not parser.edits:
        return markup
    parts = []
    previous_end = 0
    for start, end, replacement in parser.edits:
        parts.extend((markup[previous_end:start], replacement))
        previous_end = end
    parts.append(markup[previous_end:])
    return "".join(parts)
