"""Typed operational-health contracts for the completed ATLAS V4 chain."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.v4.contracts import FrozenModel

ComponentState = Literal["HEALTHY", "DEGRADED", "UNAVAILABLE", "DISABLED"]
ComponentName = Literal["DATABASE", "REDIS", "MT5", "DATA", "MODELS", "LLM"]


class OperationalComponent(FrozenModel):
    name: ComponentName
    state: ComponentState
    configured: bool
    checked_at: datetime
    latency_ms: float = Field(ge=0)
    evidence_count: int = Field(default=0, ge=0)
    message: str = Field(min_length=1, max_length=500)
    issues: tuple[str, ...] = ()


class V4OperationalHealth(FrozenModel):
    engine: Literal["ATLAS"] = "ATLAS"
    version: Literal["4.0.0"] = "4.0.0"
    phase: Literal[15] = 15
    checked_at: datetime
    status: Literal["READY", "DEGRADED", "NOT_READY"]
    database: OperationalComponent
    redis: OperationalComponent
    mt5: OperationalComponent
    data: OperationalComponent
    models: OperationalComponent
    llm: OperationalComponent
    analytical_signal_authority: Literal[True] = True
    browser_signal_authority: Literal[False] = False
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False


__all__ = ["ComponentName", "ComponentState", "OperationalComponent", "V4OperationalHealth"]
