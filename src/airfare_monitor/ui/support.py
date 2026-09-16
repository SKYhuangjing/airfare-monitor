"""Voluntary support UI and its deliberately small local prompt state.

Payment QR images are private build-time assets.  They are discovered locally
but never copied into the user data directory, logs, diagnostics, or settings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..app_paths import AppPaths
from .app_icon import application_icon


PROMPT_COOLDOWN_DAYS = 30


@dataclass(frozen=True, slots=True)
class SupportAssets:
    alipay: Path
    wechat: Path

    @property
    def available(self) -> bool:
        return self.alipay.is_file() and self.wechat.is_file()


def locate_support_assets(resource_root: Path | None = None) -> SupportAssets:
    """Find packaged assets, then the ignored development-only source folder."""

    resources = resource_root or AppPaths.discover().resource_root
    roots = (
        resources / "support",
        resources.parent / "private-assets" / "support",
    )
    for root in roots:
        assets = SupportAssets(root / "alipay.png", root / "wechat.png")
        if assets.available:
            return assets
    return SupportAssets(roots[0] / "alipay.png", roots[0] / "wechat.png")


class SupportPromptState:
    """Persist only dismissal timing; never payment or identity information."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def should_offer(
        self,
        events: Iterable[Mapping[str, object]],
        *,
        now: datetime | None = None,
    ) -> bool:
        newest = _newest_low_price_event(events)
        if newest is None:
            return False
        current = now or datetime.now()
        state = self._load()
        dismissed_event = _parse_datetime(state.get("dismissed_event_at"))
        dismissed_until = _parse_datetime(state.get("dismissed_until"))
        if dismissed_event is not None and newest <= dismissed_event:
            return False
        if dismissed_until is not None and current < dismissed_until:
            return False
        return True

    def dismiss(
        self,
        events: Iterable[Mapping[str, object]],
        *,
        now: datetime | None = None,
    ) -> None:
        newest = _newest_low_price_event(events)
        if newest is None:
            return
        current = now or datetime.now()
        payload = {
            "dismissed_event_at": newest.isoformat(timespec="seconds"),
            "dismissed_until": (current + timedelta(days=PROMPT_COOLDOWN_DAYS)).isoformat(timespec="seconds"),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _load(self) -> dict[str, object]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}


class SupportDialog(QDialog):
    """A non-blocking-in-spirit, user-initiated pair of local payment codes."""

    def __init__(self, assets: SupportAssets, parent: QWidget | None = None):
        super().__init__(parent, objectName="supportDialog")
        self.setWindowTitle("支持航价守望")
        self.setWindowIcon(application_icon())
        self.setModal(True)
        self.setMinimumSize(720, 610)
        self.resize(760, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(14)
        root.addWidget(QLabel("如果它帮到了你", objectName="supportTitle"))
        root.addWidget(QLabel(
            "如果航价守望帮你守到了合适的价格、少花了一点时间，欢迎请作者喝杯咖啡。\n"
            "你的支持会让我继续把这个小工具做得更好。",
            objectName="supportIntro",
            wordWrap=True,
        ))

        codes = QHBoxLayout()
        codes.setSpacing(16)
        self.alipay_card = _payment_card("支付宝", "#1677ff", assets.alipay)
        self.wechat_card = _payment_card("微信支付", "#07c160", assets.wechat)
        codes.addWidget(self.alipay_card, 1)
        codes.addWidget(self.wechat_card, 1)
        root.addLayout(codes, 1)

        note = QLabel(
            "完全自愿 · 不支持也不会影响任何功能和正常使用\n感谢每一位旅行家的信任与支持。",
            objectName="supportNote",
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        note.setWordWrap(True)
        root.addWidget(note)

        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton("关闭", objectName="supportCloseButton")
        close_button.setDefault(True)
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        root.addLayout(actions)


def _payment_card(title: str, accent: str, image_path: Path) -> QFrame:
    card = QFrame(objectName="supportPaymentCard")
    card.setProperty("accent", "alipay" if "支付宝" in title else "wechat")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(9)
    title_label = QLabel(title, objectName="supportPaymentTitle", alignment=Qt.AlignmentFlag.AlignCenter)
    title_label.setStyleSheet(f"color: {accent};")
    layout.addWidget(title_label)
    image = QLabel(objectName="supportQrImage", alignment=Qt.AlignmentFlag.AlignCenter)
    image.setMinimumSize(250, 350)
    pixmap = QPixmap(str(image_path))
    if pixmap.isNull():
        image.setText("收款码资源未安装")
    else:
        image.setPixmap(pixmap.scaled(
            250,
            350,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        ))
    layout.addWidget(image, 1)
    layout.addWidget(QLabel("请使用对应应用扫码", objectName="supportPaymentHint", alignment=Qt.AlignmentFlag.AlignCenter))
    return card


def _newest_low_price_event(events: Iterable[Mapping[str, object]]) -> datetime | None:
    occurred = [
        parsed
        for event in events
        if str(event.get("event_type", "")) == "low_price_confirmed"
        and (parsed := _parse_datetime(event.get("occurred_at"))) is not None
    ]
    return max(occurred, default=None)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
