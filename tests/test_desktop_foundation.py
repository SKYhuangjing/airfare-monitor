from __future__ import annotations

import json
import unittest
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.config import MAX_ENABLED_LEGS
from airfare_monitor.desktop_app.airport_catalog import AirportCatalog, AirportCatalogError
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.desktop import main as desktop_main
from airfare_monitor.errors import ConfigError
from airfare_monitor.models import EtdWindow, LegConfig


ROOT = Path(__file__).resolve().parents[1]


class DesktopFoundationTests(unittest.TestCase):
    def test_bundled_catalog_supports_multi_airport_city_and_pinyin(self):
        catalog = AirportCatalog.load(ROOT / "resources" / "airports.zh.json")
        self.assertEqual(catalog.version, 1)
        self.assertEqual({item.airport_iata for item in catalog.search("上海")}, {"SHA", "PVG"})
        self.assertEqual({item.airport_iata for item in catalog.search("tokyo")}, {"HND", "NRT"})
        self.assertEqual(catalog.search("PVG")[0].city_name_zh, "上海")

    def test_catalog_rejects_country_mismatch(self):
        payload = {
            "catalog_version": 1,
            "airports": [
                {
                    "airport_iata": "PVG",
                    "display_name_zh": "上海浦东",
                    "city_name_zh": "上海",
                    "country_code": "US",
                    "aliases": ["上海"],
                    "supported_sources": ["tongcheng"],
                }
            ],
        }
        with TemporaryDirectory() as temp:
            path = Path(temp) / "airports.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(AirportCatalogError):
                AirportCatalog.load(path)

    def test_paths_initialize_without_overwriting_existing_config(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            paths.routes_path.write_text("legs: []\n# user change\n", encoding="utf-8")
            paths.initialize()
            self.assertIn("user change", paths.routes_path.read_text(encoding="utf-8"))
            self.assertEqual(paths.browser_profile, paths.user_root / "data" / "browser-profile")

    def test_route_repository_preserves_old_config_when_limit_validation_fails(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            repository = RouteRepository(paths.routes_path)
            repository.save([_leg("route-1")])
            original = paths.routes_path.read_text(encoding="utf-8")
            with self.assertRaises(ConfigError):
                repository.save([_leg(f"route-{index}") for index in range(MAX_ENABLED_LEGS + 1)])
            self.assertEqual(paths.routes_path.read_text(encoding="utf-8"), original)
            self.assertEqual([route.id for route in repository.load()], ["route-1"])

    def test_desktop_smoke_test_initializes_local_state_without_starting_browser(self):
        with TemporaryDirectory() as temp:
            user_root = Path(temp) / "user"
            report_path = Path(temp) / "smoke.json"
            exit_code = desktop_main(
                ["--smoke-test", f"--user-root={user_root}", f"--smoke-report={report_path}"]
            )
            self.assertEqual(exit_code, 0)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "ok")
            self.assertGreaterEqual(payload["airport_count"], 60)
            self.assertTrue((user_root / "config" / "routes.yaml").is_file())
            self.assertTrue((user_root / "data" / "airfare-monitor.sqlite3").is_file())


def _paths(root: str) -> AppPaths:
    discovered = AppPaths.discover(user_root=root)
    fields = {name: getattr(discovered, name) for name in discovered.__dataclass_fields__}
    fields["resource_root"] = ROOT / "resources"
    return AppPaths(**fields)


def _leg(identifier: str) -> LegConfig:
    return LegConfig(
        id=identifier,
        enabled=True,
        origin_airport_iata="SHA",
        destination_airport_iata="XMN",
        departure_date=date(2026, 10, 1),
        etd_window=EtdWindow(time(0, 0), time(23, 59)),
        direct_only=True,
        expected_total_price_cny=None,
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
        origin_name_zh="上海",
        destination_name_zh="厦门",
    )
