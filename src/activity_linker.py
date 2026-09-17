"""Plan safe Brightspace Content links for existing assignments and quizzes.

This module deliberately has no browser or API calls.  It turns the read-only
Content, Assignments, and Quizzes inventories into a conservative plan that
``ContentChecker`` may execute during a confirmed Full Run.
"""

from __future__ import annotations

from typing import Iterable

from content_matcher import _norm


ACTIVITY_TYPES = {
    "ASSIGN": {"content_activity_type": 3, "label": "Assignment"},
    "QUIZ": {"content_activity_type": 4, "label": "Quiz"},
}


def _value(item: dict, *names: str):
    for name in names:
        value = item.get(name)
        if value is not None:
            return value
    return None


def activity_title(item: dict) -> str:
    return str(_value(item, "Name", "Title", "name", "title") or "").strip()


def activity_id(item: dict):
    return _value(item, "Id", "ID", "FolderId", "QuizId", "id")


def _exact_module_by_title(bs_flat: Iterable[dict]) -> dict[str, dict]:
    """Return only unique exact-normalized module names.

    A duplicate Brightspace unit title is not a safe automatic destination.
    """
    grouped: dict[str, list[dict]] = {}
    for item in bs_flat:
        if item.get("kind") != "MODULE" or item.get("id") is None:
            continue
        grouped.setdefault(_norm(str(item.get("title", ""))), []).append(item)
    return {name: values[0] for name, values in grouped.items() if len(values) == 1}


def _is_linked_content_topic(topic: dict, expected_type: int, title_norm: str) -> bool:
    if topic.get("kind") != "TOPIC" or _norm(str(topic.get("title", ""))) != title_norm:
        return False
    try:
        return int(topic.get("activity_type")) == expected_type
    except (TypeError, ValueError):
        return False


def resolve_existing_activity_links(
    results: list[dict],
    bs_flat: list[dict],
    assignments: Iterable[dict],
    quizzes: Iterable[dict],
) -> list[dict]:
    """Classify Moodle assignments/quizzes using only exact, typed matches.

    The returned entries are the only entries Full Run is allowed to attach.
    This intentionally ignores fuzzy Content matches: a fuzzy title must never
    cause a write to Brightspace.
    """
    catalogues = {"ASSIGN": list(assignments), "QUIZ": list(quizzes)}
    modules = _exact_module_by_title(bs_flat)
    plan: list[dict] = []

    for result in results:
        kind = result.get("type")
        if kind not in ACTIVITY_TYPES or result.get("embedded"):
            continue

        title = str(result.get("name", "")).strip()
        title_norm = _norm(title)
        expected_type = ACTIVITY_TYPES[kind]["content_activity_type"]
        linked_topics = [
            topic for topic in bs_flat
            if _is_linked_content_topic(topic, expected_type, title_norm)
        ]
        if linked_topics:
            result.update({
                "status": "activity_linked",
                "matched": linked_topics[0].get("title", title),
                "activity_link_state": "already_linked",
            })
            continue

        exact = [
            activity for activity in catalogues[kind]
            if activity_title(activity) and _norm(activity_title(activity)) == title_norm
        ]
        if len(exact) == 0:
            result.update({
                "status": "missing",
                "matched": None,
                "activity_link_state": "no_exact_activity",
            })
            continue
        if len(exact) > 1:
            result.update({
                "status": "ambiguous_activity",
                "matched": None,
                "activity_link_state": "multiple_exact_activities",
                "activity_match_count": len(exact),
            })
            continue

        activity = exact[0]
        destination = modules.get(_norm(str(result.get("section", ""))))
        result.update({
            "status": "existing_activity_link_missing",
            "matched": activity_title(activity),
            "activity_link_state": "one_exact_activity",
            "activity_id": activity_id(activity),
            "activity_kind": kind,
            "activity_label": ACTIVITY_TYPES[kind]["label"],
            "target_module_id": destination.get("id") if destination else None,
            "target_module_title": destination.get("title") if destination else None,
        })
        if destination and activity_id(activity) is not None:
            plan.append(result)

    return plan
