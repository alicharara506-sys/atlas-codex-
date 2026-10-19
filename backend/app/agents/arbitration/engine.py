"""Weighted deterministic convergence with a strict trade-intent threshold."""

from __future__ import annotations

from app.agents.contracts import ArbitrationResult, DeskName, DeskSignal, SignalDirection, TradeIntent


def _sign(direction: SignalDirection) -> float:
    if direction is SignalDirection.BULLISH:
        return 1.0
    if direction is SignalDirection.BEARISH:
        return -1.0
    return 0.0


def arbitrate(
    ticker: str,
    signals: list[DeskSignal],
    weights: dict[DeskName, float],
    threshold: float,
) -> ArbitrationResult:
    active_weight = sum(weights.get(row.desk, 0.0) for row in signals)
    if active_weight <= 0.0:
        return ArbitrationResult(
            ticker=ticker,
            final_score=0.0,
            threshold=threshold,
            intent=TradeIntent.HOLD,
            contributions={},
            rationale="No positively weighted desk output was available; Atlas failed closed to HOLD.",
        )
    contributions = {
        row.desk.value: round(_sign(row.signal) * row.confidence * weights.get(row.desk, 0.0) / active_weight, 6)
        for row in signals
    }
    final_score = round(max(-1.0, min(1.0, sum(contributions.values()))), 6)
    if final_score > threshold:
        intent = TradeIntent.LONG
    elif final_score < -threshold:
        intent = TradeIntent.SHORT
    else:
        intent = TradeIntent.HOLD
    return ArbitrationResult(
        ticker=ticker,
        final_score=final_score,
        threshold=threshold,
        intent=intent,
        contributions=contributions,
        rationale=f"Weighted desk convergence is {final_score:+.3f} versus a ±{threshold:.3f} gate.",
    )


__all__ = ["arbitrate"]
