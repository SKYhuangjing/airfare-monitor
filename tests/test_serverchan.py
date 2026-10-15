from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.config import MailSettings
from airfare_monitor.desktop_app.credential_store import (
    SERVERCHAN_ACCOUNT,
    SERVERCHAN_SERVICE,
    CredentialStore,
)
from airfare_monitor.desktop_app.events import CycleFinished, FatalError, ServerChanPushFailed
from airfare_monitor.desktop_app.monitor_coordinator import MonitorCoordinator
from airfare_monitor.desktop_app.notification_policy import alert_for_event
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.desktop_app.serverchan_profile import (
    ServerChanProfile,
    ServerChanProfileRepository,
)
from airfare_monitor.desktop_app.settings_repository import SettingsRepository
from airfare_monitor.desktop_app.preferences import PreferencesManager
from airfare_monitor.models import EtdWindow, LegConfig, LegResult, LegStatus, RunReport, RunStatus
from airfare_monitor.serverchan import (
    SERVERCHAN_API_BASE,
    ServerChanError,
    build_push_message,
    friendly_serverchan_error,
    normalize_sendkey,
    send_serverchan_message,
)
from airfare_monitor.service import MonitorService
from airfare_monitor.ui.notifications_page import NotificationsPage


ROOT = Path(__file__).resolve().parents[1]

SENDKEY = "SCT123456TqWXpXvKMiNkOlxUfFXPspf"


class _Response:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)

    def read(self, limit: int = -1) -> bytes:
        return self._stream.read(limit)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Recorder:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or _Response(json.dumps({"code": 0, "message": ""}).encode("utf-8"))
        self.error = error
        self.requests: list[object] = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class SendKeyValidationTests(unittest.TestCase):
    def test_accepts_regular_sendkey_and_strips_spaces(self):
        self.assertEqual(normalize_sendkey(f"  {SENDKEY} \n"), SENDKEY)

    def test_rejects_empty_short_or_malformed_keys(self):
        with self.assertRaisesRegex(ValueError, "SendKey"):
            normalize_sendkey("")
        with self.assertRaisesRegex(ValueError, "空格"):
            normalize_sendkey("SCT123 abcDEF")
        with self.assertRaisesRegex(ValueError, "长度"):
            normalize_sendkey("SCT123")
        with self.assertRaisesRegex(ValueError, "字母"):
            normalize_sendkey("SCT123456TqWX!pXvKMiNk")


class PushMessageTests(unittest.TestCase):
    def test_push_reuses_mail_subject_and_plain_body(self):
        report = _report(confirmed=True)
        title, desp = build_push_message(report, _mail_settings())
        self.assertIn("[低价命中]", title)
        self.assertIn("上海虹桥", title)
        self.assertIn("采集时间", desp)
        self.assertIn("r3-leg", desp)
        self.assertIn("上海虹桥", desp)
        self.assertLessEqual(len(title), 32)

    def test_normal_cycle_title_and_reminder_present(self):
        title, desp = build_push_message(_report(confirmed=False), _mail_settings())
        self.assertIn("[航价监控]", title)
        self.assertIn("最终确认", desp)


