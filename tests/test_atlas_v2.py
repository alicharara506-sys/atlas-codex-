from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from atlas_core import ATLASQuantEngine, load_config  # noqa: E402
from confluence_scorer import ConfluenceInputs, ConfluenceScorer, signal_class  # noqa: E402
from llm_orchestrator import LLMOrchestrator  # noqa: E402
from macro_engine import EconomicEvent, MacroInputs, MacroRegimeEngine, event_cooldown  # noqa: E402
from news_sentiment import Headline, LocalSentiment, analyze_headlines, tier_headline  # noqa: E402
from orderflow_engine import AVWAP, CVD, FootprintProxy, MarketStructure, OFII, VolumeDelta, build_orderflow_features  # noqa: E402
from risk_engine import correlation_guard, exposure_units, liquidity_filter, realized_volatility, structural_levels  # noqa: E402


def trend_bars(count: int = 200) -> list[dict[str, float]]:
    rows = []
    price = 100.0
    for index in range(count):
        drift = 0.25 + ((index % 7) - 3) * 0.03
        open_price = price
        close = open_price + drift
        volume = 1_000 + (index % 11) * 75
        rows.append({"time": 1_720_000_000 + index * 3_600, "open": open_price, "high": close + 0.5, "low": open_price - 0.4, "close": close, "volume": volume})
        price = close
    return rows


class InstitutionalOrderFlowTests(unittest.TestCase):
    def test_anchor_selection_and_feature_packet(self) -> None:
        rows = trend_bars()
        anchor = AVWAP.select_anchor(rows, timeframe="1H")
        self.assertIn(anchor["type"], {"swing_high", "swing_low", "session_open"})
        result = build_orderflow_features(rows, timeframe="1H", atr_percentile=0.4, bb_width_percentile=0.5)
        self.assertIn(result["microRegime"], {"BALANCED", "IMBALANCED_UP", "IMBALANCED_DOWN", "DISCOVERY", "VOL_COMPRESSION"})
        self.assertEqual(result["volumeDelta"]["method"], "OHLCV_PROXY")
        self.assertIn("bos", result["marketStructure"])

    def test_volume_delta_formula_and_flat_bar(self) -> None:
        rows = [
            {"time": 1, "open": 10, "high": 12, "low": 8, "close": 11, "volume": 100},
            {"time": 2, "open": 11, "high": 11, "low": 11, "close": 11, "volume": 100},
        ]
        delta = VolumeDelta.calculate(rows)
        self.assertAlmostEqual(delta[0]["delta"], 25)
        self.assertEqual(delta[1]["delta"], 0)

    def test_cvd_footprint_ofii_and_structure_are_bounded(self) -> None:
        rows = trend_bars(60)
        delta = VolumeDelta.calculate(rows)
        cvd = CVD.calculate(delta)
        footprint = FootprintProxy.calculate(rows)
        ofii = OFII.calculate(footprint)
        structure = MarketStructure.calculate(rows)
        self.assertEqual(len(cvd), len(rows))
        self.assertTrue(all(0 <= row["value"] <= 1 for row in ofii))
        self.assertIn(structure["bos"], {"BULLISH_BOS", "BEARISH_BOS", "NONE"})


class RegimeAndNewsTests(unittest.TestCase):
    def test_macro_regimes_and_unavailable_state(self) -> None:
        unavailable = MacroRegimeEngine.classify(MacroInputs())
        recession = MacroRegimeEngine.classify(MacroInputs("UP", "DOWN", 35))
        self.assertEqual(unavailable["regime"], "UNAVAILABLE")
        self.assertEqual(recession["regime"], "RECESSION")

    def test_tier_one_event_suppresses(self) -> None:
        result = event_cooldown([EconomicEvent(10_000, "T1", "INFLATION", "CPI")], now=9_500)
        self.assertTrue(result["suppressNewSignals"])

    def test_news_tiering_and_local_fallback(self) -> None:
        self.assertEqual(tier_headline("FOMC rate decision")['tier'], "T1")
        result = analyze_headlines([Headline("Gold rises as yields fall", "", "test", "")], "XAUUSD", LocalSentiment())
        self.assertEqual(result["headlinesAnalyzed"], 1)
        self.assertIn(result["dominantSentiment"], {"BULLISH", "BEARISH", "NEUTRAL"})


