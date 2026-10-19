from __future__ import annotations

import csv
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "mt5_file_bridge.py"
SPEC = importlib.util.spec_from_file_location("mt5_file_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def write_rows(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


class Mt5FileBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        bridge.DATA_ROOT = self.root
        bridge.STALE_SECONDS = 60
        bridge.EXPECTED_ACCOUNT_MODE = "DEMO"
        write_rows(
            self.root / "status.csv",
            [
                ["schema", "atlas-mt5-file-v1"],
                ["generated", int(time.time())],
                ["serverUtcOffsetSeconds", 10800],
                ["connected", 1],
                ["build", 6099],
                ["company", "ZX Capital Markets Ltd."],
                ["server", "ZXCapitalMarkets-Server"],
                ["currency", "USD"],
                ["balance", 100000],
                ["equity", 100000],
                ["margin", 0],
                ["marginFree", 100000],
                ["leverage", 100],
                ["tradeMode", "DEMO"],
                ["tradeAllowed", 1],
            ],
        )
        write_rows(
            self.root / "symbols" / "EURUSD.csv",
            [
                ["symbol", "EURUSD"], ["brokerSymbol", "EURUSD"], ["digits", 5],
                ["tickSize", 0.00001], ["tickValue", 1], ["contractSize", 100000],
                ["minVolume", 0.01], ["maxVolume", 100], ["lotStep", 0.01],
                ["profitCurrency", "USD"], ["marginCurrency", "EUR"],
                ["bid", 1.10000], ["ask", 1.10002], ["time", int(time.time()) + 10800],
            ],
        )
        write_rows(
            self.root / "rates" / "EURUSD_1H.csv",
            [
                ["time", "open", "high", "low", "close", "volume"],
                [11800, 1.0, 1.2, 0.9, 1.1, 10],
                [12800, 1.1, 1.3, 1.0, 1.2, 20],
            ],
        )
        write_rows(
            self.root / "rates" / "EURUSD_D1.csv",
            [
                ["time", "open", "high", "low", "close", "volume"],
                [11800, 1.0, 1.2, 0.9, 1.1, 10],
                [12800, 1.1, 1.3, 1.0, 1.2, 20],
            ],
        )
        write_rows(
            self.root / "ticks" / "EURUSD.csv",
            [
                ["time", "bid", "ask", "last", "volume"],
                [11800.1, 1.1, 1.10002, 0, 1],
                [11800.2, 1.10001, 1.10003, 0, 2],
            ],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_health_and_account_are_demo_and_read_only(self) -> None:
        health = bridge.route_health({})
        account = bridge.route_account({})
        self.assertTrue(health["mt5Connected"])
        self.assertTrue(health["readOnly"])
        self.assertFalse(health["orderSubmission"])
        self.assertEqual(account["tradeMode"], "DEMO")
        self.assertEqual(account["login"], 0)

    def test_quote_rates_ticks_and_symbol_info(self) -> None:
        quote = bridge.route_quote({"symbol": ["EURUSD"]})
        rates = bridge.route_rates({"symbol": ["EURUSD"], "timeframe": ["1H"], "count": ["2"]})
        daily_rates = bridge.route_rates({"symbol": ["EURUSD"], "timeframe": ["1D"], "count": ["2"]})
        ticks = bridge.route_ticks({"symbol": ["EURUSD"], "count": ["1"]})
        info = bridge.route_symbol_info({"symbol": ["EURUSD"]})
        self.assertLess(quote["bid"], quote["ask"])
        self.assertEqual(quote["timestampOffsetSeconds"], 10800)
        self.assertEqual([bar["time"] for bar in rates["bars"]], [1000, 2000])
        self.assertEqual(daily_rates["timeframe"], "1D")
        self.assertEqual([bar["time"] for bar in daily_rates["bars"]], [1000, 2000])
        self.assertEqual(len(ticks["ticks"]), 1)
        self.assertAlmostEqual(ticks["ticks"][0]["time"], 1000.2)
        self.assertEqual(info["tickSize"], 0.00001)

    def test_wrong_account_mode_and_stale_status_fail_closed(self) -> None:
        bridge.EXPECTED_ACCOUNT_MODE = "REAL"
        with self.assertRaises(bridge.BridgeError):
            bridge.route_account({})
        bridge.EXPECTED_ACCOUNT_MODE = "DEMO"
        bridge.STALE_SECONDS = -1
        self.assertFalse(bridge.route_health({})["mt5Connected"])

    def test_legacy_exporter_corrects_positive_machine_clock_skew(self) -> None:
        generated = int(time.time())
        broker_timezone = 2 * 60 * 60
        machine_clock_skew = 75
        write_rows(
            self.root / "status.csv",
            [
                ["schema", "atlas-mt5-file-v1"],
                ["generated", generated],
                ["connected", 1],
                ["build", 6099],
                ["company", "ZX Capital Markets Ltd."],
                ["server", "ZXCapitalMarkets-Server"],
                ["currency", "USD"],
                ["balance", 100000],
                ["equity", 100000],
                ["margin", 0],
                ["marginFree", 100000],
                ["leverage", 100],
                ["tradeMode", "DEMO"],
                ["tradeAllowed", 1],
            ],
        )
        values = bridge._read_key_values(self.root / "symbols" / "EURUSD.csv")
        values["time"] = str(generated + broker_timezone + machine_clock_skew)

        offset = bridge._server_utc_offset(bridge._status(), values)

        self.assertEqual(offset, broker_timezone + machine_clock_skew)

    def test_legacy_exporter_does_not_hide_stale_quotes(self) -> None:
        generated = int(time.time())
        broker_timezone = 2 * 60 * 60
        stale_seconds = 75
        status = {"generated": str(generated)}
        symbol = {"time": str(generated + broker_timezone - stale_seconds)}

        offset = bridge._server_utc_offset(status, symbol)

        self.assertEqual(offset, broker_timezone)


if __name__ == "__main__":
    unittest.main()
