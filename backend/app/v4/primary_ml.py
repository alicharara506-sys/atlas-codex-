"""CPU-only governed primary-model research for ATLAS V4 Phase 5."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime
from itertools import pairwise
from statistics import fmean, pstdev

from app.core.store import RecordStore, stable_key
from app.v4.ml_contracts import (
    CandidateRequest,
    ExtraTreeStump,
    FeatureSchema,
    MLSample,
    MLTarget,
    ModelEvaluationMetrics,
    ModelFamily,
    ModelGovernanceStatus,
    ModelStatusRecord,
    ModelTrainingConfig,
    ModelTrainingResult,
    PrimaryCandidate,
    PrimaryModelArtifact,
    ShadowPromotionRequest,
)
from app.v4.quant_contracts import RobustnessResult, RobustnessStatus
from app.v4.robustness import ROBUSTNESS_COLLECTION

MODEL_ARTIFACT_COLLECTION = "v4_primary_model_artifacts"
MODEL_STATUS_COLLECTION = "v4_primary_model_status"
MODEL_TRAINING_COLLECTION = "v4_primary_model_training_runs"
PRIMARY_CANDIDATE_COLLECTION = "v4_primary_candidates"
_STATUS_ORDER = {
    ModelGovernanceStatus.EXPERIMENTAL: 0,
    ModelGovernanceStatus.SHADOW: 1,
    ModelGovernanceStatus.ACTIVE: 2,
    ModelGovernanceStatus.DEGRADED: 3,
    ModelGovernanceStatus.RETIRED: 4,
}


class PrimaryMLTrainer:
    """Train deterministic logistic and Extra-Trees-stump research challengers."""

    version = "primary-ml-research-v1"

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def train(
        self,
        train: tuple[MLSample, ...],
        validation: tuple[MLSample, ...],
        test: tuple[MLSample, ...],
        config: ModelTrainingConfig | None = None,
    ) -> ModelTrainingResult:
        resolved = config or ModelTrainingConfig()
        schema = _validate_dataset(train, validation, test)
        input_hash = stable_key(
            [sample.model_dump(mode="json") for sample in train],
            [sample.model_dump(mode="json") for sample in validation],
            [sample.model_dump(mode="json") for sample in test],
        )
        run_id = stable_key(self.version, input_hash, resolved.model_dump(mode="json"))
        existing = next(
            (row for row in self.store.values(MODEL_TRAINING_COLLECTION) if row.get("training_run_id") == run_id),
            None,
        )
        if existing is not None:
            return ModelTrainingResult.model_validate(existing)

        means, scales = _standardization(train, schema.names)
        train_x = _matrix(train, schema.names, means, scales)
        train_y = _targets(train)
        validation_x = _matrix(validation, schema.names, means, scales)
        test_x = _matrix(test, schema.names, means, scales)
        baseline_target = MLTarget.LONG if sum(train_y) >= len(train_y) / 2 else MLTarget.SHORT

        coefficients, intercept = _fit_logistic(train_x, train_y, resolved)
        logistic = self._artifact(
            family=ModelFamily.LOGISTIC_REGRESSION,
            schema=schema,
            config=resolved,
            train=train,
            validation=validation,
            test=test,
            validation_probabilities=tuple(_logistic_probability(row, coefficients, intercept) for row in validation_x),
            test_probabilities=tuple(_logistic_probability(row, coefficients, intercept) for row in test_x),
            baseline_target=baseline_target,
            means=means,
            scales=scales,
            input_hash=input_hash,
            coefficients=coefficients,
            intercept=intercept,
        )
        stumps = _fit_extra_tree_stumps(train_x, train_y, resolved)
        extra_trees = self._artifact(
            family=ModelFamily.EXTRA_TREES_STUMPS,
            schema=schema,
            config=resolved,
            train=train,
            validation=validation,
            test=test,
            validation_probabilities=tuple(_stump_probability(row, stumps) for row in validation_x),
            test_probabilities=tuple(_stump_probability(row, stumps) for row in test_x),
            baseline_target=baseline_target,
            means=means,
            scales=scales,
            input_hash=input_hash,
            stumps=stumps,
        )
        artifacts = (logistic, extra_trees)
        winner = max(
            artifacts,
            key=lambda artifact: (
                artifact.validation_metrics.superior_to_majority_baseline,
                artifact.validation_metrics.mean_net_return,
                -artifact.validation_metrics.log_loss,
            ),
        )
        result = ModelTrainingResult(
            training_run_id=run_id,
            input_hash=input_hash,
            artifacts=artifacts,
            validation_winner_model_id=winner.model_id,
        )
        for artifact in artifacts:
            self.store.upsert(MODEL_ARTIFACT_COLLECTION, artifact.model_id, artifact.model_dump(mode="json"))
            self._register_initial_status(artifact)
        self.store.upsert(MODEL_TRAINING_COLLECTION, run_id, result.model_dump(mode="json"))
        return result

    def _artifact(
        self,
        *,
        family: ModelFamily,
        schema: FeatureSchema,
        config: ModelTrainingConfig,
        train: tuple[MLSample, ...],
        validation: tuple[MLSample, ...],
        test: tuple[MLSample, ...],
        validation_probabilities: tuple[float, ...],
        test_probabilities: tuple[float, ...],
        baseline_target: MLTarget,
        means: tuple[float, ...],
        scales: tuple[float, ...],
        input_hash: str,
        coefficients: tuple[float, ...] = (),
        intercept: float | None = None,
        stumps: tuple[ExtraTreeStump, ...] = (),
    ) -> PrimaryModelArtifact:
        model_id = stable_key(
            self.version,
            family.value,
            input_hash,
            config.model_dump(mode="json"),
            coefficients,
            intercept,
            [stump.model_dump(mode="json") for stump in stumps],
        )
        all_samples = train + validation + test
        return PrimaryModelArtifact(
            model_id=model_id,
            model_version=f"{self.version}:{family.value.lower()}",
            family=family,
            created_at=test[-1].label_end_at,
            feature_schema=schema,
            training_config=config,
            supported_instruments=tuple(sorted({sample.instrument.upper() for sample in all_samples})),
            supported_timeframes=tuple(sorted({sample.timeframe for sample in all_samples}, key=lambda row: row.value)),
            supported_regimes=tuple(sorted({sample.regime for sample in all_samples}, key=lambda row: row.value)),
            train_start=train[0].observed_at,
            train_end=train[-1].observed_at,
            validation_start=validation[0].observed_at,
            validation_end=validation[-1].observed_at,
            test_start=test[0].observed_at,
            test_end=test[-1].observed_at,
            train_sample_count=len(train),
            validation_metrics=_evaluate(validation, validation_probabilities, baseline_target),
            test_metrics=_evaluate(test, test_probabilities, baseline_target),
            standardization_means=means,
            standardization_scales=scales,
            logistic_coefficients=coefficients,
            logistic_intercept=intercept,
            extra_tree_stumps=stumps,
        )

    def _register_initial_status(self, artifact: PrimaryModelArtifact) -> None:
        if any(row.get("model_id") == artifact.model_id for row in self.store.values(MODEL_STATUS_COLLECTION)):
            return
        record = ModelStatusRecord(
            status_record_id=stable_key(artifact.model_id, ModelGovernanceStatus.EXPERIMENTAL.value),
            model_id=artifact.model_id,
            status=ModelGovernanceStatus.EXPERIMENTAL,
            changed_at=artifact.created_at,
            approved_by="ATLAS_TRAINING_PIPELINE",
            reason="New artifacts enter EXPERIMENTAL research state only",
        )
        self.store.upsert(MODEL_STATUS_COLLECTION, record.status_record_id, record.model_dump(mode="json"))


class PrimaryModelRegistry:
    """Govern append-only model state and evidence-gated SHADOW qualification."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def artifact(self, model_id: str) -> PrimaryModelArtifact:
        row = next(
            (row for row in self.store.values(MODEL_ARTIFACT_COLLECTION) if row.get("model_id") == model_id),
            None,
        )
        if row is None:
            raise KeyError("primary model artifact does not exist")
        return PrimaryModelArtifact.model_validate(row)

    def current_status(self, model_id: str) -> ModelStatusRecord:
        rows = [
            ModelStatusRecord.model_validate(row)
            for row in self.store.values(MODEL_STATUS_COLLECTION)
            if row.get("model_id") == model_id
        ]
        if not rows:
            raise KeyError("primary model status does not exist")
        return max(rows, key=lambda row: (row.changed_at, _STATUS_ORDER[row.status], row.status_record_id))

    def promote_to_shadow(
        self,
        model_id: str,
        request: ShadowPromotionRequest,
        *,
        changed_at: datetime | None = None,
    ) -> ModelStatusRecord:
        self._validate_shadow_evidence(model_id, request.robustness_evidence_id)
        resolved_at = (changed_at or datetime.now(UTC)).astimezone(UTC)
        record_id = stable_key(model_id, ModelGovernanceStatus.SHADOW.value, resolved_at.isoformat(), request.approved_by)
        record = ModelStatusRecord(
            status_record_id=record_id,
            model_id=model_id,
            status=ModelGovernanceStatus.SHADOW,
            changed_at=resolved_at,
            approved_by=request.approved_by,
            reason=request.reason,
            robustness_evidence_id=request.robustness_evidence_id,
        )
        self.store.upsert(MODEL_STATUS_COLLECTION, record_id, record.model_dump(mode="json"))
        return record

    def qualify_for_shadow(
        self,
        model_id: str,
        robustness_evidence_id: str,
        *,
        policy_id: str = "ATLAS_V4_OBJECTIVE_SHADOW_POLICY",
        changed_at: datetime | None = None,
    ) -> ModelStatusRecord:
        """Apply the same hard SHADOW gates through an explicit automatic policy.

        This is research-model qualification only. It grants neither publication
        authority nor order-execution authority.
        """

        self._validate_shadow_evidence(model_id, robustness_evidence_id)
        resolved_at = (changed_at or datetime.now(UTC)).astimezone(UTC)
        reason = (
            "Objective SHADOW qualification passed: untouched test sample minimum, "
            "positive net OOS return superior to the majority baseline, and positive "
            "dependence-preserving robustness confidence interval"
        )
        record_id = stable_key(
            model_id,
            ModelGovernanceStatus.SHADOW.value,
            resolved_at.isoformat(),
            policy_id,
            robustness_evidence_id,
        )
        record = ModelStatusRecord(
            status_record_id=record_id,
            model_id=model_id,
            status=ModelGovernanceStatus.SHADOW,
            changed_at=resolved_at,
            approved_by=policy_id,
            reason=reason,
            robustness_evidence_id=robustness_evidence_id,
            automatic=True,
        )
        self.store.upsert(MODEL_STATUS_COLLECTION, record_id, record.model_dump(mode="json"))
        return record

    def _validate_shadow_evidence(self, model_id: str, robustness_evidence_id: str) -> None:
        artifact = self.artifact(model_id)
        current = self.current_status(model_id)
        if current.status is not ModelGovernanceStatus.EXPERIMENTAL:
            raise ValueError("only EXPERIMENTAL models can enter SHADOW in Phase 5")
        metrics = artifact.test_metrics
        if metrics.sample_count < 30:
            raise ValueError("SHADOW requires at least 30 untouched test observations")
        if not metrics.superior_to_majority_baseline or metrics.mean_net_return <= 0:
            raise ValueError("SHADOW requires superior positive net OOS evidence")
        robustness = self._robustness(robustness_evidence_id)
        if (
            robustness.status is not RobustnessStatus.AVAILABLE
            or robustness.mean_ci_low is None
            or robustness.mean_ci_low <= 0
        ):
            raise ValueError("SHADOW requires a positive dependence-preserving robustness interval")

    def _robustness(self, robustness_id: str) -> RobustnessResult:
        row = next(
            (
                row
                for row in self.store.values(ROBUSTNESS_COLLECTION)
                if row.get("robustness_run_id") == robustness_id
            ),
            None,
        )
        if row is None:
            raise ValueError("governed robustness evidence does not exist")
        return RobustnessResult.model_validate(row)


