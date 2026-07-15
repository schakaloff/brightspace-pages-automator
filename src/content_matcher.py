import difflib
import html as html_module
import re
from typing import Optional


# Confidence assigned to a _containment_match hit. Deliberately mid-REVIEW
# (75–89 band), not LIKELY — containment is a heuristic (shared number + keyword
# prefix), not a similarity score, so it must not read as a confident match.
_CONTAINMENT_SCORE = 80


def _norm(text: str) -> str:
    """Lowercase + decode HTML entities so &amp; == & in comparisons."""
    return html_module.unescape(text).lower().strip()


def _numbers_conflict(a: str, b: str) -> bool:
    """Return True if a and b have the same number of numeric tokens but any differ in value.
    Prevents 'Chapter 6 PowerPoint' fuzzy-matching 'Chapter 9 PowerPoint'."""
    nums_a = re.findall(r'\d+', a)
    nums_b = re.findall(r'\d+', b)
    if not nums_a and not nums_b:
        return False
    if len(nums_a) != len(nums_b):
        return False
    return any(int(x) != int(y) for x, y in zip(nums_a, nums_b))


_WORD_NUMS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20",
}
_WORD_NUMS_RE = re.compile(r'\b(' + '|'.join(_WORD_NUMS) + r')\b', re.IGNORECASE)


def _digitize(text: str) -> str:
    """Spell out numbers -> digits (comparison-only), so 'Chapters Three and Four'
    lines up with 'Chapter 3'."""
    return _WORD_NUMS_RE.sub(lambda m: _WORD_NUMS[m.group(0).lower()], text)


def _containment_match(name_l: str, candidates) -> Optional[str]:
    """Catch short Moodle names ('Chapter 3') that are conceptually contained in a
    longer combined Brightspace title ('Week Two: Chapters Three and Four - ...').
    difflib's whole-string ratio penalizes this length mismatch too heavily, so this
    checks number-token overlap plus a shared keyword instead of literal substring."""
    name_digit = _digitize(name_l)
    name_nums = set(re.findall(r'\d+', name_digit))
    name_words = [w for w in re.findall(r'[a-z]+', name_digit) if len(w) >= 4]
    if not name_nums or not name_words:
        return None
    for key in candidates:
        cand_digit = _digitize(key)
        if not name_nums & set(re.findall(r'\d+', cand_digit)):
            continue
        cand_words = re.findall(r'[a-z]+', cand_digit)
        if any(cw.startswith(nw) or nw.startswith(cw) for nw in name_words for cw in cand_words):
            return key
    return None


# ── External tool detection ───────────────────────────────────────────────────

_EXTERNAL_TOOLS = {
    "access pearson":   "Access Pearson resource present - will need to be re-linked",
    "aktiv":            "Aktiv (Top Hat) resource present - will need to be re-linked",
    "top hat":          "Aktiv (Top Hat) resource present - will need to be re-linked",
    "cengage":          "Cengage resource present - will need to be re-linked",
    "electude":         "Electude resource present - will need to be re-linked",
    "hls":              "HLS videos to be connected during staging",
    "harris learning":  "HLS videos to be connected during staging",
    "kaltura":          "Kaltura resource present - will need to be re-linked",
    "macmillan":        "Macmillan Learning resource present - will need to be re-linked",
    "mcgraw":           "McGraw Hill resource present - will need to be re-linked",
    "myokanaganmath":   "MyOkanaganMath resource present - will need to be re-linked",
    "myokanagan":       "MyOkanaganMath resource present - will need to be re-linked",
    "stukent":          "Stukent resource present - will need to be re-linked",
    "wileyplus":        "WileyPlus resource present - will need to be re-linked",
    "wiris":            "Wiris Quizzes resource present - will need to be re-linked",
    "zoom":             "Zoom will need to be re-linked",
    "h5p":              "H5P will need to be manually uploaded by educators",
    "media collection": "Media collection resource present - will need to be relinked",
    "turnitin":         "Turnitin resource present - will need to be relinked",
    "poodll":           "Poodll resource present - will need to be relinked",
}

def _detect_external_tool(name: str, hint: str = "") -> Optional[tuple]:
    """Return (tool_label, warning_message) if the name or Moodle module class matches a known tool."""
    # H5P is identified by its modtype class, not its name
    if "hvp" in hint or "h5p" in hint:
        return "H5P", _EXTERNAL_TOOLS["h5p"]
    name_l = name.lower()
    for keyword, message in _EXTERNAL_TOOLS.items():
        if keyword in name_l:
            return keyword.title(), message
    return None


