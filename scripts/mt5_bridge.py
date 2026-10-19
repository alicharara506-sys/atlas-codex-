#!/usr/bin/env python3
"""
scripts/mt5_bridge.py

Read-only, loopback-only HTTP bridge between Atlas and a local MetaTrader 5
terminal. This script is intentionally, verifiably READ-ONLY:

    grep -n "order_send\\|order_check\\|order_send_async\\|OrderSend" scripts/mt5_bridge.py

...returns nothing. The only MetaTrader5 package calls used anywhere in
this file are: initialize, shutdown, last_error, terminal_info,
account_info, symbols_get, symbol_info, symbol_info_tick,
copy_rates_from_pos, copy_ticks_from, copy_ticks_range. None of those can
place, modify, or cancel an order. There is no code path from any HTTP
route in this file to a trade being sent to your broker.

Requirements (see README.md -> External integration setup):
    - Windows, with MetaTrader 5 installed, open, and logged in to your
      broker account. This script cannot run without a live terminal —
      it does not simulate or embed one.
    - `pip install -r requirements-mt5.txt` inside the venv pointed to by
      MT5_PYTHON_PATH (see scripts/setup-mt5.ps1).

Environment variables (see .env.example):
    MT5_TERMINAL_PATH   Path to terminal64.exe, passed to mt5.initialize()
    MT5_PORTABLE        "true"/"false" -> portable= for mt5.initialize()
    MT5_BRIDGE_PORT     Port to bind on 127.0.0.1 (default 8765)
    MT5_BRIDGE_TOKEN    Bearer token required on every request. If left
                         blank, the bridge still runs (loopback-only, so
                         only processes on this machine can reach it) but
                         prints a warning on startup.
    MT5_SYMBOL_MAP      Optional JSON object, e.g. {"EURUSD":"EURUSD.a"},
                         checked before the automatic suffix search.

HTTP routes (all GET, all JSON, all require the bearer token if one is
configured):
    /health                          -> connection + terminal status
    /account                         -> balance/equity/currency/leverage
    /symbol_info?symbol=EURUSD       -> broker contract spec
    /rates?symbol=EURUSD&timeframe=1H&count=240
                                      -> CLOSED bars only (forming bar
                                         excluded — see closed_bars_only())
    /ticks?symbol=EURUSD&count=2000  -> recent ticks
    /ticks?symbol=EURUSD&from=<unix>&to=<unix>
                                      -> ticks in a specific range
    /quote?symbol=EURUSD             -> latest bid/ask snapshot

Every route returns HTTP 4xx/5xx with a small JSON {"error": "..."} body
on failure rather than raising past the handler.
"""

import ctypes
from ctypes import wintypes
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import MetaTrader5 as mt5
except ImportError:
    print(
        "MetaTrader5 package not installed. Run: pip install -r requirements-mt5.txt",
        file=sys.stderr,
    )
    sys.exit(1)

BIND_HOST = "127.0.0.1"  # loopback only — never bind 0.0.0.0
PORT = int(os.environ.get("MT5_BRIDGE_PORT", "8765"))
TOKEN = os.environ.get("MT5_BRIDGE_TOKEN", "").strip()
TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH") or None
PORTABLE = os.environ.get("MT5_PORTABLE", "false").strip().lower() == "true"
REQUIRE_VISIBLE = os.environ.get("MT5_REQUIRE_VISIBLE", "true").strip().lower() == "true"

SYMBOL_MAP = {}
_raw_map = os.environ.get("MT5_SYMBOL_MAP", "").strip()
if _raw_map:
    try:
        SYMBOL_MAP = json.loads(_raw_map)
    except json.JSONDecodeError:
        print(f"MT5_SYMBOL_MAP is not valid JSON, ignoring: {_raw_map}", file=sys.stderr)

# Common broker symbol suffixes, tried in order when the plain symbol isn't
# found. Short and documented on purpose — extend MT5_SYMBOL_MAP for
# anything unusual rather than growing this list indefinitely.
COMMON_SUFFIXES = ["", ".a", ".m", ".raw", ".pro", "m", "_i", "-ECN"]

