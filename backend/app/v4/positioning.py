"""CFTC COT public-data adapter and non-directional positioning statistics."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from statistics import fmean, pstdev
from urllib.parse import urlparse

import httpx

from app.v4.context_contracts import ContextAvailability, COTReport, PositioningResult

CFTC_LEGACY_FUTURES_ONLY_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_SAFE_MARKET = re.compile(r"^[A-Za-z0-9 &'(),./+-]{1,120}$")


class CFTCDataError(RuntimeError):
    """Raised when official CFTC data cannot be safely parsed."""


class CFTCPositioningClient:
    """Fetch the official, free CFTC Legacy Futures Only dataset."""

    def __init__(
        self,
        base_url: str = CFTC_LEGACY_FUTURES_ONLY_URL,
        *,
        timeout_seconds: float = 8.0,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or parsed.hostname != "publicreporting.cftc.gov":
            raise ValueError("CFTC positioning source must use the official HTTPS public-reporting host")
        if not 0.5 <= timeout_seconds <= 30:
            raise ValueError("CFTC timeout must be between 0.5 and 30 seconds")
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.client = client

    def fetch(self, market_name: str, *, limit: int = 156) -> tuple[COTReport, ...]:
        if not _SAFE_MARKET.fullmatch(market_name):
            raise ValueError("market_name contains unsupported query characters")
        if not 2 <= limit <= 520:
            raise ValueError("CFTC row limit must be between 2 and 520")
        params = {
            "$select": (
                "market_and_exchange_names,report_date_as_yyyy_mm_dd,open_interest_all,"
                "noncomm_positions_long_all,noncomm_positions_short_all,comm_positions_long_all,"
                "comm_positions_short_all"
            ),
            "$where": f"upper(market_and_exchange_names) like '%{market_name.upper()}%'",
            "$order": "report_date_as_yyyy_mm_dd desc",
            "$limit": str(limit),
        }
        try:
            if self.client is not None:
                response = self.client.get(self.base_url, params=params, timeout=self.timeout_seconds)
            else:
                response = httpx.get(self.base_url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CFTCDataError(f"official CFTC request failed: {exc}") from exc
        if not isinstance(payload, list):
            raise CFTCDataError("official CFTC response must be a JSON list")
        reports = tuple(sorted((_parse_report(row) for row in payload), key=lambda row: row.report_date))
        if not reports:
            raise CFTCDataError("official CFTC response contained no matching reports")
        return reports


class PositioningEngine:
    """Compute transparent COT statistics without a contrarian assumption."""

    version = "cftc-positioning-v1"

    def calculate(self, reports: tuple[COTReport, ...], *, as_of: datetime) -> PositioningResult | None:
        eligible = sorted((row for row in reports if row.report_date <= as_of), key=lambda row: row.report_date)
        if not eligible:
            return None
        market_names = {row.market_name for row in eligible}
        if len(market_names) != 1:
            raise ValueError("positioning history must contain exactly one CFTC market")
        latest = eligible[-1]
        noncommercial = [row.noncommercial_long - row.noncommercial_short for row in eligible]
        commercial_net = latest.commercial_long - latest.commercial_short
        weekly_change = noncommercial[-1] - noncommercial[-2] if len(noncommercial) >= 2 else None
        window = noncommercial[-156:]
        deviation = pstdev(window) if len(window) >= 2 else 0.0
        z_score = (window[-1] - fmean(window)) / deviation if deviation > 0 else None
        percentile = sum(value <= window[-1] for value in window) / len(window)
        availability = ContextAvailability.AVAILABLE if len(window) >= 20 else ContextAvailability.DEGRADED
        return PositioningResult(
            market_name=latest.market_name,
            report_date=latest.report_date,
            commercial_net=commercial_net,
            noncommercial_net=noncommercial[-1],
            open_interest=latest.open_interest,
            weekly_change=weekly_change,
            z_score=z_score,
            percentile=percentile,
            sample_count=len(window),
            availability=availability,
        )


def _parse_report(payload: object) -> COTReport:
    if not isinstance(payload, dict):
        raise CFTCDataError("CFTC report row must be an object")
    try:
        report_date = datetime.fromisoformat(str(payload["report_date_as_yyyy_mm_dd"]))
        if report_date.tzinfo is None:
            report_date = report_date.replace(tzinfo=UTC)
        return COTReport(
            market_name=str(payload["market_and_exchange_names"]),
            report_date=report_date.astimezone(UTC),
            open_interest=float(payload["open_interest_all"]),
            noncommercial_long=float(payload["noncomm_positions_long_all"]),
            noncommercial_short=float(payload["noncomm_positions_short_all"]),
            commercial_long=float(payload["comm_positions_long_all"]),
            commercial_short=float(payload["comm_positions_short_all"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CFTCDataError(f"invalid CFTC report row: {exc}") from exc


__all__ = [
    "CFTC_LEGACY_FUTURES_ONLY_URL",
    "CFTCDataError",
    "CFTCPositioningClient",
    "PositioningEngine",
]
