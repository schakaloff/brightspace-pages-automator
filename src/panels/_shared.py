import re

from PySide6.QtWidgets import QFrame, QLabel, QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QComboBox, QToolButton
from PySide6.QtCore import Qt, QSize, QPoint, QTimer
from PySide6.QtGui import QIcon

import gui_styles
from gui_tool_icons import tool_pixmap


BROWSER_GUIDE_LABEL = 'Show browser cursor and live messages'
BROWSER_GUIDE_HINT = ('Watch real browser actions with a smooth cursor and short status messages. '
                      'Your own mouse stays free. Change this before starting a transfer.')


class TechnicalDetailsToggle(QToolButton):
    """One name and disclosure treatment for troubleshooting logs."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText('Technical Details')
        self.setAccessibleName('Technical Details')
        self.setProperty('variant', 'ghost')
        self.setCheckable(True)
        self.setMinimumHeight(32)
        self.setIconSize(QSize(18, 18))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggled.connect(self._refresh_state)
        self._refresh_state(False)

    def _refresh_state(self, shown):
        self.setToolTip('Hide technical details' if shown else 'Show technical details')
        self.setAccessibleDescription('Expanded' if shown else 'Collapsed')
        self.refresh_theme()

    def refresh_theme(self):
        self.setIcon(QIcon(tool_pixmap('chevron-down' if self.isChecked() else 'chevron-right',
            18, gui_styles.modern_colors()['muted'])))

    def reveal_log(self, log, scroll, shown):
        log.setVisible(shown)
        if shown:
            # Wait for wrapped text and the newly visible log to update the
            # scroll range. The context cancels this if the panel is closed.
            def reveal():
                if not self.isChecked() or not log.isVisible():
                    return
                # QTextEdit exposes a cursor rectangle; ensureWidgetVisible
                # can reveal just that cursor and leave most of the log hidden.
                top = log.mapTo(scroll.widget(), QPoint(0, 0)).y()
                height = min(log.height(), scroll.viewport().height())
                scroll.ensureVisible(0, top + height // 2, 0, height // 2)
            QTimer.singleShot(0, scroll, reveal)


class NoScrollComboBox(QComboBox):
    """QComboBox that ignores mouse-wheel scrolling while its popup is closed,
    so scrolling a parent panel over a dropdown doesn't silently change its
    selected value. Scrolling still works normally to move through options
    once the dropdown is open."""

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


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
    line.setFrameShape(QFrame.Shape.HLine)
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
        # Ring color from the active app theme so it's visible on light and dark.
        ring = gui_styles.current["TEXT_PRI"]
        frames[name].setStyleSheet(
            f"background:{PAGE_THEMES[name]['circle']};border-radius:13px;"
            f"border:3px solid {ring};"
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
