"""LangGraph-compatible typed state for deterministic Phase 2 research."""

from __future__ import annotations

from typing import Any, TypedDict


class AtlasState(TypedDict):
    universe: list[str]
    market_scan: dict[str, dict[str, Any]]
    desk_signals: dict[str, list[dict[str, Any]]]
    debate_transcript: list[dict[str, Any]]
    arbitration: dict[str, dict[str, Any]]
    risk_assessment: dict[str, dict[str, Any]]
    portfolio_proposal: dict[str, Any]
    risk_veto: bool
    manager_override: str | None
    execution_plan: list[dict[str, Any]]
    audit_trail: list[dict[str, Any]]


def empty_state(universe: list[str], market_scan: dict[str, dict[str, Any]]) -> AtlasState:
    return AtlasState(
        universe=list(universe),
        market_scan=market_scan,
        desk_signals={},
        debate_transcript=[],
        arbitration={},
        risk_assessment={},
        portfolio_proposal={},
        risk_veto=False,
        manager_override=None,
        execution_plan=[],
        audit_trail=[],
    )


__all__ = ["AtlasState", "empty_state"]
