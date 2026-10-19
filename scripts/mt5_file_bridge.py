#!/usr/bin/env python3
"""Read-only loopback HTTP adapter for files exported by AtlasReadOnlyBridge.mq5."""

from __future__ import annotations

import csv
import json
import os
import signal
import sys
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BIND_HOST = "127.0.0.1"
PORT = int(os.environ.get("MT5_BRIDGE_PORT", "8765"))
TOKEN = os.environ.get("MT5_BRIDGE_TOKEN", "").strip()
DATA_ROOT = Path(os.environ.get("MT5_FILE_ROOT", "")).expanduser()
STALE_SECONDS = float(os.environ.get("MT5_FILE_STALE_SECONDS", "20"))
EXPECTED_ACCOUNT_MODE = os.environ.get("MT5_EXPECTED_ACCOUNT_MODE", "").strip().upper()

SUPPORTED_SYMBOLS = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
    "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL",
}
SUPPORTED_TIMEFRAMES = {"15M", "1H", "4H", "D1"}
TIMEFRAME_FILE_ALIASES = {"1D": "D1"}


class BridgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _require(params: dict[str, list[str]], name: str) -> str:
    values = params.get(name)
    if not values or not values[0]:
        raise BridgeError(400, f"missing required query parameter '{name}'")
    return values[0]


def _symbol(params: dict[str, list[str]]) -> str:
    symbol = _require(params, "symbol").upper()
    if symbol not in SUPPORTED_SYMBOLS:
        raise BridgeError(400, f"unsupported Atlas symbol '{symbol}'")
    return symbol


def _read_rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        raise BridgeError(503, f"MT5 export is not ready: {path.name}")
    last_error: Exception | None = None
    for _ in range(3):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = [row for row in csv.reader(handle) if row]
            if rows:
                return rows
        except (OSError, UnicodeError, csv.Error) as exc:
            last_error = exc
        time.sleep(0.03)
    raise BridgeError(503, f"MT5 export is temporarily unreadable: {path.name}") from last_error


def _read_key_values(path: Path) -> dict[str, str]:
    rows = _read_rows(path)
    values: dict[str, str] = {}
    for row in rows:
        if len(row) >= 2:
            values[row[0]] = row[1]
    return values


def _number(values: dict[str, str], key: str) -> float:
    try:
        return float(values[key])
    except (KeyError, ValueError) as exc:
        raise BridgeError(503, f"MT5 export has invalid '{key}'") from exc


def _integer(values: dict[str, str], key: str) -> int:
    return int(_number(values, key))


def _status(*, require_connected: bool = True) -> dict[str, str]:
    path = DATA_ROOT / "status.csv"
    values = _read_key_values(path)
    age = max(0.0, time.time() - path.stat().st_mtime)
    if values.get("schema") != "atlas-mt5-file-v1":
        raise BridgeError(503, "MT5 export schema is unsupported")
    if age > STALE_SECONDS:
        raise BridgeError(503, f"MT5 export is stale ({age:.1f}s old)")
    if require_connected and values.get("connected") != "1":
        raise BridgeError(503, "MetaTrader is not connected to a broker server")
    mode = values.get("tradeMode", "UNKNOWN").upper()
    if EXPECTED_ACCOUNT_MODE and mode != EXPECTED_ACCOUNT_MODE:
        raise BridgeError(
            503,
            f"MetaTrader account mode is {mode}; expected {EXPECTED_ACCOUNT_MODE}",
        )
    return values


def route_health(_params: dict[str, list[str]]) -> dict:
    try:
        status = _status()
        connected = True
    except BridgeError as exc:
        return {
            "status": "degraded",
            "mt5Connected": False,
            "terminal": None,
            "readOnly": True,
            "orderSubmission": False,
            "reason": exc.message,
        }
    return {
        "status": "ok",
        "mt5Connected": connected,
        "terminal": {
            "build": _integer(status, "build"),
            "company": status.get("company", ""),
            "connected": True,
        },
        "accountMode": status.get("tradeMode", "UNKNOWN"),
        "server": status.get("server", ""),
        "readOnly": True,
        "orderSubmission": False,
    }


