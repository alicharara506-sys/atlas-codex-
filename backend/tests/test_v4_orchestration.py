from __future__ import annotations

import asyncio
import math
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import AtlasSettings
from app.core.store import MemoryRecordStore
from app.main import create_app
from app.models.database import Database
from app.v4.context_ingestion import V4ContextIngestionService
from app.v4.context_service import CONTEXT_COLLECTION, V4ContextService
from app.v4.contracts import OfficialTimeframe
from app.v4.feature_service import FEATURE_COLLECTION, V4FeatureService
from app.v4.historical_ml import HistoricalPrimaryDatasetBuilder
from app.v4.ml_contracts import ModelGovernanceStatus
from app.v4.mt5 import GatewayError
from app.v4.orchestration import V4OrchestrationService
from app.v4.orchestration_contracts import OrchestrationRunState
from app.v4.primary_ml import PrimaryMLEngine, PrimaryMLTrainer, PrimaryModelRegistry
from app.v4.risk_engine import RISK_OVERLAY_COLLECTION
from app.v4.robustness import RobustnessValidator
from app.v4.signal_engine import SignalPublicationGate
from app.v4.snapshots import SNAPSHOT_COLLECTION, MarketSnapshotService
from tests.test_v4_market_foundation import NOW, TIMEFRAME_SECONDS, FakeGateway


class OscillatingGateway(FakeGateway):
    def rates(self, symbol: str, timeframe: str, bars: int) -> dict[str, Any]:
        seconds = TIMEFRAME_SECONDS[timeframe]
        count = min(bars, 260)
        latest = int(self.now.timestamp()) - seconds
        first = latest - seconds * (count - 1)
        rows: list[dict[str, float]] = []
        for index in range(count):
            trend = index * 0.018
            wave = math.sin(index / 3.2) * 2.4 + math.sin(index / 11) * 1.3
            close = 4_300 + trend + wave
            open_price = close - math.sin(index / 2.1) * 0.45
            rows.append(
                {
                    "time": float(first + seconds * index),
                    "open": open_price,
                    "high": max(open_price, close) + 0.35,
                    "low": min(open_price, close) - 0.35,
                    "close": close,
                    "volume": 120 + (index % 17) * 4,
                }
            )
        return {
            "symbol": symbol,
            "brokerSymbol": self.broker_symbol,
            "timeframe": timeframe,
            "bars": rows,
        }


class PartiallyFailingGateway(FakeGateway):
    def quote(self, symbol: str) -> dict[str, Any]:
        if symbol == "EURUSD":
            raise GatewayError("SYMBOL_QUOTE_UNAVAILABLE", "EURUSD quote unavailable")
        return super().quote(symbol)


def service(
    gateway: FakeGateway,
    store: MemoryRecordStore,
    *,
    instruments: tuple[str, ...] = ("XAUUSD",),
    train_models: bool = False,
) -> V4OrchestrationService:
    snapshots = MarketSnapshotService(gateway, store, enabled=True)
    features = V4FeatureService(store)
    contexts = V4ContextService(store)
    registry = PrimaryModelRegistry(store)
    return V4OrchestrationService(
        store,
        snapshots,
        features,
        contexts,
        V4ContextIngestionService(store, contexts),
        PrimaryMLTrainer(store),
        registry,
        PrimaryMLEngine(registry),
        RobustnessValidator(store),
        SignalPublicationGate(store),
        enabled=True,
        interval_seconds=300,
        instruments=instruments,
        context_refresh_enabled=False,
        train_models=train_models,
        clock=lambda: NOW,
    )


class HistoricalPrimaryDatasetTests(unittest.TestCase):
    def test_training_is_chronological_and_stays_experimental(self) -> None:
        store = MemoryRecordStore()
        snapshot = MarketSnapshotService(OscillatingGateway(), store, enabled=True).capture("XAUUSD", now=NOW)
        dataset = HistoricalPrimaryDatasetBuilder().build(snapshot)
        request = dataset.request
        self.assertLess(max(row.label_end_at for row in request.train), request.validation[0].observed_at)
        self.assertLess(max(row.label_end_at for row in request.validation), request.test[0].observed_at)
        self.assertGreaterEqual(len(request.test), 30)

        result = PrimaryMLTrainer(store).train(
            request.train,
            request.validation,
            request.test,
            request.config,
        )
        registry = PrimaryModelRegistry(store)
        status = registry.current_status(result.validation_winner_model_id)
        self.assertEqual(status.status, ModelGovernanceStatus.EXPERIMENTAL)
        self.assertFalse(result.winner_automatically_promoted)
        self.assertTrue(all(not artifact.execution_enabled for artifact in result.artifacts))