class ScoringAndRiskTests(unittest.TestCase):
    def test_confluence_caps_and_discovery_gate(self) -> None:
        result = ConfluenceScorer().score(ConfluenceInputs(99, 99, 99, 99, 99, 99, 99, 99, 99), micro_regime="DISCOVERY")
        self.assertEqual(result["score"], 10)
        self.assertEqual(result["direction"], "STRONG_LONG")
        self.assertEqual(signal_class(3.5, "BALANCED")[0], "NO_TRADE")
        self.assertEqual(signal_class(3.5, "DISCOVERY")[0], "WEAK_LONG")

    def test_volatility_exposure_caps(self) -> None:
        self.assertEqual(exposure_units(1.0, 0.40)["units"], 0.25)
        self.assertLessEqual(exposure_units(1.0, 0.02)["units"], 1.0)
        self.assertIsNotNone(realized_volatility([100, 101, 102, 101, 103]))

    def test_structural_geometry_and_filters(self) -> None:
        geometry = structural_levels("STRONG_LONG", entry=100, atr=2, value_area_low=97, value_area_high=102, swing_low=96, swing_high=103, low_volume_nodes=[108], high_volume_nodes=[116])
        self.assertTrue(geometry["valid"])
        self.assertLess(geometry["stopLoss"], 100)
        self.assertGreaterEqual(geometry["riskReward"][0], 1.5)
        filtered = liquidity_filter(current_spread=5, average_spread=1, atr_percentile=0.5, timestamp_utc=1_700_000_000, tier1_cooldown=False)
        self.assertTrue(filtered["suppressed"])

    def test_correlation_guard_reduces_only_clustered_risk(self) -> None:
        result = correlation_guard({"direction": "STRONG_LONG", "conviction": 1.0}, [{"instrument": "EURUSD", "direction": "STRONG_LONG", "correlation": 0.8}, {"instrument": "GBPUSD", "direction": "STRONG_LONG", "correlation": 0.9}])
        self.assertEqual(result["adjustedConviction"], 0.5)
        self.assertEqual(result["clusterRisk"], "HIGH")


class OrchestrationAndSchemaTests(unittest.TestCase):
    def test_ttl_cache_avoids_duplicate_inference(self) -> None:
        calls = []

        def transport(_route, _prompt, _timeout):
            calls.append(1)
            return json.dumps({"state": "BALANCED", "reason": "inside value"})

        engine = LLMOrchestrator(transport=transport, cache_ttl_seconds=300)
        first = engine.route_task("micro_regime", {"x": 1})
        second = engine.route_task("micro_regime", {"x": 1})
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(calls), 1)

    def test_full_v2_schema_is_deterministic_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(ROOT / "config.yaml")
            config["engine"]["audit_database"] = str(Path(directory) / "audit.sqlite3")
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            engine = ATLASQuantEngine(config_path)
            result = asyncio.run(engine.generate_signal_from_data(instrument="XAUUSD", timeframe="1H", bars=trend_bars(), write_audit=True))
            self.assertEqual(result["metadata"]["version"], "2.0")
            self.assertIn(result["signal"]["direction"], {"STRONG_LONG", "MODERATE_LONG", "WEAK_LONG", "NO_TRADE", "WEAK_SHORT", "MODERATE_SHORT", "STRONG_SHORT"})
            self.assertTrue(result["safety"]["readOnly"])
            self.assertFalse(result["safety"]["orderSubmission"])
            self.assertEqual(result["data_health"]["macro"], "UNAVAILABLE")
            self.assertIn("wyckoff", result)
            self.assertIn("smc", result)
            self.assertGreaterEqual(result["confluence"]["score"], -10)
            self.assertLessEqual(result["confluence"]["score"], 10)
            self.assertTrue(Path(config["engine"]["audit_database"]).exists())


if __name__ == "__main__":
    unittest.main()