class PrimaryMLEngine:
    """Generate an uncalibrated candidate from a manually shadowed artifact."""

    def __init__(self, registry: PrimaryModelRegistry) -> None:
        self.registry = registry

    def generate_candidate(self, request: CandidateRequest) -> PrimaryCandidate:
        artifact = self.registry.artifact(request.model_id)
        status = self.registry.current_status(request.model_id)
        if status.status is not ModelGovernanceStatus.SHADOW:
            raise ValueError("only SHADOW models may emit Phase 5 research candidates")
        if request.instrument.upper() not in artifact.supported_instruments:
            raise ValueError("instrument is outside the model artifact scope")
        if request.timeframe not in artifact.supported_timeframes or request.regime not in artifact.supported_regimes:
            raise ValueError("timeframe or regime is outside the model artifact scope")
        if set(request.features) != set(artifact.feature_schema.names):
            raise ValueError("candidate feature schema does not match the model artifact")
        row = tuple(
            (request.features[name] - mean) / scale
            for name, mean, scale in zip(
                artifact.feature_schema.names,
                artifact.standardization_means,
                artifact.standardization_scales,
                strict=True,
            )
        )
        if any(not math.isfinite(value) for value in row):
            raise ValueError("candidate features must be finite")
        if artifact.family is ModelFamily.LOGISTIC_REGRESSION:
            assert artifact.logistic_intercept is not None
            probability_long = _logistic_probability(
                row,
                artifact.logistic_coefficients,
                artifact.logistic_intercept,
            )
        else:
            probability_long = _stump_probability(row, artifact.extra_tree_stumps)
        direction = MLTarget.LONG if probability_long >= 0.5 else MLTarget.SHORT
        direction_probability = probability_long if direction is MLTarget.LONG else 1 - probability_long
        candidate_id = stable_key(
            artifact.model_id,
            request.instrument.upper(),
            request.timeframe.value,
            request.regime.value,
            request.observed_at.isoformat(),
            request.features,
        )
        candidate = PrimaryCandidate(
            candidate_id=candidate_id,
            model_id=artifact.model_id,
            instrument=request.instrument.upper(),
            timeframe=request.timeframe,
            regime=request.regime,
            observed_at=request.observed_at,
            direction=direction,
            raw_probability_long=probability_long,
            raw_direction_probability=direction_probability,
        )
        self.registry.store.upsert(
            PRIMARY_CANDIDATE_COLLECTION,
            candidate.candidate_id,
            candidate.model_dump(mode="json"),
        )
        return candidate


