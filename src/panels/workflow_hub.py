"""Small task hub that keeps the main sidebar stable as tools are added."""

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QSizePolicy,
)
from PySide6.QtGui import QIcon

import gui_styles
from gui_tool_icons import tool_pixmap

class MediaToolRow(QPushButton):
    """One native button, including its icon, labels and surrounding whitespace."""

    def __init__(self, name, description, parent=None):
        super().__init__(parent)
        self.setProperty('role', 'media-tool')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(name)
        self.setAccessibleDescription(description)
        self.setAutoDefault(False)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(0)

    def sizeHint(self):
        return self.layout().sizeHint().expandedTo(QSize(0, 96))

    def minimumSizeHint(self):
        return QSize(0, 96)

    def heightForWidth(self, width):
        return max(96, self.layout().totalHeightForWidth(width))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class WorkflowHub(QWidget):
    """Media navigation, with one full-width click target per tool."""

    tool_selected = Signal(str)

    def __init__(self, title: str, description: str,
                 tasks: list[tuple[str, str, str]], parent=None, *, hint=""):
        super().__init__(parent)
        self.setObjectName('media_hub')
        self.setProperty('design', 'modern')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(0)
        heading = QLabel(title)
        heading.setProperty('role', 'page-heading')
        layout.addWidget(heading)
        layout.addSpacing(4)
        intro = QLabel(description)
        intro.setProperty('role', 'media-intro')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if hint:
            layout.addSpacing(4)
            note = QLabel(hint)
            note.setProperty('role', 'media-hint')
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addSpacing(24)
        self.buttons: dict[str, QPushButton] = {}
        self._icons = []
        icon_names = dict(h5p='puzzle', scorm='package', kaltura='movie')
        for key, name, explanation in tasks:
            button = MediaToolRow(name, explanation)
            button.setObjectName(f'media_tool_{key}')
            row = QHBoxLayout(button)
            row.setContentsMargins(18, 16, 20, 16)
            row.setSpacing(16)
            tile = QFrame()
            tile.setProperty('role', 'media-icon-tile')
            tile.setFixedSize(64, 64)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(16, 16, 16, 16)
            icon = QLabel()
            icon.setFixedSize(32, 32)
            tile_layout.addWidget(icon)
            self._icons.append((icon, icon_names.get(key, 'file'), 32))
            row.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)
            text = QVBoxLayout()
            text.setSpacing(4)
            heading = QLabel(name)
            heading.setProperty('role', 'media-tool-heading')
            heading.setWordWrap(True)
            heading.setMinimumWidth(0)
            detail = QLabel(explanation)
            detail.setProperty('role', 'media-description')
            detail.setWordWrap(True)
            detail.setMinimumWidth(0)
            text.addWidget(heading)
            text.addWidget(detail)
            row.addLayout(text, 1)
            arrow = QLabel()
            arrow.setFixedSize(20, 20)
            self._icons.append((arrow, 'chevron-right', 20))
            row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignVCenter)
            # Keep the whole surface as a single keyboard and pointer target.
            for child in button.findChildren(QWidget):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                child.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, tool=key: self.tool_selected.emit(tool))
            self.buttons[key] = button
            layout.addWidget(button)
            layout.addSpacing(12)
        layout.addStretch()
        self._scroll.setWidget(content)
        self.refresh_theme()

    def refresh_theme(self):
        for label, icon, size in self._icons:
            label.setPixmap(tool_pixmap(icon, size, gui_styles.modern_colors()['muted']))


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
        layout.addSpacing(16)
        layout.addWidget(self._support_card("duplicate_content", "Duplicate content",
                                           "Copy sections, pages, files and links with a preview of the resulting structure.",
                                           "Make copies", "copy"))
        layout.addSpacing(16)
        layout.addWidget(self._support_card("structured_transfer", "Structured Transfer",
                                           "Copy another course into this one, reorganized, restyled and with its activities.",
                                           "Plan transfer", "book"))
        layout.addSpacing(8)
        layout.addWidget(self._button("structured_transfer_qt", "Use in-app planner (edit and create)", "ghost"),
                         0, Qt.AlignmentFlag.AlignLeft)
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
