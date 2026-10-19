from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HAS_API_STACK = all(importlib.util.find_spec(package) is not None for package in ("fastapi", "sqlmodel"))


class SystemApiTests(unittest.TestCase):
    def test_health_and_readiness_are_typed_and_fail_closed(self) -> None:
        if not HAS_API_STACK:
            self.skipTest("FastAPI and SQLModel are not installed in the offline runtime")

        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "REDIS_URL": "redis://127.0.0.1:6379/0"}):
            settings = AtlasSettings.from_env()
            database = Database(settings.database_url)
            self.addCleanup(database.engine.dispose)
            application = create_app(settings, database, ReadyProbe())
            application.state.database.create_schema()

        with TestClient(application) as client:
            health = client.get("/health")
            ready = client.get("/ready")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["phase"], 3)
        self.assertTrue(health.json()["agent_engine_enabled"])
        self.assertTrue(health.json()["mandate_engine_enabled"])
        self.assertTrue(health.json()["backtest_engine_enabled"])
        self.assertFalse(health.json()["execution_enabled"])
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["status"], "ready")
        self.assertTrue(ready.json()["redis"])

    def test_research_endpoint_is_typed_persisted_and_non_executable(self) -> None:
        if not HAS_API_STACK:
            self.skipTest("FastAPI and SQLModel are not installed in the offline runtime")

        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database, SqlModelRecordStore

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "REDIS_URL": "redis://127.0.0.1:6379/0"}):
            settings = AtlasSettings.from_env()
            database = Database(settings.database_url)
            self.addCleanup(database.engine.dispose)
            application = create_app(settings, database, ReadyProbe())
            database.create_schema()

        payload = {
            "observations": [
                {
                    "ticker": "XAUUSD",
                    "asset_class": "commodity",
                    "fundamentals_score": 0.8,
                    "technical_score": 0.9,
                    "sentiment_score": 0.7,
                    "macro_score": 0.6,
                    "narrative_score": 0.8,
                    "flow_score": 0.7,
                    "insider_score": 0.4,
                    "volatility_percentile": 0.4,
                    "correlation_to_book": 0.2,
                    "requested_weight": 0.05,
                }
            ],
            "limits": {"allowed_universe": ["XAUUSD"]},
        }
        with TestClient(application) as client:
            response = client.post("/research/evaluate", json=payload)
            history = client.get("/research/runs")
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["phase"], 2)
        self.assertTrue(body["research_only"])
        self.assertFalse(body["execution_enabled"])
        self.assertEqual(body["state"]["execution_plan"], [])
        self.assertEqual(len(SqlModelRecordStore(database).values("agentic_research_runs")), 1)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["count"], 1)
        self.assertEqual(history.json()["runs"][0]["universe"], ["XAUUSD"])
        self.assertEqual(history.json()["runs"][0]["state"]["execution_plan"], [])
        self.assertFalse(history.json()["execution_enabled"])


if __name__ == "__main__":
    unittest.main()
