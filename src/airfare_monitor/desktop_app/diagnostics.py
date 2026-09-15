"""Small, intentionally low-detail support bundle without personal data."""

from __future__ import annotations

from datetime import datetime
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from .. import __version__
from ..storage import SQLiteStore
from .settings_repository import DesktopSettings
from .safe_logging import redact_text


_SENSITIVE = re.compile(
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|https?://\S+|"
    r"\b(?:password|passwd|smtp_secret|authorization|cookie|token|bella|queryid)\s*[:=]\s*(?!\[redacted\])\S+|"
    r"\bBearer\s+(?!\[redacted\])\S+",
    re.IGNORECASE,
)


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


def export_diagnostic_zip(
    destination: Path,
    settings: DesktopSettings,
    store: SQLiteStore | None,
    *,
    enabled_routes: int,
    logs_dir: Path,
    catalog_version: int,
    browser_version: str | None = None,
) -> None:
    """Export small redacted metadata/log snippets, never configs, DB or Profile."""
    payload = build_diagnostics(settings, store, enabled_routes=enabled_routes)
    payload.update({
        "windows_version": platform.version() if platform.system() == "Windows" else platform.system(),
        "python_version": platform.python_version(),
        "packaged_exe": bool(getattr(sys, "frozen", False)),
        "browser_version": browser_version,
        "database_schema_version": _database_version(store),
        "airport_catalog_version": catalog_version,
    })
    items = {"diagnostics.json": json.dumps(payload, ensure_ascii=False, indent=2)}
    for name in ("app.log", "collection.log", "startup.log"):
        path = logs_dir / name
        if path.is_file():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 100_000))
                recent = stream.read().decode("utf-8", errors="replace")
            items[f"logs/{name}"] = "\n".join(redact_text(line) for line in recent.splitlines()[-120:])
    for name, content in items.items():
        if _SENSITIVE.search(content):
            raise ValueError(f"诊断导出发现未脱敏字段：{name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in items.items():
                archive.writestr(name, content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _database_version(store: SQLiteStore | None) -> int | None:
    if store is None or not store.path.is_file():
        return None
    import sqlite3

    with sqlite3.connect(f"file:{store.path.as_posix()}?mode=ro", uri=True) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
