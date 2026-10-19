"""Shared immutable records for market and sentiment ingestion."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


def finite(value: object, label: str) -> float:
    if not isinstance(value, (str, int, float)):
        raise TypeError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class MarketBar:
    ticker: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper()
        if not ticker or self.timestamp <= 0:
            raise ValueError("MarketBar requires ticker and positive timestamp")
        prices = [finite(getattr(self, name), name) for name in ("open", "high", "low", "close")]
        if min(prices) <= 0 or self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("MarketBar has invalid OHLC geometry")
        if finite(self.volume, "volume") < 0:
            raise ValueError("MarketBar volume cannot be negative")
        object.__setattr__(self, "ticker", ticker)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FundamentalSnapshot:
    ticker: str
    timestamp: int
    metrics: dict[str, float | int | str | None]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EconomicObservation:
    series_id: str
    timestamp: int
    value: float
    source: str = "FRED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SentimentObservation:
    ticker: str
    source: str
    timestamp: int
    score: float
    text: str | None = None
    category: str = "NEWS"
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.ticker.strip() or not self.source.strip() or self.timestamp <= 0:
            raise ValueError("SentimentObservation requires ticker, source, and timestamp")
        object.__setattr__(self, "ticker", self.ticker.strip().upper())
        object.__setattr__(self, "score", max(-1.0, min(1.0, finite(self.score, "sentiment score"))))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["EconomicObservation", "FundamentalSnapshot", "MarketBar", "SentimentObservation", "finite"]
