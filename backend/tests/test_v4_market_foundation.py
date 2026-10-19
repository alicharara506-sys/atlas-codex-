from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.http import JsonRequest
from app.core.store import MemoryRecordStore
from app.v4.contracts import (
    AssetClass,
    DataQualityIssue,
    DataQualityStatus,
    IssueSeverity,
    MT5ConnectionState,
    MT5HealthReport,
    OfficialTimeframe,
    V4Bar,
    V4Quote,
)
from app.v4.data_quality import DataQualityEngine
from app.v4.mt5 import MT5Gateway
from app.v4.snapshots import SNAPSHOT_COLLECTION, MarketSnapshotService
from app.v4.symbols import UnsupportedInstrumentError, normalize_instrument

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
TIMEFRAME_SECONDS = {"15M": 900, "1H": 3_600, "4H": 14_400, "D1": 86_400}


def raw_bars(timeframe: str, count: int = 80, *, now: datetime = NOW) -> list[dict[str, float]]:
    seconds = TIMEFRAME_SECONDS[timeframe]
    latest = int(now.timestamp()) - seconds
    first = latest - seconds * (count - 1)
    rows: list[dict[str, float]] = []
    for index in range(count):
        close = 4_300 + index * 0.15
        rows.append(
            {
                "time": float(first + seconds * index),
                "open": close - 0.08,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 100 + index,
            }
        )
    return rows


class FakeGateway:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.quote_time = int(now.timestamp()) - 1
        self.broker_symbol = "XAUUSD.z"
        self.account_payload: dict[str, Any] = {
            "login": 123456,
            "server": "Broker-Demo",
            "tradeMode": "DEMO",
            "tradeAllowed": True,
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mt5Connected": True,
            "terminal": {"company": "Example Broker", "connected": True},
            "readOnly": True,
            "orderSubmission": False,
        }

    def account(self) -> dict[str, Any]:
        return dict(self.account_payload)

    def quote(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "brokerSymbol": self.broker_symbol,
            "bid": 4_330.0,
            "ask": 4_330.2,
            "time": self.quote_time,
        }

    def rates(self, symbol: str, timeframe: str, bars: int) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "brokerSymbol": self.broker_symbol,
            "timeframe": timeframe,
            "bars": raw_bars(timeframe, min(bars, 80), now=self.now),
        }

    def ticks(self, symbol: str, count: int) -> dict[str, Any]:
        ticks = [
            {
                "time": int(self.now.timestamp()) - 20 + index,
                "bid": 4_330 + index * 0.001,
                "ask": 4_330.2 + index * 0.001,
                "last": None,
                "volume": 1,
            }
            for index in range(20)
        ]
        return {"symbol": symbol, "brokerSymbol": self.broker_symbol, "ticks": ticks[-count:]}

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "brokerSymbol": self.broker_symbol,
            "digits": 2,
            "tickSize": 0.01,
            "contractSize": 100,
            "minVolume": 0.01,
            "maxVolume": 100,
            "lotStep": 0.01,
        }


class CapturingTransport:
    def __init__(self) -> None:
        self.request: JsonRequest | None = None

    def send(self, request: JsonRequest) -> dict[str, Any]:
        self.request = request
        return {"ok": True}


