import asyncio
import sys

import pytest

sys.path.insert(0, "src")

from content_cleanup import (
    ChangedSelectionError, ContentTopic, course_id_from_url,
    remove_topics, validate_selection,
)


def topic(id, title="Topic", module="20", unit="Unit 1"):
    return ContentTopic(str(id), title, module, unit, "2")


def test_course_url_must_be_brightspace_course():
    assert course_id_from_url(
        "https://learn.okanagancollege.ca/d2l/le/content/123/home"
    ) == "123"
    assert course_id_from_url(
        "https://learn.okanagancollege.ca/d2l/le/lessons/123/units/20"
    ) == "123"
    with pytest.raises(ValueError):
        course_id_from_url("https://example.com/d2l/le/content/123/home")


def test_changed_title_or_unit_blocks_the_whole_batch():
    selected = [topic(1), topic(2)]
    with pytest.raises(ChangedSelectionError):
        validate_selection(selected, [topic(1), topic(2, module="21")])
    with pytest.raises(ChangedSelectionError):
        validate_selection(selected, [topic(1), topic(2, title="Renamed")])


def test_remove_stops_before_any_write_if_selection_changed():
    class API:
        deleted = []

        async def list_topics(self):
            return [topic(1), topic(2, title="Renamed")]

        async def delete_topic(self, id):
            self.deleted.append(id)

        async def get_topic_identity(self, id):
            return (id, "Topic", "20")

    api = API()
    with pytest.raises(ChangedSelectionError):
        asyncio.run(remove_topics(api, [topic(1), topic(2)]))
    assert api.deleted == []


def test_remove_reports_partial_result_and_stops_on_error():
    class API:
        def __init__(self):
            self.deleted = []

        async def list_topics(self):
            return [item for item in (topic(1), topic(2), topic(3))
                    if item.id not in self.deleted]

        async def delete_topic(self, id):
            if id == "2":
                raise RuntimeError("permission denied")
            self.deleted.append(id)

        async def get_topic_identity(self, id):
            return (id, "Topic", "20")

    api = API()
    result = asyncio.run(remove_topics(api, [topic(1), topic(2), topic(3)]))
    assert api.deleted == ["1"]
    assert result.removed == (topic(1),)
    assert result.failed == topic(2)
    assert result.error == "permission denied"


def test_move_during_run_stops_before_deleting_moved_topic():
    class API:
        deleted = []

        async def list_topics(self):
            return [topic(1), topic(2)]

        async def get_topic_identity(self, id):
            return (id, "Topic", "21" if id == "2" else "20")

        async def delete_topic(self, id):
            self.deleted.append(id)

    api = API()
    result = asyncio.run(remove_topics(api, [topic(1), topic(2)]))
    assert api.deleted == ["1"]
    assert result.failed == topic(2)
