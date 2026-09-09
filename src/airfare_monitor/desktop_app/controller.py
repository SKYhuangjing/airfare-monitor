"""Application commands shared by desktop pages."""

from __future__ import annotations

from collections.abc import Callable

from ..config import MAX_ENABLED_LEGS
from ..models import LegConfig
from .route_repository import RouteRepository


class DesktopController:
    def __init__(self, routes: RouteRepository):
        self.routes = routes
        self._listeners: list[Callable[[list[LegConfig]], None]] = []

    def current_routes(self) -> list[LegConfig]:
        return self.routes.load()

    def on_routes_changed(self, listener: Callable[[list[LegConfig]], None]) -> None:
        self._listeners.append(listener)

    def save_route(self, route: LegConfig) -> None:
        current = self.current_routes()
        replaced = False
        updated: list[LegConfig] = []
        for existing in current:
            if existing.id == route.id:
                updated.append(route)
                replaced = True
            else:
                updated.append(existing)
        if not replaced:
            updated.append(route)
        self.routes.save(updated)
        self._emit(updated)

    def delete_route(self, route_id: str) -> None:
        updated = [route for route in self.current_routes() if route.id != route_id]
        self.routes.save(updated)
        self._emit(updated)

    def toggle_route(self, route_id: str, enabled: bool) -> None:
        current = self.current_routes()
        updated = [route if route.id != route_id else _with_enabled(route, enabled) for route in current]
        self.routes.save(updated)
        self._emit(updated)

    def enabled_capacity_remaining(self, *, excluding_id: str | None = None) -> int:
        used = sum(route.enabled and route.id != excluding_id for route in self.current_routes())
        return max(0, MAX_ENABLED_LEGS - used)

    def _emit(self, routes: list[LegConfig]) -> None:
        for listener in tuple(self._listeners):
            listener(routes)


def _with_enabled(route: LegConfig, enabled: bool) -> LegConfig:
    return LegConfig(
        id=route.id,
        enabled=enabled,
        origin_airport_iata=route.origin_airport_iata,
        destination_airport_iata=route.destination_airport_iata,
        departure_date=route.departure_date,
        etd_window=route.etd_window,
        direct_only=route.direct_only,
        expected_total_price_cny=route.expected_total_price_cny,
        top_n=route.top_n,
        adult_count=route.adult_count,
        child_count=route.child_count,
        cabin_class=route.cabin_class,
        origin_name_zh=route.origin_name_zh,
        destination_name_zh=route.destination_name_zh,
        preferred_schedules=route.preferred_schedules,
        market=route.market,
        max_layover_minutes=route.max_layover_minutes,
        return_date=route.return_date,
        return_etd_window=route.return_etd_window,
        return_direct_only=route.return_direct_only,
        return_max_layover_minutes=route.return_max_layover_minutes,
    )