def _validate_dataset(
    train: tuple[MLSample, ...],
    validation: tuple[MLSample, ...],
    test: tuple[MLSample, ...],
) -> FeatureSchema:
    for name, rows in (("train", train), ("validation", validation), ("test", test)):
        if not rows:
            raise ValueError(f"{name} dataset cannot be empty")
        if any(current.observed_at <= previous.observed_at for previous, current in pairwise(rows)):
            raise ValueError(f"{name} dataset must be strictly chronological")
    all_rows = train + validation + test
    if len({row.sample_id for row in all_rows}) != len(all_rows):
        raise ValueError("ML sample IDs must be unique across all folds")
    schema_names = tuple(sorted(train[0].features))
    if any(tuple(sorted(row.features)) != schema_names for row in all_rows):
        raise ValueError("every ML sample must use exactly one feature schema")
    if {row.target for row in train} != {MLTarget.LONG, MLTarget.SHORT}:
        raise ValueError("training data must contain LONG and SHORT targets")
    if max(row.label_end_at for row in train) >= validation[0].observed_at:
        raise ValueError("training labels overlap the validation window")
    if max(row.label_end_at for row in validation) >= test[0].observed_at:
        raise ValueError("validation labels overlap the test window")
    schema_hash = stable_key(schema_names)
    return FeatureSchema(names=schema_names, schema_hash=schema_hash)


