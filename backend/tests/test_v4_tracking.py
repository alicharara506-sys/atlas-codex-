from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import AtlasSettings
from app.core.store import MemoryRecordStore, stable_key
from app.main import create_app
from app.models.database import Database
from app.v4.contracts import DataQualityStatus, MarketSnapshot
from app.v4.quant_backtest import BACKTEST_COLLECTION
from app.v4.signal_contracts import CanonicalSignal
from app.v4.signal_engine import (
    PUBLICATION_ATTEMPT_COLLECTION,
    SIGNAL_COLLECTION,
    SignalPublicationGate,
)
from app.v4.snapshots import SNAPSHOT_COLLECTION
from app.v4.tracking_contracts import (
    SignalLifecycleState,
    SignalTrackingControlRequest,
    SignalTrackingRequest,
    TrackingControlAction,
    TrackingEventType,
)
from app.v4.tracking_engine import (
    TRACKING_EVENT_COLLECTION,
    TRACKING_STATE_COLLECTION,
    SignalLineageService,
    SignalTrackingEngine,
)
from tests import test_v4_signal_engine as signal_fixtures
from tests.test_v4_market_foundation import FakeGateway

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class TrackingTests(unittest.TestCase):
    base: ClassVar[MemoryRecordStore]
    signal: ClassVar[CanonicalSignal]
    source_snapshot: ClassVar[MarketSnapshot]

    @classmethod
    def setUpClass(cls) -> None:
        signal_fixtures.SignalEngineTests.setUpClass()
        fixture = signal_fixtures.SignalEngineTests()
        cls.base = fixture.store()
        for row in signal_fixtures.SignalEngineTests.base.values(BACKTEST_COLLECTION):
            cls.base.upsert(BACKTEST_COLLECTION, str(row["backtest_run_id"]), row)
        publication = SignalPublicationGate(cls.base).evaluate(fixture.request(with_ai=True))
        assert publication.signal is not None
        cls.signal = publication.signal
        cls.source_snapshot = MarketSnapshot.model_validate(
            next(
                row
                for row in cls.base.values(SNAPSHOT_COLLECTION)
                if row.get("snapshot_id") == cls.signal.lineage.market_snapshot_id
            )
        )

    def store(self) -> MemoryRecordStore:
        result = MemoryRecordStore()
        for collection in signal_fixtures.COLLECTIONS:
            for row in self.base.values(collection):
                result.upsert(collection, str(row[signal_fixtures.COLLECTION_KEYS[collection]]), row)
        for collection, key_name in (
            (BACKTEST_COLLECTION, "backtest_run_id"),
            (SIGNAL_COLLECTION, "signal_id"),
            (PUBLICATION_ATTEMPT_COLLECTION, "publication_attempt_id"),
        ):
            for row in self.base.values(collection):
                result.upsert(collection, str(row[key_name]), row)
        return result

    def snapshot(
        self,
        store: MemoryRecordStore,
        *,
        executable_price: float,
        minutes: int,
        degraded: bool = False,
    ) -> MarketSnapshot:
        spread = self.source_snapshot.spread
        if self.signal.direction.value == "LONG":
            bid = executable_price
            ask = bid + spread
        else:
            ask = executable_price
            bid = ask - spread
        timestamp = self.signal.created_at + timedelta(minutes=minutes)
        quality = self.source_snapshot.data_quality.model_copy(
            update={
                "status": DataQualityStatus.STALE if degraded else DataQualityStatus.HEALTHY,
                "publication_eligible": not degraded,
                "checked_at": timestamp,
            }
        )
        health = self.source_snapshot.health.model_copy(
            update={
                "checked_at": timestamp,
                "last_tick_at": timestamp,
                "tick_age_seconds": 0,
                "data_fresh": not degraded,
            }
        )
        last_tick = (
            self.source_snapshot.last_tick.model_copy(
                update={"timestamp": timestamp, "bid": bid, "ask": ask}
            )
            if self.source_snapshot.last_tick is not None
            else None
        )
        snapshot_id = stable_key("tracking-snapshot", timestamp.isoformat(), bid, ask, degraded)
        snapshot = self.source_snapshot.model_copy(
            update={
                "snapshot_id": snapshot_id,
                "captured_at": timestamp,
                "bid": bid,
                "ask": ask,
                "last_tick": last_tick,
                "health": health,
                "data_quality": quality,
            }
        )
        store.upsert(SNAPSHOT_COLLECTION, snapshot.snapshot_id, snapshot.model_dump(mode="json"))
        return snapshot

    def activate(self, store: MemoryRecordStore) -> SignalTrackingEngine:
        tracker = SignalTrackingEngine(store)
        entry_mid = (self.signal.entry.low + self.signal.entry.high) / 2
        fixture_price = (
            entry_mid - self.source_snapshot.spread
            if self.signal.direction.value == "LONG"
            else entry_mid + self.source_snapshot.spread
        )
        activation = self.snapshot(
            store,
            executable_price=fixture_price,
            minutes=1,
        )
        response = tracker.track(
            self.signal.signal_id,
            SignalTrackingRequest(snapshot_id=activation.snapshot_id),
        )
        self.assertEqual(response.state.state, SignalLifecycleState.ACTIVE)
        self.assertEqual(
            tuple(event.event_type for event in response.new_events),
            (TrackingEventType.PENDING_ENTRY, TrackingEventType.ACTIVE),
        )
        return tracker

    def test_acceptance_g_tp1_is_server_detected_persisted_and_reloadable(self) -> None:
        store = self.store()
        tracker = self.activate(store)
        tp1 = self.signal.targets[0].price
        snapshot = self.snapshot(store, executable_price=tp1, minutes=2)
        response = tracker.track(
            self.signal.signal_id,
            SignalTrackingRequest(snapshot_id=snapshot.snapshot_id),
        )
        self.assertEqual(response.state.state, SignalLifecycleState.TP1_HIT)
        self.assertEqual(response.state.highest_target_hit, 1)
        self.assertIn(TrackingEventType.TP1_HIT, {event.event_type for event in response.new_events})
        self.assertGreater(response.state.live_r or 0, 0)
        self.assertGreater(response.state.mfe_price, 0)
        reloaded = SignalTrackingEngine(store)
        self.assertEqual(reloaded.state(self.signal.signal_id).state, SignalLifecycleState.TP1_HIT)
        self.assertIn(
            TrackingEventType.TP1_HIT,
            {event.event_type for event in reloaded.events(self.signal.signal_id)},
        )
        duplicate = reloaded.track(
            self.signal.signal_id,
            SignalTrackingRequest(snapshot_id=snapshot.snapshot_id),
        )
        self.assertTrue(duplicate.reused_snapshot)
        self.assertEqual(duplicate.new_events, ())

    def test_stop_is_append_only_and_never_deleted(self) -> None:
        store = self.store()
        tracker = self.activate(store)
        snapshot = self.snapshot(
            store,
            executable_price=self.signal.stop_loss,
            minutes=2,
        )
        result = tracker.track(
            self.signal.signal_id,
            SignalTrackingRequest(snapshot_id=snapshot.snapshot_id),
        )
        self.assertEqual(result.state.state, SignalLifecycleState.STOPPED)
        self.assertTrue(result.state.terminal)
        self.assertIn(TrackingEventType.STOPPED, {event.event_type for event in tracker.events(self.signal.signal_id)})
        self.assertEqual(len(store.values(TRACKING_STATE_COLLECTION)), 1)
        self.assertGreaterEqual(len(store.values(TRACKING_EVENT_COLLECTION)), 3)

    def test_degraded_mt5_cancels_tracking_before_browser_can_decide(self) -> None:
        store = self.store()
        snapshot = self.snapshot(
            store,
            executable_price=(self.signal.entry.low + self.signal.entry.high) / 2,
            minutes=1,
            degraded=True,
        )
        result = SignalTrackingEngine(store).track(
            self.signal.signal_id,
            SignalTrackingRequest(snapshot_id=snapshot.snapshot_id),
        )
        self.assertEqual(result.state.state, SignalLifecycleState.DATA_CANCELLED)
        self.assertTrue(result.state.server_authoritative)
        self.assertFalse(result.state.browser_authority)
        self.assertFalse(result.state.execution_enabled)

    def test_control_is_audited_and_lineage_reconstructs_every_artifact(self) -> None:
        store = self.store()
        tracker = self.activate(store)
        result = tracker.control(
            self.signal.signal_id,
            SignalTrackingControlRequest(
                action=TrackingControlAction.INVALIDATE,
                actor="risk-operator",
                reason="Structural thesis invalidated by governed review",
            ),
        )
        self.assertEqual(result.state.state, SignalLifecycleState.INVALIDATED)
        self.assertEqual(result.new_events[0].actor, "risk-operator")
        lineage = SignalLineageService(store).reconstruct(self.signal.signal_id)
        self.assertEqual(lineage.signal.signal_id, self.signal.signal_id)
        self.assertEqual(lineage.market_snapshot.snapshot_id, self.signal.lineage.market_snapshot_id)
        self.assertEqual(lineage.primary_candidate.candidate_id, self.signal.lineage.candidate_id)
        self.assertEqual(lineage.quant_validation.quant_validation_id, self.signal.lineage.quant_validation_id)
        self.assertEqual(lineage.risk_overlay.risk_overlay_id, self.signal.lineage.risk_overlay_id)
        self.assertIsNotNone(lineage.ai_review)
        self.assertFalse(lineage.hidden_chain_of_thought_exposed)

    def test_phase_12_api_exposes_tracking_and_lineage_but_no_orders(self) -> None:
        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite://",
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "MT5_ENABLED": "false",
                "V4_AI_REVIEW_ENABLED": "false",
            },
        ):
            settings = AtlasSettings.from_env()
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway())
        database.create_schema()
        with TestClient(application) as api:
            overview = api.get("/api/v4/health")
            missing = api.post(
                f"/api/v4/signals/{'a' * 64}/track",
                json={"snapshot_id": "b" * 64},
            )
            forbidden_order = api.post("/api/v4/orders", json={})
        self.assertEqual(overview.json()["phase"], 15)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(forbidden_order.status_code, 404)


if __name__ == "__main__":
    unittest.main()
