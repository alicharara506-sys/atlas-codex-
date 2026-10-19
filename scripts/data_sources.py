#!/usr/bin/env python3
"""Optional zero-cost market/cross-asset adapters for ATLAS v2.

Production Atlas remains MT5-first. These adapters are opt-in and fail with
explicit availability errors; no missing external series is synthesized.
"""

from __future__ import annotations

import asyncio
from typing import Any


class DataSourceUnavailable(RuntimeError):
    pass


YAHOO_SYMBOLS = {
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X",
    "USDCHF": "CHF=X", "AUDUSD": "AUDUSD=X", "USDCAD": "CAD=X",
    "NZDUSD": "NZDUSD=X", "EURGBP": "EURGBP=X", "XAUUSD": "GC=F",
    "XAGUSD": "SI=F", "USOIL": "CL=F", "UKOIL": "BZ=F",
}
CROSS_ASSET_SYMBOLS = {"DXY": "DX-Y.NYB", "VIX": "^VIX", "TNX": "^TNX", "SPY": "SPY", "GLD": "GLD", "USO": "USO"}


class FreeMarketDataClient:
    def __init__(self):
        try:
            import yfinance as yf  # type: ignore
        except ImportError as exc:
            raise DataSourceUnavailable("Optional yfinance package is not installed") from exc
        self._yf = yf

    def fetch_bars(self, instrument: str, timeframe: str, lookback_bars: int = 200) -> list[dict[str, Any]]:
        symbol = YAHOO_SYMBOLS.get(instrument.upper().replace("/", ""), instrument)
        interval = {"15M": "15m", "1H": "1h", "4H": "1h", "D1": "1d"}.get(timeframe.upper())
        if interval is None:
            raise DataSourceUnavailable(f"Unsupported Yahoo timeframe '{timeframe}'")
        period = "60d" if interval in {"15m", "1h"} else "2y"
        try:
            frame = self._yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
        except Exception as exc:
            raise DataSourceUnavailable(f"Yahoo data request failed: {exc}") from exc
        if frame is None or frame.empty:
            raise DataSourceUnavailable(f"Yahoo returned no data for {symbol}")
        rows = []
        for timestamp, row in frame.tail(max(50, lookback_bars * (4 if timeframe.upper() == "4H" else 1))).iterrows():
            def scalar(name: str) -> float:
                value = row[name]
                if hasattr(value, "iloc"):
                    value = value.iloc[0]
                return float(value)
            rows.append({"time": int(timestamp.timestamp()), "open": scalar("Open"), "high": scalar("High"), "low": scalar("Low"), "close": scalar("Close"), "volume": scalar("Volume")})
        if timeframe.upper() == "4H":
            grouped = []
            for start in range(0, len(rows), 4):
                chunk = rows[start : start + 4]
                if len(chunk) == 4:
                    grouped.append({"time": chunk[0]["time"], "open": chunk[0]["open"], "high": max(item["high"] for item in chunk), "low": min(item["low"] for item in chunk), "close": chunk[-1]["close"], "volume": sum(item["volume"] for item in chunk)})
            rows = grouped
        return rows[-lookback_bars:]

    async def fetch_bars_async(self, instrument: str, timeframe: str, lookback_bars: int = 200) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.fetch_bars, instrument, timeframe, lookback_bars)

    def cross_asset_snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for name, symbol in CROSS_ASSET_SYMBOLS.items():
            try:
                frame = self._yf.download(symbol, period="1mo", interval="1d", progress=False, auto_adjust=False, threads=False)
                close = frame["Close"]
                if hasattr(close, "columns"):
                    close = close.iloc[:, 0]
                values = [float(value) for value in close.dropna().tolist()]
                if len(values) < 2:
                    raise ValueError("insufficient daily closes")
                result[name] = {"latest": values[-1], "direction": "UP" if values[-1] > values[-2] else "DOWN" if values[-1] < values[-2] else "FLAT", "change": values[-1] / values[-2] - 1}
            except Exception as exc:
                errors[name] = str(exc)
        return {"status": "LIVE" if result else "UNAVAILABLE", "assets": result, "errors": errors, "provider": "YFINANCE_OPTIONAL"}


__all__ = ["CROSS_ASSET_SYMBOLS", "DataSourceUnavailable", "FreeMarketDataClient", "YAHOO_SYMBOLS"]
