"""Shared aircraft artwork used by every desktop UI surface."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

from ..app_paths import AppPaths


@lru_cache(maxsize=2)
def _master_pixmap(filename: str) -> QPixmap:
    source = AppPaths.discover().resource_root / filename
    pixmap = QPixmap(str(source))
    if pixmap.isNull():
        raise FileNotFoundError(f"缺少飞机图标资源：{source}")
    return pixmap


def aircraft_mark_pixmap(
    size: int,
    *,
    background: str = "transparent",
    circular_background: bool = False,
) -> QPixmap:
    """Render the single slim gradient aircraft mark used throughout the UI."""

    target = QPixmap(size, size)
    target.fill(Qt.GlobalColor.transparent)
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    if background != "transparent":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        bounds = QRectF(1, 1, size - 2, size - 2)
        if circular_background:
            painter.drawEllipse(bounds)
        else:
            radius = max(4.0, size * 0.28)
            painter.drawRoundedRect(bounds, radius, radius)

    # The generated master already contains transparent breathing room. Keeping
    # it intact makes the aircraft feel lighter and prevents it from crowding
    # 16–46 px badges.
    source = _master_pixmap("aircraft-mark.png")
    painter.drawPixmap(QRectF(0, 0, size, size), source, QRectF(source.rect()))
    painter.end()
    return target


def app_icon_pixmap(size: int) -> QPixmap:
    """Return the polished rounded-square application icon at ``size``."""

    source = _master_pixmap("app-icon-master.png")
    return source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
