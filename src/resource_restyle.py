"""Remove obsolete app-generated resource markers before and after Restyle.

Only markers inside the app's resource rows are affected. A professor's normal
text, including the word "PDF" elsewhere on a page, remains untouched.
"""

from bs4 import BeautifulSoup


_OLD_BADGE_TEXT = {"PAGE", "LINK", "PDF", "XLS", "XLSX", "DOC", "DOCX", "PPT", "PPTX", "ZIP", "FILE"}


def remove_generated_resource_markers(html: str) -> tuple[str, int]:
    """Return HTML without old resource badges/decorative icons and a removal count."""
    if "resource-badge" not in html and "resource-icon" not in html:
        return html, 0

    soup = BeautifulSoup(html, "html.parser")
    removed = 0
    for row in soup.select(".link-row, .resource-row"):
        for badge in row.select("span.resource-badge"):
            if badge.get_text(" ", strip=True).upper() in _OLD_BADGE_TEXT:
                badge.decompose()
                removed += 1
        for icon in row.select("span.resource-icon"):
            # Only remove known decorative Bootstrap icons. Authored text or
            # images inside an icon-shaped span are not safe to discard.
            children = icon.find_all(True)
            if not icon.get_text(strip=True) and children and all(
                child.name == "i" and "bi" in child.get("class", []) for child in children
            ):
                icon.decompose()
                removed += 1

    return (str(soup), removed) if removed else (html, 0)


def mark_resource_directory(html: str) -> tuple[str, bool]:
    """Use compact spacing only for a page that is mainly a resource list."""
    if "main-container" not in html:
        return html, False

    soup = BeautifulSoup(html, "html.parser")
    outer = soup.select_one(".main-container")
    content = outer.select_one(".content-body") if outer else None
    if content is None:
        return html, False

    inspection = BeautifulSoup(str(content), "html.parser")
    rows = inspection.select(".link-row, .resource-row")
    for row in rows:
        row.decompose()
    for heading in inspection.select("h1, h2, h3, h4, h5, h6, .sr-only"):
        heading.decompose()
    has_other_media = bool(inspection.select("img, iframe, video, table"))
    other_text = " ".join(inspection.stripped_strings)
    is_directory = bool(rows) and not has_other_media and len(other_text) <= 120

    classes = outer.get("class", [])
    if is_directory and "resource-directory" not in classes:
        outer["class"] = [*classes, "resource-directory"]
        return str(soup), True
    if not is_directory and "resource-directory" in classes:
        outer["class"] = [name for name in classes if name != "resource-directory"]
        return str(soup), False
    return html, is_directory
