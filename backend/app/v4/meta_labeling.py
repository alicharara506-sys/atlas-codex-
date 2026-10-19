"""Leakage-resistant meta-label research and evidence dependency controls."""

from __future__ import annotations

import math
from collections import OrderedDict
from itertools import pairwise
from statistics import fmean, pstdev

from app.core.store import RecordStore, stable_key
from app.v4.ai_review import AI_REVIEW_COLLECTION
from app.v4.meta_contracts import (
    EvidenceDependencyReport,
    EvidenceFamily,
    EvidenceFamilyCount,
    EvidenceObservation,
    MetaEvaluationMetrics,
    MetaFeatureVector,
    MetaFoldEvaluation,
    MetaLabelSample,
    MetaModelArtifact,
    MetaOOSPrediction,
    MetaPrediction,
    MetaPredictionRequest,
    MetaTarget,
    MetaTrainingConfig,
    MetaTrainingResult,
)
from app.v4.ml_contracts import PrimaryCandidate
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_contracts import ValidationSample
from app.v4.validation import WalkForwardValidator

EVIDENCE_DEPENDENCY_COLLECTION = "v4_evidence_dependency_reports"
META_MODEL_COLLECTION = "v4_meta_model_artifacts"
META_TRAINING_COLLECTION = "v4_meta_training_runs"
META_PREDICTION_COLLECTION = "v4_meta_predictions"


