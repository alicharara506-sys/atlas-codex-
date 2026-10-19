"""Immutable typed contracts for the ATLAS V4 market-data boundary."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssetClass(str, Enum):
    FOREX = "FOREX"
    COMMODITY = "COMMODITY"


class OfficialTimeframe(str, Enum):
    M15 = "15M"
    H1 = "1H"
    H4 = "4H"
    D1 = "1D"


class VolumeProvenance(str, Enum):
    BROKER_TICK_VOLUME = "BROKER_TICK_VOLUME"
    BROKER_REAL_VOLUME = "BROKER_REAL_VOLUME"
    EXCHANGE_VOLUME = "EXCHANGE_VOLUME"
    FUTURES_VOLUME = "FUTURES_VOLUME"
    ESTIMATED_PROXY = "ESTIMATED_PROXY"
    UNAVAILABLE = "UNAVAILABLE"


class MT5ConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DISCONNECTED = "DISCONNECTED"
    AUTH_FAILED = "AUTH_FAILED"
    DEGRADED = "DEGRADED"
    MARKET_CLOSED = "MARKET_CLOSED"


class DataQualityStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"


class IssueSeverity(str, Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class FeatureQuality(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class EvidenceFamily(str, Enum):
    STRUCTURE = "STRUCTURE"
    AUCTION = "AUCTION"
    VOLATILITY = "VOLATILITY"
    VOLUME = "VOLUME"
    REGIME = "REGIME"
    MULTI_TIMEFRAME = "MULTI_TIMEFRAME"
    LIQUIDITY = "LIQUIDITY"


class MarketTrend(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    RANGE = "RANGE"
    UNCERTAIN = "UNCERTAIN"


class DirectionalEvent(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NONE = "NONE"


class RegimeState(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"
    TRANSITION = "TRANSITION"
    UNCERTAIN = "UNCERTAIN"


class AuctionState(str, Enum):
    BALANCED = "BALANCED"
    IMBALANCED_UP = "IMBALANCED_UP"
    IMBALANCED_DOWN = "IMBALANCED_DOWN"
    ACCEPTANCE = "ACCEPTANCE"
    REJECTION = "REJECTION"
    FAILED_AUCTION = "FAILED_AUCTION"
    UNCERTAIN = "UNCERTAIN"


class LiquidityState(str, Enum):
    ADEQUATE = "ADEQUATE"
    THIN = "THIN"
    STRESSED = "STRESSED"
    UNAVAILABLE = "UNAVAILABLE"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class V4Bar(FrozenModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_geometry(self) -> V4Bar:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("invalid OHLC geometry")
        return self


class V4Quote(FrozenModel):
    instrument: str
    broker_symbol: str
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    timestamp: datetime

    @model_validator(mode="after")
    def validate_spread(self) -> V4Quote:
        if self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        return self


class V4Tick(FrozenModel):
    timestamp: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    last: float | None = Field(default=None, ge=0)
    volume: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_spread(self) -> V4Tick:
        if self.ask < self.bid:
            raise ValueError("tick ask cannot be below bid")
        return self


class V4SymbolMetadata(FrozenModel):
    instrument: str
    broker_symbol: str
    digits: int | None = Field(default=None, ge=0, le=12)
    point: float | None = Field(default=None, gt=0)
    trade_contract_size: float | None = Field(default=None, gt=0)
    volume_min: float | None = Field(default=None, ge=0)
    volume_max: float | None = Field(default=None, ge=0)
    volume_step: float | None = Field(default=None, gt=0)
    trade_mode: int | str | None = None


class SwingPoint(FrozenModel):
    timestamp: datetime
    price: float = Field(gt=0)
    kind: Literal["HIGH", "LOW"]
    bar_index: int = Field(ge=0)


class MarketStructureResult(FrozenModel):
    trend: MarketTrend
    bos: DirectionalEvent
    choch: DirectionalEvent
    breakout: DirectionalEvent
    retest_confirmed: bool
    support: float | None = Field(default=None, gt=0)
    resistance: float | None = Field(default=None, gt=0)
    swing_highs: tuple[SwingPoint, ...] = ()
    swing_lows: tuple[SwingPoint, ...] = ()


class VolatilityResult(FrozenModel):
    atr14: float = Field(ge=0)
    atr_percent: float = Field(ge=0)
    realized_volatility_20: float = Field(ge=0)
    rolling_volatility_60: float = Field(ge=0)
    range_percentile: float = Field(ge=0, le=1)
    volatility_percentile: float = Field(ge=0, le=1)
    short_long_volatility_ratio: float = Field(ge=0)
    state: Literal["LOW", "NORMAL", "HIGH", "EXTREME"]


class AuctionMarketResult(FrozenModel):
    state: AuctionState
    poc: float = Field(gt=0)
    value_area_high: float = Field(gt=0)
    value_area_low: float = Field(gt=0)
    value_area_fraction: float = Field(ge=0, le=1)
    value_migration: Literal["UP", "DOWN", "FLAT"]
    acceptance: bool
    rejection: bool
    failed_auction: bool
    volume_provenance: VolumeProvenance
    global_volume_claim: Literal[False] = False


class LiquidityResult(FrozenModel):
    state: LiquidityState
    evidence_level: Literal[1] = 1
    spread_bps: float = Field(ge=0)
    tick_frequency_hz: float | None = Field(default=None, ge=0)
    price_activity_bps: float = Field(ge=0)
    session: str
    volume_provenance: VolumeProvenance
    futures_volume_available: Literal[False] = False
    order_book_available: Literal[False] = False
    global_institutional_volume_claim: Literal[False] = False


class RegimeResult(FrozenModel):
    primary: RegimeState
    probabilities: dict[RegimeState, float]
    adx14: float = Field(ge=0, le=100)
    trend_persistence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_probabilities(self) -> RegimeResult:
        if set(self.probabilities) != set(RegimeState):
            raise ValueError("regime probabilities must cover every state")
        if any(value < 0 or value > 1 for value in self.probabilities.values()):
            raise ValueError("regime probabilities must be between zero and one")
        if abs(sum(self.probabilities.values()) - 1.0) > 1e-6:
            raise ValueError("regime probabilities must sum to one")
        return self


class TimeframeFeatureSummary(FrozenModel):
    timeframe: OfficialTimeframe
    as_of: datetime
    structure: MarketStructureResult
    volatility: VolatilityResult
    auction: AuctionMarketResult
    regime: RegimeResult


class MultiTimeframeContext(FrozenModel):
    alignment: Literal["ALIGNED_BULLISH", "ALIGNED_BEARISH", "MIXED", "CONTRADICTORY", "UNCERTAIN"]
    bullish_timeframes: tuple[OfficialTimeframe, ...] = ()
    bearish_timeframes: tuple[OfficialTimeframe, ...] = ()
    uncertain_timeframes: tuple[OfficialTimeframe, ...] = ()


class FeatureRecord(FrozenModel):
    name: str
    version: str
    timestamp: datetime
    instrument: str
    timeframe: OfficialTimeframe | None = None
    source: Literal["DETERMINISTIC_MATH"] = "DETERMINISTIC_MATH"
    evidence_family: EvidenceFamily | None = None
    value: float | int | str | bool | None
    quality: FeatureQuality
    source_snapshot_id: str
    input_cutoff: datetime
    volume_provenance: VolumeProvenance | None = None


class FeatureSet(FrozenModel):
    feature_set_id: str = Field(min_length=64, max_length=64)
    feature_version: str
    instrument: str
    source_snapshot_id: str
    computed_at: datetime
    input_cutoff: datetime
    input_quality: DataQualityStatus
    timeframes: dict[OfficialTimeframe, TimeframeFeatureSummary]
    multi_timeframe: MultiTimeframeContext
    liquidity: LiquidityResult
    records: tuple[FeatureRecord, ...]
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False


class MT5HealthReport(FrozenModel):
    state: MT5ConnectionState
    checked_at: datetime
    latency_ms: float = Field(ge=0)
    mt5_connected: bool
    symbol_available: bool
    data_fresh: bool
    account_mode: str | None = None
    broker: str | None = None
    server: str | None = None
    last_tick_at: datetime | None = None
    tick_age_seconds: float | None = Field(default=None, ge=0)
    issues: tuple[str, ...] = ()
    read_only: Literal[True] = True
    order_submission: Literal[False] = False


class DataQualityIssue(FrozenModel):
    code: str
    severity: IssueSeverity
    message: str
    timeframe: OfficialTimeframe | None = None
    blocking: bool = False


class DataQualityReport(FrozenModel):
    status: DataQualityStatus
    score: float = Field(ge=0, le=100)
    checked_at: datetime
    issues: tuple[DataQualityIssue, ...] = ()
    publication_eligible: bool


class MarketSnapshot(FrozenModel):
    snapshot_id: str = Field(min_length=64, max_length=64)
    schema_version: Literal["4.0.0-alpha.1"] = "4.0.0-alpha.1"
    instrument: str
    asset_class: AssetClass
    captured_at: datetime
    source: Literal["MT5"] = "MT5"
    broker_symbol: str
    broker: str | None = None
    server: str | None = None
    account_mode: str | None = None
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread: float = Field(ge=0)
    spread_bps: float = Field(ge=0)
    session: str
    volume_provenance: VolumeProvenance
    timeframes: dict[OfficialTimeframe, tuple[V4Bar, ...]]
    first_tick_at: datetime | None = None
    last_tick: V4Tick | None = None
    tick_count: int = Field(ge=0)
    symbol_metadata: V4SymbolMetadata
    health: MT5HealthReport
    data_quality: DataQualityReport
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False


__all__ = [
    "AssetClass",
    "AuctionMarketResult",
    "AuctionState",
    "DataQualityIssue",
    "DataQualityReport",
    "DataQualityStatus",
    "DirectionalEvent",
    "EvidenceFamily",
    "FeatureQuality",
    "FeatureRecord",
    "FeatureSet",
    "IssueSeverity",
    "LiquidityResult",
    "LiquidityState",
    "MT5ConnectionState",
    "MT5HealthReport",
    "MarketSnapshot",
    "MarketStructureResult",
    "MarketTrend",
    "MultiTimeframeContext",
    "OfficialTimeframe",
    "RegimeResult",
    "RegimeState",
    "SwingPoint",
    "TimeframeFeatureSummary",
    "V4Bar",
    "V4Quote",
    "V4SymbolMetadata",
    "V4Tick",
    "VolatilityResult",
    "VolumeProvenance",
]
