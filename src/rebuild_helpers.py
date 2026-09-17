"""Pure helpers for safe, idempotent Content reconstruction."""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from content_matcher import _norm


INVALID_SECTION_NAMES = {"", "(unnamed)", "(unnamed section)"}


def valid_section_name(value: object) -> bool:
    return str(value or "").strip().lower() not in INVALID_SECTION_NAMES


def valid_activity_url(value: object, course_url: str) -> bool:
    """Reject edit controls, fragments, and links back to the course index."""
    href = str(value or "").strip()
    if not href or href == "#" or href.lower().startswith("javascript:"):
        return False
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    source = urlparse(str(course_url or ""))
    return not (
        parsed.scheme.lower() == source.scheme.lower()
        and parsed.netloc.lower() == source.netloc.lower()
        and parsed.path.rstrip("/") == source.path.rstrip("/")
        and parsed.query == source.query
    )


def exact_module_map(bs_flat: list[dict]) -> dict[str, dict]:
    """Return only uniquely named modules; duplicate titles are unsafe targets."""
    grouped: dict[str, list[dict]] = {}
    for item in bs_flat:
        if item.get("kind") != "MODULE" or item.get("id") is None:
            continue
        grouped.setdefault(_norm(str(item.get("title", ""))), []).append(item)
    return {key: values[0] for key, values in grouped.items() if key and len(values) == 1}


def extract_pluginfile_url(markup: str, base_url: str) -> str | None:
    # Only activity content counts: theme logos and user pictures are also
    # served from pluginfile.php and appear earlier on every Moodle page.
    match = re.search(
        r'''(?:href|src|data)\s*=\s*["']([^"']*pluginfile\.php[^"']*/mod_(?:resource|folder)/content/[^"']*)["']''',
        markup or "",
        flags=re.IGNORECASE,
    )
    return urljoin(base_url, html.unescape(match.group(1))) if match else None


def with_moodle_redirect(href: str) -> str:
    """Ask mod/url and mod/resource view pages to redirect to their target."""
    if not re.search(r"/mod/(?:url|resource)/view\.php", href or "") or "redirect=1" in href:
        return href
    return href + ("&" if "?" in href else "?") + "redirect=1"


def is_moodle_url(value: object) -> bool:
    return "mymoodle.okanagan.bc.ca" in urlparse(str(value or "")).netloc.lower()


def extract_url_workaround(markup: str, base_url: str) -> str | None:
    """Return the external target shown on a Moodle URL activity page."""
    match = re.search(
        r'''class\s*=\s*["'][^"']*urlworkaround[^"']*["'][^>]*>.*?<a[^>]+href\s*=\s*["']([^"']+)["']''',
        markup or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return urljoin(base_url, html.unescape(match.group(1))) if match else None


def download_filename(headers: dict, final_url: str, fallback_title: str) -> str:
    disposition = str(headers.get("content-disposition", "") or "")
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, re.IGNORECASE)
    plain = re.search(r'''filename\s*=\s*["']?([^;"']+)''', disposition, re.IGNORECASE)
    value = unquote(encoded.group(1)) if encoded else (plain.group(1).strip() if plain else "")
    if not value:
        value = unquote(Path(urlparse(final_url).path).name)
    value = re.sub(r'[<>:"/\\|?*]', "", value).strip()
    if value and "." in value:
        return value
    fallback = re.sub(r'[<>:"/\\|?*]', "", fallback_title).strip() or "downloaded-file"
    suffix = Path(value).suffix or Path(urlparse(final_url).path).suffix
    return fallback + suffix if suffix and not fallback.lower().endswith(suffix.lower()) else fallback


def validate_download_response(status: int, content_type: str, final_url: str, body: bytes) -> tuple[bool, str]:
    if status < 200 or status >= 400:
        return False, f"HTTP {status}"
    lower_url = (final_url or "").lower()
    if "/login/" in lower_url or "login/index.php" in lower_url or "microsoftonline.com" in lower_url:
        return False, "response redirected to a login page"
    if not body:
        return False, "response body was empty"
    lower_type = (content_type or "").split(";", 1)[0].strip().lower()
    prefix = body[:1024].lstrip().lower()
    if lower_type in {"text/html", "application/xhtml+xml"} or prefix.startswith((b"<!doctype html", b"<html")):
        return False, "response was HTML, not a downloadable file"
    return True, ""


_ORDERABLE_SKIP = {"SECTION", "LABEL", "VIDEO"}


def plan_unit_order(moodle_items: list[dict], children: list[dict]) -> list:
    """Return topic IDs to move to the end of a unit, in Moodle order.

    Only topics whose exact normalized title matches exactly one Brightspace
    topic are ordered; everything else is left where it is.  An empty list
    means the matched topics are already in Moodle order (no writes needed).
    """
    by_title: dict[str, list] = {}
    for child in children:
        if child.get("Type") != 1 or child.get("Id") is None:
            continue
        by_title.setdefault(_norm(str(child.get("Title", ""))), []).append(child["Id"])

    desired: list = []
    for item in moodle_items:
        if item.get("type") in _ORDERABLE_SKIP or item.get("embedded"):
            continue
        ids = by_title.get(_norm(str(item.get("name", ""))), [])
        if len(ids) == 1 and ids[0] not in desired:
            desired.append(ids[0])

    wanted = set(desired)
    current = [child["Id"] for child in children if child.get("Id") in wanted]
    return [] if current == desired or len(desired) < 2 else desired
