"""Typed research-only API for the Phase 2 agent workflow."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.agents.contracts import ResearchRequest
from app.agents.workflow import AtlasAgentWorkflow
from app.core.store import stable_key
from app.models.database import Database, SqlModelRecordStore


class ResearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_key: str
    phase: Literal[2] = 2
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False
    state: dict[str, Any]


class ResearchRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_key: str
    universe: list[str]
    risk_veto: bool
    state: dict[str, Any]
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False


class ResearchHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=0)
    runs: list[ResearchRunSummary]
    execution_enabled: Literal[False] = False


def build_research_router(database: Database) -> APIRouter:
    router = APIRouter(prefix="/research", tags=["research"])
    store = SqlModelRecordStore(database)
    workflow = AtlasAgentWorkflow(store)

    @router.post("/evaluate", response_model=ResearchResponse)
    def evaluate(request: ResearchRequest) -> ResearchResponse:
        return ResearchResponse(
            run_key=stable_key(request.model_dump(mode="json")),
            state=workflow.run(request),
        )

    @router.get("/runs", response_model=ResearchHistoryResponse)
    def history(limit: int = Query(default=20, ge=1, le=100)) -> ResearchHistoryResponse:
        rows = store.values("agentic_research_runs")
        rows.sort(key=lambda row: str(row.get("run_key", "")))
        summaries = [
            ResearchRunSummary(
                run_key=row["run_key"],
                universe=list(row.get("state", {}).get("universe", [])),
                risk_veto=bool(row.get("state", {}).get("risk_veto", False)),
                state=row.get("state", {}),
            )
            for row in rows[-limit:]
        ]
        return ResearchHistoryResponse(count=len(summaries), runs=summaries)

    return router


__all__ = ["ResearchHistoryResponse", "ResearchResponse", "ResearchRunSummary", "build_research_router"]
