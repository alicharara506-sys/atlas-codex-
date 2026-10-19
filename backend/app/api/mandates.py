"""Typed Phase 3 mandate validation and deterministic backtest routes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.backtest.contracts import BacktestMetrics, BacktestReport, BacktestSeries
from app.backtest.engine import BacktestError, run_backtest
from app.mandates.contracts import MandateDocument, MandateValidationError, PromotionStatus
from app.mandates.service import MandateService
from app.models.database import Database, SqlModelRecordStore


class ValidateMandateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml_text: str = Field(min_length=1, max_length=100_000)


class ValidateMandateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mandate_key: str
    phase: Literal[3] = 3
    document: MandateDocument
    lifecycle: PromotionStatus
    execution_enabled: Literal[False] = False


class RunBacktestRequest(ValidateMandateRequest):
    series: list[BacktestSeries] = Field(min_length=1)


class RunBacktestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mandate_key: str
    run_key: str
    phase: Literal[3] = 3
    report: BacktestReport
    lifecycle: PromotionStatus
    execution_enabled: Literal[False] = False


class BacktestRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_key: str
    mandate_key: str
    mandate_name: str
    mandate_version: str
    symbols: list[str]
    metrics: BacktestMetrics
    lifecycle: PromotionStatus
    evaluated_at: datetime | None = None
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False


class BacktestHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=0)
    runs: list[BacktestRunSummary]
    execution_enabled: Literal[False] = False


def build_mandate_router(database: Database) -> APIRouter:
    router = APIRouter(tags=["mandates"])
    service = MandateService(SqlModelRecordStore(database))

    @router.post("/mandates/validate", response_model=ValidateMandateResponse)
    def validate_mandate(request: ValidateMandateRequest) -> ValidateMandateResponse:
        try:
            mandate_key, document, lifecycle = service.validate_and_register(request.yaml_text)
        except MandateValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return ValidateMandateResponse(mandate_key=mandate_key, document=document, lifecycle=lifecycle)

    @router.post("/backtests/run", response_model=RunBacktestResponse)
    def execute_backtest(request: RunBacktestRequest) -> RunBacktestResponse:
        try:
            mandate_key, document, _ = service.validate_and_register(request.yaml_text)
            report = run_backtest(document, request.series)
        except (MandateValidationError, BacktestError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        run_key, lifecycle = service.record_backtest(mandate_key, document, report)
        return RunBacktestResponse(
            mandate_key=mandate_key,
            run_key=run_key,
            report=report,
            lifecycle=lifecycle,
        )

    @router.get("/backtests", response_model=BacktestHistoryResponse)
    def backtest_history(limit: int = Query(default=50, ge=1, le=100)) -> BacktestHistoryResponse:
        runs = [BacktestRunSummary.model_validate(row) for row in service.list_backtests(limit=limit)]
        return BacktestHistoryResponse(count=len(runs), runs=runs)

    return router


__all__ = [
    "BacktestHistoryResponse",
    "BacktestRunSummary",
    "RunBacktestRequest",
    "RunBacktestResponse",
    "ValidateMandateRequest",
    "ValidateMandateResponse",
    "build_mandate_router",
]
