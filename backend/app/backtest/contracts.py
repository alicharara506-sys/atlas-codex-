"""Typed input and output contracts for deterministic Phase 3 backtests."""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BacktestBar(StrictModel):
    timestamp: datetime
    open: float = Field(gt=0.0)
    high: float = Field(gt=0.0)
    low: float = Field(gt=0.0)
    close: float = Field(gt=0.0)
    volume: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_ohlc(self) -> BacktestBar:
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise ValueError("bar contains impossible OHLC values")
        return self


class BacktestSeries(StrictModel):
    symbol: str = Field(min_length=1, max_length=32)
    bars: list[BacktestBar] = Field(min_length=2)

    @model_validator(mode="after")
    def chronological_bars(self) -> BacktestSeries:
        timestamps = [bar.timestamp for bar in self.bars]
        if any(current <= previous for previous, current in pairwise(timestamps)):
            raise ValueError("backtest bars must be strictly chronological")
        return self


class TradeRecord(StrictModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    signal_index: int = Field(ge=0)
    entry_index: int = Field(ge=1)
    entry_price: float = Field(gt=0.0)
    exit_price: float = Field(gt=0.0)
    return_pct: float
    weighted_return_pct: float


class EquityPoint(StrictModel):
    timestamp: datetime
    equity: float = Field(gt=0.0)
    benchmark_equity: float = Field(gt=0.0)
    period_return: float


class BacktestAuditEvent(StrictModel):
    sequence: int = Field(ge=1)
    timestamp: datetime
    summary: str
    signals: dict[str, Literal["LONG", "SHORT", "HOLD"]]


class BacktestMetrics(StrictModel):
    total_return: float
    sharpe: float
    sortino: float
    max_drawdown: float = Field(ge=0.0, le=1.0)
    profit_factor: float | None = Field(default=None, ge=0.0)
    win_rate: float = Field(ge=0.0, le=1.0)
    benchmark_return: float
    excess_return: float
    trade_count: int = Field(ge=0)


class BacktestReport(StrictModel):
    engine: Literal["ATLAS_PHASE3_DETERMINISTIC"] = "ATLAS_PHASE3_DETERMINISTIC"
    mandate_name: str
    mandate_version: str
    symbols: list[str]
    warmup_bars: int
    evaluation_bars: int
    metrics: BacktestMetrics
    trades: list[TradeRecord]
    equity: list[EquityPoint]
    audit: list[BacktestAuditEvent]
    no_lookahead: Literal[True] = True
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False


__all__ = [
    "BacktestAuditEvent",
    "BacktestBar",
    "BacktestMetrics",
    "BacktestReport",
    "BacktestSeries",
    "EquityPoint",
    "TradeRecord",
]
