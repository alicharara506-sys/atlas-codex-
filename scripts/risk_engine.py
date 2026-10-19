#!/usr/bin/env python3
"""Account-independent volatility exposure and structural geometry for ATLAS v2."""

from __future__ import annotations

import math
import statistics
from typing import Any


def realized_volatility(closes: list[float], periods_per_year: int = 252) -> float | None:
    if len(closes) < 3 or any(value <= 0 for value in closes):
        return None
    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(periods_per_year)


def exposure_units(conviction: float, current_realized_vol: float | None, target_vol: float = 0.10) -> dict[str, Any]:
    if current_realized_vol is None or current_realized_vol <= 0:
        return {"units": None, "status": "UNAVAILABLE", "reason": "Realized volatility unavailable."}
    units = max(0.0, conviction) * target_vol / current_realized_vol
    if current_realized_vol > 0.30:
        units = min(units, 0.5)
    elif current_realized_vol < 0.05:
        units = min(1.0, units * 2)
    return {"units": min(1.0, units), "status": "AVAILABLE", "targetAnnualVol": target_vol, "currentAnnualVol": current_realized_vol}


def structural_levels(
    direction: str,
    *,
    entry: float,
    atr: float,
    value_area_low: float,
    value_area_high: float,
    swing_low: float | None,
    swing_high: float | None,
    absorption_low: float | None = None,
    absorption_high: float | None = None,
    low_volume_nodes: list[float] | None = None,
    high_volume_nodes: list[float] | None = None,
) -> dict[str, Any]:
    if atr <= 0:
        raise ValueError("atr must be positive")
    lvns = sorted(low_volume_nodes or [])
    hvns = sorted(high_volume_nodes or [])
    if "LONG" in direction:
        candidates = [value_area_low - 0.5 * atr]
        if absorption_low is not None:
            candidates.append(absorption_low - 0.3 * atr)
        if swing_low is not None:
            candidates.append(swing_low - 0.5 * atr)
        stop = min(candidates)
        stop = max(stop, entry - 2.5 * atr)
        risk = entry - stop
        directional_lvns = [value for value in lvns if value > entry]
        directional_hvns = [value for value in hvns if value > entry + 2 * risk]
        tp1 = directional_lvns[0] if directional_lvns else entry + 1.5 * risk
        tp2 = max(entry + 2 * risk, tp1)
        tp3 = directional_hvns[0] if directional_hvns else entry + 3 * risk
    elif "SHORT" in direction:
        candidates = [value_area_high + 0.5 * atr]
        if absorption_high is not None:
            candidates.append(absorption_high + 0.3 * atr)
        if swing_high is not None:
            candidates.append(swing_high + 0.5 * atr)
        stop = max(candidates)
        stop = min(stop, entry + 2.5 * atr)
        risk = stop - entry
        directional_lvns = [value for value in reversed(lvns) if value < entry]
        directional_hvns = [value for value in reversed(hvns) if value < entry - 2 * risk]
        tp1 = directional_lvns[0] if directional_lvns else entry - 1.5 * risk
        tp2 = min(entry - 2 * risk, tp1)
        tp3 = directional_hvns[0] if directional_hvns else entry - 3 * risk
    else:
        return {"entry": None, "stopLoss": None, "takeProfit": [], "riskReward": [], "valid": False, "reason": "NO_TRADE has no geometry."}
    targets = [tp1, tp2, tp3]
    reward_risk = [abs(target - entry) / risk for target in targets]
    return {"entry": entry, "stopLoss": stop, "takeProfit": targets, "riskReward": reward_risk, "slAtrMultiple": abs(entry - stop) / atr, "valid": risk > 0 and reward_risk[0] >= 1.5}


def liquidity_filter(
    *,
    current_spread: float | None,
    average_spread: float | None,
    atr_percentile: float | None,
    timestamp_utc: int,
    tier1_cooldown: bool,
) -> dict[str, Any]:
    reasons = []
    if current_spread is not None and average_spread is not None and average_spread > 0 and current_spread > 2.5 * average_spread:
        reasons.append("Spread exceeds 2.5 times its observed average.")
    if atr_percentile is not None and atr_percentile < 0.05:
        reasons.append("ATR is below the fifth percentile.")
    from datetime import datetime, timezone

    now = datetime.fromtimestamp(timestamp_utc, timezone.utc)
    if now.weekday() == 4 and now.hour >= 20:
        reasons.append("Friday post-20:00 UTC weekend-gap window.")
    if tier1_cooldown:
        reasons.append("Tier-1 economic-event cooldown is active.")
    return {"status": "SUPPRESSED" if reasons else "ADEQUATE", "suppressed": bool(reasons), "reasons": reasons}


def pearson(first: list[float], second: list[float]) -> float | None:
    count = min(len(first), len(second))
    if count < 3:
        return None
    x, y = first[-count:], second[-count:]
    mean_x, mean_y = sum(x) / count, sum(y) / count
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denominator = math.sqrt(sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y))
    return numerator / denominator if denominator else None


def correlation_guard(candidate: dict[str, Any], active: list[dict[str, Any]], threshold: float = 0.75) -> dict[str, Any]:
    warnings = []
    adjusted = float(candidate.get("conviction", 0))
    for row in active:
        correlation = row.get("correlation")
        same_direction = row.get("direction") == candidate.get("direction")
        if isinstance(correlation, (int, float)) and same_direction and abs(float(correlation)) > threshold:
            warnings.append({"instrument": row.get("instrument"), "correlation": float(correlation), "code": "CLUSTER_RISK_WARNING"})
    if len(warnings) >= 2:
        adjusted *= 0.5
    return {"clusterRisk": "HIGH" if len(warnings) >= 2 else "MEDIUM" if warnings else "LOW", "adjustedConviction": adjusted, "warnings": warnings}


def scenario_stress(direction: str, *, entry: float, stop: float, atr: float, value_area_low: float, value_area_high: float, macro_adverse: bool = False) -> dict[str, Any]:
    distance = abs(entry - stop)
    adverse_pass = distance <= 2.5 * atr
    vol_spike_pass = stop < value_area_low if "LONG" in direction else stop > value_area_high if "SHORT" in direction else False
    correlation_pass = not macro_adverse
    passed = adverse_pass and vol_spike_pass
    return {
        "stressPassed": passed,
        "confidenceAdjustment": -0.20 if not passed else -0.10 if not correlation_pass else 0.0,
        "scenarios": [
            {"name": "ADVERSE_MOVE", "passed": adverse_pass, "expectedDrawdownAtr": distance / atr if atr else None},
            {"name": "VOLATILITY_SPIKE", "passed": vol_spike_pass},
            {"name": "CORRELATION_BREAKDOWN", "passed": correlation_pass, "advisory": True},
        ],
    }


__all__ = ["correlation_guard", "exposure_units", "liquidity_filter", "pearson", "realized_volatility", "scenario_stress", "structural_levels"]
