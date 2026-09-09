from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ...desktop_app.airport_catalog import AirportCatalog, AirportRecord


class AirportPicker(QWidget):
    selected_changed = Signal(object)

    def __init__(self, catalog: AirportCatalog, parent: QWidget | None = None):
        super().__init__(parent)
        self.catalog = catalog
        self.selected: AirportRecord | None = None
        self.input = QLineEdit(placeholderText="搜索机场、城市或 IATA")
        self.results = QListWidget()
        self.results.setMaximumHeight(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.input)
        layout.addWidget(self.results)
        self.input.textChanged.connect(self._search)
        self.results.itemClicked.connect(self._select_item)
        self._search("")

    def set_record(self, record: AirportRecord | None) -> None:
        self.selected = record
        if record is not None:
            self.input.setText(record.display_text)
            self.results.hide()
        else:
            self.input.clear()

    def _search(self, text: str) -> None:
        if self.selected is not None and text != self.selected.display_text:
            self.selected = None
            self.selected_changed.emit(None)
        self.results.clear()
        for record in self.catalog.search(text):
            item = QListWidgetItem(f"{record.display_text}  ·  {record.airport_name_zh or record.city_name_zh}")
            item.setData(256, record)
            self.results.addItem(item)
        self.results.setVisible(self.results.count() > 0 and self.input.hasFocus())

    def _select_item(self, item: QListWidgetItem) -> None:
        record = item.data(256)
        self.selected = record
        self.input.blockSignals(True)
        self.input.setText(record.display_text)
        self.input.blockSignals(False)
        self.results.hide()
        self.selected_changed.emit(record)
