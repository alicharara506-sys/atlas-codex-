"""Provider-managed Phase 3 context refresh with explicit partial-data states."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.store import RecordStore
from app.v4.context_contracts import (
    ContextComputationRequest,
    ContextSet,
    COTReport,
    EconomicEvent,
    NewsItem,
    NumericSeries,
    TimedValue,
)
from app.v4.context_service import V4ContextService
from app.v4.contracts import FeatureSet, LiquidityState, MarketSnapshot, OfficialTimeframe
from app.v4.feature_service import FEATURE_COLLECTION
from app.v4.positioning import CFTCDataError, CFTCPositioningClient
from app.v4.providers import (
    FredMacroBundleProvider,
    FredReleaseCalendarProvider,
    OfficialRssNewsProvider,
    ProviderDataError,
)
from app.v4.snapshots import SNAPSHOT_COLLECTION
from app.v4.symbols import normalize_instrument

_CFTC_MARKETS = {
    "XAUUSD": "GOLD",
    "XAGUSD": "SILVER",
    "WTI": "CRUDE OIL, LIGHT SWEET",
    "BRENT": "CRUDE OIL, BRENT",
}


class V4ContextIngestionService:
    """Refresh context only from configured, trusted, read-only providers."""

    def __init__(
        self,
        store: RecordStore,
        context_service: V4ContextService,
        *,
        fred_macro: FredMacroBundleProvider | None = None,
        fred_calendar: FredReleaseCalendarProvider | None = None,
        official_news: OfficialRssNewsProvider | None = None,
        cftc: CFTCPositioningClient | None = None,
    ) -> None:
        self.store = store
        self.context_service = context_service
        self.fred_macro = fred_macro
        self.fred_calendar = fred_calendar
        self.official_news = official_news
        self.cftc = cftc

    def refresh(self, instrument: str, *, as_of: datetime | None = None, persist: bool = True) -> ContextSet:
        canonical = normalize_instrument(instrument).canonical
        resolved_as_of = (as_of or datetime.now(UTC)).astimezone(UTC)
        issues: list[str] = []

        macro_series: tuple[NumericSeries, ...] = ()
        required_macro_factors: tuple[str, ...] = ()
        if self.fred_macro is None:
            issues.append("FRED_MACRO_NOT_CONFIGURED")
        else:
            macro_series, macro_issues = self.fred_macro.fetch(as_of=resolved_as_of)
            issues.extend(macro_issues)
            required_macro_factors = tuple(spec.name for spec in self.fred_macro.manifest)

        news: tuple[NewsItem, ...] = ()
        if self.official_news is None:
            issues.append("OFFICIAL_RSS_NOT_CONFIGURED")
        else:
            news, news_issues = self.official_news.fetch(as_of=resolved_as_of)
            issues.extend(news_issues)
            if not news:
                issues.append("OFFICIAL_RSS_NO_RECENT_ITEMS")

        events: tuple[EconomicEvent, ...] = ()
        if self.fred_calendar is None:
            issues.append("FRED_RELEASE_CALENDAR_NOT_CONFIGURED")
        else:
            try:
                events = self.fred_calendar.fetch(as_of=resolved_as_of)
            except (ProviderDataError, RuntimeError, TypeError, ValueError) as exc:
                issues.append(f"FRED_RELEASE_CALENDAR_FAILED:{type(exc).__name__}")

        cot_reports: tuple[COTReport, ...] = ()
        cftc_market = _CFTC_MARKETS.get(canonical)
        if cftc_market is None:
            issues.append("CFTC_DIRECT_CONTRACT_MAPPING_UNAVAILABLE")
        elif self.cftc is None:
            issues.append("CFTC_NOT_CONFIGURED")
        else:
            try:
                cot_reports = self.cftc.fetch(cftc_market)
            except (CFTCDataError, RuntimeError, TypeError, ValueError) as exc:
                issues.append(f"CFTC_FETCH_FAILED:{type(exc).__name__}")

        snapshot = self._latest_snapshot(canonical, resolved_as_of)
        gold_prices = _gold_prices(snapshot) if canonical == "XAUUSD" else None
        if canonical == "XAUUSD" and gold_prices is None:
            issues.append("MT5_GOLD_D1_HISTORY_UNAVAILABLE")

        feature_set = self._latest_feature_set(canonical, resolved_as_of)
        liquidity = feature_set.liquidity.state if feature_set is not None else LiquidityState.UNAVAILABLE
        if feature_set is None:
            issues.append("DETERMINISTIC_FEATURE_SET_UNAVAILABLE")

        request = ContextComputationRequest(
            as_of=resolved_as_of,
            macro_series=macro_series,
            required_macro_factors=required_macro_factors,
            gold_prices=gold_prices,
            cot_reports=cot_reports,
            news=news,
            events=events,
            liquidity_state=liquidity,
        )
        return self.context_service.compute(
            canonical,
            request,
            persist=persist,
            provider_managed_ingestion=True,
            provider_issues=tuple(issues),
        )

    def _latest_snapshot(self, instrument: str, as_of: datetime) -> MarketSnapshot | None:
        candidates: list[MarketSnapshot] = []
        for row in self.store.values(SNAPSHOT_COLLECTION):
            if row.get("instrument") != instrument:
                continue
            snapshot = MarketSnapshot.model_validate(row)
            if snapshot.captured_at <= as_of:
                candidates.append(snapshot)
        return max(candidates, key=lambda row: row.captured_at, default=None)

    def _latest_feature_set(self, instrument: str, as_of: datetime) -> FeatureSet | None:
        candidates: list[FeatureSet] = []
        for row in self.store.values(FEATURE_COLLECTION):
            if row.get("instrument") != instrument:
                continue
            feature_set = FeatureSet.model_validate(row)
            if feature_set.input_cutoff <= as_of:
                candidates.append(feature_set)
        return max(candidates, key=lambda row: row.computed_at, default=None)


def _gold_prices(snapshot: MarketSnapshot | None) -> NumericSeries | None:
    if snapshot is None:
        return None
    daily = snapshot.timeframes.get(OfficialTimeframe.D1, ())
    by_date = {bar.timestamp.astimezone(UTC).date(): bar.close for bar in daily}
    if len(by_date) < 2:
        return None
    values = tuple(
        TimedValue(timestamp=datetime.combine(day, datetime.min.time(), UTC), value=close)
        for day, close in sorted(by_date.items())
    )
    return NumericSeries(
        name="XAUUSD",
        unit="PRICE",
        source=f"MT5:{snapshot.broker_symbol}:1D:CLOSE",
        values=values,
        max_age_hours=24 * 7,
    )


__all__ = ["V4ContextIngestionService"]
