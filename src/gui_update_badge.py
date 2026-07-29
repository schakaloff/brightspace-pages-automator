"""Always-present update button in the top-right corner of the window.

Floats above the page stack rather than living in a layout: the window has no
top bar, and adding one would mean restructuring the root layout and every
panel's spacing. An overlay is repositioned by the parent on resize and stays
put no matter which page is showing.

Never steals focus and never pops anything up on its own — the user clicks it
when they feel like it.
"""

from PySide6.QtWidgets import QToolButton
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPainter, QColor

import gui_styles
from gui_icons import make_icon

_MARGIN = 12

def theme_colors() -> tuple:
    """(ready, idle) for the active theme.

    The accent comes from gui_styles so the badge and the sidebar cannot drift
    apart — brand orange is too weak on the light palette and both need the
    same darkened substitute.
    """
    return gui_styles.accent(), gui_styles.current.get("TEXT_SEC", "#636780")


class UpdateBadge(QToolButton):
    clicked_with_update = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("update_badge")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 30)
        self.setAutoRaise(True)
        self.setIconSize(QSize(17, 17))
        self._release = None
        self._accent, self._idle_color = theme_colors()
        self._refresh()
        self.clicked.connect(self._on_click)

    # ── state ────────────────────────────────────────────────────────────────

    def set_release(self, release: dict | None):
        self._release = release
        self._refresh()

    def has_update(self) -> bool:
        return self._release is not None

    def release(self) -> dict | None:
        return self._release

    def set_colors(self, accent: str, idle: str):
        self._accent = accent
        self._idle_color = idle
        self._refresh()

    def refresh_theme(self):
        """Re-read palette after a theme switch — same contract as Sidebar."""
        self._accent, self._idle_color = theme_colors()
        self._refresh()

    def _refresh(self):
        color = self._accent if self.has_update() else self._idle_color
        self.setIcon(make_icon("update", color, 17))
        if self.has_update():
            self.setToolTip(
                f"Update available: {self._release.get('tag', '')}\nClick to install."
            )
        else:
            self.setToolTip("You're up to date.\nClick to check again.")
        self.update()

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.has_update():
            return
        # Small dot rather than a pulse or a toast — visible when looked at,
        # invisible when working.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._accent))
        d = 7
        p.drawEllipse(self.width() - d - 4, 3, d, d)
        p.end()

    # ── placement ────────────────────────────────────────────────────────────

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.move(parent.width() - self.width() - _MARGIN, _MARGIN)
        self.raise_()

    def _on_click(self):
        self.clicked_with_update.emit()
