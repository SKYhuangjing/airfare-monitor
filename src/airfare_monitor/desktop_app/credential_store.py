"""SMTP secrets in the operating-system credential store, never YAML."""

from __future__ import annotations

from dataclasses import dataclass, field


SERVICE_NAME = "AirfareMonitor/SMTP"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore:
    @staticmethod
    def _backend():
        import keyring

        backend = keyring.get_keyring()
        if not type(backend).__module__.startswith("keyring.backends.Windows"):
            raise CredentialStoreError("未找到 Windows 凭据安全存储；邮件设置不会降级为明文")
        return keyring

    def has_secret(self, username: str) -> bool:
        return self.get_secret(username) is not None

    def get_secret(self, username: str) -> str | None:
        try:
            keyring = self._backend()
            return keyring.get_password(SERVICE_NAME, username)
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("Windows 凭据安全存储不可用") from exc

    def save_secret(self, username: str, secret: str) -> None:
        if not username.strip() or not secret:
            raise CredentialStoreError("邮箱账号和授权码不能为空")
        try:
            keyring = self._backend()
            keyring.set_password(SERVICE_NAME, username.strip(), secret)
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("无法保存 SMTP 授权码到 Windows 凭据安全存储") from exc

    def delete_secret(self, username: str) -> None:
        try:
            keyring = self._backend()
            try:
                keyring.delete_password(SERVICE_NAME, username.strip())
            except keyring.errors.PasswordDeleteError:
                return
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError("无法删除 SMTP 授权码") from exc
