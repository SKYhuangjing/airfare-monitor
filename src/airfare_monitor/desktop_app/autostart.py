"""Current-user Windows autostart registration for the packaged desktop app."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AirfareMonitor"


class AutostartError(RuntimeError):
    pass


class AutostartManager:
    def __init__(self, command: str | None = None):
        self.command = command or desktop_start_command()

    def is_enabled(self) -> bool:
        if os.name != "nt":
            return False
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
                return str(value) == self.command
        except OSError:
            return False

    def set_enabled(self, enabled: bool) -> None:
        if os.name != "nt":
            if enabled:
                raise AutostartError("开机启动仅支持 Windows")
            return
        try:
            import winreg

            if enabled:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                    winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, self.command)
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise AutostartError("无法更新当前用户的开机启动设置") from exc


def desktop_start_command() -> str:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([str(executable), "--background"])
    pythonw = executable.with_name("pythonw.exe") if os.name == "nt" else executable
    if not pythonw.is_file():
        pythonw = executable
    return subprocess.list2cmdline([str(pythonw), "-m", "airfare_monitor.desktop", "--background"])
