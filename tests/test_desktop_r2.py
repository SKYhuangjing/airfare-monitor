from __future__ import annotations

import os
import unittest
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.desktop_app.airport_catalog import AirportCatalog
from airfare_monitor.desktop_app.controller import DesktopController
from airfare_monitor.desktop_app.event_journal import AppEventJournal
from airfare_monitor.desktop_app.events import FatalError
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.desktop_app.view_data import load_dashboard_data
from airfare_monitor.models import EtdWindow, LegConfig
from airfare_monitor.storage import SQLiteStore
from airfare_monitor.ui.app_icon import application_icon
from airfare_monitor.ui.history_page import HistoryPage, price_segments
from airfare_monitor.ui.route_wizard import RouteWizard


ROOT = Path(__file__).resolve().parents[1]


class DesktopR2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_price_chart_breaks_the_line_at_failed_queries(self):
        rows = [
            {"status": "success", "minimum_total_price_cny": "1500"},
            {"status": "success", "minimum_total_price_cny": "1450"},
            {"status": "failed", "minimum_total_price_cny": None},
            {"status": "success", "minimum_total_price_cny": "1420"},
            {"status": "manual_attention", "minimum_total_price_cny": None},
        ]
        self.assertEqual(
            price_segments(rows),
            [[(0, Decimal("1500")), (1, Decimal("1450"))], [(3, Decimal("1420"))]],
        )

    def test_application_icon_contains_branded_tray_sizes(self):
        icon = application_icon()
        self.assertFalse(icon.isNull())
        available = {(size.width(), size.height()) for size in icon.availableSizes()}
        self.assertIn((16, 16), available)
        self.assertIn((32, 32), available)
        pixmap = icon.pixmap(64, 64)
        self.assertFalse(pixmap.isNull())
        self.assertGreater(pixmap.toImage().pixelColor(32, 32).lightness(), 200)

    def test_dashboard_data_uses_persisted_cny_totals_and_redacted_events(self):
        route = _route("route-1")
        store = _FakeDashboardStore()
        data = load_dashboard_data(store, [route], now=datetime(2026, 9, 14, 18))
        self.assertEqual(data.today_minimum_cny, Decimal("1380"))
        self.assertEqual(data.routes[route.id].minimum_total_cny, Decimal("1420"))
        self.assertEqual(data.routes[route.id].change_cny, Decimal("-60"))
        self.assertEqual(data.attention_count, 0)
        self.assertEqual(data.latest_run_duration_seconds, 75)

    def test_event_journal_does_not_persist_raw_fatal_error_text(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "monitor.sqlite3")
            journal = AppEventJournal(store)
            journal.initialize()
            journal.record(FatalError("CollectionError", "session_parameter=sensitive-marker"))
            events = store.recent_app_events()
            self.assertEqual(len(events), 1)
            self.assertNotIn("sensitive-marker", events[0]["message"])
            self.assertEqual(events[0]["severity"], "error")

    def test_route_wizard_saves_period_passengers_cabin_and_paused_choice(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            controller = DesktopController(RouteRepository(paths.routes_path))
            catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
            wizard = RouteWizard(catalog, controller)
            wizard.origin_picker.set_record(catalog.by_iata("PVG"))
            wizard.destination_picker.set_record(catalog.by_iata("KUL"))
            wizard.departure_period.setCurrentIndex(2)
            wizard.adult_count.setValue(2)
            wizard.child_count.setValue(1)
            wizard.cabin_class.setCurrentIndex(wizard.cabin_class.findData("business"))
            wizard.stack.setCurrentIndex(2)
            wizard._update_step()
            self.assertEqual(wizard.next_button.text(), "保存并开始监控")
            self.assertFalse(wizard.save_paused_button.isHidden())
            route = wizard._build_route(force_enabled=False)
            self.assertEqual(route.etd_window.start, time(12, 0))
            self.assertEqual(route.etd_window.end, time(18, 0))
            self.assertEqual(route.adult_count, 2)
            self.assertEqual(route.child_count, 1)
            self.assertEqual(route.cabin_class, "business")
            self.assertFalse(route.enabled)
            wizard.close()

    def test_history_page_restores_routes_without_querying_a_website(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "monitor.sqlite3")
            store.initialize()
            page = HistoryPage(store, open_latest_report=lambda: None, outputs_dir=Path(temp))
            page.refresh([_route("route-1")])
            self.assertEqual(page.route_combo.count(), 1)
            self.assertEqual(page.records.rowCount(), 0)
            page.close()


class _FakeDashboardStore:
    def latest_leg_results(self, leg_ids):
        return [{
            "leg_id": "route-1",
            "status": "success",
            "minimum_total_price_cny": "1420",
            "previous_min_total_cny": "1480",
            "captured_at": "2026-09-14T17:30:00",
        }]

    def history(self, *, since):
        return [
            {"leg_id": "route-1", "status": "success", "minimum_total_price_cny": "1380"},
            {"leg_id": "route-1", "status": "success", "minimum_total_price_cny": "1420"},
        ]

    def latest_successful_run(self):
        return {"finished_at": "2026-09-14T17:31:15"}

    def latest_run(self):
        return {
            "started_at": "2026-09-14T17:30:00",
            "finished_at": "2026-09-14T17:31:15",
        }

    def recent_app_events(self, *, limit):
        return [{"occurred_at": "2026-09-14T17:31:15", "message": "本轮查询完成"}]


def _route(identifier: str) -> LegConfig:
    return LegConfig(
        id=identifier,
        enabled=True,
        origin_airport_iata="PVG",
        destination_airport_iata="KUL",
        departure_date=date(2026, 10, 1),
        etd_window=EtdWindow(time(0, 0), time(23, 59)),
        direct_only=True,
        expected_total_price_cny=Decimal("1500"),
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
        origin_name_zh="上海浦东",
        destination_name_zh="吉隆坡",
    )


def _paths(root: str) -> AppPaths:
    discovered = AppPaths.discover(user_root=root)
    fields = {name: getattr(discovered, name) for name in discovered.__dataclass_fields__}
    fields["resource_root"] = ROOT / "resources"
    return AppPaths(**fields)
