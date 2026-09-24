from icon_shortcodes import replace_fontawesome_shortcodes


def test_hand_pointer_becomes_accessible_right_arrow():
    result = replace_fontawesome_shortcodes(
        "<p>[fa-hand-point-right] Factors Affecting Child Development</p>"
    )

    assert "[fa-hand-point-right]" not in result
    assert "→" in result
    assert 'data-bpa-icon="hand-point-right"' in result
    assert 'aria-label="Next"' in result


def test_multiple_known_icons_are_replaced_but_unknown_and_code_are_preserved():
    result = replace_fontawesome_shortcodes(
        "<p>[fa-info-circle] Read [fa-file-text]</p>"
        "<code>[fa-check]</code><p>[fa-made-up]</p>"
    )

    assert "ℹ" in result and "📄" in result
    assert "<code>[fa-check]</code>" in result
    assert "[fa-made-up]" in result


def test_html_without_shortcodes_is_returned_verbatim():
    source = '<p class="intro">No icon here &amp; no rewrite.</p>'
    assert replace_fontawesome_shortcodes(source) == source
