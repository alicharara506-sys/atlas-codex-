from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.store import MemoryRecordStore
from app.v4.contracts import (
    DataQualityStatus,
    DirectionalEvent,
    FeatureQuality,
    LiquidityState,
    MarketTrend,
    OfficialTimeframe,
    V4Bar,
    VolumeProvenance,
)
from app.v4.feature_service import FEATURE_COLLECTION, FeatureComputationError, V4FeatureService
from app.v4.features import MarketStructureEngine, PivotParameters
from app.v4.snapshots import MarketSnapshotService
from tests.test_v4_market_foundation import NOW, FakeGateway


def wave_bars(values: list[float]) -> tuple[V4Bar, ...]:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    return tuple(
        V4Bar(
            timestamp=start + timedelta(hours=index),
            open=value - 0.05,
            high=value + 0.2,
            low=value - 0.2,
            close=value,
            volume=100 + index,
        )
        for index, value in enumerate(values)
    )


class MarketStructureFeatureTests(unittest.TestCase):
    def test_confirmed_pivots_produce_algorithmic_uptrend_and_bos(self) -> None:
        bars = wave_bars([100, 103, 101, 105, 102, 107, 104, 109, 106, 112])
        result = MarketStructureEngine(PivotParameters(left_bars=1, right_bars=1)).calculate(bars)
        self.assertEqual(result.trend, MarketTrend.UP)
        self.assertEqual(result.bos, DirectionalEvent.BULLISH)
        self.assertGreaterEqual(len(result.swing_highs), 3)
        self.assertGreaterEqual(len(result.swing_lows), 3)

    def test_parameters_are_validated_not_curve_fit_silently(self) -> None:
        with self.assertRaises(ValueError):
            PivotParameters(left_bars=0)
        with self.assertRaises(ValueError):
            PivotParameters(retest_tolerance_bps=500)


class V4FeatureServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryRecordStore()
        self.snapshot = MarketSnapshotService(FakeGateway(), self.store).capture("XAUUSD", now=NOW)
        self.service = V4FeatureService(self.store)

    def test_feature_set_is_versioned_complete_non_authoritative_and_idempotent(self) -> None:
        features = self.service.compute(self.snapshot, computed_at=NOW)
        same = self.service.compute(self.snapshot, computed_at=NOW + timedelta(minutes=1))

        self.assertEqual(features.feature_set_id, same.feature_set_id)
        self.assertEqual(features.computed_at, same.computed_at)
        self.assertEqual(set(features.timeframes), set(OfficialTimeframe))
        self.assertFalse(features.signal_authority)
        self.assertFalse(features.execution_enabled)
        self.assertGreater(len(features.records), 80)
        self.assertTrue(all(record.source_snapshot_id == self.snapshot.snapshot_id for record in features.records))
        self.assertTrue(all(record.evidence_family is not None for record in features.records))
        self.assertTrue(all(record.input_cutoff <= self.snapshot.captured_at for record in features.records))
        self.assertEqual(len(self.store.values(FEATURE_COLLECTION)), 1)
        self.assertEqual(self.service.latest("GOLD").feature_set_id, features.feature_set_id)  # type: ignore[union-attr]
        for summary in features.timeframes.values():
            self.assertAlmostEqual(sum(summary.regime.probabilities.values()), 1.0)
            self.assertFalse(summary.auction.global_volume_claim)
            self.assertEqual(summary.auction.volume_provenance, VolumeProvenance.BROKER_TICK_VOLUME)
        self.assertEqual(features.liquidity.evidence_level, 1)
        self.assertFalse(features.liquidity.order_book_available)
        self.assertFalse(features.liquidity.futures_volume_available)
        self.assertFalse(features.liquidity.global_institutional_volume_claim)

    def test_future_observation_is_rejected_before_feature_computation(self) -> None:
        unsafe = self.snapshot.model_copy(update={"captured_at": self.snapshot.captured_at - timedelta(days=2)})
        with self.assertRaises(FeatureComputationError):
            self.service.compute(unsafe, persist=False)

    def test_degraded_snapshot_marks_every_feature_degraded(self) -> None:
        quality = self.snapshot.data_quality.model_copy(
            update={"status": DataQualityStatus.DEGRADED, "publication_eligible": False}
        )
        degraded = self.snapshot.model_copy(update={"snapshot_id": "a" * 64, "data_quality": quality})
        features = self.service.compute(degraded, computed_at=NOW, persist=False)
        self.assertEqual(features.input_quality, DataQualityStatus.DEGRADED)
        self.assertTrue(all(record.quality is FeatureQuality.DEGRADED for record in features.records))

    def test_volume_provenance_is_explicit_and_not_called_exchange_volume(self) -> None:
        features = self.service.compute(self.snapshot, computed_at=NOW)
        records = [record for record in features.records if record.name == "volume.provenance"]
        self.assertEqual(len(records), 4)
        self.assertTrue(all(record.value == "BROKER_TICK_VOLUME" for record in records))

    def test_liquidity_is_unavailable_without_a_measurable_tick_window(self) -> None:
        snapshot = self.snapshot.model_copy(update={"first_tick_at": None, "last_tick": None, "tick_count": 0})
        features = self.service.compute(snapshot, computed_at=NOW, persist=False)
        self.assertEqual(features.liquidity.state, LiquidityState.UNAVAILABLE)


class V4FeatureApiTests(unittest.TestCase):
    def test_feature_api_persists_lineage_without_direction_or_execution(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database, SqlModelRecordStore

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        env = {
            "DATABASE_URL": "sqlite://",
            "REDIS_URL": "redis://127.0.0.1:6379/0",
            "MT5_ENABLED": "true",
            "MT5_BRIDGE_BASE_URL": "http://127.0.0.1:8765",
            "MT5_EXPECTED_ACCOUNT_MODE": "DEMO",
        }
        with patch.dict(os.environ, env):
            settings = AtlasSettings.from_env()
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway(datetime.now(UTC)))
        database.create_schema()

        with TestClient(application) as client:
            computed = client.post("/api/v4/features/XAUUSD/compute")
            latest = client.get("/api/v4/features/XAUUSD/latest")

        self.assertEqual(computed.status_code, 200)
        body = computed.json()
        self.assertEqual(body["feature_set_id"], latest.json()["feature_set_id"])
        self.assertFalse(body["signal_authority"])
        self.assertFalse(body["execution_enabled"])
        self.assertNotIn("direction", body)
        self.assertEqual(body["source_snapshot_id"], body["records"][0]["source_snapshot_id"])
        self.assertEqual(len(SqlModelRecordStore(database).values(FEATURE_COLLECTION)), 1)

        with TestClient(application) as client:
            overview = client.get("/api/v4/health").json()
        self.assertEqual(overview["phase"], 15)
        self.assertFalse(overview["experimental_elliott_wave_enabled"])
        self.assertFalse(overview["experimental_fibonacci_enabled"])


if __name__ == "__main__":
    unittest.main()
