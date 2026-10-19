from __future__ import annotations

import asyncio
import math
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import AtlasSettings, ConfigurationError
from app.core.store import MemoryRecordStore
from app.main import create_app
from app.models.database import Database
from app.v4.contracts import OfficialTimeframe, V4Bar
from app.v4.kronos import (
    KRONOS_BUNDLE_COLLECTION,
    KRONOS_FORECAST_COLLECTION,
    KronosShadowEngine,
    UnavailableKronosProvider,
)
from app.v4.kronos_contracts import (
    KronosBarrierGeometry,
    KronosForecastStatus,
    KronosMode,
)
from app.v4.orchestration_contracts import OrchestrationStage, OrchestrationStageState
from app.v4.snapshots import MarketSnapshotService
from tests.test_v4_market_foundation import NOW, FakeGateway


class ScenarioProvider:
    model_version = "kronos-base-test-double"
    provider_preprocessing_verified = True
    pretraining_cutoff_verified = False

    def availability(self) -> tuple[bool, str | None]:
        return True, None

    def generate_paths(
        self,
        bars: tuple[V4Bar, ...],
        timeframe: OfficialTimeframe,
        *,
        horizon_bars: int,
        sample_count: int,
        seed: int,
    ) -> tuple[tuple[float, ...], ...]:
        del timeframe, seed
        origin = bars[-1].close
        paths: list[tuple[float, ...]] = []
        for index in range(sample_count):
            if index < sample_count * 0.50:
                terminal_return = 0.02
            elif index < sample_count * 0.80:
                terminal_return = -0.02
            else:
                terminal_return = 0.002
            paths.append(
                tuple(
                    origin * (1 + terminal_return * step / horizon_bars)
                    for step in range(1, horizon_bars + 1)
                )
            )
        return tuple(paths)


