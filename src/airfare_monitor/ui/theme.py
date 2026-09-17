"""Small visual primitives shared by the desktop shell.

This module intentionally owns presentation only.  Keeping the decorative
background outside the page implementations means the monitoring pages retain
their existing signals, data flow, and test seams.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QStackedWidget, QWidget

from ..app_paths import AppPaths


class AviationPageStack(QStackedWidget):
    """A stacked page host with a subtle, edge-weighted aviation backdrop."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("contentPages")
        source = AppPaths.discover().resource_root / "aviation-background.png"
        self._source = QPixmap(str(source)) if source.is_file() else QPixmap()
        self._scaled = QPixmap()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._scaled = (
            self._source.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            if not self._source.isNull()
            else QPixmap()
        )
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f4f8fe"))
        if not self._scaled.isNull():
            x = (self.width() - self._scaled.width()) // 2
            y = (self.height() - self._scaled.height()) // 2
            painter.setOpacity(0.72)
            painter.drawPixmap(x, y, self._scaled)
        # A shared brand line belongs to the background rather than any one
        # page, so it remains present while navigating between all main views.
        painter.setOpacity(0.82)
        painter.setPen(QColor("#426a9f"))
        motto_font = QFont("KaiTi", 12)
        motto_font.setFamilies(["STXingkai", "Kaiti SC", "Kaiti TC", "KaiTi", "Microsoft YaHei UI", "PingFang SC"])
        motto_font.setItalic(True)
        painter.setFont(motto_font)
        motto_width = 260
        painter.drawText(
            (self.width() - motto_width) // 2,
            17,
            motto_width,
            54,
            Qt.AlignmentFlag.AlignCenter,
            "探索世界\n从一张好机票开始",
        )
        painter.end()
