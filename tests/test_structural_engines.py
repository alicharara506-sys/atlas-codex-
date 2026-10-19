from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from chart_renderer import render_chart  # noqa: E402
from smc_engine import SMCAnalyzer  # noqa: E402
from wyckoff_engine import WyckoffAnalyzer  # noqa: E402


class FrameLike:
    """Small DataFrame-compatible test double without a pandas dependency."""

    def __init__(self, rows: list[dict[str, float]]) -> None:
        self.rows = rows

    def to_dict(self, orient: str) -> list[dict[str, float]]:
        if orient != "records":
            raise ValueError("records orientation required")
        return self.rows


def _bar(index: int, open_: float, high: float, low: float, close: float, volume: float) -> dict[str, float]:
    return {
        "time": 1_720_000_000 + index * 3_600,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def accumulation_bars() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    price = 108.0
    for index in range(20):
        close = price - 0.16
        rows.append(_bar(index, price, price + 0.35, close - 0.35, close, 900 + (index % 5) * 45))
        price = close

    rows.extend(
        [
            _bar(20, 104.9, 105.4, 101.0, 104.0, 4_000),  # selling climax
            _bar(21, 104.0, 105.8, 103.7, 105.4, 1_900),
            _bar(22, 105.4, 106.5, 104.4, 106.0, 1_700),  # automatic rally
            _bar(23, 106.0, 106.1, 104.5, 105.0, 1_250),
            _bar(24, 105.0, 105.4, 103.4, 104.0, 1_150),
            _bar(25, 104.0, 104.8, 102.4, 103.0, 1_100),
            _bar(26, 103.0, 104.0, 101.8, 102.6, 1_050),
            _bar(27, 103.8, 104.5, 101.3, 103.5, 1_200),  # secondary test
            _bar(28, 103.5, 104.7, 102.9, 104.2, 1_100),
            _bar(29, 104.2, 105.0, 103.2, 103.8, 1_000),
            _bar(30, 103.8, 104.3, 102.0, 102.8, 1_050),
            _bar(31, 102.6, 103.4, 101.0, 102.2, 1_350),  # spring and reclaim
        ]
    )
    price = 102.2
    for index in range(32, 48):
        close = price + 0.28
        rows.append(_bar(index, price, close + 0.35, price - 0.25, close, 1_100 + (index % 4) * 80))
        price = close
    return rows


def smc_bars() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    price = 100.0
    for index in range(20):
        close = price + (0.12 if index % 2 == 0 else -0.08)
        rows.append(_bar(index, price, max(price, close) + 0.30, min(price, close) - 0.30, close, 1_000))
        price = close
    rows.extend(
        [
            _bar(20, 100.5, 100.8, 99.5, 99.8, 1_300),  # last down candle / bullish OB
            _bar(21, 99.8, 103.2, 99.7, 102.8, 2_800),  # displacement
            _bar(22, 102.8, 104.0, 102.0, 103.8, 2_000),  # leaves bullish FVG
        ]
    )
    price = 103.8
    for index in range(23, 42):
        close = price + 0.14
        rows.append(_bar(index, price, close + 0.35, price - 0.30, close, 1_100))
        price = close
    return rows


class StructuralEngineTests(unittest.TestCase):
    def test_wyckoff_detects_accumulation_sequence_from_frame_like_input(self) -> None:
        result = WyckoffAnalyzer().analyze(FrameLike(accumulation_bars()))
        event_types = {event["type"] for event in result["events"]}
        self.assertEqual(result["phase"], "ACCUMULATION")
        self.assertTrue({"SC", "AR", "ST"}.issubset(event_types))
        self.assertGreater(result["confidence"], 0)

    def test_smc_detects_untested_order_block_and_unmitigated_fvg(self) -> None:
        result = SMCAnalyzer().analyze(smc_bars())
        self.assertTrue(any(item["type"] == "bullish" and item["status"] == "untested" for item in result["order_blocks"]))
        self.assertTrue(any(item["type"] == "bullish" and not item["mitigated"] for item in result["fvgs"]))
        self.assertEqual(result["method"], "DETERMINISTIC_CLOSED_BAR_STRUCTURE")

    def test_renderer_exports_valid_static_png_with_optional_annotations(self) -> None:
        bars = smc_bars()
        smc = SMCAnalyzer().analyze(bars)
        wyckoff = WyckoffAnalyzer().analyze(bars)
        signal = {
            "metadata": {"instrument": "XAUUSD", "timeframe": "1H"},
            "signal": {
                "direction": "MODERATE_LONG",
                "entry_zone": {"min": 103.0, "max": 103.4},
                "stop_loss": 100.5,
                "take_profit": {"tp1": 105.0, "tp2": 106.5, "tp3": 108.0},
            },
            "indicators": {
                "avwap": {"value": 102.4},
                "market_structure": {"bos": "BULLISH_BOS", "choch": "NONE"},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            result = render_chart(bars, signal, wyckoff=wyckoff, smc=smc, output_directory=directory, width=800, height=500)
            output = Path(result["path"])
            data = output.read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (900, 600))
            self.assertEqual(result["mimeType"], "image/png")
            self.assertTrue(result["static"])

            minimal = render_chart(bars, signal, wyckoff={}, smc={}, output_directory=directory, width=640, height=400)
            self.assertTrue(Path(minimal["path"]).exists())


if __name__ == "__main__":
    unittest.main()
