"""Chronological OOS probability calibration for ATLAS V4 Phase 8."""

from __future__ import annotations

import math
from bisect import bisect_left
from statistics import fmean

from app.core.store import RecordStore, stable_key
from app.v4.calibration_contracts import (
    CalibratedProbability,
    CalibratedProbabilityRequest,
    CalibrationArtifact,
    CalibrationMethod,
    CalibrationMetrics,
    CalibrationStatus,
    CalibrationUncertainty,
    ReliabilityBin,
)
from app.v4.signal_contracts import PUBLICATION_PROBABILITY_THRESHOLD
from app.v4.meta_contracts import MetaModelArtifact, MetaPrediction
from app.v4.meta_labeling import META_MODEL_COLLECTION, META_PREDICTION_COLLECTION

CALIBRATION_ARTIFACT_COLLECTION = "v4_calibration_artifacts"
CALIBRATED_PROBABILITY_COLLECTION = "v4_calibrated_probabilities"


class ProbabilityCalibrationEngine:
    """Fit Platt/isotonic maps using only chronological meta-model OOS predictions."""

    version = "probability-calibration-v2"
    minimum_samples = 200
    minimum_class_count = 40
    isotonic_minimum_samples = 500
    evaluation_fraction = 0.30

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    @classmethod
    def select_method(cls, sample_count: int) -> CalibrationMethod:
        if sample_count < cls.minimum_samples:
            return CalibrationMethod.NONE
        if sample_count >= cls.isotonic_minimum_samples:
            return CalibrationMethod.ISOTONIC
        return CalibrationMethod.PLATT

    def train(self, meta_model_id: str) -> CalibrationArtifact:
        meta_artifact = self._meta_artifact(meta_model_id)
        rows = tuple(
            sorted(meta_artifact.oos_predictions, key=lambda row: (row.observed_at, row.sample_id))
        )
        calibration_id = stable_key(
            self.version,
            meta_artifact.meta_model_id,
            meta_artifact.walk_forward.validation_run_id,
            [row.model_dump(mode="json") for row in rows],
        )
        existing = self._find_artifact(calibration_id)
        if existing is not None:
            return existing
        positive_count = sum(row.tp_before_sl for row in rows)
        negative_count = len(rows) - positive_count
        method = self.select_method(len(rows))
        if (
            method is CalibrationMethod.NONE
            or positive_count < self.minimum_class_count
            or negative_count < self.minimum_class_count
        ):
            artifact = CalibrationArtifact(
                calibration_id=calibration_id,
                version=self.version,
                meta_model_id=meta_artifact.meta_model_id,
                source_validation_run_id=meta_artifact.walk_forward.validation_run_id,
                created_at=meta_artifact.created_at,
                status=CalibrationStatus.INSUFFICIENT_EVIDENCE,
                method=CalibrationMethod.NONE,
                reason=(
                    f"Calibration requires at least {self.minimum_samples} chronological OOS samples "
                    f"with {self.minimum_class_count} TP-before-SL and {self.minimum_class_count} "
                    "SL-before-TP outcomes"
                ),
                sample_count=len(rows),
                tp_before_sl_count=positive_count,
                sl_before_tp_count=negative_count,
                fit_sample_count=0,
                evaluation_sample_count=0,
                uncertainty=CalibrationUncertainty.UNAVAILABLE,
                calibration_audit_required=False,
                atlas_probability_available=False,
            )
            self._persist(artifact)
            return artifact

        split = max(1, int(len(rows) * (1 - self.evaluation_fraction)))
        while (
            split < len(rows)
            and rows[split].observed_at == rows[split - 1].observed_at
        ):
            split += 1
        raw_fit_rows = rows[:split]
        evaluation_rows = rows[split:]
        evaluation_start = evaluation_rows[0].observed_at if evaluation_rows else None
        fit_rows = tuple(
            row
            for row in raw_fit_rows
            if evaluation_start is not None and row.label_end_at < evaluation_start
        )
        if (
            len(evaluation_rows) < 40
            or {row.tp_before_sl for row in fit_rows} != {False, True}
            or {row.tp_before_sl for row in evaluation_rows} != {False, True}
        ):
            artifact = CalibrationArtifact(
                calibration_id=calibration_id,
                version=self.version,
                meta_model_id=meta_artifact.meta_model_id,
                source_validation_run_id=meta_artifact.walk_forward.validation_run_id,
                created_at=meta_artifact.created_at,
                status=CalibrationStatus.INSUFFICIENT_EVIDENCE,
                method=CalibrationMethod.NONE,
                reason=(
                    "Chronological calibration split and label-overlap purge lack adequate "
                    "fit/evaluation class coverage"
                ),
                sample_count=len(rows),
                tp_before_sl_count=positive_count,
                sl_before_tp_count=negative_count,
                fit_sample_count=len(fit_rows),
                evaluation_sample_count=len(evaluation_rows),
                uncertainty=CalibrationUncertainty.UNAVAILABLE,
                calibration_audit_required=False,
                atlas_probability_available=False,
            )
            self._persist(artifact)
            return artifact

        fit_probabilities = tuple(row.raw_accept_probability for row in fit_rows)
        fit_targets = tuple(row.tp_before_sl for row in fit_rows)
        evaluation_raw = tuple(row.raw_accept_probability for row in evaluation_rows)
        evaluation_targets = tuple(row.tp_before_sl for row in evaluation_rows)
        slope: float | None = None
        intercept: float | None = None
        thresholds: tuple[float, ...] = ()
        values: tuple[float, ...] = ()
        if method is CalibrationMethod.PLATT:
            slope, intercept = _fit_platt(fit_probabilities, fit_targets)
            evaluation_calibrated = tuple(
                _apply_platt(probability, slope, intercept) for probability in evaluation_raw
            )
        else:
            thresholds, values = _fit_isotonic(fit_probabilities, fit_targets)
            evaluation_calibrated = tuple(
                _apply_isotonic(probability, thresholds, values)
                for probability in evaluation_raw
            )
        pre_metrics = _metrics(evaluation_raw, evaluation_targets)
        post_metrics = _metrics(evaluation_calibrated, evaluation_targets)
        # Calibration is allowed to be the identity map when the fitted map is
        # strictly dominated on the untouched chronological audit slice. This
        # preserves an already calibrated meta probability instead of making it
        # worse merely because the sample-count rule selected Platt/isotonic.
        if (
            post_metrics.expected_calibration_error >= pre_metrics.expected_calibration_error
            and post_metrics.brier_score >= pre_metrics.brier_score
        ):
            method = CalibrationMethod.IDENTITY
            slope = None
            intercept = None
            thresholds = ()
            values = ()
            evaluation_calibrated = evaluation_raw
            post_metrics = pre_metrics
        high_probability_rate = sum(value >= 0.95 for value in evaluation_calibrated) / len(
            evaluation_calibrated
        )
        audit_required = (
            high_probability_rate > 0.05
            or post_metrics.expected_calibration_error > 0.10
            or post_metrics.brier_score > pre_metrics.brier_score * 1.05
        )
        if len(rows) >= self.isotonic_minimum_samples and post_metrics.expected_calibration_error <= 0.05:
            uncertainty = CalibrationUncertainty.LOW
        elif post_metrics.expected_calibration_error <= 0.10:
            uncertainty = CalibrationUncertainty.MODERATE
        else:
            uncertainty = CalibrationUncertainty.HIGH
        artifact = CalibrationArtifact(
            calibration_id=calibration_id,
            version=self.version,
            meta_model_id=meta_artifact.meta_model_id,
            source_validation_run_id=meta_artifact.walk_forward.validation_run_id,
            created_at=evaluation_rows[-1].label_end_at,
            status=CalibrationStatus.AVAILABLE,
            method=method,
            reason=(
                "Chronological OOS audit retained the already calibrated meta probability"
                if method is CalibrationMethod.IDENTITY
                else "Chronological OOS calibration fit and evaluation completed"
            ),
            sample_count=len(rows),
            tp_before_sl_count=positive_count,
            sl_before_tp_count=negative_count,
            fit_sample_count=len(fit_rows),
            evaluation_sample_count=len(evaluation_rows),
            fit_start=fit_rows[0].observed_at,
            fit_end=fit_rows[-1].label_end_at,
            evaluation_start=evaluation_rows[0].observed_at,
            evaluation_end=evaluation_rows[-1].label_end_at,
            pre_calibration_metrics=pre_metrics,
            post_calibration_metrics=post_metrics,
            platt_slope=slope,
            platt_intercept=intercept,
            isotonic_thresholds=thresholds,
            isotonic_values=values,
            uncertainty=uncertainty,
            high_probability_rate=high_probability_rate,
            calibration_audit_required=audit_required,
            atlas_probability_available=True,
        )
        self._persist(artifact)
        return artifact

    def artifact(self, calibration_id: str) -> CalibrationArtifact:
        artifact = self._find_artifact(calibration_id)
        if artifact is None:
            raise KeyError("calibration artifact does not exist")
        return artifact

    def apply(self, request: CalibratedProbabilityRequest) -> CalibratedProbability:
        artifact = self.artifact(request.calibration_id)
        prediction_row = next(
            (
                row
                for row in self.store.values(META_PREDICTION_COLLECTION)
                if row.get("meta_prediction_id") == request.meta_prediction_id
            ),
            None,
        )
        if prediction_row is None:
            raise KeyError("persisted meta prediction does not exist")
        prediction = MetaPrediction.model_validate(prediction_row)
        if prediction.meta_model_id != artifact.meta_model_id:
            raise ValueError("meta prediction and calibration artifact do not share a model")
        probability: float | None = None
        if artifact.status is CalibrationStatus.AVAILABLE:
            if artifact.method is CalibrationMethod.IDENTITY:
                probability = prediction.raw_accept_probability
            elif artifact.method is CalibrationMethod.PLATT:
                assert artifact.platt_slope is not None
                assert artifact.platt_intercept is not None
                probability = _apply_platt(
                    prediction.raw_accept_probability,
                    artifact.platt_slope,
                    artifact.platt_intercept,
                )
            else:
                probability = _apply_isotonic(
                    prediction.raw_accept_probability,
                    artifact.isotonic_thresholds,
                    artifact.isotonic_values,
                )
        output_id = stable_key(
            artifact.calibration_id,
            prediction.meta_prediction_id,
            prediction.raw_accept_probability,
        )
        output = CalibratedProbability(
            calibrated_probability_id=output_id,
            calibration_id=artifact.calibration_id,
            meta_prediction_id=prediction.meta_prediction_id,
            atlas_probability=probability,
            uncertainty=artifact.uncertainty,
            evidence_status=artifact.status,
            meets_80_percent_research_threshold=(
                probability is not None and probability >= PUBLICATION_PROBABILITY_THRESHOLD
            ),
        )
        self.store.upsert(
            CALIBRATED_PROBABILITY_COLLECTION,
            output.calibrated_probability_id,
            output.model_dump(mode="json"),
        )
        return output

    def _meta_artifact(self, meta_model_id: str) -> MetaModelArtifact:
        row = next(
            (
                row
                for row in self.store.values(META_MODEL_COLLECTION)
                if row.get("meta_model_id") == meta_model_id
            ),
            None,
        )
        if row is None:
            raise KeyError("meta-model artifact does not exist")
        return MetaModelArtifact.model_validate(row)

    def _find_artifact(self, calibration_id: str) -> CalibrationArtifact | None:
        row = next(
            (
                row
                for row in self.store.values(CALIBRATION_ARTIFACT_COLLECTION)
                if row.get("calibration_id") == calibration_id
            ),
            None,
        )
        return CalibrationArtifact.model_validate(row) if row is not None else None

    def _persist(self, artifact: CalibrationArtifact) -> None:
        self.store.upsert(
            CALIBRATION_ARTIFACT_COLLECTION,
            artifact.calibration_id,
            artifact.model_dump(mode="json"),
        )


