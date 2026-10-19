"""Versioned orchestration and persistence for ATLAS V4 Phase 3 context."""

from __future__ import annotations

import json

from app.core.store import RecordStore, stable_key
from app.v4.context_contracts import ContextAvailability, ContextComputationRequest, ContextSet
from app.v4.derivatives import DerivativesIntelligenceProvider, UnavailableDerivativesProvider
from app.v4.macro import GoldIntelligenceEngine, MacroIntelligenceEngine
from app.v4.news_events import EventRiskEngine, NewsRiskEngine
from app.v4.positioning import PositioningEngine
from app.v4.symbols import normalize_instrument

CONTEXT_COLLECTION = "v4_context_sets"
CONTEXT_VERSION = "context-foundation-v1"


class V4ContextService:
    """Build immutable numeric/defensive context with no signal authority."""

    def __init__(
        self,
        store: RecordStore,
        *,
        macro_engine: MacroIntelligenceEngine | None = None,
        gold_engine: GoldIntelligenceEngine | None = None,
        positioning_engine: PositioningEngine | None = None,
        news_engine: NewsRiskEngine | None = None,
        event_engine: EventRiskEngine | None = None,
        derivatives_provider: DerivativesIntelligenceProvider | None = None,
    ) -> None:
        self.store = store
        self.macro_engine = macro_engine or MacroIntelligenceEngine()
        self.gold_engine = gold_engine or GoldIntelligenceEngine()
        self.positioning_engine = positioning_engine or PositioningEngine()
        self.news_engine = news_engine or NewsRiskEngine()
        self.event_engine = event_engine or EventRiskEngine()
        self.derivatives_provider = derivatives_provider or UnavailableDerivativesProvider()

    def compute(
        self,
        instrument: str,
        request: ContextComputationRequest,
        *,
        persist: bool = True,
        provider_managed_ingestion: bool = False,
        provider_issues: tuple[str, ...] = (),
    ) -> ContextSet:
        canonical = normalize_instrument(instrument).canonical
        input_payload = request.model_dump(mode="json")
        input_json = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        input_hash = stable_key(input_json)
        macro = self.macro_engine.calculate(
            canonical,
            request.macro_series,
            as_of=request.as_of,
            required_factors=request.required_macro_factors,
        )
        gold = (
            self.gold_engine.calculate(request.gold_prices, request.macro_series, as_of=request.as_of)
            if canonical == "XAUUSD"
            else None
        )
        positioning = self.positioning_engine.calculate(request.cot_reports, as_of=request.as_of)
        news = self.news_engine.calculate(request.news, as_of=request.as_of)
        events = self.event_engine.calculate(
            canonical,
            request.events,
            as_of=request.as_of,
            liquidity=request.liquidity_state,
        )
        derivatives = self.derivatives_provider.fetch(canonical, as_of=request.as_of)
        normalized_issues = tuple(sorted(set(provider_issues)))
        availability = _context_availability(
            request,
            macro.availability,
            positioning is not None,
            normalized_issues,
        )
        context_set_id = stable_key(
            canonical,
            CONTEXT_VERSION,
            input_hash,
            derivatives.model_dump_json(),
            provider_managed_ingestion,
            normalized_issues,
        )
        if persist:
            existing = self._by_id(context_set_id)
            if existing is not None:
                return existing
        context = ContextSet(
            context_set_id=context_set_id,
            context_version=CONTEXT_VERSION,
            instrument=canonical,
            as_of=request.as_of,
            input_hash=input_hash,
            macro=macro,
            gold=gold,
            positioning=positioning,
            news=news,
            events=events,
            derivatives=derivatives,
            candidate_veto=news.candidate_veto or events.candidate_veto,
            availability=availability,
            provider_managed_ingestion=provider_managed_ingestion,
            provider_issues=normalized_issues,
        )
        if persist:
            self.store.upsert(CONTEXT_COLLECTION, context_set_id, context.model_dump(mode="json"))
        return context

    def latest(self, instrument: str) -> ContextSet | None:
        canonical = normalize_instrument(instrument).canonical
        rows = [row for row in self.store.values(CONTEXT_COLLECTION) if row.get("instrument") == canonical]
        if not rows:
            return None
        rows.sort(key=lambda row: str(row.get("as_of", "")))
        return ContextSet.model_validate(rows[-1])

    def _by_id(self, context_set_id: str) -> ContextSet | None:
        for row in self.store.values(CONTEXT_COLLECTION):
            if row.get("context_set_id") == context_set_id:
                return ContextSet.model_validate(row)
        return None


def _context_availability(
    request: ContextComputationRequest,
    macro_availability: ContextAvailability,
    has_positioning: bool,
    provider_issues: tuple[str, ...],
) -> ContextAvailability:
    has_context = bool(
        request.macro_series
        or has_positioning
        or request.news
        or request.events
        or request.gold_prices is not None
    )
    if not has_context:
        return ContextAvailability.DATA_UNAVAILABLE
    if provider_issues or macro_availability is not ContextAvailability.AVAILABLE:
        return ContextAvailability.DEGRADED
    return ContextAvailability.AVAILABLE


__all__ = ["CONTEXT_COLLECTION", "CONTEXT_VERSION", "V4ContextService"]
