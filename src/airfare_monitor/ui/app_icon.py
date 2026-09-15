"""Code-rendered application icon shared by the window and system tray."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QPolygonF


BRAND_BLUE = QColor("#1769E0")
BRAND_WHITE = QColor("#FFFFFF")


def application_icon() -> QIcon:
    """Return a crisp blue-and-white aircraft icon at common Windows sizes."""

    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(_render_icon(size))
    return icon


def _render_icon(size: int) -> QPixmap:
    scale = size / 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(BRAND_BLUE)
    painter.drawRoundedRect(QRectF(2 * scale, 2 * scale, 60 * scale, 60 * scale), 14 * scale, 14 * scale)

    # A deliberately broad silhouette remains recognisable in Windows' 16 px tray.
    points = (
        (7, 27),
        (24, 26),
        (34, 9),
        (42, 9),
        (37, 26),
        (51, 25),
        (59, 29),
        (59, 35),
        (51, 39),
        (37, 38),
        (42, 55),
        (34, 55),
        (24, 38),
        (7, 37),
    )
    aircraft = QPolygonF([QPointF(x * scale, y * scale) for x, y in points])
    painter.setBrush(BRAND_WHITE)
    painter.drawPolygon(aircraft)

    # Separate tail fins give the small silhouette a more obvious aircraft shape.
    tail = QPainterPath()
    tail.moveTo(12 * scale, 27 * scale)
    tail.lineTo(7 * scale, 18 * scale)
    tail.lineTo(14 * scale, 18 * scale)
    tail.lineTo(21 * scale, 27 * scale)
    tail.closeSubpath()
    painter.drawPath(tail)
    painter.end()
    return pixmap
