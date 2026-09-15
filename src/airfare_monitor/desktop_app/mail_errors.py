"""Actionable SMTP failure labels with no server response or address echo."""

from __future__ import annotations

import smtplib
import socket
import ssl


def friendly_mail_error(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "邮箱认证失败；请检查账号、授权码和邮箱服务商的 SMTP 开关。"
    if isinstance(exc, ssl.SSLError):
        return "安全连接失败；请核对 SSL/STARTTLS 方式与端口。"
    if isinstance(exc, socket.gaierror):
        return "无法解析 SMTP 服务器地址；请检查服务器名称和网络。"
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionRefusedError)):
        return "连接 SMTP 服务器超时或被拒绝；请检查网络、服务器和端口。"
    if isinstance(exc, smtplib.SMTPException):
        return "SMTP 服务器未接受测试邮件；请检查发件人、收件人和服务商限制。"
    return "测试邮件未发送成功；请检查设置后重试。"
