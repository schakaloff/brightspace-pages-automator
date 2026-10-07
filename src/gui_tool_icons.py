"""Theme-aware rendering of the bundled Tabler outline icons."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


# Existing path indices in the pinned source SVGs; only colours are changed.
_ACCENTS = {"file-plus": (2, 3), "file-pencil": (2,), "trash": (1, 2)}


def tool_pixmap(name: str, size: int, color: str, accent: str = "") -> QPixmap:
    root_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    source = root_path / "assets" / "ui" / "tabler" / f"{name}.svg"
    svg = ET.fromstring(source.read_bytes())
    svg.set("stroke", color)
    if accent:
        paths = svg.findall("{http://www.w3.org/2000/svg}path")
        for index in _ACCENTS.get(name, ()):
            paths[index].set("stroke", accent)
    renderer = QSvgRenderer(QByteArray(ET.tostring(svg)))
    if not renderer.isValid():
        raise ValueError(f"Invalid tool icon: {source.name}")
    pixmap = QPixmap(size * 4, size * 4)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(4)
    return pixmap
