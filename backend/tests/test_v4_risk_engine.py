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
from app.v4.context_contracts import ContextComputationRequest, ContextSet
from app.v4.context_service import V4ContextService
from app.v4.contracts import (
    FeatureSet,
    LiquidityState,
    OfficialTimeframe,
    RegimeState,
    SwingPoint,
)
from app.v4.feature_service import FEATURE_COLLECTION, V4FeatureService
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
)
from app.v4.quant_validation import QUANT_VALIDATION_COLLECTION
from app.v4.quant_validation_contracts import (
    QuantCheckName,
    QuantCheckStatus,
    QuantDecision,
    QuantEvidenceCheck,
    QuantValidationResult,
)
from app.v4.risk_contracts import (
    ExpectancyReport,
    ExpectancyRequest,
    ExpectancyStatus,
    ExposureStatus,
    PortfolioExposureReport,
    PortfolioExposureRequest,
    PortfolioPosition,
    RiskAccountState,
    RiskDecision,
    RiskOverlayRequest,
    RiskOverlayResult,
    StructuralTargetPlan,
    StructuralTargetRequest,
    TargetStatus,
)
from app.v4.risk_engine import (
    EXPECTANCY_COLLECTION,
    PORTFOLIO_EXPOSURE_COLLECTION,
    RISK_OVERLAY_COLLECTION,
    STRUCTURAL_TARGET_COLLECTION,
    ExpectancyEngine,
    PortfolioExposureEngine,
    RiskOverlay,
    StructuralTargetEngine,
)
from app.v4.snapshots import MarketSnapshotService
from tests.test_v4_market_foundation import FakeGateway

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class RiskEngineTests(unittest.TestCase):
    store: ClassVar[MemoryRecordStore]
    candidate: ClassVar[PrimaryCandidate]
    quant: ClassVar[QuantValidationResult]
    feature_set: ClassVar[FeatureSet]
    context: ClassVar[ContextSet]
    backtest: ClassVar[BacktestRun]
    expectancy: ClassVar[ExpectancyReport]
    targets: ClassVar[StructuralTargetPlan]
    exposure: ClassVar[PortfolioExposureReport]
    risk: ClassVar[RiskOverlayResult]

    @classmethod
    def setUpClass(cls) -> None:
        cls.store = MemoryRecordStore()
        cls.candidate = PrimaryCandidate(
            candidate_id=stable_key("risk-candidate"),
            model_id="a" * 64,
            instrument="XAUUSD",
            timeframe=OfficialTimeframe.H1,
            regime=RegimeState.TREND_UP,
            observed_at=NOW,
            direction=MLTarget.LONG,
            raw_probability_long=0.85,
            raw_direction_probability=0.85,
        )
        cls.store.upsert(
            PRIMARY_CANDIDATE_COLLECTION,
            cls.candidate.candidate_id,
            cls.candidate.model_dump(mode="json"),
        )
        checks = tuple(
            QuantEvidenceCheck(
                name=name,
                status=QuantCheckStatus.PASS,
                summary=f"{name.value} passed",
                artifact_ids=(stable_key("risk", name.value),),
            )
            for name in QuantCheckName
        )
        cls.quant = QuantValidationResult(
            quant_validation_id=stable_key("risk-quant"),
            version="quant-validation-v1",
            created_at=NOW,
            instrument="XAUUSD",
            candidate_id=cls.candidate.candidate_id,
            calibrated_probability_id="b" * 64,
            calibration_id="c" * 64,
            meta_model_id="d" * 64,
            robustness_run_id="e" * 64,
            regime_stability_id="f" * 64,
            drift_report_id="1" * 64,
            backtest_run_id="2" * 64,
            snapshot_id="3" * 64,
            atlas_probability=0.85,
            decision=QuantDecision.PASS,
            checks=checks,
            reason="All statistical evidence passed",
            quant_veto=False,
        )
        cls.store.upsert(
            QUANT_VALIDATION_COLLECTION,
            cls.quant.quant_validation_id,
            cls.quant.model_dump(mode="json"),
        )
        snapshot = MarketSnapshotService(
            FakeGateway(NOW),
            cls.store,
            enabled=True,
            expected_account_mode="DEMO",
        ).capture("XAUUSD", now=NOW, persist=True)
        raw_features = V4FeatureService(cls.store).compute(snapshot, persist=False)
        summary = raw_features.timeframes[OfficialTimeframe.H1]
        atr = summary.volatility.atr14
        entry = (snapshot.bid + snapshot.ask) / 2
        risk_distance = 0.8 * atr
        structure = summary.structure.model_copy(
            update={
                "support": entry - 0.5 * atr,
                "resistance": entry + 1.6 * risk_distance,
                "swing_lows": (
                    SwingPoint(
                        timestamp=NOW - timedelta(hours=1),
                        price=entry - 0.5 * atr,
                        kind="LOW",
                        bar_index=78,
                    ),
                ),
            }
        )
        h1 = summary.model_copy(update={"structure": structure})
        cls.feature_set = raw_features.model_copy(
            update={
                "timeframes": {
                    **raw_features.timeframes,
                    OfficialTimeframe.H1: h1,
                },
                "liquidity": raw_features.liquidity.model_copy(
                    update={"state": LiquidityState.ADEQUATE}
                ),
            }
        )
        cls.store.upsert(
            FEATURE_COLLECTION,
            cls.feature_set.feature_set_id,
            cls.feature_set.model_dump(mode="json"),
        )
        trades: list[BacktestTradeResult] = []
        for index in range(50):
            win = index < 30
            gross = 0.02 if win else -0.01
            net = gross - 0.002
            trades.append(
                BacktestTradeResult(
                    trade_id=f"risk-trade-{index}",
                    direction=ResearchDirection.LONG,
                    signal_at=NOW - timedelta(days=60 - index),
                    entry_at=NOW - timedelta(days=60 - index, minutes=-1),
                    exit_at=NOW - timedelta(days=60 - index, minutes=-45),
                    entry_fill=4_300,
                    exit_fill=4_310 if win else 4_295,
                    gross_return=gross,
                    net_return=net,
                    realized_r=1.5 if win else -1,
                    holding_bars=1,
                    exit_reason=(
                        BacktestExitReason.TARGET if win else BacktestExitReason.STOP
                    ),
                )
            )
        cls.backtest = BacktestRun(
            backtest_run_id=stable_key("risk-backtest"),
            instrument="XAUUSD",
            created_at=NOW,
            data_hash="4" * 64,
            config_hash="5" * 64,
            cost_model=CostModel(
                spread_bps=1.0,
                slippage_bps=0.5,
                commission_bps_round_turn=0.5,
                daily_swap_bps=-0.1,
            ),
            trades=tuple(trades),
            metrics=BacktestMetrics(
                total_plans=50,
                executed_trades=50,
                wins=30,
                losses=20,
                timeouts=0,
                blocked=0,
                win_rate=0.6,
                mean_net_return=0.006,
                cumulative_net_return=0.30,
                profit_factor=2.25,
                maximum_drawdown=0.04,
            ),
        )
        cls.store.upsert(
            BACKTEST_COLLECTION,
            cls.backtest.backtest_run_id,
            cls.backtest.model_dump(mode="json"),
        )
        cls.expectancy = ExpectancyEngine(cls.store).evaluate(
            ExpectancyRequest(
                quant_validation_id=cls.quant.quant_validation_id,
                backtest_run_id=cls.backtest.backtest_run_id,
            )
        )
        cls.targets = StructuralTargetEngine(cls.store).build(
            StructuralTargetRequest(
                quant_validation_id=cls.quant.quant_validation_id,
                feature_set_id=cls.feature_set.feature_set_id,
                snapshot_id=snapshot.snapshot_id,
            )
        )
        cls.exposure = PortfolioExposureEngine(cls.store).assess(
            PortfolioExposureRequest(
                candidate_id=cls.candidate.candidate_id,
                proposed_risk_fraction=0.004,
            )
        )
        cls.context = V4ContextService(cls.store).compute(
            "XAUUSD",
            ContextComputationRequest(
                as_of=NOW,
                liquidity_state=LiquidityState.ADEQUATE,
            ),
            persist=True,
        )
        cls.risk = RiskOverlay(cls.store).evaluate(
            RiskOverlayRequest(
                quant_validation_id=cls.quant.quant_validation_id,
                expectancy_id=cls.expectancy.expectancy_id,
                target_plan_id=cls.targets.target_plan_id,
                exposure_id=cls.exposure.exposure_id,
                context_set_id=cls.context.context_set_id,
                snapshot_id=snapshot.snapshot_id,
                account_state=RiskAccountState(
                    as_of=NOW,
                    daily_drawdown=0.003,
                    weekly_drawdown=0.008,
                    consecutive_losses=1,
                ),
            )
        )

    def test_expectancy_is_net_of_observed_costs_and_must_be_positive(self) -> None:
        self.assertEqual(self.expectancy.status, ExpectancyStatus.POSITIVE)
        self.assertAlmostEqual(self.expectancy.average_gross_win or 0, 0.02)
        self.assertAlmostEqual(self.expectancy.average_gross_loss or 0, 0.01)
        self.assertAlmostEqual(self.expectancy.average_cost_return or 0, 0.002)
        self.assertAlmostEqual(self.expectancy.expected_value_net or 0, 0.0135)
        self.assertTrue(self.expectancy.positive_validated_expectancy)
        self.assertEqual(len(self.store.values(EXPECTANCY_COLLECTION)), 1)

    def test_structural_targets_use_market_structure_and_atr(self) -> None:
        self.assertEqual(self.targets.status, TargetStatus.VALID)
        self.assertIsNotNone(self.targets.entry)
        self.assertIsNotNone(self.targets.stop_loss)
        self.assertEqual(len(self.targets.targets), 3)
        self.assertGreaterEqual(self.targets.minimum_reward_risk or 0, 1.5)
        self.assertLessEqual(self.targets.stop_atr_multiple or 99, 2.5)
        self.assertFalse(self.targets.fixed_pip_stop_used)
        self.assertFalse(self.targets.elliott_used)
        self.assertFalse(self.targets.fibonacci_used)
        self.assertEqual(len(self.store.values(STRUCTURAL_TARGET_COLLECTION)), 1)

    def test_portfolio_exposure_detects_repeated_short_usd_factor(self) -> None:
        report = PortfolioExposureEngine(self.store).assess(
            PortfolioExposureRequest(
                candidate_id=self.candidate.candidate_id,
                proposed_risk_fraction=0.004,
                open_positions=(
                    PortfolioPosition(
                        position_id="eur-long",
                        instrument="EURUSD",
                        direction=MLTarget.LONG,
                        risk_fraction=0.004,
                    ),
                    PortfolioPosition(
                        position_id="gbp-long",
                        instrument="GBPUSD",
                        direction=MLTarget.LONG,
                        risk_fraction=0.004,
                    ),
                    PortfolioPosition(
                        position_id="usdchf-short",
                        instrument="USDCHF",
                        direction=MLTarget.SHORT,
                        risk_fraction=0.004,
                    ),
                ),
                correlations=(
                    # The correlation is deliberately high and must be treated as a veto too.
                    {
                        "left_instrument": "XAUUSD",
                        "right_instrument": "EURUSD",
                        "coefficient": 0.82,
                        "sample_count": 120,
                        "window": "120D",
                    },
                ),
            )
        )
        self.assertEqual(report.status, ExposureStatus.VETO)
        self.assertIn("USD", report.repeated_factor_exposures)
        self.assertLess(report.currency_factor_exposure["USD"], 0)
        self.assertGreater(report.maximum_relevant_correlation or 0, 0.75)
        self.assertEqual(len(self.store.values(PORTFOLIO_EXPOSURE_COLLECTION)), 2)

    def test_hard_risk_veto_overrides_probability_and_approves_no_risk(self) -> None:
        self.assertEqual(self.risk.decision, RiskDecision.PASS)
        self.assertGreater(self.risk.approved_risk_fraction, 0)
        veto = RiskOverlay(self.store).evaluate(
            RiskOverlayRequest(
                quant_validation_id=self.quant.quant_validation_id,
                expectancy_id=self.expectancy.expectancy_id,
                target_plan_id=self.targets.target_plan_id,
                exposure_id=self.exposure.exposure_id,
                context_set_id=self.context.context_set_id,
                snapshot_id=self.targets.snapshot_id,
                account_state=RiskAccountState(
                    as_of=NOW,
                    daily_drawdown=0.02,
                    weekly_drawdown=0.02,
                    consecutive_losses=1,
                ),
            )
        )
        self.assertEqual(veto.decision, RiskDecision.RISK_VETO)
        self.assertTrue(veto.risk_veto)
        self.assertEqual(veto.approved_risk_fraction, 0)
        self.assertTrue(veto.veto_overrides_probability)
        self.assertFalse(veto.large_probability_leverage_allowed)
        self.assertFalse(veto.execution_enabled)
        self.assertEqual(len(self.store.values(RISK_OVERLAY_COLLECTION)), 2)

    def test_risk_api_is_typed_but_signal_and_order_routes_do_not_exist(self) -> None:
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
                "/api/v4/risk/expectancy",
                json={
                    "quant_validation_id": "a" * 64,
                    "backtest_run_id": "b" * 64,
                },
            )
            forbidden_signal = api.post("/api/v4/signals", json={})
            forbidden_order = api.post("/api/v4/orders", json={})
        self.assertEqual(overview.json()["phase"], 15)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(forbidden_signal.status_code, 404)
        self.assertEqual(forbidden_order.status_code, 404)


if __name__ == "__main__":
    unittest.main()
