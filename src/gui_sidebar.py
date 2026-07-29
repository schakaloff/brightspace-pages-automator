import math
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QHBoxLayout, QScrollArea, QFrame,
)
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont, QPolygonF
from PySide6.QtCore import Qt, Signal, QRect, QRectF, QPointF, QSize

import gui_styles


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
        self._hovered   = False
        self._show_dot  = True
        self.setFixedHeight(50)
        self.setProperty("class", "step-btn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self._apply_state()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def set_show_dot(self, show: bool):
        """Settings is not a workflow step, so it carries no status dot."""
        self._show_dot = show
        self.update()

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
        # Painted entirely here rather than by the stylesheet: the active row is
        # a rounded card, which QSS cannot express on a QPushButton without
        # fighting the frame it draws. super().paintEvent is skipped for the
        # same reason — it would draw a square background under the card.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        locked = self._state == self.LOCKED
        active = self._state == self.ACTIVE
        c = gui_styles.current
        accent = gui_styles.accent()

        pad = 8
        card = QRectF(pad, 2, w - pad * 2, h - 4)

        if active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["PANEL"]))
            p.drawRoundedRect(card, 8, 8)
            p.setBrush(QColor(accent))
            p.drawRoundedRect(QRectF(card.left(), card.top() + 11, 3,
                                     card.height() - 22), 2, 2)
        elif self._hovered and not locked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["PANEL"]))
            p.setOpacity(0.55)
            p.drawRoundedRect(card, 8, 8)
            p.setOpacity(1.0)

        # Icon tile
        icon_color = accent if active else (
            c["TEXT_FAINT"] if locked else gui_styles.sidebar_label()
        )
        tile = QRectF(pad + 14, (h - 28) / 2, 28, 28)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["BG"] if active else c["PANEL"]))
        p.setOpacity(0.5 if locked else 1.0)
        p.drawRoundedRect(tile, 7, 7)
        if self._icon_name:
            from gui_icons import make_pixmap
            px = make_pixmap(self._icon_name, icon_color, 16)
            p.drawPixmap(int(tile.center().x() - 8),
                         int(tile.center().y() - 8), px)
        p.setOpacity(1.0)

        # Label
        label_x = int(tile.right()) + 12
        label_color = c["TEXT_PRI"] if active else (
            c["TEXT_FAINT"] if locked else gui_styles.sidebar_label()
        )
        lf = QFont()
        lf.setPointSize(9)
        lf.setBold(True)
        p.setPen(QPen(QColor(label_color)))
        p.setFont(lf)
        p.setOpacity(0.4 if locked else 1.0)
        p.drawText(QRect(label_x, 0, w - label_x - 26, h),
                   Qt.AlignmentFlag.AlignVCenter, self._label)
        p.setOpacity(1.0)

        # Status indicator
        if not self._show_dot:
            p.end()
            return
        dot_x = w - 18
        dot_y = h // 2
        dot_r = 4
        if self._state == self.DONE:
            p.setBrush(QBrush(QColor(gui_styles.status_done())))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)
        elif self._state == self.RUNNING:
            p.setBrush(QBrush(QColor(c["RUNNING"])))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)
        elif self._state == self.LOCKED:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(c["TEXT_FAINT"]), 1.2))
            p.setOpacity(0.5)
            p.drawEllipse(dot_x - dot_r, dot_y - dot_r, dot_r * 2, dot_r * 2)
            p.setOpacity(1.0)
        elif self._state == self.PENDING:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(c["TEXT_FAINT"]), 1.2))
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

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("sidebar_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer_layout.addWidget(scroll)

        content = QWidget()
        content.setObjectName("sidebar_content")
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
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
        self._lbl_top = QLabel("Brightspace")
        self._lbl_top.setStyleSheet(f"color:{gui_styles.current['TEXT_FAINT']};font-size:9px;font-weight:700;")
        self._lbl_bot = QLabel("Automator")
        self._lbl_bot.setStyleSheet(f"color:{gui_styles.current['TEXT_PRI']};font-size:13px;font-weight:700;")
        iv.addWidget(self._lbl_top)
        iv.addWidget(self._lbl_bot)
        hl.addWidget(info)
        layout.addWidget(header)

        # Divider
        self._div_top = QWidget()
        self._div_top.setFixedHeight(1)
        self._div_top.setStyleSheet(f"background:{gui_styles.current['BORDER']};")
        layout.addWidget(self._div_top)
        layout.addSpacing(6)

        _step_tooltips = {
            1: "Step 1 — Checker: Compare Moodle and Brightspace course content, download missing files.",
            2: "Step 2 — Collect: Scrape all topics from a unit and combine them into one collapsible page.",
            3: "Step 3 — Restyle: Use Claude AI to apply an OC brand theme to a Brightspace page.",
            4: "Step 4 — Kaltura: Scan Moodle for Kaltura videos and create matching Brightspace pages.",
            5: "Step 5 — H5P: Download H5P activities from Moodle and paste them into matching Brightspace modules.",
        }

        # Step buttons (a (None, None, "Label") entry renders a section divider instead)
        self._section_dividers: list[QWidget] = []
        self._section_labels: list[QLabel] = []
        for number, icon_name, label in steps:
            if number is None:
                # Was two 1px rules wrapping the label, i.e. five widgets per
                # heading. Letter-spaced small caps with space above separates
                # the groups on its own and reads far quieter.
                layout.addSpacing(14)
                header = QLabel(label.upper())
                header.setStyleSheet(
                    f"color:{gui_styles.sidebar_header()};font-size:9px;"
                    f"font-weight:700;letter-spacing:1.4px;"
                    f"padding:0px 12px 6px 22px;"
                )
                self._section_labels.append(header)
                layout.addWidget(header)
                continue

            btn = StepButton(number, icon_name, label)
            btn.setToolTip(_step_tooltips.get(number, ""))
            btn.clicked.connect(lambda _=False, n=number: self.step_clicked.emit(n))
            self._step_buttons[number] = btn
            layout.addWidget(btn)

        layout.addStretch()

        # Bottom divider
        self._div_bot = QWidget()
        self._div_bot.setFixedHeight(1)
        self._div_bot.setStyleSheet(f"background:{gui_styles.current['BORDER']};")
        layout.addWidget(self._div_bot)
        layout.addSpacing(6)

        # Settings — a StepButton with no number so it matches the tiles above
        # instead of sitting flush against the divider as bare text.
        self._settings_btn = StepButton(0, "settings", "Settings")
        self._settings_btn.set_state(StepButton.PENDING)
        self._settings_btn.set_show_dot(False)
        self._settings_btn.setToolTip("Configure credentials, Claude API key, and app appearance.")
        self._settings_btn.clicked.connect(self.settings_clicked)
        layout.addWidget(self._settings_btn)
        layout.addSpacing(8)

    def refresh_theme(self):
        c = gui_styles.current
        self._lbl_top.setStyleSheet(f"color:{c['TEXT_FAINT']};font-size:9px;font-weight:700;")
        self._lbl_bot.setStyleSheet(f"color:{c['TEXT_PRI']};font-size:13px;font-weight:700;")
        self._div_top.setStyleSheet(f"background:{c['BORDER']};")
        self._div_bot.setStyleSheet(f"background:{c['BORDER']};")
        for div in self._section_dividers:
            div.setStyleSheet(f"background:{c['BORDER']};")
        for lbl in self._section_labels:
            lbl.setStyleSheet(
                f"color:{gui_styles.sidebar_header()};font-size:9px;font-weight:700;"
                f"letter-spacing:1.4px;padding:0px 12px 6px 22px;"
            )
        for btn in self._step_buttons.values():
            btn.update()
        self._settings_btn.update()

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
        # Settings is a hand-painted StepButton now, so its look comes from
        # state rather than the QSS "active" property.
        self._settings_btn.set_state(
            StepButton.ACTIVE if number is None else StepButton.PENDING
        )
