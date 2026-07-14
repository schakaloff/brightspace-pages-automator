import sys
sys.path.insert(0, "src")

from multi_unit_selector import select_next_unit, _flatten_modules


def _mod(module_id, title, sort_order, topic_titles):
    return {
        "module_id": module_id,
        "title": title,
        "sort_order": sort_order,
        "topic_count": len(topic_titles),
        "topic_titles": topic_titles,
    }


def test_select_next_unit_skips_already_combined():
    modules = [
        _mod(1, "Imported Module", -50, ["Meet Your Facilitators", "Imported Module — Combined"]),
        _mod(2, "Topic 1", 4, ["Welcome to Module 1"]),
    ]
    result = select_next_unit(modules)
    assert result["module_id"] == 2


def test_select_next_unit_skips_empty_modules():
    modules = [
        _mod(1, "Topic 6", 326, []),
        _mod(2, "Topic 1", 4, ["Welcome to Module 1"]),
    ]
    result = select_next_unit(modules)
    assert result["module_id"] == 2


def test_select_next_unit_orders_by_sort_order_not_list_order():
    modules = [
        _mod(2, "Topic 2", 58, ["Watch This"]),
        _mod(1, "Topic 1", 4, ["Welcome to Module 1"]),
    ]
    result = select_next_unit(modules)
    assert result["module_id"] == 1


def test_select_next_unit_returns_none_when_all_done_or_empty():
    modules = [
        _mod(1, "Imported Module", -50, ["Imported Module — Combined"]),
        _mod(2, "Topic 6", 326, []),
    ]
    assert select_next_unit(modules) is None


def test_select_next_unit_empty_list():
    assert select_next_unit([]) is None
