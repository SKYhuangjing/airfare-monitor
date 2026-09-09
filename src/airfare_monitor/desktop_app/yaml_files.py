"""Safe YAML persistence shared by desktop repositories."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml


def atomic_write_yaml(path: str | Path, payload: dict[str, Any], *, validate: Callable[[Path], None]) -> None:
    """Write, validate, fsync and atomically replace a YAML file.

    The preceding version is kept as one adjacent ``.bak`` file.  A validation
    failure leaves the current configuration untouched.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
        ) as handle:
            temporary_name = handle.name
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary = Path(temporary_name)
        validate(temporary)
        if destination.exists():
            shutil.copy2(destination, destination.with_suffix(destination.suffix + ".bak"))
        os.replace(temporary, destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
