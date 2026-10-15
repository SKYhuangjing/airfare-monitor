"""SMTP secrets in the operating-system credential store, never YAML."""

from __future__ import annotations

from dataclasses import dataclass, field


SERVICE_NAME = "AirfareMonitor/SMTP"
SERVERCHAN_SERVICE = "AirfareMonitor/ServerChan"
SERVERCHAN_ACCOUNT = "sendkey"

# 操作系统级加密存储后端：Windows 凭据管理器 / macOS 钥匙串。
# 其他后端（如明文/文件后端）必须拒绝，邮件凭据不允许降级。
_SECURE_BACKEND_PREFIXES = (
    "keyring.backends.Windows",
    "keyring.backends.macOS",
)


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self, service: str = SERVICE_NAME):
        self.service = service

    @staticmethod
    def _backend():
        import keyring

        backend = keyring.get_keyring()
        module = type(backend).__module__
        if not module.startswith(_SECURE_BACKEND_PREFIXES):
            raise CredentialStoreError("未找到系统凭据安全存储（Windows 凭据管理器 / macOS 钥匙串）；邮件设置不会降级为明文")
        return keyring

    def has_secret(self, username: str) -> bool:
        return self.get_secret(username) is not None

    def get_secret(self, username: str) -> str | None:
        try:
            keyring = self._backend()
            return keyring.get_password(self.service, username)
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("系统凭据安全存储不可用") from exc

    def save_secret(self, username: str, secret: str) -> None:
        if not username.strip() or not secret:
            raise CredentialStoreError("凭据账号和密钥不能为空")
        try:
            keyring = self._backend()
            keyring.set_password(self.service, username.strip(), secret)
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("无法保存密钥到系统凭据安全存储") from exc

    def delete_secret(self, username: str) -> None:
        try:
            keyring = self._backend()
            try:
                keyring.delete_password(self.service, username.strip())
            except keyring.errors.PasswordDeleteError:
                return
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("无法删除系统凭据中的密钥") from exc
