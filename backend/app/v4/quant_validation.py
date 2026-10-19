"""Deterministic final statistical authority for ATLAS V4 Phase 9."""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise
from statistics import fmean
from typing import Literal

from app.core.store import RecordStore, stable_key
from app.v4.calibration import (
    CALIBRATED_PROBABILITY_COLLECTION,
    CALIBRATION_ARTIFACT_COLLECTION,
)
from app.v4.calibration_contracts import (
    CalibratedProbability,
    CalibrationArtifact,
    CalibrationStatus,
    CalibrationUncertainty,
)
from app.v4.contracts import DataQualityStatus, MarketSnapshot, RegimeState
from app.v4.historical_meta import HISTORICAL_META_SAMPLE_COLLECTION
from app.v4.meta_contracts import MetaLabelSample, MetaModelArtifact, MetaPrediction
from app.v4.meta_labeling import META_MODEL_COLLECTION, META_PREDICTION_COLLECTION, MetaLabelEngine
from app.v4.ml_contracts import PrimaryCandidate
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_backtest import BACKTEST_COLLECTION
from app.v4.quant_contracts import BacktestRun, RobustnessResult, RobustnessStatus
from app.v4.quant_validation_contracts import (
    DriftStatus,
    PredictionDriftReport,
    PredictionDriftRequest,
    QuantCheckName,
    QuantCheckStatus,
    QuantDecision,
    QuantEvidenceCheck,
    QuantValidationRequest,
    QuantValidationResult,
    RegimePerformance,
    RegimeStabilityReport,
    RegimeStabilityRequest,
    RegimeStabilityStatus,
)
from app.v4.robustness import ROBUSTNESS_COLLECTION
from app.v4.snapshots import SNAPSHOT_COLLECTION

REGIME_STABILITY_COLLECTION = "v4_regime_stability_reports"
PREDICTION_DRIFT_COLLECTION = "v4_prediction_drift_reports"
QUANT_VALIDATION_COLLECTION = "v4_quant_validation_results"


class RegimeStabilityEngine:
    """Measure OOS net-return stability by observed market regime."""

    version = "regime-stability-v2"
    minimum_regime_samples = 30

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def assess(self, request: RegimeStabilityRequest) -> RegimeStabilityReport:
        artifact = _load_meta_model(self.store, request.meta_model_id)
        groups: dict[RegimeState, list[tuple[bool, float]]] = defaultdict(list)
        for row in artifact.oos_predictions:
            if row.raw_accept_probability >= artifact.config.decision_threshold:
                groups[row.regime].append((row.tp_before_sl, row.forward_return_net))
        performances = tuple(
            RegimePerformance(
                regime=regime,
                sample_count=len(rows),
                tp_before_sl_rate=sum(outcome for outcome, _ in rows) / len(rows),
                mean_net_return=fmean(net_return for _, net_return in rows),
            )
            for regime, rows in sorted(groups.items(), key=lambda item: item[0].value)
        )
        stability_id = stable_key(
            self.version,
            artifact.meta_model_id,
            request.current_regime,
            [row.model_dump(mode="json") for row in performances],
        )
        minimum_count = min((row.sample_count for row in performances), default=0)
        means = tuple(row.mean_net_return for row in performances if row.mean_net_return is not None)
        worst = min(means) if means else None
        dispersion = max(means) - min(means) if means else None
        evaluated = next(
            (row for row in performances if row.regime is request.current_regime),
            None,
        )
        if request.current_regime is not None and (
            evaluated is None or evaluated.sample_count < self.minimum_regime_samples
        ):
            status = RegimeStabilityStatus.INSUFFICIENT_EVIDENCE
            reason = f"Current {request.current_regime.value} regime requires 30 accepted chronological OOS trades"
        elif request.current_regime is not None and (
            evaluated is None or evaluated.mean_net_return is None or evaluated.mean_net_return <= 0
        ):
            status = RegimeStabilityStatus.UNSTABLE
            reason = (
                f"Accepted OOS trades have non-positive return in the current {request.current_regime.value} regime"
            )
        elif request.current_regime is not None:
            status = RegimeStabilityStatus.STABLE
            reason = f"Accepted OOS trade expectancy is positive in the current {request.current_regime.value} regime"
        elif len(performances) < 2 or minimum_count < self.minimum_regime_samples:
            status = RegimeStabilityStatus.INSUFFICIENT_EVIDENCE
            reason = "At least two observed regimes with 30 accepted chronological OOS trades each are required"
        elif worst is None or worst <= 0:
            status = RegimeStabilityStatus.UNSTABLE
            reason = "At least one evaluated regime has non-positive mean net return"
        elif dispersion is not None and dispersion > 0.05:
            status = RegimeStabilityStatus.UNSTABLE
            reason = "OOS net-return dispersion across regimes exceeds 5 percentage points"
        elif dispersion is not None and dispersion > 0.02:
            status = RegimeStabilityStatus.WATCH
            reason = "OOS net-return dispersion across regimes requires caution"
        else:
            status = RegimeStabilityStatus.STABLE
            reason = "OOS performance is positive and stable across sufficiently sampled regimes"
        report = RegimeStabilityReport(
            regime_stability_id=stability_id,
            version=self.version,
            meta_model_id=artifact.meta_model_id,
            created_at=artifact.created_at,
            status=status,
            evaluated_regime=request.current_regime,
            performances=performances,
            minimum_regime_sample_count=minimum_count,
            worst_regime_mean_net_return=worst,
            return_dispersion=dispersion,
            reason=reason,
        )
        self.store.upsert(
            REGIME_STABILITY_COLLECTION,
            report.regime_stability_id,
            report.model_dump(mode="json"),
        )
        return report


