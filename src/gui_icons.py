import math
from PySide6.QtGui import (
    QPainter, QPixmap, QIcon, QPen, QBrush,
    QPainterPath, QColor, QPolygonF,
)
from PySide6.QtCore import Qt, QRectF, QPointF, QRect


def _make_pixmap(size: int, draw_fn, color: str) -> QPixmap:
    # Render at `scale`x physical resolution and tag the pixmap with that
    # devicePixelRatio, instead of scaling back down to `size`x`size` — the
    # old .scaled() call threw away all the extra detail, so every icon was
    # only ever `size` physical pixels and looked blocky on high-DPI displays
    # (150%/200% Windows scaling, macOS retina). Qt downscales a high-res
    # pixmap cleanly on low-DPI screens, so one generous factor is safe.
    scale = 4
    px = QPixmap(size * scale, size * scale)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(scale, scale)
    draw_fn(p, size, QColor(color))
    p.end()
    # Tag the DPR only after painting. Setting it beforehand would make the
    # painter's own coordinate system logical, so the p.scale() above would
    # apply a second time and draw the glyph 4x oversized off the canvas.
    px.setDevicePixelRatio(scale)
    return px


def _stroke(p, c, w=1.7):
    """Shared stroke setup — every glyph uses the same weight and joins."""
    pen = QPen(c, w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _checker(p, s, c):
    """Page with a folded corner plus a tick — content verified."""
    _stroke(p, c)
    doc = QPainterPath()
    doc.moveTo(QPointF(s * 0.18, s * 0.10))
    doc.lineTo(QPointF(s * 0.55, s * 0.10))
    doc.lineTo(QPointF(s * 0.74, s * 0.29))
    doc.lineTo(QPointF(s * 0.74, s * 0.60))
    doc.lineTo(QPointF(s * 0.18, s * 0.60))
    doc.closeSubpath()
    p.drawPath(doc)
    fold = QPainterPath()
    fold.moveTo(QPointF(s * 0.55, s * 0.10))
    fold.lineTo(QPointF(s * 0.55, s * 0.29))
    fold.lineTo(QPointF(s * 0.74, s * 0.29))
    p.drawPath(fold)
    tick = QPainterPath()
    tick.moveTo(QPointF(s * 0.38, s * 0.74))
    tick.lineTo(QPointF(s * 0.54, s * 0.90))
    tick.lineTo(QPointF(s * 0.90, s * 0.54))
    _stroke(p, c, 2.1)
    p.drawPath(tick)


def _collect(p, s, c):
    """Two sheets folding down into one wider page."""
    _stroke(p, c)
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.10, s * 0.44, s * 0.16), 2, 2)
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.34, s * 0.44, s * 0.16), 2, 2)
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.62, s * 0.72, s * 0.28), 3, 3)
    _stroke(p, c, 1.9)
    p.drawLine(QPointF(s * 0.76, s * 0.14), QPointF(s * 0.76, s * 0.44))
    head = QPainterPath()
    head.moveTo(QPointF(s * 0.66, s * 0.34))
    head.lineTo(QPointF(s * 0.76, s * 0.46))
    head.lineTo(QPointF(s * 0.86, s * 0.34))
    p.drawPath(head)


def _spark(p, cx, cy, r):
    path = QPainterPath()
    path.moveTo(QPointF(cx, cy - r))
    path.quadTo(QPointF(cx + r * 0.2, cy - r * 0.2), QPointF(cx + r, cy))
    path.quadTo(QPointF(cx + r * 0.2, cy + r * 0.2), QPointF(cx, cy + r))
    path.quadTo(QPointF(cx - r * 0.2, cy + r * 0.2), QPointF(cx - r, cy))
    path.quadTo(QPointF(cx - r * 0.2, cy - r * 0.2), QPointF(cx, cy - r))
    p.drawPath(path)


def _restyle(p, s, c):
    """Wand and sparkles — an AI restyle."""
    _stroke(p, c, 2.0)
    p.drawLine(QPointF(s * 0.14, s * 0.88), QPointF(s * 0.58, s * 0.44))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    _spark(p, s * 0.72, s * 0.28, s * 0.21)
    _spark(p, s * 0.38, s * 0.18, s * 0.11)
    _spark(p, s * 0.88, s * 0.64, s * 0.10)


def _check(p, s, c):
    """Tick for QCheckBox::indicator:checked, which otherwise has no glyph."""
    _stroke(p, c, 2.4)
    path = QPainterPath()
    path.moveTo(QPointF(s * 0.20, s * 0.52))
    path.lineTo(QPointF(s * 0.42, s * 0.74))
    path.lineTo(QPointF(s * 0.80, s * 0.26))
    p.drawPath(path)


