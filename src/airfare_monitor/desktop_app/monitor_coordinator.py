"""One-worker, serial desktop scheduler around the existing monitoring core."""

from __future__ import annotations

import random
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from ..app_paths import AppPaths
from ..config import load_routes, load_settings
from ..models import LegConfig, LegResult, RunReport
from ..service import MonitorEventSink, MonitorService
from .events import (
    CoordinatorStateChanged, CycleFinished, CycleStarted, FatalError, LegFinished, LegStarted,
    ManualAttentionRequested, NextRunScheduled,
)


class MonitorCoordinator:
    """Runs at most one complete collection cycle at a time in one background thread."""

    def __init__(self, paths: AppPaths):
        self.paths = paths
        self._listeners: list[Callable[[object], None]] = []
        self._wake = threading.Event()
        self._shutdown = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "IDLE"

    def subscribe(self, listener: Callable[[object], None]) -> None:
        self._listeners.append(listener)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._run, name="airfare-monitor-worker", daemon=True)
        self._thread.start()

    def run_now(self) -> None:
        self._paused.clear()
        self._wake.set()

    def pause(self) -> None:
        self._paused.set()
        self._emit(CoordinatorStateChanged("PAUSED", "当前轮次结束后暂停监控"))
        self._wake.set()

    def resume(self) -> None:
        self._paused.clear()
        self._wake.set()

    def shutdown(self, *, timeout: float = 15) -> None:
        self._shutdown.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        immediate = True
        while not self._shutdown.is_set():
            if self._paused.is_set():
                self._set_state("PAUSED", "监控已暂停")
                self._wake.wait()
                self._wake.clear()
                immediate = True
                continue
            if not immediate:
                self._wake.wait()
                self._wake.clear()
                if self._shutdown.is_set():
                    break
                if self._paused.is_set():
                    continue
            immediate = False
            started_at = datetime.now()
            try:
                legs = load_routes(self.paths.routes_path, allow_empty=True)
                if not any(leg.enabled for leg in legs):
                    self._set_state("IDLE", "请先添加并启用至少一条航程")
                    self._wake.wait()
                    self._wake.clear()
                    immediate = True
                    continue
                settings = load_settings(self.paths.settings_path, project_root=self.paths.user_root)
                enabled_count = sum(leg.enabled for leg in legs)
                self._set_state("RUNNING", f"正在串行查询 {enabled_count} 条航程")
                service = MonitorService(
                    legs,
                    settings,
                    event_sink=_CoordinatorSink(self, started_at, enabled_count),
                    sleep=lambda seconds: self._wake.wait(seconds),
                )
                report, workbook = service.run_once(send_email=settings.mail.enabled)
                service.close()
                self._emit(CycleFinished(report, str(workbook)))
                if any(result.status.value == "manual_attention" for result in report.legs):
                    self._set_state("ATTENTION", "部分航程需要人工处理")
                else:
                    self._set_state("IDLE", "本轮查询完成")
                base_due = started_at + timedelta(minutes=settings.schedule.interval_minutes)
                cooldown_due = report.finished_at + timedelta(minutes=5)
                due_at = max(base_due, cooldown_due) + timedelta(seconds=random.uniform(0, settings.schedule.jitter_seconds))
                self._emit(NextRunScheduled(due_at))
                seconds = max(0.0, (due_at - datetime.now()).total_seconds())
                self._wake.wait(seconds)
                self._wake.clear()
            except Exception as exc:
                self._set_state("ERROR", "监控未能启动；请查看系统状态")
                self._emit(FatalError(type(exc).__name__, str(exc)))
                self._wake.wait(60)
                self._wake.clear()

    def _set_state(self, state: str, message: str) -> None:
        self._state = state
        self._emit(CoordinatorStateChanged(state, message))

    def _emit(self, event: object) -> None:
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                continue


class _CoordinatorSink(MonitorEventSink):
    def __init__(self, coordinator: MonitorCoordinator, started_at: datetime, total: int):
        self.coordinator = coordinator
        self.started_at = started_at
        self.total = total
        self.run_id = "pending"

    def on_cycle_started(self, run_id: str, started_at: datetime, total: int) -> None:
        self.run_id = run_id
        self.coordinator._emit(CycleStarted(run_id, started_at, total))

    def on_leg_started(self, leg: LegConfig, index: int, total: int) -> None:
        self.coordinator._emit(LegStarted(self.run_id, leg, index, total))

    def on_leg_finished(self, result: LegResult, index: int, total: int) -> None:
        self.coordinator._emit(LegFinished(self.run_id, result, index, total, result.minimum_total_cny))
        if result.status.value == "manual_attention":
            self.coordinator._emit(ManualAttentionRequested(result.leg.id, result.error_message or "需要人工处理"))

    def on_cycle_finished(self, report: RunReport, workbook: object) -> None:
        pass
