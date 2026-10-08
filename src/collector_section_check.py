"""Keep grouped resources with their course section after AI styling."""

from bs4 import BeautifulSoup, Tag


def misplaced_section_links(markup: str, requirements: list[tuple[str, str]]) -> list[str]:
    """Return links moved outside their original heading's section."""
    if not requirements:
        return []
    soup = BeautifulSoup(markup or "", "html.parser")
    headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    missing = []
    for title, href in requirements:
        found = False
        for heading in headings:
            if heading.get_text(" ", strip=True).casefold() != title.strip().casefold():
                continue
            folder = heading.find_parent("section", class_="bpa-folder")
            if folder is not None and heading is folder.find(
                ["h1", "h2", "h3", "h4", "h5", "h6"], recursive=False
            ):
                # The folder's own heading covers its nested sections, but
                # never links moved beyond the end of the folder wrapper.
                found = any(link.get("href") == href for link in folder.find_all("a"))
                if found:
                    break
                continue
            level = int(heading.name[1])
            for node in heading.next_elements:
                if not isinstance(node, Tag):
                    continue
                if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"} and int(node.name[1]) <= level:
                    break
                if node.name == "a" and node.get("href") == href:
                    found = True
                    break
            if found:
                break
        if not found:
            missing.append(f"{title}: {href}")
    return missing
