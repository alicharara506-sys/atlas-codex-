"""Deterministic bull/bear debate summaries from validated desk outputs."""

from __future__ import annotations

from app.agents.contracts import DebateResult, DeskSignal, SignalDirection


def _argument(signal: DeskSignal) -> str:
    evidence = signal.key_evidence[0] if signal.key_evidence else "No evidence supplied."
    return f"{signal.desk.value} ({signal.confidence:.2f}): {evidence}"


def build_debate(ticker: str, signals: list[DeskSignal]) -> DebateResult:
    bullish = sorted(
        (row for row in signals if row.signal is SignalDirection.BULLISH),
        key=lambda row: (-row.confidence, row.desk.value),
    )
    bearish = sorted(
        (row for row in signals if row.signal is SignalDirection.BEARISH),
        key=lambda row: (-row.confidence, row.desk.value),
    )
    bull_arguments = [_argument(row) for row in bullish[:3]]
    bear_arguments = [_argument(row) for row in bearish[:3]]
    if len(bullish) > len(bearish):
        consensus = SignalDirection.BULLISH
    elif len(bearish) > len(bullish):
        consensus = SignalDirection.BEARISH
    else:
        consensus = SignalDirection.NEUTRAL
    disagreements = [
        f"{row.desk.value} opposes the {consensus.value.lower()} desk majority."
        for row in (bearish if consensus is SignalDirection.BULLISH else bullish)
    ]
    moderator = (
        f"{ticker}: {len(bullish)} bullish, {len(bearish)} bearish, "
        f"and {len(signals) - len(bullish) - len(bearish)} neutral desk outputs."
    )
    return DebateResult(
        ticker=ticker,
        bull_arguments=bull_arguments,
        bear_arguments=bear_arguments,
        consensus=consensus,
        moderator_summary=moderator,
        disagreements=disagreements,
    )


__all__ = ["build_debate"]
