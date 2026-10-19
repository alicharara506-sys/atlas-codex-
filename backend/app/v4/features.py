"""Deterministic ATLAS V4 Phase 2 feature engines.

These engines describe market state. They have no signal, probability,
position-sizing, or execution authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean, pstdev

from app.v4.contracts import (
    AuctionMarketResult,
    AuctionState,
    DirectionalEvent,
    LiquidityResult,
    LiquidityState,
    MarketSnapshot,
    MarketStructureResult,
    MarketTrend,
    MultiTimeframeContext,
    OfficialTimeframe,
    RegimeResult,
    RegimeState,
    SwingPoint,
    TimeframeFeatureSummary,
    V4Bar,
    VolatilityResult,
    VolumeProvenance,
)

_ANNUAL_PERIODS = {
    OfficialTimeframe.M15: 252 * 24 * 4,
    OfficialTimeframe.H1: 252 * 24,
    OfficialTimeframe.H4: 252 * 6,
    OfficialTimeframe.D1: 252,
}


@dataclass(frozen=True, slots=True)
class PivotParameters:
    left_bars: int = 2
    right_bars: int = 2
    retained_swings: int = 8
    retest_tolerance_bps: float = 8.0

    def __post_init__(self) -> None:
        if not 1 <= self.left_bars <= 10 or not 1 <= self.right_bars <= 10:
            raise ValueError("pivot windows must be between 1 and 10 bars")
        if not 2 <= self.retained_swings <= 50:
            raise ValueError("retained_swings must be between 2 and 50")
        if not 0 < self.retest_tolerance_bps <= 100:
            raise ValueError("retest_tolerance_bps must be between 0 and 100")


class MarketStructureEngine:
    """Confirmed fractal pivots, BOS/CHoCH, levels, breakout, and retest."""

    version = "structure-v1"

    def __init__(self, parameters: PivotParameters | None = None) -> None:
        self.parameters = parameters or PivotParameters()

    def calculate(self, bars: tuple[V4Bar, ...]) -> MarketStructureResult:
        minimum = self.parameters.left_bars + self.parameters.right_bars + 3
        if len(bars) < minimum:
            raise ValueError(f"market structure requires at least {minimum} bars")
        highs: list[SwingPoint] = []
        lows: list[SwingPoint] = []
        left, right = self.parameters.left_bars, self.parameters.right_bars
        for index in range(left, len(bars) - right):
            window = bars[index - left : index + right + 1]
            bar = bars[index]
            other_highs = [row.high for position, row in enumerate(window) if position != left]
            other_lows = [row.low for position, row in enumerate(window) if position != left]
            if bar.high > max(other_highs):
                highs.append(SwingPoint(timestamp=bar.timestamp, price=bar.high, kind="HIGH", bar_index=index))
            if bar.low < min(other_lows):
                lows.append(SwingPoint(timestamp=bar.timestamp, price=bar.low, kind="LOW", bar_index=index))

        trend = _structure_trend(highs, lows)
        resistance = highs[-1].price if highs else None
        support = lows[-1].price if lows else None
        latest = bars[-1]
        bos = DirectionalEvent.NONE
        if resistance is not None and latest.close > resistance:
            bos = DirectionalEvent.BULLISH
        elif support is not None and latest.close < support:
            bos = DirectionalEvent.BEARISH

        choch = DirectionalEvent.NONE
        if trend is MarketTrend.DOWN and bos is DirectionalEvent.BULLISH:
            choch = DirectionalEvent.BULLISH
        elif trend is MarketTrend.UP and bos is DirectionalEvent.BEARISH:
            choch = DirectionalEvent.BEARISH

        retest = False
        tolerance = latest.close * self.parameters.retest_tolerance_bps / 10_000
        if bos is DirectionalEvent.BULLISH and resistance is not None:
            retest = any(row.low <= resistance + tolerance and row.close >= resistance for row in bars[-3:])
        elif bos is DirectionalEvent.BEARISH and support is not None:
            retest = any(row.high >= support - tolerance and row.close <= support for row in bars[-3:])

        return MarketStructureResult(
            trend=trend,
            bos=bos,
            choch=choch,
            breakout=bos,
            retest_confirmed=retest,
            support=support,
            resistance=resistance,
            swing_highs=tuple(highs[-self.parameters.retained_swings :]),
            swing_lows=tuple(lows[-self.parameters.retained_swings :]),
        )


class VolatilityEngine:
    """ATR, realized/rolling volatility, percentiles, and volatility state."""

    version = "volatility-v1"

    def calculate(self, bars: tuple[V4Bar, ...], timeframe: OfficialTimeframe) -> VolatilityResult:
        if len(bars) < 60:
            raise ValueError("volatility features require at least 60 bars")
        true_ranges = _true_ranges(bars)
        atr_series = [fmean(true_ranges[index - 13 : index + 1]) for index in range(13, len(true_ranges))]
        atr14 = atr_series[-1]
        closes = [bar.close for bar in bars]
        returns = [math.log(current / previous) for previous, current in pairwise(closes)]
        annualizer = math.sqrt(_ANNUAL_PERIODS[timeframe])
        realized20 = pstdev(returns[-20:]) * annualizer if len(returns) >= 20 else 0.0
        rolling60 = pstdev(returns[-60:]) * annualizer if len(returns) >= 60 else pstdev(returns) * annualizer
        ranges = [bar.high - bar.low for bar in bars]
        range_percentile = _percentile_rank(ranges[-100:], ranges[-1])
        volatility_percentile = _percentile_rank(atr_series[-100:], atr14)
        short_vol = pstdev(returns[-10:]) if len(returns) >= 10 else 0.0
        long_vol = pstdev(returns[-40:]) if len(returns) >= 40 else pstdev(returns)
        ratio = short_vol / long_vol if long_vol > 0 else 0.0
        if volatility_percentile >= 0.95 or ratio >= 2.0:
            state = "EXTREME"
        elif volatility_percentile >= 0.75:
            state = "HIGH"
        elif volatility_percentile <= 0.20:
            state = "LOW"
        else:
            state = "NORMAL"
        return VolatilityResult(
            atr14=atr14,
            atr_percent=(atr14 / closes[-1]) * 100,
            realized_volatility_20=realized20,
            rolling_volatility_60=rolling60,
            range_percentile=range_percentile,
            volatility_percentile=volatility_percentile,
            short_long_volatility_ratio=ratio,
            state=state,
        )


class AuctionMarketEngine:
    """Broker-volume auction context; never a standalone signal generator."""

    version = "auction-v1"

    def calculate(
        self,
        bars: tuple[V4Bar, ...],
        timeframe: OfficialTimeframe,
        volume_provenance: VolumeProvenance,
    ) -> AuctionMarketResult:
        if len(bars) < 60:
            raise ValueError("auction features require at least 60 bars")
        selected = bars[-100:]
        lower = min(bar.low for bar in selected)
        upper = max(bar.high for bar in selected)
        bins = 24 if timeframe in {OfficialTimeframe.M15, OfficialTimeframe.H1} else 48
        width = (upper - lower) / bins
        if width <= 0:
            raise ValueError("auction range is zero")
        profile = [0.0 for _ in range(bins)]
        for bar in selected:
            typical = (bar.high + bar.low + bar.close) / 3
            index = min(bins - 1, max(0, int((typical - lower) / width)))
            profile[index] += bar.volume
        total = sum(profile)
        if total <= 0:
            raise ValueError("auction volume is unavailable")
        centers = [lower + (index + 0.5) * width for index in range(bins)]
        poc_index = max(range(bins), key=profile.__getitem__)
        ranked = sorted(range(bins), key=lambda index: profile[index], reverse=True)
        selected_indices: list[int] = []
        accumulated = 0.0
        for index in ranked:
            selected_indices.append(index)
            accumulated += profile[index]
            if accumulated >= total * 0.70:
                break
        value_low = min(centers[index] for index in selected_indices)
        value_high = max(centers[index] for index in selected_indices)
        latest = selected[-1]
        acceptance = all(value_low <= bar.close <= value_high for bar in selected[-3:])
        rejected_high = latest.high > value_high and latest.close < value_high
        rejected_low = latest.low < value_low and latest.close > value_low
        rejection = rejected_high or rejected_low
        previous = selected[-2]
        failed = (previous.close > value_high and latest.close < value_high) or (
            previous.close < value_low and latest.close > value_low
        )
        recent_volume = fmean(bar.volume for bar in selected[-5:])
        average_volume = fmean(bar.volume for bar in selected)
        if failed:
            state = AuctionState.FAILED_AUCTION
        elif rejection:
            state = AuctionState.REJECTION
        elif latest.close > value_high and recent_volume > average_volume:
            state = AuctionState.IMBALANCED_UP
        elif latest.close < value_low and recent_volume > average_volume:
            state = AuctionState.IMBALANCED_DOWN
        elif acceptance:
            state = AuctionState.ACCEPTANCE
        else:
            state = AuctionState.BALANCED

        midpoint = len(selected) // 2
        old_poc = _profile_poc(selected[:midpoint], bins)
        new_poc = _profile_poc(selected[midpoint:], bins)
        migration_tolerance = latest.close * 2 / 10_000
        if new_poc > old_poc + migration_tolerance:
            migration = "UP"
        elif new_poc < old_poc - migration_tolerance:
            migration = "DOWN"
        else:
            migration = "FLAT"
        return AuctionMarketResult(
            state=state,
            poc=centers[poc_index],
            value_area_high=value_high,
            value_area_low=value_low,
            value_area_fraction=min(1.0, accumulated / total),
            value_migration=migration,
            acceptance=acceptance,
            rejection=rejection,
            failed_auction=failed,
            volume_provenance=volume_provenance,
        )


class LiquidityEngine:
    """Level-1 MT5 liquidity evidence with explicit unavailable higher tiers."""

    version = "liquidity-v1"

    def calculate(self, snapshot: MarketSnapshot) -> LiquidityResult:
        m15 = snapshot.timeframes.get(OfficialTimeframe.M15, ())
        if not m15:
            activity = 0.0
        else:
            recent = m15[-20:]
            midpoint = (max(bar.high for bar in recent) + min(bar.low for bar in recent)) / 2
            activity = ((max(bar.high for bar in recent) - min(bar.low for bar in recent)) / midpoint) * 10_000
        frequency = None
        if snapshot.first_tick_at is not None and snapshot.last_tick is not None and snapshot.tick_count > 1:
            duration = (snapshot.last_tick.timestamp - snapshot.first_tick_at).total_seconds()
            if duration > 0:
                frequency = (snapshot.tick_count - 1) / duration
        if snapshot.tick_count == 0 or frequency is None:
            state = LiquidityState.UNAVAILABLE
        elif snapshot.spread_bps >= 30:
            state = LiquidityState.STRESSED
        elif snapshot.spread_bps >= 15 or frequency < 0.1 or snapshot.session == "OFF_HOURS":
            state = LiquidityState.THIN
        else:
            state = LiquidityState.ADEQUATE
        return LiquidityResult(
            state=state,
            spread_bps=snapshot.spread_bps,
            tick_frequency_hz=frequency,
            price_activity_bps=activity,
            session=snapshot.session,
            volume_provenance=snapshot.volume_provenance,
        )


class MarketRegimeEngine:
    """Transparent rule-based baseline regime classifier with probabilities."""

    version = "regime-v1"

    def calculate(
        self,
        bars: tuple[V4Bar, ...],
        structure: MarketStructureResult,
        volatility: VolatilityResult,
    ) -> RegimeResult:
        if len(bars) < 60:
            raise ValueError("regime features require at least 60 bars")
        adx = _adx14(bars)
        persistence = _trend_persistence(bars[-21:])
        scores = {state: 0.05 for state in RegimeState}

        if structure.trend is MarketTrend.UP:
            scores[RegimeState.TREND_UP] += 0.8 + adx / 100 + persistence * 0.5
        elif structure.trend is MarketTrend.DOWN:
            scores[RegimeState.TREND_DOWN] += 0.8 + adx / 100 + persistence * 0.5
        elif structure.trend is MarketTrend.RANGE:
            scores[RegimeState.RANGE] += 1.0 + max(0.0, (25 - adx) / 25)
        else:
            scores[RegimeState.UNCERTAIN] += 1.0

        if structure.breakout is not DirectionalEvent.NONE:
            scores[RegimeState.BREAKOUT] += 1.4 + persistence * 0.4
        if structure.choch is not DirectionalEvent.NONE or volatility.short_long_volatility_ratio >= 1.6:
            scores[RegimeState.TRANSITION] += 1.2
        if volatility.state in {"HIGH", "EXTREME"}:
            scores[RegimeState.HIGH_VOL] += 1.0 + volatility.volatility_percentile
        elif volatility.state == "LOW":
            scores[RegimeState.LOW_VOL] += 1.2
        if adx < 15 and structure.trend is not MarketTrend.RANGE:
            scores[RegimeState.UNCERTAIN] += 0.8

        total = sum(scores.values())
        probabilities = {state: value / total for state, value in scores.items()}
        primary = max(probabilities, key=probabilities.__getitem__)
        return RegimeResult(
            primary=primary,
            probabilities=probabilities,
            adx14=adx,
            trend_persistence=persistence,
        )


class MultiTimeframeEngine:
    """Describe deterministic alignment without producing a trade direction."""

    version = "mtf-v1"

    def calculate(self, summaries: dict[OfficialTimeframe, TimeframeFeatureSummary]) -> MultiTimeframeContext:
        if set(summaries) != set(OfficialTimeframe):
            raise ValueError("multi-timeframe context requires 15M, 1H, 4H, and 1D")
        bullish = tuple(
            timeframe for timeframe, summary in summaries.items() if summary.structure.trend is MarketTrend.UP
        )
        bearish = tuple(
            timeframe for timeframe, summary in summaries.items() if summary.structure.trend is MarketTrend.DOWN
        )
        uncertain = tuple(
            timeframe
            for timeframe, summary in summaries.items()
            if summary.structure.trend in {MarketTrend.RANGE, MarketTrend.UNCERTAIN}
        )
        if len(bullish) >= 3 and not bearish:
            alignment = "ALIGNED_BULLISH"
        elif len(bearish) >= 3 and not bullish:
            alignment = "ALIGNED_BEARISH"
        elif bullish and bearish:
            higher_bullish = any(tf in bullish for tf in (OfficialTimeframe.H4, OfficialTimeframe.D1))
            higher_bearish = any(tf in bearish for tf in (OfficialTimeframe.H4, OfficialTimeframe.D1))
            alignment = "CONTRADICTORY" if higher_bullish != higher_bearish or (higher_bullish and higher_bearish) else "MIXED"
        elif bullish or bearish:
            alignment = "MIXED"
        else:
            alignment = "UNCERTAIN"
        return MultiTimeframeContext(
            alignment=alignment,
            bullish_timeframes=bullish,
            bearish_timeframes=bearish,
            uncertain_timeframes=uncertain,
        )


def _structure_trend(highs: list[SwingPoint], lows: list[SwingPoint]) -> MarketTrend:
    if len(highs) < 2 or len(lows) < 2:
        return MarketTrend.UNCERTAIN
    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price
    if higher_high and higher_low:
        return MarketTrend.UP
    if lower_high and lower_low:
        return MarketTrend.DOWN
    return MarketTrend.RANGE


def _profile_poc(bars: tuple[V4Bar, ...], bins: int) -> float:
    lower = min(bar.low for bar in bars)
    upper = max(bar.high for bar in bars)
    width = (upper - lower) / bins
    if width <= 0:
        return bars[-1].close
    profile = [0.0 for _ in range(bins)]
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3
        index = min(bins - 1, max(0, int((typical - lower) / width)))
        profile[index] += bar.volume
    index = max(range(bins), key=profile.__getitem__)
    return lower + (index + 0.5) * width


def _true_ranges(bars: tuple[V4Bar, ...]) -> list[float]:
    result = [bars[0].high - bars[0].low]
    for previous, current in pairwise(bars):
        result.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    return result


def _percentile_rank(values: list[float], current: float) -> float:
    if not values:
        return 0.0
    return sum(value <= current for value in values) / len(values)


def _trend_persistence(bars: tuple[V4Bar, ...]) -> float:
    changes = [current.close - previous.close for previous, current in pairwise(bars)]
    if not changes:
        return 0.0
    net = bars[-1].close - bars[0].close
    if net == 0:
        return 0.0
    direction = 1 if net > 0 else -1
    return sum((change > 0) == (direction > 0) for change in changes if change != 0) / max(
        1, sum(change != 0 for change in changes)
    )


def _adx14(bars: tuple[V4Bar, ...]) -> float:
    period = 14
    true_ranges = _true_ranges(bars)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for previous, current in pairwise(bars):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    dx_values: list[float] = []
    for end in range(period, len(bars)):
        start = end - period + 1
        atr = sum(true_ranges[start : end + 1])
        if atr <= 0:
            continue
        plus_di = 100 * sum(plus_dm[start : end + 1]) / atr
        minus_di = 100 * sum(minus_dm[start : end + 1]) / atr
        denominator = plus_di + minus_di
        dx_values.append(0.0 if denominator == 0 else 100 * abs(plus_di - minus_di) / denominator)
    return max(0.0, min(100.0, fmean(dx_values[-period:]) if dx_values else 0.0))


__all__ = [
    "AuctionMarketEngine",
    "LiquidityEngine",
    "MarketRegimeEngine",
    "MarketStructureEngine",
    "MultiTimeframeEngine",
    "PivotParameters",
    "VolatilityEngine",
]
