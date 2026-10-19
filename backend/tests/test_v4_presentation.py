from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import AtlasSettings
from app.core.store import MemoryRecordStore
from app.main import create_app
from app.models.database import Database
from app.v4.presentation import V4PresentationService
from app.v4.signal_contracts import PublicationDecision
from app.v4.signal_engine import SignalPublicationGate
from tests import test_v4_signal_engine as signal_fixtures
from tests.test_v4_market_foundation import FakeGateway


class V4PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        signal_fixtures.SignalEngineTests.setUpClass()

    def published_store(self) -> MemoryRecordStore:
        fixture = signal_fixtures.SignalEngineTests()
        store = fixture.store()
        result = SignalPublicationGate(store).evaluate(fixture.request(with_ai=True))
        self.assertEqual(result.decision, PublicationDecision.PUBLISHED)
        return store

    def test_workspace_uses_only_persisted_v4_authority(self) -> None:
        workspace = V4PresentationService(self.published_store()).workspace("XAUUSD")
        selected = next(row for row in workspace.markets if row.instrument == "XAUUSD")
        self.assertEqual(workspace.phase, 15)
        self.assertEqual(selected.availability, "AVAILABLE")
        self.assertEqual(selected.decision, PublicationDecision.PUBLISHED)
        self.assertIsNotNone(selected.signal_id)
        assert selected.best_timeframe is not None
        assert selected.grade is not None
        self.assertEqual(selected.best_timeframe.value, "1H")
        self.assertEqual(selected.grade.value, "A")
        self.assertEqual(len(selected.timeframes), 4)
        self.assertTrue(next(row for row in selected.timeframes if row.timeframe.value == "1H").selected)
        self.assertEqual(len(workspace.signals), 1)
        self.assertFalse(workspace.browser_signal_authority)
        self.assertFalse(workspace.browser_tracking_authority)
        self.assertFalse(workspace.execution_enabled)
        self.assertFalse(workspace.order_submission)

    def test_empty_workspace_is_explicitly_unavailable_without_demo_fallback(self) -> None:
        workspace = V4PresentationService(MemoryRecordStore()).workspace("EURUSD")
        self.assertEqual(len(workspace.markets), 15)
        self.assertEqual(workspace.selected_instrument, "EURUSD")
        self.assertEqual(workspace.signals, ())
        self.assertEqual(workspace.decisions, ())
        self.assertTrue(all(row.availability == "DATA_UNAVAILABLE" for row in workspace.markets))
        self.assertTrue(all(row.price is None for row in workspace.markets))
        self.assertEqual(workspace.metrics_status, "DATA_UNAVAILABLE")
        self.assertEqual(workspace.performance.status, "DATA_UNAVAILABLE")
        self.assertFalse(workspace.performance.fabricated_metrics)

    def test_corrupt_presentation_record_is_skipped_and_reported(self) -> None:
        store = MemoryRecordStore()
        store.upsert("v4_market_snapshots", "broken", {"snapshot_id": "not-valid"})
        workspace = V4PresentationService(store).workspace("XAUUSD")
        self.assertIsNotNone(next(row for row in workspace.markets if row.instrument == "XAUUSD"))
        self.assertTrue(any(issue.startswith("INVALID_V4_MARKET_SNAPSHOTS") for issue in workspace.source_issues))

    def test_phase_15_api_exposes_grounded_terminal_and_no_orders(self) -> None:
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
            terminal = api.get("/api/v4/terminal?instrument=XAUUSD")
            unsupported = api.get("/api/v4/terminal?instrument=BTCUSD")
            forbidden_order = api.post("/api/v4/orders", json={})
        self.assertEqual(terminal.status_code, 200)
        self.assertEqual(terminal.json()["phase"], 15)
        self.assertEqual(terminal.json()["metrics_status"], "DATA_UNAVAILABLE")
        self.assertFalse(terminal.json()["performance"]["fabricated_metrics"])
        self.assertEqual(terminal.json()["signals"], [])
        self.assertEqual(unsupported.status_code, 422)
        self.assertEqual(forbidden_order.status_code, 404)


if __name__ == "__main__":
    unittest.main()