def route_account(_params: dict[str, list[str]]) -> dict:
    status = _status()
    return {
        "login": 0,
        "server": status.get("server", ""),
        "currency": status.get("currency", ""),
        "balance": _number(status, "balance"),
        "equity": _number(status, "equity"),
        "margin": _number(status, "margin"),
        "marginFree": _number(status, "marginFree"),
        "leverage": _integer(status, "leverage"),
        "tradeMode": status.get("tradeMode", "UNKNOWN"),
        "tradeAllowed": status.get("tradeAllowed") == "1",
        "readOnly": True,
        "orderSubmission": False,
    }


def _symbol_values(symbol: str) -> dict[str, str]:
    _status()
    return _read_key_values(DATA_ROOT / "symbols" / f"{symbol}.csv")


def _server_utc_offset(status: dict[str, str], symbol_values: dict[str, str]) -> int:
    """Resolve broker wall-clock offset and reject implausible values.

    New exporters write the offset explicitly. The fallback safely supports
    the already-installed v1 EA by comparing its fresh UTC generation time to
    its fresh broker quote. Broker timezones normally align to a 15-minute
    boundary, but some Windows clocks drift enough to leave a small positive
    residual that would otherwise make every quote appear to come from the
    future. Only that positive residual is corrected; negative residuals stay
    visible so stale broker quotes still fail closed.
    """

    explicit = status.get("serverUtcOffsetSeconds")
    if explicit not in {None, ""}:
        offset = int(float(explicit))
    else:
        generated = _integer(status, "generated")
        quote_time = _integer(symbol_values, "time")
        delta = quote_time - generated
        if abs(delta) <= 14 * 3600:
            timezone_offset = int(round(delta / 900.0) * 900)
            positive_clock_skew = delta - timezone_offset
            offset = delta if 5 < positive_clock_skew <= 30 * 60 else timezone_offset
        else:
            offset = 0
    if abs(offset) > 14 * 3600:
        raise BridgeError(503, "MT5 broker UTC offset is invalid")
    return offset


def route_symbol_info(params: dict[str, list[str]]) -> dict:
    symbol = _symbol(params)
    values = _symbol_values(symbol)
    return {
        "symbol": symbol,
        "brokerSymbol": values.get("brokerSymbol", symbol),
        "digits": _integer(values, "digits"),
        "tickSize": _number(values, "tickSize"),
        "tickValue": _number(values, "tickValue"),
        "contractSize": _number(values, "contractSize"),
        "minVolume": _number(values, "minVolume"),
        "maxVolume": _number(values, "maxVolume"),
        "lotStep": _number(values, "lotStep"),
        "profitCurrency": values.get("profitCurrency", ""),
        "marginCurrency": values.get("marginCurrency", ""),
    }


def route_quote(params: dict[str, list[str]]) -> dict:
    symbol = _symbol(params)
    status = _status()
    values = _symbol_values(symbol)
    offset = _server_utc_offset(status, values)
    bid = _number(values, "bid")
    ask = _number(values, "ask")
    if bid <= 0 or ask <= 0 or ask < bid:
        raise BridgeError(503, f"MT5 quote for {symbol} is invalid")
    return {
        "symbol": symbol,
        "brokerSymbol": values.get("brokerSymbol", symbol),
        "bid": bid,
        "ask": ask,
        "time": _integer(values, "time") - offset,
        "timestampOffsetSeconds": offset,
    }


