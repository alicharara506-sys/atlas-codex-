"""End-to-end Phase 2 graph coordinator with idempotent audit persistence."""

from __future__ import annotations

from typing import Any

from app.agents.arbitration import arbitrate
from app.agents.contracts import ArbitrationResult, DeskSignal, ResearchRequest, RiskAssessment
from app.agents.debate import build_debate
from app.agents.desks import run_desks
from app.agents.portfolio import build_portfolio
from app.agents.risk_guard import assess_risk
from app.agents.state import AtlasState, empty_state
from app.core.store import RecordStore, stable_key


def _audit(state: AtlasState, stage: str, summary: str, metadata: dict[str, Any] | None = None) -> None:
    state["audit_trail"].append(
        {
            "sequence": len(state["audit_trail"]) + 1,
            "stage": stage,
            "summary": summary,
            "metadata": metadata or {},
        }
    )


class AtlasAgentWorkflow:
    """Run deterministic research nodes; execution is deliberately impossible."""

    def __init__(self, store: RecordStore | None = None) -> None:
        self.store = store

    def run(self, request: ResearchRequest) -> AtlasState:
        market_scan = {row.ticker: row.model_dump(mode="json") for row in request.observations}
        state = empty_state([row.ticker for row in request.observations], market_scan)
        state["manager_override"] = request.manager_override
        _audit(state, "scan", f"Validated {len(request.observations)} point-in-time market observations.")

        desk_outputs = run_desks(request.observations)
        state["desk_signals"] = {
            name: [row.model_dump(mode="json") for row in rows] for name, rows in desk_outputs.items()
        }
        _audit(state, "desks", f"Completed {len(desk_outputs)} specialist desks.")

        arbitrations: dict[str, ArbitrationResult] = {}
        assessments: dict[str, RiskAssessment] = {}
        for observation in request.observations:
            signals: list[DeskSignal] = [
                row for rows in desk_outputs.values() for row in rows if row.ticker == observation.ticker
            ]
            debate = build_debate(observation.ticker, signals)
            state["debate_transcript"].append(debate.model_dump(mode="json"))
            arbitration = arbitrate(
                observation.ticker,
                signals,
                request.limits.desk_weights,
                request.limits.arbitration_threshold,
            )
            arbitrations[observation.ticker] = arbitration
            assessment = assess_risk(
                observation,
                arbitration,
                request.risk_context,
                request.limits,
                manager_override=request.manager_override,
            )
            assessments[observation.ticker] = assessment
            _audit(
                state,
                "risk_guard",
                f"{observation.ticker}: {'VETO' if assessment.vetoed else arbitration.intent.value}.",
                {"failed_checks": [row.name for row in assessment.checks if not row.passed]},
            )

        state["arbitration"] = {
            ticker: row.model_dump(mode="json") for ticker, row in arbitrations.items()
        }
        state["risk_assessment"] = {
            ticker: row.model_dump(mode="json") for ticker, row in assessments.items()
        }
        state["risk_veto"] = any(row.vetoed for row in assessments.values())
        proposal = build_portfolio(
            request.observations,
            arbitrations,
            assessments,
            request.risk_context,
            request.limits,
        )
        state["portfolio_proposal"] = proposal.model_dump(mode="json")
        state["execution_plan"] = []
        _audit(
            state,
            "portfolio",
            f"Produced {len(proposal.positions)} research-only target positions; execution remains disabled.",
        )
        if self.store is not None:
            run_key = stable_key(request.model_dump(mode="json"))
            self.store.upsert("agentic_research_runs", run_key, {"run_key": run_key, "state": state})
        return state


__all__ = ["AtlasAgentWorkflow"]
