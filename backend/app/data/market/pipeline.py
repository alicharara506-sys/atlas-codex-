"""Idempotent multi-source price, fundamental, and macro ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote, urlencode

from app.core.http import JsonRequest, JsonTransport, UrllibJsonTransport
from app.core.store import RecordStore, stable_key
from app.data.contracts import EconomicObservation, FundamentalSnapshot, MarketBar, finite


class MarketDataError(RuntimeError):
    pass


class BarProvider(Protocol):
    name: str
    def fetch_bars(self, ticker: str, *, interval: str, start: int | None = None, end: int | None = None) -> list[MarketBar]: ...


class FundamentalsProvider(Protocol):
    name: str
    def fetch_snapshot(self, ticker: str) -> FundamentalSnapshot: ...


class EconomicProvider(Protocol):
    name: str
    def fetch_series(self, series_id: str, *, start: str | None = None, end: str | None = None) -> list[EconomicObservation]: ...


class YahooOhlcvProvider:
    name = "YAHOO"

    def __init__(self, transport: JsonTransport | None = None) -> None:
        self.transport = transport or UrllibJsonTransport()

    def fetch_bars(self, ticker: str, *, interval: str = "1d", start: int | None = None, end: int | None = None) -> list[MarketBar]:
        params: dict[str, object] = {"interval": interval, "events": "div,splits", "includeAdjustedClose": "true"}
        if start is not None and end is not None:
            params.update({"period1": start, "period2": end})
        else:
            params["range"] = "5y"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='^=.-')}?{urlencode(params)}"
        payload = self.transport.send(JsonRequest("GET", url))
        chart = payload.get("chart") if isinstance(payload, dict) else None
        results = chart.get("result") if isinstance(chart, dict) else None
        result = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else None
        timestamps = result.get("timestamp") if isinstance(result, dict) else None
        indicators = result.get("indicators") if isinstance(result, dict) else None
        quote_list = indicators.get("quote") if isinstance(indicators, dict) else None
        quote_rows = quote_list[0] if isinstance(quote_list, list) and quote_list and isinstance(quote_list[0], dict) else None
        if not isinstance(timestamps, list) or quote_rows is None:
            raise MarketDataError(f"Yahoo returned no chart data for {ticker}")
        bars = []
        for index, timestamp in enumerate(timestamps):
            values: dict[str, object | None] = {}
            for name in ("open", "high", "low", "close", "volume"):
                series = quote_rows.get(name)
                values[name] = series[index] if isinstance(series, list) and index < len(series) else None
            if any(values[name] is None for name in ("open", "high", "low", "close")):
                continue
            bars.append(MarketBar(ticker, int(timestamp), finite(values["open"], "open"), finite(values["high"], "high"), finite(values["low"], "low"), finite(values["close"], "close"), finite(values["volume"] or 0, "volume"), self.name))
        return bars


class BinanceOhlcvProvider:
    name = "BINANCE"

    def __init__(self, transport: JsonTransport | None = None, base_url: str = "https://api.binance.com") -> None:
        self.transport = transport or UrllibJsonTransport()
        self.base_url = base_url.rstrip("/")

    def fetch_bars(self, ticker: str, *, interval: str = "1d", start: int | None = None, end: int | None = None) -> list[MarketBar]:
        symbol = ticker.replace("/", "").replace("-", "").upper()
        params: dict[str, object] = {"symbol": symbol, "interval": interval, "limit": 1000}
        if start is not None:
            params["startTime"] = start * 1000
        if end is not None:
            params["endTime"] = end * 1000
        payload = self.transport.send(JsonRequest("GET", f"{self.base_url}/api/v3/klines?{urlencode(params)}"))
        if not isinstance(payload, list):
            raise MarketDataError("Binance kline response must be an array")
        bars = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 6:
                continue
            bars.append(MarketBar(ticker, int(row[0]) // 1000, finite(row[1], "open"), finite(row[2], "high"), finite(row[3], "low"), finite(row[4], "close"), finite(row[5], "volume"), self.name))
        return bars


class FmpFundamentalsProvider:
    name = "FMP"

    def __init__(self, api_key: str, transport: JsonTransport | None = None, base_url: str = "https://financialmodelingprep.com/stable") -> None:
        if not api_key:
            raise MarketDataError("FMP API key is required")
        self.api_key = api_key
        self.transport = transport or UrllibJsonTransport()
        self.base_url = base_url.rstrip("/")

    def fetch_snapshot(self, ticker: str) -> FundamentalSnapshot:
        url = f"{self.base_url}/profile?{urlencode({'symbol': ticker, 'apikey': self.api_key})}"
        payload = self.transport.send(JsonRequest("GET", url))
        row = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else payload if isinstance(payload, dict) else None
        if row is None:
            raise MarketDataError(f"FMP returned no fundamentals for {ticker}")
        timestamp = int(datetime.now(UTC).timestamp())
        metrics = {key: row.get(key) for key in ("companyName", "marketCap", "price", "beta", "lastDividend", "sector", "industry", "exchange")}
        return FundamentalSnapshot(ticker.upper(), timestamp, metrics, self.name)


class FredEconomicProvider:
    name = "FRED"

    def __init__(self, api_key: str, transport: JsonTransport | None = None) -> None:
        if not api_key:
            raise MarketDataError("FRED API key is required")
        self.api_key = api_key
        self.transport = transport or UrllibJsonTransport()

    def fetch_series(self, series_id: str, *, start: str | None = None, end: str | None = None) -> list[EconomicObservation]:
        params: dict[str, object] = {"series_id": series_id, "api_key": self.api_key, "file_type": "json", "sort_order": "asc"}
        if start:
            params["observation_start"] = start
        if end:
            params["observation_end"] = end
        payload = self.transport.send(JsonRequest("GET", "https://api.stlouisfed.org/fred/series/observations?" + urlencode(params)))
        rows = payload.get("observations", []) if isinstance(payload, dict) else []
        output = []
        for row in rows:
            if not isinstance(row, dict) or row.get("value") in {None, "."}:
                continue
            timestamp = int(datetime.fromisoformat(str(row["date"])).replace(tzinfo=UTC).timestamp())
            output.append(EconomicObservation(series_id.upper(), timestamp, finite(row["value"], "FRED observation"), self.name))
        return output


class MarketIngestionPipeline:
    """Normalize and upsert market records by stable natural keys."""

    def __init__(self, store: RecordStore, *, bar_providers: dict[str, BarProvider] | None = None, fundamentals_provider: FundamentalsProvider | None = None, economic_provider: EconomicProvider | None = None) -> None:
        self.store = store
        self.bar_providers = {key.upper(): value for key, value in (bar_providers or {}).items()}
        self.fundamentals_provider = fundamentals_provider
        self.economic_provider = economic_provider

    def ingest_prices(self, ticker: str, provider: str, *, interval: str = "1d", start: int | None = None, end: int | None = None) -> dict[str, int]:
        adapter = self.bar_providers.get(provider.upper())
        if adapter is None:
            raise MarketDataError(f"Unknown bar provider '{provider}'")
        inserted = duplicates = 0
        rows = sorted(adapter.fetch_bars(ticker, interval=interval, start=start, end=end), key=lambda item: item.timestamp)
        prior_time = -1
        for bar in rows:
            if bar.timestamp < prior_time:
                raise MarketDataError("Provider returned non-chronological market bars")
            prior_time = bar.timestamp
            result = self.store.upsert("price_bars", stable_key(bar.ticker, bar.timestamp, interval, bar.source), bar.to_dict())
            inserted += int(result.inserted)
            duplicates += int(not result.inserted)
        return {"fetched": len(rows), "inserted": inserted, "duplicates": duplicates}

    def ingest_fundamentals(self, ticker: str) -> dict[str, int]:
        if self.fundamentals_provider is None:
            raise MarketDataError("Fundamentals provider is not configured")
        row = self.fundamentals_provider.fetch_snapshot(ticker)
        result = self.store.upsert("fundamentals", stable_key(row.ticker, row.timestamp, row.source), row.to_dict())
        return {"inserted": int(result.inserted), "duplicates": int(not result.inserted)}

    def ingest_economic_series(self, series_id: str, *, start: str | None = None, end: str | None = None) -> dict[str, int]:
        if self.economic_provider is None:
            raise MarketDataError("Economic provider is not configured")
        inserted = duplicates = 0
        rows = self.economic_provider.fetch_series(series_id, start=start, end=end)
        for row in rows:
            result = self.store.upsert("economic_observations", stable_key(row.series_id, row.timestamp, row.source), row.to_dict())
            inserted += int(result.inserted)
            duplicates += int(not result.inserted)
        return {"fetched": len(rows), "inserted": inserted, "duplicates": duplicates}


__all__ = [
    "BinanceOhlcvProvider", "FmpFundamentalsProvider", "FredEconomicProvider", "MarketDataError",
    "MarketIngestionPipeline", "YahooOhlcvProvider",
]