class PredictionDriftEngine:
    """Compare live predictions, or a pre-deployment OOS tail, with an earlier reference."""

    version = "prediction-drift-v3"
    minimum_reference_samples = 50
    minimum_current_samples = 30

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def assess(self, request: PredictionDriftRequest) -> PredictionDriftReport:
        artifact = _load_meta_model(self.store, request.meta_model_id)
        requested_ids = set(request.current_meta_prediction_ids)
        rows = tuple(
            MetaPrediction.model_validate(row)
            for row in self.store.values(META_PREDICTION_COLLECTION)
            if row.get("meta_prediction_id") in requested_ids
        )
        if len(rows) != len(requested_ids):
            raise KeyError("one or more current meta predictions do not exist")
        if any(row.meta_model_id != artifact.meta_model_id for row in rows):
            raise ValueError("drift predictions must share the requested meta model")
        oos_rows = tuple(sorted(artifact.oos_predictions, key=lambda row: (row.observed_at, row.sample_id)))
        live_rows = tuple(sorted(rows, key=lambda row: row.observed_at))
        window_source: Literal["LIVE", "OOS_TEMPORAL", "OOS_FIXED_MODEL"] = "LIVE"
        reference = tuple(row.raw_accept_probability for row in oos_rows)
        current = tuple(row.raw_accept_probability for row in live_rows)
        reference_end = max(row.label_end_at for row in oos_rows)
        current_start = live_rows[0].observed_at if live_rows else None
        current_end = live_rows[-1].observed_at if live_rows else None
        window_identity: object = request.current_meta_prediction_ids

        if len(current) < self.minimum_current_samples:
            # A continuously retrained model has a new identity before it can
            # accumulate 30 live predictions. Use a strictly chronological,
            # label-purged tail of that model's own OOS predictions as a
            # pre-deployment stability gate until the live window is mature.
            split = max(self.minimum_reference_samples, int(len(oos_rows) * 0.70))
            while 0 < split < len(oos_rows) and oos_rows[split].observed_at == oos_rows[split - 1].observed_at:
                split += 1
            temporal_current = oos_rows[split:]
            temporal_start = temporal_current[0].observed_at if temporal_current else None
            temporal_reference = tuple(
                row for row in oos_rows[:split] if temporal_start is not None and row.label_end_at < temporal_start
            )
            sample_by_id = {
                sample.sample_id: sample
                for payload in self.store.values(HISTORICAL_META_SAMPLE_COLLECTION)
                if (sample := _validated_meta_sample(payload)) is not None
            }
            fixed_model = MetaLabelEngine(self.store)
            if all(row.sample_id in sample_by_id for row in temporal_reference + temporal_current):
                window_source = "OOS_FIXED_MODEL"
                reference = tuple(
                    fixed_model.score_features(
                        artifact.meta_model_id,
                        sample_by_id[row.sample_id].features,
                    )
                    for row in temporal_reference
                )
                current = tuple(
                    fixed_model.score_features(
                        artifact.meta_model_id,
                        sample_by_id[row.sample_id].features,
                    )
                    for row in temporal_current
                )
            else:
                window_source = "OOS_TEMPORAL"
                reference = tuple(row.raw_accept_probability for row in temporal_reference)
                current = tuple(row.raw_accept_probability for row in temporal_current)
            reference_end = (
                max(row.label_end_at for row in temporal_reference) if temporal_reference else artifact.created_at
            )
            current_start = temporal_start
            current_end = temporal_current[-1].observed_at if temporal_current else None
            window_identity = (
                tuple(row.sample_id for row in temporal_reference),
                tuple(row.sample_id for row in temporal_current),
            )
        report_id = stable_key(
            self.version,
            artifact.meta_model_id,
            window_source,
            window_identity,
        )
        if len(reference) < self.minimum_reference_samples or len(current) < self.minimum_current_samples:
            report = PredictionDriftReport(
                drift_report_id=report_id,
                version=self.version,
                meta_model_id=artifact.meta_model_id,
                created_at=current_end or artifact.created_at,
                reference_sample_count=len(reference),
                current_sample_count=len(current),
                window_source=window_source,
                status=DriftStatus.INSUFFICIENT_EVIDENCE,
                reason=("Prediction drift requires 50 reference and 30 chronological observations"),
            )
            self._persist(report)
            return report
        assert current_start is not None
        assert current_end is not None
        if reference_end >= current_start:
            raise ValueError("current drift window must begin after all OOS reference labels end")
        psi = _population_stability_index(reference, current)
        mean_shift = abs(fmean(reference) - fmean(current))
        if psi > 0.25 or mean_shift > 0.10:
            status = DriftStatus.DRIFTED
            reason = "Prediction distribution drift exceeds a hard statistical threshold"
        elif psi > 0.10 or mean_shift > 0.05:
            status = DriftStatus.WATCH
            reason = "Prediction distribution drift requires caution and monitoring"
        else:
            status = DriftStatus.STABLE
            reason = (
                "Live prediction distribution remains within the OOS reference envelope"
                if window_source == "LIVE"
                else (
                    "Fixed-model OOS score distribution remains within the earlier OOS envelope"
                    if window_source == "OOS_FIXED_MODEL"
                    else "Recent OOS prediction distribution remains within the earlier OOS envelope"
                )
            )
        report = PredictionDriftReport(
            drift_report_id=report_id,
            version=self.version,
            meta_model_id=artifact.meta_model_id,
            created_at=current_end,
            reference_sample_count=len(reference),
            current_sample_count=len(current),
            reference_end=reference_end,
            current_start=current_start,
            current_end=current_end,
            population_stability_index=psi,
            mean_probability_shift=mean_shift,
            window_source=window_source,
            status=status,
            reason=reason,
        )
        self._persist(report)
        return report

    def _persist(self, report: PredictionDriftReport) -> None:
        self.store.upsert(
            PREDICTION_DRIFT_COLLECTION,
            report.drift_report_id,
            report.model_dump(mode="json"),
        )


