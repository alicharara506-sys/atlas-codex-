"""Build the ATLAS V4 terminal read model from persisted authoritative records."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from threading import RLock
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.core.store import RecordStore
from app.v4.ai_contracts import AIReviewBundle
from app.v4.ai_review import AI_REVIEW_COLLECTION
from app.v4.context_contracts import ContextSet
from app.v4.context_service import CONTEXT_COLLECTION
from app.v4.contracts import FeatureSet, MarketSnapshot, OfficialTimeframe
from app.v4.feature_service import FEATURE_COLLECTION
from app.v4.ml_contracts import ModelStatusRecord, PrimaryModelArtifact
from app.v4.performance import V4PerformanceService
from app.v4.presentation_contracts import (
    TerminalAIReviewSummary,
    TerminalCalendarEvent,
    TerminalCounts,
    TerminalDecisionSummary,
    TerminalMarketSummary,
    TerminalModelSummary,
    TerminalSignalSummary,
    TerminalTimeframeSummary,
    V4TerminalWorkspace,
)
from app.v4.primary_ml import MODEL_ARTIFACT_COLLECTION, MODEL_STATUS_COLLECTION
from app.v4.signal_contracts import CanonicalSignal, PublicationDecision, SignalPublicationResult, SignalRevision
from app.v4.signal_engine import PUBLICATION_ATTEMPT_COLLECTION, SIGNAL_COLLECTION, SIGNAL_REVISION_COLLECTION
from app.v4.snapshots import SNAPSHOT_COLLECTION
from app.v4.symbols import OFFICIAL_TIMEFRAMES, approved_universe, normalize_instrument
from app.v4.tracking_contracts import SignalLifecycleState, SignalTrackingEvent, SignalTrackingState
from app.v4.tracking_engine import TRACKING_EVENT_COLLECTION, TRACKING_STATE_COLLECTION


class V4PresentationService:
    """Expose one truthful browser read model without moving authority client-side."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        self._workspace_cache: dict[str, tuple[float, V4TerminalWorkspace]] = {}
        self._workspace_cache_lock = RLock()
        self._workspace_cache_ttl_seconds = 60.0

    def workspace(self, selected_instrument: str = "XAUUSD") -> V4TerminalWorkspace:
        selected = normalize_instrument(selected_instrument).canonical
        with self._workspace_cache_lock:
            cached = self._workspace_cache.get(selected)
            now = monotonic()
            if cached is not None and now - cached[0] < self._workspace_cache_ttl_seconds:
                return cached[1]
            workspace = self._build_workspace(selected)
            self._workspace_cache[selected] = (monotonic(), workspace)
            return workspace

    def _build_workspace(self, selected_instrument: str) -> V4TerminalWorkspace:
        selected = normalize_instrument(selected_instrument).canonical
        issues: list[str] = []
        snapshots = _validated_recent(
            self.store,
            SNAPSHOT_COLLECTION,
            MarketSnapshot,
            issues,
            limit=128,
        )
        features = _validated_recent(
            self.store,
            FEATURE_COLLECTION,
            FeatureSet,
            issues,
            limit=128,
        )
        contexts = _validated_recent(
            self.store,
            CONTEXT_COLLECTION,
            ContextSet,
            issues,
            limit=128,
        )
        signals = _validated_recent(
            self.store,
            SIGNAL_COLLECTION,
            CanonicalSignal,
            issues,
            limit=128,
        )
        decisions = _validated_recent(
            self.store,
            PUBLICATION_ATTEMPT_COLLECTION,
            SignalPublicationResult,
            issues,
            limit=256,
        )
        revisions = _validated_recent(
            self.store,
            SIGNAL_REVISION_COLLECTION,
            SignalRevision,
            issues,
            limit=256,
        )
        tracking = _validated_recent(
            self.store,
            TRACKING_STATE_COLLECTION,
            SignalTrackingState,
            issues,
            limit=256,
        )
        tracking_events = _validated_recent(
            self.store,
            TRACKING_EVENT_COLLECTION,
            SignalTrackingEvent,
            issues,
            limit=512,
        )
        models = _validated_recent(
            self.store,
            MODEL_ARTIFACT_COLLECTION,
            PrimaryModelArtifact,
            issues,
            limit=32,
        )
        model_statuses = _validated_recent(
            self.store,
            MODEL_STATUS_COLLECTION,
            ModelStatusRecord,
            issues,
            limit=256,
        )
        ai_reviews = _validated_recent(
            self.store,
            AI_REVIEW_COLLECTION,
            AIReviewBundle,
            issues,
            limit=64,
        )

        snapshot_by_instrument = _latest_by(snapshots, "instrument", "captured_at")
        feature_by_instrument = _latest_by(features, "instrument", "computed_at")
        context_by_instrument = _latest_by(contexts, "instrument", "as_of")
        decision_by_instrument = _latest_by(decisions, "instrument", "created_at")
        signal_by_instrument = _latest_by(signals, "instrument", "created_at")
        tracking_by_signal = _latest_by(tracking, "signal_id", "last_updated_at")
        event_counts = Counter(event.signal_id for event in tracking_events)
        revision_counts = Counter(revision.signal_id for revision in revisions)

        markets = tuple(
            self._market_summary(
                instrument,
                snapshot_by_instrument.get(instrument),
                feature_by_instrument.get(instrument),
                context_by_instrument.get(instrument),
                decision_by_instrument.get(instrument),
                signal_by_instrument.get(instrument),
                tracking_by_signal,
            )
            for instrument in approved_universe()
        )
        signal_summaries = tuple(
            TerminalSignalSummary(
                signal=signal,
                tracking=tracking_by_signal.get(signal.signal_id),
                event_count=event_counts[signal.signal_id],
                revision_count=revision_counts[signal.signal_id],
            )
            for signal in sorted(signals, key=lambda row: row.created_at, reverse=True)
        )
        decision_summaries = tuple(
            TerminalDecisionSummary(
                publication_attempt_id=row.publication_attempt_id,
                created_at=row.created_at,
                instrument=row.instrument,
                candidate_id=row.candidate_id,
                decision=row.decision,
                atlas_probability=row.atlas_probability,
                grade=row.grade,
                reasons=row.reasons,
                signal_id=row.signal.signal_id if row.signal is not None else None,
            )
            for row in sorted(decisions, key=lambda item: item.created_at, reverse=True)
        )
        calendar = self._calendar(contexts, signals)
        model_summaries = self._models(models, model_statuses)
        ai_summaries = self._ai_reviews(ai_reviews)
        performance = V4PerformanceService(self.store).dashboard()
        issues.extend(performance.source_issues)
        counts = _counts(markets, signal_summaries, decision_summaries)
        return V4TerminalWorkspace(
            generated_at=datetime.now(UTC),
            selected_instrument=selected,
            markets=markets,
            signals=signal_summaries,
            decisions=decision_summaries,
            calendar=calendar,
            models=model_summaries,
            ai_reviews=ai_summaries,
            performance=performance,
            counts=counts,
            source_issues=tuple(sorted(set(issues))),
            metrics_status=performance.status,
        )

    @staticmethod
    def _market_summary(
        instrument: str,
        snapshot: MarketSnapshot | None,
        features: FeatureSet | None,
        context: ContextSet | None,
        decision: SignalPublicationResult | None,
        signal: CanonicalSignal | None,
        tracking_by_signal: dict[str, SignalTrackingState],
    ) -> TerminalMarketSummary:
        definition = normalize_instrument(instrument)
        tracking = tracking_by_signal.get(signal.signal_id) if signal is not None else None
        timeframes = tuple(
            _timeframe_summary(timeframe, snapshot, features, context, decision, signal)
            for timeframe in OFFICIAL_TIMEFRAMES
        )
        availability: Literal["AVAILABLE", "DEGRADED", "DATA_UNAVAILABLE"]
        if snapshot is None:
            availability = "DATA_UNAVAILABLE"
        elif snapshot.data_quality.publication_eligible:
            availability = "AVAILABLE"
        else:
            availability = "DEGRADED"
        probability = (
            signal.calibrated_probability
            if signal is not None
            else (decision.atlas_probability if decision is not None else None)
        )
        event_risk = (
            signal.event_risk.value
            if signal is not None
            else (context.events.risk.value if context is not None else None)
        )
        return TerminalMarketSummary(
            instrument=instrument,
            asset_class=definition.asset_class,
            bid=snapshot.bid if snapshot is not None else None,
            ask=snapshot.ask if snapshot is not None else None,
            price=((snapshot.bid + snapshot.ask) / 2) if snapshot is not None else None,
            spread=snapshot.spread if snapshot is not None else None,
            spread_bps=snapshot.spread_bps if snapshot is not None else None,
            captured_at=snapshot.captured_at if snapshot is not None else None,
            session=snapshot.session if snapshot is not None else None,
            broker=snapshot.broker if snapshot is not None else None,
            server=snapshot.server if snapshot is not None else None,
            account_mode=snapshot.account_mode if snapshot is not None else None,
            last_tick_at=snapshot.health.last_tick_at if snapshot is not None else None,
            tick_age_seconds=snapshot.health.tick_age_seconds if snapshot is not None else None,
            tick_count=snapshot.tick_count if snapshot is not None else None,
            volume_provenance=snapshot.volume_provenance.value if snapshot is not None else None,
            mt5_status=snapshot.health.state if snapshot is not None else None,
            data_quality=snapshot.data_quality.status if snapshot is not None else None,
            data_quality_score=snapshot.data_quality.score if snapshot is not None else None,
            data_issues=(
                tuple(issue.code for issue in snapshot.data_quality.issues)
                if snapshot is not None
                else ("NO_PERSISTED_MT5_SNAPSHOT",)
            ),
            liquidity=features.liquidity.state.value if features is not None else None,
            mtf_alignment=features.multi_timeframe.alignment if features is not None else None,
            best_timeframe=signal.timeframe if signal is not None else None,
            calibrated_probability=probability,
            grade=signal.grade if signal is not None else (decision.grade if decision is not None else None),
            expected_value_net=signal.expected_value_net if signal is not None else None,
            regime=signal.regime.value if signal is not None else None,
            event_risk=event_risk,
            quant_status=(signal.quant_validation.status if signal is not None else _quant_status(decision)),
            decision=decision.decision if decision is not None else None,
            decision_reasons=decision.reasons if decision is not None else (),
            signal_id=signal.signal_id if signal is not None else None,
            signal_direction=signal.direction.value if signal is not None else None,
            tracking_state=tracking.state.value if tracking is not None else None,
            timeframes=timeframes,
            availability=availability,
        )

    @staticmethod
    def _calendar(
        contexts: list[ContextSet],
        signals: list[CanonicalSignal],
    ) -> tuple[TerminalCalendarEvent, ...]:
        latest_contexts = _latest_by(contexts, "instrument", "as_of")
        rows: list[TerminalCalendarEvent] = []
        for instrument, context in latest_contexts.items():
            affected = tuple(
                signal.signal_id
                for signal in signals
                if signal.instrument == instrument and signal.expires_at >= context.as_of
            )
            rows.extend(
                TerminalCalendarEvent(
                    event_id=event.event_id,
                    instrument=instrument,
                    name=event.name,
                    currency=event.currency,
                    scheduled_at=event.scheduled_at,
                    minutes_to_event=event.minutes_to_event,
                    time_precision=event.time_precision,
                    risk=event.risk.value,
                    candidate_veto=event.candidate_veto,
                    affected_signal_ids=affected if event.affects_instrument else (),
                )
                for event in context.events.assessments
            )
        return tuple(sorted(rows, key=lambda row: row.scheduled_at))

    @staticmethod
    def _models(
        models: list[PrimaryModelArtifact],
        statuses: list[ModelStatusRecord],
    ) -> tuple[TerminalModelSummary, ...]:
        latest_status = _latest_by(statuses, "model_id", "changed_at")
        return tuple(
            TerminalModelSummary(
                model_id=model.model_id,
                model_version=model.model_version,
                family=model.family.value,
                status=(
                    latest_status[model.model_id].status.value
                    if model.model_id in latest_status
                    else model.initial_status.value
                ),
                created_at=model.created_at,
                supported_instruments=model.supported_instruments,
                supported_timeframes=model.supported_timeframes,
                test_sample_count=model.test_metrics.sample_count,
                test_brier_score=model.test_metrics.brier_score,
                test_mean_net_return=model.test_metrics.mean_net_return,
            )
            for model in sorted(models, key=lambda row: row.created_at, reverse=True)
        )

    @staticmethod
    def _ai_reviews(reviews: list[AIReviewBundle]) -> tuple[TerminalAIReviewSummary, ...]:
        rows: list[TerminalAIReviewSummary] = []
        for bundle in sorted(reviews, key=lambda row: row.candidate.observed_at, reverse=True):
            contradictions = tuple(item for review in bundle.reviews for item in review.payload.contradictions)
            invalidations = tuple(item for review in bundle.reviews for item in review.payload.invalidations)
            rows.append(
                TerminalAIReviewSummary(
                    bundle_id=bundle.bundle_id,
                    candidate_id=bundle.candidate.candidate_id,
                    instrument=bundle.candidate.instrument,
                    status=bundle.status.value,
                    specialist_statuses={
                        review.role.value: review.payload.review_status.value for review in bundle.reviews
                    },
                    contradictions=contradictions,
                    invalidations=invalidations,
                    total_latency_ms=bundle.total_latency_ms,
                    input_tokens=bundle.input_tokens,
                    output_tokens=bundle.output_tokens,
                )
            )
        return tuple(rows)


