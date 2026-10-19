"""Immutable contracts for the ATLAS V4 research-only Quant Lab."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel, V4Bar


class ResearchDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TripleBarrierLabel(str, Enum):
    LONG_WIN = "LONG_WIN"
    LONG_LOSS = "LONG_LOSS"
    SHORT_WIN = "SHORT_WIN"
    SHORT_LOSS = "SHORT_LOSS"
    TIMEOUT = "TIMEOUT"


class BarrierOrderPolicy(str, Enum):
    STOP_FIRST = "STOP_FIRST"
    TARGET_FIRST = "TARGET_FIRST"


class WalkForwardMode(str, Enum):
    EXPANDING = "EXPANDING"
    ROLLING = "ROLLING"


class ExperimentStatus(str, Enum):
    RESEARCH = "RESEARCH"
    SHADOW = "SHADOW"
    REJECTED = "REJECTED"


class HoldoutState(str, Enum):
    SEALED = "SEALED"
    CONSUMED = "CONSUMED"


class BacktestExitReason(str, Enum):
    TARGET = "TARGET"
    STOP = "STOP"
    TIMEOUT = "TIMEOUT"
    SESSION_BLOCKED = "SESSION_BLOCKED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class RobustnessStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INVALID = "INVALID"


class TripleBarrierConfig(FrozenModel):
    atr_lookback: int = Field(default=14, ge=5, le=200)
    structure_lookback: int = Field(default=20, ge=5, le=500)
    max_holding_bars: int = Field(default=20, ge=1, le=500)
    target_atr_multiple: float = Field(default=2.0, gt=0, le=20)
    stop_atr_multiple: float = Field(default=1.0, gt=0, le=20)
    use_structure: bool = True
    barrier_order_policy: BarrierOrderPolicy = BarrierOrderPolicy.STOP_FIRST


class TripleBarrierRecord(FrozenModel):
    label_id: str = Field(min_length=64, max_length=64)
    instrument: str
    direction: ResearchDirection
    signal_at: datetime
    input_cutoff: datetime
    entry_at: datetime
    resolved_at: datetime
    entry: float = Field(gt=0)
    upper_barrier: float = Field(gt=0)
    lower_barrier: float = Field(gt=0)
    atr_at_signal: float = Field(gt=0)
    label: TripleBarrierLabel
    holding_bars: int = Field(ge=1)
    same_bar_collision: bool = False
    future_data_used_for_barriers: Literal[False] = False

    @model_validator(mode="after")
    def validate_barriers_and_chronology(self) -> TripleBarrierRecord:
        if not self.lower_barrier < self.entry < self.upper_barrier:
            raise ValueError("triple barriers must surround entry")
        if self.input_cutoff != self.signal_at:
            raise ValueError("barrier input cutoff must equal the signal timestamp")
        if not self.signal_at < self.entry_at <= self.resolved_at:
            raise ValueError("triple-barrier timestamps are not chronological")
        return self


class TripleBarrierResult(FrozenModel):
    labeling_run_id: str = Field(min_length=64, max_length=64)
    instrument: str
    direction: ResearchDirection
    config: TripleBarrierConfig
    input_hash: str = Field(min_length=64, max_length=64)
    labels: tuple[TripleBarrierRecord, ...]
    leakage_safe: Literal[True] = True
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False


class ValidationSample(FrozenModel):
    sample_id: str = Field(min_length=1, max_length=160)
    observed_at: datetime
    label_end_at: datetime
    feature_hash: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_label_window(self) -> ValidationSample:
        if self.label_end_at < self.observed_at:
            raise ValueError("label_end_at cannot precede observed_at")
        return self


class WalkForwardConfig(FrozenModel):
    train_size: int = Field(ge=20, le=1_000_000)
    validation_size: int = Field(ge=5, le=100_000)
    test_size: int = Field(ge=5, le=100_000)
    step_size: int = Field(ge=1, le=100_000)
    embargo_size: int = Field(default=1, ge=0, le=10_000)
    mode: WalkForwardMode = WalkForwardMode.EXPANDING


class WalkForwardFold(FrozenModel):
    fold_index: int = Field(ge=0)
    train_ids: tuple[str, ...]
    validation_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    purged_train_ids: tuple[str, ...] = ()
    purged_validation_ids: tuple[str, ...] = ()
    embargoed_ids: tuple[str, ...] = ()
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime

    @model_validator(mode="after")
    def validate_split_order(self) -> WalkForwardFold:
        groups = (set(self.train_ids), set(self.validation_ids), set(self.test_ids), set(self.embargoed_ids))
        if any(left.intersection(right) for index, left in enumerate(groups) for right in groups[index + 1 :]):
            raise ValueError("walk-forward sample groups must be disjoint")
        if not self.train_end < self.validation_start <= self.validation_end < self.test_start <= self.test_end:
            raise ValueError("walk-forward windows must be chronological")
        if not self.train_ids or not self.validation_ids or not self.test_ids:
            raise ValueError("walk-forward folds require non-empty train, validation, and test sets")
        return self


class WalkForwardResult(FrozenModel):
    validation_run_id: str = Field(min_length=64, max_length=64)
    config: WalkForwardConfig
    sample_hash: str = Field(min_length=64, max_length=64)
    folds: tuple[WalkForwardFold, ...]
    ordinary_shuffled_kfold_used: Literal[False] = False
    purging_enabled: Literal[True] = True
    research_only: Literal[True] = True
    production_promotion: Literal[False] = False


class HoldoutManifest(FrozenModel):
    holdout_id: str = Field(min_length=64, max_length=64)
    created_at: datetime
    sample_count: int = Field(gt=0)
    start_at: datetime
    end_at: datetime
    content_hash: str = Field(min_length=64, max_length=64)
    state: HoldoutState = HoldoutState.SEALED
    permitted_purpose: Literal["FINAL_RELEASE_EVALUATION"] = "FINAL_RELEASE_EVALUATION"
    development_access_allowed: Literal[False] = False
    production_influence: Literal[False] = False


class HoldoutAccessRecord(FrozenModel):
    access_id: str = Field(min_length=64, max_length=64)
    holdout_id: str = Field(min_length=64, max_length=64)
    experiment_id: str = Field(min_length=1, max_length=160)
    accessed_at: datetime
    purpose: Literal["FINAL_RELEASE_EVALUATION"] = "FINAL_RELEASE_EVALUATION"
    one_time_access: Literal[True] = True
    production_promotion: Literal[False] = False


class ExperimentSpec(FrozenModel):
    name: str = Field(min_length=1, max_length=160)
    feature_sets: tuple[str, ...] = Field(min_length=1)
    model_families: tuple[str, ...] = Field(min_length=1)
    parameter_variants: int = Field(default=1, ge=1, le=1_000_000)
    threshold_variants: int = Field(default=1, ge=1, le=1_000_000)
    notes: str = Field(default="", max_length=2_000)


class ExperimentRecord(FrozenModel):
    experiment_id: str = Field(min_length=64, max_length=64)
    registered_at: datetime
    config_hash: str = Field(min_length=64, max_length=64)
    spec: ExperimentSpec
    total_variants: int = Field(ge=1)
    status: ExperimentStatus = ExperimentStatus.RESEARCH
    automatic_promotion: Literal[False] = False
    research_only: Literal[True] = True


class CostModel(FrozenModel):
    spread_bps: float = Field(default=0, ge=0, le=1_000)
    slippage_bps: float = Field(default=0, ge=0, le=1_000)
    commission_bps_round_turn: float = Field(default=0, ge=0, le=1_000)
    daily_swap_bps: float = Field(default=0, ge=-1_000, le=1_000)
    execution_delay_bars: int = Field(default=0, ge=0, le=100)
    allowed_utc_hours: tuple[int, ...] = tuple(range(24))
    barrier_order_policy: BarrierOrderPolicy = BarrierOrderPolicy.STOP_FIRST

    @model_validator(mode="after")
    def validate_sessions(self) -> CostModel:
        if not self.allowed_utc_hours or any(hour < 0 or hour > 23 for hour in self.allowed_utc_hours):
            raise ValueError("allowed_utc_hours must contain UTC hours between 0 and 23")
        if len(set(self.allowed_utc_hours)) != len(self.allowed_utc_hours):
            raise ValueError("allowed_utc_hours cannot contain duplicates")
        return self


class BacktestTradePlan(FrozenModel):
    trade_id: str = Field(min_length=1, max_length=160)
    instrument: str
    direction: ResearchDirection
    signal_at: datetime
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    max_holding_bars: int = Field(default=20, ge=1, le=10_000)


class BacktestTradeResult(FrozenModel):
    trade_id: str
    direction: ResearchDirection
    signal_at: datetime
    entry_at: datetime | None = None
    exit_at: datetime | None = None
    entry_fill: float | None = Field(default=None, gt=0)
    exit_fill: float | None = Field(default=None, gt=0)
    gross_return: float = 0
    net_return: float = 0
    realized_r: float | None = None
    holding_bars: int = Field(ge=0)
    exit_reason: BacktestExitReason
    same_bar_collision: bool = False
    gap_fill: bool = False


class BacktestMetrics(FrozenModel):
    total_plans: int = Field(ge=0)
    executed_trades: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    timeouts: int = Field(ge=0)
    blocked: int = Field(ge=0)
    win_rate: float | None = Field(default=None, ge=0, le=1)
    mean_net_return: float | None = None
    cumulative_net_return: float
    profit_factor: float | None = Field(default=None, ge=0)
    maximum_drawdown: float = Field(ge=0)


class BacktestRun(FrozenModel):
    backtest_run_id: str = Field(min_length=64, max_length=64)
    instrument: str
    created_at: datetime
    data_hash: str = Field(min_length=64, max_length=64)
    config_hash: str = Field(min_length=64, max_length=64)
    cost_model: CostModel
    trades: tuple[BacktestTradeResult, ...]
    metrics: BacktestMetrics
    deterministic: Literal[True] = True
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False


class RobustnessResult(FrozenModel):
    robustness_run_id: str = Field(min_length=64, max_length=64)
    status: RobustnessStatus
    sample_count: int = Field(ge=0)
    bootstrap_samples: int = Field(ge=0)
    block_size: int = Field(ge=0)
    mean_return: float | None = None
    mean_ci_low: float | None = None
    mean_ci_high: float | None = None
    stressed_mean_return: float | None = None
    null_hypothesis: str
    assumptions: tuple[str, ...]
    variants_tested: int = Field(ge=1)
    deflated_sharpe_available: bool = False
    deflated_sharpe_ratio: float | None = None
    pbo_available: bool = False
    probability_backtest_overfitting: float | None = Field(default=None, ge=0, le=1)
    reason: str
    individual_return_shuffle_used: Literal[False] = False
    automatic_promotion: Literal[False] = False

    @model_validator(mode="after")
    def validate_multiple_testing_metrics(self) -> RobustnessResult:
        if self.deflated_sharpe_available != (self.deflated_sharpe_ratio is not None):
            raise ValueError("deflated Sharpe availability must match its value")
        if self.pbo_available != (self.probability_backtest_overfitting is not None):
            raise ValueError("PBO availability must match its value")
        return self


class TripleBarrierRequest(FrozenModel):
    instrument: str
    direction: ResearchDirection
    bars: tuple[V4Bar, ...] = Field(min_length=20)
    config: TripleBarrierConfig = TripleBarrierConfig()


class WalkForwardRequest(FrozenModel):
    samples: tuple[ValidationSample, ...] = Field(min_length=30)
    config: WalkForwardConfig


class BacktestRequest(FrozenModel):
    instrument: str
    bars: tuple[V4Bar, ...] = Field(min_length=2)
    plans: tuple[BacktestTradePlan, ...]
    cost_model: CostModel = CostModel()


class RobustnessRequest(FrozenModel):
    returns: tuple[float, ...]
    variants_tested: int = Field(default=1, ge=1, le=1_000_000)
    bootstrap_samples: int = Field(default=1_000, ge=100, le=20_000)
    block_size: int | None = Field(default=None, ge=2)
    seed: int = 17
    stress_cost_bps: float = Field(default=0, ge=0, le=1_000)


class HoldoutSealRequest(FrozenModel):
    samples: tuple[ValidationSample, ...] = Field(min_length=10)
    holdout_size: int = Field(ge=5)


class HoldoutSealResponse(FrozenModel):
    manifest: HoldoutManifest
    development_sample_ids: tuple[str, ...]
    holdout_samples_returned: Literal[False] = False
    final_access_endpoint_available: Literal[False] = False


__all__ = [
    "BacktestExitReason",
    "BacktestMetrics",
    "BacktestRequest",
    "BacktestRun",
    "BacktestTradePlan",
    "BacktestTradeResult",
    "BarrierOrderPolicy",
    "CostModel",
    "ExperimentRecord",
    "ExperimentSpec",
    "ExperimentStatus",
    "HoldoutAccessRecord",
    "HoldoutManifest",
    "HoldoutSealRequest",
    "HoldoutSealResponse",
    "HoldoutState",
    "ResearchDirection",
    "RobustnessRequest",
    "RobustnessResult",
    "RobustnessStatus",
    "TripleBarrierConfig",
    "TripleBarrierLabel",
    "TripleBarrierRecord",
    "TripleBarrierRequest",
    "TripleBarrierResult",
    "ValidationSample",
    "WalkForwardConfig",
    "WalkForwardFold",
    "WalkForwardMode",
    "WalkForwardRequest",
    "WalkForwardResult",
]
