"""First-launch setup for non-technical desktop users."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..desktop_app.preferences import PreferencesManager
from ..desktop_app.settings_repository import DesktopSettings
from .preferences import RuntimePreferencesForm, preference_card


class OnboardingDialog(QDialog):
    def __init__(self, preferences: PreferencesManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.preferences = preferences
        self.saved_settings: DesktopSettings | None = None
        settings = preferences.load()
        browsers = preferences.refresh_browsers()
        self.setWindowTitle("欢迎使用航价守望")
        self.setModal(True)
        self.setMinimumSize(940, 650)
        self.resize(1040, 720)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._brand_panel())

        content = QWidget(objectName="onboardingContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(38, 32, 38, 28)
        layout.setSpacing(16)
        layout.addWidget(QLabel("首次设置，只需一分钟", objectName="onboardingTitle"))
        layout.addWidget(
            QLabel("选择电脑上的浏览器和运行偏好，之后就可以直接添加航程。", objectName="muted")
        )

        self.form = RuntimePreferencesForm(browsers, settings)
        self.form.redetect_requested.connect(self._redetect)
        layout.addWidget(
            preference_card(
                "浏览器与运行偏好",
                "首次默认显示浏览器运行过程，便于观察查询是否正常。",
                self.form,
            )
        )

        readiness = QFrame(objectName="infoCard")
        readiness_layout = QVBoxLayout(readiness)
        readiness_layout.addWidget(QLabel("本地数据已准备就绪", objectName="sectionTitle"))
        readiness_layout.addWidget(
            QLabel(
                "航程、历史价格和独立浏览器空间都会保存在当前 Windows 用户目录中，覆盖安装不会清除。",
                objectName="muted",
                wordWrap=True,
            )
        )
        layout.addWidget(readiness)
        layout.addStretch()

        button_row = QHBoxLayout()
        exit_button = QPushButton("暂不设置，退出")
        exit_button.clicked.connect(self.reject)
        self.finish_button = QPushButton("保存设置并添加航程", objectName="primary")
        self.finish_button.clicked.connect(self._complete)
        button_row.addWidget(exit_button)
        button_row.addStretch()
        button_row.addWidget(self.finish_button)
        layout.addLayout(button_row)
        root.addWidget(content, 1)
        self._sync_readiness()

    def _brand_panel(self) -> QWidget:
        panel = QFrame(objectName="onboardingBrand")
        panel.setFixedWidth(315)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(34, 44, 34, 34)
        title = QLabel("✈  航价守望", objectName="onboardingBrandTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(title)
        layout.addWidget(QLabel("让每一次出行，都更值得期待", objectName="onboardingBrandText"))
        layout.addStretch()
        message = QLabel("关注航价变化\n把更好的旅程，带到你身边", objectName="onboardingBrandText")
        message.setWordWrap(True)
        layout.addWidget(message)
        return panel

    def _redetect(self) -> None:
        browsers = self.preferences.refresh_browsers()
        current = self.preferences.load()
        self.form.set_browsers(
            browsers,
            preferred_path=current.browser_path,
            preferred_kind=current.browser_kind,
        )
        self._sync_readiness()

    def _sync_readiness(self) -> None:
        self.finish_button.setEnabled(self.form.has_browser)
        self.finish_button.setToolTip("" if self.form.has_browser else "需要先安装 Chrome 或 Edge")

    def _complete(self) -> None:
        try:
            self.saved_settings = self.preferences.save(
                self.form.values(onboarding_completed=True)
            )
        except Exception as exc:
            QMessageBox.warning(self, "设置未保存", str(exc))
            return
        self.accept()
