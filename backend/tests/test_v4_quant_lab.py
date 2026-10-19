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
from app.v4.contracts import V4Bar
from app.v4.labeling import TripleBarrierLabeler
from app.v4.quant_backtest import BACKTEST_COLLECTION, V4BacktestEngine
from app.v4.quant_contracts import (
    BacktestExitReason,
    BacktestTradePlan,
    BarrierOrderPolicy,
    CostModel,
    ExperimentSpec,
    ResearchDirection,
    RobustnessStatus,
    TripleBarrierConfig,
    TripleBarrierLabel,
    ValidationSample,
    WalkForwardConfig,
    WalkForwardMode,
)
from app.v4.robustness import RobustnessValidator
from app.v4.validation import (
    EXPERIMENT_COLLECTION,
    HOLDOUT_ACCESS_COLLECTION,
    ExperimentRegistry,
    HoldoutVault,
    WalkForwardValidator,
)

NOW = datetime(2026, 8, 19, tzinfo=UTC)


def research_bars(count: int = 80) -> tuple[V4Bar, ...]:
    return tuple(
        V4Bar(
            timestamp=NOW + timedelta(hours=index),
            open=100 + index * 0.1,
            high=100.5 + index * 0.1,
            low=99.5 + index * 0.1,
            close=100.1 + index * 0.1,
            volume=100 + index,
        )
        for index in range(count)
    )


def validation_samples(count: int = 100) -> tuple[ValidationSample, ...]:
    return tuple(
        ValidationSample(
            sample_id=f"sample-{index}",
            observed_at=NOW + timedelta(days=index),
            label_end_at=NOW + timedelta(days=index + 3),
            feature_hash=f"feature-{index}",
        )
        for index in range(count)
    )


class TripleBarrierTests(unittest.TestCase):
    def test_barriers_use_only_signal_history_and_entry_is_next_bar(self) -> None:
        bars = research_bars()
        config = TripleBarrierConfig(
            atr_lookback=5,
            structure_lookback=5,
            max_holding_bars=5,
            target_atr_multiple=1,
            stop_atr_multiple=1,
            use_structure=False,
        )
        baseline = TripleBarrierLabeler().label("XAUUSD", bars, ResearchDirection.LONG, config)
        first = baseline.labels[0]
        self.assertEqual(first.signal_at, bars[5].timestamp)
        self.assertEqual(first.input_cutoff, bars[5].timestamp)
        self.assertEqual(first.entry_at, bars[6].timestamp)
        self.assertFalse(first.future_data_used_for_barriers)

        changed = list(bars)
        changed[7] = changed[7].model_copy(update={"high": 150.0, "close": 149.5})
        future_changed = TripleBarrierLabeler().label("XAUUSD", tuple(changed), ResearchDirection.LONG, config)
        self.assertEqual(first.upper_barrier, future_changed.labels[0].upper_barrier)
        self.assertEqual(first.lower_barrier, future_changed.labels[0].lower_barrier)

    def test_same_bar_collision_uses_explicit_conservative_policy(self) -> None:
        bars = list(research_bars())
        bars[6] = bars[6].model_copy(update={"high": 110.0, "low": 90.0})
        config = TripleBarrierConfig(
            atr_lookback=5,
            structure_lookback=5,
            max_holding_bars=5,
            target_atr_multiple=1,
            stop_atr_multiple=1,
            use_structure=False,
            barrier_order_policy=BarrierOrderPolicy.STOP_FIRST,
        )
        first = TripleBarrierLabeler().label("XAUUSD", tuple(bars), ResearchDirection.LONG, config).labels[0]
        self.assertTrue(first.same_bar_collision)
        self.assertEqual(first.label, TripleBarrierLabel.LONG_LOSS)


class WalkForwardAndHoldoutTests(unittest.TestCase):
    def test_walk_forward_purges_label_overlap_and_embargoes_boundaries(self) -> None:
        samples = validation_samples()
        config = WalkForwardConfig(
            train_size=30,
            validation_size=10,
            test_size=10,
            step_size=10,
            embargo_size=2,
            mode=WalkForwardMode.EXPANDING,
        )
        result = WalkForwardValidator().split(samples, config)
        first = result.folds[0]
        self.assertTrue(first.purged_train_ids)
        self.assertTrue(first.purged_validation_ids)
        self.assertEqual(len(first.embargoed_ids), 4)
        lookup = {sample.sample_id: sample for sample in samples}
        self.assertTrue(all(lookup[sample_id].label_end_at < first.validation_start for sample_id in first.train_ids))
        self.assertTrue(all(lookup[sample_id].label_end_at < first.test_start for sample_id in first.validation_ids))
        self.assertFalse(result.ordinary_shuffled_kfold_used)
        self.assertTrue(result.purging_enabled)
        self.assertFalse(result.production_promotion)

    def test_rolling_mode_and_invalid_chronology_fail_closed(self) -> None:
        samples = validation_samples()
        result = WalkForwardValidator().split(
            samples,
            WalkForwardConfig(
                train_size=30,
                validation_size=10,
                test_size=10,
                step_size=10,
                embargo_size=1,
                mode=WalkForwardMode.ROLLING,
            ),
        )
        self.assertGreater(len(result.folds), 1)
        with self.assertRaises(ValueError):
            WalkForwardValidator().split(tuple(reversed(samples)), result.config)

    def test_holdout_is_sealed_and_only_one_confirmed_final_access_is_logged(self) -> None:
        store = MemoryRecordStore()
        vault = HoldoutVault(store)
        development, manifest = vault.seal(validation_samples(), holdout_size=10, created_at=NOW)
        self.assertEqual(len(development), 90)
        self.assertFalse(manifest.development_access_allowed)
        with self.assertRaises(PermissionError):
            vault.open_final_evaluation(manifest.holdout_id, experiment_id="release-1", confirmed=False)
        holdout, access = vault.open_final_evaluation(
            manifest.holdout_id,
            experiment_id="release-1",
            confirmed=True,
            accessed_at=NOW,
        )
        self.assertEqual(len(holdout), 10)
        self.assertFalse(access.production_promotion)
        self.assertEqual(len(store.values(HOLDOUT_ACCESS_COLLECTION)), 1)
        with self.assertRaises(PermissionError):
            vault.open_final_evaluation(manifest.holdout_id, experiment_id="release-2", confirmed=True)

    def test_experiment_registry_counts_trials_and_cannot_promote(self) -> None:
        store = MemoryRecordStore()
        registry = ExperimentRegistry(store)
        spec = ExperimentSpec(
            name="Elliott ON versus OFF",
            feature_sets=("base", "elliott"),
            model_families=("logistic", "extra_trees"),
            parameter_variants=3,
            threshold_variants=4,
        )
        first = registry.register(spec, registered_at=NOW)
        second = registry.register(spec, registered_at=NOW + timedelta(days=1))
        self.assertEqual(first.experiment_id, second.experiment_id)
        self.assertEqual(first.total_variants, 48)
        self.assertFalse(first.automatic_promotion)
        self.assertEqual(len(store.values(EXPERIMENT_COLLECTION)), 1)


class BacktestAndRobustnessTests(unittest.TestCase):
    def test_backtest_is_deterministic_cost_aware_and_non_executable(self) -> None:
        start = NOW
        bars = (
            V4Bar(timestamp=start, open=100, high=100.5, low=99.5, close=100, volume=100),
            V4Bar(timestamp=start + timedelta(hours=1), open=100, high=100.5, low=99.5, close=100, volume=100),
            V4Bar(timestamp=start + timedelta(hours=2), open=100.2, high=103, low=99.8, close=102, volume=100),
            V4Bar(timestamp=start + timedelta(hours=3), open=102, high=102.5, low=101.5, close=102, volume=100),
        )
        plan = BacktestTradePlan(
            trade_id="long-1",
            instrument="XAUUSD",
            direction=ResearchDirection.LONG,
            signal_at=start,
            stop_loss=98,
            take_profit=102,
            max_holding_bars=3,
        )
        costs = CostModel(spread_bps=4, slippage_bps=1, commission_bps_round_turn=2)
        store = MemoryRecordStore()
        engine = V4BacktestEngine(store)
        first = engine.run("XAUUSD", bars, (plan,), costs)
        second = engine.run("XAUUSD", bars, (plan,), costs)
        trade = first.trades[0]
        self.assertEqual(first.backtest_run_id, second.backtest_run_id)
        self.assertEqual(len(store.values(BACKTEST_COLLECTION)), 1)
        self.assertEqual(trade.exit_reason, BacktestExitReason.TARGET)
        self.assertLess(trade.net_return, trade.gross_return)
        self.assertTrue(first.deterministic)
        self.assertFalse(first.signal_authority)
        self.assertFalse(first.execution_enabled)

    def test_same_bar_backtest_collision_is_stop_first(self) -> None:
        bars = (
            V4Bar(timestamp=NOW, open=100, high=100.5, low=99.5, close=100, volume=100),
            V4Bar(
                timestamp=NOW + timedelta(hours=1),
                open=100,
                high=103,
                low=97,
                close=100,
                volume=100,
            ),
        )
        plan = BacktestTradePlan(
            trade_id="collision",
            instrument="XAUUSD",
            direction=ResearchDirection.LONG,
            signal_at=NOW,
            stop_loss=98,
            take_profit=102,
            max_holding_bars=1,
        )
        result = V4BacktestEngine().run("XAUUSD", bars, (plan,), persist=False).trades[0]
        self.assertTrue(result.same_bar_collision)
        self.assertEqual(result.exit_reason, BacktestExitReason.STOP)

    def test_block_bootstrap_is_reproducible_and_never_shuffles_individual_returns(self) -> None:
        returns = tuple(0.001 + ((index % 7) - 3) * 0.0002 for index in range(120))
        validator = RobustnessValidator()
        first = validator.validate(
            returns,
            variants_tested=12,
            bootstrap_samples=500,
            block_size=8,
            seed=44,
            stress_cost_bps=2,
        )
        second = validator.validate(
            returns,
            variants_tested=12,
            bootstrap_samples=500,
            block_size=8,
            seed=44,
            stress_cost_bps=2,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.status, RobustnessStatus.AVAILABLE)
        self.assertLess(first.stressed_mean_return, first.mean_return)  # type: ignore[operator]
        self.assertFalse(first.individual_return_shuffle_used)
        self.assertFalse(first.deflated_sharpe_available)
        self.assertFalse(first.pbo_available)

        insufficient = validator.validate(returns[:20], bootstrap_samples=100)
        self.assertEqual(insufficient.status, RobustnessStatus.INSUFFICIENT_DATA)
        self.assertEqual(insufficient.bootstrap_samples, 0)


