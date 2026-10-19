from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HAS_AGENT_STACK = importlib.util.find_spec("pydantic") is not None


class AgenticWorkflowTests(unittest.TestCase):
    def test_full_workflow_is_deterministic_audited_and_research_only(self) -> None:
        if not HAS_AGENT_STACK:
            self.skipTest("pydantic is not installed")

        from app.agents.contracts import MarketObservation, ResearchRequest, RiskLimits
        from app.agents.workflow import AtlasAgentWorkflow
        from app.core.store import MemoryRecordStore

        positive = {
            "fundamentals_score": 0.8,
            "technical_score": 0.9,
            "sentiment_score": 0.7,
            "macro_score": 0.6,
            "narrative_score": 0.75,
            "flow_score": 0.85,
            "insider_score": 0.5,
        }
        negative = {key: -value for key, value in positive.items()}
        request = ResearchRequest(
            observations=[
                MarketObservation(
                    ticker="AAA", volatility_percentile=0.3, correlation_to_book=0.2, requested_weight=0.08, **positive
                ),
                MarketObservation(
                    ticker="BBB", volatility_percentile=0.6, correlation_to_book=-0.1, requested_weight=0.08, **negative
                ),
            ],
            limits=RiskLimits(
                allowed_universe=["AAA", "BBB"],
                arbitration_threshold=0.2,
                max_gross_leverage=0.10,
            ),
        )
        store = MemoryRecordStore()
        workflow = AtlasAgentWorkflow(store)

        first = workflow.run(request)
        second = workflow.run(request)

        self.assertEqual(first, second)
        self.assertEqual(len(first["desk_signals"]), 7)
        self.assertEqual(first["arbitration"]["AAA"]["intent"], "LONG")
        self.assertEqual(first["arbitration"]["BBB"]["intent"], "SHORT")
        self.assertTrue(first["risk_assessment"]["AAA"]["approved"])
        self.assertFalse(first["risk_veto"])
        self.assertEqual(len(first["portfolio_proposal"]["positions"]), 2)
        self.assertLessEqual(first["portfolio_proposal"]["gross_target_weight"], 0.10)
        self.assertTrue(first["portfolio_proposal"]["research_only"])
        self.assertFalse(first["portfolio_proposal"]["execution_enabled"])
        self.assertEqual(first["execution_plan"], [])
        self.assertEqual(len(store.values("agentic_research_runs")), 1)
        self.assertLessEqual(len(first["debate_transcript"][0]["bull_arguments"]), 3)

    def test_risk_guard_vetoes_every_limit_breach_and_cannot_be_bypassed(self) -> None:
        from app.agents.contracts import (
            MarketObservation,
            PortfolioRiskContext,
            ResearchRequest,
            RiskLimits,
        )
        from app.agents.workflow import AtlasAgentWorkflow

        observation = MarketObservation(
            ticker="OUTSIDE",
            asset_class="derivative",
            fundamentals_score=1.0,
            technical_score=1.0,
            sentiment_score=1.0,
            macro_score=1.0,
            narrative_score=1.0,
            flow_score=1.0,
            insider_score=1.0,
            volatility_percentile=0.99,
            correlation_to_book=0.95,
            requested_weight=0.40,
        )
        request = ResearchRequest(
            observations=[observation],
            risk_context=PortfolioRiskContext(daily_drawdown=0.05, weekly_drawdown=0.10, gross_leverage=1.0),
            limits=RiskLimits(allowed_universe=["AAA"], max_gross_leverage=1.0),
            mode="supervised",
            manager_override="Human review requested; do not execute.",
        )
        state = AtlasAgentWorkflow().run(request)
        assessment = state["risk_assessment"]["OUTSIDE"]
        failed = {row["name"] for row in assessment["checks"] if not row["passed"]}

        self.assertTrue(state["risk_veto"])
        self.assertTrue(assessment["vetoed"])
        self.assertFalse(assessment["approved"])
        self.assertTrue(assessment["manager_override_recorded"])
        self.assertEqual(
            failed,
            {
                "universe",
                "asset_class",
                "position_limit",
                "gross_leverage",
                "daily_drawdown",
                "weekly_drawdown",
                "correlation",
                "volatility_regime",
            },
        )
        self.assertEqual(state["portfolio_proposal"]["positions"], [])
        self.assertEqual(state["execution_plan"], [])

    def test_missing_inputs_hold_or_fail_closed_and_contracts_reject_overrides(self) -> None:
        from app.agents.arbitration import arbitrate
        from app.agents.contracts import (
            DeskName,
            DeskSignal,
            MarketObservation,
            ResearchRequest,
            RiskLimits,
            SignalDirection,
        )
        from app.agents.workflow import AtlasAgentWorkflow

        limits = RiskLimits(allowed_universe=["HOLD", "MISSING"])
        request = ResearchRequest(
            observations=[
                MarketObservation(
                    ticker="HOLD",
                    fundamentals_score=0.05,
                    technical_score=0.05,
                    sentiment_score=0.05,
                    macro_score=0.05,
                    narrative_score=0.05,
                    flow_score=0.05,
                    insider_score=0.05,
                ),
                MarketObservation(
                    ticker="MISSING",
                    fundamentals_score=0.9,
                    technical_score=0.9,
                    sentiment_score=0.9,
                    macro_score=0.9,
                    narrative_score=0.9,
                    flow_score=0.9,
                    insider_score=0.9,
                ),
            ],
            limits=limits,
        )
        state = AtlasAgentWorkflow().run(request)
        self.assertEqual(state["arbitration"]["HOLD"]["intent"], "HOLD")
        self.assertFalse(state["risk_assessment"]["HOLD"]["vetoed"])
        self.assertTrue(state["risk_assessment"]["MISSING"]["vetoed"])
        self.assertIn(
            "risk_data_complete",
            {row["name"] for row in state["risk_assessment"]["MISSING"]["checks"] if not row["passed"]},
        )

        signal = DeskSignal(
            ticker="AAA",
            desk=DeskName.TECHNICAL,
            signal=SignalDirection.BULLISH,
            confidence=1.0,
            key_evidence=["test"],
            risk_factors=[],
            feedback="test",
        )
        self.assertEqual(arbitrate("AAA", [signal], {}, 0.2).intent.value, "HOLD")
        with self.assertRaises(ValidationError):
            RiskLimits(allowed_universe=["AAA"], desk_weights={DeskName.TECHNICAL: 0.0})
        with self.assertRaises(ValidationError):
            ResearchRequest(
                observations=[MarketObservation(ticker="AAA")],
                limits=RiskLimits(allowed_universe=["AAA"]),
                manager_override="not allowed",
            )


if __name__ == "__main__":
    unittest.main()