class QuantValidationEngine:
    """Apply fail-closed statistical checks with immutable VETO precedence."""

    version = "quant-validation-v1"
    minimum_oos_samples = 200
    minimum_executed_trades = 30

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def validate(self, request: QuantValidationRequest) -> QuantValidationResult:
        probability = _load_probability(self.store, request.calibrated_probability_id)
        calibration = _load_calibration(self.store, probability.calibration_id)
        meta_prediction = _load_meta_prediction(self.store, probability.meta_prediction_id)
        meta_model = _load_meta_model(self.store, meta_prediction.meta_model_id)
        candidate = _load_candidate(self.store, meta_prediction.candidate_id)
        robustness = _load_robustness(self.store, request.robustness_run_id)
        regime = _load_regime(self.store, request.regime_stability_id)
        drift = _load_drift(self.store, request.drift_report_id)
        backtest = _load_backtest(self.store, request.backtest_run_id)
        snapshot = _load_snapshot(self.store, request.snapshot_id)
        self._validate_lineage(
            probability,
            calibration,
            meta_prediction,
            meta_model,
            candidate,
            regime,
            drift,
            backtest,
            snapshot,
        )
        checks = (
            self._oos_check(meta_model),
            self._sample_check(calibration),
            self._calibration_check(calibration, probability),
            self._robustness_check(robustness),
            self._regime_check(regime),
            self._multiple_testing_check(robustness),
            self._data_quality_check(snapshot),
            self._drift_check(drift),
            self._uncertainty_check(calibration),
            self._transaction_cost_check(backtest),
            self._expectancy_check(meta_model, robustness, backtest),
        )
        statuses = {check.status for check in checks}
        if QuantCheckStatus.VETO in statuses:
            decision = QuantDecision.VETO
        elif QuantCheckStatus.INSUFFICIENT in statuses:
            decision = QuantDecision.INSUFFICIENT_EVIDENCE
        elif QuantCheckStatus.CAUTION in statuses:
            decision = QuantDecision.PASS_WITH_CAUTION
        else:
            decision = QuantDecision.PASS
        non_passing = tuple(
            f"{check.name.value}: {check.summary}" for check in checks if check.status is not QuantCheckStatus.PASS
        )
        reason = "All required statistical evidence checks passed" if not non_passing else "; ".join(non_passing)
        validation_id = stable_key(
            self.version,
            request.model_dump(mode="json"),
            [check.model_dump(mode="json") for check in checks],
        )
        created_at = max(
            calibration.created_at,
            regime.created_at,
            drift.created_at,
            backtest.created_at,
            snapshot.captured_at,
        )
        result = QuantValidationResult(
            quant_validation_id=validation_id,
            version=self.version,
            created_at=created_at,
            instrument=candidate.instrument,
            candidate_id=candidate.candidate_id,
            calibrated_probability_id=probability.calibrated_probability_id,
            calibration_id=calibration.calibration_id,
            meta_model_id=meta_model.meta_model_id,
            robustness_run_id=robustness.robustness_run_id,
            regime_stability_id=regime.regime_stability_id,
            drift_report_id=drift.drift_report_id,
            backtest_run_id=backtest.backtest_run_id,
            snapshot_id=snapshot.snapshot_id,
            atlas_probability=probability.atlas_probability,
            decision=decision,
            checks=checks,
            reason=reason,
            quant_veto=decision is QuantDecision.VETO,
        )
        self.store.upsert(
            QUANT_VALIDATION_COLLECTION,
            result.quant_validation_id,
            result.model_dump(mode="json"),
        )
        return result

    @staticmethod
    def _validate_lineage(
        probability: CalibratedProbability,
        calibration: CalibrationArtifact,
        meta_prediction: MetaPrediction,
        meta_model: MetaModelArtifact,
        candidate: PrimaryCandidate,
        regime: RegimeStabilityReport,
        drift: PredictionDriftReport,
        backtest: BacktestRun,
        snapshot: MarketSnapshot,
    ) -> None:
        if probability.calibration_id != calibration.calibration_id:
            raise ValueError("calibrated probability has invalid calibration lineage")
        if calibration.meta_model_id != meta_model.meta_model_id:
            raise ValueError("calibration and meta model lineage do not match")
        if meta_prediction.candidate_id != candidate.candidate_id:
            raise ValueError("meta prediction and primary candidate lineage do not match")
        if regime.meta_model_id != meta_model.meta_model_id or drift.meta_model_id != meta_model.meta_model_id:
            raise ValueError("regime/drift evidence must share the calibrated meta model")
        if backtest.instrument != candidate.instrument or snapshot.instrument != candidate.instrument:
            raise ValueError("market, backtest, and candidate instruments must match")

    def _oos_check(self, model: MetaModelArtifact) -> QuantEvidenceCheck:
        valid = (
            model.walk_forward.purging_enabled
            and not model.walk_forward.ordinary_shuffled_kfold_used
            and all(row.out_of_sample for row in model.oos_predictions)
            and model.aggregate_oos_metrics.balanced_accuracy is not None
        )
        status = QuantCheckStatus.PASS if valid else QuantCheckStatus.INSUFFICIENT
        return _check(
            QuantCheckName.OOS_EVIDENCE,
            status,
            "Purged chronological OOS evidence is present" if valid else "Valid purged OOS evidence is incomplete",
            model.meta_model_id,
            model.walk_forward.validation_run_id,
        )

    def _sample_check(self, artifact: CalibrationArtifact) -> QuantEvidenceCheck:
        sufficient = (
            artifact.sample_count >= self.minimum_oos_samples
            and artifact.tp_before_sl_count >= 40
            and artifact.sl_before_tp_count >= 40
        )
        return _check(
            QuantCheckName.SAMPLE_SIZE,
            QuantCheckStatus.PASS if sufficient else QuantCheckStatus.INSUFFICIENT,
            (
                f"OOS sample counts are sufficient ({artifact.sample_count}, "
                f"{artifact.tp_before_sl_count}/{artifact.sl_before_tp_count})"
                if sufficient
                else "OOS sample/class counts are below governed minimums"
            ),
            artifact.calibration_id,
        )

    @staticmethod
    def _calibration_check(
        artifact: CalibrationArtifact,
        probability: CalibratedProbability,
    ) -> QuantEvidenceCheck:
        metrics = artifact.post_calibration_metrics
        if artifact.status is not CalibrationStatus.AVAILABLE or probability.atlas_probability is None:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Calibrated probability is unavailable"
        elif metrics is None:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Post-calibration OOS metrics are unavailable"
        elif artifact.calibration_audit_required or metrics.expected_calibration_error > 0.10:
            status = QuantCheckStatus.VETO
            summary = "Calibration quality requires audit or exceeds the ECE limit"
        elif metrics.brier_score > 0.25:
            status = QuantCheckStatus.CAUTION
            summary = "Calibration passes but Brier score requires caution"
        else:
            status = QuantCheckStatus.PASS
            summary = "Chronological OOS calibration diagnostics pass"
        return _check(
            QuantCheckName.CALIBRATION,
            status,
            summary,
            artifact.calibration_id,
            probability.calibrated_probability_id,
        )

    @staticmethod
    def _robustness_check(result: RobustnessResult) -> QuantEvidenceCheck:
        if result.status is not RobustnessStatus.AVAILABLE or result.sample_count < 30:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Dependence-preserving robustness evidence is unavailable"
        elif result.mean_ci_low is None or result.stressed_mean_return is None:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Robustness interval or stressed return is missing"
        elif result.mean_ci_low <= 0 or result.stressed_mean_return <= 0:
            status = QuantCheckStatus.VETO
            summary = "Robustness interval/stressed net return is non-positive"
        else:
            status = QuantCheckStatus.PASS
            summary = "Moving-block robustness interval and cost stress are positive"
        return _check(QuantCheckName.ROBUSTNESS, status, summary, result.robustness_run_id)

    @staticmethod
    def _regime_check(report: RegimeStabilityReport) -> QuantEvidenceCheck:
        mapping = {
            RegimeStabilityStatus.STABLE: QuantCheckStatus.PASS,
            RegimeStabilityStatus.WATCH: QuantCheckStatus.CAUTION,
            RegimeStabilityStatus.UNSTABLE: QuantCheckStatus.VETO,
            RegimeStabilityStatus.INSUFFICIENT_EVIDENCE: QuantCheckStatus.INSUFFICIENT,
        }
        return _check(
            QuantCheckName.REGIME_STABILITY,
            mapping[report.status],
            report.reason,
            report.regime_stability_id,
        )

    @staticmethod
    def _multiple_testing_check(result: RobustnessResult) -> QuantEvidenceCheck:
        if result.variants_tested == 1:
            status = QuantCheckStatus.PASS
            summary = "Single governed variant does not require DSR/PBO correction"
        elif not result.deflated_sharpe_available or not result.pbo_available:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Multiple variants require governed DSR and PBO evidence"
        elif result.deflated_sharpe_ratio is None or result.probability_backtest_overfitting is None:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Multiple-testing values are missing"
        elif result.deflated_sharpe_ratio <= 0 or result.probability_backtest_overfitting >= 0.50:
            status = QuantCheckStatus.VETO
            summary = "Deflated Sharpe/PBO indicates unacceptable multiple-testing risk"
        else:
            status = QuantCheckStatus.PASS
            summary = "Deflated Sharpe and PBO pass"
        return _check(
            QuantCheckName.MULTIPLE_TESTING,
            status,
            summary,
            result.robustness_run_id,
        )

    @staticmethod
    def _data_quality_check(snapshot: MarketSnapshot) -> QuantEvidenceCheck:
        healthy = (
            snapshot.data_quality.status is DataQualityStatus.HEALTHY
            and snapshot.data_quality.publication_eligible
            and snapshot.health.mt5_connected
            and snapshot.health.data_fresh
        )
        return _check(
            QuantCheckName.DATA_QUALITY,
            QuantCheckStatus.PASS if healthy else QuantCheckStatus.VETO,
            "MT5 snapshot and data quality are healthy" if healthy else "Data health is not publication-grade",
            snapshot.snapshot_id,
        )

    @staticmethod
    def _drift_check(report: PredictionDriftReport) -> QuantEvidenceCheck:
        mapping = {
            DriftStatus.STABLE: QuantCheckStatus.PASS,
            DriftStatus.WATCH: QuantCheckStatus.CAUTION,
            DriftStatus.DRIFTED: QuantCheckStatus.VETO,
            DriftStatus.INSUFFICIENT_EVIDENCE: QuantCheckStatus.INSUFFICIENT,
        }
        return _check(
            QuantCheckName.MODEL_DRIFT,
            mapping[report.status],
            report.reason,
            report.drift_report_id,
        )

    @staticmethod
    def _uncertainty_check(artifact: CalibrationArtifact) -> QuantEvidenceCheck:
        mapping = {
            CalibrationUncertainty.LOW: QuantCheckStatus.PASS,
            CalibrationUncertainty.MODERATE: QuantCheckStatus.CAUTION,
            CalibrationUncertainty.HIGH: QuantCheckStatus.VETO,
            CalibrationUncertainty.UNAVAILABLE: QuantCheckStatus.INSUFFICIENT,
        }
        return _check(
            QuantCheckName.UNCERTAINTY,
            mapping[artifact.uncertainty],
            f"Calibration uncertainty is {artifact.uncertainty.value}",
            artifact.calibration_id,
        )

    def _transaction_cost_check(self, run: BacktestRun) -> QuantEvidenceCheck:
        friction = run.cost_model.spread_bps + run.cost_model.slippage_bps + run.cost_model.commission_bps_round_turn
        actual_executed = sum(trade.entry_at is not None for trade in run.trades)
        metrics = run.metrics
        if friction <= 0:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Backtest does not model non-zero spread/slippage/commission friction"
        elif metrics.executed_trades < self.minimum_executed_trades:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Cost-aware backtest has fewer than 30 executed trades"
        elif actual_executed != metrics.executed_trades:
            status = QuantCheckStatus.VETO
            summary = "Backtest execution counts are internally inconsistent"
        elif (
            metrics.mean_net_return is None
            or metrics.mean_net_return <= 0
            or metrics.profit_factor is None
            or metrics.profit_factor <= 1
        ):
            status = QuantCheckStatus.VETO
            summary = "Net cost-aware return or profit factor is non-positive"
        else:
            status = QuantCheckStatus.PASS
            summary = "Spread, slippage, commission, swap, and net outcomes are modeled"
        return _check(QuantCheckName.TRANSACTION_COSTS, status, summary, run.backtest_run_id)

    @staticmethod
    def _expectancy_check(
        model: MetaModelArtifact,
        robustness: RobustnessResult,
        backtest: BacktestRun,
    ) -> QuantEvidenceCheck:
        filtered = model.aggregate_oos_metrics.filtered_mean_net_return
        stressed = robustness.stressed_mean_return
        backtest_mean = backtest.metrics.mean_net_return
        if stressed is None or backtest_mean is None:
            status = QuantCheckStatus.INSUFFICIENT
            summary = "Net expectancy evidence is incomplete"
        elif not model.material_oos_net_improvement or filtered <= 0 or stressed <= 0 or backtest_mean <= 0:
            status = QuantCheckStatus.VETO
            summary = "OOS, stressed, or cost-aware net expectancy is non-positive"
        else:
            status = QuantCheckStatus.PASS
            summary = "OOS, stressed, and cost-aware net return evidence is positive"
        return _check(
            QuantCheckName.EXPECTANCY,
            status,
            summary,
            model.meta_model_id,
            robustness.robustness_run_id,
            backtest.backtest_run_id,
        )


