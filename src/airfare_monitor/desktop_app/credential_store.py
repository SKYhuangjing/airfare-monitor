"""SMTP secrets in the operating-system credential store, never YAML."""

from __future__ import annotations

from dataclasses import dataclass, field


SERVICE_NAME = "AirfareMonitor/SMTP"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore:
    def has_secret(self, username: str) -> bool:
        return self.get_secret(username) is not None

    def get_secret(self, username: str) -> str | None:
        try:
            import keyring
            return keyring.get_password(SERVICE_NAME, username)
        except Exception as exc:
            raise CredentialStoreError("Windows 凭据安全存储不可用") from exc

    def save_secret(self, username: str, secret: str) -> None:
        if not username.strip() or not secret:
            raise CredentialStoreError("邮箱账号和授权码不能为空")
        try:
            import keyring
            keyring.set_password(SERVICE_NAME, username.strip(), secret)
        except Exception as exc:
            raise CredentialStoreError("无法保存 SMTP 授权码到 Windows 凭据安全存储") from exc

    def delete_secret(self, username: str) -> None:
        try:
            import keyring
            try:
                keyring.delete_password(SERVICE_NAME, username.strip())
            except keyring.errors.PasswordDeleteError:
                return
        except Exception as exc:
            raise CredentialStoreError("无法删除 SMTP 授权码") from exc