def _fit_platt(probabilities: tuple[float, ...], targets: tuple[bool, ...]) -> tuple[float, float]:
    features = tuple(_logit(probability) for probability in probabilities)
    slope = 1.0
    target_rate = min(1 - 1e-6, max(1e-6, fmean(targets)))
    intercept = math.log(target_rate / (1 - target_rate))
    learning_rate = 0.05
    for _ in range(1_000):
        estimates = tuple(_sigmoid(slope * feature + intercept) for feature in features)
        errors = tuple(estimate - float(target) for estimate, target in zip(estimates, targets, strict=True))
        slope_gradient = fmean(error * feature for error, feature in zip(errors, features, strict=True))
        slope_gradient += 0.001 * slope
        slope -= learning_rate * slope_gradient
        intercept -= learning_rate * fmean(errors)
    return slope, intercept


def _apply_platt(probability: float, slope: float, intercept: float) -> float:
    return _sigmoid(slope * _logit(probability) + intercept)


def _fit_isotonic(
    probabilities: tuple[float, ...],
    targets: tuple[bool, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    ordered = sorted(zip(probabilities, targets, strict=True), key=lambda row: row[0])
    blocks: list[list[float]] = []
    for probability, target in ordered:
        blocks.append([probability, probability, float(target), 1.0])
        while len(blocks) >= 2:
            left_mean = blocks[-2][2] / blocks[-2][3]
            right_mean = blocks[-1][2] / blocks[-1][3]
            if left_mean <= right_mean:
                break
            right = blocks.pop()
            left = blocks.pop()
            blocks.append(
                [left[0], right[1], left[2] + right[2], left[3] + right[3]]
            )
    thresholds = tuple(block[1] for block in blocks)
    values = tuple(block[2] / block[3] for block in blocks)
    return thresholds, values


def _apply_isotonic(
    probability: float,
    thresholds: tuple[float, ...],
    values: tuple[float, ...],
) -> float:
    index = min(bisect_left(thresholds, probability), len(values) - 1)
    return values[index]


def _metrics(probabilities: tuple[float, ...], targets: tuple[bool, ...]) -> CalibrationMetrics:
    if not probabilities or len(probabilities) != len(targets):
        raise ValueError("calibration metrics require aligned non-empty inputs")
    clipped = tuple(min(1 - 1e-12, max(1e-12, value)) for value in probabilities)
    brier = fmean((probability - float(target)) ** 2 for probability, target in zip(probabilities, targets, strict=True))
    log_loss = -fmean(
        float(target) * math.log(probability) + (1 - float(target)) * math.log(1 - probability)
        for probability, target in zip(clipped, targets, strict=True)
    )
    bins: list[ReliabilityBin] = []
    weighted_error = 0.0
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        members = tuple(
            (probability, target)
            for probability, target in zip(probabilities, targets, strict=True)
            if (
                lower <= probability <= upper
                if index == 9
                else lower <= probability < upper
            )
        )
        if not members:
            bins.append(ReliabilityBin(lower_bound=lower, upper_bound=upper, sample_count=0))
            continue
        mean_probability = fmean(row[0] for row in members)
        observed_rate = fmean(row[1] for row in members)
        confidence_low, confidence_high = _wilson_interval(sum(row[1] for row in members), len(members))
        weighted_error += len(members) / len(probabilities) * abs(mean_probability - observed_rate)
        bins.append(
            ReliabilityBin(
                lower_bound=lower,
                upper_bound=upper,
                sample_count=len(members),
                mean_probability=mean_probability,
                observed_tp_before_sl_rate=observed_rate,
                confidence_low=confidence_low,
                confidence_high=confidence_high,
            )
        )
    return CalibrationMetrics(
        sample_count=len(probabilities),
        brier_score=brier,
        log_loss=log_loss,
        expected_calibration_error=weighted_error,
        reliability_bins=tuple(bins),
    )


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + z**2 / total
    center = (rate + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _logit(probability: float) -> float:
    clipped = min(1 - 1e-9, max(1e-9, probability))
    return math.log(clipped / (1 - clipped))


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1 / (1 + exponential)
    exponential = math.exp(value)
    return exponential / (1 + exponential)


__all__ = [
    "CALIBRATED_PROBABILITY_COLLECTION",
    "CALIBRATION_ARTIFACT_COLLECTION",
    "ProbabilityCalibrationEngine",
]
