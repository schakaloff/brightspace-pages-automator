from activity_linker import resolve_existing_activity_links


def _result(kind, name, section="Week 1"):
    return {"type": kind, "name": name, "section": section, "status": "missing"}


def _module(title="Week 1", ident=101):
    return {"kind": "MODULE", "title": title, "id": ident}


def _topic(title, activity_type, module="Week 1", ident=501):
    return {
        "kind": "TOPIC", "title": title, "module": module, "id": ident,
        "activity_type": activity_type,
    }


def test_already_linked_activity_is_not_scheduled():
    result = _result("ASSIGN", "Essay & Reflection")
    plan = resolve_existing_activity_links(
        [result], [_module(), _topic("Essay &amp; Reflection", 3)],
        [{"Name": "Essay & Reflection", "FolderId": 10}], [],
    )

    assert plan == []
    assert result["status"] == "activity_linked"


def test_one_exact_activity_is_planned_for_its_exact_unit():
    result = _result("QUIZ", "Knowledge Check")
    plan = resolve_existing_activity_links(
        [result], [_module()], [], [{"Name": "Knowledge Check", "QuizId": 22}],
    )

    assert plan == [result]
    assert result["status"] == "existing_activity_link_missing"
    assert result["activity_id"] == 22
    assert result["target_module_id"] == 101


def test_no_exact_activity_remains_missing_and_is_not_scheduled():
    result = _result("ASSIGN", "Essay")
    plan = resolve_existing_activity_links(
        [result], [_module()], [{"Name": "Essay draft", "FolderId": 10}], [],
    )

    assert plan == []
    assert result["status"] == "missing"


def test_duplicate_exact_activities_are_ambiguous_and_not_scheduled():
    result = _result("QUIZ", "Knowledge Check")
    plan = resolve_existing_activity_links(
        [result], [_module()], [],
        [{"Name": "Knowledge Check", "QuizId": 1}, {"Name": "Knowledge Check", "QuizId": 2}],
    )

    assert plan == []
    assert result["status"] == "ambiguous_activity"
    assert result["activity_match_count"] == 2


def test_assignment_and_quiz_with_same_title_do_not_collide():
    assignment = _result("ASSIGN", "Final")
    quiz = _result("QUIZ", "Final")
    plan = resolve_existing_activity_links(
        [assignment, quiz], [_module()],
        [{"Name": "Final", "FolderId": 7}], [{"Name": "Final", "QuizId": 8}],
    )

    assert {item["activity_id"] for item in plan} == {7, 8}
    assert {item["activity_kind"] for item in plan} == {"ASSIGN", "QUIZ"}


def test_same_title_content_topic_of_the_other_activity_type_is_not_linked():
    result = _result("ASSIGN", "Final")
    plan = resolve_existing_activity_links(
        [result], [_module(), _topic("Final", 4)],
        [{"Name": "Final", "FolderId": 7}], [],
    )

    assert plan == [result]
    assert result["status"] == "existing_activity_link_missing"


def test_repeated_run_sees_the_new_typed_content_link_and_schedules_nothing():
    result = _result("ASSIGN", "Essay")
    plan = resolve_existing_activity_links(
        [result], [_module(), _topic("Essay", 3)],
        [{"Name": "Essay", "FolderId": 7}], [],
    )

    assert plan == []
    assert result["status"] == "activity_linked"
