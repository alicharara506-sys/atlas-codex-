"""Governed ATLAS V4 Phase 5 primary-model research contracts."""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel, OfficialTimeframe, RegimeState


class MLTarget(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ModelFamily(str, Enum):
    LOGISTIC_REGRESSION = "LOGISTIC_REGRESSION"
    EXTRA_TREES_STUMPS = "EXTRA_TREES_STUMPS"


class ModelGovernanceStatus(str, Enum):
    EXPERIMENTAL = "EXPERIMENTAL"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    RETIRED = "RETIRED"


class MLSample(FrozenModel):
    sample_id: str = Field(min_length=1, max_length=160)
    instrument: str = Field(min_length=3, max_length=16)
    timeframe: OfficialTimeframe
    regime: RegimeState
    observed_at: datetime
    label_end_at: datetime
    features: dict[str, float] = Field(min_length=1)
    target: MLTarget
    forward_return_net: float

    @model_validator(mode="after")
    def validate_sample(self) -> MLSample:
        if self.label_end_at < self.observed_at:
            raise ValueError("ML label window cannot end before feature observation")
        if any(not name.strip() for name in self.features):
            raise ValueError("ML feature names cannot be empty")
        if any(not math.isfinite(value) for value in self.features.values()):
            raise ValueError("ML feature values must be finite")
        if not math.isfinite(self.forward_return_net) or self.forward_return_net <= -1:
            raise ValueError("forward_return_net must be finite and greater than -1")
        return self


class ModelTrainingConfig(FrozenModel):
    learning_rate: float = Field(default=0.05, gt=0, le=1)
    epochs: int = Field(default=400, ge=50, le=10_000)
    l2_penalty: float = Field(default=0.01, ge=0, le=100)
    extra_trees_estimators: int = Field(default=64, ge=8, le=2_000)
    random_seed: int = 17


class FeatureSchema(FrozenModel):
    names: tuple[str, ...] = Field(min_length=1)
    schema_hash: str = Field(min_length=64, max_length=64)
    source: Literal["VERSIONED_FEATURE_STORE"] = "VERSIONED_FEATURE_STORE"


class ModelEvaluationMetrics(FrozenModel):
    sample_count: int = Field(gt=0)
    long_count: int = Field(ge=0)
    short_count: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    balanced_accuracy: float | None = Field(default=None, ge=0, le=1)
    log_loss: float = Field(ge=0)
    brier_score: float = Field(ge=0, le=1)
    mean_net_return: float
    majority_baseline_mean_net_return: float
    superior_to_majority_baseline: bool
    calibrated: Literal[False] = False


class ExtraTreeStump(FrozenModel):
    feature_index: int = Field(ge=0)
    threshold: float
    left_probability_long: float = Field(ge=0, le=1)
    right_probability_long: float = Field(ge=0, le=1)


class PrimaryModelArtifact(FrozenModel):
    model_id: str = Field(min_length=64, max_length=64)
    model_version: str
    family: ModelFamily
    created_at: datetime
    feature_schema: FeatureSchema
    training_config: ModelTrainingConfig
    supported_instruments: tuple[str, ...] = Field(min_length=1)
    supported_timeframes: tuple[OfficialTimeframe, ...] = Field(min_length=1)
    supported_regimes: tuple[RegimeState, ...] = Field(min_length=1)
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    train_sample_count: int = Field(gt=0)
    validation_metrics: ModelEvaluationMetrics
    test_metrics: ModelEvaluationMetrics
    standardization_means: tuple[float, ...]
    standardization_scales: tuple[float, ...]
    logistic_coefficients: tuple[float, ...] = ()
    logistic_intercept: float | None = None
    extra_tree_stumps: tuple[ExtraTreeStump, ...] = ()
    initial_status: Literal[ModelGovernanceStatus.EXPERIMENTAL] = ModelGovernanceStatus.EXPERIMENTAL
    automatic_promotion: Literal[False] = False
    raw_probability_only: Literal[True] = True
    atlas_probability_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_artifact(self) -> PrimaryModelArtifact:
        width = len(self.feature_schema.names)
        if len(self.standardization_means) != width or len(self.standardization_scales) != width:
            raise ValueError("standardization parameters must match the feature schema")
        if any(scale <= 0 for scale in self.standardization_scales):
            raise ValueError("standardization scales must be positive")
        if self.family is ModelFamily.LOGISTIC_REGRESSION:
            if len(self.logistic_coefficients) != width or self.logistic_intercept is None:
                raise ValueError("logistic artifact parameters are incomplete")
            if self.extra_tree_stumps:
                raise ValueError("logistic artifacts cannot contain tree stumps")
        else:
            if not self.extra_tree_stumps or self.logistic_coefficients or self.logistic_intercept is not None:
                raise ValueError("Extra-Trees stump artifact parameters are incomplete")
            if any(stump.feature_index >= width for stump in self.extra_tree_stumps):
                raise ValueError("tree stump references a feature outside the schema")
        if not self.train_end < self.validation_start <= self.validation_end < self.test_start <= self.test_end:
            raise ValueError("model artifact windows must be chronological and disjoint")
        return self


class ModelTrainingResult(FrozenModel):
    training_run_id: str = Field(min_length=64, max_length=64)
    input_hash: str = Field(min_length=64, max_length=64)
    artifacts: tuple[PrimaryModelArtifact, ...] = Field(min_length=2)
    validation_winner_model_id: str
    winner_automatically_promoted: Literal[False] = False
    holdout_used: Literal[False] = False
    research_only: Literal[True] = True


class PrimaryCandidate(FrozenModel):
    candidate_id: str = Field(min_length=64, max_length=64)
    model_id: str = Field(min_length=64, max_length=64)
    instrument: str
    timeframe: OfficialTimeframe
    regime: RegimeState
    observed_at: datetime
    direction: MLTarget
    raw_probability_long: float = Field(ge=0, le=1)
    raw_direction_probability: float = Field(ge=0.5, le=1)
    calibrated: Literal[False] = False
    atlas_probability: None = None
    candidate_only: Literal[True] = True
    publication_eligible: Literal[False] = False
    execution_enabled: Literal[False] = False


class ModelStatusRecord(FrozenModel):
    status_record_id: str = Field(min_length=64, max_length=64)
    model_id: str = Field(min_length=64, max_length=64)
    status: ModelGovernanceStatus
    changed_at: datetime
    approved_by: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1_000)
    robustness_evidence_id: str | None = Field(default=None, min_length=64, max_length=64)
    decision_influence: Literal[False] = False
    automatic: bool = False


class ModelTrainingRequest(FrozenModel):
    train: tuple[MLSample, ...] = Field(min_length=30)
    validation: tuple[MLSample, ...] = Field(min_length=10)
    test: tuple[MLSample, ...] = Field(min_length=10)
    config: ModelTrainingConfig = ModelTrainingConfig()


class CandidateRequest(FrozenModel):
    model_id: str = Field(min_length=64, max_length=64)
    instrument: str
    timeframe: OfficialTimeframe
    regime: RegimeState
    observed_at: datetime
    features: dict[str, float] = Field(min_length=1)


class ShadowPromotionRequest(FrozenModel):
    approved_by: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1_000)
    robustness_evidence_id: str = Field(min_length=64, max_length=64)


__all__ = [
    "CandidateRequest",
    "ExtraTreeStump",
    "FeatureSchema",
    "MLSample",
    "MLTarget",
    "ModelEvaluationMetrics",
    "ModelFamily",
    "ModelGovernanceStatus",
    "ModelStatusRecord",
    "ModelTrainingConfig",
    "ModelTrainingRequest",
    "ModelTrainingResult",
    "PrimaryCandidate",
    "PrimaryModelArtifact",
    "ShadowPromotionRequest",
]
