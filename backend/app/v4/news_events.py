"""Defensive news and economic-event risk engines for ATLAS V4."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from app.v4.context_contracts import (
    EconomicEvent,
    EventAssessment,
    EventRiskResult,
    NewsCategory,
    NewsItem,
    NewsRiskResult,
    RiskLevel,
)
from app.v4.contracts import LiquidityState

_CATEGORY_TERMS: dict[NewsCategory, tuple[str, ...]] = {
    NewsCategory.CENTRAL_BANK: ("central bank", "fomc", "federal reserve", "ecb", "boe", "boj", "rate decision"),
    NewsCategory.INFLATION: ("inflation", "cpi", "pce", "ppi"),
    NewsCategory.EMPLOYMENT: ("nonfarm", "nfp", "unemployment", "payroll", "jobs report"),
    NewsCategory.GEOPOLITICAL: ("war", "missile", "invasion", "sanction", "ceasefire", "geopolitical"),
    NewsCategory.COMMODITY_SUPPLY: ("opec", "pipeline", "inventory", "production cut", "supply disruption"),
    NewsCategory.CREDIT_SYSTEMIC: ("default", "bank failure", "liquidity crisis", "downgrade", "systemic"),
}
_EXTREME_TERMS = ("emergency", "unexpected", "surprise intervention", "war declared", "default", "bank failure")
_HIGH_TERMS = ("rate decision", "fomc", "cpi", "nonfarm", "nfp", "sanction", "invasion", "opec")
_RISK_ORDER = {level: index for index, level in enumerate(RiskLevel)}
_RISK_LEVELS = tuple(RiskLevel)
_THIRTY_MINUTES = timedelta(minutes=30)


class NewsRiskEngine:
    """Detect risk/escalation and duplicates; never emit BUY/SELL sentiment."""

    version = "news-defensive-v1"

    def calculate(self, items: tuple[NewsItem, ...], *, as_of: datetime) -> NewsRiskResult:
        eligible = [item for item in items if item.published_at <= as_of]
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for item in sorted(eligible, key=lambda row: row.published_at):
            key = _news_key(item)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        categories: set[NewsCategory] = set()
        risk = RiskLevel.LOW
        category_times: dict[NewsCategory, list[datetime]] = {}
        uncertainty = False
        for item in unique:
            normalized = _normalize(item.title)
            matched = False
            for category, terms in _CATEGORY_TERMS.items():
                if any(term in normalized for term in terms):
                    matched = True
                    categories.add(category)
                    category_times.setdefault(category, []).append(item.published_at)
            if not matched:
                categories.add(NewsCategory.OTHER)
            if any(term in normalized for term in _EXTREME_TERMS):
                risk = _maximum_risk(risk, RiskLevel.EXTREME)
            elif any(term in normalized for term in _HIGH_TERMS):
                risk = _maximum_risk(risk, RiskLevel.HIGH)
            elif matched:
                risk = _maximum_risk(risk, RiskLevel.MODERATE)
            uncertainty = uncertainty or any(term in normalized for term in ("uncertain", "unclear", "unconfirmed"))
        narrative_change = any(
            len(timestamps) >= 2 and (timestamps[-1] - timestamps[0]).total_seconds() <= 6 * 3_600
            for timestamps in category_times.values()
        )
        veto = risk in {RiskLevel.HIGH, RiskLevel.EXTREME} and any(
            (as_of - item.published_at).total_seconds() <= 6 * 3_600 for item in unique
        )
        return NewsRiskResult(
            as_of=as_of,
            input_count=len(eligible),
            unique_count=len(unique),
            duplicate_count=len(eligible) - len(unique),
            categories=tuple(sorted(categories, key=lambda row: row.value)),
            risk=risk,
            uncertainty_detected=uncertainty,
            narrative_change_detected=narrative_change,
            candidate_veto=veto,
        )


class EventRiskEngine:
    """Assess instrument-relevant scheduled-event risk and veto windows."""

    version = "event-risk-v1"

    def calculate(
        self,
        instrument: str,
        events: tuple[EconomicEvent, ...],
        *,
        as_of: datetime,
        liquidity: LiquidityState = LiquidityState.UNAVAILABLE,
    ) -> EventRiskResult:
        affected = _affected_currencies(instrument)
        assessments: list[EventAssessment] = []
        for event in sorted(events, key=lambda row: row.scheduled_at):
            exact_minutes = (event.scheduled_at - as_of).total_seconds() / 60
            minutes = exact_minutes if event.time_precision == "EXACT" else None
            relevant = event.currency.upper() in affected
            risk = _event_risk(event, exact_minutes, relevant, liquidity, as_of)
            in_veto_window = (
                event.scheduled_at.date() == as_of.date()
                if event.time_precision == "DATE_ONLY"
                else -30 <= exact_minutes <= 60
            )
            veto = relevant and risk in {RiskLevel.HIGH, RiskLevel.EXTREME} and in_veto_window
            assessments.append(
                EventAssessment(
                    event_id=event.event_id,
                    name=event.name,
                    currency=event.currency.upper(),
                    scheduled_at=event.scheduled_at,
                    minutes_to_event=minutes,
                    time_precision=event.time_precision,
                    affects_instrument=relevant,
                    risk=risk,
                    candidate_veto=veto,
                )
            )
        relevant_rows = [row for row in assessments if row.affects_instrument]
        overall = max((row.risk for row in relevant_rows), key=_RISK_ORDER.__getitem__, default=RiskLevel.LOW)
        upcoming = [
            row
            for row in relevant_rows
            if (
                row.time_precision == "DATE_ONLY" and row.scheduled_at.date() == as_of.date()
            )
            or (row.time_precision == "EXACT" and row.scheduled_at >= as_of - _THIRTY_MINUTES)
        ]
        nearest = min(upcoming, key=lambda row: abs((row.scheduled_at - as_of).total_seconds()), default=None)
        return EventRiskResult(
            instrument=instrument,
            as_of=as_of,
            risk=overall,
            assessments=tuple(assessments),
            nearest_event_id=nearest.event_id if nearest else None,
            candidate_veto=any(row.candidate_veto for row in assessments),
        )


def _event_risk(
    event: EconomicEvent,
    minutes: float,
    relevant: bool,
    liquidity: LiquidityState,
    as_of: datetime,
) -> RiskLevel:
    if not relevant:
        return RiskLevel.LOW
    if event.time_precision == "DATE_ONLY":
        if event.scheduled_at.date() != as_of.date():
            return RiskLevel.MODERATE if 0 <= minutes <= 24 * 60 else RiskLevel.LOW
        return _maximum_risk(event.importance, RiskLevel.HIGH)
    if minutes < -30 or minutes > 24 * 60:
        return RiskLevel.LOW
    risk = event.importance if minutes <= 60 else RiskLevel.MODERATE
    if -15 <= minutes <= 15 and event.importance in {RiskLevel.HIGH, RiskLevel.EXTREME}:
        risk = RiskLevel.EXTREME
    elif -30 <= minutes <= 60 and risk is RiskLevel.MODERATE:
        risk = RiskLevel.HIGH
    if event.historical_volatility_percentile is not None and event.historical_volatility_percentile >= 0.9:
        risk = _maximum_risk(risk, RiskLevel.HIGH)
    if liquidity in {LiquidityState.THIN, LiquidityState.STRESSED} and -30 <= minutes <= 60:
        risk = _escalate(risk)
    return risk


def _affected_currencies(instrument: str) -> set[str]:
    canonical = instrument.upper()
    if len(canonical) == 6 and canonical.isalpha():
        return {canonical[:3], canonical[3:]}
    if canonical in {"XAUUSD", "XAGUSD", "WTI", "BRENT"}:
        return {"USD"}
    return set()


def _news_key(item: NewsItem) -> str:
    if item.url:
        parts = urlsplit(item.url)
        return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), "", ""))
    return _normalize(item.title)


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _maximum_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    return left if _RISK_ORDER[left] >= _RISK_ORDER[right] else right


def _escalate(level: RiskLevel) -> RiskLevel:
    return _RISK_LEVELS[min(_RISK_ORDER[level] + 1, len(_RISK_LEVELS) - 1)]


__all__ = ["EventRiskEngine", "NewsRiskEngine"]
