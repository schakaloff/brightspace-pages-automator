import re

from PySide6.QtWidgets import QFrame, QLabel, QWidget, QHBoxLayout, QVBoxLayout, QPushButton
from PySide6.QtCore import Qt


_ERROR_PATTERNS = [
    (r"Timeout \d+ms exceeded|TimeoutError",
     "The website took too long to respond. Check your connection and try again."),
    (r"NoneType.*has no attribute|Locator not found|waiting for .* to be visible",
     "Couldn't find an expected button or field on the page — Brightspace may have "
     "changed, or the page didn't finish loading. Try again."),
    (r"net::ERR_|ConnectionError|ClientConnectorError|Name or service not known",
     "Couldn't reach the server — check your internet connection."),
]


def friendly_error(e: Exception) -> tuple[str, str]:
    """Return (plain_message, raw_detail). raw_detail is the full str(e)."""
    raw = str(e).strip()
    first_line = raw.splitlines()[0] if raw else "Something went wrong."
    for pattern, friendly in _ERROR_PATTERNS:
        if re.search(pattern, raw, re.IGNORECASE):
            return friendly, raw
    return first_line, raw


def _divider() -> QFrame:
    line = QFrame()
    line.setProperty("role", "divider")
    return line


def _form_label(text: str) -> QLabel:
    """Return an upper-case form-field label with the 'form-label' role."""
    lbl = QLabel(text)
    lbl.setProperty("role", "form-label")
    return lbl


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "header")
    return lbl


PAGE_THEMES = {
    "lake":     dict(primary="#005F63", mid="#2ECDDC", accent="#FF8204", circle="#005F63"),
    "sky":      dict(primary="#2ECDDC", mid="#6EDFE8", accent="#005F63", circle="#2ECDDC"),
    "sunset":   dict(primary="#FF8204", mid="#FFA340", accent="#005F63", circle="#FF8204"),
    "peach":    dict(primary="#DE4F3D", mid="#E87A68", accent="#FF8204", circle="#DE4F3D"),
    "cherry":   dict(primary="#E10040", mid="#FF3366", accent="#FF8204", circle="#E10040"),
    "cabernet": dict(primary="#782434", mid="#A03C54", accent="#DE4F3D", circle="#782434"),
    "lavender": dict(primary="#50037F", mid="#8B3FC0", accent="#2ECDDC", circle="#50037F"),
    "lilac":    dict(primary="#9B5CB8", mid="#CA9CE4", accent="#2ECDDC", circle="#CA9CE4"),
    "charcoal": dict(primary="#50534C", mid="#7A7D74", accent="#2ECDDC", circle="#50534C"),
}


def _build_theme_swatches(parent_layout: QVBoxLayout) -> tuple[dict, list]:
    """Build circular colour-swatch buttons for each PAGE_THEMES entry.

    Adds the swatch row to *parent_layout* and returns
    ``(frames_dict keyed by theme name, [selected_name_ref])``
    where ``selected_name_ref`` is a 1-element list containing the
    currently selected theme name (mutable so callers always see the
    latest selection).
    """
    selected: list[str] = ["lake"]
    frames: dict[str, QPushButton] = {}

    row_widget = QWidget()
    row = QHBoxLayout(row_widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)

    def _select(name: str) -> None:
        old = selected[0]
        if old in frames:
            frames[old].setStyleSheet(
                f"background:{PAGE_THEMES[old]['circle']};border-radius:13px;"
                f"border:2px solid transparent;"
            )
        selected[0] = name
        frames[name].setStyleSheet(
            f"background:{PAGE_THEMES[name]['circle']};border-radius:13px;"
            f"border:2px solid #ffffff;"
        )

    for name, theme in PAGE_THEMES.items():
        swatch = QPushButton()
        swatch.setFixedSize(26, 26)
        swatch.setStyleSheet(
            f"background:{theme['circle']};border-radius:13px;border:2px solid transparent;"
        )
        swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        swatch.clicked.connect(lambda _=False, n=name: _select(n))
        frames[name] = swatch
        row.addWidget(swatch)

    row.addStretch()
    parent_layout.addWidget(row_widget)
    _select("lake")
    return frames, selected
