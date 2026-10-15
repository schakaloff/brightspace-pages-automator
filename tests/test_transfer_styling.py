"""Transfer styling: strip Page Automator or all custom styling without losing content."""
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bs4 import BeautifulSoup
import transfer_styling as ts

PA_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Week 1</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">
<style>:root{--primary:#005F63}.main-container{max-width:1000px}.hero{background:var(--primary)}.action-card{padding:2rem}</style>
<style>.my-note{color:red}</style>
</head><body><div class="main-container"><header class="hero"><h1>Week 1</h1><div class="hero-decoration">OC</div></header>
<div class="content-body"><div class="action-card">
<h2>Readings</h2><p style="text-align:left"><span style="color:#ff3366">Read</span> the <a class="link-btn" style="background:var(--lake)" href="notes.pdf">notes</a>.</p>
<p><span data-bpa-icon="book" role="img" aria-label="Reading" style="display:inline-block">📘</span>Chapter 2</p>
<div class="video-wrapper"><iframe src="https://www.youtube.com/embed/abc" title="Intro video" allowfullscreen class="x"></iframe></div>
</div></div></div></body></html>"""

LEGACY_PAGE = """<html><head><link rel="stylesheet" href="/shared/theme.css"><style>.callout::before{color:red}</style>
<script src="https://learn.okanagancollege.ca/shared/D2L/Courseware_HTML_Templates/V5/latest/_assets/js/client.js"></script>
<script src="https://cdn.example.org/mathjax.js"></script></head>
<body bgcolor="#eee" style="margin:0"><!-- template note --><div class="courseware-callout"><div class="title">Key idea</div>
<div><p>First <font color="red" face="Arial">point</font>.</p><p>Second point.</p></div></div>
<table border="1" width="600" cellpadding="4" class="grid"><caption>Grades</caption><thead><tr><th scope="col" style="width:50%">Task</th><th>Weight</th></tr></thead>
<tbody><tr><td align="center" headers="t">Essay</td><td>40%</td></tr></tbody></table>
<p>Area <math display="block" class="m"><mi>π</mi><msup><mi>r</mi><mn>2</mn></msup></math> and \\(x^2\\)</p>
<img src="figure.png" alt="Bar chart of grades" style="width: 320px; float:right" class="pic">
<span class="sr-only">Opens in a new window</span>
<div data-kaltura-entry="1_abc" class="kwidget" style="height:300px"></div>
<ul class="fancy" style="list-style:square"><li id="first" lang="fr">Un</li><li aria-describedby="first">Deux</li></ul>
<center>Centred line</center></body></html>"""


def text(markup):
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup.find_all(["style", "script"]): tag.decompose()
    return "".join(soup.get_text().split())


def test_preserve_changes_nothing():
    result = ts.clean(PA_PAGE, "preserve")
    assert result.ok and result.html == PA_PAGE and not result.removed


def test_page_automator_detection():
    assert ts.is_page_automator(PA_PAGE) and not ts.is_page_automator(LEGACY_PAGE)
    assert ts.has_custom_styling(LEGACY_PAGE) and not ts.has_custom_styling("<p>Plain</p>")


def test_strip_page_automator_keeps_the_authors_own_formatting():
    result = ts.clean(PA_PAGE, "strip_bpa")
    soup = BeautifulSoup(result.html, "html.parser")
    assert result.ok and not ts.is_page_automator(result.html)
    assert not soup.find(class_=["main-container", "hero", "content-body", "action-card", "link-btn", "video-wrapper"])
    assert "Poppins" not in result.html and ".main-container" not in result.html and "data-bpa" not in result.html
    assert ".my-note{color:red}" in result.html                     # someone else's CSS stays
    assert soup.find("span", style="color:#ff3366")                   # the author's colour stays
    link = soup.find("a", href="notes.pdf")
    assert link and not link.get("class") and not link.get("style")   # Page Automator's button look is gone
    assert soup.find("h1").text == "Week 1" and "OC" not in text(result.html) and "📘" not in result.html
    frame = soup.find("iframe")
    assert frame["title"] == "Intro video" and frame.has_attr("allowfullscreen")
    assert result.removed == {"pa-icon": 1, "pa-label": 1, "pa-style": 1, "pa-font": 1, "pa-wrapper": 5, "pa-class": 1}
    assert "Page Automator style block" in result.summary


def test_strip_all_removes_presentation_but_keeps_content_and_meaning():
    result = ts.clean(LEGACY_PAGE, "strip_all")
    assert result.ok, result.reason
    soup = BeautifulSoup(result.html, "html.parser")
    assert text(result.html) == text(LEGACY_PAGE.replace("<!-- template note -->", ""))
    assert not soup.find("link") and not soup.find("font") and not soup.find("center")
    assert "Courseware_HTML_Templates" not in result.html and "mathjax.js" in result.html   # only framework scripts go
    assert not [t for t in soup.find_all(style=True)] and not soup.find(class_=["courseware-callout", "grid", "fancy", "pic", "kwidget"])
    assert soup.find("body").attrs == {}
    table = soup.find("table")
    assert table["border"] == "1" and not table.has_attr("width") and not table.has_attr("cellpadding")
    assert soup.find("caption").text == "Grades" and soup.find("th")["scope"] == "col" and soup.find("td")["headers"] == ["t"]
    assert not soup.find("td").has_attr("align")
    math = soup.find("math")
    assert math["display"] == "block" and not math.has_attr("class") and math.find("msup")
    img = soup.find("img")
    assert img["alt"] == "Bar chart of grades" and img["width"] == "320" and not img.has_attr("style")
    assert soup.find("div", attrs={"data-kaltura-entry": "1_abc"})        # embeds keep data attributes
    assert soup.find("li", id="first")["lang"] == "fr" and soup.find("li", attrs={"aria-describedby": "first"})
    sr = soup.find("span", class_="sr-only")
    assert sr and ".sr-only" in soup.find("style").text                     # screen-reader text stays hidden
    assert "<!--" not in result.html
    assert soup.find("p", string="Key idea")                                # a text-only wrapper becomes a paragraph
    for key in ("style", "stylesheet", "script", "inline", "class", "attribute", "font", "wrapper", "comment"):
        assert result.removed[key], key


def test_wrappers_never_merge_blocks_of_text():
    result = ts.clean("<div style='x'><div>Alpha</div><div>Beta</div></div><span class='a'>Gamma</span>", "strip_all")
    soup = BeautifulSoup(result.html, "html.parser")
    assert [p.text for p in soup.find_all("p")] == ["Alpha", "Beta"] and "Gamma" in result.html


def test_mixed_wrapper_with_text_and_blocks_stays_a_plain_container():
    result = ts.clean("<div class='box'>Lead text<p>Body</p></div>", "strip_all")
    assert result.ok and result.html == "<div>Lead text<p>Body</p></div>"


def test_css_generated_text_falls_back_to_the_original_page():
    page = '<style>.tip::before{content:"Tip: "}</style><p class="tip">Save often.</p>'
    result = ts.clean(page, "strip_all")
    assert not result.ok and result.html == page and "stylesheet adds visible text" in result.reason
    assert result.summary.startswith("Kept the original page")


def test_lost_content_falls_back(monkeypatch):
    def lossy(soup, removed):
        soup.find("p").decompose()
    monkeypatch.setattr(ts, "strip_all", lossy)
    page = "<p>Important</p><p>Other</p>"
    result = ts.clean(page, "strip_all")
    assert not result.ok and result.html == page


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError): ts.clean("<p>x</p>", "magic")


COURSEWARE = """<html><head><link rel="stylesheet" href="/shared/global.css"><title>Template</title></head><body>
<div class="courseware-layouts-content-wrapper"><div class="courseware-headers-hero"><h1>Template title</h1></div>
<p>Example text that should go</p><div class="courseware-callout">Example callout</div></div></body></html>"""


def test_template_takes_the_cleaned_content_and_shows_the_title_once():
    cleaned = ts.clean(PA_PAGE, "strip_all").html
    page = ts.apply_template(cleaned, COURSEWARE, "Week 1")
    soup = BeautifulSoup(page, "html.parser")
    assert soup.find("link", href="/shared/global.css") and soup.title.text == "Week 1"
    assert [h.text for h in soup.find_all("h1")] == ["Week 1"]
    assert "Example text" not in page and "Example callout" not in page
    box = soup.select_one(".courseware-layouts-content-wrapper")
    assert box.find("h2").text == "Readings" and box.find("a", href="notes.pdf")


def test_style_page_only_themes_successful_cleanups():
    page = '<style>.tip::before{content:"Tip: "}</style><p class="tip">Save often.</p>'
    result = ts.style_page(page, "strip_all", "Tips", COURSEWARE)
    assert not result.ok and not result.templated and result.html == page
    result = ts.style_page("<p>Plain</p>", "strip_bpa", "Plain", COURSEWARE)
    assert result.templated and "global.css" in result.html and "Plain</p>" in result.html


def test_builtin_themes_are_empty_shells():
    themes = ts.builtin_themes()
    assert [t["id"] for t in themes] == ["theme-calm", "theme-classic"]
    for theme in themes:
        soup = BeautifulSoup(theme["html"], "html.parser")
        assert not soup.select_one(".content-body").get_text(strip=True) and not soup.find(class_="hero-decoration")
        assert "Readings" in ts.apply_template("<h2>Readings</h2>", theme["html"], "Week 1")


def test_template_choices_list_destination_pages_with_a_look_then_themes():
    target = {"id": "2", "modules": [{"Id": 1, "Type": 0, "Title": "Shell", "children": [
        {"Id": 5, "Type": 1, "Title": "Styled", "html": COURSEWARE, "Url": "/x.html"},
        {"Id": 6, "Type": 1, "Title": "Plain", "html": "<p>no style</p>"}]}]}
    choices = ts.template_choices(target)
    assert choices[0]["label"] == "Styled (Destination)" and [c for c in choices if c["id"] == "6"] == []
    assert choices[-1]["builtin"]


def test_split_page_parts_keep_the_page_head():
    doc = ts.piece_document(PA_PAGE, "<h2>Before</h2><p>Read</p>", "Week 1: Before")
    soup = BeautifulSoup(doc, "html.parser")
    assert soup.title.text == "Week 1: Before" and ".action-card" in doc and soup.body.find("h2").text == "Before"
    assert ts.clean(doc, "strip_bpa").ok
    assert "<body><p>x</p></body>" in ts.piece_document("<p>no document</p>", "<p>x</p>", "T")


def test_describe_pluralises():
    from collections import Counter
    assert ts.describe(Counter({"class": 2, "style": 1, "inline": 3})) == "1 style block, 3 inline styles, 2 classes"
