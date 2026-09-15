"""Desktop alerts and opt-in personal SMTP configuration."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QGridLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
    QWidget,
)

from ..desktop_app.credential_store import CredentialStore, CredentialStoreError
from ..desktop_app.mail_errors import friendly_mail_error
from ..desktop_app.mail_profile import MailProfile, MailProfileRepository
from ..desktop_app.preferences import PreferencesManager
from ..mail import SmtpCredentials, send_test_message


class _MailTestWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, settings, credentials: SmtpCredentials):
        super().__init__()
        self.settings = settings
        self.credentials = credentials

    def run(self) -> None:
        try:
            send_test_message(self.settings, self.credentials)
        except Exception as exc:
            self.finished.emit(False, friendly_mail_error(exc))
        else:
            self.finished.emit(True, "服务器连接、身份验证和投递请求均已完成。")


class NotificationsPage(QWidget):
    settings_saved = Signal()

    def __init__(
        self,
        preferences: PreferencesManager,
        repository: MailProfileRepository,
        credential_store: CredentialStore | None = None,
    ):
        super().__init__()
        self.preferences = preferences
        self.repository = repository
        self.credential_store = credential_store or CredentialStore()
        self._thread: QThread | None = None
        self._worker: _MailTestWorker | None = None
        self._pending_profile: MailProfile | None = None
        self._pending_secret: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 27)
        root.setSpacing(9)
        root.addWidget(QLabel("通知设置", objectName="pageTitle"))
        root.addWidget(QLabel("只在重要变化时提醒你；桌面和邮件可独立启用。", objectName="muted"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setSpacing(17)

        desktop_card = _card("▣  桌面通知")
        self.desktop_toggle = QCheckBox("开启桌面通知")
        self.desktop_toggle.setChecked(preferences.load().desktop_notifications)
        desktop_card.layout().addWidget(self.desktop_toggle)
        events = QHBoxLayout()
        for event_name in ("低价命中", "查询失败", "需要人工处理", "邮件失败"):
            events.addWidget(QLabel(event_name, objectName="sourcePill"))
        events.addStretch()
        desktop_card.layout().addLayout(events)
        desktop_card.layout().addWidget(QLabel(
            "提醒确认低价、部分/全部失败、需要人工处理及邮件发送失败。普通成功轮次不打扰；应用内最近动态始终保留。",
            objectName="muted", wordWrap=True,
        ))
        desktop_save = QPushButton("保存桌面通知设置")
        desktop_save.clicked.connect(self._save_desktop)
        desktop_card.layout().addWidget(desktop_save, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(desktop_card)

        mail_card = _card("✉  我的邮件通知")
        self.mail_toggle = QCheckBox("启用邮件汇总")
        mail_card.layout().addWidget(self.mail_toggle)
        security_note = QFrame(objectName="infoCard")
        security_layout = QVBoxLayout(security_note)
        security_layout.addWidget(QLabel("授权码保存在 Windows 系统凭据中", objectName="sectionTitle"))
        security_layout.addWidget(QLabel("航价守望不会把授权码写入 YAML、日志、诊断文件或安装包。", objectName="muted", wordWrap=True))
        mail_card.layout().addWidget(security_note)
        self.host = QLineEdit()
        self.host.setPlaceholderText("例如 smtp.example.com")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.security = QComboBox()
        self.security.addItem("SSL", "ssl")
        self.security.addItem("STARTTLS", "starttls")
        self.username = QLineEdit()
        self.username.setPlaceholderText("邮箱账号")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("授权码；留空则使用已保存的授权码")
        self.sender = QLineEdit()
        self.sender.setPlaceholderText("发件邮箱")
        self.recipients = QLineEdit()
        self.recipients.setPlaceholderText("多个收件邮箱用逗号分隔")
        self.attach = QCheckBox("邮件附带 Excel 报告")
        form = QGridLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(15)
        for index, (label, widget) in enumerate((
            ("SMTP 服务器", self.host), ("端口", self.port),
            ("安全方式", self.security), ("SMTP 授权码", self.password),
            ("邮箱账号", self.username), ("发件人", self.sender),
            ("收件人", self.recipients), ("报告附件", self.attach),
        )):
            form.addLayout(_field(label, widget), index // 2, index % 2)
        mail_card.layout().addLayout(form)
        mail_card.layout().addWidget(QLabel(
            "授权码只存入 Windows 系统凭据，不写入配置、日志或诊断文件。请使用邮箱服务商提供的 SMTP 授权码，不要填网页登录密码。",
            objectName="muted", wordWrap=True,
        ))
        actions = QHBoxLayout()
        self.status = QLabel("尚未发送测试邮件", objectName="muted", wordWrap=True)
        actions.addWidget(self.status, 1)
        self.save_button = QPushButton("保存设置")
        self.save_button.clicked.connect(self._save_mail)
        self.test_button = QPushButton("发送测试邮件并保存", objectName="primary")
        self.test_button.clicked.connect(self._test_mail)
        actions.addWidget(self.save_button)
        actions.addWidget(self.test_button)
        mail_card.layout().addLayout(actions)
        layout.addWidget(mail_card)
        layout.addStretch()
        scroll.setWidget(host)
        root.addWidget(scroll, 1)
        self.load()

    def load(self) -> None:
        profile = self.repository.load()
        self.mail_toggle.setChecked(profile.enabled)
        self.host.setText(profile.smtp_host)
        self.port.setValue(profile.smtp_port)
        self.security.setCurrentIndex(max(0, self.security.findData(profile.security)))
        self.username.setText(profile.username)
        self.sender.setText(profile.sender)
        self.recipients.setText(", ".join(profile.recipients))
        self.attach.setChecked(profile.attach_excel)
        self.password.clear()
        self.desktop_toggle.setChecked(self.preferences.load().desktop_notifications)

    def _profile(self, *, require_delivery: bool = False) -> MailProfile:
        profile = MailProfile(
            enabled=self.mail_toggle.isChecked(),
            smtp_host=self.host.text().strip(),
            smtp_port=self.port.value(),
            security=str(self.security.currentData()),
            username=self.username.text().strip(),
            sender=self.sender.text().strip(),
            recipients=tuple(item.strip() for item in self.recipients.text().split(",") if item.strip()),
            attach_excel=self.attach.isChecked(),
        )
        profile.validate(require_delivery=require_delivery)
        return profile

    def _save_desktop(self) -> None:
        try:
            current = self.preferences.load()
            self.preferences.repository.save_desktop(
                replace(current, desktop_notifications=self.desktop_toggle.isChecked())
            )
        except Exception:
            QMessageBox.warning(self, "设置未保存", "桌面通知设置保存失败，请稍后重试。")
            return
        self.settings_saved.emit()
        self.status.setText("桌面通知设置已保存")

    def _save_mail(self) -> None:
        try:
            profile = self._profile()
            self._commit(profile, self.password.text() or None)
        except (ValueError, CredentialStoreError, OSError) as exc:
            QMessageBox.warning(self, "邮件设置未保存", str(exc))
            return
        self.status.setText("邮件设置已保存；可点击测试邮件确认通路")

    def _commit(self, profile: MailProfile, secret: str | None) -> None:
        previous = self.repository.load()
        old_secret = None
        if secret:
            old_secret = self.credential_store.get_secret(profile.username)
            self.credential_store.save_secret(profile.username, secret)
        elif profile.enabled and not self.credential_store.has_secret(profile.username):
            raise CredentialStoreError("请填写 SMTP 授权码并保存")
        try:
            self.repository.save(profile)
        except Exception:
            if secret:
                if old_secret:
                    self.credential_store.save_secret(profile.username, old_secret)
                else:
                    self.credential_store.delete_secret(profile.username)
            raise
        if previous.username and previous.username != profile.username:
            self.credential_store.delete_secret(previous.username)
        self.password.clear()
        self.settings_saved.emit()

    def _test_mail(self) -> None:
        if self._thread is not None:
            return
        try:
            profile = self._profile(require_delivery=True)
            secret = self.password.text() or self.credential_store.get_secret(profile.username)
            credentials = profile.credentials(secret or "")
            settings = profile.mail_settings(self.preferences.repository.load_core().mail)
        except (ValueError, CredentialStoreError) as exc:
            QMessageBox.warning(self, "无法测试邮件", str(exc))
            return
        self._pending_profile = profile
        self._pending_secret = self.password.text() or None
        self.test_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.status.setText("正在连接邮件服务器；窗口仍可正常使用…")
        thread = QThread(self)
        worker = _MailTestWorker(settings, credentials)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._test_finished)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _test_finished(self, success: bool, message: str) -> None:
        self.status.setText(message)
        if success and self._pending_profile is not None:
            try:
                self._commit(self._pending_profile, self._pending_secret)
            except (ValueError, CredentialStoreError, OSError):
                self.status.setText("测试邮件已发送，但设置保存失败；请重新保存。")
            else:
                self.status.setText("测试成功，设置已保存：服务器连接、认证和投递请求均已完成。")
        self._pending_profile = None
        self._pending_secret = None
        self.test_button.setEnabled(True)
        self.save_button.setEnabled(True)

    def _thread_finished(self) -> None:
        self._worker = None
        self._thread = None

    def finish_pending_test(self) -> bool:
        """Keep a mail worker alive until it exits during normal application quit."""

        if self._thread is not None:
            return self._thread.wait(35000)
        return True


def _card(title: str) -> QFrame:
    frame = QFrame(objectName="card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 21, 24, 21)
    layout.setSpacing(14)
    layout.addWidget(QLabel(title, objectName="sectionTitle"))
    return frame


def _field(label: str, widget: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(5)
    layout.addWidget(QLabel(label))
    layout.addWidget(widget)
    return layout
