"""Read-only MT5 bridge gateway and explicit connection-state monitor."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse

from app.core.http import JsonRequest, JsonTransport, TransportError, UrllibJsonTransport
from app.v4.contracts import MT5ConnectionState, MT5HealthReport


class GatewayError(RuntimeError):
    """Normalized MT5 bridge failure with no credential-bearing message."""

    def __init__(self, code: str, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable


class MT5GatewayProtocol(Protocol):
    def health(self) -> dict[str, Any]: ...
    def account(self) -> dict[str, Any]: ...
    def quote(self, symbol: str) -> dict[str, Any]: ...
    def rates(self, symbol: str, timeframe: str, bars: int) -> dict[str, Any]: ...
    def ticks(self, symbol: str, count: int) -> dict[str, Any]: ...
    def symbol_info(self, symbol: str) -> dict[str, Any]: ...


class MT5Gateway:
    """Typed GET-only adapter for the local Atlas MT5 bridge.

    The class deliberately exposes no trade/order method. Its URL is restricted
    to the loopback interface so the bearer credential never leaves the laptop.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout_seconds: float = 3.0,
        transport: JsonTransport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("MT5 gateway must use a local HTTP loopback address")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MT5 gateway URL contains unsafe components")
        if not 0.5 <= timeout_seconds <= 10.0:
            raise ValueError("MT5 timeout must be between 0.5 and 10 seconds")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrllibJsonTransport()

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def account(self) -> dict[str, Any]:
        return self._get("/account")

    def quote(self, symbol: str) -> dict[str, Any]:
        return self._get("/quote", symbol=symbol)

    def rates(self, symbol: str, timeframe: str, bars: int) -> dict[str, Any]:
        if not 20 <= bars <= 5000:
            raise ValueError("bars must be between 20 and 5000")
        return self._get("/rates", symbol=symbol, timeframe=timeframe, count=bars)

    def ticks(self, symbol: str, count: int) -> dict[str, Any]:
        if not 1 <= count <= 50_000:
            raise ValueError("count must be between 1 and 50000")
        return self._get("/ticks", symbol=symbol, count=count)

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        return self._get("/symbol_info", symbol=symbol)

    def _get(self, path: str, **query: object) -> dict[str, Any]:
        suffix = f"?{urlencode(query)}" if query else ""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            payload = self.transport.send(
                JsonRequest(
                    method="GET",
                    url=f"{self.base_url}{path}{suffix}",
                    headers=headers,
                    timeout_seconds=self.timeout_seconds,
                )
            )
        except TransportError as exc:
            code = "AUTH_FAILED" if exc.status in {401, 403} else "BRIDGE_UNAVAILABLE"
            raise GatewayError(code, f"MT5 bridge request failed at {path}", status=exc.status, retryable=exc.retryable) from exc
        if not isinstance(payload, dict):
            raise GatewayError("INVALID_RESPONSE", f"MT5 bridge returned an invalid response at {path}")
        return payload


