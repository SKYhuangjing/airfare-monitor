"""Non-secret desktop SMTP profile stored beside other per-user settings."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path

import yaml

from ..config import MailSettings, load_settings
from ..mail import SmtpCredentials
from .yaml_files import atomic_write_yaml


@dataclass(frozen=True, slots=True)
class MailProfile:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    security: str = "ssl"
    username: str = ""
    sender: str = ""
    recipients: tuple[str, ...] = ()
    attach_excel: bool = True

    def validate(self, *, require_delivery: bool = False) -> None:
        if self.security not in {"ssl", "starttls"}:
            raise ValueError("邮件安全方式必须是 SSL 或 STARTTLS")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP 端口必须在 1～65535 之间")
        if not isinstance(self.enabled, bool) or not isinstance(self.attach_excel, bool):
            raise ValueError("邮件开关必须是布尔值")
        if not (self.enabled or require_delivery):
            return
        if not self.smtp_host.strip() or any(char.isspace() for char in self.smtp_host):
            raise ValueError("请填写有效的 SMTP 服务器地址")
        if not _is_email(self.username) or not _is_email(self.sender):
            raise ValueError("请填写有效的邮箱账号和发件地址")
        if not self.recipients or not all(_is_email(item) for item in self.recipients):
            raise ValueError("请填写至少一个有效的收件地址")

    def mail_settings(self, baseline: MailSettings) -> MailSettings:
        from dataclasses import replace

        self.validate(require_delivery=True)
        return replace(
            baseline,
            enabled=True,
            smtp_host=self.smtp_host.strip(),
            smtp_port=self.smtp_port,
            security=self.security,
            attach_excel=self.attach_excel,
        )

    def credentials(self, password: str) -> SmtpCredentials:
        if not password:
            raise ValueError("系统凭据中没有找到该邮箱的授权码")
        self.validate(require_delivery=True)
        return SmtpCredentials(
            username=self.username.strip(),
            password=password,
            sender=self.sender.strip(),
            recipients=self.recipients,
        )


class MailProfileRepository:
    def __init__(self, path: str | Path, *, user_root: str | Path):
        self.path = Path(path)
        self.user_root = Path(user_root)

    def load(self) -> MailProfile:
        raw = self._raw()
        value = raw.get("desktop_mail", {})
        if not isinstance(value, dict):
            raise ValueError("desktop_mail 必须是映射")
        recipients = value.get("recipients", [])
        if not isinstance(recipients, list) or not all(isinstance(item, str) for item in recipients):
            raise ValueError("邮件收件人必须是列表")
        profile = MailProfile(
            enabled=_bool(value.get("enabled", False)),
            smtp_host=str(value.get("smtp_host", "")),
            smtp_port=int(value.get("smtp_port", 465)),
            security=str(value.get("security", "ssl")),
            username=str(value.get("username", "")),
            sender=str(value.get("sender", "")),
            recipients=tuple(item.strip() for item in recipients),
            attach_excel=_bool(value.get("attach_excel", True)),
        )
        profile.validate()
        return profile

    def save(self, profile: MailProfile) -> None:
        profile.validate()
        raw = self._raw()
        raw["desktop_mail"] = {
            "enabled": profile.enabled,
            "smtp_host": profile.smtp_host.strip(),
            "smtp_port": profile.smtp_port,
            "security": profile.security,
            "username": profile.username.strip(),
            "sender": profile.sender.strip(),
            "recipients": list(profile.recipients),
            "attach_excel": profile.attach_excel,
        }
        # Never enable the CLI environment-variable mail path from desktop UI.
        atomic_write_yaml(
            self.path,
            raw,
            validate=lambda temporary: load_settings(temporary, project_root=self.user_root),
        )

    def _raw(self) -> dict:
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("settings.yaml 必须是映射")
        return raw


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("邮件开关必须是布尔值")
    return value


def _is_email(value: str) -> bool:
    display, address = parseaddr(value.strip())
    return not display and address == value.strip() and "@" in address and "." in address.rsplit("@", 1)[-1]