class OrchestrationServiceTests(unittest.TestCase):
    def test_each_timeframe_meta_model_is_prepared_once_per_universe_cycle(self) -> None:
        store = MemoryRecordStore()
        worker = service(
            PartiallyFailingGateway(),
            store,
            instruments=("XAUUSD", "EURUSD"),
        )
        with patch.object(
            worker.historical_meta,
            "train_pooled",
            side_effect=ValueError("pooled evidence is not mature"),
        ) as train:
            asyncio.run(worker.run_once())

        self.assertEqual(train.call_count, len(OfficialTimeframe))
        self.assertEqual(
            {call.args[0] for call in train.call_args_list},
            set(OfficialTimeframe),
        )

    def test_each_instrument_uses_a_fresh_observation_time(self) -> None:
        store = MemoryRecordStore()
        quote_at = NOW + timedelta(minutes=5)
        worker = service(FakeGateway(quote_at), store)
        moments = iter((NOW, quote_at, quote_at + timedelta(seconds=1)))
        worker.clock = lambda: next(moments)

        run = asyncio.run(worker.run_once())

        snapshot = store.values(SNAPSHOT_COLLECTION)[0]
        self.assertEqual(run.instruments[0].snapshot_id, snapshot["snapshot_id"])
        self.assertEqual(snapshot["data_quality"]["status"], "HEALTHY")
        self.assertNotIn(
            "DATA_NOT_PUBLICATION_ELIGIBLE",
            run.instruments[0].blocker_codes,
        )

    def test_cycle_persists_idempotent_evidence_and_exact_blockers(self) -> None:
        store = MemoryRecordStore()
        worker = service(FakeGateway(), store)
        first = asyncio.run(worker.run_once())
        second = asyncio.run(worker.run_once())

        self.assertEqual(first.state, OrchestrationRunState.COMPLETED_WITH_BLOCKERS)
        result = first.instruments[0]
        self.assertIsNotNone(result.snapshot_id)
        self.assertIsNotNone(result.feature_set_id)
        self.assertIsNotNone(result.context_set_id)
        self.assertEqual(
            result.blocker_codes,
            (
                "MODEL_TRAINING_DISABLED",
                "PRIMARY_CANDIDATE_INPUT_UNAVAILABLE",
                "AI_REVIEW_AWAITS_SHADOW_CANDIDATE",
                "META_TRAINING_EVIDENCE_INSUFFICIENT",
                "CALIBRATION_AWAITS_META_PREDICTION",
                "QUANT_INPUT_EVIDENCE_PENDING",
                "RISK_AWAITS_QUANT_EVIDENCE",
                "PUBLICATION_EVIDENCE_PENDING",
            ),
        )
        self.assertFalse(first.execution_enabled)
        self.assertFalse(first.order_submission)
        self.assertEqual(first.instruments[0].snapshot_id, second.instruments[0].snapshot_id)
        self.assertEqual(first.instruments[0].feature_set_id, second.instruments[0].feature_set_id)
        self.assertEqual(len(store.values(SNAPSHOT_COLLECTION)), 1)
        self.assertEqual(len(store.values(FEATURE_COLLECTION)), 1)
        self.assertEqual(len(store.values(CONTEXT_COLLECTION)), 1)
        self.assertEqual(store.values(RISK_OVERLAY_COLLECTION), [])
        status = worker.status()
        self.assertEqual(status.cycles_completed, 2)
        self.assertIsNone(status.current_instrument)
        self.assertIsNone(status.current_stage)
        self.assertEqual(status.instruments_completed, 1)
        self.assertEqual(status.instruments_total, 1)

    def test_one_instrument_failure_does_not_stop_other_markets(self) -> None:
        store = MemoryRecordStore()
        worker = service(
            PartiallyFailingGateway(),
            store,
            instruments=("XAUUSD", "EURUSD"),
        )
        run = asyncio.run(worker.run_once())
        by_symbol = {result.instrument: result for result in run.instruments}
        self.assertEqual(by_symbol["EURUSD"].state, OrchestrationRunState.FAILED)
        self.assertEqual(
            by_symbol["XAUUSD"].state,
            OrchestrationRunState.COMPLETED_WITH_BLOCKERS,
        )
        self.assertIsNotNone(by_symbol["XAUUSD"].feature_set_id)
        self.assertEqual(run.failed_instruments, 1)
        self.assertFalse(run.order_submission)

    def test_api_exposes_worker_status_but_no_order_submission(self) -> None:
        settings = AtlasSettings.from_env(
            {
                "DATABASE_URL": "sqlite://",
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "MT5_ENABLED": "false",
                "V4_ORCHESTRATION_ENABLED": "false",
            }
        )
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        database.create_schema()

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        application = create_app(settings, database, ReadyProbe(), FakeGateway())
        with TestClient(application) as client:
            status = client.get("/api/v4/orchestration/status")
            manual = client.post("/api/v4/orchestration/run")
            orders = client.post("/api/v4/orders", json={})
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["enabled"])
        self.assertFalse(status.json()["execution_enabled"])
        self.assertEqual(manual.status_code, 503)
        self.assertEqual(orders.status_code, 404)


if __name__ == "__main__":
    unittest.main()
