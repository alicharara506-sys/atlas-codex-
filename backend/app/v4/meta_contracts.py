"""Time-series meta-labeling and evidence-dependency contracts for ATLAS V4."""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel, RegimeState
from app.v4.ml_contracts import MLTarget, ModelGovernanceStatus
from app.v4.quant_contracts import WalkForwardConfig, WalkForwardResult


class EvidenceFamily(str, Enum):
    TREND = "TREND"
    MOMENTUM = "MOMENTUM"
    STRUCTURE = "STRUCTURE"
    REGIME = "REGIME"
    VOLATILITY = "VOLATILITY"
    MACRO = "MACRO"
    LIQUIDITY = "LIQUIDITY"
    POSITIONING = "POSITIONING"
    NEWS = "NEWS"
    EVENT = "EVENT"


class MetaSession(str, Enum):
    ASIA = "ASIA"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    OVERLAP = "OVERLAP"
    OFF_HOURS = "OFF_HOURS"


class MetaTarget(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class EvidenceObservation(FrozenModel):
    evidence_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    family: EvidenceFamily
    value: float
    upstream_ids: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_observation(self) -> EvidenceObservation:
        if not math.isfinite(self.value):
            raise ValueError("evidence values must be finite")
        if len(set(self.upstream_ids)) != len(self.upstream_ids):
            raise ValueError("evidence upstream IDs cannot repeat")
        if any(not value.strip() or len(value) > 160 for value in self.upstream_ids):
            raise ValueError("evidence upstream IDs must contain 1-160 visible characters")
        return self


class EvidenceFamilyCount(FrozenModel):
    family: EvidenceFamily
    observation_count: int = Field(gt=0)


class EvidenceDependencyReport(FrozenModel):
    report_id: str = Field(min_length=64, max_length=64)
    observations: tuple[EvidenceObservation, ...] = Field(min_length=1)
    family_counts: tuple[EvidenceFamilyCount, ...] = Field(min_length=1)
    redundant_groups: tuple[tuple[str, ...], ...]
    raw_observation_count: int = Field(gt=0)
    independent_component_count: int = Field(gt=0)
    effective_family_count: int = Field(gt=0, le=len(EvidenceFamily))
    redundancy_ratio: float = Field(ge=0, le=1)
    arbitrary_confirmation_score_created: Literal[False] = False
    signal_authority: Literal[False] = False


class EvidenceDependencyRequest(FrozenModel):
    observations: tuple[EvidenceObservation, ...] = Field(min_length=1, max_length=256)


class MetaFeatureVector(FrozenModel):
    raw_primary_probability: float = Field(ge=0.5, le=1)
    model_disagreement: float = Field(ge=0, le=1)
    regime: RegimeState
    volatility_percentile: float = Field(ge=0, le=1)
    spread_bps: float = Field(ge=0, le=1_000)
    session: MetaSession
    event_risk: float = Field(ge=0, le=1)
    macro_state: float = Field(ge=-1, le=1)
    mtf_conflict: float = Field(ge=0, le=1)
    data_quality_score: float = Field(ge=0, le=100)
    ai_contradiction_ratio: float = Field(ge=0, le=1)
    positioning_zscore: float | None = Field(default=None, ge=-20, le=20)
    liquidity_score: float | None = Field(default=None, ge=0, le=1)
    timeframe_code: float = Field(default=0.0, ge=0, le=1)
    evidence_dependency_report_id: str = Field(min_length=64, max_length=64)
    ai_review_bundle_id: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_review_lineage(self) -> MetaFeatureVector:
        if self.ai_contradiction_ratio > 0 and self.ai_review_bundle_id is None:
            raise ValueError("AI contradiction features require an AI review bundle ID")
        return self


class MetaLabelSample(FrozenModel):
    sample_id: str = Field(min_length=1, max_length=160)
    candidate_id: str = Field(min_length=64, max_length=64)
    observed_at: datetime
    label_end_at: datetime
    primary_direction: MLTarget
    features: MetaFeatureVector
    triple_barrier_label_id: str = Field(min_length=64, max_length=64)
    tp_before_sl: bool
    setup_validated: Literal[True] = True
    net_of_costs: Literal[True] = True
    target: MetaTarget
    forward_return_net: float

    @model_validator(mode="after")
    def validate_sample(self) -> MetaLabelSample:
        if self.label_end_at < self.observed_at:
            raise ValueError("meta label cannot end before its observation")
        if not math.isfinite(self.forward_return_net) or self.forward_return_net <= -1:
            raise ValueError("meta forward return must be finite and greater than -1")
        expected = MetaTarget.ACCEPT if self.tp_before_sl else MetaTarget.REJECT
        if self.target is not expected:
            raise ValueError("meta target must represent TP-before-SL outcome")
        return self


class MetaTrainingConfig(FrozenModel):
    walk_forward: WalkForwardConfig
    learning_rate: float = Field(default=0.05, gt=0, le=1)
    epochs: int = Field(default=400, ge=50, le=10_000)
    l2_penalty: float = Field(default=0.01, ge=0, le=100)
    decision_threshold: float = Field(default=0.5, ge=0.5, le=0.5)


class MetaEvaluationMetrics(FrozenModel):
    sample_count: int = Field(gt=0)
    accept_labels: int = Field(ge=0)
    reject_labels: int = Field(ge=0)
    predicted_accept_count: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    balanced_accuracy: float | None = Field(default=None, ge=0, le=1)
    log_loss: float = Field(ge=0)
    brier_score: float = Field(ge=0, le=1)
    accept_all_mean_net_return: float
    filtered_mean_net_return: float
    net_improvement: float
    threshold_tuned_on_test: Literal[False] = False
    calibrated: Literal[False] = False


class MetaFoldEvaluation(FrozenModel):
    fold_index: int = Field(ge=0)
    train_sample_count: int = Field(gt=0)
    validation_metrics: MetaEvaluationMetrics
    test_metrics: MetaEvaluationMetrics


class MetaOOSPrediction(FrozenModel):
    sample_id: str = Field(min_length=1, max_length=160)
    candidate_id: str = Field(min_length=64, max_length=64)
    fold_index: int = Field(ge=0)
    source_validation_run_id: str = Field(min_length=64, max_length=64)
    observed_at: datetime
    label_end_at: datetime
    raw_accept_probability: float = Field(ge=0, le=1)
    tp_before_sl: bool
    regime: RegimeState
    forward_return_net: float = Field(gt=-1)
    out_of_sample: Literal[True] = True
    setup_validated: Literal[True] = True
    net_of_costs: Literal[True] = True


class MetaModelArtifact(FrozenModel):
    meta_model_id: str = Field(min_length=64, max_length=64)
    version: str
    created_at: datetime
    feature_names: tuple[str, ...] = Field(min_length=1)
    feature_schema_hash: str = Field(min_length=64, max_length=64)
    config: MetaTrainingConfig
    walk_forward: WalkForwardResult
    folds: tuple[MetaFoldEvaluation, ...] = Field(min_length=1)
    oos_predictions: tuple[MetaOOSPrediction, ...] = Field(min_length=1)
    aggregate_oos_metrics: MetaEvaluationMetrics
    coefficients: tuple[float, ...]
    intercept: float
    standardization_means: tuple[float, ...]
    standardization_scales: tuple[float, ...]
    initial_status: Literal[ModelGovernanceStatus.EXPERIMENTAL] = ModelGovernanceStatus.EXPERIMENTAL
    material_oos_net_improvement: bool
    threshold_search_performed: Literal[False] = False
    automatic_promotion: Literal[False] = False
    raw_probability_only: Literal[True] = True
    atlas_probability_authority: Literal[False] = False
    publication_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_artifact(self) -> MetaModelArtifact:
        width = len(self.feature_names)
        if not (
            len(self.coefficients)
            == len(self.standardization_means)
            == len(self.standardization_scales)
            == width
        ):
            raise ValueError("meta-model parameters must match the feature schema")
        if any(scale <= 0 for scale in self.standardization_scales):
            raise ValueError("meta-model standardization scales must be positive")
        return self


class MetaTrainingRequest(FrozenModel):
    samples: tuple[MetaLabelSample, ...] = Field(min_length=30)
    config: MetaTrainingConfig


class MetaTrainingResult(FrozenModel):
    training_run_id: str = Field(min_length=64, max_length=64)
    artifact: MetaModelArtifact
    holdout_used: Literal[False] = False
    automatically_promoted: Literal[False] = False
    research_only: Literal[True] = True


class MetaPredictionRequest(FrozenModel):
    meta_model_id: str = Field(min_length=64, max_length=64)
    candidate_id: str = Field(min_length=64, max_length=64)
    observed_at: datetime
    features: MetaFeatureVector


class MetaPrediction(FrozenModel):
    meta_prediction_id: str = Field(min_length=64, max_length=64)
    meta_model_id: str = Field(min_length=64, max_length=64)
    candidate_id: str = Field(min_length=64, max_length=64)
    observed_at: datetime
    raw_accept_probability: float = Field(ge=0, le=1)
    research_classification: MetaTarget
    calibrated: Literal[False] = False
    atlas_probability: None = None
    decision_authority: Literal[False] = False
    publication_eligible: Literal[False] = False
    execution_enabled: Literal[False] = False


__all__ = [
    "EvidenceDependencyReport",
    "EvidenceDependencyRequest",
    "EvidenceFamily",
    "EvidenceFamilyCount",
    "EvidenceObservation",
    "MetaEvaluationMetrics",
    "MetaFeatureVector",
    "MetaFoldEvaluation",
    "MetaLabelSample",
    "MetaModelArtifact",
    "MetaOOSPrediction",
    "MetaPrediction",
    "MetaPredictionRequest",
    "MetaSession",
    "MetaTarget",
    "MetaTrainingConfig",
    "MetaTrainingRequest",
    "MetaTrainingResult",
]
