from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlmodel import Session, select

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import AtlasSettings, ConfigurationError
from app.main import create_app
from app.models.database import Database
from app.models.tables import IngestionRecord
from tests.test_v4_market_foundation import FakeGateway


class ReadyProbe:
    def healthy(self) -> bool:
        return True


class FailedProbe:
    def healthy(self) -> bool:
        return False


class Phase15SecurityTests(unittest.TestCase):
    def app(self, **overrides: str) -> tuple[object, Database]:
        env = {
            "DATABASE_URL": "sqlite://",
            "REDIS_URL": "redis://127.0.0.1:6379/0",
            "MT5_ENABLED": "true",
            **overrides,
        }
        settings = AtlasSettings.from_env(env)
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway(datetime.now(UTC)))
        database.create_schema()
        return application, database

    def test_secret_values_are_redacted_from_configuration_repr(self) -> None:
        secret = "s" * 32
        settings = AtlasSettings.from_env(
            {
                "DATABASE_URL": f"postgresql+psycopg://atlas:{secret}@127.0.0.1/atlas",
                "REDIS_URL": f"redis://:{secret}@127.0.0.1/0",
                "OPENAI_API_KEY": secret,
                "OPENAI_MODEL": "local-test",
                "MT5_BRIDGE_TOKEN": secret,
                "FRED_API_KEY": secret,
                "ATLAS_ADMIN_TOKEN": secret,
            }
        )
        self.assertNotIn(secret, repr(settings))
        self.assertNotIn(secret, repr(settings.providers["openai"]))
        with self.assertRaises(ConfigurationError):
            AtlasSettings.from_env({"ATLAS_ADMIN_TOKEN": "too-short"})

    def test_local_boundary_security_headers_and_size_limit(self) -> None:
        application, _ = self.app(ATLAS_MAX_REQUEST_BYTES="65536")
        with TestClient(application) as client:
            health = client.get("/api/v4/health")
            oversized = client.post("/api/v4/market/XAUUSD/snapshot", content=b"x" * 65_537)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.headers["x-atlas-execution"], "disabled")
        self.assertEqual(health.headers["x-frame-options"], "DENY")
        self.assertEqual(health.headers["cache-control"], "no-store")
        self.assertEqual(oversized.status_code, 413)
        self.assertFalse(oversized.json()["execution_enabled"])

    def test_v4_mutations_require_token_and_are_rate_limited(self) -> None:
        token = "a" * 32
        application, _ = self.app(ATLAS_ADMIN_TOKEN=token, ATLAS_MUTATION_RATE_LIMIT="1")
        with TestClient(application) as client:
            rejected = client.post("/api/v4/market/XAUUSD/snapshot")
            accepted = client.post(
                "/api/v4/market/XAUUSD/snapshot",
                headers={"X-Atlas-Admin-Token": token},
            )
            limited = client.post(
                "/api/v4/market/XAUUSD/snapshot",
                headers={"X-Atlas-Admin-Token": token},
            )
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(limited.status_code, 429)

    def test_non_loopback_clients_are_rejected(self) -> None:
        application, _ = self.app()
        with TestClient(application, client=("198.51.100.7", 50_000)) as client:
            response = client.get("/api/v4/health")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "LOCAL_ACCESS_REQUIRED")


class Phase15OperationalHealthTests(unittest.TestCase):
    def test_operational_health_is_truthful_and_never_enables_execution(self) -> None:
        settings = AtlasSettings.from_env(
            {
                "DATABASE_URL": "sqlite://",
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "MT5_ENABLED": "true",
                "V4_AI_REVIEW_ENABLED": "false",
            }
        )
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway(datetime.now(UTC)))
        database.create_schema()
        with TestClient(application) as client:
            before = client.get("/api/v4/health/operational?instrument=XAUUSD")
            client.post("/api/v4/market/XAUUSD/snapshot")
            after = client.get("/api/v4/health/operational?instrument=XAUUSD")
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.json()["phase"], 15)
        self.assertEqual(before.json()["data"]["state"], "UNAVAILABLE")
        self.assertEqual(after.json()["data"]["state"], "HEALTHY")
        self.assertEqual(after.json()["mt5"]["state"], "HEALTHY")
        self.assertEqual(after.json()["llm"]["state"], "DISABLED")
        self.assertTrue(after.json()["analytical_signal_authority"])
        self.assertFalse(after.json()["browser_signal_authority"])
        self.assertFalse(after.json()["execution_enabled"])
        self.assertFalse(after.json()["order_submission"])

    def test_failed_required_infrastructure_is_not_ready(self) -> None:
        settings = AtlasSettings.from_env({"DATABASE_URL": "sqlite://", "MT5_ENABLED": "false"})
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, FailedProbe(), FakeGateway())
        database.create_schema()
        with TestClient(application) as client:
            response = client.get("/api/v4/health/operational")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "NOT_READY")
        self.assertEqual(response.json()["redis"]["state"], "UNAVAILABLE")


class Phase15MigrationTests(unittest.TestCase):
    def test_baseline_upgrade_is_idempotent_and_preserves_existing_data(self) -> None:
        with TemporaryDirectory() as folder:
            database_path = Path(folder) / "atlas.sqlite"
            url = f"sqlite:///{database_path.as_posix()}"
            config = Config(str(BACKEND / "alembic.ini"))
            config.set_main_option("sqlalchemy.url", url)
            with patch.dict(os.environ, {"DATABASE_URL": url}):
                command.upgrade(config, "head")
                database = Database(url)
                try:
                    expected = {
                        "funds",
                        "holdings",
                        "assets",
                        "price_bars",
                        "sentiment_snapshots",
                        "mandates",
                        "runs",
                        "decisions",
                        "ingestion_records",
                        "alembic_version",
                    }
                    self.assertTrue(expected.issubset(set(inspect(database.engine).get_table_names())))
                    with Session(database.engine) as session:
                        session.add(
                            IngestionRecord(collection="phase15", natural_key="proof", payload_json={"ok": True})
                        )
                        session.commit()
                    command.upgrade(config, "head")
                    with database.engine.connect() as connection:
                        context = MigrationContext.configure(connection)
                        current = context.get_current_revision()
                    self.assertEqual(current, ScriptDirectory.from_config(config).get_current_head())
                    with self.assertRaisesRegex(RuntimeError, "downgrade is intentionally disabled"):
                        command.downgrade(config, "base")
                    with Session(database.engine) as session:
                        rows = session.exec(select(IngestionRecord)).all()
                    self.assertEqual([(row.collection, row.natural_key) for row in rows], [("phase15", "proof")])
                finally:
                    database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
