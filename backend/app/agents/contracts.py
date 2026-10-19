"""Strict contracts for the Phase 2 research-only agent workflow."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeskName(StrEnum):
    FUNDAMENTALS = "fundamentals"
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"
    MACRO = "macro"
    NARRATIVE = "narrative"
    FLOW = "flow"
    INSIDER = "insider"


class SignalDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class TradeIntent(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"


def default_desk_weights() -> dict[DeskName, float]:
    return {desk: 1.0 for desk in DeskName}


class MarketObservation(StrictModel):
    """Normalized point-in-time inputs; positive scores are bullish."""

    ticker: str = Field(min_length=1, max_length=32)
    asset_class: str = Field(default="equity", min_length=1, max_length=32)
    fundamentals_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    technical_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    sentiment_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    macro_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    narrative_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    flow_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    insider_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    volatility_percentile: float | None = Field(default=None, ge=0.0, le=1.0)
    correlation_to_book: float | None = Field(default=None, ge=-1.0, le=1.0)
    current_weight: float = Field(default=0.0, ge=-1.0, le=1.0)
    requested_weight: float = Field(default=0.05, gt=0.0, le=1.0)


class PortfolioRiskContext(StrictModel):
    daily_drawdown: float = Field(default=0.0, ge=0.0, le=1.0)
    weekly_drawdown: float = Field(default=0.0, ge=0.0, le=1.0)
    gross_leverage: float = Field(default=0.0, ge=0.0)


class RiskLimits(StrictModel):
    allowed_universe: list[str] = Field(min_length=1)
    allowed_asset_classes: list[str] = Field(default_factory=lambda: ["equity", "forex", "commodity", "crypto"])
    max_position_weight: float = Field(default=0.10, gt=0.0, le=1.0)
    max_gross_leverage: float = Field(default=1.0, gt=0.0)
    max_daily_drawdown: float = Field(default=0.03, gt=0.0, le=1.0)
    max_weekly_drawdown: float = Field(default=0.06, gt=0.0, le=1.0)
    max_correlation: float = Field(default=0.80, ge=0.0, le=1.0)
    max_volatility_percentile: float = Field(default=0.90, gt=0.0, le=1.0)
    arbitration_threshold: float = Field(default=0.20, ge=0.0, lt=1.0)
    desk_weights: dict[DeskName, float] = Field(default_factory=default_desk_weights)

    @model_validator(mode="after")
    def validate_weights(self) -> RiskLimits:
        if not self.desk_weights or any(weight < 0.0 for weight in self.desk_weights.values()):
            raise ValueError("desk weights must be non-negative and non-empty")
        if sum(self.desk_weights.values()) <= 0.0:
            raise ValueError("at least one desk weight must be positive")
        if len(set(self.allowed_universe)) != len(self.allowed_universe):
            raise ValueError("allowed universe contains duplicates")
        return self


class ResearchRequest(StrictModel):
    observations: list[MarketObservation] = Field(min_length=1)
    risk_context: PortfolioRiskContext = Field(default_factory=PortfolioRiskContext)
    limits: RiskLimits
    mode: Literal["autonomous", "supervised"] = "autonomous"
    manager_override: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_observations(self) -> ResearchRequest:
        tickers = [row.ticker for row in self.observations]
        if len(set(tickers)) != len(tickers):
            raise ValueError("observations contain duplicate tickers")
        if self.mode == "autonomous" and self.manager_override:
            raise ValueError("manager override is unavailable in autonomous mode")
        return self


class DeskSignal(StrictModel):
    ticker: str
    desk: DeskName
    signal: SignalDirection
    confidence: float = Field(ge=0.0, le=1.0)
    key_evidence: list[str]
    risk_factors: list[str]
    feedback: str


class DebateResult(StrictModel):
    ticker: str
    bull_arguments: list[str] = Field(max_length=3)
    bear_arguments: list[str] = Field(max_length=3)
    consensus: SignalDirection
    moderator_summary: str
    disagreements: list[str]


class ArbitrationResult(StrictModel):
    ticker: str
    final_score: float = Field(ge=-1.0, le=1.0)
    threshold: float = Field(ge=0.0, lt=1.0)
    intent: TradeIntent
    contributions: dict[str, float]
    rationale: str


class RiskCheck(StrictModel):
    name: str
    passed: bool
    detail: str


class RiskAssessment(StrictModel):
    ticker: str
    approved: bool
    vetoed: bool
    checks: list[RiskCheck]
    veto_reasons: list[str]
    approved_weight: float = Field(ge=0.0, le=1.0)
    manager_override_recorded: bool = False


class PositionProposal(StrictModel):
    ticker: str
    intent: TradeIntent
    target_weight: float = Field(ge=-1.0, le=1.0)
    current_weight: float = Field(ge=-1.0, le=1.0)
    rebalance_delta: float = Field(ge=-2.0, le=2.0)
    reason: str


class PortfolioProposal(StrictModel):
    positions: list[PositionProposal]
    gross_target_weight: float = Field(ge=0.0)
    cash_weight: float = Field(ge=0.0, le=1.0)
    method: Literal["bounded_risk_parity"] = "bounded_risk_parity"
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False


__all__ = [
    "ArbitrationResult",
    "DebateResult",
    "DeskName",
    "DeskSignal",
    "MarketObservation",
    "PortfolioProposal",
    "PortfolioRiskContext",
    "PositionProposal",
    "ResearchRequest",
    "RiskAssessment",
    "RiskCheck",
    "RiskLimits",
    "SignalDirection",
    "TradeIntent",
]
