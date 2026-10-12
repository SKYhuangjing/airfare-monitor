"""Launch an ordinary isolated Chromium window already minimized through CDP."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import websocket

from .errors import CollectionError


class BackgroundBrowserLaunch:
    """Own the process and cleanup connection; never attach to another browser.

    --start-minimized is ignored by some Chrome builds. Starting with no window
    and creating a minimized target avoids briefly opening a foreground window.
    Unsupported protocol versions fail closed instead of opening a visible one.
    """

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self._connection: Any = None
        self._request_id = 0
        self._owns_connection = False

    def start(self, options: Any, *, timeout: float = 20) -> None:
        if options.is_headless:
            raise CollectionError("后台最小化模式不能与无头模式同时启用")
        host, port_text = options.address.rsplit(":", 1)
        port = int(port_text)
        if host not in {"127.0.0.1", "localhost"}:
            raise CollectionError("后台浏览器只允许本机独立调试端口")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.3):
                raise CollectionError("独立浏览器端口已占用，请关闭工具的独立浏览器后重试")
        except OSError:
            pass

        path = options.browser_path
        if not Path(path).is_file():
            path = shutil.which(path)
        if not path:
            # Use the same installed-browser discovery as the visible launcher.
            from DrissionPage._functions.browser import get_chrome_path
            path = get_chrome_path(options.ini_path)
        if not path:
            raise CollectionError("未找到 Chrome 或 Edge，请在系统状态选择浏览器")
        if not options.user_data_path or options.system_user_path:
            raise CollectionError("后台查询必须使用工具的独立浏览器空间")

        arguments = [
            argument for argument in options.arguments
            if not argument.startswith(("--remote-debugging-port", "--no-startup-window"))
        ]
        try:
            self.process = subprocess.Popen(
                [str(path), *arguments, f"--remote-debugging-port={port}", "--no-startup-window"],
                shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + timeout
            with requests.Session() as client:
                client.trust_env = False
                while True:
                    if self.process.poll() is not None:
                        raise CollectionError("后台浏览器已退出，请检查独立浏览器空间是否被占用")
                    try:
                        response = client.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
                        response.raise_for_status()
                        endpoint = response.json()["webSocketDebuggerUrl"]
                        break
                    except (requests.RequestException, ValueError, KeyError):
                        if time.monotonic() >= deadline:
                            raise CollectionError("后台浏览器启动超时，请检查所选浏览器") from None
                        time.sleep(.1)
            parsed = urlparse(endpoint)
            if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != port:
                raise CollectionError("独立浏览器返回了无效的本机连接地址")
            self._connection = websocket.create_connection(endpoint, timeout=5, suppress_origin=True)
            info = self._cdp("SystemInfo.getProcessInfo")
            if not any(item.get("type") == "browser" and item.get("id") == self.process.pid
                       for item in info.get("processInfo", [])):
                raise CollectionError("独立浏览器进程不匹配，已停止连接")
            self._owns_connection = True
            # Do not restore windows left by an earlier session or fall back to a
            # normal target: either the owned window starts minimized or we stop.
            targets = self._cdp("Target.getTargets").get("targetInfos", [])
            if any(item.get("type") in {"page", "webview"} for item in targets):
                raise CollectionError("后台浏览器意外恢复了页面，已停止启动；请检查独立浏览器设置")
            target = self._cdp("Target.createTarget", url="about:blank", newWindow=True,
                               background=True, windowState="minimized")
            bounds = self._cdp("Browser.getWindowForTarget", targetId=target["targetId"])
            if bounds.get("bounds", {}).get("windowState") != "minimized":
                window_id = bounds.get("windowId")
                if window_id is not None:
                    with suppress(Exception):
                        self._cdp("Browser.setWindowBounds", windowId=window_id, bounds={"windowState": "minimized"})
                    bounds = self._cdp("Browser.getWindowForTarget", targetId=target["targetId"])
            if bounds.get("bounds", {}).get("windowState") != "minimized":
                raise CollectionError("浏览器不支持直接最小化启动，请更新 Chrome 或 Edge")
            options.existing_only()
            # Keep the owned connection so attachment failures can close Chrome
            # gracefully without leaving a crashed profile or child processes.
        except Exception as exc:
            self.close()
            if isinstance(exc, CollectionError):
                raise
            raise CollectionError("后台浏览器启动或连接失败，请检查浏览器版本和独立浏览器空间") from exc

    def _cdp(self, method: str, **params: Any) -> dict[str, Any]:
        self._request_id += 1
        self._connection.send(json.dumps({"id": self._request_id, "method": method, "params": params}))
        while True:
            response = json.loads(self._connection.recv())
            if response.get("id") == self._request_id:
                if "error" in response:
                    # Do not propagate raw protocol responses into app logs.
                    raise CollectionError("浏览器不支持后台最小化启动，请更新 Chrome 或 Edge")
                return response["result"]

    def close(self) -> None:
        if self._connection is not None:
            if self._owns_connection:
                with suppress(Exception):
                    self._cdp("Browser.close")
            with suppress(Exception):
                self._connection.close()
            self._connection = None
        self._owns_connection = False
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
