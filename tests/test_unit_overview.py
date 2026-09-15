import copy
import sys

import pytest

sys.path.insert(0, "src")

from content_preservation import add_generated_heading
from unit_overview import (
    extract_description_html,
    has_meaningful_unit_content,
    move_unit_content_to_overview,
    overview_title,
)
from youtube_embed import transform_standalone_youtube_urls


def _module(source_html="<p>Welcome to the unit.</p>", *, hidden=False, title="Unit One"):
    return {
        "Id": 7,
        "Title": title,
        "ShortTitle": "",
        "Type": 0,
        "ModuleStartDate": None,
        "ModuleEndDate": None,
        "ModuleDueDate": None,
        "IsHidden": hidden,
        "IsLocked": False,
        "Description": {"Html": source_html, "Text": "Welcome to the unit."},
        "ParentModuleId": None,
    }


class FakeContentAPI:
    course_id = "42"
    module_id = "7"

    def __init__(self, module=None):
        self.module = copy.deepcopy(module or _module())
        self.structure = [
            {
                "Id": 10,
                "Title": "Existing child",
                "ShortTitle": "",
                "Type": 1,
                "TopicType": 1,
                "Url": "existing.html",
                "ParentModuleId": 7,
                "IsHidden": False,
                "IsLocked": False,
            }
        ]
        self.html = {10: "<p>Existing.</p>"}
        self.created_ids = []
        self.deleted_ids = []
        self.next_id = 100
        self.fail_get_module = False
        self.fail_create = False
        self.corrupt_topic_readback = False
        self.fail_delete = False
        self.fail_clear = False

    async def get_module(self):
        if self.fail_get_module:
            raise RuntimeError("module read unavailable")
        return copy.deepcopy(self.module)

    async def list_structure(self):
        return copy.deepcopy(self.structure)

    async def create_html_topic(self, title):
        if self.fail_create:
            raise RuntimeError("create denied")
        topic = {
            "Id": self.next_id,
            "Title": title,
            "ShortTitle": "",
            "Type": 1,
            "TopicType": 1,
            "Url": f"overview-{self.next_id}.html",
            "ParentModuleId": 7,
            "IsHidden": True,
            "IsLocked": False,
            "StartDate": None,
            "EndDate": None,
            "DueDate": None,
            "OpenAsExternalResource": None,
            "Description": None,
        }
        self.next_id += 1
        self.structure.append(topic)
        self.html[topic["Id"]] = "<p></p>"
        self.created_ids.append(topic["Id"])
        return copy.deepcopy(topic)

    async def get_topic(self, topic_id):
        return copy.deepcopy(next(item for item in self.structure if item["Id"] == topic_id))

    async def get_topic_html(self, topic_id):
        value = self.html[topic_id]
        if self.corrupt_topic_readback and topic_id in self.created_ids and "Welcome" in value:
            return value.replace("Welcome", "Changed")
        return value

    async def replace_topic_html(self, topic_id, source_html):
        self.html[topic_id] = source_html

    async def set_topic_hidden(self, topic_id, is_hidden):
        topic = next(item for item in self.structure if item["Id"] == topic_id)
        topic["IsHidden"] = bool(is_hidden)

    async def move_topic_first(self, topic_id):
        topic = next(item for item in self.structure if item["Id"] == topic_id)
        self.structure.remove(topic)
        self.structure.insert(0, topic)

    async def replace_module_description(self, original_module, source_html):
        self.module["Title"] = original_module["Title"]
        self.module["Description"] = {"Html": source_html, "Text": ""}
        if self.fail_clear and not source_html:
            raise RuntimeError("clear response lost")

    async def delete_topic(self, topic_id):
        if self.fail_delete:
            raise RuntimeError("delete denied")
        self.structure = [item for item in self.structure if item["Id"] != topic_id]
        self.html.pop(topic_id, None)
        self.deleted_ids.append(topic_id)


async def _style(source_html):
    return f'<main class="theme">{source_html}</main>', {"input_tokens": 2, "output_tokens": 3, "cost_cad": 0.1}


def _logs():
    entries = []
    return entries, lambda message, level="info": entries.append((message, level))


def _expected_source(module):
    title = overview_title(module["Title"])
    converted = transform_standalone_youtube_urls(extract_description_html(module)).html
    return add_generated_heading(title, converted)


def test_units_with_no_description_content_are_skipped():
    assert not has_meaningful_unit_content("")
    assert not has_meaningful_unit_content("<p>&nbsp;</p><div><br></div>")


@pytest.mark.parametrize(
    "source",
    [
        "<p>Text only.</p>",
        '<p><img src="/image.png" alt="Diagram"></p>',
        '<p><a href="/handout.pdf">Handout</a></p>',
        "<ul><li>One</li><li>Two</li></ul>",
        "<table><tr><td>Cell</td></tr></table>",
        '<p>Mixed.</p><video controls><source src="/movie.mp4"></video>',
    ],
)
def test_text_images_links_lists_tables_and_mixed_media_are_meaningful(source):
    assert has_meaningful_unit_content(source)


@pytest.mark.asyncio
async def test_no_description_does_not_create_a_page():
    api = FakeContentAPI(_module("<p> </p>"))
    logs, log = _logs()
    result = await move_unit_content_to_overview(api, _style, log)
    assert result.ok and result.status == "no-content"
    assert not api.created_ids
    assert len(api.structure) == 1