def _timeframe_summary(
    timeframe: OfficialTimeframe,
    snapshot: MarketSnapshot | None,
    features: FeatureSet | None,
    context: ContextSet | None,
    decision: SignalPublicationResult | None,
    signal: CanonicalSignal | None,
) -> TerminalTimeframeSummary:
    bars = snapshot.timeframes.get(timeframe, ()) if snapshot is not None else ()
    feature = features.timeframes.get(timeframe) if features is not None else None
    selected = signal is not None and signal.timeframe is timeframe
    return TerminalTimeframeSummary(
        timeframe=timeframe,
        last_close=bars[-1].close if bars else None,
        last_bar_at=bars[-1].timestamp if bars else None,
        trend=feature.structure.trend.value if feature is not None else None,
        structure=(
            f"BOS_{feature.structure.bos.value}/CHOCH_{feature.structure.choch.value}" if feature is not None else None
        ),
        regime=feature.regime.primary.value if feature is not None else None,
        volatility=feature.volatility.state if feature is not None else None,
        raw_probability=signal.raw_probability if signal is not None and selected else None,
        meta_probability=signal.meta_probability if signal is not None and selected else None,
        calibrated_probability=(signal.calibrated_probability if signal is not None and selected else None),
        event_risk=context.events.risk.value if context is not None else None,
        quant_status=signal.quant_validation.status if signal is not None and selected else None,
        selected=selected,
        availability="AVAILABLE" if bars and feature is not None else "DATA_UNAVAILABLE",
    )


