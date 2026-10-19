from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from llm_orchestrator import LLMOrchestrator, OrchestratorError, ROUTES  # noqa: E402
from orderflow_engine import (  # noqa: E402
    METHOD,
    OrderFlowValidationError,
    analyze_orderflow,
    anchored_vwap,
    classify_ticks,
    volume_profile,
)


def bars() -> list[dict[str, float]]:
    return [
        {"time": 100 + i * 60, "open": 100 + i, "high": 101.5 + i, "low": 99.5 + i, "close": 101 + i, "volume": 10 + i}
        for i in range(8)
    ]


def ticks(volume: float = 1) -> list[dict[str, float]]:
    prices = [100.0, 100.2, 100.2, 100.1, 101.1, 102.2, 103.3, 104.4, 105.5]
    return [
        {"time": 101 + i * 55, "bid": price - 0.01, "ask": price + 0.01, "last": price, "volume": volume}
        for i, price in enumerate(prices)
    ]


class OrderFlowTests(unittest.TestCase):
    def test_tick_rule_carries_flat_tick_direction(self) -> None:
        self.assertEqual(classify_ticks(ticks())[:4], [0, 1, 1, -1])

    def test_full_packet_is_truthfully_labeled_and_finite(self) -> None:
        result = analyze_orderflow("EURUSD", bars(), ticks())
        self.assertEqual(result["method"], METHOD)
        self.assertFalse(result["centralizedTape"])
        self.assertFalse(result["dataQuality"]["orderFlowVerified"])
        self.assertEqual(result["coverage"], {"closedBars": 8, "ticks": 9})
        self.assertGreaterEqual(result["confluence"]["score"], -100)
        self.assertLessEqual(result["confluence"]["score"], 100)
        self.assertIsNotNone(result["volumeProfile"]["poc"])

    def test_zero_reported_volume_uses_tick_count_proxy(self) -> None:
        result = analyze_orderflow("XAUUSD", bars(), ticks(0))
        self.assertEqual(result["delta"]["volumeBasis"], "TICK_COUNT_PROXY")
        self.assertGreater(result["delta"]["buyVolume"] + result["delta"]["sellVolume"], 0)

    def test_avwap_and_profile_are_deterministic(self) -> None:
        first = anchored_vwap(bars())
        second = anchored_vwap(bars())
        self.assertEqual(first, second)
        profile = volume_profile(bars(), bins=8)
        self.assertEqual(len(profile["bins"]), 8)
        self.assertLessEqual(profile["valueAreaLow"], profile["poc"])
        self.assertGreaterEqual(profile["valueAreaHigh"], profile["poc"])

    def test_invalid_ohlc_is_rejected(self) -> None:
        bad = bars()
        bad[0]["high"] = 98
        with self.assertRaises(OrderFlowValidationError):
            analyze_orderflow("EURUSD", bad, ticks())


class OrchestratorTests(unittest.TestCase):
    def test_routing_table_assigns_the_requested_four_models(self) -> None:
        self.assertEqual(ROUTES["confluence_synthesis"].model, "ministral-3:3b")
        self.assertEqual(ROUTES["regime_classification"].model, "llama3.2:1b-instruct-q3_K_L")
        self.assertEqual(ROUTES["signal_formatting"].model, "qwen2.5:3b")
        self.assertEqual(ROUTES["risk_review"].model, "phi4-mini")

    def test_task_uses_exact_route_and_returns_advisory_metadata(self) -> None:
        calls = []

        def transport(route, prompt, timeout):
            calls.append((route, prompt, timeout))
            return json.dumps({"regime": "RANGE", "confidence": 0.75, "tags": ["quiet"], "reason": "bounded"})

        result = LLMOrchestrator(transport=transport).route_task("regime_classification", {"atr": 1.2})
        self.assertEqual(calls[0][0].model, "llama3.2:1b-instruct-q3_K_L")
        self.assertTrue(result["advisoryOnly"])
        self.assertFalse(result["cloudFallback"])
        self.assertEqual(result["output"]["regime"], "RANGE")

    def test_qwen_cannot_change_deterministic_prices(self) -> None:
        def transport(_route, _prompt, _timeout):
            return json.dumps(
                {
                    "action": "LONG",
                    "symbol": "EURUSD",
                    "timeframe": "1H",
                    "entry": 1.2,
                    "stopLoss": 1.15,
                    "takeProfits": [1.3],
                    "grade": "A",
                    "rationale": ["formatted"],
                    "source": "DETERMINISTIC_INPUT_ONLY",
                }
            )

        payload = {"deterministicSignal": {"action": "LONG", "symbol": "EURUSD", "timeframe": "1H", "entry": 1.2, "stopLoss": 1.1, "takeProfits": [1.3]}}
        with self.assertRaisesRegex(OrchestratorError, "stopLoss"):
            LLMOrchestrator(transport=transport).route_task("signal_formatting", payload)

    def test_qwen_prose_is_replaced_with_deterministic_evidence(self) -> None:
        def transport(_route, _prompt, _timeout):
            return json.dumps(
                {
                    "action": "LONG",
                    "symbol": "EURUSD",
                    "timeframe": "1H",
                    "entry": 1.2,
                    "stopLoss": 1.1,
                    "takeProfits": [1.3],
                    "grade": "A",
                    "rationale": ["A hallucinated geometric claim."],
                    "source": "DETERMINISTIC_INPUT_ONLY",
                }
            )

        payload = {"deterministicSignal": {"action": "LONG", "symbol": "EURUSD", "timeframe": "1H", "entry": 1.2, "stopLoss": 1.1, "takeProfits": [1.3], "grade": "A", "evidence": ["Verified trend evidence."]}}
        result = LLMOrchestrator(transport=transport).route_task("signal_formatting", payload)
        self.assertEqual(result["output"]["rationale"], ["Verified trend evidence."])

    def test_phi_review_must_remain_non_authoritative(self) -> None:
        def transport(_route, _prompt, _timeout):
            return json.dumps({"status": "PASS", "findings": [], "calculationChecks": [], "authoritative": True})

        with self.assertRaisesRegex(OrchestratorError, "authoritative=false"):
            LLMOrchestrator(transport=transport).route_task("risk_review", {"positionUnits": 0.1})

    def test_malformed_model_json_fails_closed(self) -> None:
        orchestrator = LLMOrchestrator(transport=lambda _route, _prompt, _timeout: "LONG")
        with self.assertRaisesRegex(OrchestratorError, "malformed JSON"):
            orchestrator.route_task("confluence_synthesis", {})


if __name__ == "__main__":
    unittest.main()