TIMEFRAME_MAP = {
    "15M": "TIMEFRAME_M15",
    "1H": "TIMEFRAME_H1",
    "4H": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}

_init_lock = threading.Lock()
_initialized = False


def _window_process_path(window_handle: int) -> str | None:
    """Return the executable path that owns a top-level Windows window."""
    if os.name != "nt":
        return None

    process_id = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(window_handle, ctypes.byref(process_id))
    if not process_id.value:
        return None

    process_query_limited_information = 0x1000
    process_handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information, False, process_id.value
    )
    if not process_handle:
        return None
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            process_handle, 0, buffer, ctypes.byref(capacity)
        ):
            return None
        return buffer.value
    finally:
        ctypes.windll.kernel32.CloseHandle(process_handle)


def _find_terminal_window(*, visible_only: bool) -> int | None:
    """Find this MT5 installation's top-level window without launching it."""
    if os.name != "nt":
        return None

    expected_path = os.path.normcase(os.path.abspath(TERMINAL_PATH)) if TERMINAL_PATH else None
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def inspect_window(window_handle: int, _lparam: int) -> bool:
        if visible_only and not ctypes.windll.user32.IsWindowVisible(window_handle):
            return True
        process_path = _window_process_path(window_handle)
        if not process_path:
            return True
        normalized = os.path.normcase(os.path.abspath(process_path))
        if (expected_path and normalized == expected_path) or (
            not expected_path and os.path.basename(normalized) == "terminal64.exe"
        ):
            matches.append(int(window_handle))
            return False
        return True

    callback = callback_type(inspect_window)
    ctypes.windll.user32.EnumWindows(callback, 0)
    return matches[0] if matches else None


def _restore_terminal_window(window_handle: int) -> None:
    """Keep the user's already-open MT5 desktop window visible during IPC attach."""
    if os.name != "nt" or not ctypes.windll.user32.IsWindow(window_handle):
        return
    ctypes.windll.user32.ShowWindowAsync(window_handle, 5)  # SW_SHOW
    ctypes.windll.user32.ShowWindowAsync(window_handle, 9)  # SW_RESTORE


def _preserve_terminal_window(window_handle: int) -> None:
    """Restore the MT5 window while the Python package negotiates IPC."""
    deadline = time.monotonic() + 12.0
    current_handle = window_handle
    while time.monotonic() < deadline:
        if not ctypes.windll.user32.IsWindow(current_handle):
            replacement = _find_terminal_window(visible_only=False)
            if replacement is None:
                return
            current_handle = replacement
        _restore_terminal_window(current_handle)
        time.sleep(0.25)


def _schedule_terminal_window_preservation(window_handle: int) -> None:
    threading.Thread(
        target=_preserve_terminal_window,
        args=(window_handle,),
        name="mt5-window-preserver",
        daemon=True,
    ).start()


class BridgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def ensure_initialized() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        visible_window = _find_terminal_window(visible_only=True) if REQUIRE_VISIBLE else None
        if REQUIRE_VISIBLE and visible_window is None:
            raise BridgeError(
                503,
                "MetaTrader 5 desktop is not visibly open. Open MT5 and log in before Atlas connects.",
            )
        if visible_window is not None:
            _schedule_terminal_window_preservation(visible_window)
        kwargs = {"portable": PORTABLE}
        if TERMINAL_PATH:
            kwargs["path"] = TERMINAL_PATH
        ok = mt5.initialize(**kwargs)
        if not ok:
            raise BridgeError(503, f"mt5.initialize() failed: {mt5.last_error()}")
        if visible_window is not None:
            _restore_terminal_window(visible_window)
        _initialized = True


def resolve_symbol(symbol: str) -> str:
    """Resolves an Atlas symbol to the broker's actual symbol name."""
    if symbol in SYMBOL_MAP:
        candidate = SYMBOL_MAP[symbol]
        if mt5.symbol_info(candidate) is not None:
            return candidate
    for suffix in COMMON_SUFFIXES:
        candidate = symbol + suffix
        if mt5.symbol_info(candidate) is not None:
            return candidate
    tried = [symbol + s for s in COMMON_SUFFIXES]
    raise BridgeError(404, f"symbol '{symbol}' not found (tried: {tried})")


