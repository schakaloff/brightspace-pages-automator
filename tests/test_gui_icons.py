import sys
sys.path.insert(0, "src")
import pytest

@pytest.fixture(scope="session")
def qapp(qapp):
    return qapp

ALL_ICONS = ["checker", "collect", "restyle", "settings", "run", "next", "done", "locked", "running"]

def test_all_icons_return_qicon(qapp):
    from gui_icons import make_icon
    from PySide6.QtGui import QIcon
    for name in ALL_ICONS:
        icon = make_icon(name, "#ffffff")
        assert isinstance(icon, QIcon), f"make_icon({name!r}) did not return QIcon"
        assert not icon.isNull(), f"make_icon({name!r}) returned null QIcon"

def test_make_pixmap_returns_correct_size(qapp):
    from gui_icons import make_pixmap
    px = make_pixmap("run", "#ff0000", size=24)
    assert px.width() == 24
    assert px.height() == 24

def test_unknown_icon_raises(qapp):
    from gui_icons import make_icon
    with pytest.raises(KeyError):
        make_icon("nonexistent", "#fff")