def _kaltura(p, s, c):
    """Film frame with sprocket holes and a play head."""
    _stroke(p, c)
    p.drawRoundedRect(QRectF(s * 0.10, s * 0.22, s * 0.80, s * 0.56), 3, 3)
    p.drawLine(QPointF(s * 0.10, s * 0.36), QPointF(s * 0.23, s * 0.36))
    p.drawLine(QPointF(s * 0.10, s * 0.64), QPointF(s * 0.23, s * 0.64))
    p.drawLine(QPointF(s * 0.77, s * 0.36), QPointF(s * 0.90, s * 0.36))
    p.drawLine(QPointF(s * 0.77, s * 0.64), QPointF(s * 0.90, s * 0.64))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.42, s * 0.38),
        QPointF(s * 0.42, s * 0.62),
        QPointF(s * 0.64, s * 0.50),
    ]))


def _h5p(p, s, c):
    """Puzzle piece — the shape already associated with H5P."""
    _stroke(p, c)
    path = QPainterPath()
    path.moveTo(QPointF(s * 0.14, s * 0.32))
    path.lineTo(QPointF(s * 0.38, s * 0.32))
    path.cubicTo(QPointF(s * 0.32, s * 0.12), QPointF(s * 0.64, s * 0.12),
                 QPointF(s * 0.58, s * 0.32))
    path.lineTo(QPointF(s * 0.84, s * 0.32))
    path.lineTo(QPointF(s * 0.84, s * 0.56))
    path.cubicTo(QPointF(s * 0.98, s * 0.50), QPointF(s * 0.98, s * 0.76),
                 QPointF(s * 0.84, s * 0.70))
    path.lineTo(QPointF(s * 0.84, s * 0.88))
    path.lineTo(QPointF(s * 0.14, s * 0.88))
    path.closeSubpath()
    p.drawPath(path)


def _settings(p, s, c):
    """Gear with a real centre hole — the old solid star blurred into a blob."""
    cx, cy = s / 2, s / 2
    outer, inner = s * 0.40, s * 0.27
    pts = []
    for i in range(16):
        angle = math.radians(i * 22.5 - 90)
        r = outer if i % 2 == 0 else inner
        pts.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
    ring = QPainterPath()
    ring.addPolygon(QPolygonF(pts))
    ring.closeSubpath()
    hole = QPainterPath()
    hole.addEllipse(QRectF(cx - s * 0.13, cy - s * 0.13, s * 0.26, s * 0.26))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPath(ring.subtracted(hole))


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


def _update(p, s, c):
    """Arrow descending into a tray — the universal 'update is ready'.

    Same 1.8px round-capped stroke as the sidebar glyphs so it does not read as
    a foreign element when parked in the corner of the window.
    """
    pen = QPen(c, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    cx = s / 2
    p.drawLine(QPointF(cx, s * 0.14), QPointF(cx, s * 0.58))
    head = QPainterPath()
    head.moveTo(QPointF(cx - s * 0.17, s * 0.39))
    head.lineTo(QPointF(cx, s * 0.58))
    head.lineTo(QPointF(cx + s * 0.17, s * 0.39))
    p.drawPath(head)

    tray = QPainterPath()
    tray.moveTo(QPointF(s * 0.20, s * 0.66))
    tray.lineTo(QPointF(s * 0.20, s * 0.84))
    tray.lineTo(QPointF(s * 0.80, s * 0.84))
    tray.lineTo(QPointF(s * 0.80, s * 0.66))
    p.drawPath(tray)


_FNS = {
    "checker": _checker, "collect": _collect, "restyle": _restyle,
    "kaltura": _kaltura, "h5p": _h5p,
    "settings": _settings, "run": _run, "next": _next_arrow,
    "done": _done, "locked": _locked, "running": _running,
    "update": _update, "check": _check,
}


def make_pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    return _make_pixmap(size, _FNS[name], color)


def make_icon(name: str, color: str, size: int = 16) -> QIcon:
    return QIcon(make_pixmap(name, color, size))


def write_png(name: str, color: str, size: int, path) -> bool:
    """Render a glyph to a PNG on disk.

    Qt stylesheets can only reference images by URL, so a QSS-driven glyph (the
    checkbox tick) has to exist as a file. Drawing it here keeps it in the same
    QPainter set as everything else instead of shipping a separate asset.
    """
    try:
        from pathlib import Path
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return make_pixmap(name, color, size).save(str(path), "PNG")
    except Exception:
        return False
