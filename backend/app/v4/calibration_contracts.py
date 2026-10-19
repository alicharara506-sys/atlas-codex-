"""Governed probability-calibration contracts for ATLAS V4 Phase 8."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Final, Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel

PROBABILITY_SEMANTICS: Final = "P(TP before SL | validated setup and available evidence)"


class CalibrationStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class CalibrationMethod(str, Enum):
    NONE = "NONE"
    IDENTITY = "IDENTITY"
    PLATT = "PLATT"
    ISOTONIC = "ISOTONIC"


class CalibrationUncertainty(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    UNAVAILABLE = "UNAVAILABLE"


class ReliabilityBin(FrozenModel):
    lower_bound: float = Field(ge=0, le=1)
    upper_bound: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=0)
    mean_probability: float | None = Field(default=None, ge=0, le=1)
    observed_tp_before_sl_rate: float | None = Field(default=None, ge=0, le=1)
    confidence_low: float | None = Field(default=None, ge=0, le=1)
    confidence_high: float | None = Field(default=None, ge=0, le=1)


class CalibrationMetrics(FrozenModel):
    sample_count: int = Field(gt=0)
    brier_score: float = Field(ge=0, le=1)
    log_loss: float = Field(ge=0)
    expected_calibration_error: float = Field(ge=0, le=1)
    reliability_bins: tuple[ReliabilityBin, ...] = Field(min_length=2)


class CalibrationArtifact(FrozenModel):
    calibration_id: str = Field(min_length=64, max_length=64)
    version: str
    meta_model_id: str = Field(min_length=64, max_length=64)
    source_validation_run_id: str = Field(min_length=64, max_length=64)
    created_at: datetime
    status: CalibrationStatus
    method: CalibrationMethod
    reason: str = Field(min_length=1, max_length=1_000)
    probability_semantics: Literal[
        "P(TP before SL | validated setup and available evidence)"
    ] = PROBABILITY_SEMANTICS
    sample_count: int = Field(ge=0)
    tp_before_sl_count: int = Field(ge=0)
    sl_before_tp_count: int = Field(ge=0)
    fit_sample_count: int = Field(ge=0)
    evaluation_sample_count: int = Field(ge=0)
    fit_start: datetime | None = None
    fit_end: datetime | None = None
    evaluation_start: datetime | None = None
    evaluation_end: datetime | None = None
    pre_calibration_metrics: CalibrationMetrics | None = None
    post_calibration_metrics: CalibrationMetrics | None = None
    platt_slope: float | None = None
    platt_intercept: float | None = None
    isotonic_thresholds: tuple[float, ...] = ()
    isotonic_values: tuple[float, ...] = ()
    uncertainty: CalibrationUncertainty
    high_probability_rate: float | None = Field(default=None, ge=0, le=1)
    calibration_audit_required: bool
    atlas_probability_available: bool
    raw_primary_probability_used_directly: Literal[False] = False
    llm_confidence_used: Literal[False] = False
    committee_vote_used: Literal[False] = False
    publication_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_artifact(self) -> CalibrationArtifact:
        if self.status is CalibrationStatus.AVAILABLE:
            if self.method is CalibrationMethod.NONE or not self.atlas_probability_available:
                raise ValueError("available calibration requires a fitted method")
            if self.pre_calibration_metrics is None or self.post_calibration_metrics is None:
                raise ValueError("available calibration requires OOS evaluation metrics")
            if not self.fit_end or not self.evaluation_start or self.fit_end >= self.evaluation_start:
                raise ValueError("calibration fit and evaluation periods must be chronological")
            if self.method is CalibrationMethod.PLATT:
                if self.platt_slope is None or self.platt_intercept is None:
                    raise ValueError("Platt artifacts require slope and intercept")
            elif self.method is CalibrationMethod.ISOTONIC and (
                not self.isotonic_thresholds
                or len(self.isotonic_thresholds) != len(self.isotonic_values)
            ):
                raise ValueError("isotonic artifacts require aligned thresholds and values")
        elif self.method is not CalibrationMethod.NONE or self.atlas_probability_available:
            raise ValueError("insufficient calibration evidence cannot expose Atlas probability")
        return self


class CalibrationTrainingRequest(FrozenModel):
    meta_model_id: str = Field(min_length=64, max_length=64)


class CalibratedProbabilityRequest(FrozenModel):
    calibration_id: str = Field(min_length=64, max_length=64)
    meta_prediction_id: str = Field(min_length=64, max_length=64)


class CalibratedProbability(FrozenModel):
    calibrated_probability_id: str = Field(min_length=64, max_length=64)
    calibration_id: str = Field(min_length=64, max_length=64)
    meta_prediction_id: str = Field(min_length=64, max_length=64)
    probability_semantics: Literal[
        "P(TP before SL | validated setup and available evidence)"
    ] = PROBABILITY_SEMANTICS
    atlas_probability: float | None = Field(default=None, ge=0, le=1)
    uncertainty: CalibrationUncertainty
    evidence_status: CalibrationStatus
    # Legacy serialized field name retained for persisted-record compatibility.
    # It now means the current canonical publication threshold (60%).
    meets_80_percent_research_threshold: bool
    grade: None = None
    quant_validated: Literal[False] = False
    publication_eligible: Literal[False] = False
    execution_enabled: Literal[False] = False


__all__ = [
    "PROBABILITY_SEMANTICS",
    "CalibratedProbability",
    "CalibratedProbabilityRequest",
    "CalibrationArtifact",
    "CalibrationMethod",
    "CalibrationMetrics",
    "CalibrationStatus",
    "CalibrationTrainingRequest",
    "CalibrationUncertainty",
    "ReliabilityBin",
]
