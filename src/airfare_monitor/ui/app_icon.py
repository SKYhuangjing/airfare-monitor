"""Application icon shared by the window, tray, shortcuts and executable."""

from __future__ import annotations

from PySide6.QtGui import QIcon

from .aircraft_assets import app_icon_pixmap


def application_icon() -> QIcon:
    """Return the light blue aircraft brand icon at common Windows sizes."""

    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon
