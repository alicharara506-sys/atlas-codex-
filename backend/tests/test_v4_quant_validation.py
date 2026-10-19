from __future__ import annotations

import math
import os
import sys
import unittest
from datetime import timedelta
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
from app.v4.calibration import (
    CALIBRATION_ARTIFACT_COLLECTION,
    ProbabilityCalibrationEngine,
)
from app.v4.calibration_contracts import (
    CalibratedProbability,
    CalibratedProbabilityRequest,
    CalibrationArtifact,
    CalibrationUncertainty,
)
from app.v4.contracts import OfficialTimeframe, RegimeState
from app.v4.historical_meta import HISTORICAL_META_SAMPLE_COLLECTION
from app.v4.meta_contracts import (
    EvidenceDependencyReport,
    MetaModelArtifact,
    MetaPrediction,
    MetaTarget,
    MetaTrainingConfig,
)
from app.v4.meta_labeling import (
    META_MODEL_COLLECTION,
    META_PREDICTION_COLLECTION,
    EvidenceDependencyEngine,
    MetaLabelTrainer,
)
from app.v4.ml_contracts import MLTarget, PrimaryCandidate
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_backtest import BACKTEST_COLLECTION
from app.v4.quant_contracts import (
    BacktestExitReason,
    BacktestMetrics,
    BacktestRun,
    BacktestTradeResult,
    CostModel,
    ResearchDirection,
    RobustnessResult,
    WalkForwardConfig,
)
from app.v4.quant_validation import (
    PREDICTION_DRIFT_COLLECTION,
    QUANT_VALIDATION_COLLECTION,
    REGIME_STABILITY_COLLECTION,
    PredictionDriftEngine,
    QuantValidationEngine,
    RegimeStabilityEngine,
)
from app.v4.quant_validation_contracts import (
    DriftStatus,
    PredictionDriftReport,
    PredictionDriftRequest,
    QuantCheckName,
    QuantCheckStatus,
    QuantDecision,
    QuantValidationRequest,
    RegimeStabilityReport,
    RegimeStabilityRequest,
    RegimeStabilityStatus,
)
from app.v4.robustness import RobustnessValidator
from app.v4.snapshots import MarketSnapshotService
from tests.test_v4_market_foundation import FakeGateway
from tests.test_v4_meta_labeling import meta_dataset, observations