def closed_bars_only(rates, count: int):
    """
    mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count) with
    start_pos=1 already skips position 0 (the current, still-forming bar).
    This helper exists so that guarantee is named and tested rather than
    left as an easy-to-lose implementation detail two lines above a caller.
    """
    return rates[:count] if rates is not None else None


def bar_to_dict(rate) -> dict:
    return {
        "time": int(rate["time"]),
        "open": float(rate["open"]),
        "high": float(rate["high"]),
        "low": float(rate["low"]),
        "close": float(rate["close"]),
        "volume": float(rate["tick_volume"]),
    }


def tick_to_dict(tick) -> dict:
    names = tick.dtype.names or ()
    time_val = float(tick["time_msc"]) / 1000.0 if "time_msc" in names else float(tick["time"])
    last_val = float(tick["last"]) if "last" in names and tick["last"] else None
    if "volume_real" in names and tick["volume_real"]:
        volume_val = float(tick["volume_real"])
    else:
        volume_val = float(tick["volume"]) if "volume" in names else 0.0
    return {
        "time": time_val,
        "bid": float(tick["bid"]),
        "ask": float(tick["ask"]),
        "last": last_val,
        "volume": volume_val,
    }


# ---------------------------------------------------------------------------
# Route handlers — each takes the parsed query dict, returns a JSON-able dict
# ---------------------------------------------------------------------------


def route_health(_params: dict) -> dict:
    connected = False
    terminal = None
    try:
        ensure_initialized()
        info = mt5.terminal_info()
        connected = info is not None and info.connected
        if info is not None:
            terminal = {"build": info.build, "company": info.company, "connected": info.connected}
    except BridgeError:
        pass
    return {"status": "ok", "mt5Connected": connected, "terminal": terminal}


def route_account(_params: dict) -> dict:
    ensure_initialized()
    acc = mt5.account_info()
    if acc is None:
        raise BridgeError(503, f"account_info() failed: {mt5.last_error()}")
    return {
        "login": acc.login,
        "server": acc.server,
        "currency": acc.currency,
        "balance": acc.balance,
        "equity": acc.equity,
        "margin": acc.margin,
        "marginFree": acc.margin_free,
        "leverage": acc.leverage,
        "tradeMode": "REAL" if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL else "DEMO" if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "CONTEST",
        "tradeAllowed": bool(acc.trade_allowed),
    }


def route_symbol_info(params: dict) -> dict:
    symbol = _require(params, "symbol")
    ensure_initialized()
    broker_symbol = resolve_symbol(symbol)
    info = mt5.symbol_info(broker_symbol)
    if info is None:
        raise BridgeError(404, f"symbol_info('{broker_symbol}') returned None")
    return {
        "symbol": symbol,
        "brokerSymbol": broker_symbol,
        "digits": info.digits,
        "tickSize": info.trade_tick_size,
        "tickValue": info.trade_tick_value,
        "contractSize": info.trade_contract_size,
        "minVolume": info.volume_min,
        "maxVolume": info.volume_max,
        "lotStep": info.volume_step,
        "profitCurrency": info.currency_profit,
        "marginCurrency": info.currency_margin,
    }


def route_rates(params: dict) -> dict:
    symbol = _require(params, "symbol")
    timeframe_key = _require(params, "timeframe")
    count = int(params.get("count", ["240"])[0])
    if timeframe_key not in TIMEFRAME_MAP:
        raise BridgeError(400, f"timeframe must be one of {list(TIMEFRAME_MAP)}")
    if count <= 0 or count > 5000:
        raise BridgeError(400, "count must be between 1 and 5000")

    ensure_initialized()
    broker_symbol = resolve_symbol(symbol)
    tf_constant = getattr(mt5, TIMEFRAME_MAP[timeframe_key])
    # start_pos=1 -> skip position 0, the forming/live bar.
    rates = mt5.copy_rates_from_pos(broker_symbol, tf_constant, 1, count)
    rates = closed_bars_only(rates, count)
    if rates is None:
        raise BridgeError(502, f"copy_rates_from_pos failed: {mt5.last_error()}")

    bars = [bar_to_dict(r) for r in rates]
    bars.sort(key=lambda b: b["time"])  # chronological, oldest first
    return {"symbol": symbol, "brokerSymbol": broker_symbol, "timeframe": timeframe_key, "bars": bars}


