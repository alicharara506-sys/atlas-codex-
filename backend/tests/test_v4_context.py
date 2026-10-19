from __future__ import annotations

import math
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.store import MemoryRecordStore
from app.v4.context_contracts import (
    ContextAvailability,
    ContextComputationRequest,
    COTReport,
    EconomicEvent,
    NewsItem,
    NumericSeries,
    RiskLevel,
    TimedValue,
)
from app.v4.context_service import CONTEXT_COLLECTION, V4ContextService
from app.v4.derivatives import UnavailableDerivativesProvider
from app.v4.macro import GoldIntelligenceEngine, MacroIntelligenceEngine
from app.v4.news_events import EventRiskEngine, NewsRiskEngine
from app.v4.positioning import CFTCDataError, CFTCPositioningClient, PositioningEngine

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def numeric_series(name: str, count: int = 130, *, transform: float = 1.0) -> NumericSeries:
    start = NOW - timedelta(days=count - 1)
    return NumericSeries(
        name=name,
        unit="INDEX",
        source="TEST_NUMERIC_FIXTURE",
        values=tuple(
            TimedValue(
                timestamp=start + timedelta(days=index),
                value=100 + transform * (index + math.sin(index / 3)),
            )
            for index in range(count)
        ),
    )


def cot_reports(count: int = 30) -> tuple[COTReport, ...]:
    start = NOW - timedelta(weeks=count)
    return tuple(
        COTReport(
            market_name="GOLD - COMMODITY EXCHANGE INC.",
            report_date=start + timedelta(weeks=index),
            commercial_long=100_000 + index * 200,
            commercial_short=120_000 - index * 100,
            noncommercial_long=150_000 + index * 500,
            noncommercial_short=100_000 + index * 100,
            open_interest=500_000 + index * 1_000,
        )
        for index in range(count)
    )


class MacroAndGoldTests(unittest.TestCase):
    def test_macro_is_numeric_first_and_rejects_future_observations(self) -> None:
        series = numeric_series("DXY")
        result = MacroIntelligenceEngine().calculate(
            "XAUUSD",
            (series,),
            as_of=NOW,
            required_factors=("DXY", "US10Y_REAL"),
        )
        self.assertEqual(result.availability, ContextAvailability.DEGRADED)
        self.assertEqual(result.missing_factors, ("US10Y_REAL",))
        self.assertFalse(result.llm_generated)
        self.assertFalse(result.signal_authority)

        future = series.model_copy(
            update={
                "values": series.values
                + (TimedValue(timestamp=NOW + timedelta(days=1), value=250),)
            }
        )
        with self.assertRaises(ValueError):
            MacroIntelligenceEngine().calculate("XAUUSD", (future,), as_of=NOW)

    def test_gold_engine_measures_rolling_relationships_without_direction_assumption(self) -> None:
        result = GoldIntelligenceEngine().calculate(
            numeric_series("XAUUSD"),
            (numeric_series("DXY", transform=0.75),),
            as_of=NOW,
        )
        relationship = result.relationships[0]
        self.assertEqual(result.availability, ContextAvailability.AVAILABLE)
        self.assertIsNotNone(relationship.correlation_20)
        self.assertIsNotNone(relationship.correlation_60)
        self.assertIsNotNone(relationship.correlation_120)
        self.assertFalse(relationship.directional_assumption)
        self.assertFalse(result.hard_coded_real_yield_direction)


class PositioningTests(unittest.TestCase):
    def test_positioning_statistics_do_not_force_a_contrarian_signal(self) -> None:
        result = PositioningEngine().calculate(cot_reports(), as_of=NOW)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.availability, ContextAvailability.AVAILABLE)
        self.assertIsNotNone(result.weekly_change)
        self.assertIsNotNone(result.z_score)
        self.assertIsNotNone(result.percentile)
        self.assertFalse(result.automatic_contrarian_interpretation)
        self.assertFalse(result.signal_authority)

    def test_cftc_client_uses_official_free_dataset_and_strict_schema(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, "publicreporting.cftc.gov")
            self.assertIn("6dca-aqww", request.url.path)
            return httpx.Response(
                200,
                json=[
                    {
                        "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
                        "report_date_as_yyyy_mm_dd": "2026-08-11T00:00:00.000",
                        "open_interest_all": "500000",
                        "noncomm_positions_long_all": "150000",
                        "noncomm_positions_short_all": "100000",
                        "comm_positions_long_all": "100000",
                        "comm_positions_short_all": "120000",
                    }
                ],
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        reports = CFTCPositioningClient(client=client).fetch("GOLD", limit=20)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].source, "CFTC_LEGACY_FUTURES_ONLY")

        with self.assertRaises(ValueError):
            CFTCPositioningClient(base_url="https://example.com/data")
        with self.assertRaises(ValueError):
            CFTCPositioningClient(client=client).fetch("GOLD' OR 1=1")

    def test_invalid_cftc_payload_fails_closed(self) -> None:
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[{}])))
        self.addCleanup(client.close)
        with self.assertRaises(CFTCDataError):
            CFTCPositioningClient(client=client).fetch("GOLD")


