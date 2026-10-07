"""Modern native startup card using the app's shared theme and brand icon."""

from pathlib import Path
import sys

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSplashScreen

import gui_styles
from app_version import APP_VERSION


_BRAND_ICON = None


def _brand_icon():
    global _BRAND_ICON
    if _BRAND_ICON is None:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        _BRAND_ICON = QIcon(str(root / "assets" / "icon.ico")).pixmap(256, 256)
        _BRAND_ICON.setDevicePixelRatio(4)
    return _BRAND_ICON


def build_splash_pixmap(progress=0.0, message="Starting…") -> QPixmap:
    """Render at high density; text and progress are part of the same layout."""
    colors = gui_styles.modern_colors()
    width, height, scale = 520, 300, 4
    pixmap = QPixmap(width * scale, height * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    painter.setPen(QColor(colors["border"]))
    painter.setBrush(QColor(colors["surface"]))
    painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 16, 16)

    def text(value, x, y, w, h, size, color, bold=False):
        font = QFont("Segoe UI")
        font.setPixelSize(size)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)

    painter.drawPixmap(32, 34, _brand_icon())
    text("Brightspace", 116, 34, 370, 24, 16, colors["muted"], True)
    text("Pages Automator", 116, 61, 370, 38, 28, colors["text"], True)
    text("Build, style, and organize your course content.", 32, 122, 456, 24, 14, colors["muted"])
    text(message, 32, 174, 456, 22, 13, colors["text"])

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors["soft"]))
    painter.drawRoundedRect(QRectF(32, 208, 456, 4), 2, 2)
    filled = 456 * min(max(progress, 0.0), 1.0)
    if filled > 0:
        painter.setBrush(QColor(colors["focus"]))
        painter.drawRoundedRect(QRectF(32, 208, filled, 4), 2, 2)
    text(f"Version {APP_VERSION}", 32, 250, 456, 20, 12, colors["muted"])
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


class StartupSplash(QSplashScreen):
    """Progress advances with reported startup stages instead of elapsed time."""

    def __init__(self):
        self.progress = 0.0
        self.status = "Starting…"
        super().__init__(build_splash_pixmap(self.progress, self.status))

    def set_stage(self, message, progress):
        self.progress = max(self.progress, min(max(progress, 0.0), 1.0))
        self.status = message
        self.setPixmap(build_splash_pixmap(self.progress, self.status))
