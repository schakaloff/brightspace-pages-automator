"""Deterministic, idempotent conversion of standalone YouTube URLs to players.

This runs before AI styling.  The resulting iframe is therefore handled as an
atomic protected block by :mod:`content_preservation`; the model never sees or
gets an opportunity to rewrite the video ID or URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAIN_URL_RE = re.compile(r"^https?://[^\s<>]+$", re.IGNORECASE)
_TIME_RE = re.compile(
    r"^(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    start_seconds: int | None = None
    end_seconds: int | None = None

    @property
    def embed_url(self) -> str:
        query = {}
        if self.start_seconds:
            query["start"] = str(self.start_seconds)
        if self.end_seconds and self.end_seconds > (self.start_seconds or 0):
            query["end"] = str(self.end_seconds)
        suffix = f"?{urlencode(query)}" if query else ""
        return f"https://www.youtube.com/embed/{self.video_id}{suffix}"


@dataclass(frozen=True)
class YouTubeTransform:
    html: str
    embeds_created: int = 0
    redundant_urls_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.embeds_created or self.redundant_urls_removed)


def _timestamp_seconds(value: str | None) -> int | None:
    value = (value or "").strip().lower()
    if not value:
        return None
    if value.isdigit():
        seconds = int(value)
        return seconds if seconds >= 0 else None
    match = _TIME_RE.fullmatch(value)
    if not match or not any(match.groupdict().values()):
        return None
    return (
        int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )


def parse_youtube_url(value: str) -> YouTubeVideo | None:
    """Return a canonical video reference for supported YouTube URL forms."""
    raw = (value or "").strip().rstrip(".,;)")
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"}:
        return None

    host = (parsed.hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    query = parse_qs(parsed.query)
    path_parts = [part for part in parsed.path.split("/") if part]

    video_id = None
    if host == "youtu.be" and len(path_parts) == 1:
        video_id = path_parts[0]
    elif host in {"youtube.com", "youtube-nocookie.com"}:
        if parsed.path.rstrip("/").casefold() == "/watch":
            video_id = (query.get("v") or [None])[0]
        elif len(path_parts) == 2 and path_parts[0].casefold() == "embed":
            video_id = path_parts[1]
    if not video_id or not _VIDEO_ID_RE.fullmatch(video_id):
        return None

    start = _timestamp_seconds(
        (query.get("start") or query.get("t") or query.get("time_continue") or [None])[0]
    )
    if start is None and parsed.fragment:
        fragment = parsed.fragment
        if fragment.startswith("t="):
            fragment = fragment[2:]
        start = _timestamp_seconds(fragment)
    end = _timestamp_seconds((query.get("end") or [None])[0])
    return YouTubeVideo(video_id=video_id, start_seconds=start, end_seconds=end)


def youtube_embed_markup(video: YouTubeVideo) -> str:
    """Build the single standard player used by Collector and Restyle."""
    src = video.embed_url.replace("&", "&amp;")
    return (
        '<div class="bpa-youtube-embed" data-bpa-youtube-id="'
        f'{video.video_id}" style="position:relative; width:100%; max-width:960px; '
        'aspect-ratio:16/9; overflow:hidden">'
        '<iframe src="'
        f'{src}" title="YouTube video player" loading="lazy" '
        'style="position:absolute; inset:0; width:100%; height:100%; border:0" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
        'gyroscope; picture-in-picture; web-share" allowfullscreen></iframe></div>'
    )


def _iframe_video_ids(root: Tag) -> set[str]:
    result = set()
    for iframe in root.find_all("iframe", src=True):
        video = parse_youtube_url(str(iframe.get("src", "")))
        if video:
            result.add(video.video_id)
    return result


def _raw_anchor_video(anchor: Tag) -> YouTubeVideo | None:
    href = str(anchor.get("href", "")).strip()
    video = parse_youtube_url(href)
    if not video:
        return None
    # Meaningful custom text is an intentional normal link and must survive.
    label = " ".join(anchor.get_text(" ", strip=True).split())
    if label != href and label != href.rstrip("/"):
        return None
    return video


def _container_is_only(node: Tag, target: Tag | NavigableString) -> bool:
    for child in node.contents:
        if child is target:
            continue
        if isinstance(child, NavigableString) and not str(child).strip():
            continue
        return False
    return True


def transform_standalone_youtube_urls(source_html: str) -> YouTubeTransform:
    """Replace standalone raw YouTube URLs without touching intentional links.

    A URL is standalone only when it is the sole meaningful content of its
    paragraph/div/list item, either as text or as an anchor whose label is the
    URL itself. Existing iframes are canonicalized by video ID for de-duplication.
    """
    markup = source_html or ""
    if "<" not in markup and ">" not in markup:
        markup = f"<body>{markup}</body>"
    soup = BeautifulSoup(markup, "lxml")
    root = soup.body or soup
    existing_ids = _iframe_video_ids(root)
    created = removed = 0

    candidates: list[tuple[Tag, Tag | NavigableString, YouTubeVideo]] = []
    for anchor in list(root.find_all("a", href=True)):
        video = _raw_anchor_video(anchor)
        parent = anchor.parent
        if video and isinstance(parent, Tag) and parent.name in {"p", "div", "li", "body"}:
            if _container_is_only(parent, anchor):
                candidates.append((parent, anchor, video))

    anchor_text_nodes = {id(text) for anchor in root.find_all("a") for text in anchor.find_all(string=True)}
    for text_node in list(root.find_all(string=True)):
        if id(text_node) in anchor_text_nodes:
            continue
        raw = str(text_node).strip()
        if not _PLAIN_URL_RE.fullmatch(raw):
            continue
        video = parse_youtube_url(raw)
        parent = text_node.parent
        if video and isinstance(parent, Tag) and parent.name in {"p", "div", "li", "body"}:
            if _container_is_only(parent, text_node):
                candidates.append((parent, text_node, video))

    seen_containers = set()
    for container, _target, video in candidates:
        if id(container) in seen_containers or container.parent is None:
            continue
        seen_containers.add(id(container))
        if video.video_id in existing_ids:
            container.decompose()
            removed += 1
            continue
        fragment = BeautifulSoup(youtube_embed_markup(video), "lxml")
        replacement = (fragment.body or fragment).find("div")
        if replacement is None:
            continue
        if container.name == "body":
            container.clear()
            container.append(replacement)
        else:
            container.replace_with(replacement)
        existing_ids.add(video.video_id)
        created += 1

    body = soup.find("body")
    result = body.decode_contents() if body else str(soup)
    return YouTubeTransform(result.strip(), created, removed)
