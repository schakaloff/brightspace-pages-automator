"""Local link-review drafts. This module performs no requests or course writes."""

from dataclasses import dataclass
from urllib.parse import urlparse


OPEN_LABELS = {"keep": "Leave as is", "new": "New tab", "same": "Same tab"}


def valid_web_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"https", "http"} and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


@dataclass
class LinkDraft:
    id: str
    title: str
    page: str
    current_url: str
    broken: bool = True
    replacement: str = ""
    replacement_kind: str = "url"
    opening: str = "keep"
    selected: bool = False
    current_opening: str = "same"

    @property
    def ready(self) -> bool:
        if self.opening not in OPEN_LABELS:
            return False
        if self.replacement_kind == "file":
            valid = bool(self.replacement)
        else:
            valid = not self.replacement or valid_web_url(self.replacement)
        return valid and (not self.broken or bool(self.replacement))

    @property
    def changed(self) -> bool:
        return bool(self.replacement and self.replacement != self.current_url) or (
            self.opening != "keep" and self.opening != self.current_opening
        )

    @property
    def description(self) -> str:
        if self.replacement:
            return {
                "moodle": "Use matching Moodle file",
                "file": "Use chosen file",
            }.get(self.replacement_kind, "Use replacement URL")
        if self.broken:
            return "Choose a replacement"
        if self.opening != "keep" and self.changed:
            return f"Open in {OPEN_LABELS[self.opening].lower()}"
        return "No change"


def sample_links() -> list[LinkDraft]:
    return [
        LinkDraft("notes", "Lecture notes.pdf", "Lesson 1 resources",
                  "https://brightspace.example/missing/notes.pdf",
                  replacement="https://moodle.example/files/notes.pdf",
                  replacement_kind="moodle", selected=True),
        LinkDraft("outline", "Course outline.pdf", "Lesson 1 resources",
                  "https://brightspace.example/missing/outline.pdf",
                  replacement="https://moodle.example/files/outline.pdf",
                  replacement_kind="moodle", selected=True),
        LinkDraft("reading", "Reading.pdf", "Lesson 1 resources",
                  "https://brightspace.example/missing/reading.pdf"),
        LinkDraft("guide", "Course guide.pdf", "Course information",
                  "https://brightspace.example/files/guide.pdf", broken=False),
    ]


def selected_changes(links: list[LinkDraft]) -> list[LinkDraft]:
    """Include selected changes even when a filter hides their rows."""
    selected = [link for link in links if link.selected]
    if any(not link.ready or not link.changed for link in selected):
        raise ValueError("Choose a replacement or opening change for each selected link.")
    return selected
