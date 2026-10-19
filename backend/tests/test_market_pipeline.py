from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.http import JsonRequest
from app.core.store import MemoryRecordStore
from app.data.contracts import EconomicObservation, FundamentalSnapshot, MarketBar
from app.data.market.pipeline import (
    BinanceOhlcvProvider,
    FmpFundamentalsProvider,
    FredEconomicProvider,
    MarketDataError,
    MarketIngestionPipeline,
    YahooOhlcvProvider,
)


class StaticTransport:
    def __init__(self, payload: dict[str, Any] | list[Any]) -> None:
        self.payload = payload
        self.requests: list[JsonRequest] = []

    def send(self, request: JsonRequest) -> dict[str, Any] | list[Any]:
        self.requests.append(request)
        return self.payload


class FixtureBars:
    name = "FIXTURE"
    def fetch_bars(self, ticker: str, *, interval: str, start: int | None = None, end: int | None = None) -> list[MarketBar]:
        return [
            MarketBar(ticker, 100, 10, 11, 9, 10.5, 1_000, self.name),
            MarketBar(ticker, 100, 10, 11, 9, 10.5, 1_000, self.name),
            MarketBar(ticker, 200, 10.5, 12, 10, 11.5, 1_200, self.name),
        ]


class FixtureFundamentals:
    name = "FIXTURE_FUNDAMENTALS"
    def fetch_snapshot(self, ticker: str) -> FundamentalSnapshot:
        return FundamentalSnapshot(ticker, 300, {"marketCap": 1_000_000}, self.name)


class FixtureEconomics:
    name = "FIXTURE_ECONOMICS"
    def fetch_series(self, series_id: str, *, start: str | None = None, end: str | None = None) -> list[EconomicObservation]:
        return [EconomicObservation(series_id, 100, 3.0), EconomicObservation(series_id, 200, 3.1)]


class MarketPipelineTests(unittest.TestCase):
    def test_price_fundamental_and_macro_ingestion_are_idempotent(self) -> None:
        store = MemoryRecordStore()
        pipeline = MarketIngestionPipeline(store, bar_providers={"fixture": FixtureBars()}, fundamentals_provider=FixtureFundamentals(), economic_provider=FixtureEconomics())
        first = pipeline.ingest_prices("ABC", "fixture")
        second = pipeline.ingest_prices("ABC", "fixture")
        self.assertEqual(first, {"fetched": 3, "inserted": 2, "duplicates": 1})
        self.assertEqual(second, {"fetched": 3, "inserted": 0, "duplicates": 3})
        self.assertEqual(pipeline.ingest_fundamentals("ABC")["inserted"], 1)
        self.assertEqual(pipeline.ingest_fundamentals("ABC")["duplicates"], 1)
        self.assertEqual(pipeline.ingest_economic_series("DFF")["inserted"], 2)
        self.assertEqual(pipeline.ingest_economic_series("DFF")["inserted"], 0)
        self.assertEqual(len(store.values("price_bars")), 2)

    def test_yahoo_and_binance_normalize_provider_payloads(self) -> None:
        yahoo_transport = StaticTransport({"chart": {"result": [{
            "timestamp": [100, 200],
            "indicators": {"quote": [{
                "open": [10, None], "high": [11, None], "low": [9, None],
                "close": [10.5, None], "volume": [1000, None],
            }]},
        }]}})
        yahoo = YahooOhlcvProvider(yahoo_transport)
        bars = yahoo.fetch_bars("EURUSD=X", interval="1h", start=1, end=2)
        self.assertEqual(len(bars), 1)
        self.assertIn("period1=1", yahoo_transport.requests[0].url)
        self.assertEqual(bars[0].source, "YAHOO")

        binance_transport = StaticTransport([[100_000, "10", "11", "9", "10.5", "5"], ["invalid"]])
        crypto = BinanceOhlcvProvider(binance_transport, "https://binance.example/")
        crypto_bars = crypto.fetch_bars("btc/usdt", start=1, end=2)
        self.assertEqual(len(crypto_bars), 1)
        self.assertIn("symbol=BTCUSDT", binance_transport.requests[0].url)
        with self.assertRaisesRegex(MarketDataError, "must be an array"):
            BinanceOhlcvProvider(StaticTransport({})).fetch_bars("BTCUSDT")

    def test_fundamental_and_economic_adapters_validate_payloads(self) -> None:
        with self.assertRaises(MarketDataError):
            FmpFundamentalsProvider("")
        with self.assertRaises(MarketDataError):
            FredEconomicProvider("")

        fmp = FmpFundamentalsProvider("secret", StaticTransport([{"companyName": "Alpha", "marketCap": 10}]))
        snapshot = fmp.fetch_snapshot("abc")
        self.assertEqual(snapshot.ticker, "ABC")
        self.assertEqual(snapshot.metrics["companyName"], "Alpha")
        with self.assertRaisesRegex(MarketDataError, "no fundamentals"):
            FmpFundamentalsProvider("secret", StaticTransport([])).fetch_snapshot("ABC")

        fred_transport = StaticTransport({"observations": [
            {"date": "2026-08-01", "value": "3.5"},
            {"date": "2026-08-02", "value": "."},
            "invalid",
        ]})
        observations = FredEconomicProvider("secret", fred_transport).fetch_series("dff", start="2026-08-01", end="2026-08-02")
        self.assertEqual([(row.series_id, row.value) for row in observations], [("DFF", 3.5)])
        self.assertIn("observation_start=2026-08-01", fred_transport.requests[0].url)

    def test_pipeline_rejects_missing_providers(self) -> None:
        pipeline = MarketIngestionPipeline(MemoryRecordStore())
        with self.assertRaisesRegex(MarketDataError, "Unknown bar provider"):
            pipeline.ingest_prices("ABC", "missing")
        with self.assertRaisesRegex(MarketDataError, "Fundamentals provider"):
            pipeline.ingest_fundamentals("ABC")
        with self.assertRaisesRegex(MarketDataError, "Economic provider"):
            pipeline.ingest_economic_series("DFF")


if __name__ == "__main__":
    unittest.main()
