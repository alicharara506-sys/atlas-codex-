from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.http import JsonRequest
from app.core.store import MemoryRecordStore
from app.data.contracts import SentimentObservation
from app.data.sentiment.pipeline import (
    EodhdSocialProvider,
    FinnhubNewsProvider,
    KeywordSentiment,
    LicensedNewsProvider,
    OnChainMetricProvider,
    SentimentDataError,
    SentimentIngestionPipeline,
)


class StaticTransport:
    def __init__(self, payload: dict[str, Any] | list[Any]) -> None:
        self.payload = payload
        self.requests: list[JsonRequest] = []

    def send(self, request: JsonRequest) -> dict[str, Any] | list[Any]:
        self.requests.append(request)
        return self.payload


class PositiveProvider:
    name = "NEWS"
    def fetch(self, ticker: str, *, start: int, end: int) -> list[SentimentObservation]:
        return [SentimentObservation(ticker, self.name, start + 60, 2.0, "record growth")]


class NegativeProvider:
    name = "SOCIAL"
    def fetch(self, ticker: str, *, start: int, end: int) -> list[SentimentObservation]:
        return [SentimentObservation(ticker, self.name, start + 120, -0.5, "bearish decline", "SOCIAL")]


class BrokenProvider:
    name = "BROKEN"
    def fetch(self, ticker: str, *, start: int, end: int) -> list[SentimentObservation]:
        raise RuntimeError("fixture unavailable")


class SentimentPipelineTests(unittest.TestCase):
    def test_scores_are_bounded_weighted_and_idempotent(self) -> None:
        store = MemoryRecordStore()
        pipeline = SentimentIngestionPipeline(store, [PositiveProvider(), NegativeProvider(), BrokenProvider()], {"NEWS": 2, "SOCIAL": 1})
        first = pipeline.ingest("ABC", start=1_720_000_000, end=1_720_086_400)
        second = pipeline.ingest("ABC", start=1_720_000_000, end=1_720_086_400)
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["duplicates"], 2)
        self.assertEqual(first["daily"][0]["score"], 0.5)
        self.assertIn("BROKEN", first["provider_errors"])
        self.assertEqual(KeywordSentiment.score("record growth but bearish decline"), 0.0)

    def test_news_social_and_onchain_adapters_normalize_payloads(self) -> None:
        with self.assertRaises(SentimentDataError):
            FinnhubNewsProvider("")
        with self.assertRaises(SentimentDataError):
            EodhdSocialProvider("")
        with self.assertRaises(SentimentDataError):
            LicensedNewsProvider("Reuters", "http://unsafe.example", "secret")
        with self.assertRaises(SentimentDataError):
            OnChainMetricProvider("http://unsafe.example")

        news_transport = StaticTransport([
            {"headline": "Record growth", "summary": "profit surge", "datetime": 100, "id": 7, "url": "https://news.example/7"},
            "invalid",
        ])
        news = FinnhubNewsProvider("secret", news_transport).fetch("ABC", start=90, end=110)
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].score, 1.0)
        with self.assertRaisesRegex(SentimentDataError, "must be an array"):
            FinnhubNewsProvider("secret", StaticTransport({})).fetch("ABC", start=90, end=110)

        social_transport = StaticTransport({"ABC": [
            {"date": "2026-08-01T00:00:00Z", "sentiment": 75},
            {"date": "invalid", "normalized": -0.5},
            "invalid",
        ]})
        social = EodhdSocialProvider("secret", social_transport).fetch("ABC", start=100, end=200)
        self.assertEqual([row.score for row in social], [0.75, -0.5])

        licensed_transport = StaticTransport({"items": [
            {"headline": "profit upgrade", "timestamp": 100, "id": "a"},
            {"text": "loss plunge", "timestamp": 101, "score": -0.8},
        ]})
        licensed = LicensedNewsProvider("Reuters", "https://licensed.example/feed", "secret", licensed_transport)
        licensed_rows = licensed.fetch("ABC", start=90, end=110)
        self.assertEqual([row.score for row in licensed_rows], [1.0, -0.8])
        self.assertEqual(licensed_transport.requests[0].headers["Authorization"], "Bearer secret")

        chain_transport = StaticTransport({"data": [{"timestamp": 100, "score": 0.4}, "invalid"]})
        chain = OnChainMetricProvider("https://chain.example/metrics", "secret", chain_transport)
        self.assertEqual(chain.fetch("BTC", start=90, end=110)[0].score, 0.4)

    def test_pipeline_rejects_bad_window_and_filters_out_of_range_rows(self) -> None:
        pipeline = SentimentIngestionPipeline(MemoryRecordStore(), [PositiveProvider()])
        with self.assertRaisesRegex(SentimentDataError, "Invalid sentiment time window"):
            pipeline.ingest("ABC", start=0, end=1)
        result = pipeline.ingest("ABC", start=100, end=100)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["daily"], [])


if __name__ == "__main__":
    unittest.main()
