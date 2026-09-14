"""Reusable, non-technical runtime preference controls."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..desktop_app.browser_detector import BrowserCandidate, BrowserDetector
from ..desktop_app.settings_repository import DesktopSettings


class RuntimePreferencesForm(QWidget):
    redetect_requested = Signal()

    def __init__(
        self,
        browsers: list[BrowserCandidate],
        settings: DesktopSettings,
        *,
        show_redetect: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._initial = settings
        self.browser_combo = QComboBox()
        self.interval_combo = QComboBox()
        for minutes in (30, 60, 120):
            self.interval_combo.addItem(f"{minutes} 分钟", minutes)
        self.show_browser = QCheckBox("显示浏览器运行过程")
        self.desktop_notifications = QCheckBox("开启桌面通知")
        self.autostart = QCheckBox("登录 Windows 后自动启动")
        self.browser_status = QLabel(objectName="muted", wordWrap=True)

        browser_row = QHBoxLayout()
        browser_row.addWidget(self.browser_combo, 1)
        if show_redetect:
            redetect = QPushButton("重新检测")
            redetect.clicked.connect(self.redetect_requested.emit)
            browser_row.addWidget(redetect)

        form = QFormLayout()
        form.setSpacing(14)
        form.addRow("用于查询的浏览器", _layout_widget(browser_row))
        form.addRow("自动查询间隔", self.interval_combo)
        form.addRow("浏览器窗口", self.show_browser)
        form.addRow("价格提醒", self.desktop_notifications)
        form.addRow("开机启动", self.autostart)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.browser_status)
        self.set_browsers(browsers, preferred_path=settings.browser_path, preferred_kind=settings.browser_kind)
        self.load(settings)

    @property
    def has_browser(self) -> bool:
        return isinstance(self.browser_combo.currentData(), BrowserCandidate)

    def set_browsers(
        self,
        browsers: list[BrowserCandidate],
        *,
        preferred_path: str | None = None,
        preferred_kind: str = "auto",
    ) -> None:
        self.browser_combo.clear()
        selected_index = -1
        preferred = BrowserDetector.select(
            browsers,
            preferred_kind=preferred_kind,
            preferred_path=preferred_path,
        )
        for index, candidate in enumerate(browsers):
            version = f" · {candidate.version}" if candidate.version else ""
            name = "Google Chrome" if candidate.kind == "chrome" else "Microsoft Edge"
            self.browser_combo.addItem(f"{name}{version}", candidate)
            if candidate == preferred:
                selected_index = index
        if not browsers:
            self.browser_combo.addItem("未检测到 Chrome 或 Edge", None)
            self.browser_combo.setEnabled(False)
            self.browser_status.setText("需要先安装 Chrome 或 Edge，航价守望不会使用你的日常浏览器数据。")
        else:
            self.browser_combo.setEnabled(True)
            self.browser_combo.setCurrentIndex(max(0, selected_index))
            self.browser_status.setText("查询会使用航价守望自己的独立浏览器空间，不读取日常浏览记录。")

    def load(self, settings: DesktopSettings) -> None:
        self._initial = settings
        index = self.interval_combo.findData(settings.interval_minutes)
        self.interval_combo.setCurrentIndex(max(0, index))
        self.show_browser.setChecked(settings.show_browser)
        self.desktop_notifications.setChecked(settings.desktop_notifications)
        self.autostart.setChecked(settings.autostart)

    def values(self, *, onboarding_completed: bool | None = None) -> DesktopSettings:
        candidate = self.browser_combo.currentData()
        completed = self._initial.onboarding_completed if onboarding_completed is None else onboarding_completed
        return replace(
            self._initial,
            browser_kind=candidate.kind if isinstance(candidate, BrowserCandidate) else "auto",
            browser_path=str(candidate.path) if isinstance(candidate, BrowserCandidate) else None,
            interval_minutes=int(self.interval_combo.currentData()),
            show_browser=self.show_browser.isChecked(),
            desktop_notifications=self.desktop_notifications.isChecked(),
            autostart=self.autostart.isChecked(),
            onboarding_completed=completed,
        )


def preference_card(title: str, subtitle: str, form: QWidget) -> QFrame:
    card = QFrame(objectName="card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(8)
    heading = QLabel(title, objectName="sectionTitle")
    layout.addWidget(heading)
    layout.addWidget(QLabel(subtitle, objectName="muted", wordWrap=True))
    layout.addSpacing(8)
    layout.addWidget(form)
    return card


def _layout_widget(layout: QHBoxLayout) -> QWidget:
    widget = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    widget.setLayout(layout)
    return widget
