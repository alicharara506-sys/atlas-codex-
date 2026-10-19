"""Deterministic Tier-0 specialist desks with one strict output contract."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.contracts import DeskName, DeskSignal, MarketObservation, SignalDirection


@dataclass(frozen=True, slots=True)
class ScoreDesk:
    name: DeskName
    score_field: str
    evidence_label: str
    neutral_band: float = 0.10

    def process(self, observation: MarketObservation) -> DeskSignal:
        raw = getattr(observation, self.score_field)
        if raw is None:
            return DeskSignal(
                ticker=observation.ticker,
                desk=self.name,
                signal=SignalDirection.NEUTRAL,
                confidence=0.0,
                key_evidence=[f"No validated {self.evidence_label.lower()} input was supplied."],
                risk_factors=[f"Missing {self.name.value} data"],
                feedback=f"Refresh {self.name.value} inputs before the next cycle.",
            )
        score = float(raw)
        if abs(score) <= self.neutral_band:
            direction = SignalDirection.NEUTRAL
        elif score > 0.0:
            direction = SignalDirection.BULLISH
        else:
            direction = SignalDirection.BEARISH
        return DeskSignal(
            ticker=observation.ticker,
            desk=self.name,
            signal=direction,
            confidence=round(abs(score), 4),
            key_evidence=[f"{self.evidence_label} score is {score:+.3f}."],
            risk_factors=[] if direction is not SignalDirection.NEUTRAL else ["Signal is inside the neutral band"],
            feedback="Re-evaluate when a completed input window is available.",
        )


class FundamentalsDesk(ScoreDesk):
    def __init__(self) -> None:
        super().__init__(DeskName.FUNDAMENTALS, "fundamentals_score", "Fundamental quality")


class TechnicalDesk(ScoreDesk):
    def __init__(self) -> None:
        super().__init__(DeskName.TECHNICAL, "technical_score", "Technical confluence")


class SentimentDesk(ScoreDesk):
    def __init__(self) -> None:
        super().__init__(DeskName.SENTIMENT, "sentiment_score", "Sentiment composite")


class MacroDesk(ScoreDesk):
    def __init__(self) -> None:
        super().__init__(DeskName.MACRO, "macro_score", "Macro regime alignment")


class NarrativeDesk(ScoreDesk):
    def __init__(self) -> None:
        super().__init__(DeskName.NARRATIVE, "narrative_score", "Narrative strength")


class FlowDesk(ScoreDesk):
    def __init__(self) -> None:
        super().__init__(DeskName.FLOW, "flow_score", "Institutional flow")


class InsiderDesk(ScoreDesk):
    def __init__(self) -> None:
        super().__init__(DeskName.INSIDER, "insider_score", "Insider activity")


DEFAULT_DESKS: tuple[ScoreDesk, ...] = (
    FundamentalsDesk(),
    TechnicalDesk(),
    SentimentDesk(),
    MacroDesk(),
    NarrativeDesk(),
    FlowDesk(),
    InsiderDesk(),
)


def run_desks(
    observations: list[MarketObservation], desks: tuple[ScoreDesk, ...] = DEFAULT_DESKS
) -> dict[str, list[DeskSignal]]:
    return {desk.name.value: [desk.process(row) for row in observations] for desk in desks}


__all__ = [
    "DEFAULT_DESKS",
    "FlowDesk",
    "FundamentalsDesk",
    "InsiderDesk",
    "MacroDesk",
    "NarrativeDesk",
    "ScoreDesk",
    "SentimentDesk",
    "TechnicalDesk",
    "run_desks",
]
