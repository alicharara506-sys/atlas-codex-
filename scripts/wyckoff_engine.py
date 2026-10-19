#!/usr/bin/env python3
"""Deterministic Wyckoff phase and event detection for closed OHLCV bars.

The analyzer accepts either a pandas DataFrame or a list of OHLCV mappings.
It performs no network or broker operations and returns JSON-safe evidence.
"""

from __future__ import annotations

import math
import statistics
from typing import Any


class WyckoffValidationError(ValueError):
    """Raised when the supplied closed-bar series is malformed."""


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise WyckoffValidationError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise WyckoffValidationError(f"{name} must be finite")
    return result


def _records(data: Any) -> list[dict[str, float]]:
    if hasattr(data, "to_dict"):
        rows = data.to_dict(orient="records")
    elif isinstance(data, list):
        rows = data
    else:
        raise WyckoffValidationError("bars must be a DataFrame or list of mappings")
    clean: list[dict[str, float]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise WyckoffValidationError(f"bars[{index}] must be a mapping")
        item = {
            "time": _finite(row.get("time", index), f"bars[{index}].time"),
            "open": _finite(row.get("open"), f"bars[{index}].open"),
            "high": _finite(row.get("high"), f"bars[{index}].high"),
            "low": _finite(row.get("low"), f"bars[{index}].low"),
            "close": _finite(row.get("close"), f"bars[{index}].close"),
            "volume": max(0.0, _finite(row.get("volume", 0), f"bars[{index}].volume")),
        }
        if item["low"] > min(item["open"], item["close"]) or item["high"] < max(item["open"], item["close"]):
            raise WyckoffValidationError(f"bars[{index}] has invalid OHLC geometry")
        clean.append(item)
    clean.sort(key=lambda row: row["time"])
    return clean


def _median_range(rows: list[dict[str, float]], index: int, period: int = 20) -> float:
    values = [row["high"] - row["low"] for row in rows[max(0, index - period) : index]]
    return statistics.median(values) if values else max(rows[index]["high"] - rows[index]["low"], 1e-12)


def _atr(rows: list[dict[str, float]], index: int, period: int = 14) -> float:
    start = max(0, index - period + 1)
    values: list[float] = []
    for cursor in range(start, index + 1):
        previous = rows[cursor - 1]["close"] if cursor else rows[cursor]["close"]
        row = rows[cursor]
        values.append(max(row["high"] - row["low"], abs(row["high"] - previous), abs(row["low"] - previous)))
    return sum(values) / len(values) if values else 0.0


def _volume_signature(rows: list[dict[str, float]], index: int, period: int = 20) -> tuple[float, float]:
    history = [row["volume"] for row in rows[max(0, index - period) : index]]
    if not history:
        return 0.0, 1.0
    mean = sum(history) / len(history)
    deviation = statistics.pstdev(history) if len(history) > 1 else 0.0
    z_score = (rows[index]["volume"] - mean) / deviation if deviation > 1e-12 else (2.0 if rows[index]["volume"] > mean * 1.5 else 0.0)
    ratio = rows[index]["volume"] / mean if mean > 0 else 0.0
    return z_score, ratio


def _event(event_type: str, index: int, row: dict[str, float], price: float, volume_z: float) -> dict[str, Any]:
    return {"type": event_type, "index": index, "time": row["time"], "price": round(price, 10), "volume_z": round(volume_z, 3)}


class WyckoffAnalyzer:
    """Detect Wyckoff accumulation/distribution events from deterministic rules."""

    def __init__(self, minimum_bars: int = 20):
        self.minimum_bars = max(20, int(minimum_bars))

    @staticmethod
    def _climax_candidates(rows: list[dict[str, float]], side: str) -> list[int]:
        candidates: list[tuple[float, int]] = []
        for index in range(5, len(rows) - 3):
            row = rows[index]
            spread = row["high"] - row["low"]
            if spread <= 0:
                continue
            lower_wick = min(row["open"], row["close"]) - row["low"]
            upper_wick = row["high"] - max(row["open"], row["close"])
            volume_z, volume_ratio = _volume_signature(rows, index)
            range_ratio = spread / max(_median_range(rows, index), 1e-12)
            if side == "low":
                extreme = row["low"] <= min(item["low"] for item in rows[index - 5 : index])
                wick_ratio = lower_wick / spread
            else:
                extreme = row["high"] >= max(item["high"] for item in rows[index - 5 : index])
                wick_ratio = upper_wick / spread
            if extreme and wick_ratio >= 0.40 and range_ratio >= 1.35 and volume_ratio >= 1.45 and volume_z >= 0.8:
                candidates.append((volume_z + range_ratio + wick_ratio, index))
        return [index for _score, index in sorted(candidates, reverse=True)]

    @staticmethod
    def _prior_event(rows: list[dict[str, float]], climax_index: int, side: str) -> dict[str, Any] | None:
        for index in range(climax_index - 1, max(1, climax_index - 12), -1):
            row = rows[index]
            spread = max(row["high"] - row["low"], 1e-12)
            volume_z, ratio = _volume_signature(rows, index)
            wick = (min(row["open"], row["close"]) - row["low"]) / spread if side == "low" else (row["high"] - max(row["open"], row["close"])) / spread
            if ratio >= 1.15 and wick >= 0.20:
                return _event("PS" if side == "low" else "PSY", index, row, row["low"] if side == "low" else row["high"], volume_z)
        return None

    def _accumulation(self, rows: list[dict[str, float]]) -> dict[str, Any] | None:
        for climax_index in self._climax_candidates(rows, "low"):
            climax = rows[climax_index]
            atr = max(_atr(rows, climax_index), climax["high"] - climax["low"], 1e-12)
            ar_candidates = [index for index in range(climax_index + 1, min(len(rows), climax_index + 10)) if rows[index]["high"] - climax["low"] >= atr * 1.05 and rows[index]["close"] > climax["close"] and rows[index]["volume"] < climax["volume"]]
            if not ar_candidates:
                continue
            ar_index = max(ar_candidates, key=lambda index: rows[index]["high"])
            st_candidates = []
            for index in range(ar_index + 1, min(len(rows), ar_index + 14)):
                distance = abs(rows[index]["low"] - climax["low"])
                if distance <= atr * 0.75 and rows[index]["low"] >= climax["low"] - atr * 0.25 and rows[index]["volume"] <= climax["volume"] * 0.72:
                    st_candidates.append(index)
            if not st_candidates:
                continue
            st_index = min(st_candidates, key=lambda index: abs(rows[index]["low"] - climax["low"]))
            spring_index = next((index for index in range(st_index + 1, min(len(rows), st_index + 9)) if rows[index]["low"] < rows[st_index]["low"] and rows[index]["close"] > rows[st_index]["low"]), None)
            sc_z, _ = _volume_signature(rows, climax_index)
            events: list[dict[str, Any]] = []
            prior = self._prior_event(rows, climax_index, "low")
            if prior:
                events.append(prior)
            events.append(_event("SC", climax_index, climax, climax["low"], sc_z))
            events.append(_event("AR", ar_index, rows[ar_index], rows[ar_index]["high"], _volume_signature(rows, ar_index)[0]))
            events.append(_event("ST", st_index, rows[st_index], rows[st_index]["low"], _volume_signature(rows, st_index)[0]))
            if spring_index is not None:
                events.append(_event("SPRING", spring_index, rows[spring_index], rows[spring_index]["low"], _volume_signature(rows, spring_index)[0]))
            confidence = min(0.95, 0.60 + (0.10 if prior else 0.0) + (0.20 if spring_index is not None else 0.0) + min(0.05, max(0.0, sc_z) / 20))
            return {"phase": "ACCUMULATION", "confidence": round(confidence, 3), "events": events, "last_event": events[-1]["type"], "narrative": f"Deterministic accumulation structure: SC at bar {climax_index}, AR at {ar_index}, ST at {st_index}" + (f", followed by a Spring at {spring_index}." if spring_index is not None else ".")}
        return None

    def _distribution(self, rows: list[dict[str, float]]) -> dict[str, Any] | None:
        for climax_index in self._climax_candidates(rows, "high"):
            climax = rows[climax_index]
            atr = max(_atr(rows, climax_index), climax["high"] - climax["low"], 1e-12)
            reaction_candidates = [index for index in range(climax_index + 1, min(len(rows), climax_index + 10)) if climax["high"] - rows[index]["low"] >= atr * 1.05 and rows[index]["close"] < climax["close"] and rows[index]["volume"] < climax["volume"]]
            if not reaction_candidates:
                continue
            reaction_index = min(reaction_candidates, key=lambda index: rows[index]["low"])
            test_candidates = []
            for index in range(reaction_index + 1, min(len(rows), reaction_index + 14)):
                distance = abs(rows[index]["high"] - climax["high"])
                if distance <= atr * 0.75 and rows[index]["high"] <= climax["high"] + atr * 0.25 and rows[index]["volume"] <= climax["volume"] * 0.72:
                    test_candidates.append(index)
            if not test_candidates:
                continue
            test_index = min(test_candidates, key=lambda index: abs(rows[index]["high"] - climax["high"]))
            upthrust_index = next((index for index in range(test_index + 1, min(len(rows), test_index + 9)) if rows[index]["high"] > rows[test_index]["high"] and rows[index]["close"] < rows[test_index]["high"]), None)
            bc_z, _ = _volume_signature(rows, climax_index)
            events: list[dict[str, Any]] = []
            prior = self._prior_event(rows, climax_index, "high")
            if prior:
                events.append(prior)
            events.append(_event("BC", climax_index, climax, climax["high"], bc_z))
            events.append(_event("AR", reaction_index, rows[reaction_index], rows[reaction_index]["low"], _volume_signature(rows, reaction_index)[0]))
            events.append(_event("ST", test_index, rows[test_index], rows[test_index]["high"], _volume_signature(rows, test_index)[0]))
            if upthrust_index is not None:
                events.append(_event("UPTHRUST", upthrust_index, rows[upthrust_index], rows[upthrust_index]["high"], _volume_signature(rows, upthrust_index)[0]))
            confidence = min(0.95, 0.60 + (0.10 if prior else 0.0) + (0.20 if upthrust_index is not None else 0.0) + min(0.05, max(0.0, bc_z) / 20))
            return {"phase": "DISTRIBUTION", "confidence": round(confidence, 3), "events": events, "last_event": events[-1]["type"], "narrative": f"Deterministic distribution structure: BC at bar {climax_index}, reaction at {reaction_index}, ST at {test_index}" + (f", followed by an Upthrust at {upthrust_index}." if upthrust_index is not None else ".")}
        return None

    def analyze(self, bars: Any) -> dict[str, Any]:
        rows = _records(bars)
        if len(rows) < self.minimum_bars:
            return {"phase": "UNKNOWN", "confidence": 0.0, "events": [], "last_event": None, "narrative": f"At least {self.minimum_bars} closed bars are required for Wyckoff structure."}
        accumulation = self._accumulation(rows)
        distribution = self._distribution(rows)
        if accumulation and distribution:
            return accumulation if accumulation["confidence"] >= distribution["confidence"] else distribution
        if accumulation:
            return accumulation
        if distribution:
            return distribution
        atr = max(_atr(rows, len(rows) - 1), 1e-12)
        net_move = rows[-1]["close"] - rows[max(0, len(rows) - 20)]["close"]
        if net_move >= atr * 3:
            phase, confidence = "MARKUP", min(0.80, 0.45 + abs(net_move / atr) * 0.03)
        elif net_move <= -atr * 3:
            phase, confidence = "MARKDOWN", min(0.80, 0.45 + abs(net_move / atr) * 0.03)
        else:
            phase, confidence = "UNKNOWN", 0.2
        return {"phase": phase, "confidence": round(confidence, 3), "events": [], "last_event": None, "narrative": f"No complete Wyckoff range sequence was detected; deterministic phase is {phase}."}


def analyze_wyckoff(bars: Any, minimum_bars: int = 20) -> dict[str, Any]:
    return WyckoffAnalyzer(minimum_bars).analyze(bars)


__all__ = ["WyckoffAnalyzer", "WyckoffValidationError", "analyze_wyckoff"]
