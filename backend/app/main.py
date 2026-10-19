"""Atlas FastAPI application factory with an isolated V4 foundation route."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.mandates import build_mandate_router
from app.api.research import build_research_router
from app.api.system import HealthProbe, build_system_router
from app.api.v4 import build_v4_router
from app.core.config import AtlasSettings
from app.core.infrastructure import RedisProbe
from app.core.security import AtlasSecurityMiddleware
from app.models.database import Database
from app.v4.ai_review import AIReviewLLMClient
from app.v4.mt5 import MT5Gateway, MT5GatewayProtocol
from app.v4.orchestration import V4OrchestrationService, build_v4_orchestration_service


def create_app(
    settings: AtlasSettings | None = None,
    database: Database | None = None,
    redis_probe: HealthProbe | None = None,
    v4_gateway: MT5GatewayProtocol | None = None,
    v4_llm_client: AIReviewLLMClient | None = None,
    v4_orchestrator: V4OrchestrationService | None = None,
) -> FastAPI:
    resolved_settings = settings or AtlasSettings.from_env()
    resolved_database = database or Database(resolved_settings.database_url)
    resolved_redis_probe = redis_probe or RedisProbe(resolved_settings.redis_url)
    resolved_gateway = v4_gateway or MT5Gateway(
        resolved_settings.mt5_base_url,
        resolved_settings.mt5_bridge_token,
        timeout_seconds=resolved_settings.mt5_timeout_seconds,
    )
    resolved_orchestrator = v4_orchestrator or build_v4_orchestration_service(
        resolved_settings,
        resolved_database,
        resolved_gateway,
        v4_llm_client,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await resolved_orchestrator.start()
        try:
            yield
        finally:
            await resolved_orchestrator.stop()

    application = FastAPI(
        title="Atlas Quant Research OS",
        version="0.4.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.add_middleware(AtlasSecurityMiddleware, settings=resolved_settings)
    application.state.settings = resolved_settings
    application.state.database = resolved_database
    application.state.redis_probe = resolved_redis_probe
    application.state.v4_orchestrator = resolved_orchestrator
    application.include_router(build_system_router(resolved_settings, resolved_database, resolved_redis_probe))
    application.include_router(build_research_router(resolved_database))
    application.include_router(build_mandate_router(resolved_database))
    application.include_router(
        build_v4_router(
            resolved_settings,
            resolved_database,
            resolved_gateway,
            v4_llm_client,
            infrastructure_probe=resolved_redis_probe,
            orchestrator=resolved_orchestrator,
        )
    )
    return application


app = create_app()
