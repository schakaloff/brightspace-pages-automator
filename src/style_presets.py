"""Named page-design references used by the restyling workflows."""

from pathlib import Path
import sys


STYLE_PRESETS = {
    "calm": "Calm resources (recommended)",
    "classic": "Classic cards",
}


def load_style_reference(preset: str) -> str:
    """Load the selected design from the checkout or installed app bundle."""
    filename = "style_reference_classic.html" if preset == "classic" else "style_reference.html"
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent.parent
    return (root / "templates" / filename).read_text(encoding="utf-8")