class DeliveryTests(unittest.TestCase):
    def test_send_posts_urlencoded_form_to_sendkey_endpoint(self):
        recorder = _Recorder()
        send_serverchan_message(
            SENDKEY, "标题", "正文", urlopen=recorder,
        )
        request = recorder.requests[0]
        self.assertEqual(request.full_url, f"{SERVERCHAN_API_BASE}/{SENDKEY}.send")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.headers.get("Content-type"), "application/x-www-form-urlencoded"
        )
        body = request.data.decode("utf-8")
        self.assertIn("title=%E6%A0%87%E9%A2%98", body)
        self.assertIn("%E6%AD%A3%E6%96%87", body)

    def test_api_error_code_becomes_friendly_error(self):
        recorder = _Recorder(
            result=_Response(json.dumps({"code": 40001, "message": "bad sendkey"}).encode("utf-8"))
        )
        with self.assertRaises(ServerChanError) as ctx:
            send_serverchan_message(SENDKEY, "标题", "正文", urlopen=recorder)
        self.assertIn("SendKey", str(ctx.exception))

    def test_http_and_network_errors_map_to_friendly_text(self):
        cases = (
            (urllib.error.HTTPError(None, 401, "Unauthorized", None, None), "检查 SendKey"),
            (urllib.error.HTTPError(None, 429, "Too Many", None, None), "频繁"),
            (urllib.error.HTTPError(None, 503, "Down", None, None), "稍后重试"),
            (urllib.error.URLError(TimeoutError()), "超时"),
        )
        for error, expected in cases:
            with self.subTest(error=error):
                message = friendly_serverchan_error(
                    _capture(error, SENDKEY, "标题", "正文")
                )
                self.assertIn(expected, message)

    def test_malformed_body_and_missing_sendkey_are_rejected(self):
        recorder = _Recorder(result=_Response(b"<html>gateway</html>"))
        with self.assertRaisesRegex(ServerChanError, "无法识别"):
            send_serverchan_message(SENDKEY, "标题", "正文", urlopen=recorder)
        with self.assertRaises(ValueError):
            send_serverchan_message("short", "标题", "正文", urlopen=_Recorder())


def _capture(error: Exception, key: str, title: str, desp: str) -> Exception:
    try:
        send_serverchan_message(key, title, desp, urlopen=_Recorder(error=error))
    except Exception as exc:
        return exc
    raise AssertionError("expected a delivery error")


class ProfileTests(unittest.TestCase):
    def test_profile_round_trip_and_default_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            repository = ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root)
            self.assertFalse(repository.load().enabled)
            repository.save(ServerChanProfile(enabled=True))
            self.assertTrue(repository.load().enabled)
            text = paths.settings_path.read_text(encoding="utf-8")
            self.assertIn("desktop_serverchan:", text)
            self.assertNotIn(SENDKEY, text)

    def test_profile_rejects_non_boolean_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            paths.settings_path.write_text(
                paths.settings_path.read_text(encoding="utf-8")
                + "\ndesktop_serverchan:\n  enabled: yes-please\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "布尔值"):
                ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root).load()