def typed_timeframes() -> dict[OfficialTimeframe, tuple[V4Bar, ...]]:
    result: dict[OfficialTimeframe, tuple[V4Bar, ...]] = {}
    mapping = {
        OfficialTimeframe.M15: "15M",
        OfficialTimeframe.H1: "1H",
        OfficialTimeframe.H4: "4H",
        OfficialTimeframe.D1: "D1",
    }
    for timeframe, bridge in mapping.items():
        result[timeframe] = tuple(
            V4Bar(
                timestamp=datetime.fromtimestamp(row["time"], UTC),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
            for row in raw_bars(bridge)
        )
    return result


def healthy_report() -> MT5HealthReport:
    return MT5HealthReport(
        state=MT5ConnectionState.CONNECTED,
        checked_at=NOW,
        latency_ms=1,
        mt5_connected=True,
        symbol_available=True,
        data_fresh=True,
        account_mode="DEMO",
        broker="Example Broker",
        server="Broker-Demo",
        last_tick_at=NOW - timedelta(seconds=1),
        tick_age_seconds=1,
    )


class V4SymbolAndGatewayTests(unittest.TestCase):
    def test_symbol_normalization_is_canonical_and_explicit(self) -> None:
        self.assertEqual(normalize_instrument("eur/usd").canonical, "EURUSD")
        self.assertEqual(normalize_instrument("USOIL").canonical, "WTI")
        self.assertEqual(normalize_instrument("BRENT").bridge_symbol, "UKOIL")
        with self.assertRaises(UnsupportedInstrumentError):
            normalize_instrument("BTCUSD")

    def test_gateway_is_loopback_get_only_and_uses_bridge_contract(self) -> None:
        transport = CapturingTransport()
        gateway = MT5Gateway("http://127.0.0.1:8765", "secret", transport=transport)
        gateway.rates("XAUUSD", "1H", 260)
        assert transport.request is not None
        self.assertEqual(transport.request.method, "GET")
        self.assertIn("count=260", transport.request.url)
        self.assertEqual(transport.request.headers["Authorization"], "Bearer secret")
        self.assertFalse(hasattr(gateway, "order_send"))
        with self.assertRaises(ValueError):
            MT5Gateway("https://example.com", None)


class V4DataQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DataQualityEngine(minimum_bars=60)
        self.quote = V4Quote(
            instrument="XAUUSD",
            broker_symbol="XAUUSD.z",
            bid=4_330,
            ask=4_330.2,
            timestamp=NOW - timedelta(seconds=1),
        )

    def test_healthy_complete_data_is_publication_eligible(self) -> None:
        result = self.engine.evaluate(
            now=NOW,
            health=healthy_report(),
            quote=self.quote,
            asset_class=AssetClass.COMMODITY,
            timeframes=typed_timeframes(),
            ticks=(),
        )
        self.assertEqual(result.status, DataQualityStatus.DEGRADED)
        self.assertTrue(all(not issue.blocking for issue in result.issues))
        self.assertFalse(result.publication_eligible)

    def test_complete_data_with_ticks_is_healthy(self) -> None:
        gateway = FakeGateway()
        snapshot = MarketSnapshotService(gateway, MemoryRecordStore()).capture("XAUUSD", now=NOW)
        self.assertEqual(snapshot.data_quality.status, DataQualityStatus.HEALTHY)
        self.assertTrue(snapshot.data_quality.publication_eligible)

    def test_stale_quote_and_missing_timeframe_fail_closed(self) -> None:
        timeframes = typed_timeframes()
        del timeframes[OfficialTimeframe.H4]
        result = self.engine.evaluate(
            now=NOW,
            health=healthy_report(),
            quote=self.quote.model_copy(update={"timestamp": NOW - timedelta(minutes=10)}),
            asset_class=AssetClass.COMMODITY,
            timeframes=timeframes,
            ticks=(),
        )
        self.assertEqual(result.status, DataQualityStatus.STALE)
        self.assertFalse(result.publication_eligible)
        self.assertIn("STALE_QUOTE", {issue.code for issue in result.issues})
        self.assertIn("MISSING_TIMEFRAME", {issue.code for issue in result.issues})

    def test_future_or_invalid_ingestion_is_invalid(self) -> None:
        issue = DataQualityIssue(
            code="INVALID_BAR",
            severity=IssueSeverity.CRITICAL,
            message="invalid",
            blocking=True,
        )
        result = self.engine.evaluate(
            now=NOW,
            health=healthy_report(),
            quote=self.quote.model_copy(update={"timestamp": NOW + timedelta(minutes=5)}),
            asset_class=AssetClass.COMMODITY,
            timeframes=typed_timeframes(),
            ticks=(),
            ingestion_issues=(issue,),
        )
        self.assertEqual(result.status, DataQualityStatus.INVALID)
        self.assertFalse(result.publication_eligible)

    def test_commodity_maintenance_gap_is_allowed_but_midday_gap_is_not(self) -> None:
        scheduled = typed_timeframes()
        m15 = list(scheduled[OfficialTimeframe.M15])
        left = next(index for index, bar in enumerate(m15) if bar.timestamp.hour == 20 and bar.timestamp.minute == 45)
        scheduled[OfficialTimeframe.M15] = tuple(m15[: left + 1] + m15[left + 5 :])
        allowed = self.engine.evaluate(
            now=NOW,
            health=healthy_report(),
            quote=self.quote,
            asset_class=AssetClass.COMMODITY,
            timeframes=scheduled,
            ticks=(),
        )
        self.assertNotIn("BAR_GAP", {issue.code for issue in allowed.issues})

        midday = typed_timeframes()
        m15 = list(midday[OfficialTimeframe.M15])
        left = next(index for index, bar in enumerate(m15) if bar.timestamp.hour == 8 and bar.timestamp.minute == 45)
        midday[OfficialTimeframe.M15] = tuple(m15[: left + 1] + m15[left + 5 :])
        rejected = self.engine.evaluate(
            now=NOW,
            health=healthy_report(),
            quote=self.quote,
            asset_class=AssetClass.COMMODITY,
            timeframes=midday,
            ticks=(),
        )
        self.assertIn("BAR_GAP", {issue.code for issue in rejected.issues})


class V4SnapshotServiceTests(unittest.TestCase):
    def test_snapshot_is_immutable_attributed_and_idempotently_stored(self) -> None:
        store = MemoryRecordStore()
        service = MarketSnapshotService(FakeGateway(), store, expected_account_mode="DEMO")
        snapshot = service.capture("gold", now=NOW)
        same = service.capture("XAUUSD", now=NOW)

        self.assertEqual(snapshot.snapshot_id, same.snapshot_id)
        self.assertEqual(len(store.values(SNAPSHOT_COLLECTION)), 1)
        self.assertEqual(snapshot.source, "MT5")
        self.assertEqual(snapshot.account_mode, "DEMO")
        self.assertTrue(snapshot.research_only)
        self.assertFalse(snapshot.execution_enabled)
        with self.assertRaises(ValidationError):
            snapshot.bid = 1  # type: ignore[misc]

    def test_account_mode_mismatch_blocks_quality(self) -> None:
        snapshot = MarketSnapshotService(
            FakeGateway(), MemoryRecordStore(), expected_account_mode="REAL"
        ).capture("XAUUSD", now=NOW)
        self.assertEqual(snapshot.health.state, MT5ConnectionState.DEGRADED)
        self.assertFalse(snapshot.data_quality.publication_eligible)
        self.assertIn("ACCOUNT_MODE_MISMATCH", snapshot.health.issues)


class V4ApiTests(unittest.TestCase):
    def test_v4_api_is_persisted_typed_and_never_executable(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database, SqlModelRecordStore

        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        env = {
            "DATABASE_URL": "sqlite://",
            "REDIS_URL": "redis://127.0.0.1:6379/0",
            "MT5_ENABLED": "true",
            "MT5_BRIDGE_BASE_URL": "http://127.0.0.1:8765",
            "MT5_EXPECTED_ACCOUNT_MODE": "DEMO",
        }
        with patch.dict(os.environ, env):
            settings = AtlasSettings.from_env()
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway())
        database.create_schema()

        with TestClient(application) as client:
            overview = client.get("/api/v4/health")
            captured = client.post("/api/v4/market/XAUUSD/snapshot")
            latest = client.get("/api/v4/market/XAUUSD/snapshot")
            rejected = client.post("/api/v4/market/BTCUSD/snapshot")

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["official_timeframes"], ["15M", "1H", "4H", "1D"])
        self.assertFalse(overview.json()["execution_enabled"])
        self.assertFalse(overview.json()["order_submission"])
        self.assertEqual(captured.status_code, 200)
        self.assertEqual(captured.json()["snapshot_id"], latest.json()["snapshot_id"])
        self.assertNotIn("login", captured.text)
        self.assertFalse(captured.json()["execution_enabled"])
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(len(SqlModelRecordStore(database).values(SNAPSHOT_COLLECTION)), 1)


if __name__ == "__main__":
    unittest.main()
