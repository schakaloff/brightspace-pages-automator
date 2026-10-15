"""Copy Components selection: exactly the planned activities, rubrics and grade items."""
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_transfer_structure import source_course
from transfer_components import activity_name, check_selection, selection_for, target_matches


def course():
    source = source_course()
    source["assignments"][0].update(GradeItemId=501, Assessment={"ScoreDenominator": 20, "Rubrics": [{"RubricId": 31}, {"RubricId": 32}]})
    source["modules"][0]["children"] += [
        {"Id": 20, "Type": 1, "Title": "Quiz topic", "ActivityType": 4, "ToolItemId": 80},
        {"Id": 21, "Type": 1, "Title": "Forum topic", "ActivityType": 6, "ToolItemId": 90},
        {"Id": 22, "Type": 1, "Title": "Survey topic", "ActivityType": 12, "ToolItemId": 95}]
    source["quizzes"] = [{"QuizId": 80, "Name": "Week 1 quiz", "GradeItemId": 502}]
    source["discussions"] = [{"TopicId": 90, "Name": "Introduce yourself", "ForumId": 9}]
    source["surveys"] = [{"SurveyId": 95, "Name": "Preferences form"}]
    source["categories"] = [{"Id": 600, "Name": "Assignments", "Grades": [{"Id": 501}]}]
    return source


ROWS = [{"id": "13", "source_id": "13", "kind": "assignment", "title": "Week 1 Essay"},
        {"id": "20", "source_id": "20", "kind": "quiz", "title": "Quiz topic"},
        {"id": "21", "source_id": "21", "kind": "discussion", "title": "Forum topic"},
        {"id": "22", "source_id": "22", "kind": "survey", "title": "Survey topic"}]


def test_selection_has_each_activity_its_rubrics_grade_items_and_their_categories():
    assert selection_for(ROWS, course()) == {
        "dropbox": ["70"], "rubrics": ["31", "32"], "grades": ["501", "502"], "quizzes": ["80"],
        "discuss": ["t90"], "surveys": ["95"], "_grade_categories": ["600"]}


def test_unidentifiable_activities_are_refused():
    source = course()
    source["modules"][0]["children"][-1]["ToolItemId"] = None
    with pytest.raises(ValueError, match="couldn't be identified"):
        selection_for(ROWS[3:], source)


def test_activity_names_come_from_the_tool():
    source = course()
    assert [activity_name(r, source) for r in ROWS] == ["Week 1 Essay", "Week 1 quiz", "Introduce yourself", "Preferences form"]


def test_confirmation_must_match_exactly():
    expected = selection_for(ROWS, course())
    good = {"wizSelectedDropbox": "70", "wizSelectedRubrics": "31|32", "wizSelectedGrades": "501|502|600",
            "wizSelectedQuizzes": "80", "wizSelectedDiscuss": "f9|t90", "wizSelectedSurveys": "95", "wizCopyCourseId": "1"}
    assert check_selection(good, expected) == []
    assert check_selection({**good, "wizSelectedDropbox": "70|71"}, expected)          # an extra assignment
    assert check_selection({**good, "wizSelectedGrades": "501|502|600|700"}, expected)  # an unrelated category
    assert check_selection({**good, "wizSelectedContent": "5"}, expected)              # Content must never be copied
    assert check_selection({**good, "wizSelectedQuizzes": ""}, expected)               # something missing


def test_target_matches_by_kind_and_exact_name():
    target = {"assignments": [{"Id": 1, "Name": "Week 1 Essay"}], "quizzes": [{"QuizId": 2, "Name": "Week 1 quiz"}],
              "discussions": [{"TopicId": 3, "Name": "Other"}], "surveys": [{"SurveyId": 4, "Name": "Preferences form"}]}
    assert target_matches("assignment", "week 1 essay", target) == ["1"]
    assert target_matches("quiz", "Week 1 quiz", target) == ["2"]
    assert target_matches("discussion", "Introduce yourself", target) == []
    assert target_matches("survey", "Anything", target) == []
    assert target_matches("survey", "Preferences form", target) == ["4"]


def test_category_plan_moves_copied_grades_into_the_existing_category_and_removes_the_empty_duplicate():
    from transfer_components import category_plan
    target = {"categories": [{"Id": 800, "Name": "Assignments", "Grades": [{"Id": 1}]},
                             {"Id": 990, "Name": "Assignments", "Grades": [{"Id": 991}]},
                             {"Id": 995, "Name": "Quizzes", "Grades": [{"Id": 996}]},
                             {"Id": 997, "Name": "Labs", "Grades": [{"Id": 998}, {"Id": 999}]}],
              "grades": [{"Id": 991, "Name": "Essay", "CategoryId": 990}, {"Id": 996, "Name": "Quiz", "CategoryId": 995},
                         {"Id": 998, "Name": "Lab 1", "CategoryId": 997}, {"Id": 999, "Name": "Lab 2", "CategoryId": 997}]}
    moves, removals = category_plan(target, {"800", "997"}, ["991", "996", "998"])
    assert moves == [{"grade": "991", "name": "Essay", "from": "990", "to": "800", "category": "Assignments"}]
    assert removals == [{"category": "990", "name": "Assignments"}]
    # A new category with no existing namesake (Quizzes) stays; an existing one is never removed.
    moves, removals = category_plan(target, {"800", "990", "997"}, ["991"])
    assert moves == [] and removals == []


