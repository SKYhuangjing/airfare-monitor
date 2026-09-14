from __future__ import annotations

import threading
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.desktop_app.events import CycleFinished, NextRunScheduled
from airfare_monitor.desktop_app.monitor_coordinator import MonitorCoordinator, calculate_next_run
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.models import EtdWindow, LegConfig, LegResult, LegStatus, RunReport, RunStatus
from airfare_monitor.scheduler import AlreadyRunningError, ProcessLock


ROOT = Path(__file__).resolve().parents[1]


class MonitorCoordinatorTests(unittest.TestCase):
    def test_next_run_uses_start_time_for_short_cycle(self):
        started = datetime(2026, 9, 10, 10, 0)
        finished = started + timedelta(minutes=2)
        self.assertEqual(
            calculate_next_run(started, finished, interval_minutes=30, jitter_seconds=10),
            datetime(2026, 9, 10, 10, 30, 10),
        )

    def test_next_run_keeps_five_minute_cooldown_after_long_cycle(self):
        started = datetime(2026, 9, 10, 10, 0)
        finished = started + timedelta(minutes=40)
        self.assertEqual(
            calculate_next_run(started, finished, interval_minutes=30),
            datetime(2026, 9, 10, 10, 45),
        )

    def test_rejects_interval_below_desktop_minimum(self):
        now = datetime(2026, 9, 10, 10, 0)
        with self.assertRaisesRegex(ValueError, "30"):
            calculate_next_run(now, now, interval_minutes=29)

    def test_desktop_and_cli_share_the_same_exclusive_process_lock(self):
        with TemporaryDirectory() as temp:
            lock_path = Path(temp) / "airfare-monitor.lock"
            with ProcessLock(lock_path):
                with self.assertRaises(AlreadyRunningError):
                    ProcessLock(lock_path).acquire()

            with ProcessLock(lock_path):
                pass

    def test_run_now_is_coalesced_and_pause_resume_do_not_overlap(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg("route-1")])
            started = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            created: list[_FakeService] = []
            events: list[object] = []

            def factory(legs, settings, sink, delay):
                service = _FakeService(legs, sink, started, release)
                created.append(service)
                return service

            coordinator = MonitorCoordinator(paths, service_factory=factory, jitter=lambda low, high: 0)

            def receive(event: object) -> None:
                events.append(event)
                if isinstance(event, CycleFinished):
                    finished.set()

            coordinator.subscribe(receive)
            coordinator.start()
            self.assertTrue(started.wait(2), "fake monitoring cycle did not start")
            self.assertFalse(coordinator.run_now())
            self.assertFalse(coordinator.run_now())
            self.assertTrue(coordinator.pause())
            release.set()
            self.assertTrue(finished.wait(2), "fake monitoring cycle did not finish")
            self.assertTrue(_wait_until(lambda: coordinator.snapshot().paused))
            self.assertEqual(len(created), 1)
            self.assertTrue(created[0].closed)
            self.assertTrue(coordinator.resume())
            self.assertTrue(_wait_until(lambda: not coordinator.snapshot().paused))
            self.assertEqual(len(created), 1)
            self.assertTrue(any(isinstance(event, NextRunScheduled) for event in events))
            self.assertTrue(coordinator.shutdown(timeout=2))

    def test_start_can_wait_for_onboarding_without_running_a_route(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg("route-1")])
            created: list[object] = []

            def factory(legs, settings, sink, delay):
                created.append(object())
                raise AssertionError("service must not be created before onboarding is complete")

            coordinator = MonitorCoordinator(paths, service_factory=factory)
            coordinator.start(run_immediately=False)
            self.assertTrue(_wait_until(lambda: coordinator.snapshot().state == "IDLE"))
            self.assertEqual(created, [])
            self.assertTrue(coordinator.shutdown(timeout=2))


class _FakeService:
    def __init__(self, legs, sink, started: threading.Event, release: threading.Event):
        self.legs = legs
        self.sink = sink
        self.started = started
        self.release = release
        self.closed = False

    def run_once(self, *, send_email: bool = False):
        started_at = datetime(2026, 9, 10, 10, 0)
        run_id = "fake-run"
        self.sink.on_cycle_started(run_id, started_at, len(self.legs))
        results = []
        for index, leg in enumerate(self.legs, start=1):
            self.sink.on_leg_started(leg, index, len(self.legs))
            self.started.set()
            self.release.wait(2)
            result = LegResult(leg=leg, status=LegStatus.SUCCESS, captured_at=started_at, completed_response=True)
            results.append(result)
            self.sink.on_leg_finished(result, index, len(self.legs))
        report = RunReport(
            run_id=run_id,
            started_at=started_at,
            finished_at=started_at + timedelta(minutes=1),
            status=RunStatus.SUCCESS,
            legs=results,
        )
        self.sink.on_cycle_finished(report, Path("fake.xlsx"))
        return report, Path("fake.xlsx")

    def close(self) -> None:
        self.closed = True


def _wait_until(predicate, timeout: float = 2) -> bool:
    event = threading.Event()
    deadline = datetime.now() + timedelta(seconds=timeout)
    while datetime.now() < deadline:
        if predicate():
            return True
        event.wait(0.01)
    return predicate()


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
