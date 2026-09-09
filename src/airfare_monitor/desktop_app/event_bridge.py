"""Thread-safe delivery of coordinator events into the Qt UI thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class CoordinatorEventBridge(QObject):
    event_received = Signal(object)

    def publish(self, event: object) -> None:
        self.event_received.emit(event)
