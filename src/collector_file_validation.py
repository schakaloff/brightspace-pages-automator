"""Check that a topic download is the course material it claims to be."""

from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlparse


_WEB_ASSET_EXTENSIONS = {".css", ".js", ".map"}
_SLIDE_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".odp", ".key", ".zip"}


def validate_topic_download(label: str, filename: str, source_url: str = "") -> tuple[bool, str]:
    """Reject obvious page assets and files that disagree with topic metadata.

    Brightspace's Download menu can serve a page dependency such as layout.css
    when the selected topic is a lecture slide. Such a file must never be
    uploaded as the slide. Unknown cases stay available through the original
    topic link and are reported for review by the collector.
    """
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    if not name or not suffix:
        return False, "download has no recognizable filename or file type"

    label_lower = (label or "").lower()
    source_name = unquote(Path(urlparse(source_url or "").path).name)
    source_suffix = Path(source_name).suffix.lower()

    if re.search(r"\b(slides?|presentation|lecture deck)\b", label_lower):
        if suffix not in _SLIDE_EXTENSIONS:
            return False, f"expected presentation material, received {name}"

    if suffix in _WEB_ASSET_EXTENSIONS:
        named_asset = suffix.lstrip(".") in label_lower or name.lower() in label_lower
        if not named_asset:
            return False, f"received page asset {name} instead of the topic file"

    if source_suffix and source_suffix not in {".html", ".htm"}:
        if source_suffix != suffix and not (source_name.lower().endswith(".html.zip") and suffix == ".zip"):
            return False, f"download type {suffix} disagrees with topic file {source_name}"
        if source_name.casefold() != name.casefold() and not source_name.lower().endswith(".html.zip"):
            return False, f"download name {name} disagrees with topic file {source_name}"

    return True, ""


def matching_direct_file_url(filename: str, candidate_url: str) -> str:
    """Use a course-file URL only when it names the downloaded file."""
    if not candidate_url:
        return ""
    path = urlparse(candidate_url).path
    if "/content/enforced/" not in path:
        return ""
    return candidate_url if unquote(Path(path).name).casefold() == filename.casefold() else ""


def direct_slide_url(topic_url: str, source_url: str, label: str) -> str:
    """Use an explicit Brightspace course-file URL without downloading it again."""
    if not re.search(r"\b(slides?|presentation|lecture deck)\b", label or "", re.I):
        return ""
    if "/content/enforced/" not in urlparse(source_url or "").path:
        return ""
    resolved = urljoin(topic_url, source_url)
    topic_host = urlparse(topic_url).netloc.casefold()
    parsed = urlparse(resolved)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != topic_host:
        return ""
    if Path(parsed.path).suffix.lower() not in _SLIDE_EXTENSIONS:
        return ""
    return resolved


def external_topic_url(topic_url: str, source_url: str) -> str:
    """Read an external topic destination from Brightspace metadata."""
    parsed = urlparse(source_url or "")
    course_host = urlparse(topic_url).netloc.casefold()
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return source_url if parsed.netloc.casefold() != course_host else ""
