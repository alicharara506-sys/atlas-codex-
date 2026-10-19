"""Fail-closed operational probes for ATLAS V4 data and research services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import AtlasSettings
from app.core.infrastructure import LocalModelInventory
from app.core.store import RecordStore
from app.models.database import Database
from app.v4.contracts import DataQualityStatus, MT5ConnectionState
from app.v4.health_contracts import ComponentName, ComponentState, OperationalComponent, V4OperationalHealth
from app.v4.ml_contracts import ModelStatusRecord, PrimaryModelArtifact
from app.v4.primary_ml import MODEL_ARTIFACT_COLLECTION, MODEL_STATUS_COLLECTION
from app.v4.snapshots import MarketSnapshotService


class InfrastructureProbe(Protocol):
    def healthy(self) -> bool: ...


class LLMInventoryProbe(Protocol):
    def check(self) -> LocalModelInventory: ...


class V4OperationalHealthService:
    """Report observed component state without creating market or model evidence."""

    def __init__(
        self,
        settings: AtlasSettings,
        database: Database,
        infrastructure_probe: InfrastructureProbe,
        snapshots: MarketSnapshotService,
        store: RecordStore,
        llm_inventory_probe: LLMInventoryProbe | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.infrastructure_probe = infrastructure_probe
        self.snapshots = snapshots
        self.store = store
        self.llm_inventory_probe = llm_inventory_probe

    def report(self, instrument: str) -> V4OperationalHealth:
        checked_at = datetime.now(UTC)
        database = self._simple_probe("DATABASE", True, checked_at, self.database.healthy)
        redis = self._simple_probe("REDIS", True, checked_at, self.infrastructure_probe.healthy)
        mt5 = self._mt5(instrument, checked_at)
        data = self._data(instrument, checked_at)
        models = self._models(checked_at)
        llm = self._llm(checked_at)

        if database.state != "HEALTHY" or redis.state != "HEALTHY":
            status: Literal["READY", "DEGRADED", "NOT_READY"] = "NOT_READY"
        elif any(component.state in {"DEGRADED", "UNAVAILABLE"} for component in (mt5, data, models, llm)):
            status = "DEGRADED"
        else:
            status = "READY"
        return V4OperationalHealth(
            checked_at=checked_at,
            status=status,
            database=database,
            redis=redis,
            mt5=mt5,
            data=data,
            models=models,
            llm=llm,
        )

    @staticmethod
    def _simple_probe(
        name: ComponentName,
        configured: bool,
        checked_at: datetime,
        probe: Callable[[], bool],
    ) -> OperationalComponent:
        started = perf_counter()
        try:
            healthy = bool(probe())
        except (OSError, RuntimeError, SQLAlchemyError):
            healthy = False
        latency = (perf_counter() - started) * 1_000
        return OperationalComponent(
            name=name,
            state="HEALTHY" if healthy else "UNAVAILABLE",
            configured=configured,
            checked_at=checked_at,
            latency_ms=latency,
            message=f"{name.title()} probe passed" if healthy else f"{name.title()} probe failed",
            issues=() if healthy else (f"{name}_UNAVAILABLE",),
        )

    def _mt5(self, instrument: str, checked_at: datetime) -> OperationalComponent:
        if not self.settings.mt5_enabled:
            return OperationalComponent(
                name="MT5",
                state="DISABLED",
                configured=False,
                checked_at=checked_at,
                latency_ms=0,
                message="MT5 ingestion is disabled by configuration",
                issues=("MT5_DISABLED",),
            )
        started = perf_counter()
        try:
            report = self.snapshots.health(instrument, now=checked_at)
        except (OSError, RuntimeError, SQLAlchemyError):
            return OperationalComponent(
                name="MT5",
                state="UNAVAILABLE",
                configured=True,
                checked_at=checked_at,
                latency_ms=(perf_counter() - started) * 1_000,
                message="MT5 health probe failed",
                issues=("MT5_HEALTH_PROBE_FAILED",),
            )
        healthy = report.state is MT5ConnectionState.CONNECTED and report.data_fresh
        degraded = report.state in {MT5ConnectionState.DEGRADED, MT5ConnectionState.MARKET_CLOSED}
        return OperationalComponent(
            name="MT5",
            state="HEALTHY" if healthy else ("DEGRADED" if degraded else "UNAVAILABLE"),
            configured=True,
            checked_at=checked_at,
            latency_ms=report.latency_ms,
            evidence_count=1,
            message=f"MT5 is {report.state.value.lower().replace('_', ' ')}",
            issues=report.issues,
        )

    def _data(self, instrument: str, checked_at: datetime) -> OperationalComponent:
        started = perf_counter()
        try:
            snapshot = self.snapshots.latest(instrument)
        except (OSError, RuntimeError, SQLAlchemyError, ValidationError):
            snapshot = None
            failed = True
        else:
            failed = False
        latency = (perf_counter() - started) * 1_000
        if failed:
            return OperationalComponent(
                name="DATA",
                state="UNAVAILABLE",
                configured=self.settings.mt5_enabled,
                checked_at=checked_at,
                latency_ms=latency,
                message="Persisted market-data probe failed",
                issues=("MARKET_DATA_PROBE_FAILED",),
            )
        if snapshot is None:
            return OperationalComponent(
                name="DATA",
                state="UNAVAILABLE",
                configured=self.settings.mt5_enabled,
                checked_at=checked_at,
                latency_ms=latency,
                message="No persisted MT5 snapshot exists for this instrument",
                issues=("NO_PERSISTED_MT5_SNAPSHOT",),
            )
        healthy = (
            snapshot.data_quality.status is DataQualityStatus.HEALTHY
            and snapshot.data_quality.publication_eligible
            and snapshot.health.data_fresh
        )
        return OperationalComponent(
            name="DATA",
            state="HEALTHY" if healthy else "DEGRADED",
            configured=True,
            checked_at=checked_at,
            latency_ms=latency,
            evidence_count=1,
            message=f"Latest persisted snapshot is {snapshot.data_quality.status.value.lower()}",
            issues=tuple(issue.code for issue in snapshot.data_quality.issues),
        )

    def _models(self, checked_at: datetime) -> OperationalComponent:
        started = perf_counter()
        issues: list[str] = []
        try:
            artifacts = self._validated(
                MODEL_ARTIFACT_COLLECTION,
                PrimaryModelArtifact,
                issues,
                limit=128,
            )
            statuses = self._validated(
                MODEL_STATUS_COLLECTION,
                ModelStatusRecord,
                issues,
                limit=256,
            )
        except (OSError, RuntimeError, SQLAlchemyError):
            return OperationalComponent(
                name="MODELS",
                state="UNAVAILABLE",
                configured=True,
                checked_at=checked_at,
                latency_ms=(perf_counter() - started) * 1_000,
                message="Model registry probe failed",
                issues=("MODEL_REGISTRY_PROBE_FAILED",),
            )
        latency = (perf_counter() - started) * 1_000
        if not artifacts:
            return OperationalComponent(
                name="MODELS",
                state="UNAVAILABLE",
                configured=True,
                checked_at=checked_at,
                latency_ms=latency,
                message="No governed primary-model artifact is persisted",
                issues=("NO_MODEL_ARTIFACT", *issues),
            )
        state: ComponentState = "HEALTHY" if not issues else "DEGRADED"
        return OperationalComponent(
            name="MODELS",
            state=state,
            configured=True,
            checked_at=checked_at,
            latency_ms=latency,
            evidence_count=len(artifacts) + len(statuses),
            message=f"{len(artifacts)} governed model artifact(s) available",
            issues=tuple(issues),
        )

    def _llm(self, checked_at: datetime) -> OperationalComponent:
        if not self.settings.v4_ai_review_enabled:
            return OperationalComponent(
                name="LLM",
                state="DISABLED",
                configured=False,
                checked_at=checked_at,
                latency_ms=0,
                message="Optional non-voting local AI review is disabled",
                issues=("V4_AI_REVIEW_DISABLED",),
            )
        if self.llm_inventory_probe is None:
            return OperationalComponent(
                name="LLM",
                state="UNAVAILABLE",
                configured=True,
                checked_at=checked_at,
                latency_ms=0,
                message="Local model inventory probe is unavailable",
                issues=("LLM_INVENTORY_PROBE_UNAVAILABLE",),
            )
        started = perf_counter()
        try:
            inventory = self.llm_inventory_probe.check()
        except (OSError, RuntimeError, SQLAlchemyError):
            inventory = LocalModelInventory(False, (), (), "LLM_INVENTORY_PROBE_FAILED")
        latency = (perf_counter() - started) * 1_000
        healthy = inventory.healthy
        partial = inventory.reachable and bool(inventory.available_models)
        issue = inventory.issue
        return OperationalComponent(
            name="LLM",
            state="HEALTHY" if healthy else ("DEGRADED" if partial else "UNAVAILABLE"),
            configured=True,
            checked_at=checked_at,
            latency_ms=latency,
            evidence_count=len(inventory.available_models),
            message=(
                "All four local reviewer models are installed"
                if healthy
                else (
                    f"{len(inventory.available_models)} of "
                    f"{len(inventory.available_models) + len(inventory.missing_models)} local models are available"
                )
            ),
            issues=() if issue is None else (issue,),
        )

    def _validated[ModelT: BaseModel](
        self,
        collection: str,
        model: type[ModelT],
        issues: list[str],
        *,
        limit: int,
    ) -> list[ModelT]:
        rows: list[ModelT] = []
        for index, payload in enumerate(self.store.recent_values(collection, limit)):
            try:
                rows.append(model.model_validate(payload))
            except ValidationError:
                issues.append(f"INVALID_{collection.upper()}_ROW_{index}")
        return rows


__all__ = ["InfrastructureProbe", "LLMInventoryProbe", "V4OperationalHealthService"]
