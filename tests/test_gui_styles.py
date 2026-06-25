import sys
sys.path.insert(0, "src")
from gui_styles import APP_STYLESHEET, BG, OC_ORANGE, OC_TEAL

def test_colors_are_hex():
    for val in (BG, OC_ORANGE, OC_TEAL):
        assert val.startswith("#"), f"expected hex, got {val!r}"
        assert len(val) == 7

def test_stylesheet_is_nonempty_string():
    assert isinstance(APP_STYLESHEET, str)
    assert len(APP_STYLESHEET) > 200