def _quant_status(decision: SignalPublicationResult | None) -> str | None:
    if decision is None:
        return None
    if decision.decision is PublicationDecision.QUANT_VETO:
        return "VETO"
    if decision.decision is PublicationDecision.INSUFFICIENT_DATA:
        return "INSUFFICIENT_EVIDENCE"
    if decision.signal is not None:
        return decision.signal.quant_validation.status
    return None


def _counts(
    markets: tuple[TerminalMarketSummary, ...],
    signals: tuple[TerminalSignalSummary, ...],
    decisions: tuple[TerminalDecisionSummary, ...],
) -> TerminalCounts:
    states = Counter(row.tracking.state for row in signals if row.tracking is not None)
    latest_decisions = {market.instrument: market.decision for market in markets}
    return TerminalCounts(
        published_signals=len(signals),
        active_signals=states[SignalLifecycleState.ACTIVE]
        + states[SignalLifecycleState.TP1_HIT]
        + states[SignalLifecycleState.TP2_HIT],
        pending_signals=states[SignalLifecycleState.PENDING_ENTRY],
        wait_markets=sum(decision is PublicationDecision.WAIT for decision in latest_decisions.values()),
        quant_vetoes=sum(row.decision is PublicationDecision.QUANT_VETO for row in decisions),
        risk_vetoes=sum(row.decision is PublicationDecision.RISK_VETO for row in decisions),
        event_blocks=sum(row.decision is PublicationDecision.EVENT_BLOCK for row in decisions),
        degraded_markets=sum(row.availability != "AVAILABLE" for row in markets),
    )


