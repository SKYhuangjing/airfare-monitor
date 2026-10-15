"""ServerChan (方糖) WeChat push that mirrors the SMTP report channel.

The push reuses the mail subject/plain-body pipeline so both channels stay
on the same wording, thresholds and per-cycle cadence without re-deriving
any low-price policy of its own.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .config import MailSettings
from .mail import build_plain_body, build_subject
from .models import RunReport

SERVERCHAN_API_BASE = "https://sctapi.ftqq.com"
TITLE_LIMIT = 32
# 方糖 desp 上限按官方文档口径为 32KB；中文按 UTF-8 计费，按字节截断更保守。
DESP_LIMIT = 32_000


class ServerChanError(RuntimeError):
    """ServerChan delivery failed with a user-actionable explanation."""


def normalize_sendkey(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("请填写方糖 SendKey（在 sct.ftqq.com 的「SendKey」页复制）")
    if any(char.isspace() for char in text):
        raise ValueError("SendKey 不能包含空格；请重新复制完整内容")
    if not 12 <= len(text) <= 128:
        raise ValueError("SendKey 长度不符合方糖格式；请重新复制")
    if not all(char.isalnum() or char in "_-" for char in text):
        raise ValueError("SendKey 只能包含字母、数字、下划线或连字符")
    return text


def build_push_message(report: RunReport, settings: MailSettings) -> tuple[str, str]:
    """Reuse the SMTP subject/body builders so push stays aligned with mail."""
    title = build_subject(report, settings)[:TITLE_LIMIT]
    desp = "\n".join(build_plain_body(report))
    encoded = desp.encode("utf-8")
    if len(encoded) > DESP_LIMIT:
        desp = encoded[:DESP_LIMIT].decode("utf-8", errors="ignore")
    return title, desp


def send_serverchan_message(
    sendkey: str,
    title: str,
    desp: str,
    *,
    timeout_seconds: float = 15.0,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    key = normalize_sendkey(sendkey)
    payload = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    request = urllib.request.Request(
        f"{SERVERCHAN_API_BASE}/{key}.send",
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "airfare-monitor-desktop",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(65536)
    except urllib.error.HTTPError as exc:
        raise ServerChanError(_http_error_text(exc.code)) from exc
    except urllib.error.URLError as exc:
        raise ServerChanError(_url_error_text(exc)) from exc
    except (TimeoutError, socket.timeout, OSError) as exc:
        raise ServerChanError("连接方糖服务失败；请检查网络后重试。") from exc
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ServerChanError("方糖服务返回了无法识别的内容；请稍后重试。") from exc
    if not isinstance(parsed, dict) or parsed.get("code") != 0:
        raise ServerChanError(_api_error_text(parsed))


def send_report_push(report: RunReport, settings: MailSettings, sendkey: str) -> None:
    """Push one cycle summary; callers decide cadence exactly like mail."""
    title, desp = build_push_message(report, settings)
    send_serverchan_message(sendkey, title, desp)


def send_test_push(sendkey: str) -> None:
    """Explicit GUI action: send a test only after the user requests it."""
    send_serverchan_message(
        sendkey,
        "航价守望推送测试",
        "方糖推送测试成功。此推送由您在设置页主动发起；开启后每轮采集摘要都会推送到微信。",
    )


def friendly_serverchan_error(exc: Exception) -> str:
    if isinstance(exc, (ServerChanError, ValueError)):
        return str(exc)
    return "推送未发送成功；请检查 SendKey 和网络后重试。"


def _http_error_text(code: int) -> str:
    if code in (401, 403):
        return "方糖服务拒绝了请求；请检查 SendKey 是否正确。"
    if code == 429:
        return "方糖推送过于频繁；本轮已跳过，请稍后观察下一轮。"
    if code >= 500:
        return "方糖服务暂时不可用；请稍后重试。"
    return f"方糖服务返回错误（HTTP {code}）；请稍后重试。"


def _url_error_text(exc: urllib.error.URLError) -> str:
    reason = exc.reason
    if isinstance(reason, socket.gaierror):
        return "无法解析方糖服务地址；请检查网络。"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "连接方糖服务超时；请检查网络后重试。"
    return "无法连接方糖服务；请检查网络。"


def _api_error_text(parsed: object) -> str:
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or "").strip()
        if message and len(message) <= 80:
            hint = "请检查 SendKey。" if "key" in message.lower() else "请稍后重试。"
            return f"方糖服务拒绝了本次推送（{message}）；{hint}"
    return "方糖服务拒绝了本次推送；请检查 SendKey 后重试。"