class QuantValidationTests(unittest.TestCase):
    store: ClassVar[MemoryRecordStore]
    dependency: ClassVar[EvidenceDependencyReport]
    meta_artifact: ClassVar[MetaModelArtifact]
    calibration: ClassVar[CalibrationArtifact]
    probability: ClassVar[CalibratedProbability]
    regime: ClassVar[RegimeStabilityReport]
    drift: ClassVar[PredictionDriftReport]
    robustness: ClassVar[RobustnessResult]
    backtest: ClassVar[BacktestRun]
    request: ClassVar[QuantValidationRequest]

    @classmethod
    def setUpClass(cls) -> None:
        cls.store = MemoryRecordStore()
        cls.dependency = EvidenceDependencyEngine(cls.store).analyze(observations())
        samples = meta_dataset(
            cls.store,
            cls.dependency.report_id,
            count=360,
            prefix="quant-validation",
        )
        for sample in samples:
            cls.store.upsert(
                HISTORICAL_META_SAMPLE_COLLECTION,
                sample.sample_id,
                sample.model_dump(mode="json"),
            )
        config = MetaTrainingConfig(
            walk_forward=WalkForwardConfig(
                train_size=80,
                validation_size=20,
                test_size=20,
                step_size=20,
                embargo_size=2,
            ),
            epochs=50,
        )
        artifact = MetaLabelTrainer(cls.store).train(samples, config).artifact
        stable_oos = tuple(
            row.model_copy(update={"forward_return_net": (0.010 if row.regime is RegimeState.TREND_UP else 0.009)})
            for row in artifact.oos_predictions
        )
        cls.meta_artifact = artifact.model_copy(update={"oos_predictions": stable_oos})
        cls.store.upsert(
            META_MODEL_COLLECTION,
            cls.meta_artifact.meta_model_id,
            cls.meta_artifact.model_dump(mode="json"),
        )
        calibration_engine = ProbabilityCalibrationEngine(cls.store)
        fitted = calibration_engine.train(cls.meta_artifact.meta_model_id)
        post = fitted.post_calibration_metrics
        assert post is not None
        cls.calibration = fitted.model_copy(
            update={
                "post_calibration_metrics": post.model_copy(
                    update={
                        "brier_score": min(post.brier_score, 0.18),
                        "expected_calibration_error": 0.03,
                    }
                ),
                "uncertainty": CalibrationUncertainty.LOW,
                "high_probability_rate": 0.01,
                "calibration_audit_required": False,
            }
        )
        cls.store.upsert(
            CALIBRATION_ARTIFACT_COLLECTION,
            cls.calibration.calibration_id,
            cls.calibration.model_dump(mode="json"),
        )

        reference_end = max(row.label_end_at for row in cls.meta_artifact.oos_predictions)
        current_start = reference_end + timedelta(days=2)
        candidate = PrimaryCandidate(
            candidate_id=stable_key("quant-current-candidate"),
            model_id="d" * 64,
            instrument="XAUUSD",
            timeframe=OfficialTimeframe.H1,
            regime=RegimeState.TREND_UP,
            observed_at=current_start,
            direction=MLTarget.LONG,
            raw_probability_long=0.75,
            raw_direction_probability=0.75,
        )
        cls.store.upsert(
            PRIMARY_CANDIDATE_COLLECTION,
            candidate.candidate_id,
            candidate.model_dump(mode="json"),
        )
        current_ids: list[str] = []
        for index, source in enumerate(cls.meta_artifact.oos_predictions):
            prediction = MetaPrediction(
                meta_prediction_id=stable_key("quant-current-prediction", index),
                meta_model_id=cls.meta_artifact.meta_model_id,
                candidate_id=candidate.candidate_id,
                observed_at=current_start + timedelta(minutes=index),
                raw_accept_probability=source.raw_accept_probability,
                research_classification=(
                    MetaTarget.ACCEPT if source.raw_accept_probability >= 0.5 else MetaTarget.REJECT
                ),
            )
            current_ids.append(prediction.meta_prediction_id)
            cls.store.upsert(
                META_PREDICTION_COLLECTION,
                prediction.meta_prediction_id,
                prediction.model_dump(mode="json"),
            )
        cls.probability = calibration_engine.apply(
            CalibratedProbabilityRequest(
                calibration_id=cls.calibration.calibration_id,
                meta_prediction_id=current_ids[0],
            )
        )
        cls.regime = RegimeStabilityEngine(cls.store).assess(
            RegimeStabilityRequest(
                meta_model_id=cls.meta_artifact.meta_model_id,
                current_regime=RegimeState.TREND_UP,
            )
        )
        cls.drift = PredictionDriftEngine(cls.store).assess(
            PredictionDriftRequest(
                meta_model_id=cls.meta_artifact.meta_model_id,
                current_meta_prediction_ids=tuple(current_ids),
            )
        )
        cls.robustness = RobustnessValidator(cls.store).validate(
            tuple(0.01 + 0.001 * math.sin(index / 7) for index in range(120)),
            variants_tested=1,
            bootstrap_samples=300,
            block_size=8,
            stress_cost_bps=2,
        )

        trades = tuple(
            BacktestTradeResult(
                trade_id=f"quant-trade-{index}",
                direction=ResearchDirection.LONG,
                signal_at=current_start + timedelta(hours=index),
                entry_at=current_start + timedelta(hours=index, minutes=1),
                exit_at=current_start + timedelta(hours=index, minutes=45),
                entry_fill=4_300,
                exit_fill=4_310,
                gross_return=0.0025,
                net_return=0.0020,
                realized_r=1.5,
                holding_bars=1,
                exit_reason=BacktestExitReason.TARGET,
            )
            for index in range(40)
        )
        cls.backtest = BacktestRun(
            backtest_run_id=stable_key("quant-cost-backtest"),
            instrument="XAUUSD",
            created_at=current_start + timedelta(days=1),
            data_hash="e" * 64,
            config_hash="f" * 64,
            cost_model=CostModel(
                spread_bps=1.2,
                slippage_bps=0.4,
                commission_bps_round_turn=0.2,
                daily_swap_bps=-0.1,
            ),
            trades=trades,
            metrics=BacktestMetrics(
                total_plans=40,
                executed_trades=40,
                wins=40,
                losses=0,
                timeouts=0,
                blocked=0,
                win_rate=1,
                mean_net_return=0.002,
                cumulative_net_return=0.08,
                profit_factor=3.0,
                maximum_drawdown=0.01,
            ),
        )
        cls.store.upsert(
            BACKTEST_COLLECTION,
            cls.backtest.backtest_run_id,
            cls.backtest.model_dump(mode="json"),
        )
        snapshot_at = current_start + timedelta(days=2)
        snapshot = MarketSnapshotService(
            FakeGateway(snapshot_at),
            cls.store,
            enabled=True,
            expected_account_mode="DEMO",
        ).capture("XAUUSD", now=snapshot_at, persist=True)
        cls.request = QuantValidationRequest(
            calibrated_probability_id=cls.probability.calibrated_probability_id,
            robustness_run_id=cls.robustness.robustness_run_id,
            regime_stability_id=cls.regime.regime_stability_id,
            drift_report_id=cls.drift.drift_report_id,
            backtest_run_id=cls.backtest.backtest_run_id,
            snapshot_id=snapshot.snapshot_id,
        )

    def test_regime_and_prediction_drift_evidence_are_grounded(self) -> None:
        self.assertEqual(self.regime.status, RegimeStabilityStatus.STABLE)
        self.assertEqual(self.regime.evaluated_regime, RegimeState.TREND_UP)
        self.assertGreaterEqual(len(self.regime.performances), 1)
        self.assertGreaterEqual(self.regime.minimum_regime_sample_count, 30)
        self.assertEqual(self.drift.status, DriftStatus.STABLE)
        psi = self.drift.population_stability_index
        assert psi is not None
        self.assertAlmostEqual(psi, 0)
        self.assertEqual(len(self.store.values(REGIME_STABILITY_COLLECTION)), 1)
        self.assertTrue(
            any(
                row["drift_report_id"] == self.drift.drift_report_id
                for row in self.store.values(PREDICTION_DRIFT_COLLECTION)
            )
        )

    def test_regime_gate_evaluates_accepted_trades_in_current_regime(self) -> None:
        current = RegimeStabilityEngine(self.store).assess(
            RegimeStabilityRequest(
                meta_model_id=self.meta_artifact.meta_model_id,
                current_regime=RegimeState.TREND_UP,
            )
        )

        self.assertEqual(current.evaluated_regime, RegimeState.TREND_UP)
        self.assertEqual(current.status, RegimeStabilityStatus.STABLE)
        evaluated = next(row for row in current.performances if row.regime is RegimeState.TREND_UP)
        self.assertGreaterEqual(evaluated.sample_count, 30)
        self.assertGreater(evaluated.mean_net_return or 0, 0)

    def test_drift_uses_purged_oos_tail_until_live_window_is_mature(self) -> None:
        report = PredictionDriftEngine(self.store).assess(
            PredictionDriftRequest(
                meta_model_id=self.meta_artifact.meta_model_id,
                current_meta_prediction_ids=(self.probability.meta_prediction_id,),
            )
        )

        self.assertEqual(report.window_source, "OOS_FIXED_MODEL")
        self.assertNotEqual(report.status, DriftStatus.INSUFFICIENT_EVIDENCE)
        self.assertGreaterEqual(report.reference_sample_count, 50)
        self.assertGreaterEqual(report.current_sample_count, 30)
        assert report.reference_end is not None
        assert report.current_start is not None
        self.assertLess(report.reference_end, report.current_start)

    def test_all_statistical_checks_can_pass_without_publishing(self) -> None:
        result = QuantValidationEngine(self.store).validate(self.request)
        self.assertEqual(result.decision, QuantDecision.PASS)
        self.assertFalse(result.quant_veto)
        self.assertEqual({check.name for check in result.checks}, set(QuantCheckName))
        self.assertTrue(all(check.status is QuantCheckStatus.PASS for check in result.checks))
        self.assertTrue(result.quant_validation_authority)
        self.assertIsNone(result.grade)
        self.assertFalse(result.publication_authority)
        self.assertFalse(result.execution_enabled)
        self.assertEqual(len(self.store.values(QUANT_VALIDATION_COLLECTION)), 1)

    def test_quant_veto_is_absolute_even_with_a_calibrated_probability(self) -> None:
        failed = RobustnessValidator(self.store).validate(
            tuple(-0.004 + 0.001 * math.sin(index / 5) for index in range(120)),
            variants_tested=1,
            bootstrap_samples=300,
            block_size=8,
        )
        result = QuantValidationEngine(self.store).validate(
            self.request.model_copy(update={"robustness_run_id": failed.robustness_run_id})
        )
        self.assertIsNotNone(result.atlas_probability)
        self.assertEqual(result.decision, QuantDecision.VETO)
        self.assertTrue(result.quant_veto)
        self.assertTrue(result.veto_overrides_everything)
        self.assertFalse(result.ai_override_allowed)
        self.assertFalse(result.probability_override_allowed)

    def test_multiple_testing_without_dsr_and_pbo_is_insufficient(self) -> None:
        multiple = RobustnessValidator(self.store).validate(
            tuple(0.01 + 0.001 * math.sin(index / 5) for index in range(120)),
            variants_tested=7,
            bootstrap_samples=300,
            block_size=8,
        )
        result = QuantValidationEngine(self.store).validate(
            self.request.model_copy(update={"robustness_run_id": multiple.robustness_run_id})
        )
        multiple_check = next(check for check in result.checks if check.name is QuantCheckName.MULTIPLE_TESTING)
        self.assertEqual(multiple_check.status, QuantCheckStatus.INSUFFICIENT)
        self.assertEqual(result.decision, QuantDecision.INSUFFICIENT_EVIDENCE)

    def test_quant_api_is_typed_and_has_no_publish_or_order_route(self) -> None:
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
                "/api/v4/quant/validate",
                json=self.request.model_dump(mode="json"),
            )
            forbidden_publish = api.post("/api/v4/quant/publish", json={})
            forbidden_order = api.post("/api/v4/orders", json={})
        self.assertEqual(overview.json()["phase"], 15)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(forbidden_publish.status_code, 404)
        self.assertEqual(forbidden_order.status_code, 404)


if __name__ == "__main__":
    unittest.main()
