import sys

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, "src")

from content_preservation import content_is_preserved
from dividers import EDITABLE_HR_STYLE, make_dividers_easy_to_select


def test_brightspace_divider_gets_the_wide_click_area():
    # What the editor's own Divider inserts.
    html = "<p>Above</p>\n<hr>\n<p>Below</p>"
    assert make_dividers_easy_to_select(html) == (
        f'<p>Above</p>\n<hr style="{EDITABLE_HR_STYLE}">\n<p>Below</p>'
    )


def test_existing_style_is_replaced_and_other_attributes_kept():
    html = '<HR class="section-rule" style="border-top: 1px solid red"/><h3>Next</h3>'
    result = make_dividers_easy_to_select(html)
    assert result == f'<hr class="section-rule" style="{EDITABLE_HR_STYLE}"><h3>Next</h3>'
    assert "red" not in result


def test_running_twice_does_not_stack_styles():
    once = make_dividers_easy_to_select("<hr/><hr />")
    assert make_dividers_easy_to_select(once) == once
    assert once.count("style=") == 2


def test_pages_without_dividers_are_untouched():
    html = "<p>Hrm, <strong>hrs</strong> of work</p>"
    assert make_dividers_easy_to_select(html) is html


def test_widened_dividers_pass_the_save_check():
    html = '<h2>Overview</h2><hr class="section-rule"><p>Text</p><a href="/x.pdf">File</a>'
    assert content_is_preserved(html, make_dividers_easy_to_select(html)) == (True, "")


def test_quoted_attributes_keep_greater_than_and_style_text():
    markup = '<hr title="A > B" data-note="old style=red" style="color: red" class="rule">'
    divider = BeautifulSoup(make_dividers_easy_to_select(markup), "lxml").find("hr")
    assert divider.attrs == {
        "title": "A > B", "data-note": "old style=red", "class": ["rule"],
        "style": EDITABLE_HR_STYLE,
    }


@pytest.mark.parametrize("literal", [
    '<script>const example = "<hr>";</script>',
    '<style>.example::after { content: "<hr>"; }</style>',
    '<!-- Example: <hr> -->',
    '<textarea><hr></textarea>',
    '<iframe src="/embed"><hr></iframe>',
])
def test_divider_examples_and_embed_payloads_are_untouched(literal):
    assert make_dividers_easy_to_select(literal + "<hr>") == (
        literal + f'<hr style="{EDITABLE_HR_STYLE}">'
    )


def test_multiline_markup_outside_actual_dividers_is_preserved_exactly():
    before, after = '<!-- keep -->\r\n<p title="<hr>">Above</p>\n', '\r\n<p>Below &amp; beyond</p>'
    assert make_dividers_easy_to_select(before + "<hr />" + after) == (
        before + f'<hr style="{EDITABLE_HR_STYLE}">' + after
    )
