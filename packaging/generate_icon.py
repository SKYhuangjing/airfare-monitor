"""Build a Windows ICO from the application's code-rendered Qt icon."""

from __future__ import annotations

import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from airfare_monitor.ui.app_icon import application_icon


def generate_icon(destination: Path) -> None:
    app = QApplication.instance() or QApplication([])
    icon = application_icon()
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images: list[tuple[int, bytes]] = []
    for size in sizes:
        data = QBuffer()
        data.open(QIODevice.OpenModeFlag.WriteOnly)
        if not icon.pixmap(size, size).save(data, "PNG"):
            raise RuntimeError(f"无法生成 {size}px 图标")
        images.append((size, bytes(data.data())))
        data.close()
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + len(images) * 16
    entries = bytearray()
    payload = bytearray()
    for size, image in images:
        entries += struct.pack(
            "<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0,
            0, 0, 1, 32, len(image), offset,
        )
        payload += image
        offset += len(image)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(header + entries + payload)
    _ = app


if __name__ == "__main__":
    generate_icon(Path(__file__).resolve().parents[1] / "resources" / "app.ico")
