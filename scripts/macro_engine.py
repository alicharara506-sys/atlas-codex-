#!/usr/bin/env python3
"""Deterministic macro-regime and economic-event controls for ATLAS v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class MacroInputs:
    dxy_direction: str = "UNAVAILABLE"
    yield_10y_direction: str = "UNAVAILABLE"
    vix_level: float | None = None
    inflation_surprise: float | None = None
    employment_surprise: float | None = None


@dataclass(frozen=True)
class EconomicEvent:
    timestamp: int
    tier: str
    category: str
    title: str
    instruments: tuple[str, ...] = ()


class MacroRegimeEngine:
    """Classify the economic-cycle overlay without asking an LLM for math."""

    @staticmethod
    def classify(inputs: MacroInputs) -> dict[str, Any]:
        dxy = inputs.dxy_direction.upper()
        yields = inputs.yield_10y_direction.upper()
        vix = inputs.vix_level
        available = sum([dxy != "UNAVAILABLE", yields != "UNAVAILABLE", vix is not None])
        if available < 2:
            return {
                "regime": "UNAVAILABLE",
                "confidence": 0.0,
                "fxBias": [],
                "commodityBias": [],
                "inputs": asdict(inputs),
                "evidence": ["Insufficient DXY, 10Y-yield, and VIX evidence for a macro regime."],
            }
        if vix is not None and vix > 30 and dxy in {"UP", "STRONG_UP"} and yields in {"DOWN", "STRONG_DOWN"}:
            regime = "RECESSION"
            fx_bias = ["USD", "JPY", "CHF"]
            commodity_bias = ["GOLD", "SILVER"]
        elif vix is not None and 20 <= vix <= 30 and yields in {"DOWN", "STRONG_DOWN"}:
            regime = "SLOWDOWN"
            fx_bias = ["DEFENSIVE", "REDUCE_CARRY"]
            commodity_bias = ["GOLD", "DEFENSIVE"]
        elif vix is not None and vix < 20 and dxy in {"DOWN", "FLAT", "WEAK_DOWN"} and yields in {"UP", "STRONG_UP"}:
            regime = "EXPANSION"
            fx_bias = ["EUR", "GBP", "AUD"]
            commodity_bias = ["ENERGY", "INDUSTRIAL_METALS"]
        elif dxy in {"DOWN", "WEAK_DOWN"} and yields in {"UP", "FLAT"} and (vix is None or vix < 25):
            regime = "RECOVERY"
            fx_bias = ["AUD", "CAD", "RISK_FX"]
            commodity_bias = ["BROAD_COMMODITIES"]
        else:
            regime = "MIXED"
            fx_bias = []
            commodity_bias = []
        confidence = min(1.0, 0.35 + available * 0.2)
        return {
            "regime": regime,
            "confidence": confidence,
            "fxBias": fx_bias,
            "commodityBias": commodity_bias,
            "inputs": asdict(inputs),
            "evidence": [f"DXY {dxy}, 10Y yield {yields}, VIX {vix if vix is not None else 'unavailable'}."],
        }


def event_cooldown(
    events: list[EconomicEvent],
    *,
    now: int | None = None,
    instrument: str | None = None,
) -> dict[str, Any]:
    """Return the strictest active pre/post event suppression window."""

    current = int(datetime.now(timezone.utc).timestamp()) if now is None else int(now)
    active: list[dict[str, Any]] = []
    next_tier1_minutes: int | None = None
    for event in events:
        if instrument and event.instruments and instrument not in event.instruments:
            continue
        distance_minutes = (event.timestamp - current) / 60
        tier = event.tier.upper()
        category = event.category.upper()
        if tier == "T1":
            pre = post = 30 if category == "CENTRAL_BANK" else 15
            if distance_minutes >= 0:
                rounded = max(0, int(distance_minutes))
                next_tier1_minutes = rounded if next_tier1_minutes is None else min(next_tier1_minutes, rounded)
        elif tier == "T2":
            pre = post = 10 if category == "COMMODITY" else 5
        else:
            pre = post = 0
        if -post <= distance_minutes <= pre:
            active.append({**asdict(event), "minutesFromNow": distance_minutes, "preMinutes": pre, "postMinutes": post})
    return {
        "suppressNewSignals": any(item["tier"].upper() == "T1" for item in active),
        "caution": bool(active),
        "activeEvents": active,
        "tier1CountdownMin": next_tier1_minutes,
    }


def parse_calendar_rows(rows: list[dict[str, Any]]) -> list[EconomicEvent]:
    """Normalize externally obtained free-calendar rows without fabricating fields."""

    events = []
    for row in rows:
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            continue
        tier = str(row.get("tier", "T3")).upper()
        if tier not in {"T1", "T2", "T3"}:
            tier = "T3"
        instruments = row.get("instruments", [])
        events.append(
            EconomicEvent(
                timestamp=int(timestamp),
                tier=tier,
                category=str(row.get("category", "NONE")).upper(),
                title=str(row.get("title", "Untitled event"))[:300],
                instruments=tuple(str(item).upper() for item in instruments) if isinstance(instruments, list) else (),
            )
        )
    return sorted(events, key=lambda event: event.timestamp)


class EconomicCalendarClient:
    """Optional public-page loader with injectable parsing and visible failure.

    The Investing.com page is a streaming webpage, not a stable free API.
    Atlas stores the raw page only for a caller-provided parser. If no parser
    is configured, the result is explicitly UNAVAILABLE rather than guessed.
    """

    URL = "https://www.investing.com/economic-calendar/"

    def fetch(self, parser=None, timeout_seconds: float = 10.0) -> dict[str, Any]:
        if parser is None:
            return {"status": "UNAVAILABLE", "events": [], "error": "No verified economic-calendar HTML parser is configured.", "source": self.URL}
        try:
            request = Request(self.URL, headers={"User-Agent": "ATLAS-Quant/2.0 local research"})
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 -- fixed public HTTPS URL
                html = response.read(2_000_000).decode("utf-8", errors="replace")
            events = parse_calendar_rows(parser(html))
            return {"status": "LIVE" if events else "UNAVAILABLE", "events": [asdict(event) for event in events], "source": self.URL, "error": None if events else "Parser returned no normalized events."}
        except Exception as exc:
            return {"status": "UNAVAILABLE", "events": [], "error": str(exc), "source": self.URL}


__all__ = ["EconomicCalendarClient", "EconomicEvent", "MacroInputs", "MacroRegimeEngine", "event_cooldown", "parse_calendar_rows"]
