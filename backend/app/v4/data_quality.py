"""Fail-closed deterministic data-quality checks for ATLAS V4."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from app.v4.contracts import (
    AssetClass,
    DataQualityIssue,
    DataQualityReport,
    DataQualityStatus,
    IssueSeverity,
    MT5ConnectionState,
    MT5HealthReport,
    OfficialTimeframe,
    V4Bar,
    V4Quote,
    V4Tick,
)
from app.v4.mt5 import retail_fx_market_closed
from app.v4.symbols import OFFICIAL_TIMEFRAMES

_SECONDS = {
    OfficialTimeframe.M15: 15 * 60,
    OfficialTimeframe.H1: 60 * 60,
    OfficialTimeframe.H4: 4 * 60 * 60,
    OfficialTimeframe.D1: 24 * 60 * 60,
}


class DataQualityEngine:
    """Score data deterministically and veto publication on any critical issue."""

    def __init__(self, *, minimum_bars: int = 60, max_spread_bps: float = 30.0) -> None:
        if minimum_bars < 20:
            raise ValueError("minimum_bars must be at least 20")
        if not 0 < max_spread_bps <= 500:
            raise ValueError("max_spread_bps must be between 0 and 500")
        self.minimum_bars = minimum_bars
        self.max_spread_bps = max_spread_bps

    def evaluate(
        self,
        *,
        now: datetime,
        health: MT5HealthReport,
        quote: V4Quote,
        asset_class: AssetClass,
        timeframes: dict[OfficialTimeframe, tuple[V4Bar, ...]],
        ticks: tuple[V4Tick, ...],
        ingestion_issues: tuple[DataQualityIssue, ...] = (),
    ) -> DataQualityReport:
        checked_at = now.astimezone(UTC)
        issues = list(ingestion_issues)

        if health.state in {MT5ConnectionState.DISCONNECTED, MT5ConnectionState.RECONNECTING, MT5ConnectionState.AUTH_FAILED}:
            self._add(issues, "MT5_UNAVAILABLE", "MT5 is not available", critical=True)
        elif health.state is MT5ConnectionState.MARKET_CLOSED:
            self._add(issues, "MARKET_CLOSED", "The broker market is closed", critical=True)
        elif health.state is MT5ConnectionState.DEGRADED:
            self._add(issues, "MT5_DEGRADED", "MT5 health is degraded", critical=True)

        quote_age = (checked_at - quote.timestamp).total_seconds()
        if quote_age < -5:
            self._add(issues, "FUTURE_QUOTE", "Quote timestamp is in the future", critical=True)
        elif quote_age > 120:
            code = "MARKET_CLOSED" if retail_fx_market_closed(checked_at) else "STALE_QUOTE"
            self._add(issues, code, "Quote is not fresh enough for publication", critical=True)

        mid = (quote.bid + quote.ask) / 2
        spread_bps = ((quote.ask - quote.bid) / mid) * 10_000
        if not math.isfinite(spread_bps) or spread_bps < 0:
            self._add(issues, "INVALID_SPREAD", "Spread is invalid", critical=True)
        elif spread_bps > self.max_spread_bps:
            self._add(issues, "ABNORMAL_SPREAD", f"Spread is {spread_bps:.2f} bps", critical=True)

        for timeframe in OFFICIAL_TIMEFRAMES:
            bars = timeframes.get(timeframe)
            if not bars:
                self._add(issues, "MISSING_TIMEFRAME", "Required timeframe is missing", critical=True, timeframe=timeframe)
                continue
            if len(bars) < self.minimum_bars:
                self._add(
                    issues,
                    "INSUFFICIENT_BARS",
                    f"Only {len(bars)} bars; {self.minimum_bars} required",
                    critical=True,
                    timeframe=timeframe,
                )
            timestamps = [bar.timestamp.astimezone(UTC) for bar in bars]
            if len(set(timestamps)) != len(timestamps):
                self._add(issues, "DUPLICATE_BARS", "Duplicate bar timestamps detected", critical=True, timeframe=timeframe)
            if timestamps != sorted(timestamps):
                self._add(issues, "NON_MONOTONIC_BARS", "Bars are not chronological", critical=True, timeframe=timeframe)
            self._check_gaps(issues, timeframe, timestamps, asset_class)
            latest = max(timestamps)
            age = (checked_at - latest).total_seconds()
            max_age = _SECONDS[timeframe] * 3
            if age < -5:
                self._add(issues, "FUTURE_BAR", "Latest bar timestamp is in the future", critical=True, timeframe=timeframe)
            elif age > max_age and not retail_fx_market_closed(checked_at):
                self._add(issues, "STALE_BARS", "Latest bar is stale", critical=True, timeframe=timeframe)

            closes = [bar.close for bar in bars]
            if len(closes) >= 20 and max(closes[-20:]) == min(closes[-20:]) and not retail_fx_market_closed(checked_at):
                self._add(issues, "FROZEN_BARS", "Recent closes are unchanged", critical=True, timeframe=timeframe)

        if len(ticks) >= 10 and not retail_fx_market_closed(checked_at):
            recent = ticks[-10:]
            if len({(tick.bid, tick.ask) for tick in recent}) == 1:
                self._add(issues, "FROZEN_TICKS", "Recent broker ticks are unchanged", critical=False)
        elif not ticks:
            self._add(issues, "NO_TICK_WINDOW", "No tick window was available", critical=False)

        issues = _deduplicate(issues)
        critical = [issue for issue in issues if issue.blocking]
        score = max(0.0, 100.0 - sum(25.0 if issue.blocking else 5.0 for issue in issues))
        status = _status_for(issues)
        return DataQualityReport(
            status=status,
            score=score,
            checked_at=checked_at,
            issues=tuple(issues),
            publication_eligible=not critical and status is DataQualityStatus.HEALTHY,
        )

    @staticmethod
    def _check_gaps(
        issues: list[DataQualityIssue],
        timeframe: OfficialTimeframe,
        timestamps: list[datetime],
        asset_class: AssetClass,
    ) -> None:
        expected = _SECONDS[timeframe]
        for left, right in zip(sorted(timestamps), sorted(timestamps)[1:], strict=False):
            if (right - left).total_seconds() <= expected * 3:
                continue
            if _contains_weekend(left, right):
                continue
            scheduled_commodity_break = (
                asset_class is AssetClass.COMMODITY
                and right - left <= timedelta(hours=2)
                and (left.hour >= 20 or right.hour <= 2)
            )
            if scheduled_commodity_break:
                # Retail metals/energy CFDs have a documented daily maintenance
                # break. It is not a missing-data defect when the boundary is
                # in the UTC evening window and the gap remains bounded.
                continue
            DataQualityEngine._add(
                issues,
                "BAR_GAP",
                f"Unexpected gap from {left.isoformat()} to {right.isoformat()}",
                critical=True,
                timeframe=timeframe,
            )
            break

    @staticmethod
    def _add(
        issues: list[DataQualityIssue],
        code: str,
        message: str,
        *,
        critical: bool,
        timeframe: OfficialTimeframe | None = None,
    ) -> None:
        issues.append(
            DataQualityIssue(
                code=code,
                severity=IssueSeverity.CRITICAL if critical else IssueSeverity.WARNING,
                message=message,
                timeframe=timeframe,
                blocking=critical,
            )
        )


def _contains_weekend(start: datetime, end: datetime) -> bool:
    cursor = start.astimezone(UTC).date()
    last = end.astimezone(UTC).date()
    while cursor <= last:
        if cursor.weekday() >= 5:
            return True
        cursor += timedelta(days=1)
    return False


def _deduplicate(issues: list[DataQualityIssue]) -> list[DataQualityIssue]:
    unique: dict[tuple[str, OfficialTimeframe | None], DataQualityIssue] = {}
    for issue in issues:
        unique.setdefault((issue.code, issue.timeframe), issue)
    return list(unique.values())


def _status_for(issues: list[DataQualityIssue]) -> DataQualityStatus:
    codes = {issue.code for issue in issues if issue.blocking}
    if codes & {"INVALID_BAR", "INVALID_QUOTE", "INVALID_SPREAD", "FUTURE_QUOTE", "FUTURE_BAR"}:
        return DataQualityStatus.INVALID
    if codes & {"STALE_QUOTE", "STALE_BARS", "MARKET_CLOSED", "FROZEN_BARS"}:
        return DataQualityStatus.STALE
    if codes & {"MISSING_TIMEFRAME", "INSUFFICIENT_BARS", "DUPLICATE_BARS", "NON_MONOTONIC_BARS", "BAR_GAP"}:
        return DataQualityStatus.INCOMPLETE
    if any(issue.blocking for issue in issues) or issues:
        return DataQualityStatus.DEGRADED
    return DataQualityStatus.HEALTHY


__all__ = ["DataQualityEngine"]
