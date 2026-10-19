from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.models.partitioning import add_months, postgres_partition_ddl, price_partition_spec


class PartitioningTests(unittest.TestCase):
    def test_month_arithmetic_crosses_year_boundary(self) -> None:
        value = datetime(2026, 12, 20, 10, 30, tzinfo=UTC)
        self.assertEqual(add_months(value, 1), datetime(2027, 1, 1, tzinfo=UTC))
        self.assertEqual(add_months(value, -12), datetime(2025, 12, 1, tzinfo=UTC))

    def test_partition_ddl_is_deterministic_and_bounded(self) -> None:
        spec = price_partition_spec(datetime(2026, 8, 14, tzinfo=UTC))
        self.assertEqual(spec.name, "price_bars_2026_08")
        ddl = postgres_partition_ddl(spec)
        self.assertIn("PARTITION OF price_bars", ddl)
        self.assertIn("2026-08-01T00:00:00+00:00", ddl)
        self.assertIn("2026-09-01T00:00:00+00:00", ddl)


if __name__ == "__main__":
    unittest.main()
