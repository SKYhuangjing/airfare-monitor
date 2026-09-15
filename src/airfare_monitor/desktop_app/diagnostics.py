"""Small, intentionally low-detail support bundle without personal data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .. import __version__
from ..storage import SQLiteStore
from .settings_repository import DesktopSettings


def build_diagnostics(
    settings: DesktopSettings,
    store: SQLiteStore | None,
    *,
    enabled_routes: int,
) -> dict[str, Any]:
    """Avoid URLs, paths, route IDs, emails, cookies and exception messages."""

    latest = store.latest_run() if store is not None else None
    events = store.recent_app_events(limit=30) if store is not None else []
    return {
        "product": "航价守望",
        "version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "browser_kind": settings.browser_kind,
        "browser_visible": settings.show_browser,
        "interval_minutes": settings.interval_minutes,
        "desktop_notifications": settings.desktop_notifications,
        "enabled_route_count": enabled_routes,
        "latest_run": {
            "status": str(latest.get("status") or "unknown"),
            "finished_at": str(latest.get("finished_at") or ""),
        } if latest else None,
        "recent_events": [
            {
                "occurred_at": str(item.get("occurred_at") or ""),
                "event_type": str(item.get("event_type") or ""),
                "severity": str(item.get("severity") or ""),
            }
            for item in events
        ],
    }
