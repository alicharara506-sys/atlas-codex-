"""Build traceable Phase 14 metrics exclusively from persisted V4 artifacts."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.core.store import RecordStore
from app.v4.calibration import CALIBRATION_ARTIFACT_COLLECTION
from app.v4.calibration_contracts import CalibrationArtifact
from app.v4.ml_contracts import ModelStatusRecord, PrimaryModelArtifact
from app.v4.performance_contracts import (
    BacktestDashboardItem,
    CalibrationDashboardItem,
    EvidenceStatus,
    ExperimentDashboardItem,
    ModelComparisonItem,
    PerformanceSlice,
    ReliabilityBinSummary,
    RobustnessDashboardItem,
    V4PerformanceDashboard,
    VetoAnalytics,
)
from app.v4.primary_ml import MODEL_ARTIFACT_COLLECTION, MODEL_STATUS_COLLECTION
from app.v4.quant_backtest import BACKTEST_COLLECTION
from app.v4.quant_contracts import BacktestRun, ExperimentRecord, RobustnessResult
from app.v4.robustness import ROBUSTNESS_COLLECTION
from app.v4.signal_contracts import CanonicalSignal, SignalPublicationResult
from app.v4.signal_engine import PUBLICATION_ATTEMPT_COLLECTION, SIGNAL_COLLECTION
from app.v4.tracking_contracts import SignalLifecycleState, SignalTrackingState
from app.v4.tracking_engine import TRACKING_STATE_COLLECTION
from app.v4.validation import EXPERIMENT_COLLECTION

PerformanceDimension = Literal["ALL", "INSTRUMENT", "TIMEFRAME", "REGIME"]


class V4PerformanceService:
    """Aggregate persisted outcomes and research runs without inferring missing values."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def dashboard(self) -> V4PerformanceDashboard:
        issues: list[str] = []
        signals = _validated(self.store, SIGNAL_COLLECTION, CanonicalSignal, issues)
        tracking = _validated(self.store, TRACKING_STATE_COLLECTION, SignalTrackingState, issues)
        calibrations = _validated_recent(
            self.store,
            CALIBRATION_ARTIFACT_COLLECTION,
            CalibrationArtifact,
            issues,
            limit=64,
        )
        backtests = _validated_recent(self.store, BACKTEST_COLLECTION, BacktestRun, issues, limit=128)
        robustness = _validated_recent(self.store, ROBUSTNESS_COLLECTION, RobustnessResult, issues, limit=256)
        experiments = _validated_recent(self.store, EXPERIMENT_COLLECTION, ExperimentRecord, issues, limit=64)
        models = _validated_recent(
            self.store,
            MODEL_ARTIFACT_COLLECTION,
            PrimaryModelArtifact,
            issues,
            limit=128,
        )
        statuses = _validated_recent(self.store, MODEL_STATUS_COLLECTION, ModelStatusRecord, issues, limit=256)
        decisions = _validated(
            self.store,
            PUBLICATION_ATTEMPT_COLLECTION,
            SignalPublicationResult,
            issues,
        )
        latest_tracking = _latest_tracking(tracking)
        slices = _performance_slices(signals, latest_tracking)
        calibration_rows = tuple(
            _calibration_item(row) for row in sorted(calibrations, key=lambda item: item.created_at, reverse=True)
        )
        backtest_rows = tuple(
            _backtest_item(row) for row in sorted(backtests, key=lambda item: item.created_at, reverse=True)
        )
        robustness_rows = tuple(_robustness_item(row) for row in robustness)
        experiment_rows = tuple(
            ExperimentDashboardItem(
                experiment_id=row.experiment_id,
                registered_at=row.registered_at,
                name=row.spec.name,
                status=row.status.value,
                total_variants=row.total_variants,
                feature_sets=row.spec.feature_sets,
                model_families=row.spec.model_families,
            )
            for row in sorted(experiments, key=lambda item: item.registered_at, reverse=True)
        )
        model_rows = _model_comparisons(models, statuses)
        vetoes = _veto_analytics(decisions)
        evidence_groups = sum(
            bool(rows)
            for rows in (
                slices,
                calibration_rows,
                backtest_rows,
                robustness_rows,
                experiment_rows,
                model_rows,
            )
        )
        status: EvidenceStatus = (
            "DATA_UNAVAILABLE"
            if evidence_groups == 0 and vetoes.attempt_count == 0
            else ("AVAILABLE" if calibration_rows and backtest_rows else "PARTIAL")
        )
        return V4PerformanceDashboard(
            generated_at=datetime.now(UTC),
            status=status,
            performance_slices=slices,
            calibrations=calibration_rows,
            backtests=backtest_rows,
            robustness_runs=robustness_rows,
            experiments=experiment_rows,
            model_comparisons=model_rows,
            vetoes=vetoes,
            source_issues=tuple(sorted(set(issues))),
        )


