"""Fast, offline desktop startup validation shared by source and packaged builds."""

from __future__ import annotations

from dataclasses import dataclass

from ..app_paths import AppPaths
from ..storage import SQLiteStore
from .airport_catalog import AirportCatalog
from .browser_detector import BrowserDetector
from .route_repository import RouteRepository
from .settings_repository import SettingsRepository


@dataclass(frozen=True, slots=True)
class DesktopInitialization:
    airport_count: int
    enabled_route_count: int
    detected_browsers: tuple[str, ...]


def initialize_desktop(paths: AppPaths) -> DesktopInitialization:
    """Initialize and validate local state without importing Qt or opening a browser."""
    paths.initialize()
    catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
    routes = RouteRepository(paths.routes_path).load()
    settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
    settings.load_core()
    desktop_settings = settings.load_desktop()
    browsers = BrowserDetector().detect(preferred_path=desktop_settings.browser_path)
    SQLiteStore(paths.database_path).initialize()
    return DesktopInitialization(
        airport_count=len(catalog.records),
        enabled_route_count=sum(route.enabled for route in routes),
        detected_browsers=tuple(browser.kind for browser in browsers),
    )
