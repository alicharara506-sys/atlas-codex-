"""Typed, immutable contracts for ATLAS V4 Phase 3 context intelligence."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from itertools import pairwise
from typing import Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel, LiquidityState


class ContextAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    INVALID = "INVALID"


class NumericTrend(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class NewsCategory(str, Enum):
    CENTRAL_BANK = "CENTRAL_BANK"
    INFLATION = "INFLATION"
    EMPLOYMENT = "EMPLOYMENT"
    GEOPOLITICAL = "GEOPOLITICAL"
    COMMODITY_SUPPLY = "COMMODITY_SUPPLY"
    CREDIT_SYSTEMIC = "CREDIT_SYSTEMIC"
    OTHER = "OTHER"


class TimedValue(FrozenModel):
    timestamp: datetime
    value: float


class NumericSeries(FrozenModel):
    name: str = Field(min_length=1, max_length=80)
    unit: str = Field(min_length=1, max_length=32)
    source: str = Field(min_length=1, max_length=120)
    values: tuple[TimedValue, ...] = Field(min_length=2)
    max_age_hours: float = Field(default=168.0, gt=0, le=24 * 366)

    @model_validator(mode="after")
    def validate_chronology(self) -> NumericSeries:
        timestamps = [row.timestamp for row in self.values]
        if any(current <= previous for previous, current in pairwise(timestamps)):
            raise ValueError("numeric series timestamps must be unique and strictly chronological")
        return self


class MacroFactorResult(FrozenModel):
    name: str
    unit: str
    source: str
    observed_at: datetime
    current: float
    change_1: float
    z_score_20: float | None = None
    percentile: float = Field(ge=0, le=1)
    trend: NumericTrend
    sample_count: int = Field(ge=2)
    availability: ContextAvailability


class MacroContext(FrozenModel):
    instrument: str
    as_of: datetime
    factors: dict[str, MacroFactorResult]
    missing_factors: tuple[str, ...] = ()
    availability: ContextAvailability
    numeric_first: Literal[True] = True
    llm_generated: Literal[False] = False
    signal_authority: Literal[False] = False


class RollingRelationship(FrozenModel):
    factor: str
    correlation_20: float | None = Field(default=None, ge=-1, le=1)
    correlation_60: float | None = Field(default=None, ge=-1, le=1)
    correlation_120: float | None = Field(default=None, ge=-1, le=1)
    aligned_return_samples: int = Field(ge=0)
    availability: ContextAvailability
    directional_assumption: Literal[False] = False


class GoldContext(FrozenModel):
    as_of: datetime
    relationships: tuple[RollingRelationship, ...]
    availability: ContextAvailability
    hard_coded_real_yield_direction: Literal[False] = False
    signal_authority: Literal[False] = False


class COTReport(FrozenModel):
    market_name: str
    report_date: datetime
    commercial_long: float = Field(ge=0)
    commercial_short: float = Field(ge=0)
    noncommercial_long: float = Field(ge=0)
    noncommercial_short: float = Field(ge=0)
    open_interest: float = Field(gt=0)
    source: Literal["CFTC_LEGACY_FUTURES_ONLY"] = "CFTC_LEGACY_FUTURES_ONLY"


class PositioningResult(FrozenModel):
    market_name: str
    report_date: datetime
    commercial_net: float
    noncommercial_net: float
    open_interest: float = Field(gt=0)
    weekly_change: float | None = None
    z_score: float | None = None
    percentile: float | None = Field(default=None, ge=0, le=1)
    sample_count: int = Field(ge=1)
    availability: ContextAvailability
    automatic_contrarian_interpretation: Literal[False] = False
    signal_authority: Literal[False] = False


class NewsItem(FrozenModel):
    title: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=120)
    published_at: datetime
    url: str | None = Field(default=None, max_length=2_000)


class NewsRiskResult(FrozenModel):
    as_of: datetime
    input_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    categories: tuple[NewsCategory, ...] = ()
    risk: RiskLevel
    uncertainty_detected: bool
    narrative_change_detected: bool
    candidate_veto: bool
    primary_signal_direction: None = None
    signal_authority: Literal[False] = False


class EconomicEvent(FrozenModel):
    event_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    currency: str = Field(min_length=3, max_length=3)
    scheduled_at: datetime
    importance: RiskLevel
    source: str = Field(min_length=1, max_length=120)
    time_precision: Literal["EXACT", "DATE_ONLY"] = "EXACT"
    previous: float | None = None
    forecast: float | None = None
    actual: float | None = None
    historical_volatility_percentile: float | None = Field(default=None, ge=0, le=1)


class EventAssessment(FrozenModel):
    event_id: str
    name: str
    currency: str
    scheduled_at: datetime
    minutes_to_event: float | None
    time_precision: Literal["EXACT", "DATE_ONLY"]
    affects_instrument: bool
    risk: RiskLevel
    candidate_veto: bool


class EventRiskResult(FrozenModel):
    instrument: str
    as_of: datetime
    risk: RiskLevel
    assessments: tuple[EventAssessment, ...]
    nearest_event_id: str | None = None
    candidate_veto: bool
    signal_authority: Literal[False] = False


class DerivativesContext(FrozenModel):
    instrument: str
    as_of: datetime
    availability: ContextAvailability
    provider: str
    options_skew: float | None = None
    implied_volatility: float | None = Field(default=None, ge=0)
    gamma: float | None = None
    futures_term_structure: float | None = None
    expected_move: float | None = Field(default=None, ge=0)
    reason: str
    fabricated: Literal[False] = False
    signal_authority: Literal[False] = False


class ContextComputationRequest(FrozenModel):
    as_of: datetime
    macro_series: tuple[NumericSeries, ...] = ()
    required_macro_factors: tuple[str, ...] = ()
    gold_prices: NumericSeries | None = None
    cot_reports: tuple[COTReport, ...] = ()
    news: tuple[NewsItem, ...] = ()
    events: tuple[EconomicEvent, ...] = ()
    liquidity_state: LiquidityState = LiquidityState.UNAVAILABLE


class ContextSet(FrozenModel):
    context_set_id: str = Field(min_length=64, max_length=64)
    context_version: str
    instrument: str
    as_of: datetime
    input_hash: str = Field(min_length=64, max_length=64)
    macro: MacroContext
    gold: GoldContext | None = None
    positioning: PositioningResult | None = None
    news: NewsRiskResult
    events: EventRiskResult
    derivatives: DerivativesContext
    candidate_veto: bool
    availability: ContextAvailability
    provider_managed_ingestion: bool
    provider_issues: tuple[str, ...] = ()
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False


__all__ = [
    "COTReport",
    "ContextAvailability",
    "ContextComputationRequest",
    "ContextSet",
    "DerivativesContext",
    "EconomicEvent",
    "EventAssessment",
    "EventRiskResult",
    "GoldContext",
    "MacroContext",
    "MacroFactorResult",
    "NewsCategory",
    "NewsItem",
    "NewsRiskResult",
    "NumericSeries",
    "NumericTrend",
    "PositioningResult",
    "RiskLevel",
    "RollingRelationship",
    "TimedValue",
]