def _performance_slices(
    signals: list[CanonicalSignal],
    tracking: dict[str, SignalTrackingState],
) -> tuple[PerformanceSlice, ...]:
    buckets: dict[
        tuple[PerformanceDimension, str],
        list[tuple[CanonicalSignal, SignalTrackingState | None]],
    ] = defaultdict(list)
    for signal in signals:
        state = tracking.get(signal.signal_id)
        buckets[("ALL", "ALL")].append((signal, state))
        buckets[("INSTRUMENT", signal.instrument)].append((signal, state))
        buckets[("TIMEFRAME", signal.timeframe.value)].append((signal, state))
        buckets[("REGIME", signal.regime.value)].append((signal, state))
    rows: list[PerformanceSlice] = []
    for (dimension, key), records in sorted(buckets.items()):
        resolved = [
            (signal, state)
            for signal, state in records
            if state is not None and state.state in {SignalLifecycleState.TP3_HIT, SignalLifecycleState.STOPPED}
        ]
        wins = sum(state.state is SignalLifecycleState.TP3_HIT for _, state in resolved)
        losses = sum(state.state is SignalLifecycleState.STOPPED for _, state in resolved)
        realized = [state.live_r for _, state in resolved if state.live_r is not None]
        rows.append(
            PerformanceSlice(
                dimension=dimension,
                key=key,
                published_count=len(records),
                resolved_count=len(resolved),
                win_count=wins,
                loss_count=losses,
                win_rate=(wins / len(resolved)) if resolved else None,
                mean_realized_r=fmean(realized) if realized else None,
                cumulative_realized_r=sum(realized) if realized else None,
                signal_ids=tuple(signal.signal_id for signal, _ in records),
                tracking_ids=tuple(state.tracking_id for _, state in records if state is not None),
                status="AVAILABLE" if resolved else "PARTIAL",
            )
        )
    return tuple(rows)


def _calibration_item(row: CalibrationArtifact) -> CalibrationDashboardItem:
    metrics = row.post_calibration_metrics
    return CalibrationDashboardItem(
        calibration_id=row.calibration_id,
        source_validation_run_id=row.source_validation_run_id,
        meta_model_id=row.meta_model_id,
        created_at=row.created_at,
        status=row.status.value,
        method=row.method.value,
        sample_count=row.sample_count,
        evaluation_sample_count=row.evaluation_sample_count,
        brier_before=(row.pre_calibration_metrics.brier_score if row.pre_calibration_metrics else None),
        brier_after=metrics.brier_score if metrics else None,
        ece_after=metrics.expected_calibration_error if metrics else None,
        uncertainty=row.uncertainty.value,
        reliability_bins=tuple(
            ReliabilityBinSummary(
                lower_bound=item.lower_bound,
                upper_bound=item.upper_bound,
                sample_count=item.sample_count,
                mean_probability=item.mean_probability,
                observed_rate=item.observed_tp_before_sl_rate,
                confidence_low=item.confidence_low,
                confidence_high=item.confidence_high,
            )
            for item in (metrics.reliability_bins if metrics else ())
        ),
    )


