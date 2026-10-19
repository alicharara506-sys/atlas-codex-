"""Multi-source sentiment ingestion normalized to the closed [-1, 1] range."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol
from urllib.parse import urlencode

from app.core.http import JsonRequest, JsonTransport, UrllibJsonTransport
from app.core.store import RecordStore, stable_key
from app.data.contracts import SentimentObservation, finite


class SentimentDataError(RuntimeError):
    pass


class SentimentProvider(Protocol):
    name: str
    def fetch(self, ticker: str, *, start: int, end: int) -> list[SentimentObservation]: ...


class KeywordSentiment:
    """Transparent offline fallback for unscored headlines."""

    POSITIVE: ClassVar[frozenset[str]] = frozenset(
        {"beat", "beats", "growth", "upgrade", "surge", "record", "profit", "bullish", "approval", "rally"}
    )
    NEGATIVE: ClassVar[frozenset[str]] = frozenset(
        {"miss", "misses", "downgrade", "loss", "fraud", "decline", "bearish", "lawsuit", "default", "plunge"}
    )

    @classmethod
    def score(cls, text: str) -> float:
        tokens = {token.strip(".,:;!?()[]{}\"'").lower() for token in text.split()}
        positive = len(tokens & cls.POSITIVE)
        negative = len(tokens & cls.NEGATIVE)
        total = positive + negative
        return 0.0 if total == 0 else (positive - negative) / total


class FinnhubNewsProvider:
    name = "FINNHUB_NEWS"

    def __init__(self, api_key: str, transport: JsonTransport | None = None) -> None:
        if not api_key:
            raise SentimentDataError("Finnhub API key is required")
        self.api_key = api_key
        self.transport = transport or UrllibJsonTransport()

    def fetch(self, ticker: str, *, start: int, end: int) -> list[SentimentObservation]:
        start_date = datetime.fromtimestamp(start, UTC).date().isoformat()
        end_date = datetime.fromtimestamp(end, UTC).date().isoformat()
        url = "https://finnhub.io/api/v1/company-news?" + urlencode({"symbol": ticker, "from": start_date, "to": end_date, "token": self.api_key})
        payload = self.transport.send(JsonRequest("GET", url))
        if not isinstance(payload, list):
            raise SentimentDataError("Finnhub company-news response must be an array")
        rows = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            text = f"{item.get('headline', '')} {item.get('summary', '')}".strip()
            timestamp = int(item.get("datetime") or start)
            rows.append(SentimentObservation(ticker, self.name, timestamp, KeywordSentiment.score(text), text[:2_000], "NEWS", {"url": item.get("url"), "provider_id": item.get("id")}))
        return rows


class EodhdSocialProvider:
    name = "EODHD_SOCIAL"

    def __init__(self, api_key: str, transport: JsonTransport | None = None) -> None:
        if not api_key:
            raise SentimentDataError("EODHD API key is required")
        self.api_key = api_key
        self.transport = transport or UrllibJsonTransport()

    def fetch(self, ticker: str, *, start: int, end: int) -> list[SentimentObservation]:
        params = {"s": ticker, "from": datetime.fromtimestamp(start, UTC).date().isoformat(), "to": datetime.fromtimestamp(end, UTC).date().isoformat(), "api_token": self.api_key, "fmt": "json"}
        payload = self.transport.send(JsonRequest("GET", "https://eodhd.com/api/sentiments?" + urlencode(params)))
        rows = payload if isinstance(payload, list) else payload.get(ticker, []) if isinstance(payload, dict) else []
        output = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            timestamp = _timestamp(item.get("date") or item.get("timestamp"), start)
            raw = item.get("normalized", item.get("sentiment", item.get("score", 0)))
            score = finite(raw, "EODHD sentiment")
            if score > 1 or score < -1:
                score = max(-1.0, min(1.0, score / 100.0))
            output.append(SentimentObservation(ticker, self.name, timestamp, score, None, "SOCIAL", item))
        return output


class LicensedNewsProvider:
    """Configurable adapter for a licensed Reuters/Bloomberg-compatible feed."""

    def __init__(self, name: str, endpoint: str, token: str, transport: JsonTransport | None = None) -> None:
        if not endpoint.startswith("https://") or not token:
            raise SentimentDataError("Licensed news feeds require HTTPS endpoint and token")
        self.name = name.upper()
        self.endpoint = endpoint
        self.token = token
        self.transport = transport or UrllibJsonTransport()

    def fetch(self, ticker: str, *, start: int, end: int) -> list[SentimentObservation]:
        payload = self.transport.send(JsonRequest("GET", self.endpoint + "?" + urlencode({"ticker": ticker, "start": start, "end": end}), {"Authorization": f"Bearer {self.token}"}))
        rows = payload.get("items", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        output = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            text = str(item.get("headline", item.get("text", "")))
            score = float(item["score"]) if isinstance(item.get("score"), (int, float)) else KeywordSentiment.score(text)
            output.append(SentimentObservation(ticker, self.name, _timestamp(item.get("timestamp"), start), score, text[:2_000], "NEWS", {"provider_id": item.get("id")}))
        return output


class OnChainMetricProvider:
    """Configurable adapter for normalized on-chain score endpoints."""

    def __init__(self, endpoint: str, token: str | None = None, transport: JsonTransport | None = None) -> None:
        if not endpoint.startswith("https://"):
            raise SentimentDataError("On-chain endpoint must use HTTPS")
        self.name = "ON_CHAIN"
        self.endpoint = endpoint
        self.token = token
        self.transport = transport or UrllibJsonTransport()

    def fetch(self, ticker: str, *, start: int, end: int) -> list[SentimentObservation]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        payload = self.transport.send(JsonRequest("GET", self.endpoint + "?" + urlencode({"asset": ticker, "start": start, "end": end}), headers))
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return [SentimentObservation(ticker, self.name, _timestamp(row.get("timestamp"), start), float(row.get("score", 0)), None, "ON_CHAIN", row) for row in rows if isinstance(row, dict)]


class SentimentIngestionPipeline:
    def __init__(self, store: RecordStore, providers: list[SentimentProvider], source_weights: dict[str, float] | None = None) -> None:
        self.store = store
        self.providers = providers
        self.source_weights = {key.upper(): max(0.0, float(value)) for key, value in (source_weights or {}).items()}

    def ingest(self, ticker: str, *, start: int, end: int) -> dict[str, Any]:
        if start <= 0 or end < start:
            raise SentimentDataError("Invalid sentiment time window")
        inserted = duplicates = 0
        by_day: dict[str, list[SentimentObservation]] = defaultdict(list)
        provider_errors: dict[str, str] = {}
        for provider in self.providers:
            try:
                rows = provider.fetch(ticker, start=start, end=end)
            except Exception as exc:  # noqa: BLE001 - each optional provider must fail independently
                provider_errors[provider.name] = str(exc)
                continue
            for row in rows:
                if row.timestamp < start or row.timestamp > end:
                    continue
                key = stable_key(row.ticker, row.source, row.timestamp, row.text, row.metadata)
                result = self.store.upsert("sentiment_snapshots", key, row.to_dict())
                inserted += int(result.inserted)
                duplicates += int(not result.inserted)
                day = datetime.fromtimestamp(row.timestamp, UTC).date().isoformat()
                by_day[day].append(row)
        aggregate_rows = []
        for day, rows in sorted(by_day.items()):
            numerator = denominator = 0.0
            for row in rows:
                weight = self.source_weights.get(row.source.upper(), 1.0)
                numerator += row.score * weight
                denominator += weight
            score = 0.0 if denominator == 0 else max(-1.0, min(1.0, numerator / denominator))
            payload = {"ticker": ticker.upper(), "date": day, "score": round(score, 6), "sources": sorted({row.source for row in rows}), "observations": len(rows)}
            self.store.upsert("sentiment_daily", stable_key(ticker.upper(), day), payload)
            aggregate_rows.append(payload)
        return {"inserted": inserted, "duplicates": duplicates, "daily": aggregate_rows, "provider_errors": provider_errors}


def _timestamp(value: Any, fallback: int) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value:
        try:
            return int(datetime.fromisoformat(value).timestamp())
        except ValueError:
            return fallback
    return fallback


__all__ = [
    "EodhdSocialProvider", "FinnhubNewsProvider", "KeywordSentiment", "LicensedNewsProvider",
    "OnChainMetricProvider", "SentimentDataError", "SentimentIngestionPipeline",
]
