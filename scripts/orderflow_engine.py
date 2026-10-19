#!/usr/bin/env python3
"""Deterministic order-flow analytics for ATLAS Quant.

The MT5 forex/CFD feed is decentralized and does not provide a consolidated
exchange tape.  Consequently, aggressor side, footprint, delta, and liquidity
outputs in this module are explicitly broker-feed approximations.  They must
never be presented as verified Level-2 order-book data.

The module has no network access and no execution capability.  It accepts
closed OHLCV bars plus chronological ticks and returns JSON-serializable
research features.
"""

from __future__ import annotations

import bisect
import math
import statistics
from typing import Any, Iterable

METHOD = "TICK_RULE_APPROXIMATION"


class OrderFlowValidationError(ValueError):
    """Raised when an order-flow payload violates a numeric invariant."""


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise OrderFlowValidationError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OrderFlowValidationError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise OrderFlowValidationError(f"{name} must be a finite number")
    return result


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def _normalise_bars(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    bars: list[dict[str, float]] = []
    for index, row in enumerate(rows):
        bar = {
            "time": _number(row.get("time"), f"bars[{index}].time"),
            "open": _number(row.get("open"), f"bars[{index}].open"),
            "high": _number(row.get("high"), f"bars[{index}].high"),
            "low": _number(row.get("low"), f"bars[{index}].low"),
            "close": _number(row.get("close"), f"bars[{index}].close"),
            "volume": max(0.0, _number(row.get("volume", 0), f"bars[{index}].volume")),
        }
        if min(bar["open"], bar["close"]) < bar["low"] or max(bar["open"], bar["close"]) > bar["high"]:
            raise OrderFlowValidationError(f"bars[{index}] has invalid OHLC geometry")
        bars.append(bar)
    bars.sort(key=lambda bar: bar["time"])
    if any(bars[index]["time"] <= bars[index - 1]["time"] for index in range(1, len(bars))):
        raise OrderFlowValidationError("bars must have unique timestamps")
    return bars


def _normalise_ticks(rows: list[dict[str, Any]]) -> list[dict[str, float | None]]:
    ticks: list[dict[str, float | None]] = []
    for index, row in enumerate(rows):
        bid = _number(row.get("bid"), f"ticks[{index}].bid")
        ask = _number(row.get("ask"), f"ticks[{index}].ask")
        if ask < bid:
            raise OrderFlowValidationError(f"ticks[{index}] ask is below bid")
        last_raw = row.get("last")
        last = _number(last_raw, f"ticks[{index}].last") if last_raw is not None else None
        ticks.append(
            {
                "time": _number(row.get("time"), f"ticks[{index}].time"),
                "bid": bid,
                "ask": ask,
                "last": last,
                "volume": max(0.0, _number(row.get("volume", 0), f"ticks[{index}].volume")),
            }
        )
    ticks.sort(key=lambda tick: float(tick["time"]))
    return ticks


def _tick_price(tick: dict[str, float | None]) -> float:
    last = tick["last"]
    return float(last) if last is not None and last > 0 else (float(tick["bid"]) + float(tick["ask"])) / 2


def classify_ticks(ticks: list[dict[str, Any]]) -> list[int]:
    """Classify ticks with the uptick/downtick rule and zero-tick carry."""

    normalised = _normalise_ticks(ticks)
    signs: list[int] = []
    previous_price: float | None = None
    previous_sign = 0
    for tick in normalised:
        price = _tick_price(tick)
        if previous_price is None:
            sign = 0
        elif price > previous_price:
            sign = previous_sign = 1
        elif price < previous_price:
            sign = previous_sign = -1
        else:
            sign = previous_sign
        signs.append(sign)
        previous_price = price
    return signs


def _volume_basis(ticks: list[dict[str, float | None]]) -> str:
    return "REPORTED_VOLUME" if sum(float(tick["volume"]) for tick in ticks) > 0 else "TICK_COUNT_PROXY"


def _tick_weight(tick: dict[str, float | None], basis: str) -> float:
    return float(tick["volume"]) if basis == "REPORTED_VOLUME" else 1.0


def anchored_vwap(bars: list[dict[str, Any]], anchor_time: float | None = None) -> dict[str, Any]:
    clean = _normalise_bars(bars)
    if not clean:
        return {"value": None, "anchorTime": None, "volumeBasis": "UNAVAILABLE"}
    selected = [bar for bar in clean if anchor_time is None or bar["time"] >= anchor_time]
    if not selected:
        raise OrderFlowValidationError("anchor_time is later than every supplied bar")
    has_volume = sum(bar["volume"] for bar in selected) > 0
    weights = [bar["volume"] if has_volume else 1.0 for bar in selected]
    value = sum(((bar["high"] + bar["low"] + bar["close"]) / 3) * weight for bar, weight in zip(selected, weights)) / sum(weights)
    return {
        "value": value,
        "anchorTime": selected[0]["time"],
        "volumeBasis": "BROKER_TICK_VOLUME" if has_volume else "EQUAL_BAR_WEIGHT",
    }


def volume_profile(bars: list[dict[str, Any]], bins: int = 24, value_area: float = 0.70) -> dict[str, Any]:
    clean = _normalise_bars(bars)
    if not clean:
        return {"method": "BAR_TYPICAL_PRICE_PROXY", "bins": [], "poc": None, "valueAreaLow": None, "valueAreaHigh": None}
    bins = min(100, max(5, int(bins)))
    low = min(bar["low"] for bar in clean)
    high = max(bar["high"] for bar in clean)
    width = (high - low) / bins if high > low else max(abs(high) * 1e-9, 1e-9)
    has_volume = sum(bar["volume"] for bar in clean) > 0
    bucket_volume = [0.0] * bins
    for bar in clean:
        price = (bar["high"] + bar["low"] + bar["close"]) / 3
        bucket = min(bins - 1, max(0, int((price - low) / width)))
        bucket_volume[bucket] += bar["volume"] if has_volume else 1.0
    levels = [
        {"low": low + index * width, "high": low + (index + 1) * width, "price": low + (index + 0.5) * width, "volume": volume}
        for index, volume in enumerate(bucket_volume)
    ]
    poc_index = max(range(bins), key=bucket_volume.__getitem__)
    total = sum(bucket_volume)
    # Build a contiguous value area outward from POC. At each step include
    # the heavier adjacent price bin; this avoids a disconnected set of
    # high-volume bins being mislabeled as one auction value area.
    selected: list[int] = [poc_index]
    running = bucket_volume[poc_index]
    left = poc_index - 1
    right = poc_index + 1
    while running < total * value_area and (left >= 0 or right < bins):
        left_volume = bucket_volume[left] if left >= 0 else -1.0
        right_volume = bucket_volume[right] if right < bins else -1.0
        if right_volume > left_volume:
            selected.append(right)
            running += bucket_volume[right]
            right += 1
        else:
            selected.append(left)
            running += bucket_volume[left]
            left -= 1
    nonzero = [volume for volume in bucket_volume if volume > 0]
    low_node_threshold = _percentile(nonzero, 0.25)
    return {
        "method": "BAR_TYPICAL_PRICE_PROXY",
        "volumeBasis": "BROKER_TICK_VOLUME" if has_volume else "EQUAL_BAR_WEIGHT",
        "poc": levels[poc_index]["price"],
        "valueAreaLow": levels[min(selected)]["low"],
        "valueAreaHigh": levels[max(selected)]["high"],
        "totalVolume": total,
        "bins": levels,
        "highVolumeNodes": [level["price"] for level in levels if level["volume"] >= bucket_volume[poc_index] * 0.75 and level["volume"] > 0],
        "lowVolumeNodes": [level["price"] for level in levels if 0 < level["volume"] <= low_node_threshold],
    }


def _tick_analytics(bars: list[dict[str, float]], ticks: list[dict[str, float | None]], footprint_bins: int) -> dict[str, Any]:
    signs: list[int] = []
    previous_price: float | None = None
    previous_sign = 0
    for tick in ticks:
        price = _tick_price(tick)
        if previous_price is None:
            sign = 0
        elif price > previous_price:
            sign = previous_sign = 1
        elif price < previous_price:
            sign = previous_sign = -1
        else:
            sign = previous_sign
        signs.append(sign)
        previous_price = price

    basis = _volume_basis(ticks)
    buy = sum(_tick_weight(tick, basis) for tick, sign in zip(ticks, signs) if sign > 0)
    sell = sum(_tick_weight(tick, basis) for tick, sign in zip(ticks, signs) if sign < 0)
    delta = buy - sell
    classified = buy + sell
    ratio = delta / classified if classified else 0.0

    bar_times = [bar["time"] for bar in bars]
    intervals = [bar_times[index] - bar_times[index - 1] for index in range(1, len(bar_times))]
    final_bar_end = bar_times[-1] + (_percentile(intervals, 0.5) if intervals else 0)
    by_bar = [{"barTime": bar["time"], "buyVolume": 0.0, "sellVolume": 0.0, "delta": 0.0, "cumulativeDelta": 0.0, "tickCount": 0, "absorption": False, "absorptionSide": None} for bar in bars]
    for tick, sign in zip(ticks, signs):
        index = bisect.bisect_right(bar_times, float(tick["time"])) - 1
        if index < 0 or index >= len(by_bar) or (final_bar_end > bar_times[-1] and float(tick["time"]) >= final_bar_end):
            continue
        weight = _tick_weight(tick, basis)
        if sign > 0:
            by_bar[index]["buyVolume"] += weight
        elif sign < 0:
            by_bar[index]["sellVolume"] += weight
        by_bar[index]["tickCount"] += 1
    cumulative = 0.0
    volumes = []
    ranges = []
    for index, item in enumerate(by_bar):
        item["delta"] = item["buyVolume"] - item["sellVolume"]
        cumulative += item["delta"]
        item["cumulativeDelta"] = cumulative
        volumes.append(item["buyVolume"] + item["sellVolume"])
        ranges.append(bars[index]["high"] - bars[index]["low"])
    volume_threshold = _percentile([value for value in volumes if value > 0], 0.75)
    range_threshold = _percentile(ranges, 0.35)
    absorption_events = []
    for index, item in enumerate(by_bar):
        bar_range = ranges[index]
        if volumes[index] >= volume_threshold > 0 and bar_range <= range_threshold:
            close_location = (bars[index]["close"] - bars[index]["low"]) / bar_range if bar_range > 0 else 0.5
            item["absorption"] = True
            if item["delta"] > 0 and close_location < 0.5:
                item["absorptionSide"] = "SELLER_ABSORPTION"
            elif item["delta"] < 0 and close_location > 0.5:
                item["absorptionSide"] = "BUYER_ABSORPTION"
            else:
                item["absorptionSide"] = "UNRESOLVED"
            absorption_events.append({"barTime": item["barTime"], "side": item["absorptionSide"], "delta": item["delta"]})

    prices = [_tick_price(tick) for tick in ticks]
    footprint: list[dict[str, float]] = []
    if prices:
        floor = min(prices)
        ceiling = max(prices)
        count = min(80, max(5, int(footprint_bins)))
        width = (ceiling - floor) / count if ceiling > floor else max(abs(ceiling) * 1e-9, 1e-9)
        levels = [{"price": floor + (index + 0.5) * width, "buyVolume": 0.0, "sellVolume": 0.0, "delta": 0.0} for index in range(count)]
        for tick, sign in zip(ticks, signs):
            index = min(count - 1, max(0, int((_tick_price(tick) - floor) / width)))
            weight = _tick_weight(tick, basis)
            if sign > 0:
                levels[index]["buyVolume"] += weight
            elif sign < 0:
                levels[index]["sellVolume"] += weight
        for level in levels:
            level["delta"] = level["buyVolume"] - level["sellVolume"]
        footprint = [level for level in levels if level["buyVolume"] + level["sellVolume"] > 0]

    pressure = "NEUTRAL"
    if ratio >= 0.60:
        pressure = "STRONG_BUY_PRESSURE"
    elif ratio >= 0.20:
        pressure = "BUY_PRESSURE"
    elif ratio <= -0.60:
        pressure = "STRONG_SELL_PRESSURE"
    elif ratio <= -0.20:
        pressure = "SELL_PRESSURE"

    return {
        "volumeBasis": basis,
        "buyVolume": buy,
        "sellVolume": sell,
        "delta": delta,
        "cumulativeDelta": cumulative,
        "imbalanceRatio": ratio,
        "pressure": pressure,
        "barDelta": by_bar,
        "footprint": footprint,
        "absorption": absorption_events,
    }


def _swing_liquidity(bars: list[dict[str, float]], profile: dict[str, Any], window: int = 2) -> dict[str, Any]:
    highs: list[float] = []
    lows: list[float] = []
    for index in range(window, len(bars) - window):
        neighborhood = bars[index - window : index + window + 1]
        if bars[index]["high"] == max(bar["high"] for bar in neighborhood):
            highs.append(bars[index]["high"])
        if bars[index]["low"] == min(bar["low"] for bar in neighborhood):
            lows.append(bars[index]["low"])
    return {
        "method": "SWING_AND_VOLUME_NODE_PROXY",
        "swingHighs": highs[-8:],
        "swingLows": lows[-8:],
        "highVolumeNodes": profile.get("highVolumeNodes", []),
        "lowVolumeNodes": profile.get("lowVolumeNodes", []),
    }


def analyze_orderflow(
    symbol: str,
    bars: list[dict[str, Any]],
    ticks: list[dict[str, Any]],
    *,
    anchor_time: float | None = None,
    profile_bins: int = 24,
    footprint_bins: int = 30,
) -> dict[str, Any]:
    """Return the full deterministic ATLAS order-flow research packet."""

    clean_bars = _normalise_bars(bars)
    clean_ticks = _normalise_ticks(ticks)
    if len(clean_bars) < 5:
        raise OrderFlowValidationError("at least five closed bars are required")
    avwap = anchored_vwap(clean_bars, anchor_time)
    profile = volume_profile(clean_bars, profile_bins)
    tick_read = _tick_analytics(clean_bars, clean_ticks, footprint_bins)
    liquidity = _swing_liquidity(clean_bars, profile)
    last_price = clean_bars[-1]["close"]

    orderflow_component = tick_read["imbalanceRatio"] * 40
    avwap_gap = (last_price - avwap["value"]) / avwap["value"] if avwap["value"] else 0.0
    avwap_component = max(-20.0, min(20.0, avwap_gap * 2_000))
    profile_component = 15.0 if last_price > profile["poc"] else -15.0 if last_price < profile["poc"] else 0.0
    recent_cvd = tick_read["barDelta"][-5:]
    cvd_change = recent_cvd[-1]["cumulativeDelta"] - recent_cvd[0]["cumulativeDelta"] if len(recent_cvd) > 1 else 0.0
    recent_volume = sum(item["buyVolume"] + item["sellVolume"] for item in recent_cvd)
    cvd_component = max(-15.0, min(15.0, (cvd_change / recent_volume * 30) if recent_volume else 0.0))
    absorption_component = 0.0
    if tick_read["absorption"]:
        side = tick_read["absorption"][-1]["side"]
        absorption_component = 10.0 if side == "BUYER_ABSORPTION" else -10.0 if side == "SELLER_ABSORPTION" else 0.0
    score = max(-100.0, min(100.0, orderflow_component + avwap_component + profile_component + cvd_component + absorption_component))
    bias = "BULLISH" if score >= 20 else "BEARISH" if score <= -20 else "NEUTRAL"
    v2_features = build_orderflow_features(
        clean_bars,
        clean_ticks,
        timeframe="1H",
        macro_event_time=anchor_time,
    )

    return {
        "symbol": str(symbol).upper().replace("/", ""),
        "method": METHOD,
        "centralizedTape": False,
        "lastPrice": last_price,
        "anchoredVwap": avwap,
        "volumeProfile": profile,
        "delta": {key: tick_read[key] for key in ("volumeBasis", "buyVolume", "sellVolume", "delta", "cumulativeDelta", "imbalanceRatio", "pressure")},
        "barDelta": tick_read["barDelta"],
        "footprint": {"method": METHOD, "levels": tick_read["footprint"]},
        "absorption": tick_read["absorption"],
        "liquidity": liquidity,
        "v2": v2_features,
        "confluence": {
            "score": score,
            "bias": bias,
            "components": {
                "orderFlow": orderflow_component,
                "anchoredVwap": avwap_component,
                "volumeProfile": profile_component,
                "cvdTrend": cvd_component,
                "absorption": absorption_component,
            },
            "evidence": [
                f"Tick-rule pressure is {tick_read['pressure']} using {tick_read['volumeBasis'].lower().replace('_', ' ')}.",
                f"Last close is {'above' if last_price >= avwap['value'] else 'below'} anchored VWAP.",
                f"Volume-profile POC proxy is {profile['poc']:.8g}.",
                "Forex/CFD output is a broker-feed approximation, not consolidated Level-2 order flow.",
            ],
        },
        "coverage": {"closedBars": len(clean_bars), "ticks": len(clean_ticks)},
        "dataQuality": {
            "orderFlowVerified": False,
            "volumeBasis": tick_read["volumeBasis"],
            "warnings": [
                "OTC forex/CFD data has no consolidated tape.",
                "Footprint and aggressor side use the tick rule.",
            ],
        },
    }


class AVWAP:
    """Anchored VWAP: sum(V_i * TP_i) / sum(V_i), TP=(H+L+C)/3."""

    @staticmethod
    def select_anchor(
        bars: list[dict[str, Any]],
        *,
        timeframe: str = "1H",
        macro_event_time: float | None = None,
    ) -> dict[str, Any]:
        clean = _normalise_bars(bars)
        if not clean:
            return {"type": "unavailable", "time": None, "price": None}
        if macro_event_time is not None and clean[0]["time"] <= macro_event_time <= clean[-1]["time"]:
            closest = min(clean, key=lambda bar: abs(bar["time"] - macro_event_time))
            return {"type": "macro_event", "time": closest["time"], "price": closest["close"]}
        recent = clean[-20:]
        average_volume = sum(bar["volume"] for bar in recent) / len(recent)
        candidates: list[tuple[float, dict[str, Any]]] = []
        for index in range(2, len(recent) - 2):
            bar = recent[index]
            neighbors = recent[index - 2 : index + 3]
            high = bar["high"] == max(item["high"] for item in neighbors)
            low = bar["low"] == min(item["low"] for item in neighbors)
            volume_ratio = bar["volume"] / average_volume if average_volume > 0 else 1.0
            if volume_ratio >= 1.5 and (high or low):
                candidates.append(
                    (
                        bar["time"],
                        {
                            "type": "swing_high" if high else "swing_low",
                            "time": bar["time"],
                            "price": bar["high"] if high else bar["low"],
                            "volumeRatio": volume_ratio,
                        },
                    )
                )
        if candidates:
            return max(candidates, key=lambda item: item[0])[1]
        seconds = {"15M": 900, "1H": 3_600, "4H": 14_400, "D1": 86_400}.get(timeframe.upper(), 3_600)
        session_seconds = 86_400 if seconds < 86_400 else seconds
        session_start = math.floor(clean[-1]["time"] / session_seconds) * session_seconds
        session = [bar for bar in clean if bar["time"] >= session_start]
        anchor = session[0] if session else clean[-min(len(clean), 20)]
        return {"type": "session_open", "time": anchor["time"], "price": anchor["open"]}

    @staticmethod
    def calculate(bars: list[dict[str, Any]], anchor_time: float | None = None) -> dict[str, Any]:
        return anchored_vwap(bars, anchor_time)


class VolumeProfile:
    """Typical-price OHLCV proxy profile with POC and 70% value area."""

    @staticmethod
    def calculate(bars: list[dict[str, Any]], bins: int = 24) -> dict[str, Any]:
        return volume_profile(bars, bins=bins, value_area=0.70)


class VolumeDelta:
    """OHLCV proxy delta: V*(C-O)/(H-L), zero when H equals L."""

    @staticmethod
    def calculate(bars: list[dict[str, Any]], anomaly_window: int = 20) -> list[dict[str, Any]]:
        clean = _normalise_bars(bars)
        raw: list[float] = []
        for bar in clean:
            spread = bar["high"] - bar["low"]
            raw.append(bar["volume"] * (bar["close"] - bar["open"]) / spread if spread > 0 else 0.0)
        rows: list[dict[str, Any]] = []
        for index, (bar, delta) in enumerate(zip(clean, raw)):
            history = raw[max(0, index - anomaly_window) : index]
            std = statistics.pstdev(history) if len(history) >= 2 else 0.0
            anomalous = std > 0 and abs(delta) > 2 * std
            candle_sign = 1 if bar["close"] > bar["open"] else -1 if bar["close"] < bar["open"] else 0
            delta_sign = 1 if delta > 0 else -1 if delta < 0 else 0
            rows.append(
                {
                    "time": bar["time"],
                    "delta": delta,
                    "anomalous": anomalous,
                    "contradiction": candle_sign != 0 and delta_sign != 0 and candle_sign != delta_sign,
                }
            )
        return rows


class CVD:
    """Cumulative volume-delta series and bounded swing-divergence read."""

    @staticmethod
    def calculate(delta_rows: list[dict[str, Any]]) -> list[dict[str, float]]:
        total = 0.0
        result = []
        for row in delta_rows:
            total += float(row["delta"])
            result.append({"time": float(row["time"]), "value": total})
        return result

    @staticmethod
    def divergence(bars: list[dict[str, Any]], cvd: list[dict[str, float]], lookback: int = 20) -> str:
        clean = _normalise_bars(bars)
        count = min(len(clean), len(cvd), lookback)
        if count < 6:
            return "NONE"
        price = clean[-count:]
        flow = cvd[-count:]
        split = count // 2
        previous_high = max(bar["high"] for bar in price[:split])
        recent_high = max(bar["high"] for bar in price[split:])
        previous_low = min(bar["low"] for bar in price[:split])
        recent_low = min(bar["low"] for bar in price[split:])
        previous_cvd_high = max(row["value"] for row in flow[:split])
        recent_cvd_high = max(row["value"] for row in flow[split:])
        previous_cvd_low = min(row["value"] for row in flow[:split])
        recent_cvd_low = min(row["value"] for row in flow[split:])
        if recent_high > previous_high and recent_cvd_high < previous_cvd_high:
            return "BEARISH_DIVERGENCE_DISTRIBUTION"
        if recent_low < previous_low and recent_cvd_low > previous_cvd_low:
            return "BULLISH_DIVERGENCE_ACCUMULATION"
        if recent_high > previous_high and recent_cvd_high >= previous_cvd_high:
            return "BULLISH_TREND_CONFIRMED"
        if recent_low < previous_low and recent_cvd_low <= previous_cvd_low:
            return "BEARISH_TREND_CONFIRMED"
        return "NONE"


class FootprintProxy:
    """Bar-level pressure and microstructure patterns from closed OHLCV bars."""

    @staticmethod
    def calculate(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clean = _normalise_bars(bars)
        result = []
        for index, bar in enumerate(clean):
            spread = bar["high"] - bar["low"]
            buy = bar["volume"] * (bar["close"] - bar["low"]) / spread if spread > 0 else 0.0
            sell = bar["volume"] * (bar["high"] - bar["close"]) / spread if spread > 0 else 0.0
            prior = clean[max(0, index - 20) : index]
            average = sum(item["volume"] for item in prior) / len(prior) if prior else bar["volume"]
            ratio = bar["volume"] / average if average > 0 else 1.0
            body = abs(bar["close"] - bar["open"])
            upper_wick = bar["high"] - max(bar["open"], bar["close"])
            lower_wick = min(bar["open"], bar["close"]) - bar["low"]
            patterns: list[str] = []
            if ratio > 1.5 and spread > 0 and body < 0.3 * spread:
                patterns.append("ABSORPTION")
            if ratio > 2 and spread > 0 and max(upper_wick, lower_wick) > 0.7 * spread:
                patterns.append("EXHAUSTION")
            if index > 0 and ratio > 1.5:
                previous = clean[index - 1]
                if bar["high"] > previous["high"] and bar["close"] < previous["high"]:
                    patterns.append("SWEEP_HIGH")
                if bar["low"] < previous["low"] and bar["close"] > previous["low"]:
                    patterns.append("SWEEP_LOW")
                if bar["close"] > previous["high"] and ratio > 1.3 and buy > 0.6 * bar["volume"]:
                    patterns.append("INITIATIVE_BUYING")
                if bar["close"] < previous["low"] and ratio > 1.3 and sell > 0.6 * bar["volume"]:
                    patterns.append("INITIATIVE_SELLING")
            result.append({"time": bar["time"], "buyPressure": buy, "sellPressure": sell, "patterns": patterns, "volumeRatio": ratio})
        return result


class OFII:
    """Order-flow imbalance index: buy/(buy+sell), bounded to [0, 1]."""

    @staticmethod
    def calculate(footprint_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in footprint_rows:
            total = float(row["buyPressure"]) + float(row["sellPressure"])
            value = float(row["buyPressure"]) / total if total > 0 else 0.5
            classification = "AGGRESSIVE_BUYING" if value > 0.70 else "AGGRESSIVE_SELLING" if value < 0.30 else "BALANCED"
            result.append({"time": row["time"], "value": value, "classification": classification})
        return result


class MarketStructure:
    """Deterministic swing, BOS, and first-attempt CHoCH approximation."""

    @staticmethod
    def calculate(bars: list[dict[str, Any]], swing_window: int = 2) -> dict[str, Any]:
        clean = _normalise_bars(bars)
        swing_highs = []
        swing_lows = []
        for index in range(swing_window, len(clean) - swing_window):
            group = clean[index - swing_window : index + swing_window + 1]
            if clean[index]["high"] == max(bar["high"] for bar in group):
                swing_highs.append({"time": clean[index]["time"], "price": clean[index]["high"]})
            if clean[index]["low"] == min(bar["low"] for bar in group):
                swing_lows.append({"time": clean[index]["time"], "price": clean[index]["low"]})
        close = clean[-1]["close"] if clean else 0.0
        prior_high = swing_highs[-1]["price"] if swing_highs else None
        prior_low = swing_lows[-1]["price"] if swing_lows else None
        bos = "BULLISH_BOS" if prior_high is not None and close > prior_high else "BEARISH_BOS" if prior_low is not None and close < prior_low else "NONE"
        prior_trend = "BULLISH" if len(swing_lows) >= 2 and swing_lows[-1]["price"] > swing_lows[-2]["price"] else "BEARISH" if len(swing_highs) >= 2 and swing_highs[-1]["price"] < swing_highs[-2]["price"] else "MIXED"
        choch = "BULLISH_CHOCH" if prior_trend == "BEARISH" and bos == "BULLISH_BOS" else "BEARISH_CHOCH" if prior_trend == "BULLISH" and bos == "BEARISH_BOS" else "NONE"
        return {"bos": bos, "choch": choch, "priorTrend": prior_trend, "swingHighs": swing_highs[-10:], "swingLows": swing_lows[-10:]}


def classify_micro_regime(
    bars: list[dict[str, Any]],
    profile: dict[str, Any],
    cvd: list[dict[str, float]],
    ofii: float,
    atr_percentile: float | None = None,
    bb_width_percentile: float | None = None,
) -> str:
    clean = _normalise_bars(bars)
    if not clean or profile.get("valueAreaHigh") is None:
        return "UNAVAILABLE"
    close = clean[-1]["close"]
    recent_cvd_rising = len(cvd) >= 2 and cvd[-1]["value"] > cvd[max(0, len(cvd) - 6)]["value"]
    recent_volume = clean[-1]["volume"]
    average = sum(bar["volume"] for bar in clean[-20:]) / min(20, len(clean))
    if bb_width_percentile is not None and bb_width_percentile < 0.10:
        return "VOL_COMPRESSION"
    if profile["valueAreaLow"] <= close <= profile["valueAreaHigh"] and (atr_percentile is None or atr_percentile < 0.50):
        return "BALANCED"
    if close > profile["valueAreaHigh"] and recent_cvd_rising and ofii > 0.65:
        return "IMBALANCED_UP"
    if close < profile["valueAreaLow"] and not recent_cvd_rising and ofii < 0.35:
        return "IMBALANCED_DOWN"
    if (close > profile["valueAreaHigh"] or close < profile["valueAreaLow"]) and average > 0 and recent_volume > 1.3 * average:
        return "DISCOVERY"
    return "BALANCED"


def build_orderflow_features(
    bars: list[dict[str, Any]],
    ticks: list[dict[str, Any]] | None = None,
    *,
    timeframe: str = "1H",
    macro_event_time: float | None = None,
    atr_percentile: float | None = None,
    bb_width_percentile: float | None = None,
) -> dict[str, Any]:
    clean = _normalise_bars(bars)
    anchor = AVWAP.select_anchor(clean, timeframe=timeframe, macro_event_time=macro_event_time)
    avwap = AVWAP.calculate(clean, anchor["time"])
    profile = VolumeProfile.calculate(clean, bins=48 if timeframe.upper() == "4H" else 24)
    delta_rows = VolumeDelta.calculate(clean)
    cvd = CVD.calculate(delta_rows)
    footprint = FootprintProxy.calculate(clean)
    ofii_rows = OFII.calculate(footprint)
    structure = MarketStructure.calculate(clean)
    current_ofii = ofii_rows[-1]["value"] if ofii_rows else 0.5
    regime = classify_micro_regime(clean, profile, cvd, current_ofii, atr_percentile, bb_width_percentile)
    return {
        "avwap": {**avwap, "anchor": anchor},
        "volumeProfile": profile,
        "volumeDelta": {"method": "OHLCV_PROXY", "bars": delta_rows, "latest": delta_rows[-1] if delta_rows else None},
        "cvd": {"series": cvd, "latest": cvd[-1]["value"] if cvd else 0.0, "divergence": CVD.divergence(clean, cvd)},
        "footprintProxy": {"method": "OHLCV_PROXY", "bars": footprint, "latest": footprint[-1] if footprint else None},
        "ofii": {"series": ofii_rows, "latest": ofii_rows[-1] if ofii_rows else None},
        "marketStructure": structure,
        "microRegime": regime,
        "preferredOrderFlowSource": "BROKER_TICKS" if ticks else "OHLCV_PROXY",
    }


__all__ = [
    "METHOD",
    "OrderFlowValidationError",
    "analyze_orderflow",
    "anchored_vwap",
    "classify_ticks",
    "volume_profile",
    "AVWAP",
    "VolumeProfile",
    "VolumeDelta",
    "CVD",
    "FootprintProxy",
    "OFII",
    "MarketStructure",
    "build_orderflow_features",
    "classify_micro_regime",
]
