"""Canonical immutable MT5 snapshot capture and persistence service."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import Enum
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.store import RecordStore, stable_key
from app.v4.contracts import (
    DataQualityIssue,
    IssueSeverity,
    MarketSnapshot,
    MT5HealthReport,
    OfficialTimeframe,
    V4Bar,
    V4Quote,
    V4SymbolMetadata,
    V4Tick,
    VolumeProvenance,
)
from app.v4.data_quality import DataQualityEngine
from app.v4.mt5 import GatewayError, MT5GatewayProtocol, MT5HealthMonitor
from app.v4.symbols import BRIDGE_TIMEFRAMES, OFFICIAL_TIMEFRAMES, normalize_instrument

SNAPSHOT_COLLECTION = "v4_market_snapshots"


class SnapshotUnavailable(RuntimeError):
    """A safe, credential-free explanation for an unavailable live snapshot."""

    def __init__(self, code: str, message: str, health: MT5HealthReport | None = None):
        super().__init__(message)
        self.code = code
        self.health = health


class MarketSnapshotService:
    """Capture one canonical V4 snapshot without invoking any signal logic."""

    def __init__(
        self,
        gateway: MT5GatewayProtocol,
        store: RecordStore,
        *,
        enabled: bool = True,
        expected_account_mode: str | None = None,
        bars_per_timeframe: int = 260,
        tick_count: int = 2_000,
        quality_engine: DataQualityEngine | None = None,
        health_monitor: MT5HealthMonitor | None = None,
    ) -> None:
        if not 60 <= bars_per_timeframe <= 5_000:
            raise ValueError("bars_per_timeframe must be between 60 and 5000")
        if not 1 <= tick_count <= 50_000:
            raise ValueError("tick_count must be between 1 and 50000")
        self.gateway = gateway
        self.store = store
        self.enabled = enabled
        self.expected_account_mode = expected_account_mode
        self.bars_per_timeframe = bars_per_timeframe
        self.tick_count = tick_count
        self.quality_engine = quality_engine or DataQualityEngine()
        self.health_monitor = health_monitor or MT5HealthMonitor()

    def health(self, instrument: str, *, now: datetime | None = None) -> MT5HealthReport:
        definition = normalize_instrument(instrument)
        checked_at = (now or datetime.now(UTC)).astimezone(UTC)
        report, _, _ = self._probe(definition.bridge_symbol, checked_at)
        return report

    def capture(
        self,
        instrument: str,
        *,
        now: datetime | None = None,
        persist: bool = True,
    ) -> MarketSnapshot:
        definition = normalize_instrument(instrument)
        captured_at = (now or datetime.now(UTC)).astimezone(UTC)
        health, account, quote_payload = self._probe(definition.bridge_symbol, captured_at)
        if not health.mt5_connected or not quote_payload:
            raise SnapshotUnavailable("MT5_UNAVAILABLE", "A live MT5 snapshot is unavailable", health)

        ingestion_issues: list[DataQualityIssue] = []
        quote = self._parse_quote(definition.canonical, definition.bridge_symbol, quote_payload)
        metadata_payload = self._required_call("symbol metadata", self.gateway.symbol_info, definition.bridge_symbol)
        metadata = self._parse_metadata(definition.canonical, definition.bridge_symbol, metadata_payload)
        broker_symbol = quote.broker_symbol
        if metadata.broker_symbol != broker_symbol:
            ingestion_issues.append(_issue("SYMBOL_MISMATCH", "Quote and metadata broker symbols differ"))

        timeframes: dict[OfficialTimeframe, tuple[V4Bar, ...]] = {}
        for timeframe in OFFICIAL_TIMEFRAMES:
            payload = self._required_call(
                f"{timeframe.value} rates",
                self.gateway.rates,
                definition.bridge_symbol,
                BRIDGE_TIMEFRAMES[timeframe],
                self.bars_per_timeframe,
            )
            payload_symbol = str(payload.get("brokerSymbol") or broker_symbol)
            if payload_symbol != broker_symbol:
                ingestion_issues.append(
                    _issue("SYMBOL_MISMATCH", f"{timeframe.value} rates use a different broker symbol", timeframe)
                )
            parsed_bars: list[V4Bar] = []
            raw_bars = payload.get("bars")
            if not isinstance(raw_bars, list):
                ingestion_issues.append(_issue("MISSING_TIMEFRAME", "Rates payload has no bars", timeframe))
                raw_bars = []
            for raw in raw_bars:
                try:
                    parsed_bars.append(_parse_bar(raw))
                except (TypeError, ValueError, ValidationError):
                    ingestion_issues.append(_issue("INVALID_BAR", "An invalid OHLCV bar was rejected", timeframe))
            timeframes[timeframe] = tuple(parsed_bars)

        ticks_payload = self._required_call("ticks", self.gateway.ticks, definition.bridge_symbol, self.tick_count)
        ticks: list[V4Tick] = []
        raw_ticks = ticks_payload.get("ticks")
        if not isinstance(raw_ticks, list):
            raw_ticks = []
        for raw in raw_ticks:
            try:
                ticks.append(_parse_tick(raw))
            except (TypeError, ValueError, ValidationError):
                ingestion_issues.append(_issue("INVALID_TICK", "An invalid broker tick was rejected", critical=False))
        ticks.sort(key=lambda row: row.timestamp)

        quality = self.quality_engine.evaluate(
            now=captured_at,
            health=health,
            quote=quote,
            asset_class=definition.asset_class,
            timeframes=timeframes,
            ticks=tuple(ticks),
            ingestion_issues=tuple(ingestion_issues),
        )
        spread = quote.ask - quote.bid
        mid = (quote.ask + quote.bid) / 2
        draft = {
            "instrument": definition.canonical,
            "asset_class": definition.asset_class,
            "captured_at": captured_at,
            "broker_symbol": broker_symbol,
            "broker": health.broker,
            "server": health.server or _optional_string(account.get("server")),
            "account_mode": health.account_mode,
            "bid": quote.bid,
            "ask": quote.ask,
            "spread": spread,
            "spread_bps": (spread / mid) * 10_000,
            "session": _session(captured_at),
            "volume_provenance": VolumeProvenance.BROKER_TICK_VOLUME,
            "timeframes": timeframes,
            "first_tick_at": ticks[0].timestamp if ticks else None,
            "last_tick": ticks[-1] if ticks else None,
            "tick_count": len(ticks),
            "symbol_metadata": metadata,
            "health": health,
            "data_quality": quality,
        }
        fingerprint = _jsonable(draft)
        if isinstance(fingerprint, dict) and isinstance(fingerprint.get("health"), dict):
            # Probe latency is operational telemetry, not market identity. Two
            # captures of identical source data at the same timestamp must have
            # one natural key even when the HTTP round-trip duration differs.
            fingerprint["health"].pop("latency_ms", None)
        snapshot_id = stable_key(fingerprint)
        snapshot = MarketSnapshot(snapshot_id=snapshot_id, **draft)
        if persist:
            self.store.upsert(SNAPSHOT_COLLECTION, snapshot_id, snapshot.model_dump(mode="json"))
        return snapshot

    def latest(self, instrument: str) -> MarketSnapshot | None:
        canonical = normalize_instrument(instrument).canonical
        rows = [row for row in self.store.values(SNAPSHOT_COLLECTION) if row.get("instrument") == canonical]
        if not rows:
            return None
        rows.sort(key=lambda row: str(row.get("captured_at", "")))
        return MarketSnapshot.model_validate(rows[-1])

    def _probe(
        self, bridge_symbol: str, checked_at: datetime
    ) -> tuple[MT5HealthReport, dict[str, Any], dict[str, Any]]:
        started = perf_counter()
        if not self.enabled:
            error = GatewayError("MT5_DISABLED", "MT5 is disabled")
            return self.health_monitor.unavailable(error, checked_at, 0), {}, {}
        try:
            health = self.gateway.health()
            account = self.gateway.account()
            quote = self.gateway.quote(bridge_symbol)
        except GatewayError as exc:
            latency_ms = (perf_counter() - started) * 1000
            return self.health_monitor.unavailable(exc, checked_at, latency_ms), {}, {}
        latency_ms = (perf_counter() - started) * 1000
        report = self.health_monitor.evaluate(
            health,
            account,
            quote,
            checked_at=checked_at,
            latency_ms=latency_ms,
            expected_account_mode=self.expected_account_mode,
        )
        return report, account, quote

    @staticmethod
    def _parse_quote(instrument: str, expected_bridge_symbol: str, raw: dict[str, Any]) -> V4Quote:
        try:
            bridge_symbol = str(raw.get("brokerSymbol") or expected_bridge_symbol)
            return V4Quote(
                instrument=instrument,
                broker_symbol=bridge_symbol,
                bid=_number(raw.get("bid")),
                ask=_number(raw.get("ask")),
                timestamp=_datetime(raw.get("time")),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise SnapshotUnavailable("INVALID_QUOTE", "MT5 returned an invalid quote") from exc

    @staticmethod
    def _parse_metadata(instrument: str, expected_bridge_symbol: str, raw: dict[str, Any]) -> V4SymbolMetadata:
        try:
            return V4SymbolMetadata(
                instrument=instrument,
                broker_symbol=str(raw.get("brokerSymbol") or expected_bridge_symbol),
                digits=_optional_int(raw.get("digits")),
                point=_optional_positive(raw.get("tickSize")),
                trade_contract_size=_optional_positive(raw.get("contractSize")),
                volume_min=_optional_nonnegative(raw.get("minVolume")),
                volume_max=_optional_nonnegative(raw.get("maxVolume")),
                volume_step=_optional_positive(raw.get("lotStep")),
                trade_mode=raw.get("tradeMode"),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise SnapshotUnavailable("INVALID_SYMBOL_METADATA", "MT5 returned invalid symbol metadata") from exc

    @staticmethod
    def _required_call(label: str, function: Any, *args: object) -> dict[str, Any]:
        try:
            payload = function(*args)
        except GatewayError as exc:
            raise SnapshotUnavailable(exc.code, f"MT5 {label} is unavailable") from exc
        if not isinstance(payload, dict):
            raise SnapshotUnavailable("INVALID_RESPONSE", f"MT5 {label} response is invalid")
        return payload


def _number(value: object) -> float:
    parsed = float(value)  # type: ignore[arg-type]
    if not math.isfinite(parsed):
        raise ValueError("number must be finite")
    return parsed


def _datetime(value: object) -> datetime:
    return datetime.fromtimestamp(_number(value), UTC)


def _parse_bar(raw: object) -> V4Bar:
    if not isinstance(raw, dict):
        raise TypeError("bar must be an object")
    return V4Bar(
        timestamp=_datetime(raw.get("time")),
        open=_number(raw.get("open")),
        high=_number(raw.get("high")),
        low=_number(raw.get("low")),
        close=_number(raw.get("close")),
        volume=_number(raw.get("volume", 0)),
    )


def _parse_tick(raw: object) -> V4Tick:
    if not isinstance(raw, dict):
        raise TypeError("tick must be an object")
    last_raw = raw.get("last")
    return V4Tick(
        timestamp=_datetime(raw.get("time")),
        bid=_number(raw.get("bid")),
        ask=_number(raw.get("ask")),
        last=None if last_raw is None else _number(last_raw),
        volume=max(0.0, _number(raw.get("volume", 0))),
    )


def _optional_positive(value: object) -> float | None:
    if value in {None, ""}:
        return None
    parsed = _number(value)
    return parsed if parsed > 0 else None


def _optional_nonnegative(value: object) -> float | None:
    if value in {None, ""}:
        return None
    parsed = _number(value)
    return parsed if parsed >= 0 else None


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError("integer metadata must be numeric")
    return int(value)


def _optional_string(value: object) -> str | None:
    return str(value) if value not in {None, ""} else None


def _issue(
    code: str,
    message: str,
    timeframe: OfficialTimeframe | None = None,
    *,
    critical: bool = True,
) -> DataQualityIssue:
    return DataQualityIssue(
        code=code,
        severity=IssueSeverity.CRITICAL if critical else IssueSeverity.WARNING,
        message=message,
        timeframe=timeframe,
        blocking=critical,
    )


def _session(now: datetime) -> str:
    hour = now.astimezone(UTC).hour
    if 12 <= hour < 16:
        return "LONDON_NEW_YORK_OVERLAP"
    if 7 <= hour < 16:
        return "LONDON"
    if 12 <= hour < 21:
        return "NEW_YORK"
    if 0 <= hour < 9:
        return "ASIA"
    return "OFF_HOURS"


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = ["SNAPSHOT_COLLECTION", "MarketSnapshotService", "SnapshotUnavailable"]
