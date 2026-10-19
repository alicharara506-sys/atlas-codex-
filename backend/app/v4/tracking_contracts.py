"""Server-side signal lifecycle and lineage contracts for ATLAS V4 Phase 12."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.ai_contracts import AIReviewBundle
from app.v4.calibration_contracts import CalibratedProbability, CalibrationArtifact
from app.v4.context_contracts import ContextSet
from app.v4.contracts import FeatureSet, FrozenModel, MarketSnapshot
from app.v4.meta_contracts import MetaPrediction
from app.v4.ml_contracts import PrimaryCandidate
from app.v4.quant_contracts import BacktestRun
from app.v4.quant_validation_contracts import QuantValidationResult
from app.v4.risk_contracts import (
    ExpectancyReport,
    PortfolioExposureReport,
    RiskOverlayResult,
    StructuralTargetPlan,
)
from app.v4.signal_contracts import CanonicalSignal, SignalPublicationResult


class SignalLifecycleState(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    ACTIVE = "ACTIVE"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    TP3_HIT = "TP3_HIT"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"
    EVENT_CANCELLED = "EVENT_CANCELLED"
    DATA_CANCELLED = "DATA_CANCELLED"
    RISK_CANCELLED = "RISK_CANCELLED"


class TrackingEventType(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    ACTIVE = "ACTIVE"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    TP3_HIT = "TP3_HIT"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"
    EVENT_CANCELLED = "EVENT_CANCELLED"
    DATA_CANCELLED = "DATA_CANCELLED"
    RISK_CANCELLED = "RISK_CANCELLED"


TERMINAL_STATES = frozenset(
    {
        SignalLifecycleState.TP3_HIT,
        SignalLifecycleState.STOPPED,
        SignalLifecycleState.EXPIRED,
        SignalLifecycleState.INVALIDATED,
        SignalLifecycleState.CANCELLED,
        SignalLifecycleState.EVENT_CANCELLED,
        SignalLifecycleState.DATA_CANCELLED,
        SignalLifecycleState.RISK_CANCELLED,
    }
)


class SignalTrackingState(FrozenModel):
    tracking_id: str = Field(min_length=64, max_length=64)
    version: str
    signal_id: str = Field(min_length=64, max_length=64)
    instrument: str
    state: SignalLifecycleState
    started_at: datetime
    activated_at: datetime | None = None
    terminal_at: datetime | None = None
    entry_fill: float | None = Field(default=None, gt=0)
    highest_target_hit: int = Field(default=0, ge=0, le=3)
    live_price: float | None = Field(default=None, gt=0)
    live_pips: float | None = None
    live_r: float | None = None
    mfe_price: float = Field(default=0, ge=0)
    mae_price: float = Field(default=0, ge=0)
    distance_to_stop: float | None = Field(default=None, ge=0)
    distance_to_next_target: float | None = Field(default=None, ge=0)
    elapsed_seconds: float = Field(ge=0)
    spread: float | None = Field(default=None, ge=0)
    pip_size: float | None = Field(default=None, gt=0)
    last_snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    last_updated_at: datetime
    event_sequence: int = Field(ge=0)
    terminal: bool
    server_authoritative: Literal[True] = True
    browser_authority: Literal[False] = False
    history_deletion_allowed: Literal[False] = False
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False

    @model_validator(mode="after")
    def validate_tracking_state(self) -> SignalTrackingState:
        is_terminal = self.state in TERMINAL_STATES
        if self.terminal != is_terminal:
            raise ValueError("terminal flag must match lifecycle state")
        if is_terminal != (self.terminal_at is not None):
            raise ValueError("terminal states require exactly one terminal timestamp")
        if (
            self.state is not SignalLifecycleState.PENDING_ENTRY
            and self.entry_fill is None
            and self.state
            not in {
                SignalLifecycleState.EXPIRED,
                SignalLifecycleState.CANCELLED,
                SignalLifecycleState.EVENT_CANCELLED,
                SignalLifecycleState.DATA_CANCELLED,
                SignalLifecycleState.RISK_CANCELLED,
                SignalLifecycleState.INVALIDATED,
            }
        ):
            raise ValueError("activated lifecycle states require an entry fill")
        return self


class SignalTrackingEvent(FrozenModel):
    event_id: str = Field(min_length=64, max_length=64)
    signal_id: str = Field(min_length=64, max_length=64)
    sequence: int = Field(ge=1)
    event_type: TrackingEventType
    occurred_at: datetime
    market_snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    price: float | None = Field(default=None, gt=0)
    live_r: float | None = None
    reason: str = Field(min_length=1, max_length=1_000)
    actor: str = Field(default="ATLAS_SERVER", min_length=1, max_length=160)
    append_only: Literal[True] = True
    execution_enabled: Literal[False] = False


class SignalTrackingRequest(FrozenModel):
    snapshot_id: str = Field(min_length=64, max_length=64)
    current_context_set_id: str | None = Field(default=None, min_length=64, max_length=64)
    current_risk_overlay_id: str | None = Field(default=None, min_length=64, max_length=64)


class SignalTrackingResponse(FrozenModel):
    state: SignalTrackingState
    new_events: tuple[SignalTrackingEvent, ...]
    reused_snapshot: bool


class TrackingControlAction(str, Enum):
    INVALIDATE = "INVALIDATE"
    CANCEL = "CANCEL"


class SignalTrackingControlRequest(FrozenModel):
    action: TrackingControlAction
    actor: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1_000)


class SignalLineageBundle(FrozenModel):
    signal: CanonicalSignal
    publication: SignalPublicationResult
    market_snapshot: MarketSnapshot
    features: FeatureSet
    context: ContextSet
    primary_candidate: PrimaryCandidate
    ai_review: AIReviewBundle | None = None
    meta_prediction: MetaPrediction
    calibration: CalibrationArtifact
    calibrated_probability: CalibratedProbability
    quant_validation: QuantValidationResult
    backtest: BacktestRun
    expectancy: ExpectancyReport
    structural_targets: StructuralTargetPlan
    portfolio_exposure: PortfolioExposureReport
    risk_overlay: RiskOverlayResult
    reconstructed_from_persisted_ids: Literal[True] = True
    hidden_chain_of_thought_exposed: Literal[False] = False
    execution_enabled: Literal[False] = False


__all__ = [
    "TERMINAL_STATES",
    "SignalLifecycleState",
    "SignalLineageBundle",
    "SignalTrackingControlRequest",
    "SignalTrackingEvent",
    "SignalTrackingRequest",
    "SignalTrackingResponse",
    "SignalTrackingState",
    "TrackingControlAction",
    "TrackingEventType",
]
