"""Validated-MT5 server tracking and signal-lineage reconstruction for V4."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel

from app.core.store import RecordStore, stable_key
from app.v4.ai_contracts import AIReviewBundle
from app.v4.ai_review import AI_REVIEW_COLLECTION
from app.v4.calibration import CALIBRATED_PROBABILITY_COLLECTION, CALIBRATION_ARTIFACT_COLLECTION
from app.v4.calibration_contracts import CalibratedProbability, CalibrationArtifact
from app.v4.context_contracts import ContextSet
from app.v4.context_service import CONTEXT_COLLECTION
from app.v4.contracts import DataQualityStatus, FeatureSet, MarketSnapshot
from app.v4.feature_service import FEATURE_COLLECTION
from app.v4.meta_contracts import MetaPrediction
from app.v4.meta_labeling import META_PREDICTION_COLLECTION
from app.v4.ml_contracts import MLTarget, PrimaryCandidate
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_backtest import BACKTEST_COLLECTION
from app.v4.quant_contracts import BacktestRun
from app.v4.quant_validation import QUANT_VALIDATION_COLLECTION
from app.v4.quant_validation_contracts import QuantValidationResult
from app.v4.risk_contracts import (
    ExpectancyReport,
    PortfolioExposureReport,
    RiskDecision,
    RiskOverlayResult,
    StructuralTargetPlan,
)
from app.v4.risk_engine import (
    EXPECTANCY_COLLECTION,
    PORTFOLIO_EXPOSURE_COLLECTION,
    RISK_OVERLAY_COLLECTION,
    STRUCTURAL_TARGET_COLLECTION,
)
from app.v4.signal_contracts import CanonicalSignal, SignalPublicationResult
from app.v4.signal_engine import (
    PUBLICATION_ATTEMPT_COLLECTION,
    SIGNAL_COLLECTION,
)
from app.v4.snapshots import SNAPSHOT_COLLECTION
from app.v4.tracking_contracts import (
    SignalLifecycleState,
    SignalLineageBundle,
    SignalTrackingControlRequest,
    SignalTrackingEvent,
    SignalTrackingRequest,
    SignalTrackingResponse,
    SignalTrackingState,
    TrackingControlAction,
    TrackingEventType,
)

TRACKING_STATE_COLLECTION = "v4_signal_tracking_states"
TRACKING_EVENT_COLLECTION = "v4_signal_tracking_events"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class SignalTrackingEngine:
    """Project append-only MT5 lifecycle events into one server-owned state."""

    version = "signal-tracking-v1"

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def track(self, signal_id: str, request: SignalTrackingRequest) -> SignalTrackingResponse:
        signal = _load(self.store, SIGNAL_COLLECTION, "signal_id", signal_id, CanonicalSignal)
        snapshot = _load(
            self.store,
            SNAPSHOT_COLLECTION,
            "snapshot_id",
            request.snapshot_id,
            MarketSnapshot,
        )
        if snapshot.instrument != signal.instrument:
            raise ValueError("tracking snapshot must match the signal instrument")
        if snapshot.captured_at < signal.created_at:
            raise ValueError("tracking cannot use a snapshot from before signal publication")
        existing = self._optional_state(signal_id)
        if existing is not None and existing.last_snapshot_id == snapshot.snapshot_id:
            return SignalTrackingResponse(state=existing, new_events=(), reused_snapshot=True)
        if existing is not None and existing.terminal:
            return SignalTrackingResponse(state=existing, new_events=(), reused_snapshot=False)

        state, events = self._base(signal, snapshot, existing)
        if not _data_is_valid(snapshot):
            state, event = self._terminal(
                state,
                snapshot,
                SignalLifecycleState.DATA_CANCELLED,
                TrackingEventType.DATA_CANCELLED,
                "Validated MT5 data became degraded or stale",
            )
            events.append(event)
        elif request.current_context_set_id is not None and self._event_blocked(
            signal, request.current_context_set_id
        ):
            state, event = self._terminal(
                state,
                snapshot,
                SignalLifecycleState.EVENT_CANCELLED,
                TrackingEventType.EVENT_CANCELLED,
                "Current event/news context vetoed the signal",
            )
            events.append(event)
        elif request.current_risk_overlay_id is not None and self._risk_blocked(
            signal, request.current_risk_overlay_id
        ):
            state, event = self._terminal(
                state,
                snapshot,
                SignalLifecycleState.RISK_CANCELLED,
                TrackingEventType.RISK_CANCELLED,
                "Current hard-risk overlay no longer passes",
            )
            events.append(event)
        elif snapshot.captured_at >= signal.expires_at:
            state, event = self._terminal(
                state,
                snapshot,
                SignalLifecycleState.EXPIRED,
                TrackingEventType.EXPIRED,
                "Signal validity window expired",
            )
            events.append(event)
        else:
            state, outcome_events = self._advance_market_state(signal, snapshot, state)
            events.extend(outcome_events)

        self.store.upsert(
            TRACKING_STATE_COLLECTION,
            state.tracking_id,
            state.model_dump(mode="json"),
        )
        for event in events:
            self.store.upsert(
                TRACKING_EVENT_COLLECTION,
                event.event_id,
                event.model_dump(mode="json"),
            )
        return SignalTrackingResponse(state=state, new_events=tuple(events), reused_snapshot=False)

    def control(
        self,
        signal_id: str,
        request: SignalTrackingControlRequest,
    ) -> SignalTrackingResponse:
        signal = _load(self.store, SIGNAL_COLLECTION, "signal_id", signal_id, CanonicalSignal)
        state = self._optional_state(signal_id)
        if state is None:
            state = _initial_state(signal)
        if state.terminal:
            return SignalTrackingResponse(state=state, new_events=(), reused_snapshot=False)
        now = datetime.now(UTC)
        lifecycle = (
            SignalLifecycleState.INVALIDATED
            if request.action is TrackingControlAction.INVALIDATE
            else SignalLifecycleState.CANCELLED
        )
        event_type = (
            TrackingEventType.INVALIDATED
            if request.action is TrackingControlAction.INVALIDATE
            else TrackingEventType.CANCELLED
        )
        state = state.model_copy(
            update={
                "state": lifecycle,
                "terminal": True,
                "terminal_at": now,
                "last_updated_at": now,
                "elapsed_seconds": max(0.0, (now - state.started_at).total_seconds()),
                "event_sequence": state.event_sequence + 1,
            }
        )
        event = _event(
            state,
            event_type,
            now,
            request.reason,
            actor=request.actor,
        )
        self.store.upsert(
            TRACKING_STATE_COLLECTION,
            state.tracking_id,
            state.model_dump(mode="json"),
        )
        self.store.upsert(
            TRACKING_EVENT_COLLECTION,
            event.event_id,
            event.model_dump(mode="json"),
        )
        return SignalTrackingResponse(state=state, new_events=(event,), reused_snapshot=False)

    def state(self, signal_id: str) -> SignalTrackingState:
        _load(self.store, SIGNAL_COLLECTION, "signal_id", signal_id, CanonicalSignal)
        state = self._optional_state(signal_id)
        if state is None:
            raise KeyError(f"SignalTrackingState not found: {signal_id}")
        return state

    def events(self, signal_id: str) -> tuple[SignalTrackingEvent, ...]:
        _load(self.store, SIGNAL_COLLECTION, "signal_id", signal_id, CanonicalSignal)
        values = tuple(
            SignalTrackingEvent.model_validate(row)
            for row in self.store.values(TRACKING_EVENT_COLLECTION)
            if row.get("signal_id") == signal_id
        )
        return tuple(sorted(values, key=lambda item: item.sequence))

    def _base(
        self,
        signal: CanonicalSignal,
        snapshot: MarketSnapshot,
        existing: SignalTrackingState | None,
    ) -> tuple[SignalTrackingState, list[SignalTrackingEvent]]:
        if existing is not None:
            return existing, []
        state = _initial_state(signal).model_copy(
            update={
                "last_snapshot_id": snapshot.snapshot_id,
                "last_updated_at": snapshot.captured_at,
                "live_price": _entry_side_price(signal, snapshot),
                "spread": snapshot.spread,
                "pip_size": _pip_size(snapshot),
                "distance_to_stop": abs(_entry_side_price(signal, snapshot) - signal.stop_loss),
                "distance_to_next_target": abs(
                    signal.targets[0].price - _entry_side_price(signal, snapshot)
                ),
                "elapsed_seconds": max(0.0, (snapshot.captured_at - signal.created_at).total_seconds()),
                "event_sequence": 1,
            }
        )
        event = _event(
            state,
            TrackingEventType.PENDING_ENTRY,
            signal.created_at,
            "Canonical signal entered server-side pending tracking",
            snapshot=snapshot,
        )
        return state, [event]

    def _advance_market_state(
        self,
        signal: CanonicalSignal,
        snapshot: MarketSnapshot,
        state: SignalTrackingState,
    ) -> tuple[SignalTrackingState, list[SignalTrackingEvent]]:
        events: list[SignalTrackingEvent] = []
        entry_price = _entry_side_price(signal, snapshot)
        if state.state is SignalLifecycleState.PENDING_ENTRY:
            if signal.entry.low <= entry_price <= signal.entry.high:
                state = state.model_copy(
                    update={
                        "state": SignalLifecycleState.ACTIVE,
                        "activated_at": snapshot.captured_at,
                        "entry_fill": entry_price,
                        "event_sequence": state.event_sequence + 1,
                    }
                )
                events.append(
                    _event(
                        state,
                        TrackingEventType.ACTIVE,
                        snapshot.captured_at,
                        "Validated executable-side MT5 price entered the signal zone",
                        snapshot=snapshot,
                        price=entry_price,
                    )
                )
            else:
                return _mark_to_market(signal, snapshot, state), events
        state = _mark_to_market(signal, snapshot, state)
        exit_price = _exit_side_price(signal, snapshot)
        stop_hit = (
            exit_price <= signal.stop_loss
            if signal.direction is MLTarget.LONG
            else exit_price >= signal.stop_loss
        )
        if stop_hit:
            stopped_state, event = self._terminal(
                state,
                snapshot,
                SignalLifecycleState.STOPPED,
                TrackingEventType.STOPPED,
                "Validated executable-side MT5 price reached the structural stop",
                price=exit_price,
            )
            events.append(event)
            return stopped_state, events
        highest = state.highest_target_hit
        for index, target in enumerate(signal.targets, start=1):
            if index <= highest:
                continue
            hit = (
                exit_price >= target.price
                if signal.direction is MLTarget.LONG
                else exit_price <= target.price
            )
            if not hit:
                break
            lifecycle = (
                SignalLifecycleState.TP1_HIT,
                SignalLifecycleState.TP2_HIT,
                SignalLifecycleState.TP3_HIT,
            )[index - 1]
            event_type = (
                TrackingEventType.TP1_HIT,
                TrackingEventType.TP2_HIT,
                TrackingEventType.TP3_HIT,
            )[index - 1]
            terminal_hit = index == 3
            next_target = signal.targets[index] if index < len(signal.targets) else None
            state = state.model_copy(
                update={
                    "state": lifecycle,
                    "highest_target_hit": index,
                    "terminal": terminal_hit,
                    "terminal_at": snapshot.captured_at if terminal_hit else None,
                    "distance_to_next_target": (
                        abs(exit_price - next_target.price) if next_target is not None else None
                    ),
                    "event_sequence": state.event_sequence + 1,
                }
            )
            events.append(
                _event(
                    state,
                    event_type,
                    snapshot.captured_at,
                    f"Validated MT5 price reached {target.name}",
                    snapshot=snapshot,
                    price=exit_price,
                )
            )
        return state, events

    def _terminal(
        self,
        state: SignalTrackingState,
        snapshot: MarketSnapshot,
        lifecycle: SignalLifecycleState,
        event_type: TrackingEventType,
        reason: str,
        *,
        price: float | None = None,
    ) -> tuple[SignalTrackingState, SignalTrackingEvent]:
        terminal = state.model_copy(
            update={
                "state": lifecycle,
                "terminal": True,
                "terminal_at": snapshot.captured_at,
                "last_snapshot_id": snapshot.snapshot_id,
                "last_updated_at": snapshot.captured_at,
                "live_price": price or state.live_price,
                "spread": snapshot.spread,
                "elapsed_seconds": max(0.0, (snapshot.captured_at - state.started_at).total_seconds()),
                "event_sequence": state.event_sequence + 1,
            }
        )
        return terminal, _event(
            terminal,
            event_type,
            snapshot.captured_at,
            reason,
            snapshot=snapshot,
            price=price,
        )

    def _event_blocked(self, signal: CanonicalSignal, context_id: str) -> bool:
        context = _load(self.store, CONTEXT_COLLECTION, "context_set_id", context_id, ContextSet)
        if context.instrument != signal.instrument:
            raise ValueError("current context must match the signal instrument")
        return context.candidate_veto

    def _risk_blocked(self, signal: CanonicalSignal, risk_id: str) -> bool:
        risk = _load(
            self.store,
            RISK_OVERLAY_COLLECTION,
            "risk_overlay_id",
            risk_id,
            RiskOverlayResult,
        )
        if risk.instrument != signal.instrument:
            raise ValueError("current risk overlay must match the signal instrument")
        return risk.decision is not RiskDecision.PASS

    def _optional_state(self, signal_id: str) -> SignalTrackingState | None:
        row = next(
            (
                item
                for item in self.store.values(TRACKING_STATE_COLLECTION)
                if item.get("signal_id") == signal_id
            ),
            None,
        )
        return SignalTrackingState.model_validate(row) if row is not None else None


class SignalLineageService:
    """Reconstruct every persisted artifact referenced by a canonical signal."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def reconstruct(self, signal_id: str) -> SignalLineageBundle:
        signal = _load(self.store, SIGNAL_COLLECTION, "signal_id", signal_id, CanonicalSignal)
        lineage = signal.lineage
        publication_row = next(
            (
                row
                for row in self.store.values(PUBLICATION_ATTEMPT_COLLECTION)
                if isinstance(row.get("signal"), dict)
                and row["signal"].get("signal_id") == signal_id
            ),
            None,
        )
        if publication_row is None:
            raise KeyError(f"SignalPublicationResult not found for signal: {signal_id}")
        ai_review = (
            _load(
                self.store,
                AI_REVIEW_COLLECTION,
                "bundle_id",
                lineage.ai_review_bundle_id,
                AIReviewBundle,
            )
            if lineage.ai_review_bundle_id is not None
            else None
        )
        return SignalLineageBundle(
            signal=signal,
            publication=SignalPublicationResult.model_validate(publication_row),
            market_snapshot=_load(
                self.store,
                SNAPSHOT_COLLECTION,
                "snapshot_id",
                lineage.market_snapshot_id,
                MarketSnapshot,
            ),
            features=_load(
                self.store,
                FEATURE_COLLECTION,
                "feature_set_id",
                lineage.feature_set_id,
                FeatureSet,
            ),
            context=_load(
                self.store,
                CONTEXT_COLLECTION,
                "context_set_id",
                lineage.context_set_id,
                ContextSet,
            ),
            primary_candidate=_load(
                self.store,
                PRIMARY_CANDIDATE_COLLECTION,
                "candidate_id",
                lineage.candidate_id,
                PrimaryCandidate,
            ),
            ai_review=ai_review,
            meta_prediction=_load(
                self.store,
                META_PREDICTION_COLLECTION,
                "meta_prediction_id",
                lineage.meta_prediction_id,
                MetaPrediction,
            ),
            calibration=_load(
                self.store,
                CALIBRATION_ARTIFACT_COLLECTION,
                "calibration_id",
                lineage.calibration_id,
                CalibrationArtifact,
            ),
            calibrated_probability=_load(
                self.store,
                CALIBRATED_PROBABILITY_COLLECTION,
                "calibrated_probability_id",
                lineage.calibrated_probability_id,
                CalibratedProbability,
            ),
            quant_validation=_load(
                self.store,
                QUANT_VALIDATION_COLLECTION,
                "quant_validation_id",
                lineage.quant_validation_id,
                QuantValidationResult,
            ),
            backtest=_load(
                self.store,
                BACKTEST_COLLECTION,
                "backtest_run_id",
                lineage.backtest_run_id,
                BacktestRun,
            ),
            expectancy=_load(
                self.store,
                EXPECTANCY_COLLECTION,
                "expectancy_id",
                lineage.expectancy_id,
                ExpectancyReport,
            ),
            structural_targets=_load(
                self.store,
                STRUCTURAL_TARGET_COLLECTION,
                "target_plan_id",
                lineage.target_plan_id,
                StructuralTargetPlan,
            ),
            portfolio_exposure=_load(
                self.store,
                PORTFOLIO_EXPOSURE_COLLECTION,
                "exposure_id",
                lineage.exposure_id,
                PortfolioExposureReport,
            ),
            risk_overlay=_load(
                self.store,
                RISK_OVERLAY_COLLECTION,
                "risk_overlay_id",
                lineage.risk_overlay_id,
                RiskOverlayResult,
            ),
        )


