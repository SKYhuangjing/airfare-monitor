"""Date-picker usability and domestic return-trip guidance, without website access."""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QApplication, QCalendarWidget, QPushButton

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.desktop_app.airport_catalog import AirportCatalog
from airfare_monitor.desktop_app.controller import DesktopController
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.ui.route_wizard import RouteWizard


class DateAndDomesticGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _wizard(self, temp: str) -> RouteWizard:
        paths = AppPaths.discover(user_root=temp)
        paths.initialize()
        return RouteWizard(
            AirportCatalog.load(paths.resource_root / "airports.zh.json"),
            DesktopController(RouteRepository(paths.routes_path)),
        )

    def test_large_calendar_button_selects_date_in_one_click(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            wizard = self._wizard(temp)
            button = wizard.departure_date_field.findChild(QPushButton, "datePickerButton")
            self.assertIsNotNone(button)
            self.assertGreaterEqual(button.minimumWidth(), 100)
            self.assertEqual(wizard.departure_date.displayFormat(), "yyyy-MM-dd")
            chosen = QDate.currentDate().addDays(3)
            dialog_sizes: list[tuple[int, int]] = []

            def pick() -> None:
                dialog = QApplication.activeModalWidget()
                self.assertIsNotNone(dialog)
                dialog_sizes.append((dialog.minimumWidth(), dialog.minimumHeight()))
                calendar = dialog.findChild(QCalendarWidget, "datePickerCalendar")
                self.assertIsNotNone(calendar)
                calendar.clicked.emit(chosen)

            QTimer.singleShot(0, pick)
            button.click()
            self.assertEqual(dialog_sizes, [(430, 370)])
            self.assertEqual(wizard.departure_date.date(), chosen)
            self.assertEqual(wizard.return_date.minimumDate(), chosen.addDays(1))
            wizard.close()

    def test_domestic_round_trip_is_disabled_with_reverse_route_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            wizard = self._wizard(temp)
            wizard.origin_picker.set_record(wizard.catalog.by_iata("PVG"))
            wizard.destination_picker.set_record(wizard.catalog.by_iata("SYX"))
            wizard._update_capability()
            self.assertFalse(wizard.trip_type.model().item(1).isEnabled())
            self.assertEqual(wizard.trip_type.currentIndex(), 0)
            self.assertFalse(wizard.domestic_roundtrip_hint.isHidden())
            self.assertIn("SYX → PVG", wizard.domestic_roundtrip_hint.text())
            self.assertIn("单独选择返程日期", wizard.domestic_roundtrip_hint.text())
            self.assertTrue(wizard.return_date_field.isHidden())
            wizard._refresh_summary()
            self.assertIn("SYX → PVG", wizard.summary.text())
            wizard.destination_picker.set_record(wizard.catalog.by_iata("KUL"))
            wizard._update_capability()
            self.assertTrue(wizard.trip_type.model().item(1).isEnabled())
            self.assertTrue(wizard.domestic_roundtrip_hint.isHidden())
            wizard.close()


if __name__ == "__main__":
    unittest.main()
