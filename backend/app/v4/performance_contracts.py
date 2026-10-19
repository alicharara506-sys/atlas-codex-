"""Traceable performance and calibration contracts for ATLAS V4 Phase 14."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.v4.contracts import FrozenModel

EvidenceStatus = Literal["AVAILABLE", "PARTIAL", "DATA_UNAVAILABLE"]


class PerformanceSlice(FrozenModel):
    dimension: Literal["ALL", "INSTRUMENT", "TIMEFRAME", "REGIME"]
    key: str
    published_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    win_count: int = Field(ge=0)
    loss_count: int = Field(ge=0)
    win_rate: float | None = Field(default=None, ge=0, le=1)
    mean_realized_r: float | None = None
    cumulative_realized_r: float | None = None
    signal_ids: tuple[str, ...] = ()
    tracking_ids: tuple[str, ...] = ()
    status: EvidenceStatus


class ReliabilityBinSummary(FrozenModel):
    lower_bound: float = Field(ge=0, le=1)
    upper_bound: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=0)
    mean_probability: float | None = Field(default=None, ge=0, le=1)
    observed_rate: float | None = Field(default=None, ge=0, le=1)
    confidence_low: float | None = Field(default=None, ge=0, le=1)
    confidence_high: float | None = Field(default=None, ge=0, le=1)


class CalibrationDashboardItem(FrozenModel):
    calibration_id: str = Field(min_length=64, max_length=64)
    source_validation_run_id: str = Field(min_length=64, max_length=64)
    meta_model_id: str = Field(min_length=64, max_length=64)
    created_at: datetime
    status: str
    method: str
    sample_count: int = Field(ge=0)
    evaluation_sample_count: int = Field(ge=0)
    brier_before: float | None = Field(default=None, ge=0, le=1)
    brier_after: float | None = Field(default=None, ge=0, le=1)
    ece_after: float | None = Field(default=None, ge=0, le=1)
    uncertainty: str
    reliability_bins: tuple[ReliabilityBinSummary, ...]


class BacktestDashboardItem(FrozenModel):
    backtest_run_id: str = Field(min_length=64, max_length=64)
    instrument: str
    created_at: datetime
    sample_count: int = Field(ge=0)
    win_rate: float | None = Field(default=None, ge=0, le=1)
    mean_net_return: float | None = None
    cumulative_net_return: float
    profit_factor: float | None = Field(default=None, ge=0)
    maximum_drawdown: float = Field(ge=0)
    mean_gross_return: float | None = None
    cost_drag: float | None = None
    spread_bps: float = Field(ge=0)
    slippage_bps: float = Field(ge=0)
    commission_bps_round_turn: float = Field(ge=0)
    daily_swap_bps: float


class RobustnessDashboardItem(FrozenModel):
    robustness_run_id: str = Field(min_length=64, max_length=64)
    status: str
    sample_count: int = Field(ge=0)
    bootstrap_samples: int = Field(ge=0)
    variants_tested: int = Field(ge=1)
    mean_return: float | None = None
    stressed_mean_return: float | None = None
    deflated_sharpe_ratio: float | None = None
    probability_backtest_overfitting: float | None = Field(default=None, ge=0, le=1)
    reason: str


class ExperimentDashboardItem(FrozenModel):
    experiment_id: str = Field(min_length=64, max_length=64)
    registered_at: datetime
    name: str
    status: str
    total_variants: int = Field(ge=1)
    feature_sets: tuple[str, ...]
    model_families: tuple[str, ...]
    automatic_promotion: Literal[False] = False


class ModelComparisonItem(FrozenModel):
    model_id: str = Field(min_length=64, max_length=64)
    model_version: str
    family: str
    created_at: datetime
    sample_count: int = Field(gt=0)
    brier_score: float = Field(ge=0, le=1)
    log_loss: float = Field(ge=0)
    balanced_accuracy: float | None = Field(default=None, ge=0, le=1)
    mean_net_return: float
    baseline_mean_net_return: float
    status: str
    run_ids: tuple[str, ...]


class VetoAnalytics(FrozenModel):
    attempt_count: int = Field(ge=0)
    decision_counts: dict[str, int]
    reason_counts: dict[str, int]
    publication_attempt_ids: tuple[str, ...]


class V4PerformanceDashboard(FrozenModel):
    version: Literal["performance-dashboard-v1"] = "performance-dashboard-v1"
    generated_at: datetime
    status: EvidenceStatus
    performance_slices: tuple[PerformanceSlice, ...]
    calibrations: tuple[CalibrationDashboardItem, ...]
    backtests: tuple[BacktestDashboardItem, ...]
    robustness_runs: tuple[RobustnessDashboardItem, ...]
    experiments: tuple[ExperimentDashboardItem, ...]
    model_comparisons: tuple[ModelComparisonItem, ...]
    vetoes: VetoAnalytics
    source_issues: tuple[str, ...] = ()
    all_metrics_traceable: Literal[True] = True
    fabricated_metrics: Literal[False] = False
    browser_calculation_authority: Literal[False] = False
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False


__all__ = [
    "BacktestDashboardItem",
    "CalibrationDashboardItem",
    "ExperimentDashboardItem",
    "ModelComparisonItem",
    "PerformanceSlice",
    "ReliabilityBinSummary",
    "RobustnessDashboardItem",
    "V4PerformanceDashboard",
    "VetoAnalytics",
]
