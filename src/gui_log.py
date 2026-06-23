# src/gui_log.py
from PySide6.QtWidgets import QTextEdit, QLabel
from PySide6.QtGui import QTextCharFormat, QColor, QFont, QFontDatabase, QKeySequence
from PySide6.QtCore import Qt, QTimer

from gui_styles import (
    LOG_INFO, LOG_SUCCESS, LOG_ERROR, LOG_WARNING, LOG_STEP, LOG_DIM,
    TEXT_SEC, BG,
)

_TAG_COLORS = {
    "info":    LOG_INFO,
    "success": LOG_SUCCESS,
    "error":   LOG_ERROR,
    "warning": LOG_WARNING,
    "step":    LOG_STEP,
    "dim":     LOG_DIM,
}
_LOG_FONTS = ["Cascadia Code", "JetBrains Mono", "Consolas", "Courier New"]


class LogWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self._setup_font()
        self._at_bottom = True
        self.verticalScrollBar().valueChanged.connect(self._track_scroll)
        self._setup_zoom_badge()

    def _setup_font(self):
        available = QFontDatabase.families()
        family = next((f for f in _LOG_FONTS if f in available), "monospace")
        self.setFont(QFont(family, 13))

    def _setup_zoom_badge(self):
        self._zoom_level = 100
        self._zoom_badge = QLabel("100%", self)
        self._zoom_badge.setStyleSheet(
            f"color:{TEXT_SEC};background:rgba(13,13,18,200);"
            "padding:2px 6px;border-radius:3px;font-size:10px;"
        )
        self._zoom_badge.hide()
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._zoom_badge.hide)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_badge()

    def _reposition_badge(self):
        self._zoom_badge.adjustSize()
        self._zoom_badge.move(
            self.width() - self._zoom_badge.width() - 10,
            self.height() - self._zoom_badge.height() - 10,
        )

    def _show_zoom_badge(self):
        self._zoom_badge.setText(f"{self._zoom_level}%")
        self._reposition_badge()
        self._zoom_badge.show()
        self._zoom_badge.raise_()
        self._zoom_timer.start(1500)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoomIn(1)
                self._zoom_level = min(200, self._zoom_level + 10)
            else:
                self.zoomOut(1)
                self._zoom_level = max(50, self._zoom_level - 10)
            self._show_zoom_badge()
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.ZoomIn):
            self.zoomIn(1)
            self._zoom_level = min(200, self._zoom_level + 10)
            self._show_zoom_badge()
        elif event.matches(QKeySequence.StandardKey.ZoomOut):
            self.zoomOut(1)
            self._zoom_level = max(50, self._zoom_level - 10)
            self._show_zoom_badge()
        else:
            super().keyPressEvent(event)

    def _track_scroll(self, value):
        self._at_bottom = value >= self.verticalScrollBar().maximum() - 5

    def append_log(self, text: str, tag: str = "info"):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(_TAG_COLORS.get(tag, LOG_INFO)))
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        if self._at_bottom:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def clear_log(self):
        self.clear()
        self._at_bottom = True
