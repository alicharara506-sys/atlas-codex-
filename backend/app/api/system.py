"""Typed system-health contracts and routes."""

from __future__ import annotations

from typing import Literal, Protocol

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict

from app.core.config import AtlasSettings
from app.models.database import Database


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    phase: Literal[3] = 3
    agent_engine_enabled: Literal[True] = True
    mandate_engine_enabled: Literal[True] = True
    backtest_engine_enabled: Literal[True] = True
    execution_enabled: Literal[False] = False
    providers: dict[str, bool]


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    database: bool
    redis: bool
    execution_enabled: Literal[False] = False


class HealthProbe(Protocol):
    def healthy(self) -> bool: ...


def build_system_router(settings: AtlasSettings, database: Database, redis_probe: HealthProbe) -> APIRouter:
    router = APIRouter(tags=["system"])

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(providers={name: row.configured for name, row in settings.providers.items()})

    @router.get("/ready", response_model=ReadinessResponse)
    def readiness(response: Response) -> ReadinessResponse:
        database_ready = database.healthy()
        redis_ready = redis_probe.healthy()
        if not database_ready or not redis_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="ready" if database_ready and redis_ready else "not_ready",
            database=database_ready,
            redis=redis_ready,
        )

    return router


__all__ = ["HealthProbe", "HealthResponse", "ReadinessResponse", "build_system_router"]