def _check(
    name: QuantCheckName,
    status: QuantCheckStatus,
    summary: str,
    *artifact_ids: str,
) -> QuantEvidenceCheck:
    return QuantEvidenceCheck(
        name=name,
        status=status,
        summary=summary,
        artifact_ids=artifact_ids,
    )


def _population_stability_index(
    reference: tuple[float, ...],
    current: tuple[float, ...],
) -> float:
    epsilon = 1e-6
    total = 0.0
    ordered = sorted(reference)
    edges = tuple(
        sorted(
            {
                ordered[
                    min(
                        len(ordered) - 1,
                        math.ceil(len(ordered) * quantile / 10) - 1,
                    )
                ]
                for quantile in range(1, 10)
            }
        )
    )
    boundaries = (-math.inf, *edges, math.inf)
    for lower, upper in pairwise(boundaries):
        reference_count = sum(lower <= value < upper for value in reference)
        current_count = sum(lower <= value < upper for value in current)
        reference_rate = max(epsilon, reference_count / len(reference))
        current_rate = max(epsilon, current_count / len(current))
        total += (current_rate - reference_rate) * math.log(current_rate / reference_rate)
    return total


def _validated_meta_sample(payload: dict[str, object]) -> MetaLabelSample | None:
    try:
        return MetaLabelSample.model_validate(payload)
    except ValueError:
        return None