class KronosShadowEngineTests(unittest.TestCase):
    def snapshot(self, store: MemoryRecordStore):  # type: ignore[no-untyped-def]
        return MarketSnapshotService(FakeGateway(), store, enabled=True).capture("XAUUSD", now=NOW)

    def test_shadow_forecast_persists_full_distribution_without_signal_authority(self) -> None:
        store = MemoryRecordStore()
        snapshot = self.snapshot(store)
        engine = KronosShadowEngine(
            store,
            ScenarioProvider(),
            enabled=True,
            mode=KronosMode.SHADOW,
            sample_count=200,
            horizon_bars=12,
        )

        forecast = engine.forecast(
            snapshot,
            OfficialTimeframe.H4,
            barrier=KronosBarrierGeometry(
                take_profit_return=0.01,
                stop_loss_return=-0.01,
            ),
            now=NOW,
        )

        self.assertEqual(forecast.status, KronosForecastStatus.AVAILABLE)
        self.assertEqual(forecast.sample_count, 200)
        self.assertTrue(math.isclose(forecast.p_positive_return or 0, 0.70))
        self.assertTrue(math.isclose(forecast.p_negative_return or 0, 0.30))
        self.assertTrue(math.isclose(forecast.tp_first_probability or 0, 0.50))
        self.assertTrue(math.isclose(forecast.sl_first_probability or 0, 0.30))
        self.assertTrue(math.isclose(forecast.neither_probability or 0, 0.20))
        self.assertEqual(forecast.effective_weight, 0)
        self.assertFalse(forecast.signal_authority)
        self.assertFalse(forecast.execution_enabled)
        self.assertFalse(forecast.incremental_value_verified)
        self.assertFalse(forecast.pretraining_cutoff_verified)
        self.assertEqual(len(store.values(KRONOS_FORECAST_COLLECTION)), 1)

    def test_bundle_records_each_official_timeframe_in_shadow_mode(self) -> None:
        store = MemoryRecordStore()
        engine = KronosShadowEngine(store, ScenarioProvider(), enabled=True)

        bundle = engine.forecast_all(self.snapshot(store), now=NOW)

        self.assertEqual(bundle.available_forecasts, 4)
        self.assertEqual({row.timeframe for row in bundle.forecasts}, set(OfficialTimeframe))
        self.assertEqual(bundle.effective_weight, 0)
        self.assertFalse(bundle.signal_authority)
        self.assertEqual(len(store.values(KRONOS_BUNDLE_COLLECTION)), 1)

    def test_unavailable_provider_is_reported_honestly(self) -> None:
        store = MemoryRecordStore()
        engine = KronosShadowEngine(
            store,
            UnavailableKronosProvider("Kronos model files are not installed"),
            enabled=True,
        )

        forecast = engine.forecast(self.snapshot(store), OfficialTimeframe.H1, now=NOW)

        self.assertEqual(forecast.status, KronosForecastStatus.PROVIDER_UNAVAILABLE)
        self.assertIn("not installed", forecast.failure_reason or "")
        self.assertIsNone(forecast.expected_return)
        self.assertEqual(forecast.effective_weight, 0)

    def test_future_input_forces_leakage_veto_and_prevents_inference(self) -> None:
        store = MemoryRecordStore()
        snapshot = self.snapshot(store)
        bars = list(snapshot.timeframes[OfficialTimeframe.H1])
        bars[-1] = bars[-1].model_copy(update={"timestamp": snapshot.captured_at + timedelta(hours=1)})
        contaminated = snapshot.model_copy(
            update={
                "timeframes": {
                    **snapshot.timeframes,
                    OfficialTimeframe.H1: tuple(bars),
                }
            }
        )
        engine = KronosShadowEngine(store, ScenarioProvider(), enabled=True)

        forecast = engine.forecast(contaminated, OfficialTimeframe.H1, now=NOW)

        self.assertEqual(forecast.status, KronosForecastStatus.DATA_BLOCKED)
        self.assertTrue(forecast.leakage_audit.future_bar_detected)
        self.assertTrue(forecast.leakage_audit.quant_veto_required)
        self.assertEqual(forecast.sample_count, 0)

    def test_shadow_mode_rejects_nonzero_weight(self) -> None:
        with self.assertRaisesRegex(ValueError, "zero effective weight"):
            KronosShadowEngine(
                MemoryRecordStore(),
                ScenarioProvider(),
                enabled=True,
                mode=KronosMode.SHADOW,
                effective_weight=0.01,
            )

    def test_settings_reject_unsupported_sampling_and_shadow_weight(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "200, 500, or 1000"):
            AtlasSettings.from_env({"KRONOS_SAMPLE_COUNT": "300"})
        with self.assertRaisesRegex(ConfigurationError, "requires KRONOS_EFFECTIVE_WEIGHT=0"):
            AtlasSettings.from_env(
                {
                    "KRONOS_MODE": "shadow",
                    "KRONOS_EFFECTIVE_WEIGHT": "0.01",
                }
            )

    def test_background_cycle_persists_shadow_bundle_without_changing_blockers(self) -> None:
        from tests.test_v4_orchestration import service

        store = MemoryRecordStore()
        worker = service(FakeGateway(), store)
        worker.kronos = KronosShadowEngine(store, ScenarioProvider(), enabled=True)

        run = asyncio.run(worker.run_once())

        result = run.instruments[0]
        stage = next(row for row in result.stages if row.stage is OrchestrationStage.KRONOS_SHADOW)
        self.assertEqual(stage.state, OrchestrationStageState.COMPLETED)
        self.assertIsNotNone(result.kronos_bundle_id)
        self.assertNotIn("KRONOS_SHADOW_UNAVAILABLE", result.blocker_codes)
        self.assertEqual(result.kronos_bundle_id, store.values(KRONOS_BUNDLE_COLLECTION)[0]["bundle_id"])

    def test_api_exposes_truthful_kronos_status_and_persists_unavailable_attempt(self) -> None:
        settings = AtlasSettings.from_env(
            {
                "DATABASE_URL": "sqlite://",
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "MT5_ENABLED": "true",
                "KRONOS_ENABLED": "true",
                "KRONOS_REPOSITORY_PATH": "Z:/missing/kronos",
                "KRONOS_SOURCE_REVISION": "test-revision",
                "V4_ORCHESTRATION_ENABLED": "false",
            }
        )
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        database.create_schema()

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        application = create_app(settings, database, ReadyProbe(), FakeGateway(datetime.now(UTC)))
        with TestClient(application) as client:
            status = client.get("/api/v4/kronos/status")
            forecast = client.post("/api/v4/kronos/XAUUSD/1H/forecast")
            latest = client.get("/api/v4/kronos/XAUUSD/1H/latest")

        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["enabled"])
        self.assertFalse(status.json()["provider_available"])
        self.assertEqual(status.json()["effective_weight"], 0)
        self.assertEqual(forecast.status_code, 200)
        self.assertEqual(forecast.json()["status"], "PROVIDER_UNAVAILABLE")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["forecast_id"], forecast.json()["forecast_id"])


if __name__ == "__main__":
    unittest.main()