def route_ticks(params: dict) -> dict:
    symbol = _require(params, "symbol")
    ensure_initialized()
    broker_symbol = resolve_symbol(symbol)

    if "from" in params and "to" in params:
        date_from = float(params["from"][0])
        date_to = float(params["to"][0])
        if date_to <= date_from:
            raise BridgeError(400, "'to' must be after 'from'")
        ticks = mt5.copy_ticks_range(broker_symbol, date_from, date_to, mt5.COPY_TICKS_ALL)
    else:
        count = int(params.get("count", ["2000"])[0])
        if count <= 0 or count > 50000:
            raise BridgeError(400, "count must be between 1 and 50000")
        import time as _time

        ticks = mt5.copy_ticks_from(broker_symbol, _time.time(), count, mt5.COPY_TICKS_ALL)

    if ticks is None:
        raise BridgeError(502, f"tick fetch failed: {mt5.last_error()}")

    result = [tick_to_dict(t) for t in ticks]
    result.sort(key=lambda t: t["time"])
    return {"symbol": symbol, "brokerSymbol": broker_symbol, "ticks": result}


def route_quote(params: dict) -> dict:
    symbol = _require(params, "symbol")
    ensure_initialized()
    broker_symbol = resolve_symbol(symbol)
    tick = mt5.symbol_info_tick(broker_symbol)
    if tick is None:
        raise BridgeError(502, f"symbol_info_tick('{broker_symbol}') returned None")
    return {
        "symbol": symbol,
        "brokerSymbol": broker_symbol,
        "bid": tick.bid,
        "ask": tick.ask,
        "time": tick.time,
    }


def _require(params: dict, name: str) -> str:
    values = params.get(name)
    if not values or not values[0]:
        raise BridgeError(400, f"missing required query parameter '{name}'")
    return values[0]


ROUTES = {
    "/health": route_health,
    "/account": route_account,
    "/symbol_info": route_symbol_info,
    "/rates": route_rates,
    "/ticks": route_ticks,
    "/quote": route_quote,
}


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter default logging
        sys.stdout.write(f"[mt5_bridge] {self.address_string()} - {fmt % args}\n")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not TOKEN:
            return True  # loopback-only bind is the fallback protection
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {TOKEN}"

    def do_GET(self):  # noqa: N802 (stdlib naming convention)
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return

        parsed = urlparse(self.path)
        handler = ROUTES.get(parsed.path)
        if handler is None:
            self._send_json(404, {"error": f"unknown route '{parsed.path}'"})
            return

        params = parse_qs(parsed.query)
        try:
            result = handler(params)
            self._send_json(200, result)
        except BridgeError as e:
            self._send_json(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001 — last line of defense, never crash the loop
            self._send_json(500, {"error": f"internal error: {e}"})


def main() -> None:
    if not TOKEN:
        print(
            "[mt5_bridge] WARNING: MT5_BRIDGE_TOKEN is not set. The bridge is "
            "still loopback-only (127.0.0.1), but set a token for defense in depth.",
            file=sys.stderr,
        )

    server = ThreadingHTTPServer((BIND_HOST, PORT), BridgeHandler)
    print(f"[mt5_bridge] listening on http://{BIND_HOST}:{PORT} (loopback only)")

    def shutdown(_signum, _frame):
        print("[mt5_bridge] shutting down...")
        try:
            mt5.shutdown()
        finally:
            server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    server.serve_forever()


if __name__ == "__main__":
    main()