class QuantLabApiTests(unittest.TestCase):
    def test_research_endpoints_are_typed_persisted_and_have_no_promotion_or_execution(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database
        from tests.test_v4_market_foundation import FakeGateway

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite://",
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "MT5_ENABLED": "false",
                "V4_CONTEXT_REFRESH_ENABLED": "false",
            },
        ):
            settings = AtlasSettings.from_env()
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway())
        database.create_schema()
        bars = research_bars(50)
        label_payload = {
            "instrument": "XAUUSD",
            "direction": "LONG",
            "bars": [bar.model_dump(mode="json") for bar in bars],
            "config": {
                "atr_lookback": 5,
                "structure_lookback": 5,
                "max_holding_bars": 5,
                "target_atr_multiple": 1,
                "stop_atr_multiple": 1,
                "use_structure": False,
                "barrier_order_policy": "STOP_FIRST",
            },
        }
        samples = validation_samples(70)
        walk_payload = {
            "samples": [sample.model_dump(mode="json") for sample in samples],
            "config": {
                "train_size": 25,
                "validation_size": 10,
                "test_size": 10,
                "step_size": 10,
                "embargo_size": 2,
                "mode": "EXPANDING",
            },
        }
        backtest_payload = {
            "instrument": "XAUUSD",
            "bars": [bar.model_dump(mode="json") for bar in bars[:10]],
            "plans": [
                BacktestTradePlan(
                    trade_id="api-plan",
                    instrument="XAUUSD",
                    direction=ResearchDirection.LONG,
                    signal_at=bars[0].timestamp,
                    stop_loss=98,
                    take_profit=105,
                    max_holding_bars=5,
                ).model_dump(mode="json")
            ],
            "cost_model": CostModel(spread_bps=2, slippage_bps=1).model_dump(mode="json"),
        }
        experiment_payload = ExperimentSpec(
            name="API experiment",
            feature_sets=("baseline",),
            model_families=("logistic",),
        ).model_dump(mode="json")
        holdout_payload = {
            "samples": [sample.model_dump(mode="json") for sample in samples],
            "holdout_size": 10,
        }

        with TestClient(application) as client:
            overview = client.get("/api/v4/health")
            labels = client.post("/api/v4/quant/labels", json=label_payload)
            walk = client.post("/api/v4/quant/walk-forward", json=walk_payload)
            backtest = client.post("/api/v4/quant/backtests", json=backtest_payload)
            robust = client.post(
                "/api/v4/quant/robustness",
                json={"returns": [0.001] * 40, "bootstrap_samples": 100, "block_size": 4},
            )
            experiment = client.post("/api/v4/quant/experiments", json=experiment_payload)
            sealed = client.post("/api/v4/quant/holdouts/seal", json=holdout_payload)
            unavailable_open = client.get("/api/v4/quant/holdouts/open")

        for response in (labels, walk, backtest, robust, experiment, sealed):
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(overview.json()["phase"], 15)
        self.assertFalse(labels.json()["signal_authority"])
        self.assertFalse(backtest.json()["execution_enabled"])
        self.assertFalse(walk.json()["production_promotion"])
        self.assertFalse(experiment.json()["automatic_promotion"])
        self.assertFalse(sealed.json()["holdout_samples_returned"])
        self.assertFalse(sealed.json()["final_access_endpoint_available"])
        self.assertNotIn("samples", sealed.json())
        self.assertEqual(unavailable_open.status_code, 404)


if __name__ == "__main__":
    unittest.main()