def _epoch(value: object) -> datetime | None:
    numeric = _number_or_none(value)
    if numeric is None or numeric <= 0:
        return None
    try:
        return datetime.fromtimestamp(numeric, UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _account_mode(health: dict[str, Any], account: dict[str, Any]) -> str | None:
    direct = health.get("accountMode") or account.get("accountMode")
    if isinstance(direct, str) and direct.strip():
        return direct.strip().upper()
    trade_mode = account.get("tradeMode")
    if isinstance(trade_mode, str) and trade_mode.upper() in {"DEMO", "REAL", "CONTEST"}:
        return trade_mode.upper()
    mapping = {0: "DEMO", 1: "CONTEST", 2: "REAL", "0": "DEMO", "1": "CONTEST", "2": "REAL"}
    return mapping.get(trade_mode)


def retail_fx_market_closed(now: datetime) -> bool:
    """Conservative UTC weekend closure used only to classify stale data."""

    current = now.astimezone(UTC)
    weekday = current.weekday()
    return weekday == 5 or (weekday == 4 and current.hour >= 22) or (weekday == 6 and current.hour < 22)


class MT5HealthMonitor:
    """Convert bridge payloads into one deterministic V4 health state."""

    def __init__(self) -> None:
        self._previous = MT5ConnectionState.DISCONNECTED
        self._lock = RLock()

    def unavailable(self, error: GatewayError, checked_at: datetime, latency_ms: float) -> MT5HealthReport:
        with self._lock:
            if error.code == "AUTH_FAILED":
                state = MT5ConnectionState.AUTH_FAILED
            elif self._previous in {MT5ConnectionState.CONNECTED, MT5ConnectionState.DEGRADED}:
                state = MT5ConnectionState.RECONNECTING
            else:
                state = MT5ConnectionState.DISCONNECTED
            self._previous = state
        return MT5HealthReport(
            state=state,
            checked_at=checked_at,
            latency_ms=max(0.0, latency_ms),
            mt5_connected=False,
            symbol_available=False,
            data_fresh=False,
            issues=(error.code,),
        )

    def evaluate(
        self,
        health: dict[str, Any],
        account: dict[str, Any],
        quote: dict[str, Any],
        *,
        checked_at: datetime,
        latency_ms: float,
        expected_account_mode: str | None = None,
    ) -> MT5HealthReport:
        issues: list[str] = []
        connected = bool(health.get("mt5Connected"))
        bid = _number_or_none(quote.get("bid"))
        ask = _number_or_none(quote.get("ask"))
        symbol_available = bid is not None and ask is not None and bid > 0 and ask >= bid
        last_tick_at = _epoch(quote.get("time"))
        future_tick = last_tick_at is not None and (last_tick_at - checked_at).total_seconds() > 5
        tick_age = None if last_tick_at is None else max(0.0, (checked_at - last_tick_at).total_seconds())
        data_fresh = symbol_available and tick_age is not None and tick_age <= 120 and not future_tick
        account_mode = _account_mode(health, account)

        if not connected:
            issues.append("MT5_DISCONNECTED")
        if not symbol_available:
            issues.append("SYMBOL_UNAVAILABLE")
        if last_tick_at is None:
            issues.append("MISSING_TICK_TIME")
        elif future_tick:
            issues.append("FUTURE_TICK")
        elif not data_fresh:
            issues.append("STALE_TICK")
        if expected_account_mode and account_mode != expected_account_mode:
            issues.append("ACCOUNT_MODE_MISMATCH")
        if health.get("orderSubmission") is True or health.get("readOnly") is False:
            issues.append("UNSAFE_BRIDGE_MODE")

        if not connected:
            state = MT5ConnectionState.DISCONNECTED
        elif not data_fresh and retail_fx_market_closed(checked_at):
            state = MT5ConnectionState.MARKET_CLOSED
        elif issues:
            state = MT5ConnectionState.DEGRADED
        else:
            state = MT5ConnectionState.CONNECTED

        terminal_raw = health.get("terminal")
        terminal: dict[str, Any] = terminal_raw if isinstance(terminal_raw, dict) else {}
        broker = terminal.get("company") if isinstance(terminal.get("company"), str) else None
        server_raw = account.get("server") or health.get("server")
        server = str(server_raw) if server_raw not in {None, ""} else None
        with self._lock:
            self._previous = state
        return MT5HealthReport(
            state=state,
            checked_at=checked_at,
            latency_ms=max(0.0, latency_ms),
            mt5_connected=connected,
            symbol_available=bool(symbol_available),
            data_fresh=data_fresh,
            account_mode=account_mode,
            broker=broker,
            server=server,
            last_tick_at=last_tick_at,
            tick_age_seconds=tick_age,
            issues=tuple(issues),
        )


def _number_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


__all__ = [
    "GatewayError",
    "MT5Gateway",
    "MT5GatewayProtocol",
    "MT5HealthMonitor",
    "retail_fx_market_closed",
]
