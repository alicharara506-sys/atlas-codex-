"""Trusted, zero-cost provider adapters for ATLAS V4 context intelligence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Final
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree

import httpx

from app.core.http import JsonRequest, JsonTransport, UrllibJsonTransport
from app.data.market.pipeline import EconomicProvider
from app.v4.context_contracts import EconomicEvent, NewsItem, NumericSeries, RiskLevel, TimedValue

FRED_RELEASE_DATES_URL: Final = "https://api.stlouisfed.org/fred/releases/dates"


@dataclass(frozen=True, slots=True)
class MacroSeriesSpec:
    """One explicit logical factor mapped to one official FRED series."""

    name: str
    series_id: str
    unit: str
    max_age_hours: float


DEFAULT_MACRO_MANIFEST: Final = (
    MacroSeriesSpec("FED_FUNDS", "DFF", "PERCENT", 24 * 7),
    MacroSeriesSpec("US2Y", "DGS2", "PERCENT", 24 * 7),
    MacroSeriesSpec("US10Y", "DGS10", "PERCENT", 24 * 7),
    MacroSeriesSpec("US10Y_REAL", "DFII10", "PERCENT", 24 * 7),
    MacroSeriesSpec("YIELD_CURVE_10Y2Y", "T10Y2Y", "PERCENTAGE_POINTS", 24 * 7),
    MacroSeriesSpec("CPI", "CPIAUCSL", "INDEX", 24 * 62),
    MacroSeriesSpec("EMPLOYMENT", "PAYEMS", "THOUSANDS", 24 * 62),
    MacroSeriesSpec("UNEMPLOYMENT", "UNRATE", "PERCENT", 24 * 62),
    MacroSeriesSpec("VIX", "VIXCLS", "INDEX", 24 * 7),
    MacroSeriesSpec("USD_BROAD_INDEX", "DTWEXBGS", "INDEX", 24 * 7),
)

OFFICIAL_RSS_FEEDS: Final[dict[str, str]] = {
    "FEDERAL_RESERVE": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "ECB": "https://www.ecb.europa.eu/rss/press.html",
    "BANK_OF_JAPAN": "https://www.boj.or.jp/en/rss/whatsnew.xml",
    "BIS": "https://www.bis.org/doclist/cbspeeches.rss",
}

_RELEASE_TERMS: Final = (
    "consumer price",
    "personal income and outlays",
    "employment situation",
    "gross domestic product",
    "retail sales",
    "industrial production",
)


class ProviderDataError(RuntimeError):
    """Raised when a trusted provider returns invalid or unsafe data."""


class FredMacroBundleProvider:
    """Convert official FRED observations into typed numeric macro series."""

    def __init__(
        self,
        provider: EconomicProvider,
        *,
        manifest: tuple[MacroSeriesSpec, ...] = DEFAULT_MACRO_MANIFEST,
    ) -> None:
        if not manifest:
            raise ValueError("FRED macro manifest cannot be empty")
        self.provider = provider
        self.manifest = manifest

    def fetch(self, *, as_of: datetime, lookback_days: int = 730) -> tuple[tuple[NumericSeries, ...], tuple[str, ...]]:
        if not 30 <= lookback_days <= 3_650:
            raise ValueError("FRED lookback_days must be between 30 and 3650")
        end = as_of.astimezone(UTC).date().isoformat()
        start = (as_of.astimezone(UTC) - timedelta(days=lookback_days)).date().isoformat()
        series: list[NumericSeries] = []
        issues: list[str] = []
        for spec in self.manifest:
            try:
                observations = self.provider.fetch_series(spec.series_id, start=start, end=end)
                values = tuple(
                    TimedValue(timestamp=datetime.fromtimestamp(row.timestamp, UTC), value=row.value)
                    for row in observations
                    if datetime.fromtimestamp(row.timestamp, UTC) <= as_of
                )
                if len(values) < 2:
                    issues.append(f"FRED_{spec.series_id}_INSUFFICIENT_HISTORY")
                    continue
                series.append(
                    NumericSeries(
                        name=spec.name,
                        unit=spec.unit,
                        source=f"FRED:{spec.series_id}",
                        values=values,
                        max_age_hours=spec.max_age_hours,
                    )
                )
            except (RuntimeError, TypeError, ValueError) as exc:  # failures are isolated per series
                issues.append(f"FRED_{spec.series_id}_FAILED:{type(exc).__name__}")
        return tuple(series), tuple(issues)


class FredReleaseCalendarProvider:
    """Fetch date-only official FRED release dates without inventing event times."""

    def __init__(self, api_key: str, *, transport: JsonTransport | None = None) -> None:
        if not api_key or len(api_key.strip()) != 32:
            raise ValueError("FRED API key must contain 32 characters")
        self.api_key = api_key.strip()
        self.transport = transport or UrllibJsonTransport()

    def fetch(self, *, as_of: datetime, horizon_days: int = 14) -> tuple[EconomicEvent, ...]:
        if not 1 <= horizon_days <= 90:
            raise ValueError("FRED release horizon must be between 1 and 90 days")
        start = as_of.astimezone(UTC).date()
        end = start + timedelta(days=horizon_days)
        params = {
            "api_key": self.api_key,
            "file_type": "json",
            "include_release_dates_with_no_data": "true",
            "realtime_start": start.isoformat(),
            "realtime_end": end.isoformat(),
            "limit": "1000",
            "sort_order": "asc",
        }
        request = JsonRequest("GET", f"{FRED_RELEASE_DATES_URL}?{urlencode(params)}", timeout_seconds=8.0)
        payload = self.transport.send(request)
        rows = payload.get("release_dates") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ProviderDataError("FRED release-date response must contain release_dates")
        events: list[EconomicEvent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("release_name") or "").strip()
            if not name or not any(term in name.casefold() for term in _RELEASE_TERMS):
                continue
            try:
                release_date = datetime.fromisoformat(str(row["date"])).date()
            except (KeyError, ValueError) as exc:
                raise ProviderDataError("FRED release date is invalid") from exc
            if not start <= release_date <= end:
                continue
            release_id = str(row.get("release_id") or name).strip()
            events.append(
                EconomicEvent(
                    event_id=f"FRED:{release_id}:{release_date.isoformat()}",
                    name=name,
                    currency="USD",
                    scheduled_at=datetime.combine(release_date, datetime.min.time(), UTC),
                    importance=RiskLevel.HIGH,
                    source="FRED_RELEASE_CALENDAR",
                    time_precision="DATE_ONLY",
                )
            )
        return tuple(sorted(events, key=lambda event: (event.scheduled_at, event.event_id)))


class OfficialRssNewsProvider:
    """Fetch a fixed allowlist of official central-bank and BIS RSS feeds."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 8.0,
        max_response_bytes: int = 2_000_000,
        feeds: dict[str, str] | None = None,
    ) -> None:
        if not 0.5 <= timeout_seconds <= 30:
            raise ValueError("RSS timeout must be between 0.5 and 30 seconds")
        if not 10_000 <= max_response_bytes <= 5_000_000:
            raise ValueError("RSS response limit must be between 10000 and 5000000 bytes")
        resolved = dict(OFFICIAL_RSS_FEEDS if feeds is None else feeds)
        for source, url in resolved.items():
            expected = OFFICIAL_RSS_FEEDS.get(source)
            if expected != url:
                raise ValueError("RSS feeds must match the ATLAS official allowlist")
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("official RSS feeds must use safe HTTPS URLs")
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.feeds = resolved

    def fetch(self, *, as_of: datetime, lookback_hours: int = 168) -> tuple[tuple[NewsItem, ...], tuple[str, ...]]:
        if not 1 <= lookback_hours <= 24 * 31:
            raise ValueError("RSS lookback must be between 1 and 744 hours")
        cutoff = as_of - timedelta(hours=lookback_hours)
        items: list[NewsItem] = []
        issues: list[str] = []
        for source, url in self.feeds.items():
            try:
                response = self._get(url)
                items.extend(_parse_feed(response.content, source=source, cutoff=cutoff, as_of=as_of))
            except (ProviderDataError, TypeError, ValueError) as exc:
                issues.append(f"RSS_{source}_FAILED:{type(exc).__name__}")
        unique = {(item.source, item.title, item.published_at, item.url): item for item in items}
        ordered = tuple(sorted(unique.values(), key=lambda item: (item.published_at, item.source, item.title)))
        return ordered, tuple(issues)

    def _get(self, url: str) -> httpx.Response:
        try:
            response = (
                self.client.get(url, timeout=self.timeout_seconds)
                if self.client is not None
                else httpx.get(url, timeout=self.timeout_seconds)
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderDataError(f"official RSS request failed: {type(exc).__name__}") from exc
        if len(response.content) > self.max_response_bytes:
            raise ProviderDataError("official RSS response exceeded the size limit")
        return response


def _parse_feed(content: bytes, *, source: str, cutoff: datetime, as_of: datetime) -> list[NewsItem]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ProviderDataError("official RSS response is invalid XML") from exc
    items: list[NewsItem] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = _child_text(node, ("title",))
        published = _child_text(node, ("pubDate", "published", "updated", "date"))
        if not title or not published:
            continue
        published_at = _parse_timestamp(published)
        if published_at is None or published_at < cutoff or published_at > as_of:
            continue
        link = _link(node)
        items.append(NewsItem(title=title, source=source, published_at=published_at, url=link))
    return items


def _child_text(node: ElementTree.Element, names: Iterable[str]) -> str | None:
    allowed = set(names)
    for child in node.iter():
        if child is node:
            continue
        if _local_name(child.tag) in allowed and child.text and child.text.strip():
            return child.text.strip()
    return None


def _link(node: ElementTree.Element) -> str | None:
    for child in node.iter():
        if _local_name(child.tag) != "link":
            continue
        value = (child.attrib.get("href") or child.text or "").strip()
        if value and urlparse(value).scheme in {"http", "https"}:
            return value
    return None


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


__all__ = [
    "DEFAULT_MACRO_MANIFEST",
    "FRED_RELEASE_DATES_URL",
    "OFFICIAL_RSS_FEEDS",
    "FredMacroBundleProvider",
    "FredReleaseCalendarProvider",
    "MacroSeriesSpec",
    "OfficialRssNewsProvider",
    "ProviderDataError",
]
