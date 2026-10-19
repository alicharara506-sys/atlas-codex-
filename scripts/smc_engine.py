#!/usr/bin/env python3
"""Deterministic Smart Money Concepts labels for closed OHLCV bars.

The terms order block, fair-value gap, liquidity sweep, and breaker block are
structural research labels. They are derived from the supplied broker bars and
must not be presented as verified institutional orders or centralized depth.
"""

from __future__ import annotations

from typing import Any

from wyckoff_engine import _atr, _records


class SMCAnalyzer:
    """Calculate OB, FVG, sweep, and breaker evidence without inference."""

    def __init__(self, impulse_atr: float = 1.5, swing_span: int = 3, maximum_items: int = 24):
        self.impulse_atr = max(1.0, float(impulse_atr))
        self.swing_span = max(2, int(swing_span))
        self.maximum_items = max(5, int(maximum_items))

    @staticmethod
    def _zone_status(rows: list[dict[str, float]], start: int, low: float, high: float, side: str) -> str:
        touched = False
        for row in rows[start:]:
            if side == "bullish" and row["close"] < low:
                return "broken"
            if side == "bearish" and row["close"] > high:
                return "broken"
            if row["low"] <= high and row["high"] >= low:
                touched = True
        return "tapped" if touched else "untested"

    def _order_blocks(self, rows: list[dict[str, float]]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for index in range(1, len(rows) - 2):
            row = rows[index]
            atr = max(_atr(rows, index), row["high"] - row["low"], 1e-12)
            future_indexes = range(index + 1, min(len(rows), index + 4))
            if row["close"] < row["open"]:
                impulse_index = next((cursor for cursor in future_indexes if rows[cursor]["close"] > row["high"] and rows[cursor]["high"] - row["low"] >= atr * self.impulse_atr), None)
                side = "bullish"
            elif row["close"] > row["open"]:
                impulse_index = next((cursor for cursor in future_indexes if rows[cursor]["close"] < row["low"] and row["high"] - rows[cursor]["low"] >= atr * self.impulse_atr), None)
                side = "bearish"
            else:
                impulse_index = None
                side = ""
            if impulse_index is None:
                continue
            low, high = row["low"], row["high"]
            status = self._zone_status(rows, impulse_index + 1, low, high, side)
            blocks.append({
                "type": side,
                "high": round(high, 10),
                "low": round(low, 10),
                "status": status,
                "age_bars": len(rows) - 1 - index,
                "index": index,
                "time": row["time"],
                "impulse_index": impulse_index,
                "impulse_atr": round((abs(rows[impulse_index]["close"] - row["close"]) / atr), 3),
            })
        return blocks[-self.maximum_items :]

    def _fair_value_gaps(self, rows: list[dict[str, float]]) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        for index in range(len(rows) - 2):
            first, third = rows[index], rows[index + 2]
            if first["high"] < third["low"]:
                bottom, top, side = first["high"], third["low"], "bullish"
                later = rows[index + 3 :]
                mitigated = any(row["low"] <= bottom for row in later)
            elif first["low"] > third["high"]:
                bottom, top, side = third["high"], first["low"], "bearish"
                later = rows[index + 3 :]
                mitigated = any(row["high"] >= top for row in later)
            else:
                continue
            gaps.append({
                "type": side,
                "top": round(top, 10),
                "bottom": round(bottom, 10),
                "mitigated": mitigated,
                "index": index + 1,
                "time": rows[index + 1]["time"],
                "age_bars": len(rows) - 2 - index,
            })
        return gaps[-self.maximum_items :]

    def _swings(self, rows: list[dict[str, float]]) -> tuple[list[int], list[int]]:
        highs: list[int] = []
        lows: list[int] = []
        span = self.swing_span
        for index in range(span, len(rows) - span):
            window = rows[index - span : index + span + 1]
            if rows[index]["high"] == max(row["high"] for row in window):
                highs.append(index)
            if rows[index]["low"] == min(row["low"] for row in window):
                lows.append(index)
        return highs, lows

    def _liquidity_sweeps(self, rows: list[dict[str, float]]) -> list[dict[str, Any]]:
        swing_highs, swing_lows = self._swings(rows)
        sweeps: list[dict[str, Any]] = []
        for index in range(self.swing_span + 2, len(rows) - 1):
            prior_highs = [cursor for cursor in swing_highs if index - 30 <= cursor < index]
            prior_lows = [cursor for cursor in swing_lows if index - 30 <= cursor < index]
            if prior_lows:
                swing_index = prior_lows[-1]
                level = rows[swing_index]["low"]
                rejected = rows[index]["low"] < level and rows[index]["close"] > level
                opposing_level = max(row["high"] for row in rows[max(0, index - 6) : index])
                confirmed = rejected and any(row["close"] > opposing_level for row in rows[index : min(len(rows), index + 4)])
                if rejected:
                    sweeps.append({"type": "LIQUIDITY_SWEEP_BULL", "direction": "bullish", "level": round(level, 10), "index": index, "time": rows[index]["time"], "confirmed": confirmed})
            if prior_highs:
                swing_index = prior_highs[-1]
                level = rows[swing_index]["high"]
                rejected = rows[index]["high"] > level and rows[index]["close"] < level
                opposing_level = min(row["low"] for row in rows[max(0, index - 6) : index])
                confirmed = rejected and any(row["close"] < opposing_level for row in rows[index : min(len(rows), index + 4)])
                if rejected:
                    sweeps.append({"type": "LIQUIDITY_SWEEP_BEAR", "direction": "bearish", "level": round(level, 10), "index": index, "time": rows[index]["time"], "confirmed": confirmed})
        unique: dict[tuple[str, int], dict[str, Any]] = {(row["type"], row["index"]): row for row in sweeps}
        return list(unique.values())[-self.maximum_items :]

    @staticmethod
    def _breaker_blocks(order_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        breakers: list[dict[str, Any]] = []
        for block in order_blocks:
            if block["status"] != "broken":
                continue
            breakers.append({
                "type": "bearish_breaker" if block["type"] == "bullish" else "bullish_breaker",
                "high": block["high"],
                "low": block["low"],
                "origin_index": block["index"],
                "age_bars": block["age_bars"],
                "source": "BROKEN_ORDER_BLOCK",
            })
        return breakers

    @staticmethod
    def _nearest_order_block(rows: list[dict[str, float]], order_blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
        active = [block for block in order_blocks if block["status"] != "broken"]
        if not active:
            return None
        price = rows[-1]["close"]
        atr = max(_atr(rows, len(rows) - 1), 1e-12)
        nearest = min(active, key=lambda block: abs(price - ((block["high"] + block["low"]) / 2)))
        midpoint = (nearest["high"] + nearest["low"]) / 2
        return {"distance_atr": round(abs(price - midpoint) / atr, 3), "type": nearest["type"], "high": nearest["high"], "low": nearest["low"], "status": nearest["status"], "index": nearest["index"]}

    def analyze(self, bars: Any) -> dict[str, Any]:
        rows = _records(bars)
        if len(rows) < 3:
            return {"order_blocks": [], "fvgs": [], "liquidity_sweeps": [], "breaker_blocks": [], "nearest_ob_to_price": None, "method": "DETERMINISTIC_CLOSED_BAR_STRUCTURE"}
        order_blocks = self._order_blocks(rows)
        fvgs = self._fair_value_gaps(rows)
        sweeps = self._liquidity_sweeps(rows)
        return {
            "order_blocks": order_blocks,
            "fvgs": fvgs,
            "liquidity_sweeps": sweeps,
            "breaker_blocks": self._breaker_blocks(order_blocks),
            "nearest_ob_to_price": self._nearest_order_block(rows, order_blocks),
            "method": "DETERMINISTIC_CLOSED_BAR_STRUCTURE",
        }


def analyze_smc(bars: Any) -> dict[str, Any]:
    return SMCAnalyzer().analyze(bars)


__all__ = ["SMCAnalyzer", "analyze_smc"]
