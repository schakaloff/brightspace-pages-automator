"""Named page-design references used by the restyling workflows."""

from pathlib import Path


STYLE_PRESETS = {
    "calm": "Calm resources (recommended)",
    "classic": "Classic cards",
}


def load_style_reference(preset: str) -> str:
    """Return the requested bundled reference, falling back to the default."""
    filename = "style_reference_classic.html" if preset == "classic" else "style_reference.html"
    path = Path(__file__).parent.parent / "templates" / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
