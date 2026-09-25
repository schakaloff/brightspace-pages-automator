import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from book_migration import (
    BookChapter, asset_filename, chapter_asset_urls, parse_book_index,
    parse_chapter, render_chapter, rewrite_chapter_links,
)


BOOK = "https://mymoodle.okanagan.bc.ca/mod/book/view.php?id=3758342"
ASSET = "https://mymoodle.okanagan.bc.ca/pluginfile.php/12/mod_book/chapter/5/diagram%20one.jpg"


def test_book_index_and_chapter_body_keep_order_and_media():
    markup = f"""
      <div class="book_toc"><a href="{BOOK}&chapterid=9">2. Chapter Two</a></div>
      <div id="mod_book-chapter" class="book_content"><h3>1. First Chapter</h3>
        <div class="no-overflow"><p>Read this.</p><img src="{ASSET}">
        <a href="/mod/assign/view.php?id=3">Assignment</a></div></div>"""
    assert parse_book_index(markup, BOOK) == [
        ("First Chapter", BOOK), ("Chapter Two", f"{BOOK}&chapterid=9")]
    chapter = parse_chapter(markup, BOOK, "First Chapter")
    assert chapter.chapter_id == "first"
    assert ASSET in chapter_asset_urls(chapter)
    assert 'href="https://mymoodle.okanagan.bc.ca/mod/assign/view.php?id=3"' in chapter.body_html


def test_book_rewrite_replaces_files_and_activity_without_moodle_links():
    body = (f'<img src="{ASSET}">'
            '<a href="https://mymoodle.okanagan.bc.ca/mod/assign/view.php?id=3">Assignment</a>')
    chapter = BookChapter("First", BOOK, "first", body)
    rewritten, unresolved = rewrite_chapter_links(
        chapter, {ASSET: "/content/enforced/10318-Course/Casting/diagram.jpg"},
        {"assignment": "/d2l/le/lessons/10318/topics/44"}, {})
    assert not unresolved
    assert "mymoodle" not in rewritten
    assert "/content/enforced/10318-Course/Casting/diagram.jpg" in rewritten
    assert "/d2l/le/lessons/10318/topics/44" in rewritten


def test_unmatched_moodle_link_is_reported_and_duplicate_name_is_stable():
    chapter = BookChapter("First", BOOK, "first", '<a href="https://mymoodle.okanagan.bc.ca/mod/page/view.php?id=7">Other</a>')
    _, unresolved = rewrite_chapter_links(chapter, {}, {}, {})
    assert unresolved == ["https://mymoodle.okanagan.bc.ca/mod/page/view.php?id=7"]
    assert asset_filename(ASSET, {"diagram one.jpg"}).startswith("diagram one-")
    assert asset_filename(ASSET, {"diagram one.jpg"}) == asset_filename(ASSET, {"diagram one.jpg"})


def test_rendered_chapter_has_one_layout_and_only_its_own_content():
    chapter = BookChapter("First", BOOK, "first", "<p>Only this chapter</p>")
    rendered = render_chapter("Casting Techniques", chapter, chapter.body_html,
                              "<style>.main-container{color:#782434}</style>")
    soup = BeautifulSoup(rendered, "lxml")
    assert len(soup.select(".main-container")) == 1
    assert soup.select_one(".section-header").get_text() == "First"
    assert "Only this chapter" in rendered
    assert "#782434" in rendered
