import asyncio
import zipfile

import pytest

from h5p_handler import H5PHandler
from panels.h5p_manual_card import collect_files, parse_unit_url


def make_h5p(path, valid=True):
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("h5p.json" if valid else "other.json", "{}")
    return str(path)


def test_parse_unit_url():
    url = "https://learn.okanagancollege.ca/d2l/le/lessons/25706/units/1025133"
    assert parse_unit_url(url) == ("25706", "1025133")
    with pytest.raises(ValueError):
        parse_unit_url("https://learn.okanagancollege.ca/d2l/home/25706")


def test_collect_files_validates(tmp_path):
    good = make_h5p(tmp_path / "a.h5p")
    assert collect_files([(good, " Quiz A ")])[0]["title"] == "Quiz A"
    with pytest.raises(ValueError, match="at least one"):
        collect_files([])
    with pytest.raises(ValueError, match="h5p.json"):
        collect_files([(make_h5p(tmp_path / "bad.h5p", valid=False), "Bad")])
    with pytest.raises(ValueError, match="page title"):
        collect_files([(good, "  ")])
    with pytest.raises(ValueError, match="own title"):
        collect_files([(good, "Same"), (make_h5p(tmp_path / "b.h5p"), "same!")])


class GradesPage:
    def __init__(self, ids=None, error=False):
        self.ids, self.error = ids, error

    async def evaluate(self, js, arg):
        if self.error:
            raise RuntimeError("HTTP 403")
        return self.ids


def test_tool_grade_ids_reads_tool_linked_items_and_hides_errors():
    assert asyncio.run(H5PHandler._tool_grade_ids(GradesPage([1, 2]), 25706)) == {1, 2}
    assert asyncio.run(H5PHandler._tool_grade_ids(GradesPage(error=True), 25706)) is None
