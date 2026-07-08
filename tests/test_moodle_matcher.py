import sys
sys.path.insert(0, "src")

from moodle_matcher import normalize_name, build_name_matcher


def test_normalize_name_lowercases_and_unescapes_entities():
    assert normalize_name("Chapter 9 &amp; Review") == "chapter 9 & review"
    assert normalize_name("  Extra Space  ") == "extra space"


def test_build_name_matcher_exact_match():
    matcher = build_name_matcher(["Chapter 9 PowerPoint", "Lecture Notes"])
    assert matcher("chapter 9 powerpoint") == "Chapter 9 PowerPoint"


def test_build_name_matcher_fuzzy_match_above_cutoff():
    matcher = build_name_matcher(["Communicating Effectively PowerPoint"])
    # Brightspace label is a mangled/shortened variant of the Moodle name
    assert matcher("Communicating Effectively PPT") == "Communicating Effectively PowerPoint"


def test_build_name_matcher_no_match_below_cutoff():
    matcher = build_name_matcher(["Totally Unrelated Item"])
    assert matcher("Communicating Effectively PowerPoint") is None


def test_build_name_matcher_empty_names_list_never_matches():
    matcher = build_name_matcher([])
    assert matcher("Anything") is None