class CoordinatorPushTests(unittest.TestCase):
    def test_pushes_report_each_cycle_with_mail_wording(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg()])
            ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root).save(
                ServerChanProfile(enabled=True)
            )
            coordinator = MonitorCoordinator(
                paths,
                service_factory=lambda legs, settings, sink, delay: MonitorService(
                    legs, settings, browser=_Browser(), event_sink=sink,
                    sleep=delay, now=lambda: datetime(2026, 10, 15, 10),
                ),
                now=lambda: datetime(2026, 10, 15, 10),
                jitter=lambda low, high: 0,
            )
            with patch(
                "airfare_monitor.desktop_app.monitor_coordinator.CredentialStore",
                return_value=_MemoryCredentials({SERVERCHAN_ACCOUNT: SENDKEY}),
            ), patch(
                "airfare_monitor.desktop_app.monitor_coordinator.send_report_push"
            ) as push:
                coordinator._execute_cycle(None)
                push.assert_called_once()
                report = push.call_args.args[0]
                self.assertEqual(report.legs[0].leg.id, "r3-leg")

    def test_disabled_or_missing_key_never_raises_and_emits_event(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg()])
            coordinator = MonitorCoordinator(
                paths,
                service_factory=lambda legs, settings, sink, delay: MonitorService(
                    legs, settings, browser=_Browser(), event_sink=sink,
                    sleep=delay, now=lambda: datetime(2026, 10, 15, 10),
                ),
                now=lambda: datetime(2026, 10, 15, 10),
                jitter=lambda low, high: 0,
            )
            with patch(
                "airfare_monitor.desktop_app.monitor_coordinator.send_report_push"
            ) as push:
                coordinator._execute_cycle(None)
                push.assert_not_called()

            events: list[object] = []
            coordinator.subscribe(events.append)
            ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root).save(
                ServerChanProfile(enabled=True)
            )
            with patch(
                "airfare_monitor.desktop_app.monitor_coordinator.CredentialStore",
                return_value=_MemoryCredentials({}),
            ), patch(
                "airfare_monitor.desktop_app.monitor_coordinator.send_report_push"
            ) as push:
                coordinator._execute_cycle(None)
                push.assert_not_called()
            self.assertTrue(any(isinstance(event, ServerChanPushFailed) for event in events))

    def test_delivery_failure_surfaces_event_but_keeps_saved_prices(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg()])
            ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root).save(
                ServerChanProfile(enabled=True)
            )
            coordinator = MonitorCoordinator(
                paths,
                service_factory=lambda legs, settings, sink, delay: MonitorService(
                    legs, settings, browser=_Browser(), event_sink=sink,
                    sleep=delay, now=lambda: datetime(2026, 10, 15, 10),
                ),
                now=lambda: datetime(2026, 10, 15, 10),
                jitter=lambda low, high: 0,
            )
            events: list[object] = []
            coordinator.subscribe(events.append)
            with patch(
                "airfare_monitor.desktop_app.monitor_coordinator.CredentialStore",
                return_value=_MemoryCredentials({SERVERCHAN_ACCOUNT: SENDKEY}),
            ), patch(
                "airfare_monitor.desktop_app.monitor_coordinator.send_report_push",
                side_effect=ServerChanError("连接方糖服务失败；请检查网络后重试。"),
            ):
                coordinator._execute_cycle(None)
            self.assertTrue(any(isinstance(event, ServerChanPushFailed) for event in events))

    def test_unexpected_http_exception_never_stalls_the_schedule(self):
        import http.client

        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg()])
            ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root).save(
                ServerChanProfile(enabled=True)
            )
            coordinator = MonitorCoordinator(
                paths,
                service_factory=lambda legs, settings, sink, delay: MonitorService(
                    legs, settings, browser=_Browser(), event_sink=sink,
                    sleep=delay, now=lambda: datetime(2026, 10, 15, 10),
                ),
                now=lambda: datetime(2026, 10, 15, 10),
                jitter=lambda low, high: 0,
            )
            events: list[object] = []
            coordinator.subscribe(events.append)
            with patch(
                "airfare_monitor.desktop_app.monitor_coordinator.CredentialStore",
                return_value=_MemoryCredentials({SERVERCHAN_ACCOUNT: SENDKEY}),
            ), patch(
                "airfare_monitor.desktop_app.monitor_coordinator.send_report_push",
                side_effect=http.client.IncompleteRead(12),
            ):
                coordinator._execute_cycle(None)
            self.assertTrue(any(isinstance(event, ServerChanPushFailed) for event in events))
            self.assertTrue(any(isinstance(event, CycleFinished) for event in events))
            self.assertIsNotNone(coordinator._next_schedule())
            self.assertFalse(any(isinstance(event, FatalError) for event in events))

    def test_push_failure_is_persisted_to_the_event_journal(self):
        from airfare_monitor.desktop_app.event_journal import _event_payload

        payload = _event_payload(ServerChanPushFailed("DeliveryFailed"))
        self.assertIsNotNone(payload)
        self.assertEqual(payload[0], "serverchan_push_failed")
        self.assertIn("方糖推送失败", payload[2])

    def test_push_failure_alert_targets_notification_page(self):
        alert = alert_for_event(ServerChanPushFailed("DeliveryFailed"))
        self.assertIsNotNone(alert)
        self.assertIn("方糖推送失败", alert.title)
        self.assertEqual(alert.target_page, 3)


class NotificationsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_page_loads_saved_state_and_keeps_sendkey_out_of_yaml(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            repository = ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root)
            repository.save(ServerChanProfile(enabled=True))
            page = NotificationsPage(
                PreferencesManager(settings),
                _mail_repository(paths),
                serverchan_repository=repository,
                serverchan_store=_MemoryCredentials({SERVERCHAN_ACCOUNT: SENDKEY}),
            )
            self.assertTrue(page.push_toggle.isChecked())
            self.assertEqual(page.push_state.text(), "已开启")
            self.assertEqual(page.push_key.echoMode(), page.push_key.EchoMode.Password)
            self.assertEqual(page.push_test_button.text(), "发送测试推送并保存")
            self.assertNotIn(SENDKEY, paths.settings_path.read_text(encoding="utf-8"))
            page.close()

    def test_enabling_without_key_is_rejected_before_saving(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            repository = ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root)
            page = NotificationsPage(
                PreferencesManager(settings),
                _mail_repository(paths),
                serverchan_repository=repository,
                serverchan_store=_MemoryCredentials(),
            )
            page.push_toggle.setChecked(True)
            with patch("PySide6.QtWidgets.QMessageBox.warning") as warned:
                self.assertFalse(page._save_push())
                warned.assert_called_once()
            self.assertFalse(repository.load().enabled)
            page.close()

    def test_save_stores_secret_in_credential_backend_only(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            repository = ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root)
            secrets = _MemoryCredentials()
            page = NotificationsPage(
                PreferencesManager(settings),
                _mail_repository(paths),
                serverchan_repository=repository,
                serverchan_store=secrets,
            )
            page.push_toggle.setChecked(True)
            page.push_key.setText(SENDKEY)
            self.assertTrue(page._save_push())
            self.assertEqual(secrets.get_secret(SERVERCHAN_ACCOUNT), SENDKEY)
            self.assertTrue(repository.load().enabled)
            self.assertEqual(page.push_key.text(), "")
            self.assertNotIn(SENDKEY, paths.settings_path.read_text(encoding="utf-8"))
            page.close()

    def test_explicit_test_runs_in_worker_then_saves_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            repository = ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root)
            page = NotificationsPage(
                PreferencesManager(settings),
                _mail_repository(paths),
                serverchan_repository=repository,
                serverchan_store=_MemoryCredentials(),
            )
            page.push_toggle.setChecked(True)
            page.push_key.setText(SENDKEY)
            with patch("airfare_monitor.ui.notifications_page.send_test_push") as sender:
                page._test_push()
                for _ in range(150):
                    self.app.processEvents()
                    if page._push_thread is None and repository.load().enabled:
                        break
                    import time as wall_time
                    wall_time.sleep(0.01)
                sender.assert_called_once()
                self.assertEqual(sender.call_args.args[0], SENDKEY)
                self.assertTrue(repository.load().enabled)
                self.assertIn("测试成功", page.push_status.text())
            page.close()

    def test_test_push_failure_keeps_profile_unsaved_and_restores_buttons(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            repository = ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root)
            secrets = _MemoryCredentials()
            page = NotificationsPage(
                PreferencesManager(settings),
                _mail_repository(paths),
                serverchan_repository=repository,
                serverchan_store=secrets,
            )
            page.push_toggle.setChecked(True)
            page.push_key.setText(SENDKEY)
            toggle_disabled_during_test = []

            def fail_and_probe(sendkey: str) -> None:
                toggle_disabled_during_test.append(not page.push_toggle.isEnabled())
                raise ServerChanError("方糖服务拒绝了本次推送（[AUTH]错误的Key）；请检查 SendKey。")

            with patch(
                "airfare_monitor.ui.notifications_page.send_test_push",
                side_effect=fail_and_probe,
            ):
                page._test_push()
                for _ in range(150):
                    self.app.processEvents()
                    if page._push_thread is None:
                        break
                    import time as wall_time
                    wall_time.sleep(0.01)
                self.assertIn("请检查 SendKey", page.push_status.text())
                self.assertEqual(toggle_disabled_during_test, [True])
                self.assertFalse(repository.load().enabled)
                self.assertIsNone(secrets.get_secret(SERVERCHAN_ACCOUNT))
                self.assertTrue(page.push_test_button.isEnabled())
                self.assertTrue(page.push_save_button.isEnabled())
                self.assertTrue(page.push_toggle.isEnabled())
            page.close()

    def test_reset_push_form_restores_saved_state_and_clears_input(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            repository = ServerChanProfileRepository(paths.settings_path, user_root=paths.user_root)
            repository.save(ServerChanProfile(enabled=True))
            page = NotificationsPage(
                PreferencesManager(settings),
                _mail_repository(paths),
                serverchan_repository=repository,
                serverchan_store=_MemoryCredentials(),
            )
            page.push_toggle.setChecked(False)
            page.push_key.setText("SCTwhatever123456")
            page._reset_push_form()
            self.assertTrue(page.push_toggle.isChecked())
            self.assertEqual(page.push_key.text(), "")
            self.assertIn("已恢复", page.push_status.text())
            page.close()

    def test_bad_sendkey_input_is_rejected_before_any_network_call(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            page = NotificationsPage(
                PreferencesManager(settings),
                _mail_repository(paths),
                serverchan_store=_MemoryCredentials(),
            )
            page.push_key.setText("bad key!")
            with patch("airfare_monitor.ui.notifications_page.send_test_push") as sender, patch(
                "PySide6.QtWidgets.QMessageBox.warning"
            ) as warned:
                page._test_push()
                sender.assert_not_called()
                warned.assert_called_once()
            page.close()


class _Browser:
    def collect(self, leg, now):
        return LegResult(leg, LegStatus.SUCCESS, now(), completed_response=True)

    def close(self):
        pass

    def restart(self):
        pass


class _MemoryCredentials:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_secret(self, username):
        return self.values.get(username)

    def has_secret(self, username):
        return username in self.values

    def save_secret(self, username, secret):
        self.values[username] = secret

    def delete_secret(self, username):
        self.values.pop(username, None)


def _mail_repository(paths: AppPaths):
    from airfare_monitor.desktop_app.mail_profile import MailProfileRepository

    return MailProfileRepository(paths.settings_path, user_root=paths.user_root)


def _mail_settings() -> MailSettings:
    return MailSettings(
        enabled=False,
        smtp_host="",
        smtp_port=465,
        security="ssl",
        username_env="A",
        password_env="B",
        sender_env="C",
        recipients_env="D",
        attach_excel=False,
        normal_subject_prefix="[航价监控]",
        threshold_subject_prefix="[低价命中]",
    )


def _report(*, confirmed: bool) -> RunReport:
    leg = _leg()
    now = datetime(2026, 10, 15, 10, 0)
    result = LegResult(leg, LegStatus.SUCCESS, now, completed_response=True)
    return RunReport(
        run_id="run-1",
        started_at=now,
        finished_at=now,
        status=RunStatus.SUCCESS,
        legs=[result],
        threshold_confirmed_leg_ids={leg.id} if confirmed else set(),
    )


def _leg() -> LegConfig:
    return LegConfig(
        id="r3-leg", enabled=True, origin_airport_iata="SHA", destination_airport_iata="XMN",
        departure_date=date(2026, 12, 1), etd_window=EtdWindow(time(0), time(23, 59)),
        direct_only=True, expected_total_price_cny=Decimal("1000"), top_n=10,
        adult_count=1, child_count=0, cabin_class="economy",
        origin_name_zh="上海虹桥", destination_name_zh="厦门高崎",
    )


def _paths(root: str) -> AppPaths:
    found = AppPaths.discover(user_root=root)
    values = {name: getattr(found, name) for name in found.__dataclass_fields__}
    values["resource_root"] = ROOT / "resources"
    return AppPaths(**values)


if __name__ == "__main__":
    unittest.main()
