"""Offline tests: no real browser, live website, or daily browser profile."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from airfare_monitor.background_browser import BackgroundBrowserLaunch
from airfare_monitor.collector import QunarBrowserSession
from airfare_monitor.config import load_settings
from airfare_monitor.desktop_app.settings_repository import SettingsRepository
from airfare_monitor.errors import CollectionError, ConfigError

ROOT = Path(__file__).resolve().parents[1]


class _Protocol:
    def __init__(self, *, pid=1234, state="minimized", unsupported=False, initial_pages=False):
        self.pid, self.state, self.unsupported, self.initial_pages = pid, state, unsupported, initial_pages
        self.calls = []
        self.close = Mock()

    def send(self, message):
        request = json.loads(message)
        self.calls.append(request)
        method = request["method"]
        result = {}
        if method == "SystemInfo.getProcessInfo":
            result = {"processInfo": [{"type": "browser", "id": self.pid}]}
        elif method == "Target.getTargets":
            result = {"targetInfos": [{"type": "page"}] if self.initial_pages else []}
        elif method == "Target.createTarget":
            result = {"targetId": "owned-target"}
        elif method == "Browser.getWindowForTarget":
            result = {"bounds": {"windowState": self.state}}
        self.response = {"id": request["id"], "result": result}
        if method == "Target.createTarget" and self.unsupported:
            self.response = {"id": request["id"], "error": {"message": "raw-sensitive-response"}}

    def recv(self):
        return json.dumps(self.response)


class BackgroundBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        executable = self.root / "chrome.exe"
        executable.touch()
        self.options = SimpleNamespace(
            is_headless=False, address="127.0.0.1:9449", browser_path=str(executable),
            user_data_path=str(self.root / "data/browser-profile"), system_user_path=False,
            arguments=[f"--user-data-dir={self.root / 'data/browser-profile'}", "--no-first-run"],
            existing_only=Mock(),
        )
        self.process = Mock(pid=1234)
        self.process.poll.return_value = None
        self.popen = self._patch("subprocess.Popen", return_value=self.process)
        self.port = self._patch("socket.create_connection", side_effect=ConnectionRefusedError)
        self.client = Mock()
        self.client.get.return_value.json.return_value = {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9449/devtools/browser/owned"
        }
        client_type = self._patch("requests.Session")
        client_type.return_value.__enter__.return_value = self.client
        self.protocol = _Protocol()
        self.connection = self._patch("websocket.create_connection", return_value=self.protocol)

    def _patch(self, name, **kwargs):
        patcher = patch(f"airfare_monitor.background_browser.{name}", **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_launch_creates_only_a_minimized_background_window(self):
        launch = BackgroundBrowserLaunch()
        launch.start(self.options)
        arguments = self.popen.call_args.args[0]
        self.assertIn("--no-startup-window", arguments)
        self.assertIn("--remote-debugging-port=9449", arguments)
        self.assertIn(self.options.arguments[0], arguments)
        self.assertFalse(any(value.startswith("--headless") for value in arguments))
        create = next(call for call in self.protocol.calls if call["method"] == "Target.createTarget")
        self.assertEqual(create["params"], {
            "url": "about:blank", "newWindow": True, "background": True, "windowState": "minimized",
        })
        self.options.existing_only.assert_called_once_with()
        self.assertFalse(self.client.trust_env)
        self.assertTrue(self.connection.call_args.kwargs["suppress_origin"])
        launch.close()
        self.assertIsNone(launch.process)

    def test_occupied_port_never_attaches_or_launches(self):
        self.port.side_effect = None
        with self.assertRaisesRegex(CollectionError, "端口已占用"):
            BackgroundBrowserLaunch().start(self.options)
        self.popen.assert_not_called()
        self.connection.assert_not_called()

    def test_wrong_process_is_never_closed_through_the_foreign_connection(self):
        self.protocol.pid = 5678
        launch = BackgroundBrowserLaunch()
        with self.assertRaisesRegex(CollectionError, "进程不匹配"):
            launch.start(self.options)
        methods = [call["method"] for call in self.protocol.calls]
        self.assertNotIn("Browser.close", methods)
        self.assertNotIn("Target.createTarget", methods)
        self.assertIsNone(launch.process)

    def test_unsupported_minimization_stops_without_visible_fallback(self):
        self.protocol.unsupported = True
        launch = BackgroundBrowserLaunch()
        with self.assertRaisesRegex(CollectionError, "不支持后台最小化") as caught:
            launch.start(self.options)
        self.assertNotIn("raw-sensitive", str(caught.exception))
        self.assertEqual(sum(call["method"] == "Target.createTarget" for call in self.protocol.calls), 1)
        self.assertIn("Browser.close", [call["method"] for call in self.protocol.calls])
        self.options.existing_only.assert_not_called()
        self.assertIsNone(launch.process)

    def test_unexpected_restored_pages_are_not_reused(self):
        self.protocol.initial_pages = True
        with self.assertRaisesRegex(CollectionError, "意外恢复"):
            BackgroundBrowserLaunch().start(self.options)
        self.assertNotIn("Target.createTarget", [call["method"] for call in self.protocol.calls])

    def test_unminimized_target_is_rejected(self):
        self.protocol.state = "normal"
        with self.assertRaisesRegex(CollectionError, "不支持直接最小化"):
            BackgroundBrowserLaunch().start(self.options)
        self.options.existing_only.assert_not_called()

    def test_connection_failure_becomes_a_collection_error_and_cleans_up(self):
        self.connection.side_effect = OSError("connection failed")
        launch = BackgroundBrowserLaunch()
        with self.assertRaisesRegex(CollectionError, "启动或连接失败"):
            launch.start(self.options)
        self.assertIsNone(launch.process)
        self.process.wait.assert_called()

    def test_unresponsive_owned_process_is_terminated_after_graceful_close(self):
        launch = BackgroundBrowserLaunch()
        launch.start(self.options)
        self.process.wait.side_effect = [subprocess.TimeoutExpired("owned-browser", 3), None]
        launch.close()
        self.assertIn("Browser.close", [call["method"] for call in self.protocol.calls])
        self.process.terminate.assert_called_once()
        self.process.kill.assert_not_called()

    def test_foreign_endpoint_is_rejected_before_connecting(self):
        self.client.get.return_value.json.return_value = {"webSocketDebuggerUrl": "ws://example.com:9449/devtools/browser/x"}
        with self.assertRaisesRegex(CollectionError, "无效的本机"):
            BackgroundBrowserLaunch().start(self.options)
        self.connection.assert_not_called()

    def test_headless_or_daily_profile_is_rejected_before_launch(self):
        self.options.is_headless = True
        with self.assertRaisesRegex(CollectionError, "无头模式"):
            BackgroundBrowserLaunch().start(self.options)
        self.options.is_headless = False
        self.options.system_user_path = True
        with self.assertRaisesRegex(CollectionError, "独立浏览器"):
            BackgroundBrowserLaunch().start(self.options)
        self.popen.assert_not_called()

    def test_session_cleans_up_if_drission_attachment_fails(self):
        settings = replace(load_settings(ROOT / "resources/settings.default.yaml", project_root=self.root).browser,
                           background=True)
        with patch("airfare_monitor.collector.BackgroundBrowserLaunch") as launcher, patch(
            "DrissionPage.Chromium", side_effect=CollectionError("attachment failed")
        ):
            session = QunarBrowserSession(settings)
            with self.assertRaisesRegex(CollectionError, "attachment failed"):
                session.start()
            launcher.return_value.start.assert_called_once()
            launcher.return_value.close.assert_called_once()
            self.assertIsNone(session.browser)

    def test_session_converts_attachment_transport_errors_for_regular_retry_handling(self):
        settings = replace(load_settings(ROOT / "resources/settings.default.yaml", project_root=self.root).browser,
                           background=True)
        with patch("airfare_monitor.collector.BackgroundBrowserLaunch") as launcher, patch(
            "DrissionPage.Chromium", side_effect=OSError("transport failed")
        ):
            session = QunarBrowserSession(settings)
            with self.assertRaisesRegex(CollectionError, "后台浏览器连接失败"):
                session.start()
            launcher.return_value.close.assert_called_once()

    def test_legacy_desktop_hidden_setting_migrates_without_altering_cli_or_file(self):
        path = self.root / "settings.yaml"
        raw = yaml.safe_load((ROOT / "resources/settings.default.yaml").read_text(encoding="utf-8"))
        raw["browser"]["headless"] = True
        raw["browser"].pop("background", None)
        path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        original = path.read_bytes()
        repository = SettingsRepository(path, user_root=self.root)
        self.assertFalse(repository.load_desktop().show_browser)
        desktop = repository.load_core().browser
        self.assertFalse(desktop.headless)
        self.assertTrue(desktop.background)
        self.assertTrue(load_settings(path, project_root=self.root).browser.headless)
        self.assertEqual(path.read_bytes(), original)
        repository.save_desktop(replace(repository.load_desktop(), show_browser=True))
        self.assertFalse(repository.load_core().browser.background)
        repository.save_desktop(replace(repository.load_desktop(), show_browser=False))
        saved = load_settings(path, project_root=self.root).browser
        self.assertFalse(saved.headless)
        self.assertTrue(saved.background)

    def test_background_setting_requires_a_boolean(self):
        path = self.root / "settings.yaml"
        raw = yaml.safe_load((ROOT / "resources/settings.default.yaml").read_text(encoding="utf-8"))
        raw["browser"]["background"] = "true"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "background"):
            load_settings(path, project_root=self.root)
