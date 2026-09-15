"""Detect an interrupted desktop session without retaining user data."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


class DesktopSessionState:
    def __init__(self, logs_dir: Path):
        self.path = logs_dir / "running-session.json"
        self._begun = False

    def begin(self) -> bool:
        """Return whether the previous session failed to finish cleanly."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        previous_unclean = self.path.exists()
        payload = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "pid": os.getpid(),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, self.path)
        self._begun = True
        return previous_unclean

    def finish(self) -> None:
        if self._begun:
            self.path.unlink(missing_ok=True)
            self._begun = False
