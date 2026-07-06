import difflib
import html as html_module
from typing import Callable, Optional


def normalize_name(text: str) -> str:
    """Lowercase + decode HTML entities so &amp; == & in comparisons."""
    return html_module.unescape(text).lower().strip()


def build_name_matcher(moodle_names: list) -> Callable[[str], Optional[str]]:
    """Return a function that maps a Brightspace label to its corrected
    Moodle name, or None if nothing matched closely enough.

    Exact normalized match wins outright; otherwise falls back to a fuzzy
    match with a 0.6 similarity cutoff (difflib.get_close_matches).
    """
    norm_to_original = {}
    for name in moodle_names:
        norm_to_original[normalize_name(name)] = name
    norm_keys = list(norm_to_original.keys())

    def matcher(label: str) -> Optional[str]:
        norm_label = normalize_name(label)
        if norm_label in norm_to_original:
            return norm_to_original[norm_label]
        if not norm_keys:
            return None
        close = difflib.get_close_matches(norm_label, norm_keys, n=1, cutoff=0.6)
        if close:
            return norm_to_original[close[0]]
        return None

    return matcher
