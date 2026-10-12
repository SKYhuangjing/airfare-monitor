"""Date-picker usability and domestic return-trip guidance, without website access."""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QTime, QTimer
from PySide6.QtWidgets import QApplication, QCalendarWidget, QLabel, QPushButton

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.desktop_app.airport_catalog import AirportCatalog
from airfare_monitor.desktop_app.controller import DesktopController
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.ui.route_wizard import RouteWizard, StepperSpinBox, TimeComboBox


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

    def test_roundtrip_fields_scroll_in_small_styled_window(self) -> None:
        from airfare_monitor.desktop_app.application import _apply_style
        previous_style = self.app.styleSheet()
        with tempfile.TemporaryDirectory() as temp:
            wizard = self._wizard(temp)
            try:
                _apply_style(self.app, AppPaths.discover(user_root=temp).resource_root)
                wizard.origin_picker.set_record(wizard.catalog.by_iata("PVG"))
                wizard.destination_picker.set_record(wizard.catalog.by_iata("KUL"))
                wizard.trip_type.setCurrentIndex(1)
                wizard.resize(940, 690)
                wizard.show()
                self.app.processEvents()
                scroll = wizard.route_fields_scroll
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                self.assertTrue(wizard.departure_date_field.rect().contains(wizard.departure_date.geometry()))
                scroll.ensureWidgetVisible(wizard.return_end_time)
                self.app.processEvents()
                position = wizard.return_end_time.mapTo(scroll.viewport(), wizard.return_end_time.rect().bottomLeft())
                self.assertLess(position.y(), scroll.viewport().height())
            finally:
                wizard.close()
                self.app.setStyleSheet(previous_style)

    def test_refreshed_time_and_stepper_controls_preserve_values(self) -> None:
        time_picker = TimeComboBox(QTime(0, 0))
        time_picker.resize(180, 44)
        self.assertTrue(time_picker.drop_button.isVisibleTo(time_picker))
        self.assertGreaterEqual(time_picker.drop_button.width(), 30)
        time_picker.setEditText("0:08")
        self.assertEqual(time_picker.time(), QTime(0, 8))
        time_picker.setTime(QTime(23, 59))
        self.assertEqual(time_picker.currentText(), "23:59")

        stepper = StepperSpinBox()
        stepper.setRange(0, 8)
        stepper.setValue(2)
        stepper.up.click()
        self.assertEqual(stepper.value(), 3)
        stepper.down.click()
        self.assertEqual(stepper.value(), 2)

    def test_query_provider_can_be_saved_reloaded_and_limits_incompatible_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            wizard = self._wizard(temp)
            wizard.origin_picker.set_record(wizard.catalog.by_iata("PVG"))
            jeju = wizard.catalog.by_iata("CJU")
            self.assertIsNotNone(jeju)
            self.assertIn("ctrip", jeju.supported_sources)
            wizard.destination_picker.set_record(jeju)
            wizard._update_capability()
            self.assertIn("携程", wizard.preview_source.text())
            wizard.provider.setCurrentIndex(wizard.provider.findData("qunar"))
            route = wizard._build_route()
            repo = RouteRepository(AppPaths.discover(user_root=temp).routes_path)
            repo.save([route])
            wizard._load_route(repo.load()[0])
            self.assertEqual(wizard.provider.currentData(), "qunar")
            self.assertIn("去哪儿", wizard.preview_source.text())
            wizard.trip_type.setCurrentIndex(1)
            wizard._update_capability()
            self.assertTrue(wizard.provider.model().item(wizard.provider.findData("ctrip")).isEnabled())
            wizard.provider.setCurrentIndex(wizard.provider.findData("ctrip"))
            wizard._load_route(wizard._build_route())
            self.assertEqual(wizard.trip_type.currentIndex(), 1)
            self.assertEqual(wizard.provider.currentData(), "ctrip")
            wizard.destination_picker.set_record(wizard.catalog.by_iata("SYX"))
            wizard._update_capability()
            self.assertEqual(wizard.provider.currentData(), "auto")
            self.assertIn("同程", wizard.preview_source.text())
            wizard.close()

    def test_route_wizard_includes_brand_motto(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            wizard = self._wizard(temp)
            motto = wizard.findChild(QLabel, "brandMotto")
            self.assertIsNotNone(motto)
            self.assertEqual(motto.text(), "探索世界\n从一张好机票开始")
            wizard.close()


if __name__ == "__main__":
    unittest.main()
