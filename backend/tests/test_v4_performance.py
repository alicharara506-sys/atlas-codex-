from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.store import MemoryRecordStore
from app.v4.performance import V4PerformanceService
from app.v4.quant_backtest import BACKTEST_COLLECTION
from app.v4.signal_contracts import PublicationDecision
from app.v4.signal_engine import SignalPublicationGate
from tests import test_v4_quant_validation as quant_fixtures
from tests import test_v4_signal_engine as signal_fixtures


class V4PerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        signal_fixtures.SignalEngineTests.setUpClass()
        quant_fixtures.QuantValidationTests.setUpClass()

    def published_store(self) -> MemoryRecordStore:
        fixture = signal_fixtures.SignalEngineTests()
        store = fixture.store()
        result = SignalPublicationGate(store).evaluate(fixture.request(with_ai=True))
        self.assertEqual(result.decision, PublicationDecision.PUBLISHED)
        return store

    def test_empty_dashboard_never_fabricates_performance(self) -> None:
        dashboard = V4PerformanceService(MemoryRecordStore()).dashboard()
        self.assertEqual(dashboard.status, "DATA_UNAVAILABLE")
        self.assertEqual(dashboard.performance_slices, ())
        self.assertEqual(dashboard.backtests, ())
        self.assertEqual(dashboard.vetoes.attempt_count, 0)
        self.assertTrue(dashboard.all_metrics_traceable)
        self.assertFalse(dashboard.fabricated_metrics)
        self.assertFalse(dashboard.browser_calculation_authority)
        self.assertFalse(dashboard.execution_enabled)

    def test_published_evidence_exposes_traceable_partial_metrics(self) -> None:
        dashboard = V4PerformanceService(self.published_store()).dashboard()
        self.assertEqual(dashboard.status, "PARTIAL")
        self.assertEqual(len(dashboard.calibrations), 1)
        calibration = dashboard.calibrations[0]
        self.assertEqual(calibration.sample_count, 200)
        self.assertEqual(calibration.evaluation_sample_count, 60)
        self.assertEqual(len(calibration.source_validation_run_id), 64)
        self.assertEqual(sum(row.sample_count for row in calibration.reliability_bins), 200)
        aggregate = next(row for row in dashboard.performance_slices if row.dimension == "ALL")
        self.assertEqual(aggregate.published_count, 1)
        self.assertEqual(aggregate.resolved_count, 0)
        self.assertEqual(len(aggregate.signal_ids), 1)
        self.assertEqual(dashboard.vetoes.decision_counts["PUBLISHED"], 1)
        self.assertEqual(len(dashboard.vetoes.publication_attempt_ids), 1)

    def test_backtest_and_calibration_make_metrics_available_with_cost_trace(self) -> None:
        store = self.published_store()
        backtest = quant_fixtures.QuantValidationTests.backtest
        store.upsert(
            BACKTEST_COLLECTION,
            backtest.backtest_run_id,
            backtest.model_dump(mode="json"),
        )
        dashboard = V4PerformanceService(store).dashboard()
        self.assertEqual(dashboard.status, "AVAILABLE")
        self.assertEqual(len(dashboard.backtests), 1)
        item = dashboard.backtests[0]
        self.assertEqual(item.backtest_run_id, backtest.backtest_run_id)
        self.assertEqual(item.sample_count, 40)
        self.assertAlmostEqual(item.mean_gross_return or 0, 0.0025)
        self.assertAlmostEqual(item.cost_drag or 0, 0.0005)
        self.assertAlmostEqual(item.cumulative_net_return, 0.08)

    def test_invalid_persisted_metric_is_omitted_and_reported(self) -> None:
        store = MemoryRecordStore()
        store.upsert(BACKTEST_COLLECTION, "broken", {"backtest_run_id": "invalid"})
        dashboard = V4PerformanceService(store).dashboard()
        self.assertEqual(dashboard.status, "DATA_UNAVAILABLE")
        self.assertEqual(dashboard.backtests, ())
        self.assertIn("INVALID_V4_QUANT_BACKTESTS_ROW_0", dashboard.source_issues)


if __name__ == "__main__":
    unittest.main()