class EvidenceDependencyEngine:
    """Identify shared upstream evidence without inventing a confirmation score."""

    version = "evidence-dependency-v1"

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def analyze(self, observations: tuple[EvidenceObservation, ...]) -> EvidenceDependencyReport:
        if not observations:
            raise ValueError("evidence dependency analysis requires observations")
        if len({row.evidence_id for row in observations}) != len(observations):
            raise ValueError("evidence observation IDs must be unique")
        parents = list(range(len(observations)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        for left_index, left in enumerate(observations):
            for right_index in range(left_index + 1, len(observations)):
                right = observations[right_index]
                shared_upstream = bool(set(left.upstream_ids).intersection(right.upstream_ids))
                same_measure = left.name.strip().casefold() == right.name.strip().casefold()
                if shared_upstream or same_measure:
                    union(left_index, right_index)

        components: dict[int, list[str]] = {}
        for index, observation in enumerate(observations):
            components.setdefault(find(index), []).append(observation.evidence_id)
        groups = tuple(
            sorted(
                (tuple(sorted(values)) for values in components.values() if len(values) > 1),
                key=lambda row: row[0],
            )
        )
        family_counts = tuple(
            EvidenceFamilyCount(
                family=family,
                observation_count=sum(row.family is family for row in observations),
            )
            for family in EvidenceFamily
            if any(row.family is family for row in observations)
        )
        independent = len(components)
        report_id = stable_key(
            self.version,
            [row.model_dump(mode="json") for row in observations],
        )
        report = EvidenceDependencyReport(
            report_id=report_id,
            observations=observations,
            family_counts=family_counts,
            redundant_groups=groups,
            raw_observation_count=len(observations),
            independent_component_count=independent,
            effective_family_count=len(family_counts),
            redundancy_ratio=1 - independent / len(observations),
        )
        self.store.upsert(
            EVIDENCE_DEPENDENCY_COLLECTION,
            report.report_id,
            report.model_dump(mode="json"),
        )
        return report


class MetaLabelTrainer:
    """Train a logistic accept/reject model across purged walk-forward folds."""

    version = "meta-label-logistic-v2"

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        self.validator = WalkForwardValidator()

    def train(
        self,
        samples: tuple[MetaLabelSample, ...],
        config: MetaTrainingConfig,
    ) -> MetaTrainingResult:
        self._validate_samples(samples)
        input_hash = stable_key(
            [sample.model_dump(mode="json") for sample in samples],
            config.model_dump(mode="json"),
        )
        run_id = stable_key(self.version, input_hash)
        existing = next(
            (row for row in self.store.values(META_TRAINING_COLLECTION) if row.get("training_run_id") == run_id),
            None,
        )
        if existing is not None:
            return MetaTrainingResult.model_validate(existing)
        validation_samples = tuple(
            ValidationSample(
                sample_id=sample.sample_id,
                observed_at=sample.observed_at,
                label_end_at=sample.label_end_at,
                feature_hash=stable_key(sample.features.model_dump(mode="json")),
            )
            for sample in samples
        )
        walk_forward = self.validator.split(validation_samples, config.walk_forward)
        sample_by_id = {sample.sample_id: sample for sample in samples}
        fold_evaluations: list[MetaFoldEvaluation] = []
        oos_by_id: dict[str, tuple[MetaLabelSample, float, int]] = {}
        for fold in walk_forward.folds:
            train = tuple(sample_by_id[sample_id] for sample_id in fold.train_ids)
            validation = tuple(sample_by_id[sample_id] for sample_id in fold.validation_ids)
            test = tuple(sample_by_id[sample_id] for sample_id in fold.test_ids)
            names, means, scales, coefficients, intercept = _fit(train, config)
            validation_probabilities = tuple(
                _probability(_encode(sample.features, names, means, scales), coefficients, intercept)
                for sample in validation
            )
            test_probabilities = tuple(
                _probability(_encode(sample.features, names, means, scales), coefficients, intercept) for sample in test
            )
            fold_evaluations.append(
                MetaFoldEvaluation(
                    fold_index=fold.fold_index,
                    train_sample_count=len(train),
                    validation_metrics=_metrics(validation, validation_probabilities),
                    test_metrics=_metrics(test, test_probabilities),
                )
            )
            for sample, probability in zip(test, test_probabilities, strict=True):
                oos_by_id.setdefault(sample.sample_id, (sample, probability, fold.fold_index))
        if not oos_by_id:
            raise ValueError("meta-label validation produced no OOS predictions")
        ordered_oos = tuple(sorted(oos_by_id.values(), key=lambda row: (row[0].observed_at, row[0].sample_id)))
        aggregate = _metrics(
            tuple(row[0] for row in ordered_oos),
            tuple(row[1] for row in ordered_oos),
        )
        oos_predictions = tuple(
            MetaOOSPrediction(
                sample_id=sample.sample_id,
                candidate_id=sample.candidate_id,
                fold_index=fold_index,
                source_validation_run_id=walk_forward.validation_run_id,
                observed_at=sample.observed_at,
                label_end_at=sample.label_end_at,
                raw_accept_probability=probability,
                tp_before_sl=sample.tp_before_sl,
                regime=sample.features.regime,
                forward_return_net=sample.forward_return_net,
            )
            for sample, probability, fold_index in ordered_oos
        )
        names, means, scales, coefficients, intercept = _fit(samples, config)
        model_id = stable_key(
            self.version,
            input_hash,
            names,
            coefficients,
            intercept,
        )
        artifact = MetaModelArtifact(
            meta_model_id=model_id,
            version=self.version,
            created_at=max(sample.label_end_at for sample in samples),
            feature_names=names,
            feature_schema_hash=stable_key(names),
            config=config,
            walk_forward=walk_forward,
            folds=tuple(fold_evaluations),
            oos_predictions=oos_predictions,
            aggregate_oos_metrics=aggregate,
            coefficients=coefficients,
            intercept=intercept,
            standardization_means=means,
            standardization_scales=scales,
            material_oos_net_improvement=(
                aggregate.net_improvement > 0
                and aggregate.balanced_accuracy is not None
                and aggregate.balanced_accuracy > 0.5
            ),
        )
        result = MetaTrainingResult(training_run_id=run_id, artifact=artifact)
        self.store.upsert(META_MODEL_COLLECTION, model_id, artifact.model_dump(mode="json"))
        self.store.upsert(META_TRAINING_COLLECTION, run_id, result.model_dump(mode="json"))
        return result

    def _validate_samples(self, samples: tuple[MetaLabelSample, ...]) -> None:
        if len({sample.sample_id for sample in samples}) != len(samples):
            raise ValueError("meta-label sample IDs must be unique")
        if any(current.observed_at < previous.observed_at for previous, current in pairwise(samples)):
            raise ValueError("meta-label samples must be chronologically ordered")
        if {sample.target for sample in samples} != set(MetaTarget):
            raise ValueError("meta-label training requires ACCEPT and REJECT examples")
        dependency_ids = {str(row.get("report_id")) for row in self.store.values(EVIDENCE_DEPENDENCY_COLLECTION)}
        review_ids = {str(row.get("bundle_id")) for row in self.store.values(AI_REVIEW_COLLECTION)}
        candidates = {
            str(row.get("candidate_id")): PrimaryCandidate.model_validate(row)
            for row in self.store.values(PRIMARY_CANDIDATE_COLLECTION)
        }
        for sample in samples:
            if sample.features.evidence_dependency_report_id not in dependency_ids:
                raise ValueError("meta-label samples require persisted evidence-dependency lineage")
            review_id = sample.features.ai_review_bundle_id
            if review_id is not None and review_id not in review_ids:
                raise ValueError("meta-label AI features require persisted review lineage")
            candidate = candidates.get(sample.candidate_id)
            if candidate is None:
                raise ValueError("meta-label samples require persisted primary candidates")
            if candidate.direction is not sample.primary_direction:
                raise ValueError("meta-label direction must match the primary candidate")
            if candidate.observed_at != sample.observed_at:
                raise ValueError("meta-label observation must match the primary candidate timestamp")
            if not math.isclose(
                candidate.raw_direction_probability,
                sample.features.raw_primary_probability,
                rel_tol=0,
                abs_tol=1e-12,
            ):
                raise ValueError("meta-label raw probability must match the primary candidate")


class MetaLabelEngine:
    """Emit raw, uncalibrated research classifications from a meta artifact."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        self._artifact_cache: OrderedDict[str, MetaModelArtifact] = OrderedDict()

    def artifact(self, meta_model_id: str) -> MetaModelArtifact:
        """Load one immutable content-addressed artifact per process.

        Drift diagnostics score many observations with the same fixed model.
        Re-querying and validating the complete artifact collection for every
        observation makes that bounded calculation grow with both the number
        of observations and the number of historical models. Model IDs are
        content-addressed and artifacts are immutable, so caching by ID keeps
        the statistical result identical while removing repeated database IO.
        """

        cached = self._artifact_cache.get(meta_model_id)
        if cached is not None:
            self._artifact_cache.move_to_end(meta_model_id)
            return cached

        row = next(
            (row for row in self.store.values(META_MODEL_COLLECTION) if row.get("meta_model_id") == meta_model_id),
            None,
        )
        if row is None:
            raise KeyError("meta-model artifact does not exist")
        artifact = MetaModelArtifact.model_validate(row)
        self._artifact_cache[meta_model_id] = artifact
        if len(self._artifact_cache) > 256:
            self._artifact_cache.popitem(last=False)
        return artifact

    def score_features(
        self,
        meta_model_id: str,
        features: MetaFeatureVector,
    ) -> float:
        """Score features with one fixed persisted model without creating a prediction."""

        artifact = self.artifact(meta_model_id)
        encoded = _encode(
            features,
            artifact.feature_names,
            artifact.standardization_means,
            artifact.standardization_scales,
        )
        return _probability(encoded, artifact.coefficients, artifact.intercept)

    def predict(self, request: MetaPredictionRequest) -> MetaPrediction:
        artifact = self.artifact(request.meta_model_id)
        candidate_row = next(
            (
                row
                for row in self.store.values(PRIMARY_CANDIDATE_COLLECTION)
                if row.get("candidate_id") == request.candidate_id
            ),
            None,
        )
        if candidate_row is None:
            raise KeyError("persisted primary candidate does not exist")
        candidate = PrimaryCandidate.model_validate(candidate_row)
        if request.observed_at < candidate.observed_at or request.observed_at <= artifact.created_at:
            raise ValueError("meta prediction must occur after candidate creation and model training")
        if not math.isclose(
            candidate.raw_direction_probability,
            request.features.raw_primary_probability,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ValueError("meta prediction probability must match the primary candidate")
        dependency_exists = any(
            row.get("report_id") == request.features.evidence_dependency_report_id
            for row in self.store.values(EVIDENCE_DEPENDENCY_COLLECTION)
        )
        if not dependency_exists:
            raise ValueError("meta prediction requires persisted dependency evidence")
        review_id = request.features.ai_review_bundle_id
        if review_id is not None and not any(
            row.get("bundle_id") == review_id for row in self.store.values(AI_REVIEW_COLLECTION)
        ):
            raise ValueError("meta prediction requires persisted AI review evidence")
        probability = self.score_features(artifact.meta_model_id, request.features)
        classification = MetaTarget.ACCEPT if probability >= artifact.config.decision_threshold else MetaTarget.REJECT
        prediction_id = stable_key(
            artifact.meta_model_id,
            request.candidate_id,
            request.observed_at.isoformat(),
            request.features.model_dump(mode="json"),
        )
        prediction = MetaPrediction(
            meta_prediction_id=prediction_id,
            meta_model_id=artifact.meta_model_id,
            candidate_id=request.candidate_id,
            observed_at=request.observed_at,
            raw_accept_probability=probability,
            research_classification=classification,
        )
        self.store.upsert(
            META_PREDICTION_COLLECTION,
            prediction.meta_prediction_id,
            prediction.model_dump(mode="json"),
        )
        return prediction


def _raw_features(features: MetaFeatureVector) -> dict[str, float]:
    values = {
        "raw_primary_probability": features.raw_primary_probability,
        "model_disagreement": features.model_disagreement,
        "volatility_percentile": features.volatility_percentile,
        "spread_log1p": math.log1p(features.spread_bps),
        "event_risk": features.event_risk,
        "macro_state": features.macro_state,
        "mtf_conflict": features.mtf_conflict,
        "data_quality": features.data_quality_score / 100,
        "ai_contradiction_ratio": features.ai_contradiction_ratio,
        "positioning_zscore": features.positioning_zscore or 0,
        "positioning_available": float(features.positioning_zscore is not None),
        "liquidity_score": features.liquidity_score or 0,
        "liquidity_available": float(features.liquidity_score is not None),
        "timeframe_code": features.timeframe_code,
    }
    for regime in type(features.regime):
        values[f"regime__{regime.value}"] = float(features.regime is regime)
    for session in type(features.session):
        values[f"session__{session.value}"] = float(features.session is session)
    return values


def _fit(
    samples: tuple[MetaLabelSample, ...],
    config: MetaTrainingConfig,
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...], float]:
    if {sample.target for sample in samples} != set(MetaTarget):
        raise ValueError("each meta-model training fold requires ACCEPT and REJECT labels")
    names = tuple(sorted(_raw_features(samples[0].features)))
    raw_rows = [tuple(_raw_features(sample.features)[name] for name in names) for sample in samples]
    columns = tuple(tuple(row[index] for row in raw_rows) for index in range(len(names)))
    means = tuple(fmean(column) for column in columns)
    scales = tuple(pstdev(column) or 1.0 for column in columns)
    matrix = tuple(
        tuple((value - mean) / scale for value, mean, scale in zip(row, means, scales, strict=True)) for row in raw_rows
    )
    targets = tuple(1 if sample.target is MetaTarget.ACCEPT else 0 for sample in samples)
    coefficients = [0.0] * len(names)
    positive_rate = min(1 - 1e-6, max(1e-6, fmean(targets)))
    intercept = math.log(positive_rate / (1 - positive_rate))
    for _ in range(config.epochs):
        probabilities = tuple(_probability(row, tuple(coefficients), intercept) for row in matrix)
        errors = tuple(probability - target for probability, target in zip(probabilities, targets, strict=True))
        for index in range(len(coefficients)):
            gradient = fmean(error * row[index] for error, row in zip(errors, matrix, strict=True))
            gradient += config.l2_penalty * coefficients[index]
            coefficients[index] -= config.learning_rate * gradient
        intercept -= config.learning_rate * fmean(errors)
    return names, means, scales, tuple(coefficients), intercept


def _encode(
    features: MetaFeatureVector,
    names: tuple[str, ...],
    means: tuple[float, ...],
    scales: tuple[float, ...],
) -> tuple[float, ...]:
    raw = _raw_features(features)
    if set(raw) != set(names):
        raise ValueError("meta feature schema does not match the artifact")
    return tuple((raw[name] - mean) / scale for name, mean, scale in zip(names, means, scales, strict=True))


def _probability(row: tuple[float, ...], coefficients: tuple[float, ...], intercept: float) -> float:
    value = intercept + sum(weight * feature for weight, feature in zip(coefficients, row, strict=True))
    if value >= 0:
        exponential = math.exp(-value)
        return 1 / (1 + exponential)
    exponential = math.exp(value)
    return exponential / (1 + exponential)


def _metrics(
    samples: tuple[MetaLabelSample, ...],
    probabilities: tuple[float, ...],
) -> MetaEvaluationMetrics:
    if len(samples) != len(probabilities) or not samples:
        raise ValueError("meta metrics require aligned non-empty samples and probabilities")
    targets = tuple(1 if sample.target is MetaTarget.ACCEPT else 0 for sample in samples)
    decisions = tuple(probability >= 0.5 for probability in probabilities)
    accuracy = sum(decision == bool(target) for decision, target in zip(decisions, targets, strict=True)) / len(samples)
    positive = tuple(index for index, target in enumerate(targets) if target == 1)
    negative = tuple(index for index, target in enumerate(targets) if target == 0)
    balanced = None
    if positive and negative:
        true_positive = sum(decisions[index] for index in positive) / len(positive)
        true_negative = sum(not decisions[index] for index in negative) / len(negative)
        balanced = (true_positive + true_negative) / 2
    clipped = tuple(min(1 - 1e-12, max(1e-12, value)) for value in probabilities)
    log_loss = -fmean(
        target * math.log(probability) + (1 - target) * math.log(1 - probability)
        for target, probability in zip(targets, clipped, strict=True)
    )
    brier = fmean((probability - target) ** 2 for probability, target in zip(probabilities, targets, strict=True))
    accept_all = fmean(sample.forward_return_net for sample in samples)
    accepted_returns = tuple(
        sample.forward_return_net for sample, decision in zip(samples, decisions, strict=True) if decision
    )
    # This metric is trade expectancy after the meta filter, so rejected
    # opportunities must be excluded from the denominator. Treating them as
    # zero-return trades measured opportunity throughput instead and made a
    # selective, profitable filter look worse merely because it traded less.
    filtered = fmean(accepted_returns) if accepted_returns else 0.0
    return MetaEvaluationMetrics(
        sample_count=len(samples),
        accept_labels=sum(targets),
        reject_labels=len(samples) - sum(targets),
        predicted_accept_count=sum(decisions),
        accuracy=accuracy,
        balanced_accuracy=balanced,
        log_loss=log_loss,
        brier_score=brier,
        accept_all_mean_net_return=accept_all,
        filtered_mean_net_return=filtered,
        net_improvement=filtered - accept_all,
    )


__all__ = [
    "EVIDENCE_DEPENDENCY_COLLECTION",
    "META_MODEL_COLLECTION",
    "META_PREDICTION_COLLECTION",
    "META_TRAINING_COLLECTION",
    "EvidenceDependencyEngine",
    "MetaLabelEngine",
    "MetaLabelTrainer",
]
