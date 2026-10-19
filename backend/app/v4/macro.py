"""Numeric-first macro and rolling gold-relationship engines."""

from __future__ import annotations

import math
from datetime import datetime
from statistics import fmean, pstdev

from app.v4.context_contracts import (
    ContextAvailability,
    GoldContext,
    MacroContext,
    MacroFactorResult,
    NumericSeries,
    NumericTrend,
    RollingRelationship,
)


class MacroIntelligenceEngine:
    """Transform timestamped numeric series without LLM interpretation."""

    version = "macro-numeric-v1"

    def calculate(
        self,
        instrument: str,
        series: tuple[NumericSeries, ...],
        *,
        as_of: datetime,
        required_factors: tuple[str, ...] = (),
    ) -> MacroContext:
        unique_names = {row.name.casefold() for row in series}
        if len(unique_names) != len(series):
            raise ValueError("macro series names must be unique")
        factors: dict[str, MacroFactorResult] = {}
        for row in series:
            if row.values[-1].timestamp > as_of:
                raise ValueError(f"macro factor {row.name} contains a future observation")
            values = [point.value for point in row.values]
            current = values[-1]
            change = current - values[-2]
            history = values[-20:]
            deviation = pstdev(history) if len(history) > 1 else 0.0
            z_score = (current - fmean(history)) / deviation if deviation > 0 else None
            tolerance = max(abs(current) * 1e-6, 1e-12)
            baseline = fmean(values[-6:-1]) if len(values) >= 6 else values[0]
            if current > baseline + tolerance:
                trend = NumericTrend.UP
            elif current < baseline - tolerance:
                trend = NumericTrend.DOWN
            else:
                trend = NumericTrend.FLAT
            age_hours = (as_of - row.values[-1].timestamp).total_seconds() / 3_600
            availability = ContextAvailability.STALE if age_hours > row.max_age_hours else ContextAvailability.AVAILABLE
            factors[row.name] = MacroFactorResult(
                name=row.name,
                unit=row.unit,
                source=row.source,
                observed_at=row.values[-1].timestamp,
                current=current,
                change_1=change,
                z_score_20=z_score,
                percentile=_percentile(history, current),
                trend=trend,
                sample_count=len(values),
                availability=availability,
            )
        missing = tuple(name for name in required_factors if name.casefold() not in unique_names)
        if not factors:
            availability = ContextAvailability.DATA_UNAVAILABLE
        elif missing or any(row.availability is ContextAvailability.STALE for row in factors.values()):
            availability = ContextAvailability.DEGRADED
        else:
            availability = ContextAvailability.AVAILABLE
        return MacroContext(
            instrument=instrument,
            as_of=as_of,
            factors=factors,
            missing_factors=missing,
            availability=availability,
        )


class GoldIntelligenceEngine:
    """Measure rolling XAU relationships instead of hard-coding a direction."""

    version = "gold-relationships-v1"

    def calculate(
        self,
        gold_prices: NumericSeries | None,
        factors: tuple[NumericSeries, ...],
        *,
        as_of: datetime,
    ) -> GoldContext:
        if gold_prices is None:
            return GoldContext(
                as_of=as_of,
                relationships=(),
                availability=ContextAvailability.DATA_UNAVAILABLE,
            )
        if gold_prices.values[-1].timestamp > as_of:
            raise ValueError("gold price series contains a future observation")
        gold_returns = _returns_by_timestamp(gold_prices)
        relationships: list[RollingRelationship] = []
        for factor in factors:
            if factor.values[-1].timestamp > as_of:
                raise ValueError(f"gold factor {factor.name} contains a future observation")
            factor_returns = _returns_by_timestamp(factor)
            aligned = sorted(set(gold_returns).intersection(factor_returns))
            left = [gold_returns[timestamp] for timestamp in aligned]
            right = [factor_returns[timestamp] for timestamp in aligned]
            correlations = {window: _window_correlation(left, right, window) for window in (20, 60, 120)}
            if correlations[20] is not None:
                availability = ContextAvailability.AVAILABLE
            elif len(aligned) >= 5:
                availability = ContextAvailability.DEGRADED
            else:
                availability = ContextAvailability.DATA_UNAVAILABLE
            relationships.append(
                RollingRelationship(
                    factor=factor.name,
                    correlation_20=correlations[20],
                    correlation_60=correlations[60],
                    correlation_120=correlations[120],
                    aligned_return_samples=len(aligned),
                    availability=availability,
                )
            )
        if not relationships:
            availability = ContextAvailability.DATA_UNAVAILABLE
        elif any(row.availability is ContextAvailability.AVAILABLE for row in relationships):
            availability = ContextAvailability.AVAILABLE
        else:
            availability = ContextAvailability.DEGRADED
        return GoldContext(as_of=as_of, relationships=tuple(relationships), availability=availability)


def _returns_by_timestamp(series: NumericSeries) -> dict[datetime, float]:
    result: dict[datetime, float] = {}
    for previous, current in zip(series.values, series.values[1:], strict=False):
        if previous.value > 0 and current.value > 0:
            result[current.timestamp] = math.log(current.value / previous.value)
        else:
            result[current.timestamp] = current.value - previous.value
    return result


def _window_correlation(left: list[float], right: list[float], window: int) -> float | None:
    if len(left) < window or len(right) < window:
        return None
    x = left[-window:]
    y = right[-window:]
    mean_x = fmean(x)
    mean_y = fmean(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True))
    denominator = math.sqrt(sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y))
    if denominator <= 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def _percentile(values: list[float], current: float) -> float:
    return sum(value <= current for value in values) / len(values)


__all__ = ["GoldIntelligenceEngine", "MacroIntelligenceEngine"]
