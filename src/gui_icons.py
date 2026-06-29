import math
from PySide6.QtGui import (
    QPainter, QPixmap, QIcon, QPen, QBrush,
    QPainterPath, QColor, QPolygonF,
)
from PySide6.QtCore import Qt, QRectF, QPointF, QRect


def _make_pixmap(size: int, draw_fn, color: str) -> QPixmap:
    scale = 2
    px = QPixmap(size * scale, size * scale)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(scale, scale)
    draw_fn(p, size, QColor(color))
    p.end()
    return px.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


def _checker(p, s, c):
    pen = QPen(c, 1.8); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    r = s * 0.28
    cx = s / 2
    cy = s / 2
    p.drawEllipse(QRectF(cx - r - 2, cy - r, r * 2, r * 2))
    p.drawEllipse(QRectF(cx - r + 2, cy - r, r * 2, r * 2))


def _collect(p, s, c):
    pen = QPen(c, 1.8); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    w = s * 0.65; h = s * 0.14
    for i, yo in enumerate([0, s * 0.23, s * 0.46]):
        x = (s - w) / 2 + i * 1.5
        y = s * 0.18 + yo
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w - i * 1.5, h), 2, 2)
        p.drawPath(path)


def _restyle(p, s, c):
    pen = QPen(c, 2.2); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(s * 0.2, s * 0.8), QPointF(s * 0.72, s * 0.28))
    sq = s * 0.13
    p.setBrush(QBrush(c)); p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(QRectF(s * 0.72 - sq / 2, s * 0.28 - sq / 2, sq, sq))


def _kaltura(p, s, c):
    pen = QPen(c, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.1, s * 0.2, s * 0.8, s * 0.6), 3, 3)
    p.setBrush(QBrush(c))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.38, s * 0.35),
        QPointF(s * 0.38, s * 0.65),
        QPointF(s * 0.68, s * 0.50),
    ]))


def _settings(p, s, c):
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(c))
    cx, cy = s / 2, s / 2
    outer, inner = s * 0.38, s * 0.17
    pts = []
    for i in range(16):
        angle = math.radians(i * 22.5 - 90)
        r = outer if i % 2 == 0 else inner
        pts.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
    p.drawPolygon(QPolygonF(pts))


def _run(p, s, c):
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(c))
    m = s * 0.22
    p.drawPolygon(QPolygonF([QPointF(m, m), QPointF(m, s - m), QPointF(s - m, s / 2)]))


def _next_arrow(p, s, c):
    pen = QPen(c, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    cy = s / 2
    p.drawLine(QPointF(s * 0.12, cy), QPointF(s * 0.78, cy))
    p.drawLine(QPointF(s * 0.62, s * 0.32), QPointF(s * 0.86, cy))
    p.drawLine(QPointF(s * 0.62, s * 0.68), QPointF(s * 0.86, cy))


def _done(p, s, c):
    pen = QPen(c, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    m = s * 0.08
    p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
    path = QPainterPath()
    path.moveTo(QPointF(s * 0.28, s * 0.52))
    path.lineTo(QPointF(s * 0.44, s * 0.68))
    path.lineTo(QPointF(s * 0.72, s * 0.36))
    p.drawPath(path)


def _locked(p, s, c):
    pen = QPen(c, 1.8); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    bx, by = s * 0.26, s * 0.50
    bw, bh = s * 0.48, s * 0.38
    path = QPainterPath(); path.addRoundedRect(QRectF(bx, by, bw, bh), 3, 3)
    p.drawPath(path)
    p.drawArc(QRectF(s * 0.32, s * 0.16, s * 0.36, s * 0.46), 0, 180 * 16)


def _running(p, s, c):
    pen = QPen(c, 2.2); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    m = s * 0.1
    p.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 90 * 16, -270 * 16)


_FNS = {
    "checker": _checker, "collect": _collect, "restyle": _restyle,
    "kaltura": _kaltura,
    "settings": _settings, "run": _run, "next": _next_arrow,
    "done": _done, "locked": _locked, "running": _running,
}


def make_pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    return _make_pixmap(size, _FNS[name], color)


def make_icon(name: str, color: str, size: int = 16) -> QIcon:
    return QIcon(make_pixmap(name, color, size))
