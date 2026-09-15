"""Quiet-by-default desktop alert decisions from immutable worker events."""

from __future__ import annotations

from dataclasses import dataclass

from .events import CycleFinished, FatalError, MailDeliveryFailed, ManualAttentionRequested


@dataclass(frozen=True, slots=True)
class DesktopAlert:
    title: str
    message: str
    target_page: int


def alert_for_event(event: object) -> DesktopAlert | None:
    if isinstance(event, ManualAttentionRequested):
        return DesktopAlert("需要人工完成页面确认", "请打开系统状态处理受影响航程。", 4)
    if isinstance(event, MailDeliveryFailed):
        return DesktopAlert("邮件发送失败", "价格和 Excel 已保存，请检查通知设置。", 3)
    if isinstance(event, FatalError):
        return DesktopAlert("监控运行异常", "请打开系统状态检查运行设置。", 4)
    if isinstance(event, CycleFinished):
        confirmed = event.report.threshold_confirmed_leg_ids
        if confirmed:
            return DesktopAlert(
                "发现确认低价",
                f"本轮 {len(confirmed)} 条航程命中心理价位，请打开概览查看 CNY 含税价格。",
                0,
            )
        succeeded = sum(item.status.value == "success" for item in event.report.legs)
        total = event.total_legs or len(event.report.legs)
        if any(item.status.value == "manual_attention" for item in event.report.legs):
            return None  # ManualAttentionRequested already gives the actionable alert.
        if succeeded < total:
            return DesktopAlert(
                "本轮查询未全部完成",
                f"成功 {succeeded}/{total}；已完成结果仍保留在价格历史中。",
                4,
            )
    return None