def _row(store: RecordStore, collection: str, field: str, value: str) -> dict[str, object]:
    row = next((row for row in store.values(collection) if row.get(field) == value), None)
    if row is None:
        raise KeyError(f"{collection} artifact does not exist")
    return row


def _load_probability(store: RecordStore, value: str) -> CalibratedProbability:
    return CalibratedProbability.model_validate(
        _row(store, CALIBRATED_PROBABILITY_COLLECTION, "calibrated_probability_id", value)
    )


def _load_calibration(store: RecordStore, value: str) -> CalibrationArtifact:
    return CalibrationArtifact.model_validate(_row(store, CALIBRATION_ARTIFACT_COLLECTION, "calibration_id", value))


def _load_meta_prediction(store: RecordStore, value: str) -> MetaPrediction:
    return MetaPrediction.model_validate(_row(store, META_PREDICTION_COLLECTION, "meta_prediction_id", value))


def _load_meta_model(store: RecordStore, value: str) -> MetaModelArtifact:
    return MetaModelArtifact.model_validate(_row(store, META_MODEL_COLLECTION, "meta_model_id", value))


def _load_candidate(store: RecordStore, value: str) -> PrimaryCandidate:
    return PrimaryCandidate.model_validate(_row(store, PRIMARY_CANDIDATE_COLLECTION, "candidate_id", value))


