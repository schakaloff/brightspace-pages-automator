"""Small task hub that keeps the main sidebar stable as tools are added."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QSizePolicy,
)
from PySide6.QtGui import QIcon

import gui_styles
from gui_tool_icons import tool_pixmap

from panels._shared import _section_header


class WorkflowHub(QWidget):
    """A page of tool cards. Adding a tool means adding a card, not a tab."""

    tool_selected = Signal(str)

    def __init__(self, title: str, description: str,
                 tasks: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)
        layout.addWidget(_section_header(title))
        intro = QLabel(description)
        intro.setProperty("role", "dim")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(20)
        self.buttons: dict[str, QPushButton] = {}
        for key, name, explanation in tasks:
            card = QFrame()
            card.setProperty("role", "card")
            row = QHBoxLayout(card)
            row.setContentsMargins(18, 14, 14, 14)
            row.setSpacing(16)
            text = QVBoxLayout()
            text.setSpacing(3)
            heading = QLabel(name)
            heading.setStyleSheet("font-weight:600;font-size:14px;")
            detail = QLabel(explanation)
            detail.setProperty("role", "dim")
            detail.setWordWrap(True)
            text.addWidget(heading)
            text.addWidget(detail)
            row.addLayout(text, 1)
            button = QPushButton("Open")
            button.setProperty("variant", "secondary")
            button.setFixedWidth(96)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, tool=key: self.tool_selected.emit(tool))
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self.buttons[key] = button
            layout.addWidget(card)
            layout.addSpacing(10)
        layout.addStretch()


class BuildStyleHub(QWidget):
    """Grouped content tools using the shared modern desktop design tokens."""

    tool_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("build_style_hub")
        self.setProperty("design", "modern")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.buttons = {}
        self._icons = {}
        self._compact = None
        self._support_rows = []
        self._build()
        self.refresh_theme()

    @staticmethod
    def _label(text, role):
        label = QLabel(text)
        label.setProperty("role", role)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(0)
        layout.addWidget(self._label("Build & style", "page-heading"))
        layout.addSpacing(4)
        layout.addWidget(self._label("Work on Brightspace pages and course content.", "hub-intro"))
        layout.addSpacing(30)
        layout.addWidget(self._label("Build content", "hub-section"))
        layout.addSpacing(4)
        layout.addWidget(self._label("Create new pages from your course content.", "hub-intro"))
        layout.addSpacing(16)
        self._primary_grid = QGridLayout()
        self._primary_grid.setSpacing(16)
        self._primary_cards = [
            self._primary_card("create_pages", "Create pages in bulk",
                               "Paste titles, add content, and create pages in a chosen section.",
                               "Create pages", "file-plus"),
            self._primary_card("collect", "Collect a unit",
                               "Combine a unit’s topics into one structured page.",
                               "Collect unit", "book"),
        ]
        layout.addLayout(self._primary_grid)
        layout.addSpacing(30)
        layout.addWidget(self._label("Update existing content", "hub-section"))
        layout.addSpacing(4)
        layout.addWidget(self._label("Improve and tidy up pages you already have.", "hub-intro"))
        layout.addSpacing(16)
        layout.addWidget(self._support_card("restyle", "Restyle pages",
                                           "Apply an OC theme to the pages you select.",
                                           "Choose pages", "file-pencil"))
        layout.addSpacing(12)
        layout.addWidget(self._support_card("cleanup", "Remove topics",
                                           "Choose course topics to remove in one reviewed batch.",
                                           "Review topics", "trash", quiet=True))
        layout.addStretch(1)
        self._scroll.setWidget(content)
        self._reflow()

    def _button(self, key, text, variant=""):
        button = QPushButton(text)
        button.setObjectName(f"build_tool_{key}")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # Action arrow follows the label.
        button.setMinimumHeight(42)
        if variant:
            button.setProperty("variant", variant)
        button.clicked.connect(lambda _=False, tool=key: self.tool_selected.emit(tool))
        self.buttons[key] = button
        return button

    def _icon(self, key, name, size):
        icon = QLabel()
        icon.setFixedSize(size, size)
        icon.setAccessibleName("")  # Adjacent text names the tool.
        self._icons[key] = (icon, name, size)
        return icon

    def _primary_card(self, key, title, description, action, icon_name):
        card = QFrame()
        card.setProperty("role", "surface")
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        grid = QGridLayout(card)
        grid.setContentsMargins(22, 22, 22, 22)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(20)
        grid.addWidget(self._icon(key, icon_name, 48), 0, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(6)
        text.addWidget(self._label(title, "hub-tool-heading"))
        text.addWidget(self._label(description, "hub-description"))
        text.addStretch()
        grid.addLayout(text, 0, 1)
        button = self._button(key, action)
        button.setMinimumWidth(160)
        grid.addWidget(button, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute)
        grid.setColumnStretch(1, 1)
        card.setMinimumHeight(184)
        return card

    def _support_card(self, key, title, description, action, icon_name, quiet=False):
        card = QFrame()
        card.setProperty("role", "surface")
        grid = QGridLayout(card)
        grid.setContentsMargins(22, 18, 22, 18)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        grid.addWidget(self._icon(key, icon_name, 40), 0, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(self._label(title, "hub-tool-heading"))
        text.addWidget(self._label(description, "hub-description"))
        grid.addLayout(text, 0, 1)
        button = self._button(key, action, "ghost" if quiet else "hub-outline")
        grid.addWidget(button, 0, 2)
        grid.setColumnStretch(1, 1)
        self._support_rows.append((grid, button))
        return card

    def _reflow(self):
        compact = self.width() < 720
        if compact == self._compact:
            return
        self._compact = compact
        for card in self._primary_cards:
            self._primary_grid.removeWidget(card)
        for i, card in enumerate(self._primary_cards):
            self._primary_grid.addWidget(card, i if compact else 0, 0 if compact else i)
        self._primary_grid.setColumnStretch(0, 1)
        self._primary_grid.setColumnStretch(1, 0 if compact else 1)
        for grid, button in self._support_rows:
            grid.removeWidget(button)
            grid.addWidget(button, 1 if compact else 0, 1 if compact else 2,
                           (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute)
                           if compact else Qt.AlignmentFlag.AlignVCenter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def refresh_theme(self):
        colors = gui_styles.modern_colors()
        for key, (label, name, size) in self._icons.items():
            accent = colors["error"] if key == "cleanup" else colors["focus"]
            label.setPixmap(tool_pixmap(name, size, colors["text"], accent))
        for key, button in self.buttons.items():
            color = "#ffffff" if key in ("create_pages", "collect") else colors["focus"]
            if key == "cleanup":
                color = colors["muted"]
            button.setIcon(QIcon(tool_pixmap("arrow-right", 16, color)))
