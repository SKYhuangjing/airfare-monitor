"""Desktop-only preferences layered into the existing settings YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..config import AppSettings, load_settings
from .yaml_files import atomic_write_yaml


@dataclass(frozen=True, slots=True)
class DesktopSettings:
    browser_kind: str = "auto"
    browser_path: str | None = None
    desktop_notifications: bool = True
    autostart: bool = False


class SettingsRepository:
    def __init__(self, path: str | Path, *, user_root: str | Path):
        self.path = Path(path)
        self.user_root = Path(user_root)

    def load_core(self) -> AppSettings:
        return load_settings(self.path, project_root=self.user_root)

    def load_desktop(self) -> DesktopSettings:
        raw = self._raw()
        desktop = raw.get("desktop", {})
        if not isinstance(desktop, dict):
            raise ValueError("settings.desktop 必须是映射")
        kind = str(desktop.get("browser_kind", "auto")).lower()
        if kind not in {"auto", "chrome", "edge"}:
            raise ValueError("browser_kind 必须是 auto、chrome 或 edge")
        browser_path = desktop.get("browser_path")
        if browser_path is not None and not isinstance(browser_path, str):
            raise ValueError("browser_path 必须是字符串或 null")
        return DesktopSettings(
            browser_kind=kind,
            browser_path=browser_path or None,
            desktop_notifications=_boolean(desktop.get("desktop_notifications", True), "desktop_notifications"),
            autostart=_boolean(desktop.get("autostart", False), "autostart"),
        )

    def save_desktop(self, value: DesktopSettings) -> None:
        if value.browser_kind not in {"auto", "chrome", "edge"}:
            raise ValueError("browser_kind 必须是 auto、chrome 或 edge")
        raw = self._raw()
        raw["desktop"] = {
            "browser_kind": value.browser_kind,
            "browser_path": value.browser_path,
            "desktop_notifications": value.desktop_notifications,
            "autostart": value.autostart,
        }
        atomic_write_yaml(
            self.path,
            raw,
            validate=lambda temporary: load_settings(temporary, project_root=self.user_root),
        )

    def _raw(self) -> dict[str, Any]:
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"无法读取设置：{self.path}") from exc
        if not isinstance(raw, dict):
            raise ValueError("settings.yaml 必须是映射")
        return raw


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是布尔值")
    return value
