"""Typed, read-only presentation contracts for the ATLAS V4 terminal."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.v4.contracts import (
    AssetClass,
    DataQualityStatus,
    FrozenModel,
    MT5ConnectionState,
    OfficialTimeframe,
)
from app.v4.performance_contracts import V4PerformanceDashboard
from app.v4.signal_contracts import (
    CanonicalSignal,
    PublicationDecision,
    SignalGrade,
)
from app.v4.tracking_contracts import SignalTrackingState


class TerminalTimeframeSummary(FrozenModel):
    timeframe: OfficialTimeframe
    last_close: float | None = Field(default=None, gt=0)
    last_bar_at: datetime | None = None
    trend: str | None = None
    structure: str | None = None
    regime: str | None = None
    volatility: str | None = None
    raw_probability: float | None = Field(default=None, ge=0, le=1)
    meta_probability: float | None = Field(default=None, ge=0, le=1)
    calibrated_probability: float | None = Field(default=None, ge=0, le=1)
    event_risk: str | None = None
    quant_status: str | None = None
    selected: bool = False
    availability: Literal["AVAILABLE", "DATA_UNAVAILABLE"]


class TerminalMarketSummary(FrozenModel):
    instrument: str
    asset_class: AssetClass
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, gt=0)
    spread: float | None = Field(default=None, ge=0)
    spread_bps: float | None = Field(default=None, ge=0)
    captured_at: datetime | None = None
    session: str | None = None
    broker: str | None = None
    server: str | None = None
    account_mode: str | None = None
    last_tick_at: datetime | None = None
    tick_age_seconds: float | None = Field(default=None, ge=0)
    tick_count: int | None = Field(default=None, ge=0)
    volume_provenance: str | None = None
    mt5_status: MT5ConnectionState | None = None
    data_quality: DataQualityStatus | None = None
    data_quality_score: float | None = Field(default=None, ge=0, le=100)
    data_issues: tuple[str, ...] = ()
    liquidity: str | None = None
    mtf_alignment: str | None = None
    best_timeframe: OfficialTimeframe | None = None
    calibrated_probability: float | None = Field(default=None, ge=0, le=1)
    grade: SignalGrade | None = None
    expected_value_net: float | None = None
    regime: str | None = None
    event_risk: str | None = None
    quant_status: str | None = None
    decision: PublicationDecision | None = None
    decision_reasons: tuple[str, ...] = ()
    signal_id: str | None = Field(default=None, min_length=64, max_length=64)
    signal_direction: Literal["LONG", "SHORT"] | None = None
    tracking_state: str | None = None
    timeframes: tuple[TerminalTimeframeSummary, ...]
    availability: Literal["AVAILABLE", "DEGRADED", "DATA_UNAVAILABLE"]


class TerminalSignalSummary(FrozenModel):
    signal: CanonicalSignal
    tracking: SignalTrackingState | None = None
    event_count: int = Field(ge=0)
    revision_count: int = Field(ge=0)


class TerminalDecisionSummary(FrozenModel):
    publication_attempt_id: str = Field(min_length=64, max_length=64)
    created_at: datetime
    instrument: str
    candidate_id: str = Field(min_length=64, max_length=64)
    decision: PublicationDecision
    atlas_probability: float | None = Field(default=None, ge=0, le=1)
    grade: SignalGrade | None = None
    reasons: tuple[str, ...]
    signal_id: str | None = Field(default=None, min_length=64, max_length=64)


class TerminalCalendarEvent(FrozenModel):
    event_id: str
    instrument: str
    name: str
    currency: str
    scheduled_at: datetime
    minutes_to_event: float | None = None
    time_precision: Literal["EXACT", "DATE_ONLY"]
    risk: str
    candidate_veto: bool
    affected_signal_ids: tuple[str, ...] = ()


class TerminalModelSummary(FrozenModel):
    model_id: str = Field(min_length=64, max_length=64)
    model_version: str
    family: str
    status: str
    created_at: datetime
    supported_instruments: tuple[str, ...]
    supported_timeframes: tuple[OfficialTimeframe, ...]
    test_sample_count: int = Field(gt=0)
    test_brier_score: float = Field(ge=0, le=1)
    test_mean_net_return: float
    decision_influence: Literal[False] = False


class TerminalAIReviewSummary(FrozenModel):
    bundle_id: str = Field(min_length=64, max_length=64)
    candidate_id: str = Field(min_length=64, max_length=64)
    instrument: str
    status: str
    specialist_statuses: dict[str, str]
    contradictions: tuple[str, ...]
    invalidations: tuple[str, ...]
    total_latency_ms: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    external_api_cost_usd: float = Field(default=0.0, ge=0, le=0)
    non_voting: Literal[True] = True


class TerminalCounts(FrozenModel):
    published_signals: int = Field(ge=0)
    active_signals: int = Field(ge=0)
    pending_signals: int = Field(ge=0)
    wait_markets: int = Field(ge=0)
    quant_vetoes: int = Field(ge=0)
    risk_vetoes: int = Field(ge=0)
    event_blocks: int = Field(ge=0)
    degraded_markets: int = Field(ge=0)


class V4TerminalWorkspace(FrozenModel):
    engine: Literal["ATLAS"] = "ATLAS"
    version: Literal["4.0.0"] = "4.0.0"
    phase: Literal[15] = 15
    generated_at: datetime
    selected_instrument: str
    markets: tuple[TerminalMarketSummary, ...]
    signals: tuple[TerminalSignalSummary, ...]
    decisions: tuple[TerminalDecisionSummary, ...]
    calendar: tuple[TerminalCalendarEvent, ...]
    models: tuple[TerminalModelSummary, ...]
    ai_reviews: tuple[TerminalAIReviewSummary, ...]
    performance: V4PerformanceDashboard
    counts: TerminalCounts
    source_issues: tuple[str, ...] = ()
    metrics_status: Literal["AVAILABLE", "PARTIAL", "DATA_UNAVAILABLE"]
    browser_signal_authority: Literal[False] = False
    browser_tracking_authority: Literal[False] = False
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False


__all__ = [
    "TerminalAIReviewSummary",
    "TerminalCalendarEvent",
    "TerminalCounts",
    "TerminalDecisionSummary",
    "TerminalMarketSummary",
    "TerminalModelSummary",
    "TerminalSignalSummary",
    "TerminalTimeframeSummary",
    "V4TerminalWorkspace",
]
