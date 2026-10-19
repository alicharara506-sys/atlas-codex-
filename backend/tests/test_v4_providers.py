from __future__ import annotations

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.http import JsonRequest
from app.core.store import MemoryRecordStore
from app.data.contracts import EconomicObservation
from app.v4.context_contracts import ContextAvailability
from app.v4.context_ingestion import V4ContextIngestionService
from app.v4.context_service import V4ContextService
from app.v4.feature_service import V4FeatureService
from app.v4.positioning import CFTCPositioningClient
from app.v4.providers import (
    DEFAULT_MACRO_MANIFEST,
    OFFICIAL_RSS_FEEDS,
    FredMacroBundleProvider,
    FredReleaseCalendarProvider,
    OfficialRssNewsProvider,
)
from app.v4.snapshots import MarketSnapshotService
from tests.test_v4_market_foundation import NOW, FakeGateway


class StubEconomicProvider:
    name = "FRED"

    def fetch_series(
        self,
        series_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> list[EconomicObservation]:
        del start, end
        first = NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=129)
        return [
            EconomicObservation(
                series_id=series_id,
                timestamp=int((first + timedelta(days=index)).timestamp()),
                value=100 + index * 0.1,
            )
            for index in range(130)
        ]


class StubJsonTransport:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.request: JsonRequest | None = None

    def send(self, request: JsonRequest) -> dict[str, Any]:
        self.request = request
        return self.payload


def _cftc_client() -> CFTCPositioningClient:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        rows = []
        for index in range(30):
            report_date = (NOW - timedelta(weeks=29 - index)).date().isoformat()
            rows.append(
                {
                    "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
                    "report_date_as_yyyy_mm_dd": report_date,
                    "open_interest_all": str(500_000 + index * 1_000),
                    "noncomm_positions_long_all": str(150_000 + index * 500),
                    "noncomm_positions_short_all": str(100_000 + index * 100),
                    "comm_positions_long_all": str(100_000 + index * 200),
                    "comm_positions_short_all": str(120_000 - index * 100),
                }
            )
        return httpx.Response(200, json=list(reversed(rows)))

    return CFTCPositioningClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _rss_provider() -> OfficialRssNewsProvider:
    xml = f"""<?xml version="1.0"?>
    <rss><channel><item>
      <title>Federal Reserve issues FOMC monetary policy statement</title>
      <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary-test.htm</link>
      <pubDate>{(NOW - timedelta(hours=2)).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
    </item><item><title>Untimed item must not be fabricated</title></item></channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        self_url = OFFICIAL_RSS_FEEDS["FEDERAL_RESERVE"]
        if str(request.url) != self_url:
            return httpx.Response(404)
        return httpx.Response(200, content=xml.encode("utf-8"))

    return OfficialRssNewsProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        feeds={"FEDERAL_RESERVE": OFFICIAL_RSS_FEEDS["FEDERAL_RESERVE"]},
    )


class TrustedProviderTests(unittest.TestCase):
    def test_fred_macro_manifest_preserves_logical_names_and_provenance(self) -> None:
        provider = FredMacroBundleProvider(StubEconomicProvider())
        series, issues = provider.fetch(as_of=NOW)
        self.assertFalse(issues)
        self.assertEqual({row.name for row in series}, {spec.name for spec in DEFAULT_MACRO_MANIFEST})
        self.assertTrue(all(row.source.startswith("FRED:") for row in series))
        self.assertNotIn("DXY", {row.name for row in series})
        self.assertIn("USD_BROAD_INDEX", {row.name for row in series})

    def test_fred_calendar_is_date_only_and_never_invents_release_time(self) -> None:
        transport = StubJsonTransport(
            {
                "release_dates": [
                    {
                        "release_id": 10,
                        "release_name": "Consumer Price Index",
                        "date": NOW.date().isoformat(),
                    },
                    {"release_id": 999, "release_name": "Unrelated release", "date": NOW.date().isoformat()},
                ]
            }
        )
        provider = FredReleaseCalendarProvider("a" * 32, transport=transport)
        events = provider.fetch(as_of=NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].time_precision, "DATE_ONLY")
        self.assertEqual(events[0].scheduled_at.hour, 0)
        self.assertEqual(events[0].source, "FRED_RELEASE_CALENDAR")
        assert transport.request is not None
        self.assertEqual(transport.request.url.split("?", maxsplit=1)[0], "https://api.stlouisfed.org/fred/releases/dates")

    def test_official_rss_accepts_only_timed_items_from_allowlisted_feed(self) -> None:
        provider = _rss_provider()
        self.addCleanup(provider.client.close)  # type: ignore[union-attr]
        items, issues = provider.fetch(as_of=NOW)
        self.assertFalse(issues)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "FEDERAL_RESERVE")
        self.assertLess(items[0].published_at, NOW)
        with self.assertRaises(ValueError):
            OfficialRssNewsProvider(feeds={"BLOG": "https://example.com/rss"})


class ContextIngestionTests(unittest.TestCase):
    def test_refresh_combines_trusted_sources_with_mt5_lineage_and_no_authority(self) -> None:
        store = MemoryRecordStore()
        snapshot = MarketSnapshotService(FakeGateway(), store).capture("XAUUSD", now=NOW)
        V4FeatureService(store).compute(snapshot, computed_at=NOW)
        rss = _rss_provider()
        self.addCleanup(rss.client.close)  # type: ignore[union-attr]
        cftc = _cftc_client()
        self.addCleanup(cftc.client.close)  # type: ignore[union-attr]
        calendar = FredReleaseCalendarProvider(
            "a" * 32,
            transport=StubJsonTransport(
                {
                    "release_dates": [
                        {
                            "release_id": 10,
                            "release_name": "Consumer Price Index",
                            "date": NOW.date().isoformat(),
                        }
                    ]
                }
            ),
        )
        service = V4ContextIngestionService(
            store,
            V4ContextService(store),
            fred_macro=FredMacroBundleProvider(StubEconomicProvider()),
            fred_calendar=calendar,
            official_news=rss,
            cftc=cftc,
        )
        context = service.refresh("GOLD", as_of=NOW)
        self.assertTrue(context.provider_managed_ingestion)
        self.assertFalse(context.provider_issues)
        self.assertEqual(context.availability, ContextAvailability.AVAILABLE)
        self.assertIsNotNone(context.positioning)
        self.assertIsNotNone(context.gold)
        self.assertTrue(context.events.candidate_veto)
        self.assertFalse(context.signal_authority)
        self.assertFalse(context.execution_enabled)
        self.assertEqual(context.gold.relationships[0].aligned_return_samples, 79)  # type: ignore[union-attr]

    def test_unconfigured_refresh_is_explicitly_unavailable_not_fabricated(self) -> None:
        context = V4ContextIngestionService(
            MemoryRecordStore(),
            V4ContextService(MemoryRecordStore()),
        ).refresh("EURUSD", as_of=NOW, persist=False)
        self.assertEqual(context.availability, ContextAvailability.DATA_UNAVAILABLE)
        self.assertTrue(context.provider_managed_ingestion)
        self.assertIn("FRED_MACRO_NOT_CONFIGURED", context.provider_issues)
        self.assertFalse(context.execution_enabled)


if __name__ == "__main__":
    unittest.main()
