"""Canonical signal publication contracts for ATLAS V4 Phase 11."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.context_contracts import ContextAvailability, RiskLevel
from app.v4.contracts import FrozenModel, OfficialTimeframe, RegimeState
from app.v4.ml_contracts import MLTarget
from app.v4.risk_contracts import EntryZone, StructuralTarget

PUBLICATION_PROBABILITY_THRESHOLD = 0.60


class SignalGrade(str, Enum):
    B = "B"
    A = "A"
    A_PLUS = "A+"
    A_FIVE_PLUS = "A+++++"


class PublicationDecision(str, Enum):
    PUBLISHED = "PUBLISHED"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"
    QUANT_VETO = "QUANT_VETO"
    RISK_VETO = "RISK_VETO"
    EVENT_BLOCK = "EVENT_BLOCK"
    DATA_DEGRADED = "DATA_DEGRADED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    MODEL_UNCERTAIN = "MODEL_UNCERTAIN"


class SignalLineage(FrozenModel):
    candidate_id: str = Field(min_length=64, max_length=64)
    ai_review_bundle_id: str | None = Field(default=None, min_length=64, max_length=64)
    meta_prediction_id: str = Field(min_length=64, max_length=64)
    calibrated_probability_id: str = Field(min_length=64, max_length=64)
    calibration_id: str = Field(min_length=64, max_length=64)
    quant_validation_id: str = Field(min_length=64, max_length=64)
    expectancy_id: str = Field(min_length=64, max_length=64)
    target_plan_id: str = Field(min_length=64, max_length=64)
    exposure_id: str = Field(min_length=64, max_length=64)
    risk_overlay_id: str = Field(min_length=64, max_length=64)
    context_set_id: str = Field(min_length=64, max_length=64)
    feature_set_id: str = Field(min_length=64, max_length=64)
    market_snapshot_id: str = Field(min_length=64, max_length=64)
    backtest_run_id: str = Field(min_length=64, max_length=64)


class PublicationValidation(FrozenModel):
    status: Literal["PASS"] = "PASS"
    reasons: tuple[str, ...] = Field(min_length=1)


class CanonicalSignal(FrozenModel):
    signal_id: str = Field(min_length=64, max_length=64)
    schema_version: Literal["4.0.0"] = "4.0.0"
    instrument: str
    direction: MLTarget
    timeframe: OfficialTimeframe
    entry: EntryZone
    stop_loss: float = Field(gt=0)
    targets: tuple[StructuralTarget, ...] = Field(min_length=3, max_length=3)
    raw_probability: float = Field(ge=0.5, le=1)
    meta_probability: float = Field(ge=0, le=1)
    calibrated_probability: float = Field(ge=PUBLICATION_PROBABILITY_THRESHOLD, le=1)
    grade: SignalGrade
    expected_r: float = Field(gt=0)
    expected_value_net: float = Field(gt=0)
    approved_risk_fraction: float = Field(gt=0, le=0.02)
    regime: RegimeState
    macro_bias: Literal["NOT_DIRECTIONALLY_ASSUMED"] = "NOT_DIRECTIONALLY_ASSUMED"
    macro_availability: ContextAvailability
    event_risk: RiskLevel
    data_quality_score: float = Field(ge=0, le=100)
    quant_validation: PublicationValidation
    risk_validation: PublicationValidation
    drivers: tuple[str, ...] = Field(min_length=1, max_length=64)
    contradictions: tuple[str, ...] = Field(default=(), max_length=64)
    invalidations: tuple[str, ...] = Field(min_length=1, max_length=64)
    lineage: SignalLineage
    model_versions: dict[str, str] = Field(min_length=1, max_length=32)
    created_at: datetime
    expires_at: datetime
    publication_decision: Literal[PublicationDecision.PUBLISHED] = PublicationDecision.PUBLISHED
    signal_authority: Literal[True] = True
    lineage_immutable: Literal[True] = True
    geometry_immutable: Literal[True] = True
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False

    @model_validator(mode="after")
    def validate_canonical_signal(self) -> CanonicalSignal:
        if self.grade is not grade_for_probability(self.calibrated_probability):
            raise ValueError("signal grade must match the exact calibrated-probability boundary")
        if self.created_at >= self.expires_at:
            raise ValueError("signal expiry must be after creation")
        if tuple(target.name for target in self.targets) != ("TP1", "TP2", "TP3"):
            raise ValueError("canonical targets must be ordered TP1, TP2, TP3")
        prices = tuple(target.price for target in self.targets)
        if self.direction is MLTarget.LONG:
            if self.stop_loss >= self.entry.low or not self.entry.high < prices[0] < prices[1] < prices[2]:
                raise ValueError("LONG geometry must order stop, entry, TP1, TP2, TP3")
        elif self.stop_loss <= self.entry.high or not self.entry.low > prices[0] > prices[1] > prices[2]:
            raise ValueError("SHORT geometry must order TP3, TP2, TP1, entry, stop")
        return self


class SignalPublicationRequest(FrozenModel):
    risk_overlay_id: str = Field(min_length=64, max_length=64)
    feature_set_id: str = Field(min_length=64, max_length=64)
    ai_review_bundle_id: str | None = Field(default=None, min_length=64, max_length=64)


class SignalPublicationResult(FrozenModel):
    publication_attempt_id: str = Field(min_length=64, max_length=64)
    version: str
    created_at: datetime
    decision: PublicationDecision
    instrument: str
    candidate_id: str = Field(min_length=64, max_length=64)
    atlas_probability: float | None = Field(default=None, ge=0, le=1)
    grade: SignalGrade | None = None
    signal: CanonicalSignal | None = None
    reasons: tuple[str, ...] = Field(min_length=1, max_length=64)
    signal_published: bool
    sole_publication_authority: Literal[True] = True
    ai_override_allowed: Literal[False] = False
    probability_override_allowed: Literal[False] = False
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False

    @model_validator(mode="after")
    def validate_publication_result(self) -> SignalPublicationResult:
        published = self.decision is PublicationDecision.PUBLISHED
        if published != self.signal_published or published != (self.signal is not None):
            raise ValueError("only PUBLISHED decisions can contain a canonical signal")
        if published != (self.grade is not None):
            raise ValueError("grades are assigned only to published canonical signals")
        return self


class SignalRevisionKind(str, Enum):
    ANNOTATION = "ANNOTATION"
    CORRECTION = "CORRECTION"
    RETRACTION = "RETRACTION"


class SignalRevisionRequest(FrozenModel):
    kind: SignalRevisionKind
    revised_by: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1_000)
    annotation: str = Field(min_length=1, max_length=2_000)
    replacement_signal_id: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_revision_request(self) -> SignalRevisionRequest:
        if (self.kind is SignalRevisionKind.CORRECTION) != (self.replacement_signal_id is not None):
            raise ValueError("a correction must reference one replacement signal")
        return self


class SignalRevision(FrozenModel):
    revision_id: str = Field(min_length=64, max_length=64)
    signal_id: str = Field(min_length=64, max_length=64)
    revision_index: int = Field(ge=1)
    previous_revision_id: str | None = Field(default=None, min_length=64, max_length=64)
    kind: SignalRevisionKind
    revised_by: str
    reason: str
    annotation: str
    replacement_signal_id: str | None = Field(default=None, min_length=64, max_length=64)
    created_at: datetime
    original_signal_mutated: Literal[False] = False
    execution_enabled: Literal[False] = False


def grade_for_probability(probability: float) -> SignalGrade | None:
    """Return the exact V4 grade; sub-60% values are deliberately ungraded."""

    if not 0 <= probability <= 1:
        raise ValueError("probability must be between zero and one")
    if probability < PUBLICATION_PROBABILITY_THRESHOLD:
        return None
    if probability < 0.70:
        return SignalGrade.B
    if probability < 0.80:
        return SignalGrade.A
    if probability < 0.90:
        return SignalGrade.A_PLUS
    return SignalGrade.A_FIVE_PLUS


__all__ = [
    "CanonicalSignal",
    "PUBLICATION_PROBABILITY_THRESHOLD",
    "PublicationDecision",
    "PublicationValidation",
    "SignalGrade",
    "SignalLineage",
    "SignalPublicationRequest",
    "SignalPublicationResult",
    "SignalRevision",
    "SignalRevisionKind",
    "SignalRevisionRequest",
    "grade_for_probability",
]
