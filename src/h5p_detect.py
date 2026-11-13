"""Recognise H5P content in Brightspace topics and saved page HTML.

Plain functions: they need no browser or checker state, so any module can use them.
"""
from html import unescape  # the parameter below is named `html`, so import the function itself
import re
from urllib.parse import parse_qs, urlparse


def is_h5p_topic(topic: dict) -> bool:
    """Return true only for a module topic that points to H5P content.

    The module-structure API exposes the H5P launch URL and, depending on
    the Brightspace version, a textual type identifier.  Do not infer H5P
    from its title: course PDFs frequently use the activity's title.
    """
    values = (
        topic.get("Url", ""), topic.get("url", ""),
        topic.get("TypeIdentifier", ""), topic.get("typeIdentifier", ""),
        topic.get("TopicType", ""), topic.get("topicType", ""),
    )
    return any(
        "h5p" in str(value).lower()
        for value in values if value is not None
    )

def html_contains_h5p_embed(html: str) -> bool:
    """Recognize the H5P or LTI iframe format saved by Insert Stuff."""
    if re.search(
        r'<(?:iframe|embed|object)\b[^>]*(?:h5p\.com|h5p\.org)',
        html or "", re.IGNORECASE,
    ):
        return True
    # Brightspace rewrites H5P embeds to an internal QuickLink iframe.
    # The exact page title is checked before this method is called.
    for source in re.findall(
        r'<iframe\b[^>]*\bsrc=["\']([^"\']+)["\']', html or "", re.IGNORECASE
    ):
        parsed = urlparse(unescape(source))
        if (parsed.path.lower().endswith("/quicklink.d2l")
                and "lti" in [v.lower() for v in parse_qs(parsed.query).get("type", [])]):
            return True
    return False
