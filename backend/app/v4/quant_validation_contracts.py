"""Statistical-authority contracts for ATLAS V4 Phase 9."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel, RegimeState


class QuantDecision(str, Enum):
    PASS = "PASS"
    PASS_WITH_CAUTION = "PASS_WITH_CAUTION"
    VETO = "VETO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class QuantCheckStatus(str, Enum):
    PASS = "PASS"
    CAUTION = "CAUTION"
    VETO = "VETO"
    INSUFFICIENT = "INSUFFICIENT"


class QuantCheckName(str, Enum):
    OOS_EVIDENCE = "OOS_EVIDENCE"
    SAMPLE_SIZE = "SAMPLE_SIZE"
    CALIBRATION = "CALIBRATION"
    ROBUSTNESS = "ROBUSTNESS"
    REGIME_STABILITY = "REGIME_STABILITY"
    MULTIPLE_TESTING = "MULTIPLE_TESTING"
    DATA_QUALITY = "DATA_QUALITY"
    MODEL_DRIFT = "MODEL_DRIFT"
    UNCERTAINTY = "UNCERTAINTY"
    TRANSACTION_COSTS = "TRANSACTION_COSTS"
    EXPECTANCY = "EXPECTANCY"


class RegimeStabilityStatus(str, Enum):
    STABLE = "STABLE"
    WATCH = "WATCH"
    UNSTABLE = "UNSTABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DriftStatus(str, Enum):
    STABLE = "STABLE"
    WATCH = "WATCH"
    DRIFTED = "DRIFTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RegimePerformance(FrozenModel):
    regime: RegimeState
    sample_count: int = Field(ge=0)
    tp_before_sl_rate: float | None = Field(default=None, ge=0, le=1)
    mean_net_return: float | None = None


class RegimeStabilityReport(FrozenModel):
    regime_stability_id: str = Field(min_length=64, max_length=64)
    version: str
    meta_model_id: str = Field(min_length=64, max_length=64)
    created_at: datetime
    status: RegimeStabilityStatus
    evaluated_regime: RegimeState | None = None
    performances: tuple[RegimePerformance, ...]
    minimum_regime_sample_count: int = Field(ge=0)
    worst_regime_mean_net_return: float | None = None
    return_dispersion: float | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=1_000)
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False


class RegimeStabilityRequest(FrozenModel):
    meta_model_id: str = Field(min_length=64, max_length=64)
    current_regime: RegimeState | None = None


class PredictionDriftReport(FrozenModel):
    drift_report_id: str = Field(min_length=64, max_length=64)
    version: str
    meta_model_id: str = Field(min_length=64, max_length=64)
    created_at: datetime
    reference_sample_count: int = Field(ge=0)
    current_sample_count: int = Field(ge=0)
    reference_end: datetime | None = None
    current_start: datetime | None = None
    current_end: datetime | None = None
    population_stability_index: float | None = Field(default=None, ge=0)
    mean_probability_shift: float | None = Field(default=None, ge=0, le=1)
    window_source: Literal["LIVE", "OOS_TEMPORAL", "OOS_FIXED_MODEL"] = "LIVE"
    status: DriftStatus
    reason: str = Field(min_length=1, max_length=1_000)
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_chronology(self) -> PredictionDriftReport:
        if self.status is not DriftStatus.INSUFFICIENT_EVIDENCE:
            if self.reference_end is None or self.current_start is None:
                raise ValueError("available drift evidence requires chronological boundaries")
            if self.reference_end >= self.current_start:
                raise ValueError("drift reference must end before the current window starts")
            if self.population_stability_index is None or self.mean_probability_shift is None:
                raise ValueError("available drift evidence requires PSI and mean shift")
        return self


class PredictionDriftRequest(FrozenModel):
    meta_model_id: str = Field(min_length=64, max_length=64)
    current_meta_prediction_ids: tuple[str, ...] = Field(min_length=1)


class QuantEvidenceCheck(FrozenModel):
    name: QuantCheckName
    status: QuantCheckStatus
    summary: str = Field(min_length=1, max_length=1_000)
    artifact_ids: tuple[str, ...] = ()


class QuantValidationRequest(FrozenModel):
    calibrated_probability_id: str = Field(min_length=64, max_length=64)
    robustness_run_id: str = Field(min_length=64, max_length=64)
    regime_stability_id: str = Field(min_length=64, max_length=64)
    drift_report_id: str = Field(min_length=64, max_length=64)
    backtest_run_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)


class QuantValidationResult(FrozenModel):
    quant_validation_id: str = Field(min_length=64, max_length=64)
    version: str
    created_at: datetime
    instrument: str
    candidate_id: str = Field(min_length=64, max_length=64)
    calibrated_probability_id: str = Field(min_length=64, max_length=64)
    calibration_id: str = Field(min_length=64, max_length=64)
    meta_model_id: str = Field(min_length=64, max_length=64)
    robustness_run_id: str = Field(min_length=64, max_length=64)
    regime_stability_id: str = Field(min_length=64, max_length=64)
    drift_report_id: str = Field(min_length=64, max_length=64)
    backtest_run_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)
    atlas_probability: float | None = Field(default=None, ge=0, le=1)
    decision: QuantDecision
    checks: tuple[QuantEvidenceCheck, ...] = Field(min_length=11, max_length=11)
    reason: str = Field(min_length=1, max_length=2_000)
    quant_veto: bool
    veto_overrides_everything: Literal[True] = True
    ai_override_allowed: Literal[False] = False
    probability_override_allowed: Literal[False] = False
    quant_validation_authority: Literal[True] = True
    grade: None = None
    publication_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision_precedence(self) -> QuantValidationResult:
        statuses = {check.status for check in self.checks}
        names = {check.name for check in self.checks}
        if names != set(QuantCheckName):
            raise ValueError("quant validation must cover every required evidence check exactly once")
        if (self.decision is QuantDecision.VETO) != self.quant_veto:
            raise ValueError("quant_veto must match the VETO decision")
        if QuantCheckStatus.VETO in statuses and self.decision is not QuantDecision.VETO:
            raise ValueError("any veto check requires the final VETO decision")
        if self.decision is QuantDecision.PASS and statuses != {QuantCheckStatus.PASS}:
            raise ValueError("PASS requires every evidence check to pass")
        return self


__all__ = [
    "DriftStatus",
    "PredictionDriftReport",
    "PredictionDriftRequest",
    "QuantCheckName",
    "QuantCheckStatus",
    "QuantDecision",
    "QuantEvidenceCheck",
    "QuantValidationRequest",
    "QuantValidationResult",
    "RegimePerformance",
    "RegimeStabilityReport",
    "RegimeStabilityRequest",
    "RegimeStabilityStatus",
]