def test_category_notes_explain_what_the_gradebook_gets():
    from transfer_components import category_notes
    source = course()
    source["categories"].append({"Id": 601, "Name": "Quizzes", "Weight": 20, "Grades": [{"Id": 502}]})
    target = {"name": "Shell", "categories": [{"Id": 1, "Name": "Assignments"}]}
    merged = category_notes(ROWS, source, target, merge=True)
    assert merged[0] == "Shell already has a “Assignments” grade category; 1 grade item will be moved into it."
    assert "add the grade category “Quizzes” (20%)" in merged[1]
    assert "second “Assignments” category" in category_notes(ROWS, source, target, merge=False)[0]


def test_course_files_are_selected_by_path_and_their_folders_are_expected():
    from transfer_components import file_selection
    selection = file_selection(["8keys.pdf", "img/preview.png", "a/b/c.mp4", "img/preview.png"])
    assert selection == {"files": [r"\8keys.pdf", r"\img\preview.png", r"\a\b\c.mp4"], "_file_folders": [r"\img", r"\a", r"\a\b"]}
    confirmed = {"wizSelectedFiles": r"\8keys.pdf|\img|\img\Preview.png|\a|\a\b|\a\b\c.mp4"}  # Brightspace's own case
    assert check_selection(confirmed, selection) == []
    assert check_selection({"wizSelectedFiles": r"\8keys.pdf|\img|\img\other.png"}, selection)


@pytest.mark.asyncio
async def test_activity_catalogues_include_every_survey_page_and_distinguish_unavailable_tools():
    from transfer_components import activity_lists
    class API:
        course_id = "2"
        async def _json(self, path):
            if "quizzes" in path: return {"Objects": [], "Next": None}
            if "discussions" in path: return []
            if "bookmark" in path: return {"Objects": [{"SurveyId": 9, "Name": "Second"}], "Next": None}
            return {"Objects": [{"SurveyId": 8, "Name": "First"}], "Next": "https://learn.okanagancollege.ca/d2l/api/le/1.82/2/surveys/?bookmark=x"}
    result = await activity_lists(API())
    assert [s["SurveyId"] for s in result["surveys"]] == [8, 9]
    class Missing(API):
        status = 403
        async def _json(self, path):
            if "surveys" in path: raise RuntimeError(f"HTTP {self.status}")
            return await super()._json(path)
    assert (await activity_lists(Missing()))["surveys"] is None
    failing = Missing(); failing.status = 500
    with pytest.raises(RuntimeError, match="HTTP 500"): await activity_lists(failing)


@pytest.mark.asyncio
async def test_survey_copy_completion_waits_for_its_actual_tool_object():
    from transfer_components import wait_for_copies
    snapshots = iter([{"surveys": []}, {"surveys": [{"SurveyId": 9, "Name": "Preferences"}]}])
    calls = []
    async def fetch():
        calls.append(1)
        return next(snapshots)
    result = await wait_for_copies(fetch, [("survey", "Preferences")], timeout=1, interval=0)
    assert len(calls) == 2 and result["surveys"][0]["SurveyId"] == 9


@pytest.mark.asyncio
async def test_hiding_native_content_keeps_its_url_dates_and_description():
    from transfer_api import TransferAPI
    topic = {"Title": "Preferences", "ShortTitle": "", "Type": 1, "TopicType": 3,
             "Url": "/quickLink?ou=2&type=survey", "StartDate": "2026-09-08T17:00:00Z",
             "EndDate": "2026-09-12T06:59:00Z", "DueDate": None, "IsHidden": False, "IsLocked": False,
             "OpenAsExternalResource": False, "Description": {"Html": "<p>Instructions</p>", "Text": "Instructions"}}
    calls = []
    class API:
        course_id = "2"
        def guard(self): calls.append("guard")
        async def _json(self, path, method="GET", payload=None):
            calls.append((path, method, payload))
            return dict(topic) if method == "GET" else {**payload, "ToolItemId": 95}
    result = await TransferAPI.hide_topic(API(), 7)
    payload = calls[-1][2]
    assert calls[0] == "guard" and result["IsHidden"] and result["ToolItemId"] == 95
    assert all(payload[k] == topic[k] for k in topic if k not in ("IsHidden", "Description"))
    assert payload["Description"] == {"Content": "<p>Instructions</p>", "Type": 1}
