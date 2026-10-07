import sys

from bs4 import BeautifulSoup

sys.path.insert(0, "src")

from ai_styler import _keep_page_container_wide


def test_file_only_page_keeps_full_width_in_brightspace_content_view():
    styled = (
        "<style>body { display: flex; justify-content: center; }"
        ".main-container { max-width: 1000px; }</style>"
        '<div class="main-container" style="margin: 2rem auto">'
        '<h2>Files</h2><a href="/file.pptx">Session 10 ppt</a></div>'
    )

    result = _keep_page_container_wide(styled)
    container = BeautifulSoup(result, "html.parser").select_one(".main-container")

    assert "width: 95% !important" in container["style"]
    assert "max-width: 1000px !important" in container["style"]
    assert "margin: 2rem auto" in container["style"]
    assert container.a["href"] == "/file.pptx"
    assert _keep_page_container_wide(result) == result


def test_unstyled_fragments_are_left_alone():
    fragment = "<h2>Files</h2><p>Session 10 ppt</p>"
    assert _keep_page_container_wide(fragment) == fragment