@pytest.mark.asyncio
async def test_text_only_rich_text_fallback_is_preserved():
    module = _module("")
    module["Description"] = {"Text": "Use <care> & caution."}
    assert extract_description_html(module) == "<p>Use &lt;care&gt; &amp; caution.</p>"
    api = FakeContentAPI(module)
    result = await move_unit_content_to_overview(api, _style, _logs()[1])
    assert result.ok
    assert "Use &lt;care&gt; &amp; caution." in api.html[result.topic_id]


@pytest.mark.asyncio
async def test_page_name_first_position_and_visible_parent_are_verified():
    api = FakeContentAPI()
    result = await move_unit_content_to_overview(api, _style, _logs()[1], "https://learn.test")
    assert result.ok and result.status == "moved"
    assert result.topic_url == "https://learn.test/d2l/le/lessons/42/topics/100"
    assert api.structure[0]["Title"] == "Unit One — Overview"
    assert api.structure[0]["IsHidden"] is False
    assert api.module["Title"] == "Unit One"
    assert not has_meaningful_unit_content(extract_description_html(api.module))


@pytest.mark.asyncio
async def test_hidden_parent_produces_hidden_child():
    api = FakeContentAPI(_module(hidden=True))
    result = await move_unit_content_to_overview(api, _style, _logs()[1])
    assert result.ok
    assert api.structure[0]["IsHidden"] is True


@pytest.mark.asyncio
async def test_existing_verified_overview_is_resumed_not_duplicated():
    module = _module()
    api = FakeContentAPI(module)
    title = overview_title(module["Title"])
    api.structure.insert(0, {
        "Id": 55, "Title": title, "ShortTitle": "", "Type": 1,
        "TopicType": 1, "Url": "overview.html", "ParentModuleId": 7,
        "IsHidden": True, "IsLocked": False, "StartDate": None,
        "EndDate": None, "DueDate": None, "OpenAsExternalResource": None,
        "Description": None,
    })
    api.html[55] = f'<main class="already-themed">{_expected_source(module)}</main>'
    result = await move_unit_content_to_overview(api, _style, _logs()[1])
    assert result.ok and result.status == "existing-resumed"
    assert not api.created_ids
    assert api.structure[0]["Id"] == 55
    assert not has_meaningful_unit_content(extract_description_html(api.module))


@pytest.mark.asyncio
async def test_existing_unverifiable_overview_blocks_transfer():
    api = FakeContentAPI()
    api.structure.insert(0, {
        "Id": 55, "Title": "Unit One — Overview", "Type": 1,
        "TopicType": 1, "Url": "overview.html", "ParentModuleId": 7,
        "IsHidden": True,
    })
    api.html[55] = "<p>Different authored content.</p>"
    result = await move_unit_content_to_overview(api, _style, _logs()[1])
    assert not result.ok and result.status == "conflict"
    assert not api.created_ids
    assert has_meaningful_unit_content(extract_description_html(api.module))


@pytest.mark.asyncio
async def test_rerun_after_success_creates_no_duplicate():
    api = FakeContentAPI()
    first = await move_unit_content_to_overview(api, _style, _logs()[1])
    second = await move_unit_content_to_overview(api, _style, _logs()[1])
    assert first.ok and second.ok
    assert second.status == "no-content"
    assert api.created_ids == [100]


@pytest.mark.asyncio
async def test_failure_before_page_creation_is_reported_without_mutation():
    api = FakeContentAPI()
    api.fail_get_module = True
    result = await move_unit_content_to_overview(api, _style, _logs()[1])
    assert not result.ok
    assert "before page creation" in result.reason
    assert not api.created_ids


@pytest.mark.asyncio
async def test_failure_after_creation_rolls_back_and_keeps_unit_description():
    api = FakeContentAPI()

    async def failed_style(_source):
        return None, None

    result = await move_unit_content_to_overview(api, failed_style, _logs()[1])
    assert not result.ok
    assert api.created_ids == [100]
    assert api.deleted_ids == [100]
    assert all(item["Id"] != 100 for item in api.structure)
    assert has_meaningful_unit_content(extract_description_html(api.module))


@pytest.mark.asyncio
async def test_readback_validation_failure_rolls_back_before_clearing_unit():
    api = FakeContentAPI()
    api.corrupt_topic_readback = True
    result = await move_unit_content_to_overview(api, _style, _logs()[1])
    assert not result.ok
    assert "read-back validation failed" in result.reason
    assert api.deleted_ids == [100]
    assert has_meaningful_unit_content(extract_description_html(api.module))


@pytest.mark.asyncio
async def test_failed_delete_leaves_exact_created_page_hidden():
    api = FakeContentAPI()
    api.fail_delete = True

    async def failed_style(_source):
        return None, None

    result = await move_unit_content_to_overview(api, failed_style, _logs()[1])
    assert not result.ok
    topic = next(item for item in api.structure if item["Id"] == 100)
    assert topic["IsHidden"] is True
    assert "left hidden" in result.reason


@pytest.mark.asyncio
async def test_ambiguous_clear_failure_restores_original_then_removes_child():
    api = FakeContentAPI()
    api.fail_clear = True
    original = extract_description_html(api.module)
    result = await move_unit_content_to_overview(api, _style, _logs()[1])
    assert not result.ok
    assert extract_description_html(api.module) == original
    assert api.deleted_ids == [100]
