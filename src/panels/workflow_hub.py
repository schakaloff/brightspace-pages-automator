"""Small task hub that keeps the main sidebar stable as tools are added."""

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QSizePolicy,
)

import gui_styles
from gui_tool_icons import tool_pixmap, soft_tool_pixmap

class MediaToolRow(QPushButton):
    """One native button, including its icon, labels and surrounding whitespace."""

    def __init__(self, name, description, parent=None, *, minimum_height=96):
        super().__init__(parent)
        self._minimum_height = minimum_height
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
        return self.layout().sizeHint().expandedTo(QSize(0, self._minimum_height))

    def minimumSizeHint(self):
        return QSize(0, self._minimum_height)

    def heightForWidth(self, width):
        return max(self._minimum_height, self.layout().totalHeightForWidth(width))

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
    """Two groups of full-row navigation targets using the Media palette."""

    tool_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('build_style_hub')
        self.setProperty('design', 'modern')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.buttons = {}
        self._icons = []
        self._build()
        self.refresh_theme()

    @staticmethod
    def _label(text, role):
        label = QLabel(text)
        label.setProperty('role', role)
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
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 16, 32, 8)
        layout.setSpacing(0)
        layout.addWidget(self._label('Build & style', 'page-heading'))
        layout.addSpacing(4)
        layout.addWidget(self._label('Work on Brightspace pages and course content.', 'hub-intro'))
        layout.addSpacing(20)
        self._add_group(layout, 'Build content', [
            ('create_pages', 'Create pages in bulk',
             'Paste titles, add content, and create pages in a chosen section.', 'file-plus'),
            ('collect', 'Collect a unit',
             'Combine a unit’s topics into one structured page.', 'stack'),
            ('duplicate_content', 'Duplicate content',
             'Copy sections, pages, files and links with a preview first.', 'copy'),
            ('structured_transfer', 'Structured transfer',
             'Copy and reorganize content from another course, including its activities.', 'folders'),
        ])
        layout.addSpacing(24)
        self._add_group(layout, 'Update existing content', [
            ('quiz_repair', 'Repair quiz exports',
             'Restore quiz images and repair multi-select and matching scoring in local files.', 'folders'),
            ('restyle', 'Restyle pages', 'Apply an OC theme to the pages you select.', 'paint-roller'),
            ('cleanup', 'Remove topics', 'Review course topics before removing them in one batch.', 'trash'),
        ])
        layout.addStretch()
        self._scroll.setWidget(content)

    def _add_group(self, layout, title, tasks):
        layout.addWidget(self._label(title, 'hub-section'))
        layout.addSpacing(6)
        group = QFrame()
        group.setProperty('role', 'hub-group')
        group.setAccessibleName(title)
        items = QVBoxLayout(group)
        items.setContentsMargins(1, 1, 1, 1)
        items.setSpacing(0)
        for index, (key, name, description, icon_name) in enumerate(tasks):
            if index:
                divider = QFrame()
                divider.setProperty('role', 'hub-divider')
                divider.setFixedHeight(1)
                items.addWidget(divider)
            button = MediaToolRow(name, description, minimum_height=80)
            button.setObjectName(f'build_tool_{key}')
            button.setProperty('role', 'build-tool')
            button.setProperty('position', 'first' if index == 0 else 'last' if index == len(tasks) - 1 else 'middle')
            row = QHBoxLayout(button)
            row.setContentsMargins(18, 12, 20, 12)
            row.setSpacing(18)
            tile = QFrame()
            tile.setProperty('role', 'hub-icon-tile')
            tile.setFixedSize(56, 56)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(12, 12, 12, 12)
            icon = QLabel()
            icon.setFixedSize(32, 32)
            tile_layout.addWidget(icon)
            self._icons.append((icon, icon_name, 32, True))
            row.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)
            text = QVBoxLayout()
            text.setSpacing(4)
            text.addWidget(self._label(name, 'hub-tool-heading'))
            text.addWidget(self._label(description, 'hub-description'))
            row.addLayout(text, 1)
            arrow = QLabel()
            arrow.setFixedSize(20, 20)
            self._icons.append((arrow, 'chevron-right', 20, False))
            row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignVCenter)
            for child in button.findChildren(QWidget):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                child.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, tool=key: self.tool_selected.emit(tool))
            self.buttons[key] = button
            items.addWidget(button)
        layout.addWidget(group)

    def refresh_theme(self):
        color = gui_styles.modern_colors()['muted']
        for label, name, size, soft in self._icons:
            label.setPixmap((soft_tool_pixmap if soft else tool_pixmap)(name, size, color))