def _initial_state(signal: CanonicalSignal) -> SignalTrackingState:
    return SignalTrackingState(
        tracking_id=stable_key("signal-tracking-v1", signal.signal_id),
        version="signal-tracking-v1",
        signal_id=signal.signal_id,
        instrument=signal.instrument,
        state=SignalLifecycleState.PENDING_ENTRY,
        started_at=signal.created_at,
        last_updated_at=signal.created_at,
        elapsed_seconds=0,
        event_sequence=0,
        terminal=False,
    )


def _mark_to_market(
    signal: CanonicalSignal,
    snapshot: MarketSnapshot,
    state: SignalTrackingState,
) -> SignalTrackingState:
    price = _exit_side_price(signal, snapshot) if state.entry_fill is not None else _entry_side_price(signal, snapshot)
    pip_size = state.pip_size or _pip_size(snapshot)
    favorable = 0.0
    live_r = None
    live_pips = None
    if state.entry_fill is not None:
        favorable = (
            price - state.entry_fill
            if signal.direction is MLTarget.LONG
            else state.entry_fill - price
        )
        risk_distance = abs(state.entry_fill - signal.stop_loss)
        live_r = favorable / risk_distance if risk_distance > 0 else None
        live_pips = favorable / pip_size if pip_size is not None else None
    next_target = next(
        (target for index, target in enumerate(signal.targets, start=1) if index > state.highest_target_hit),
        None,
    )
    return state.model_copy(
        update={
            "live_price": price,
            "live_r": live_r,
            "live_pips": live_pips,
            "mfe_price": max(state.mfe_price, max(0.0, favorable)),
            "mae_price": max(state.mae_price, max(0.0, -favorable)),
            "distance_to_stop": abs(price - signal.stop_loss),
            "distance_to_next_target": abs(price - next_target.price) if next_target else None,
            "elapsed_seconds": max(0.0, (snapshot.captured_at - state.started_at).total_seconds()),
            "spread": snapshot.spread,
            "pip_size": pip_size,
            "last_snapshot_id": snapshot.snapshot_id,
            "last_updated_at": snapshot.captured_at,
        }
    )