def match_sections(section_names: list, bs_modules: list) -> dict:
    """Match Moodle section names to Brightspace modules using the same
    exact/fuzzy/containment logic as _compare_items's SECTION branch.

    bs_modules: [{"id": ..., "title": ...}, ...]
    Returns {section_name: (matched_module_dict_or_None, score)} — 100 for exact,
    ~70-100 for fuzzy, 80 (_CONTAINMENT_SCORE) for containment, 0 for no match.
    """
    bs_by_norm = {_norm(m["title"]): m for m in bs_modules}
    results = {}
    for name in section_names:
        name_l = _norm(name)
        if name_l in bs_by_norm:
            results[name] = (bs_by_norm[name_l], 100)
            continue
        close = difflib.get_close_matches(name_l, bs_by_norm.keys(), n=1, cutoff=0.70)
        if close and not _numbers_conflict(name_l, close[0]):
            score = int(difflib.SequenceMatcher(None, name_l, close[0]).ratio() * 100)
            results[name] = (bs_by_norm[close[0]], score)
            continue
        contained = _containment_match(name_l, bs_by_norm)
        if contained:
            results[name] = (bs_by_norm[contained], _CONTAINMENT_SCORE)
            continue
        results[name] = (None, 0)
    return results


def _compare_items(moodle_items: list, bs_flat: list) -> list:
    SKIP = {"LABEL", "FORUM"}

    bs_modules = {_norm(i["title"]): i["title"] for i in bs_flat if i["kind"] == "MODULE"}
    bs_topics  = {_norm(i["title"]): i["title"] for i in bs_flat if i["kind"] == "TOPIC"}
    bs_all     = {**bs_modules, **bs_topics}

    results = []
    current_section = ""

    for item in moodle_items:
        if item["type"] == "SECTION":
            current_section = item["name"]
            name_l = _norm(item["name"])
            if name_l in bs_modules:
                status, matched = "exact", bs_modules[name_l]
            else:
                close = difflib.get_close_matches(name_l, bs_modules.keys(), n=1, cutoff=0.70)
                if close and not _numbers_conflict(name_l, close[0]):
                    score = int(difflib.SequenceMatcher(None, name_l, close[0]).ratio() * 100)
                    status, matched = "fuzzy", (bs_modules[close[0]], score)
                else:
                    contained = _containment_match(name_l, bs_modules)
                    if contained:
                        status, matched = "fuzzy", (bs_modules[contained], _CONTAINMENT_SCORE)
                    else:
                        status, matched = "missing", None
            results.append({**item, "section": "", "status": status, "matched": matched})
            continue

        if item["type"] in SKIP:
            # Accordion labels carry structure we want to display — pass them through
            if item["type"] == "LABEL" and item.get("accordion_cards") is not None:
                results.append({**item, "section": current_section, "status": "label_accordion", "matched": None})
            continue

        # External tools are flagged separately — don't try to match them in BS
        if item["type"] == "EXTERNAL":
            detected = _detect_external_tool(item["name"], item.get("hint", ""))
            tool_label, warning = detected if detected else ("External Tool", "External tool - will need to be re-linked")
            results.append({**item, "section": current_section,
                             "status": "external", "matched": warning, "tool_label": tool_label})
            continue

        # Embedded items (found inside page bodies) — preserve as-is, no BS comparison
        if item.get("embedded"):
            results.append({**item, "status": "embedded"})
            continue

        name_l = _norm(item["name"])

        if name_l in bs_all:
            results.append({**item, "section": current_section,
                             "status": "exact", "matched": bs_all[name_l]})
            continue

        close = difflib.get_close_matches(name_l, bs_all.keys(), n=1, cutoff=0.75)
        if close and not _numbers_conflict(name_l, close[0]):
            score = int(difflib.SequenceMatcher(None, name_l, close[0]).ratio() * 100)
            results.append({**item, "section": current_section,
                             "status": "fuzzy", "matched": bs_all[close[0]], "score": score})
            continue

        contained = _containment_match(name_l, bs_all)
        if contained:
            results.append({**item, "section": current_section,
                             "status": "fuzzy", "matched": bs_all[contained], "score": _CONTAINMENT_SCORE})
            continue

        results.append({**item, "section": current_section,
                         "status": "missing", "matched": None})

    return results
