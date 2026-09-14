"""Persist a small, redacted activity journal for the desktop overview."""

from __future__ import annotations

from datetime import datetime

from ..storage import SQLiteStore
from .events import (
    CycleFinished,
    CycleStarted,
    FatalError,
    LegFinished,
    ManualAttentionRequested,
)


class AppEventJournal:
    """Translate worker events into stable, user-facing and non-sensitive records."""

    def __init__(self, store: SQLiteStore):
        self.store = store

    def initialize(self) -> None:
        self.store.initialize()
        self.store.prune_app_events(30)

    def record(self, event: object) -> None:
        payload = _event_payload(event)
        if payload is None:
            return
        event_type, severity, message, leg_id, occurred_at = payload
        self.store.record_app_event(
            event_type=event_type,
            severity=severity,
            message=message,
            leg_id=leg_id,
            occurred_at=occurred_at,
        )

    def record_settings_changed(self) -> None:
        self.store.record_app_event(
            event_type="settings_changed",
            severity="info",
            message="运行设置已更新，将从下一轮查询开始生效",
        )

    def record_routes_changed(self, enabled_count: int) -> None:
        self.store.record_app_event(
            event_type="routes_changed",
            severity="info",
            message=f"航程配置已更新，当前启用 {enabled_count}/10 条",
        )


def _event_payload(
    event: object,
) -> tuple[str, str, str, str | None, datetime | None] | None:
    if isinstance(event, CycleStarted):
        return (
            "cycle_started",
            "info",
            f"开始串行查询 {event.total_legs} 条航程",
            None,
            event.started_at,
        )
    if isinstance(event, LegFinished):
        route = (
            f"{event.result.leg.origin_airport_iata} → "
            f"{event.result.leg.destination_airport_iata}"
        )
        status = event.result.status.value
        if status == "failed":
            return "leg_failed", "warning", f"{route} 查询失败，本次价格未采用", event.result.leg.id, None
        if status == "manual_attention":
            # ManualAttentionRequested records the single public attention message.
            return None
        return None
    if isinstance(event, ManualAttentionRequested):
        return (
            "manual_attention",
            "warning",
            f"航程 {event.leg_id} 需要人工完成页面确认",
            event.leg_id,
            None,
        )
    if isinstance(event, CycleFinished):
        succeeded = sum(item.status.value == "success" for item in event.report.legs)
        total = event.total_legs or len(event.report.legs)
        duration = max(0, int((event.report.finished_at - event.report.started_at).total_seconds()))
        severity = "info" if succeeded == total else "warning"
        return (
            "cycle_finished",
            severity,
            f"本轮查询完成：成功 {succeeded}/{total}，耗时 {duration} 秒",
            None,
            event.report.finished_at,
        )
    if isinstance(event, FatalError):
        # Never persist raw exception text; it can include URLs or browser details.
        return "fatal_error", "error", "监控运行异常，请打开系统状态检查", None, None
    return None
