"""Identify a Brightspace course without confusing it with a unit or topic."""
import re
from urllib.parse import parse_qs, urlparse


def brightspace_course_key(url):
    value = str(url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value if "://" in value else "https://" + value)
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not hostname:
        return None
    for pattern in (r"/lessons/(\d+)(?:/|$)", r"/content/(\d+)(?:/|$)", r"/d2l/home/(\d+)(?:/|$)"):
        match = re.search(pattern, parsed.path, re.IGNORECASE)
        if match:
            return hostname, match.group(1)
    course_id = parse_qs(parsed.query).get("ou", [""])[0]
    if course_id.isdigit():
        return hostname, course_id
    return None


def moodle_course_id(url):
    try:
        parsed = urlparse(str(url or "").strip())
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not hostname or parsed.username or parsed.password:
        return None
    course_id = parse_qs(parsed.query).get("id", [""])[0]
    if parsed.path.rstrip("/").endswith("/course/view.php") and course_id.isdigit():
        return course_id
    return None
