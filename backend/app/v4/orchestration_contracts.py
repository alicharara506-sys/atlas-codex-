"""Typed contracts for the persistent ATLAS V4 background worker."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from app.v4.contracts import FrozenModel


class OrchestrationStage(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    KRONOS_SHADOW = "KRONOS_SHADOW"
    FEATURES = "FEATURES"
    CONTEXT = "CONTEXT"
    PRIMARY_MODEL = "PRIMARY_MODEL"
    PRIMARY_CANDIDATE = "PRIMARY_CANDIDATE"
    AI_REVIEW = "AI_REVIEW"
    META_MODEL = "META_MODEL"
    CALIBRATION = "CALIBRATION"
    QUANT_VALIDATION = "QUANT_VALIDATION"
    RISK = "RISK"
    PUBLICATION = "PUBLICATION"


class OrchestrationStageState(str, Enum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class OrchestrationRunState(str, Enum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_BLOCKERS = "COMPLETED_WITH_BLOCKERS"
    FAILED = "FAILED"


class OrchestrationStageResult(FrozenModel):
    stage: OrchestrationStage
    state: OrchestrationStageState
    message: str = Field(min_length=1, max_length=1_000)
    evidence_id: str | None = Field(default=None, min_length=64, max_length=64)
    blocker_code: str | None = Field(default=None, min_length=1, max_length=160)
    latency_ms: float = Field(ge=0)


class InstrumentOrchestrationResult(FrozenModel):
    instrument: str = Field(min_length=3, max_length=16)
    state: OrchestrationRunState
    stages: tuple[OrchestrationStageResult, ...] = Field(min_length=1)
    blocker_codes: tuple[str, ...] = ()
    snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    feature_set_id: str | None = Field(default=None, min_length=64, max_length=64)
    kronos_bundle_id: str | None = Field(default=None, min_length=64, max_length=64)
    context_set_id: str | None = Field(default=None, min_length=64, max_length=64)
    model_id: str | None = Field(default=None, min_length=64, max_length=64)
    robustness_run_id: str | None = Field(default=None, min_length=64, max_length=64)
    candidate_id: str | None = Field(default=None, min_length=64, max_length=64)
    ai_review_bundle_id: str | None = Field(default=None, min_length=64, max_length=64)
    meta_model_id: str | None = Field(default=None, min_length=64, max_length=64)
    meta_prediction_id: str | None = Field(default=None, min_length=64, max_length=64)
    calibration_id: str | None = Field(default=None, min_length=64, max_length=64)
    calibrated_probability_id: str | None = Field(default=None, min_length=64, max_length=64)
    quant_validation_id: str | None = Field(default=None, min_length=64, max_length=64)
    risk_overlay_id: str | None = Field(default=None, min_length=64, max_length=64)
    publication_attempt_id: str | None = Field(default=None, min_length=64, max_length=64)
    signal_id: str | None = Field(default=None, min_length=64, max_length=64)
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False


class OrchestrationRun(FrozenModel):
    run_id: str = Field(min_length=64, max_length=64)
    started_at: datetime
    completed_at: datetime
    state: OrchestrationRunState
    instruments: tuple[InstrumentOrchestrationResult, ...] = Field(min_length=1)
    completed_instruments: int = Field(ge=0)
    blocked_instruments: int = Field(ge=0)
    failed_instruments: int = Field(ge=0)
    signals_published: int = Field(ge=0)
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False


class OrchestrationStatus(FrozenModel):
    engine: Literal["ATLAS"] = "ATLAS"
    version: Literal["4.0.0"] = "4.0.0"
    enabled: bool
    running: bool
    cycle_in_progress: bool
    interval_seconds: int = Field(ge=10, le=86_400)
    instruments: tuple[str, ...] = Field(min_length=1)
    current_instrument: str | None = Field(default=None, min_length=3, max_length=16)
    current_stage: str | None = Field(default=None, min_length=1, max_length=80)
    instruments_completed: int = Field(default=0, ge=0)
    instruments_total: int = Field(default=0, ge=0)
    cycles_started: int = Field(ge=0)
    cycles_completed: int = Field(ge=0)
    consecutive_cycle_failures: int = Field(ge=0)
    service_started_at: datetime | None = None
    current_run_started_at: datetime | None = None
    last_cycle_started_at: datetime | None = None
    last_cycle_completed_at: datetime | None = None
    next_cycle_at: datetime | None = None
    last_run_id: str | None = Field(default=None, min_length=64, max_length=64)
    last_state: OrchestrationRunState | None = None
    last_error: str | None = Field(default=None, max_length=1_000)
    latest_results: tuple[InstrumentOrchestrationResult, ...] = ()
    analytical_signal_authority: Literal[True] = True
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False


__all__ = [
    "InstrumentOrchestrationResult",
    "OrchestrationRun",
    "OrchestrationRunState",
    "OrchestrationStage",
    "OrchestrationStageResult",
    "OrchestrationStageState",
    "OrchestrationStatus",
]
