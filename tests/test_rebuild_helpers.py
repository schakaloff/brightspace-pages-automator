from rebuild_helpers import (
    download_filename,
    exact_module_map,
    extract_pluginfile_url,
    valid_activity_url,
    valid_section_name,
    validate_download_response,
)


def test_activity_url_rejects_course_page_fragment():
    course = "https://moodle.example/course/view.php?id=7"
    assert not valid_activity_url(course + "#", course)
    assert not valid_activity_url("#", course)


def test_activity_url_accepts_real_moodle_activity():
    course = "https://moodle.example/course/view.php?id=7"
    assert valid_activity_url("https://moodle.example/mod/resource/view.php?id=99", course)


def test_blank_and_placeholder_sections_are_never_valid_write_targets():
    assert not valid_section_name("")
    assert not valid_section_name("  ")
    assert not valid_section_name("(unnamed section)")
    assert valid_section_name("Course Overview")


def test_exact_module_map_rejects_duplicate_normalized_titles():
    modules = exact_module_map([
        {"kind": "MODULE", "id": 1, "title": "Unit One"},
        {"kind": "MODULE", "id": 2, "title": " unit one "},
        {"kind": "MODULE", "id": 3, "title": "Unit Two"},
    ])
    assert "unit one" not in modules
    assert modules["unit two"]["id"] == 3


def test_extract_pluginfile_url_handles_relative_authenticated_link():
    markup = '<a href="/pluginfile.php/10/mod_resource/content/1/file.pdf">Open</a>'
    assert extract_pluginfile_url(markup, "https://moodle.example/mod/resource/view.php?id=7") == (
        "https://moodle.example/pluginfile.php/10/mod_resource/content/1/file.pdf"
    )


def test_download_filename_prefers_utf8_content_disposition():
    name = download_filename(
        {"content-disposition": "attachment; filename*=UTF-8''Week%201%20Notes.pdf"},
        "https://moodle.example/pluginfile.php/1/file",
        "Fallback",
    )
    assert name == "Week 1 Notes.pdf"


def test_download_filename_uses_url_suffix_with_moodle_title():
    name = download_filename({}, "https://moodle.example/files/slides.pptx", "Week 1 Slides")
    assert name == "slides.pptx"


def test_valid_download_accepts_inline_pdf():
    assert validate_download_response(200, "application/pdf", "https://moodle.example/file.pdf", b"%PDF-1.7") == (True, "")


def test_download_rejects_login_and_error_html():
    ok, reason = validate_download_response(
        200, "text/html; charset=utf-8", "https://moodle.example/login/index.php", b"<html>login</html>"
    )
    assert not ok
    assert "login" in reason


def test_download_rejects_html_even_when_mislabeled():
    ok, reason = validate_download_response(
        200, "application/octet-stream", "https://moodle.example/file", b"<!doctype html><title>Error</title>"
    )
    assert not ok
    assert "HTML" in reason


def test_download_rejects_empty_or_failed_responses():
    assert not validate_download_response(404, "application/pdf", "https://moodle.example/x", b"missing")[0]
    assert not validate_download_response(200, "application/pdf", "https://moodle.example/x", b"")[0]


def test_pluginfile_extraction_ignores_theme_logo():
    from rebuild_helpers import extract_pluginfile_url
    markup = (
        '<img src="https://mymoodle.okanagan.bc.ca/pluginfile.php/1/core_admin/logo/0x200/1/2023%20OC%20Moodle.png">'
        '<div class="resourceworkaround"><a href="https://mymoodle.okanagan.bc.ca/pluginfile.php/9/mod_resource/content/1/a.pdf">a</a></div>'
    )
    assert extract_pluginfile_url(markup, "https://mymoodle.okanagan.bc.ca/").endswith("/mod_resource/content/1/a.pdf")
    assert extract_pluginfile_url(markup.split("<div")[0], "https://mymoodle.okanagan.bc.ca/") is None


def test_moodle_url_activity_resolves_to_external_target():
    from rebuild_helpers import extract_url_workaround, is_moodle_url, with_moodle_redirect
    href = "https://mymoodle.okanagan.bc.ca/mod/url/view.php?id=3200060"
    assert with_moodle_redirect(href) == href + "&redirect=1"
    assert with_moodle_redirect("https://youtu.be/x") == "https://youtu.be/x"
    assert is_moodle_url(href) and not is_moodle_url("https://youtu.be/_qzHA9rmVlo")
    page = '<div class="urlworkaround">Click <a href="https://youtu.be/_qzHA9rmVlo">link</a></div>'
    assert extract_url_workaround(page, href) == "https://youtu.be/_qzHA9rmVlo"


def test_plan_unit_order_follows_moodle_and_is_idempotent():
    from rebuild_helpers import plan_unit_order
    moodle = [
        {"type": "LABEL", "name": "Week 1"},
        {"type": "FILE", "name": "Case Study 1"},
        {"type": "URL", "name": "Axio360"},
        {"type": "FILE", "name": "Not in Brightspace"},
    ]
    children = [
        {"Id": 1, "Type": 1, "Title": "Case Studies — Overview"},
        {"Id": 2, "Type": 1, "Title": "Axio360"},
        {"Id": 3, "Type": 1, "Title": "Case Study 1"},
    ]
    assert plan_unit_order(moodle, children) == [3, 2]
    reordered = [children[0], children[2], children[1]]
    assert plan_unit_order(moodle, reordered) == []


def test_plan_unit_order_skips_duplicate_titles():
    from rebuild_helpers import plan_unit_order
    moodle = [{"type": "FILE", "name": "B"}, {"type": "FILE", "name": "A"}]
    children = [
        {"Id": 1, "Type": 1, "Title": "A"},
        {"Id": 2, "Type": 1, "Title": "B"},
        {"Id": 3, "Type": 1, "Title": "B"},
    ]
    assert plan_unit_order(moodle, children) == []