def route_rates(params: dict[str, list[str]]) -> dict:
    symbol = _symbol(params)
    timeframe = _require(params, "timeframe").upper()
    file_timeframe = TIMEFRAME_FILE_ALIASES.get(timeframe, timeframe)
    if file_timeframe not in SUPPORTED_TIMEFRAMES:
        raise BridgeError(400, f"unsupported timeframe '{timeframe}'")
    try:
        count = int(params.get("count", ["240"])[0])
    except ValueError as exc:
        raise BridgeError(400, "count must be an integer") from exc
    if count <= 0 or count > 5000:
        raise BridgeError(400, "count must be between 1 and 5000")
    status = _status()
    values = _symbol_values(symbol)
    offset = _server_utc_offset(status, values)
    rows = _read_rows(DATA_ROOT / "rates" / f"{symbol}_{file_timeframe}.csv")
    if rows[0][:6] != ["time", "open", "high", "low", "close", "volume"]:
        raise BridgeError(503, "MT5 rates header is invalid")
    bars = []
    for row in rows[1:]:
        if len(row) < 6:
            continue
        try:
            bars.append({
                "time": int(float(row[0])) - offset,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })
        except ValueError as exc:
            raise BridgeError(503, "MT5 rates contain an invalid number") from exc
    bars.sort(key=lambda bar: bar["time"])
    bars = bars[-count:]
    if not bars:
        raise BridgeError(503, f"MT5 has no closed {timeframe} bars for {symbol}")
    return {
        "symbol": symbol,
        "brokerSymbol": values.get("brokerSymbol", symbol),
        "timeframe": timeframe,
        "timestampOffsetSeconds": offset,
        "bars": bars,
    }


def route_ticks(params: dict[str, list[str]]) -> dict:
    symbol = _symbol(params)
    try:
        count = int(params.get("count", ["2000"])[0])
    except ValueError as exc:
        raise BridgeError(400, "count must be an integer") from exc
    if count <= 0 or count > 50000:
        raise BridgeError(400, "count must be between 1 and 50000")
    status = _status()
    values = _symbol_values(symbol)
    offset = _server_utc_offset(status, values)
    rows = _read_rows(DATA_ROOT / "ticks" / f"{symbol}.csv")
    if rows[0][:5] != ["time", "bid", "ask", "last", "volume"]:
        raise BridgeError(503, "MT5 ticks header is invalid")
    ticks = []
    for row in rows[1:]:
        if len(row) < 5:
            continue
        try:
            last = float(row[3])
            ticks.append({
                "time": float(row[0]) - offset,
                "bid": float(row[1]),
                "ask": float(row[2]),
                "last": last if last != 0 else None,
                "volume": float(row[4]),
            })
        except ValueError as exc:
            raise BridgeError(503, "MT5 ticks contain an invalid number") from exc
    if "from" in params and "to" in params:
        try:
            date_from = float(params["from"][0])
            date_to = float(params["to"][0])
        except ValueError as exc:
            raise BridgeError(400, "from/to must be Unix timestamps") from exc
        if date_to <= date_from:
            raise BridgeError(400, "'to' must be after 'from'")
        ticks = [tick for tick in ticks if date_from <= tick["time"] <= date_to]
    ticks.sort(key=lambda tick: tick["time"])
    ticks = ticks[-count:]
    return {
        "symbol": symbol,
        "brokerSymbol": values.get("brokerSymbol", symbol),
        "timestampOffsetSeconds": offset,
        "ticks": ticks,
    }


ROUTES: dict[str, Callable[[dict[str, list[str]]], dict]] = {
    "/health": route_health,
    "/account": route_account,
    "/symbol_info": route_symbol_info,
    "/rates": route_rates,
    "/ticks": route_ticks,
    "/quote": route_quote,
}


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write(f"[mt5_file_bridge] {self.address_string()} - {fmt % args}\n")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if TOKEN and self.headers.get("Authorization", "") != f"Bearer {TOKEN}":
            self._send_json(401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        handler = ROUTES.get(parsed.path)
        if handler is None:
            self._send_json(404, {"error": f"unknown route '{parsed.path}'"})
            return
        try:
            self._send_json(200, handler(parse_qs(parsed.query)))
        except BridgeError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001 - last-resort process protection
            self._send_json(500, {"error": f"internal error: {exc}"})


def main() -> None:
    if not str(DATA_ROOT):
        raise SystemExit("MT5_FILE_ROOT must point to MetaTrader's MQL5\\Files\\AtlasBridge folder")
    server = ThreadingHTTPServer((BIND_HOST, PORT), BridgeHandler)
    print(f"[mt5_file_bridge] reading {DATA_ROOT}")
    print(f"[mt5_file_bridge] listening on http://{BIND_HOST}:{PORT} (read-only)")

    def shutdown(_signum, _frame) -> None:
        server.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    server.serve_forever()


if __name__ == "__main__":
    main()
