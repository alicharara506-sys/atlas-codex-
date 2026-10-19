from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HAS_SQLMODEL = importlib.util.find_spec("sqlmodel") is not None


class DatabaseIntegrationTests(unittest.TestCase):
    def test_schema_and_record_store_are_idempotent(self) -> None:
        if not HAS_SQLMODEL:
            self.skipTest("sqlmodel is not installed in the offline runtime")

        from app.models.database import Database, SqlModelRecordStore

        database = Database(os.environ.get("ATLAS_TEST_DATABASE_URL", "sqlite://"))
        self.addCleanup(database.engine.dispose)
        database.create_schema()
        self.assertTrue(database.healthy())

        store = SqlModelRecordStore(database)
        collection = f"test_price_bars_{uuid4().hex}"
        self.assertTrue(store.upsert(collection, "stable-key", {"ticker": "ABC", "close": 12.5}).inserted)
        self.assertFalse(store.upsert(collection, "stable-key", {"ticker": "ABC", "close": 13.0}).inserted)
        self.assertEqual(store.values(collection), [{"ticker": "ABC", "close": 13.0}])

    def test_record_store_supports_bounded_and_latest_group_reads(self) -> None:
        if not HAS_SQLMODEL:
            self.skipTest("sqlmodel is not installed in the offline runtime")

        from app.models.database import Database, SqlModelRecordStore

        database = Database("sqlite://")
        self.addCleanup(database.engine.dispose)
        database.create_schema()
        store = SqlModelRecordStore(database)
        collection = f"test_latest_{uuid4().hex}"
        store.upsert(collection, "a-old", {"instrument": "A", "captured_at": "2026-01-01T00:00:00Z"})
        store.upsert(collection, "b", {"instrument": "B", "captured_at": "2026-01-02T00:00:00Z"})
        store.upsert(collection, "a-new", {"instrument": "A", "captured_at": "2026-01-03T00:00:00Z"})

        self.assertEqual(len(store.recent_values(collection, 2)), 2)
        latest = store.latest_values(collection, "instrument", "captured_at")
        self.assertEqual(len(latest), 2)
        self.assertEqual(
            next(row for row in latest if row["instrument"] == "A")["captured_at"],
            "2026-01-03T00:00:00Z",
        )


if __name__ == "__main__":
    unittest.main()