def _standardization(
    samples: tuple[MLSample, ...],
    names: tuple[str, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    columns = [[sample.features[name] for sample in samples] for name in names]
    means = tuple(fmean(column) for column in columns)
    scales = tuple(pstdev(column) or 1.0 for column in columns)
    return means, scales


def _matrix(
    samples: tuple[MLSample, ...],
    names: tuple[str, ...],
    means: tuple[float, ...],
    scales: tuple[float, ...],
) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(
            (sample.features[name] - mean) / scale
            for name, mean, scale in zip(names, means, scales, strict=True)
        )
        for sample in samples
    )


def _targets(samples: tuple[MLSample, ...]) -> tuple[int, ...]:
    return tuple(1 if sample.target is MLTarget.LONG else 0 for sample in samples)


def _fit_logistic(
    matrix: tuple[tuple[float, ...], ...],
    targets: tuple[int, ...],
    config: ModelTrainingConfig,
) -> tuple[tuple[float, ...], float]:
    width = len(matrix[0])
    coefficients = [0.0] * width
    prior = min(1 - 1e-6, max(1e-6, sum(targets) / len(targets)))
    intercept = math.log(prior / (1 - prior))
    for _ in range(config.epochs):
        coefficient_gradient = [0.0] * width
        intercept_gradient = 0.0
        for row, target in zip(matrix, targets, strict=True):
            error = _sigmoid(intercept + sum(weight * value for weight, value in zip(coefficients, row))) - target
            intercept_gradient += error
            for index, value in enumerate(row):
                coefficient_gradient[index] += error * value
        scale = 1 / len(matrix)
        intercept -= config.learning_rate * intercept_gradient * scale
        for index in range(width):
            gradient = coefficient_gradient[index] * scale + config.l2_penalty * coefficients[index]
            coefficients[index] -= config.learning_rate * gradient
    return tuple(coefficients), intercept


def _fit_extra_tree_stumps(
    matrix: tuple[tuple[float, ...], ...],
    targets: tuple[int, ...],
    config: ModelTrainingConfig,
) -> tuple[ExtraTreeStump, ...]:
    rng = random.Random(config.random_seed)
    width = len(matrix[0])
    stumps: list[ExtraTreeStump] = []
    for _ in range(config.extra_trees_estimators):
        feature_index = rng.randrange(width)
        values = [row[feature_index] for row in matrix]
        minimum = min(values)
        maximum = max(values)
        threshold = minimum if minimum == maximum else rng.uniform(minimum, maximum)
        left = [target for row, target in zip(matrix, targets, strict=True) if row[feature_index] <= threshold]
        right = [target for row, target in zip(matrix, targets, strict=True) if row[feature_index] > threshold]
        stumps.append(
            ExtraTreeStump(
                feature_index=feature_index,
                threshold=threshold,
                left_probability_long=(sum(left) + 1) / (len(left) + 2),
                right_probability_long=(sum(right) + 1) / (len(right) + 2),
            )
        )
    return tuple(stumps)


def _logistic_probability(row: tuple[float, ...], coefficients: tuple[float, ...], intercept: float) -> float:
    return _sigmoid(intercept + sum(weight * value for weight, value in zip(coefficients, row, strict=True)))


def _stump_probability(row: tuple[float, ...], stumps: tuple[ExtraTreeStump, ...]) -> float:
    probabilities = [
        stump.left_probability_long if row[stump.feature_index] <= stump.threshold else stump.right_probability_long
        for stump in stumps
    ]
    return sum(probabilities) / len(probabilities)


def _evaluate(
    samples: tuple[MLSample, ...],
    probabilities: tuple[float, ...],
    baseline_target: MLTarget,
) -> ModelEvaluationMetrics:
    targets = _targets(samples)
    predictions = tuple(1 if probability >= 0.5 else 0 for probability in probabilities)
    accuracy = sum(prediction == target for prediction, target in zip(predictions, targets, strict=True)) / len(samples)
    long_indices = [index for index, target in enumerate(targets) if target == 1]
    short_indices = [index for index, target in enumerate(targets) if target == 0]
    recall_long = (
        sum(predictions[index] == 1 for index in long_indices) / len(long_indices) if long_indices else None
    )
    recall_short = (
        sum(predictions[index] == 0 for index in short_indices) / len(short_indices) if short_indices else None
    )
    balanced = (recall_long + recall_short) / 2 if recall_long is not None and recall_short is not None else None
    clipped = tuple(min(1 - 1e-12, max(1e-12, probability)) for probability in probabilities)
    log_loss = -sum(
        target * math.log(probability) + (1 - target) * math.log(1 - probability)
        for target, probability in zip(targets, clipped, strict=True)
    ) / len(samples)
    brier = sum((probability - target) ** 2 for probability, target in zip(probabilities, targets, strict=True)) / len(samples)
    model_returns = [
        (1 if prediction == 1 else -1) * sample.forward_return_net
        for prediction, sample in zip(predictions, samples, strict=True)
    ]
    baseline_sign = 1 if baseline_target is MLTarget.LONG else -1
    baseline_returns = [baseline_sign * sample.forward_return_net for sample in samples]
    mean_net = fmean(model_returns)
    baseline_mean = fmean(baseline_returns)
    superior = mean_net > baseline_mean and balanced is not None and balanced > 0.5
    return ModelEvaluationMetrics(
        sample_count=len(samples),
        long_count=len(long_indices),
        short_count=len(short_indices),
        accuracy=accuracy,
        balanced_accuracy=balanced,
        log_loss=log_loss,
        brier_score=brier,
        mean_net_return=mean_net,
        majority_baseline_mean_net_return=baseline_mean,
        superior_to_majority_baseline=superior,
    )


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1 / (1 + exponential)
    exponential = math.exp(value)
    return exponential / (1 + exponential)


__all__ = [
    "MODEL_ARTIFACT_COLLECTION",
    "MODEL_STATUS_COLLECTION",
    "MODEL_TRAINING_COLLECTION",
    "PRIMARY_CANDIDATE_COLLECTION",
    "PrimaryMLEngine",
    "PrimaryMLTrainer",
    "PrimaryModelRegistry",
]
