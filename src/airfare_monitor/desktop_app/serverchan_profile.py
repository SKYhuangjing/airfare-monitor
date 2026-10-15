"""Non-secret ServerChan push profile stored beside other per-user settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import load_settings
from .yaml_files import atomic_write_yaml


@dataclass(frozen=True, slots=True)
class ServerChanProfile:
    enabled: bool = False

    def validate(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("方糖推送开关必须是布尔值")


class ServerChanProfileRepository:
    def __init__(self, path: str | Path, *, user_root: str | Path):
        self.path = Path(path)
        self.user_root = Path(user_root)

    def load(self) -> ServerChanProfile:
        value = self._raw().get("desktop_serverchan", {})
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise ValueError("desktop_serverchan 必须是映射")
        profile = ServerChanProfile(enabled=_bool(value.get("enabled", False)))
        profile.validate()
        return profile

    def save(self, profile: ServerChanProfile) -> None:
        profile.validate()
        raw = self._raw()
        raw["desktop_serverchan"] = {"enabled": profile.enabled}
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
        raise ValueError("方糖推送开关必须是布尔值")
    return value
