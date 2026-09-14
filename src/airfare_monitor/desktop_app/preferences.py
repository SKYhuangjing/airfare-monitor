"""Validated desktop preference workflow shared by onboarding and settings UI."""

from __future__ import annotations

from dataclasses import replace
from .autostart import AutostartManager
from .browser_detector import BrowserCandidate, BrowserDetector
from .settings_repository import DesktopSettings, SettingsRepository


class PreferencesError(ValueError):
    pass


class PreferencesManager:
    def __init__(
        self,
        repository: SettingsRepository,
        *,
        browser_detector: BrowserDetector | None = None,
        autostart: AutostartManager | None = None,
    ):
        self.repository = repository
        self.browser_detector = browser_detector or BrowserDetector()
        self.autostart = autostart or AutostartManager()
        self._browsers: list[BrowserCandidate] = []

    def load(self) -> DesktopSettings:
        return self.repository.load_desktop()

    def refresh_browsers(self) -> list[BrowserCandidate]:
        settings = self.load()
        self._browsers = self.browser_detector.detect(preferred_path=settings.browser_path)
        return list(self._browsers)

    @property
    def browsers(self) -> list[BrowserCandidate]:
        if not self._browsers:
            return self.refresh_browsers()
        return list(self._browsers)

    def selected_browser(self, settings: DesktopSettings | None = None) -> BrowserCandidate | None:
        value = settings or self.load()
        return self.browser_detector.select(
            self.browsers,
            preferred_kind=value.browser_kind,
            preferred_path=value.browser_path,
        )

    def save(self, settings: DesktopSettings) -> DesktopSettings:
        selected = self.browser_detector.select(
            self.browsers,
            preferred_kind=settings.browser_kind,
            preferred_path=settings.browser_path,
        )
        if settings.onboarding_completed and selected is None:
            raise PreferencesError("需要选择电脑上已安装的 Chrome 或 Edge")
        canonical = replace(
            settings,
            browser_kind=selected.kind if selected else "auto",
            browser_path=str(selected.path.resolve()) if selected else None,
        )
        previous = self.load()
        previous_autostart = self.autostart.is_enabled()
        self.repository.save_desktop(canonical)
        try:
            self.autostart.set_enabled(canonical.autostart)
        except Exception:
            self.repository.save_desktop(previous)
            try:
                self.autostart.set_enabled(previous_autostart)
            except Exception:
                pass
            raise
        return canonical
