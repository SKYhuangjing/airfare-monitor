"""Per-Windows-user single-instance activation using Qt local IPC."""

from __future__ import annotations

import getpass
from collections.abc import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance:
    def __init__(self) -> None:
        self.name = f"AirfareMonitor-{getpass.getuser()}"
        self.server = QLocalServer()
        self._on_activate: Callable[[], None] | None = None
        self._activation_pending = False

    def acquire(self) -> bool:
        client = QLocalSocket()
        client.connectToServer(self.name)
        if client.waitForConnected(250):
            client.write(b"activate")
            client.flush()
            client.waitForBytesWritten(250)
            client.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            return False
        self.server.newConnection.connect(self._accept_connections)
        return True

    def set_activation_handler(self, on_activate: Callable[[], None]) -> None:
        self._on_activate = on_activate
        if self._activation_pending:
            self._activation_pending = False
            on_activate()

    def _accept_connections(self) -> None:
        received_activation = False
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                break
            socket.waitForReadyRead(100)
            socket.readAll()
            socket.disconnectFromServer()
            socket.deleteLater()
            received_activation = True
        if not received_activation:
            return
        if self._on_activate is None:
            self._activation_pending = True
        else:
            self._on_activate()

    def close(self) -> None:
        self.server.close()
        QLocalServer.removeServer(self.name)
