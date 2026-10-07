"""Resolve explicit page selections before any Restyle writes."""


def selected_pages(pages: list[dict], indices: list[int]) -> list[dict]:
    """Keep course order; reject stale, duplicate or invalid picker results."""
    if not isinstance(indices, list):
        raise ValueError("Choose pages using the page checklist.")
    if any(type(index) is not int or not 0 <= index < len(pages) for index in indices):
        raise ValueError("The page selection is invalid. Scan the section again.")
    if len(set(indices)) != len(indices):
        raise ValueError("The page selection contains duplicates.")
    chosen = set(indices)
    return [page for index, page in enumerate(pages) if index in chosen]
