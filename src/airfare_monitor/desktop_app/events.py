"""Immutable events passed from the monitoring worker to the desktop layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..models import LegConfig, LegResult, RunReport


@dataclass(frozen=True, slots=True)
class CoordinatorStateChanged:
    state: str
    message: str


@dataclass(frozen=True, slots=True)
class CycleStarted:
    run_id: str
    started_at: datetime
    total_legs: int


@dataclass(frozen=True, slots=True)
class LegStarted:
    run_id: str
    leg: LegConfig
    index: int
    total: int


@dataclass(frozen=True, slots=True)
class LegFinished:
    run_id: str
    result: LegResult
    index: int
    total: int
    minimum_total_cny: Decimal | None


@dataclass(frozen=True, slots=True)
class CycleFinished:
    report: RunReport
    workbook_path: str


@dataclass(frozen=True, slots=True)
class NextRunScheduled:
    due_at: datetime


@dataclass(frozen=True, slots=True)
class ManualAttentionRequested:
    leg_id: str
    message: str


@dataclass(frozen=True, slots=True)
class FatalError:
    category: str
    user_message: str