def _load_robustness(store: RecordStore, value: str) -> RobustnessResult:
    return RobustnessResult.model_validate(_row(store, ROBUSTNESS_COLLECTION, "robustness_run_id", value))


def _load_regime(store: RecordStore, value: str) -> RegimeStabilityReport:
    return RegimeStabilityReport.model_validate(_row(store, REGIME_STABILITY_COLLECTION, "regime_stability_id", value))


def _load_drift(store: RecordStore, value: str) -> PredictionDriftReport:
    return PredictionDriftReport.model_validate(_row(store, PREDICTION_DRIFT_COLLECTION, "drift_report_id", value))


def _load_backtest(store: RecordStore, value: str) -> BacktestRun:
    return BacktestRun.model_validate(_row(store, BACKTEST_COLLECTION, "backtest_run_id", value))


def _load_snapshot(store: RecordStore, value: str) -> MarketSnapshot:
    return MarketSnapshot.model_validate(_row(store, SNAPSHOT_COLLECTION, "snapshot_id", value))


__all__ = [
    "PREDICTION_DRIFT_COLLECTION",
    "QUANT_VALIDATION_COLLECTION",
    "REGIME_STABILITY_COLLECTION",
    "PredictionDriftEngine",
    "QuantValidationEngine",
    "RegimeStabilityEngine",
]