class DefensiveContextTests(unittest.TestCase):
    def test_news_deduplicates_and_never_emits_trade_direction(self) -> None:
        items = (
            NewsItem(
                title="FOMC unexpected emergency rate decision",
                source="Newswire",
                published_at=NOW - timedelta(minutes=10),
                url="https://example.com/story?utm_source=x",
            ),
            NewsItem(
                title="FOMC unexpected emergency rate decision - repeated",
                source="Republisher",
                published_at=NOW - timedelta(minutes=8),
                url="https://example.com/story?utm_source=y",
            ),
        )
        result = NewsRiskEngine().calculate(items, as_of=NOW)
        self.assertEqual(result.unique_count, 1)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.risk, RiskLevel.EXTREME)
        self.assertTrue(result.candidate_veto)
        self.assertIsNone(result.primary_signal_direction)

    def test_high_impact_event_near_release_is_an_absolute_candidate_veto(self) -> None:
        event = EconomicEvent(
            event_id="us-cpi-2026-08",
            name="US CPI",
            currency="USD",
            scheduled_at=NOW + timedelta(minutes=10),
            importance=RiskLevel.HIGH,
            source="TEST_CALENDAR",
            historical_volatility_percentile=0.95,
        )
        result = EventRiskEngine().calculate("EURUSD", (event,), as_of=NOW)
        self.assertEqual(result.risk, RiskLevel.EXTREME)
        self.assertTrue(result.candidate_veto)
        self.assertTrue(result.assessments[0].affects_instrument)

    def test_date_only_official_release_vetoes_the_day_without_inventing_minutes(self) -> None:
        event = EconomicEvent(
            event_id="fred-cpi-date-only",
            name="Consumer Price Index",
            currency="USD",
            scheduled_at=NOW.replace(hour=0, minute=0),
            importance=RiskLevel.HIGH,
            source="FRED_RELEASE_CALENDAR",
            time_precision="DATE_ONLY",
        )
        result = EventRiskEngine().calculate("XAUUSD", (event,), as_of=NOW)
        self.assertTrue(result.candidate_veto)
        self.assertEqual(result.nearest_event_id, event.event_id)
        self.assertIsNone(result.assessments[0].minutes_to_event)
        self.assertEqual(result.assessments[0].time_precision, "DATE_ONLY")

    def test_derivatives_default_is_explicitly_unavailable(self) -> None:
        result = UnavailableDerivativesProvider().fetch("XAUUSD", as_of=NOW)
        self.assertEqual(result.availability, ContextAvailability.DATA_UNAVAILABLE)
        self.assertIsNone(result.implied_volatility)
        self.assertFalse(result.fabricated)


class V4ContextServiceTests(unittest.TestCase):
    def test_context_is_versioned_idempotent_persisted_and_non_authoritative(self) -> None:
        store = MemoryRecordStore()
        service = V4ContextService(store)
        request = ContextComputationRequest(
            as_of=NOW,
            macro_series=(numeric_series("DXY"),),
            required_macro_factors=("DXY", "US10Y_REAL"),
            gold_prices=numeric_series("XAUUSD"),
            cot_reports=cot_reports(),
            news=(
                NewsItem(
                    title="FOMC rate decision due",
                    source="TEST_NEWS",
                    published_at=NOW - timedelta(minutes=5),
                ),
            ),
            events=(
                EconomicEvent(
                    event_id="fomc",
                    name="FOMC",
                    currency="USD",
                    scheduled_at=NOW + timedelta(minutes=20),
                    importance=RiskLevel.HIGH,
                    source="TEST_CALENDAR",
                ),
            ),
        )
        first = service.compute("GOLD", request)
        second = service.compute("XAUUSD", request)
        self.assertEqual(first.context_set_id, second.context_set_id)
        self.assertEqual(len(store.values(CONTEXT_COLLECTION)), 1)
        self.assertTrue(first.candidate_veto)
        self.assertFalse(first.signal_authority)
        self.assertFalse(first.execution_enabled)
        self.assertEqual(first.derivatives.availability, ContextAvailability.DATA_UNAVAILABLE)
        self.assertEqual(service.latest("XAUUSD").context_set_id, first.context_set_id)  # type: ignore[union-attr]


class V4ContextApiTests(unittest.TestCase):
    def test_context_api_has_no_signal_or_execution_authority(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database
        from tests.test_v4_market_foundation import FakeGateway

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite://",
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "MT5_ENABLED": "false",
            },
        ):
            settings = AtlasSettings.from_env()
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway())
        database.create_schema()
        request = ContextComputationRequest(as_of=NOW, macro_series=(numeric_series("DXY"),))

        with TestClient(application) as client:
            computed = client.post("/api/v4/context/XAUUSD/compute", json=request.model_dump(mode="json"))
            latest = client.get("/api/v4/context/XAUUSD/latest")
            disabled_refresh = client.post("/api/v4/context/XAUUSD/refresh")

        self.assertEqual(computed.status_code, 200)
        self.assertEqual(latest.status_code, 200)
        body = computed.json()
        self.assertEqual(body["context_set_id"], latest.json()["context_set_id"])
        self.assertFalse(body["signal_authority"])
        self.assertFalse(body["execution_enabled"])
        self.assertFalse(body["provider_managed_ingestion"])
        self.assertNotIn("direction", body)
        self.assertEqual(disabled_refresh.status_code, 503)
        self.assertEqual(disabled_refresh.json()["detail"]["code"], "V4_CONTEXT_REFRESH_DISABLED")


if __name__ == "__main__":
    unittest.main()
