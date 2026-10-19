#!/usr/bin/env python3
"""Bounded, fully attributed ATLAS v2 confluence scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConfluenceInputs:
    avwap: float = 0.0
    volume_profile: float = 0.0
    cvd: float = 0.0
    market_structure: float = 0.0
    footprint: float = 0.0
    ofii: float = 0.0
    news_sentiment: float = 0.0
    macro_narrative: float = 0.0
    strategist_review: float = 0.0
    wyckoff_phase: float = 0.0
    smc_order_block: float = 0.0
    smc_fvg: float = 0.0
    smc_liquidity_sweep: float = 0.0


LIMITS = {
    "avwap": 2.0,
    "volume_profile": 2.0,
    "cvd": 2.0,
    "market_structure": 2.0,
    "footprint": 1.0,
    "ofii": 1.0,
    "news_sentiment": 2.0,
    "macro_narrative": 1.0,
    "strategist_review": 1.0,
    "wyckoff_phase": 1.0,
    "smc_order_block": 1.0,
    "smc_fvg": 1.0,
    "smc_liquidity_sweep": 2.0,
}


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, float(value)))


def signal_class(score: float, micro_regime: str) -> tuple[str, float, bool]:
    if score >= 8:
        return "STRONG_LONG", 1.0, False
    if score >= 5:
        return "MODERATE_LONG", 0.66, False
    if score >= 3:
        return ("WEAK_LONG", 0.33, False) if micro_regime == "DISCOVERY" else ("NO_TRADE", 0.0, True)
    if score <= -9:
        return "STRONG_SHORT", 1.0, False
    if score <= -6:
        return "MODERATE_SHORT", 0.66, False
    if score <= -4:
        return ("WEAK_SHORT", 0.33, False) if micro_regime == "DISCOVERY" else ("NO_TRADE", 0.0, True)
    return "NO_TRADE", 0.0, False


class ConfluenceScorer:
    """Apply factor caps, macro asymmetry, and deterministic suppressions."""

    def score(
        self,
        inputs: ConfluenceInputs,
        *,
        micro_regime: str,
        liquidity_suppressed: bool = False,
        news_suppressed: bool = False,
    ) -> dict[str, Any]:
        components = {name: _clamp(getattr(inputs, name), limit) for name, limit in LIMITS.items()}
        raw = sum(components.values())
        bounded = max(-10.0, min(10.0, raw))
        direction, conviction, weak_suppressed = signal_class(bounded, micro_regime)
        reasons = []
        if weak_suppressed:
            reasons.append("Weak signal suppressed outside DISCOVERY regime.")
        if liquidity_suppressed:
            direction, conviction = "NO_TRADE", 0.0
            reasons.append("Liquidity filter suppressed the signal.")
        if news_suppressed:
            direction, conviction = "NO_TRADE", 0.0
            reasons.append("Tier-1 economic-event cooldown suppressed new signals.")
        return {
            "score": bounded,
            "direction": direction,
            "conviction": conviction,
            "components": components,
            "suppressed": direction == "NO_TRADE" and bool(reasons),
            "suppressionReasons": reasons,
        }


__all__ = ["ConfluenceInputs", "ConfluenceScorer", "LIMITS", "signal_class"]