def _validated[ModelT: BaseModel](
    store: RecordStore,
    collection: str,
    model: type[ModelT],
    issues: list[str],
) -> list[ModelT]:
    rows: list[ModelT] = []
    for index, payload in enumerate(store.values(collection)):
        try:
            rows.append(model.model_validate(payload))
        except ValidationError:
            issues.append(f"INVALID_{collection.upper()}_ROW_{index}")
    return rows


def _validated_recent[ModelT: BaseModel](
    store: RecordStore,
    collection: str,
    model: type[ModelT],
    issues: list[str],
    *,
    limit: int,
) -> list[ModelT]:
    rows: list[ModelT] = []
    for index, payload in enumerate(store.recent_values(collection, limit)):
        try:
            rows.append(model.model_validate(payload))
        except ValidationError:
            issues.append(f"INVALID_{collection.upper()}_ROW_{index}")
    return rows


def _latest_by[ModelT: BaseModel](rows: list[ModelT], key_name: str, time_name: str) -> dict[str, ModelT]:
    result: dict[str, ModelT] = {}
    for row in rows:
        key = str(getattr(row, key_name))
        current = result.get(key)
        if current is None or getattr(row, time_name) > getattr(current, time_name):
            result[key] = row
    return result


__all__ = ["V4PresentationService"]
