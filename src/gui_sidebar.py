import math
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QHBoxLayout
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont, QPolygonF
from PySide6.QtCore import Qt, Signal, QRect, QRectF, QPointF, QSize

from gui_styles import (
    OC_ORANGE, OC_TEAL, TEXT_PRI, TEXT_SEC, TEXT_FAINT,
    PANEL, DONE, RUNNING, BORDER, SIDEBAR,
)
from gui_icons import make_pixmap


class StepButton(QPushButton):
    LOCKED  = "locked"
    PENDING = "pending"
    ACTIVE  = "active"
    DONE    = "done"
    RUNNING = "running"

    def __init__(self, number: int, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self._number    = number
        self._icon_name = icon_name
        self._label     = label
        self._state     = self.LOCKED
        self.setFixedHeight(52)
        self.setProperty("class", "step-btn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_state()

    def set_state(self, state: str):
        if state == self._state:
            return
        self._state = state
        self._apply_state()
        self.update()

    def get_state(self) -> str:
        return self._state

    def _apply_state(self):
        self.setEnabled(self._state != self.LOCKED)
        self.setProperty("active", self._state == self.ACTIVE)
        self.style().unpolish(self)
        self.style().polish(self)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        locked = self._state == self.LOCKED

        # Step number chip
        chip_w, chip_h = 20, 14
        chip_x = 12
        chip_y = (h - chip_h) // 2
        chip_color = OC_ORANGE if not locked else TEXT_FAINT
        p.setBrush(QBrush(QColor(chip_color + "22")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(chip_x, chip_y, chip_w, chip_h, 3, 3)
        p.setPen(QPen(QColor(chip_color)))
        nf = QFont()
        nf.setPointSize(7)
        nf.setBold(True)
        p.setFont(nf)
        p.drawText(QRect(chip_x, chip_y, chip_w, chip_h),
                   Qt.AlignmentFlag.AlignCenter, str(self._number))

        # Icon
        icon_x = chip_x + chip_w + 8
        icon_y = (h - 16) // 2
        icon_color = TEXT_PRI if self._state == self.ACTIVE else TEXT_SEC
        px = make_pixmap(self._icon_name, icon_color, 16)
        p.setOpacity(0.4 if locked else 1.0)
        p.drawPixmap(icon_x, icon_y, px)
        p.setOpacity(1.0)

        # Label
        label_x = icon_x + 16 + 8
        label_color = TEXT_PRI if self._state == self.ACTIVE else (
            TEXT_FAINT if locked else TEXT_SEC
        )
        lf = QFont()
        lf.setPointSize(9)
        lf.setBold(True)
        p.setPen(QPen(QColor(label_color)))
        p.setFont(lf)
        p.setOpacity(0.4 if locked else 1.0)
        p.drawText(QRect(label_x, 0, w - label_x - 28, h),
                   Qt.AlignmentFlag.AlignVCenter, self._label)
        p.setOpacity(1.0)

        # Status indicator
        dot_x = w - 16
        dot_y = h // 2
        dot_r = 4
        if self._state == self.DONE:
            p.setBrush(QBrush(QColor(DONE)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)
        elif self._state == self.RUNNING:
            p.setBrush(QBrush(QColor(RUNNING)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)
        elif self._state == self.LOCKED:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(TEXT_FAINT), 1.2))
            p.setOpacity(0.5)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)
            p.setOpacity(1.0)
        elif self._state == self.PENDING:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(TEXT_FAINT), 1.2))
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)

        p.end()


class Sidebar(QWidget):
    step_clicked     = Signal(int)
    settings_clicked = Signal()

    def __init__(self, steps: list, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(160)
        self._step_buttons: dict[int, StepButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # App header
        header = QWidget()
        header.setFixedHeight(64)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 10, 14, 10)
        info = QWidget()
        iv = QVBoxLayout(info)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(1)
        top = QLabel("Brightspace")
        top.setStyleSheet(f"color:{TEXT_FAINT};font-size:9px;font-weight:700;")
        bot = QLabel("Automator")
        bot.setStyleSheet(f"color:{TEXT_PRI};font-size:13px;font-weight:700;")
        iv.addWidget(top)
        iv.addWidget(bot)
        hl.addWidget(info)
        layout.addWidget(header)

        # Divider
        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{BORDER};")
        layout.addWidget(div)
        layout.addSpacing(6)

        # Step buttons
        for number, icon_name, label in steps:
            btn = StepButton(number, icon_name, label)
            btn.clicked.connect(lambda _=False, n=number: self.step_clicked.emit(n))
            self._step_buttons[number] = btn
            layout.addWidget(btn)

        layout.addStretch()

        # Bottom divider
        div2 = QWidget()
        div2.setFixedHeight(1)
        div2.setStyleSheet(f"background:{BORDER};")
        layout.addWidget(div2)

        # Settings button
        self._settings_btn = QPushButton("  Settings")
        self._settings_btn.setProperty("class", "step-btn")
        self._settings_btn.setFixedHeight(44)
        self._settings_btn.setIcon(make_pixmap("settings", TEXT_SEC, 16))  # type: ignore
        self._settings_btn.clicked.connect(self.settings_clicked)
        layout.addWidget(self._settings_btn)
        layout.addSpacing(4)

    def set_step_state(self, number: int, state: str):
        if number in self._step_buttons:
            self._step_buttons[number].set_state(state)

    def set_active(self, number: int | None):
        for n, btn in self._step_buttons.items():
            if btn.get_state() not in (StepButton.LOCKED, StepButton.DONE, StepButton.RUNNING):
                btn.set_state(StepButton.PENDING)
        if number is not None and number in self._step_buttons:
            if self._step_buttons[number].get_state() != StepButton.LOCKED:
                self._step_buttons[number].set_state(StepButton.ACTIVE)
        is_settings = number is None
        self._settings_btn.setProperty("active", is_settings)
        self._settings_btn.style().unpolish(self._settings_btn)
        self._settings_btn.style().polish(self._settings_btn)
