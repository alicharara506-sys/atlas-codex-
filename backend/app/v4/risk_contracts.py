"""Expectancy, structural target, exposure, and hard-risk contracts for V4 Phase 10."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel, LiquidityState, OfficialTimeframe
from app.v4.ml_contracts import MLTarget


class ExpectancyStatus(str, Enum):
    POSITIVE = "POSITIVE"
    NON_POSITIVE = "NON_POSITIVE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BLOCKED = "BLOCKED"


class TargetStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BLOCKED = "BLOCKED"


class ExposureStatus(str, Enum):
    ACCEPTABLE = "ACCEPTABLE"
    VETO = "VETO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RiskDecision(str, Enum):
    PASS = "PASS"
    RISK_VETO = "RISK_VETO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RiskCheckStatus(str, Enum):
    PASS = "PASS"
    VETO = "VETO"
    INSUFFICIENT = "INSUFFICIENT"


class RiskCheckName(str, Enum):
    QUANT = "QUANT"
    EXPECTANCY = "EXPECTANCY"
    STRUCTURAL_TARGETS = "STRUCTURAL_TARGETS"
    DAILY_DRAWDOWN = "DAILY_DRAWDOWN"
    WEEKLY_DRAWDOWN = "WEEKLY_DRAWDOWN"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    PORTFOLIO_HEAT = "PORTFOLIO_HEAT"
    FACTOR_CONCENTRATION = "FACTOR_CONCENTRATION"
    CORRELATION = "CORRELATION"
    DATA_QUALITY = "DATA_QUALITY"
    MODEL_HEALTH = "MODEL_HEALTH"
    EVENT_RISK = "EVENT_RISK"
    LIQUIDITY = "LIQUIDITY"


class ExpectancyRequest(FrozenModel):
    quant_validation_id: str = Field(min_length=64, max_length=64)
    backtest_run_id: str = Field(min_length=64, max_length=64)


class ExpectancyReport(FrozenModel):
    expectancy_id: str = Field(min_length=64, max_length=64)
    version: str
    created_at: datetime
    instrument: str
    quant_validation_id: str = Field(min_length=64, max_length=64)
    backtest_run_id: str = Field(min_length=64, max_length=64)
    status: ExpectancyStatus
    probability_win: float | None = Field(default=None, ge=0, le=1)
    sample_count: int = Field(ge=0)
    win_count: int = Field(ge=0)
    loss_count: int = Field(ge=0)
    average_gross_win: float | None = Field(default=None, ge=0)
    average_gross_loss: float | None = Field(default=None, ge=0)
    average_cost_return: float | None = Field(default=None, ge=0)
    expected_value_net: float | None = None
    formula: Literal["P(win)*AvgWin - P(loss)*AvgLoss - Costs"] = (
        "P(win)*AvgWin - P(loss)*AvgLoss - Costs"
    )
    positive_validated_expectancy: bool
    reason: str = Field(min_length=1, max_length=1_000)
    research_only: Literal[True] = True
    publication_authority: Literal[False] = False
    execution_enabled: Literal[False] = False


class EntryZone(FrozenModel):
    type: Literal["ZONE"] = "ZONE"
    low: float = Field(gt=0)
    high: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_zone(self) -> EntryZone:
        if self.low > self.high:
            raise ValueError("entry zone low cannot exceed high")
        return self


class StructuralTarget(FrozenModel):
    name: Literal["TP1", "TP2", "TP3"]
    price: float = Field(gt=0)
    reward_risk: float = Field(gt=0)
    basis: str = Field(min_length=1, max_length=240)


class StructuralTargetRequest(FrozenModel):
    quant_validation_id: str = Field(min_length=64, max_length=64)
    feature_set_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)


class StructuralTargetPlan(FrozenModel):
    target_plan_id: str = Field(min_length=64, max_length=64)
    version: str
    created_at: datetime
    instrument: str
    timeframe: OfficialTimeframe
    direction: MLTarget
    quant_validation_id: str = Field(min_length=64, max_length=64)
    feature_set_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)
    status: TargetStatus
    entry: EntryZone | None = None
    stop_loss: float | None = Field(default=None, gt=0)
    targets: tuple[StructuralTarget, ...] = ()
    atr: float | None = Field(default=None, gt=0)
    stop_atr_multiple: float | None = Field(default=None, gt=0)
    minimum_reward_risk: float | None = Field(default=None, gt=0)
    liquidity_state: LiquidityState
    fixed_pip_stop_used: Literal[False] = False
    elliott_used: Literal[False] = False
    fibonacci_used: Literal[False] = False
    reason: str = Field(min_length=1, max_length=1_000)
    research_only: Literal[True] = True
    publication_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_targets(self) -> StructuralTargetPlan:
        if self.status is TargetStatus.VALID:
            if self.entry is None or self.stop_loss is None or len(self.targets) != 3:
                raise ValueError("valid structural plan requires entry, stop, and TP1/TP2/TP3")
            if tuple(target.name for target in self.targets) != ("TP1", "TP2", "TP3"):
                raise ValueError("structural targets must be ordered TP1, TP2, TP3")
            if self.minimum_reward_risk is None or self.minimum_reward_risk < 1.5:
                raise ValueError("valid structural plan requires at least 1.5R")
        return self


class PortfolioPosition(FrozenModel):
    position_id: str = Field(min_length=1, max_length=160)
    instrument: str
    direction: MLTarget
    risk_fraction: float = Field(gt=0, le=0.10)


class CorrelationObservation(FrozenModel):
    left_instrument: str
    right_instrument: str
    coefficient: float = Field(ge=-1, le=1)
    sample_count: int = Field(ge=30)
    window: str = Field(min_length=1, max_length=80)


class PortfolioExposureRequest(FrozenModel):
    candidate_id: str = Field(min_length=64, max_length=64)
    proposed_risk_fraction: float = Field(gt=0, le=0.02)
    open_positions: tuple[PortfolioPosition, ...] = ()
    correlations: tuple[CorrelationObservation, ...] = ()
    maximum_portfolio_heat: float = Field(default=0.02, gt=0, le=0.20)
    maximum_factor_exposure: float = Field(default=0.015, gt=0, le=0.20)
    maximum_usd_concentration: float = Field(default=1.25, gt=0, le=5)
    maximum_absolute_correlation: float = Field(default=0.75, gt=0, le=1)


class PortfolioExposureReport(FrozenModel):
    exposure_id: str = Field(min_length=64, max_length=64)
    version: str
    created_at: datetime
    candidate_id: str = Field(min_length=64, max_length=64)
    proposed_instrument: str
    proposed_risk_fraction: float = Field(gt=0, le=0.02)
    status: ExposureStatus
    portfolio_heat: float = Field(ge=0)
    currency_factor_exposure: dict[str, float]
    commodity_exposure: dict[str, float]
    usd_concentration: float = Field(ge=0, le=5)
    maximum_relevant_correlation: float | None = Field(default=None, ge=0, le=1)
    repeated_factor_exposures: tuple[str, ...] = ()
    veto_reasons: tuple[str, ...] = ()
    proposed_position_included: Literal[True] = True
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False


class RiskPolicy(FrozenModel):
    maximum_risk_per_signal: float = Field(default=0.005, gt=0, le=0.02)
    maximum_daily_drawdown: float = Field(default=0.02, gt=0, le=0.20)
    maximum_weekly_drawdown: float = Field(default=0.04, gt=0, le=0.30)
    maximum_consecutive_losses: int = Field(default=4, ge=1, le=20)
    maximum_portfolio_heat: float = Field(default=0.02, gt=0, le=0.20)


class RiskAccountState(FrozenModel):
    as_of: datetime
    daily_drawdown: float = Field(ge=0, le=1)
    weekly_drawdown: float = Field(ge=0, le=1)
    consecutive_losses: int = Field(ge=0)


class RiskOverlayRequest(FrozenModel):
    quant_validation_id: str = Field(min_length=64, max_length=64)
    expectancy_id: str = Field(min_length=64, max_length=64)
    target_plan_id: str = Field(min_length=64, max_length=64)
    exposure_id: str = Field(min_length=64, max_length=64)
    context_set_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)
    account_state: RiskAccountState
    policy: RiskPolicy = RiskPolicy()


class RiskCheck(FrozenModel):
    name: RiskCheckName
    status: RiskCheckStatus
    summary: str = Field(min_length=1, max_length=1_000)
    artifact_ids: tuple[str, ...] = ()


class RiskOverlayResult(FrozenModel):
    risk_overlay_id: str = Field(min_length=64, max_length=64)
    version: str
    created_at: datetime
    instrument: str
    quant_validation_id: str = Field(min_length=64, max_length=64)
    expectancy_id: str = Field(min_length=64, max_length=64)
    target_plan_id: str = Field(min_length=64, max_length=64)
    exposure_id: str = Field(min_length=64, max_length=64)
    context_set_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)
    decision: RiskDecision
    checks: tuple[RiskCheck, ...] = Field(min_length=13, max_length=13)
    approved_risk_fraction: float = Field(ge=0, le=0.02)
    probability_size_multiplier: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=2_000)
    risk_veto: bool
    veto_overrides_probability: Literal[True] = True
    large_probability_leverage_allowed: Literal[False] = False
    account_size_required: Literal[False] = False
    grade: None = None
    publication_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision(self) -> RiskOverlayResult:
        statuses = {check.status for check in self.checks}
        names = {check.name for check in self.checks}
        if names != set(RiskCheckName):
            raise ValueError("risk overlay must cover every hard-risk check exactly once")
        if (self.decision is RiskDecision.RISK_VETO) != self.risk_veto:
            raise ValueError("risk_veto must match RISK_VETO decision")
        if RiskCheckStatus.VETO in statuses and self.decision is not RiskDecision.RISK_VETO:
            raise ValueError("any hard-risk veto requires RISK_VETO")
        if self.decision is RiskDecision.PASS and statuses != {RiskCheckStatus.PASS}:
            raise ValueError("risk PASS requires every check to pass")
        if self.decision is not RiskDecision.PASS and self.approved_risk_fraction != 0:
            raise ValueError("non-passing risk results cannot approve risk")
        return self


__all__ = [
    "CorrelationObservation",
    "EntryZone",
    "ExpectancyReport",
    "ExpectancyRequest",
    "ExpectancyStatus",
    "ExposureStatus",
    "PortfolioExposureReport",
    "PortfolioExposureRequest",
    "PortfolioPosition",
    "RiskAccountState",
    "RiskCheck",
    "RiskCheckName",
    "RiskCheckStatus",
    "RiskDecision",
    "RiskOverlayRequest",
    "RiskOverlayResult",
    "RiskPolicy",
    "StructuralTarget",
    "StructuralTargetPlan",
    "StructuralTargetRequest",
    "TargetStatus",
]
