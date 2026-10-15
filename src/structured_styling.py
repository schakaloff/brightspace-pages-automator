"""Safe foundations for AI-assisted, deterministic page styling.

The existing styler asks an LLM to rewrite an entire page.  This module takes
the narrower approach proposed for the structured-styling prototype:

* application code splits source HTML into stable, addressable blocks;
* an AI may *plan* which approved component should contain each block;
* application code validates the plan and renders the original block HTML;
* an invalid or lossy plan returns the original HTML unchanged.

This module is deliberately not connected to the production Restyle workflow
yet.  It is a safe seam for experiments and tests while the current styler
continues to behave exactly as it does today.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from content_preservation import content_is_preserved


APPROVED_COMPONENTS = frozenset(
    {
        "content",
        "heading",
        "contact",
        "schedule",
        "meeting",
        "links",
        "files",
        "media",
        "table",
    }
)

_FILE_SUFFIXES = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
        ".csv",
        ".txt",
        ".rtf",
    }
)
_MEETING_HOSTS = (
    "zoom.us",
    "teams.microsoft.com",
    "meet.google.com",
    "webex.com",
)
_CONTACT_RE = re.compile(
    r"\b(professor|instructor|teacher|email|office(?:\s+location)?|contact)\b",
    re.IGNORECASE,
)
_SCHEDULE_RE = re.compile(
    r"\b(class\s+schedule|schedule|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|am|pm)\b|\b\d{1,2}:\d{2}\b",
    re.IGNORECASE,
)
_MEETING_RE = re.compile(r"\b(zoom|teams|webex|meeting\s+room)\b", re.IGNORECASE)


PAGE_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_hash", "page_type", "components"],
    "properties": {
        "source_hash": {"type": "string"},
        "page_type": {"type": "string"},
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "block_ids"],
                "properties": {
                    "kind": {"type": "string", "enum": sorted(APPROVED_COMPONENTS)},
                    "block_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _source_hash(source_html: str) -> str:
    return hashlib.sha256((source_html or "").encode("utf-8")).hexdigest()


def _is_file_href(href: str) -> bool:
    path = urlsplit(href or "").path.casefold()
    if "/content/enforced/" in path:
        return True
    return any(path.endswith(suffix) for suffix in _FILE_SUFFIXES)


def _is_meeting_href(href: str) -> bool:
    host = (urlsplit(href or "").hostname or "").casefold()
    return any(host == expected or host.endswith(f".{expected}") for expected in _MEETING_HOSTS)


def _ordered_hints(tag: Tag, text: str, hrefs: tuple[str, ...]) -> tuple[str, ...]:
    hints: list[str] = []

    def add(name: str, condition: bool) -> None:
        if condition and name not in hints:
            hints.append(name)

    names = {node.name.casefold() for node in tag.find_all(True)} | {tag.name.casefold()}
    add("media", bool(names & {"audio", "embed", "iframe", "object", "video"}))
    add("table", "table" in names)
    add("files", any(_is_file_href(href) for href in hrefs))
    add(
        "meeting",
        any(_is_meeting_href(href) for href in hrefs) or bool(_MEETING_RE.search(text)),
    )
    add("contact", bool(_CONTACT_RE.search(text)) or bool(tag.find("a", href=re.compile(r"^mailto:"))))
    add("schedule", bool(_SCHEDULE_RE.search(text)))
    add("links", bool(hrefs))
    add("heading", tag.name.casefold() in {"h1", "h2", "h3", "h4", "h5", "h6"})
    add("content", True)
    return tuple(hints)


@dataclass(frozen=True)
class PageBlock:
    """One authored source block that a plan must preserve exactly once."""

    id: str
    html: str
    text: str
    hints: tuple[str, ...]
    hrefs: tuple[str, ...]

    @property
    def primary_hint(self) -> str:
        return self.hints[0] if self.hints else "content"


@dataclass(frozen=True)
class PageDocument:
    source_html: str
    source_hash: str
    blocks: tuple[PageBlock, ...]

    @property
    def block_ids(self) -> tuple[str, ...]:
        return tuple(block.id for block in self.blocks)

    def block_map(self) -> dict[str, PageBlock]:
        return {block.id: block for block in self.blocks}


@dataclass(frozen=True)
class PlannedComponent:
    kind: str
    block_ids: tuple[str, ...]


@dataclass(frozen=True)
class PagePlan:
    source_hash: str
    page_type: str
    components: tuple[PlannedComponent, ...]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "PagePlan":
        components = tuple(
            PlannedComponent(
                kind=str(component.get("kind", "")),
                block_ids=tuple(str(item) for item in component.get("block_ids", [])),
            )
            for component in value.get("components", [])
            if isinstance(component, dict)
        )
        return cls(
            source_hash=str(value.get("source_hash", "")),
            page_type=str(value.get("page_type", "general")) or "general",
            components=components,
        )


@dataclass(frozen=True)
class PlanValidation:
    valid: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class RenderResult:
    html: str
    used_fallback: bool
    reason: str = ""


def extract_document(source_html: str) -> PageDocument:
    """Split a source fragment into stable blocks without interpreting layout.

    Direct children are intentionally the unit of preservation.  A complicated
    or unfamiliar wrapper remains one block rather than being destructively
    flattened.  Text nodes are represented as paragraphs so they remain
    addressable and visible after rendering.
    """

    source_html = source_html or ""
    soup = BeautifulSoup(source_html, "lxml")
    root = soup.body or soup
    blocks: list[PageBlock] = []

    for child in root.children:
        if isinstance(child, NavigableString):
            text = _normalise_text(str(child))
            if not text:
                continue
            block_html = f"<p>{html_lib.escape(text)}</p>"
            tag_soup = BeautifulSoup(block_html, "lxml")
            tag = tag_soup.find("p")
        elif isinstance(child, Tag):
            tag = child
            block_html = str(child)
            text = _normalise_text(child.get_text(" ", strip=True))
        else:
            continue

        if tag is None:
            continue
        anchors = list(tag.find_all("a", href=True))
        if tag.name.casefold() == "a" and tag.has_attr("href"):
            anchors.insert(0, tag)
        hrefs = tuple(str(anchor.get("href", "")) for anchor in anchors)
        digest = hashlib.sha1(f"{len(blocks)}\0{block_html}".encode("utf-8")).hexdigest()[:10]
        blocks.append(
            PageBlock(
                id=f"block-{len(blocks) + 1:04d}-{digest}",
                html=block_html,
                text=text,
                hints=_ordered_hints(tag, text, hrefs),
                hrefs=hrefs,
            )
        )

    return PageDocument(
        source_html=source_html,
        source_hash=_source_hash(source_html),
        blocks=tuple(blocks),
    )


def build_conservative_plan(document: PageDocument, page_type: str = "general") -> PagePlan:
    """Create a no-AI plan that preserves source order and uses obvious hints."""

    components = tuple(
        PlannedComponent(kind=block.primary_hint, block_ids=(block.id,))
        for block in document.blocks
    )
    return PagePlan(document.source_hash, page_type, components)


def _component_has_evidence(
    component: PlannedComponent,
    block_map: dict[str, PageBlock],
) -> bool:
    if component.kind == "content":
        return True
    return any(
        component.kind in block_map[block_id].hints
        for block_id in component.block_ids
        if block_id in block_map
    )


def validate_plan(document: PageDocument, plan: PagePlan) -> PlanValidation:
    """Reject plans that could lose, duplicate, invent, or mislabel content."""

    errors: list[str] = []
    block_map = document.block_map()
    expected = Counter(document.block_ids)
    referenced = Counter(
        block_id
        for component in plan.components
        for block_id in component.block_ids
    )

    if plan.source_hash != document.source_hash:
        errors.append("the plan was generated for a different source page")

    for component_number, component in enumerate(plan.components, start=1):
        if component.kind not in APPROVED_COMPONENTS:
            errors.append(
                f"component {component_number} uses unsupported kind {component.kind!r}"
            )
        if not component.block_ids:
            errors.append(f"component {component_number} contains no source blocks")
        unknown = [block_id for block_id in component.block_ids if block_id not in block_map]
        if unknown:
            errors.append(
                f"component {component_number} references unknown blocks: {', '.join(unknown)}"
            )
        elif component.kind in APPROVED_COMPONENTS and not _component_has_evidence(component, block_map):
            errors.append(
                f"component {component_number} has no source evidence for {component.kind!r}"
            )

    missing = [block_id for block_id in document.block_ids if referenced[block_id] == 0]
    duplicated = [block_id for block_id, count in referenced.items() if count > 1 and block_id in expected]
    if missing:
        errors.append(f"source blocks are missing from the plan: {', '.join(missing)}")
    if duplicated:
        errors.append(f"source blocks are duplicated in the plan: {', '.join(duplicated)}")

    return PlanValidation(valid=not errors, errors=tuple(errors))


def render_plan(document: PageDocument, plan: PagePlan) -> RenderResult:
    """Render approved wrappers around original HTML, or return the source.

    No component is allowed to generate visible labels or destinations.  The
    only visible content in a successful result comes from the source blocks.
    """

    validation = validate_plan(document, plan)
    if not validation.valid:
        return RenderResult(
            html=document.source_html,
            used_fallback=True,
            reason="; ".join(validation.errors),
        )

    block_map = document.block_map()
    rendered_components = []
    for component in plan.components:
        authored_html = "\n".join(block_map[block_id].html for block_id in component.block_ids)
        kind = html_lib.escape(component.kind, quote=True)
        rendered_components.append(
            f'<section class="bpa-component bpa-component--{kind}">\n'
            f"{authored_html}\n"
            "</section>"
        )

    page_type = re.sub(r"[^a-z0-9_-]+", "-", plan.page_type.casefold()).strip("-") or "general"
    rendered = (
        f'<div class="bpa-structured-page bpa-page--{page_type}">\n'
        + "\n".join(rendered_components)
        + "\n</div>"
    )
    preserved, detail = content_is_preserved(document.source_html, rendered)
    if not preserved:
        return RenderResult(
            html=document.source_html,
            used_fallback=True,
            reason=f"rendered content failed preservation: {detail}",
        )
    return RenderResult(html=rendered, used_fallback=False)


def analysis_payload(document: PageDocument) -> dict[str, Any]:
    """Return the non-visual information an AI needs to propose a PagePlan."""

    return {
        "source_hash": document.source_hash,
        "blocks": [
            {
                "id": block.id,
                "text": block.text,
                "hints": list(block.hints),
                "hrefs": list(block.hrefs),
            }
            for block in document.blocks
        ],
    }


def analysis_payload_json(document: PageDocument) -> str:
    return json.dumps(analysis_payload(document), ensure_ascii=False, indent=2)


def plan_from_json(value: str) -> PagePlan:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("page plan must be a JSON object")
    return PagePlan.from_mapping(parsed)


def referenced_block_ids(plan: PagePlan) -> Iterable[str]:
    for component in plan.components:
        yield from component.block_ids