def _entry_side_price(signal: CanonicalSignal, snapshot: MarketSnapshot) -> float:
    return snapshot.ask if signal.direction is MLTarget.LONG else snapshot.bid


def _exit_side_price(signal: CanonicalSignal, snapshot: MarketSnapshot) -> float:
    return snapshot.bid if signal.direction is MLTarget.LONG else snapshot.ask


def _pip_size(snapshot: MarketSnapshot) -> float | None:
    point = snapshot.symbol_metadata.point
    digits = snapshot.symbol_metadata.digits
    if point is None:
        return None
    return point * 10 if digits in {3, 5} else point


def _data_is_valid(snapshot: MarketSnapshot) -> bool:
    return (
        snapshot.data_quality.status is DataQualityStatus.HEALTHY
        and snapshot.data_quality.publication_eligible
        and snapshot.health.data_fresh
    )


def _event(
    state: SignalTrackingState,
    event_type: TrackingEventType,
    occurred_at: datetime,
    reason: str,
    *,
    snapshot: MarketSnapshot | None = None,
    price: float | None = None,
    actor: str = "ATLAS_SERVER",
) -> SignalTrackingEvent:
    event_id = stable_key(
        "signal-tracking-event-v1",
        state.signal_id,
        state.event_sequence,
        event_type.value,
        snapshot.snapshot_id if snapshot else None,
        occurred_at.isoformat(),
    )
    return SignalTrackingEvent(
        event_id=event_id,
        signal_id=state.signal_id,
        sequence=state.event_sequence,
        event_type=event_type,
        occurred_at=occurred_at,
        market_snapshot_id=snapshot.snapshot_id if snapshot else None,
        price=price,
        live_r=state.live_r,
        reason=reason,
        actor=actor,
    )


def _load(  # noqa: UP047 -- keep Python 3.10-compatible TypeVar syntax
    store: RecordStore,
    collection: str,
    key_name: str,
    value: str,
    model: type[_ModelT],
) -> _ModelT:
    row = next((item for item in store.values(collection) if item.get(key_name) == value), None)
    if row is None:
        raise KeyError(f"{model.__name__} not found: {value}")
    return model.model_validate(row)


__all__ = [
    "TRACKING_EVENT_COLLECTION",
    "TRACKING_STATE_COLLECTION",
    "SignalLineageService",
    "SignalTrackingEngine",
]