def _backtest_item(row: BacktestRun) -> BacktestDashboardItem:
    gross = [trade.gross_return for trade in row.trades]
    net = [trade.net_return for trade in row.trades]
    mean_gross = fmean(gross) if gross else None
    mean_net = fmean(net) if net else None
    return BacktestDashboardItem(
        backtest_run_id=row.backtest_run_id,
        instrument=row.instrument,
        created_at=row.created_at,
        sample_count=row.metrics.executed_trades,
        win_rate=row.metrics.win_rate,
        mean_net_return=row.metrics.mean_net_return,
        cumulative_net_return=row.metrics.cumulative_net_return,
        profit_factor=row.metrics.profit_factor,
        maximum_drawdown=row.metrics.maximum_drawdown,
        mean_gross_return=mean_gross,
        cost_drag=(mean_gross - mean_net) if mean_gross is not None and mean_net is not None else None,
        spread_bps=row.cost_model.spread_bps,
        slippage_bps=row.cost_model.slippage_bps,
        commission_bps_round_turn=row.cost_model.commission_bps_round_turn,
        daily_swap_bps=row.cost_model.daily_swap_bps,
    )


def _robustness_item(row: RobustnessResult) -> RobustnessDashboardItem:
    return RobustnessDashboardItem(
        robustness_run_id=row.robustness_run_id,
        status=row.status.value,
        sample_count=row.sample_count,
        bootstrap_samples=row.bootstrap_samples,
        variants_tested=row.variants_tested,
        mean_return=row.mean_return,
        stressed_mean_return=row.stressed_mean_return,
        deflated_sharpe_ratio=row.deflated_sharpe_ratio,
        probability_backtest_overfitting=row.probability_backtest_overfitting,
        reason=row.reason,
    )


def _model_comparisons(
    models: list[PrimaryModelArtifact], statuses: list[ModelStatusRecord]
) -> tuple[ModelComparisonItem, ...]:
    latest: dict[str, ModelStatusRecord] = {}
    for status in statuses:
        current = latest.get(status.model_id)
        if current is None or status.changed_at > current.changed_at:
            latest[status.model_id] = status
    return tuple(
        ModelComparisonItem(
            model_id=row.model_id,
            model_version=row.model_version,
            family=row.family.value,
            created_at=row.created_at,
            sample_count=row.test_metrics.sample_count,
            brier_score=row.test_metrics.brier_score,
            log_loss=row.test_metrics.log_loss,
            balanced_accuracy=row.test_metrics.balanced_accuracy,
            mean_net_return=row.test_metrics.mean_net_return,
            baseline_mean_net_return=row.test_metrics.majority_baseline_mean_net_return,
            status=(latest[row.model_id].status.value if row.model_id in latest else row.initial_status.value),
            run_ids=(row.model_id,),
        )
        for row in sorted(models, key=lambda item: item.created_at, reverse=True)
    )


def _veto_analytics(decisions: list[SignalPublicationResult]) -> VetoAnalytics:
    decision_counts = Counter(row.decision.value for row in decisions)
    reason_counts = Counter(reason for row in decisions for reason in row.reasons)
    return VetoAnalytics(
        attempt_count=len(decisions),
        decision_counts=dict(sorted(decision_counts.items())),
        reason_counts=dict(sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        publication_attempt_ids=tuple(row.publication_attempt_id for row in decisions),
    )


def _latest_tracking(rows: list[SignalTrackingState]) -> dict[str, SignalTrackingState]:
    latest: dict[str, SignalTrackingState] = {}
    for row in rows:
        current = latest.get(row.signal_id)
        if current is None or row.last_updated_at > current.last_updated_at:
            latest[row.signal_id] = row
    return latest


def _validated[ModelT: BaseModel](
    store: RecordStore,
    collection: str,
    model: type[ModelT],
    issues: list[str],
) -> list[ModelT]:
    rows: list[ModelT] = []
    for index, payload in enumerate(store.values(collection)):
        try:
            rows.append(model.model_validate(payload))
        except ValidationError:
            issues.append(f"INVALID_{collection.upper()}_ROW_{index}")
    return rows


def _validated_recent[ModelT: BaseModel](
    store: RecordStore,
    collection: str,
    model: type[ModelT],
    issues: list[str],
    *,
    limit: int,
) -> list[ModelT]:
    rows: list[ModelT] = []
    for index, payload in enumerate(store.recent_values(collection, limit)):
        try:
            rows.append(model.model_validate(payload))
        except ValidationError:
            issues.append(f"INVALID_{collection.upper()}_ROW_{index}")
    return rows


__all__ = ["V4PerformanceService"]
