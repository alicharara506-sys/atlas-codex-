"""Pooled, chronological meta-label research derived from primary OOS predictions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import fmean

from pydantic import ValidationError

from app.core.store import RecordStore, stable_key
from app.v4.ai_contracts import AIReviewBundle
from app.v4.context_contracts import ContextSet
from app.v4.contracts import FeatureSet, LiquidityState, MarketSnapshot, OfficialTimeframe, RegimeState
from app.v4.historical_ml import HistoricalPrimaryDataset, HistoricalPrimaryDatasetBuilder
from app.v4.labeling import TripleBarrierLabeler
from app.v4.meta_contracts import (
    EvidenceFamily,
    EvidenceObservation,
    MetaFeatureVector,
    MetaLabelSample,
    MetaSession,
    MetaTarget,
    MetaTrainingConfig,
    MetaTrainingResult,
)
from app.v4.meta_labeling import EvidenceDependencyEngine, MetaLabelTrainer
from app.v4.ml_contracts import MLTarget, PrimaryCandidate, PrimaryModelArtifact
from app.v4.primary_ml import MODEL_ARTIFACT_COLLECTION, PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_contracts import (
    BacktestTradePlan,
    ResearchDirection,
    TripleBarrierConfig,
    TripleBarrierLabel,
    TripleBarrierRecord,
    WalkForwardConfig,
)

HISTORICAL_META_SAMPLE_COLLECTION = "v4_historical_meta_samples"

_TIMEFRAME_CODE = {
    OfficialTimeframe.M15: 0.0,
    OfficialTimeframe.H1: 1 / 3,
    OfficialTimeframe.H4: 2 / 3,
    OfficialTimeframe.D1: 1.0,
}


@dataclass(frozen=True, slots=True)
class HistoricalMetaBuildResult:
    sample_ids: tuple[str, ...]
    sample_count: int
    accept_count: int
    reject_count: int


class HistoricalMetaResearchService:
    """Build meta labels from untouched primary test rows without publication authority."""

    version = "historical-meta-research-v2"
    # Pooled rows contain several assets and timeframes at the same timestamp.
    # Windows therefore need enough chronological breadth for the six-bar D1
    # label horizon to clear before validation and test observations begin.
    minimum_training_samples = 1_980
    maximum_training_samples = 2_400

    def __init__(
        self,
        store: RecordStore,
        dataset_builder: HistoricalPrimaryDatasetBuilder,
        evidence: EvidenceDependencyEngine,
        trainer: MetaLabelTrainer,
    ) -> None:
        self.store = store
        self.dataset_builder = dataset_builder
        self.evidence = evidence
        self.trainer = trainer
        self.labeler = TripleBarrierLabeler()

    def add_primary_oos_samples(
        self,
        snapshot: MarketSnapshot,
        dataset: HistoricalPrimaryDataset,
        artifact: PrimaryModelArtifact,
        peer_artifacts: tuple[PrimaryModelArtifact, ...],
    ) -> HistoricalMetaBuildResult:
        """Persist triple-barrier labels for one pre-declared primary OOS slice."""

        bars = snapshot.timeframes[dataset.timeframe]
        config = TripleBarrierConfig(
            max_holding_bars=self.dataset_builder.holding_bars,
            target_atr_multiple=1.0,
            stop_atr_multiple=1.0,
            use_structure=False,
        )
        labels = {
            ResearchDirection.LONG: {
                row.signal_at: row
                for row in self.labeler.label(
                    snapshot.instrument,
                    bars,
                    ResearchDirection.LONG,
                    config,
                ).labels
            },
            ResearchDirection.SHORT: {
                row.signal_at: row
                for row in self.labeler.label(
                    snapshot.instrument,
                    bars,
                    ResearchDirection.SHORT,
                    config,
                ).labels
            },
        }
        history = dataset.request.train + dataset.request.validation + dataset.request.test
        volatilities = tuple(row.features["realized_volatility_10"] for row in history)
        sample_ids: list[str] = []
        accept_count = 0
        for sample in dataset.request.test:
            probability_long = self.dataset_builder.model_probability_long(artifact, sample.features)
            direction = MLTarget.LONG if probability_long >= 0.5 else MLTarget.SHORT
            direction_probability = probability_long if direction is MLTarget.LONG else 1 - probability_long
            candidate = self._historical_candidate(
                artifact,
                sample.instrument,
                sample.timeframe,
                sample.regime,
                sample.observed_at,
                sample.features,
                probability_long,
                direction_probability,
                direction,
            )
            research_direction = ResearchDirection.LONG if direction is MLTarget.LONG else ResearchDirection.SHORT
            barrier = labels[research_direction].get(sample.observed_at)
            if barrier is None:
                continue
            report = self.evidence.analyze(self._observations(snapshot, artifact, sample.sample_id, sample.features))
            peer_probabilities = tuple(
                self.dataset_builder.model_probability_long(peer, sample.features) for peer in peer_artifacts
            )
            disagreement = max(peer_probabilities) - min(peer_probabilities) if len(peer_probabilities) > 1 else 0.0
            current_volatility = sample.features["realized_volatility_10"]
            available_volatility = tuple(
                value for row, value in zip(history, volatilities, strict=True) if row.observed_at <= sample.observed_at
            )
            volatility_percentile = sum(value <= current_volatility for value in available_volatility) / len(
                available_volatility
            )
            tp_before_sl = barrier.label in {
                TripleBarrierLabel.LONG_WIN,
                TripleBarrierLabel.SHORT_WIN,
            }
            directional_return = sample.forward_return_net if direction is MLTarget.LONG else -sample.forward_return_net
            net_return = self._barrier_return(
                barrier,
                tp_before_sl,
                directional_return,
                snapshot.spread_bps,
            )
            features = MetaFeatureVector(
                raw_primary_probability=direction_probability,
                model_disagreement=disagreement,
                regime=sample.regime,
                volatility_percentile=volatility_percentile,
                spread_bps=snapshot.spread_bps,
                session=_session(sample.observed_at.hour),
                event_risk=0.0,
                macro_state=0.0,
                mtf_conflict=0.0,
                data_quality_score=snapshot.data_quality.score,
                ai_contradiction_ratio=0.0,
                liquidity_score=min(1.0, max(0.0, sample.features["volume_ratio_10"] / 2)),
                timeframe_code=_TIMEFRAME_CODE[sample.timeframe],
                evidence_dependency_report_id=report.report_id,
            )
            sample_id = stable_key(
                self.version,
                candidate.candidate_id,
                barrier.label_id,
                features.model_dump(mode="json"),
            )
            meta_sample = MetaLabelSample(
                sample_id=sample_id,
                candidate_id=candidate.candidate_id,
                observed_at=sample.observed_at,
                label_end_at=barrier.resolved_at,
                primary_direction=direction,
                features=features,
                triple_barrier_label_id=barrier.label_id,
                tp_before_sl=tp_before_sl,
                target=MetaTarget.ACCEPT if tp_before_sl else MetaTarget.REJECT,
                forward_return_net=net_return,
            )
            self.store.upsert(
                HISTORICAL_META_SAMPLE_COLLECTION,
                sample_id,
                meta_sample.model_dump(mode="json"),
            )
            sample_ids.append(sample_id)
            accept_count += int(tp_before_sl)
        return HistoricalMetaBuildResult(
            sample_ids=tuple(sample_ids),
            sample_count=len(sample_ids),
            accept_count=accept_count,
            reject_count=len(sample_ids) - accept_count,
        )

    def train_pooled(
        self,
        timeframe: OfficialTimeframe | None = None,
    ) -> MetaTrainingResult:
        """Train a cross-asset meta model from unique chronological OOS research.

        Production uses one pooled-across-instruments model per official
        timeframe. Mixing timeframe calendars in one chronological walk-forward
        makes the most recent validation tail collapse to the fastest timeframe.
        The optional ``None`` scope remains available for aggregate research.
        """

        rows: list[MetaLabelSample] = []
        for payload in self.store.values(HISTORICAL_META_SAMPLE_COLLECTION):
            try:
                rows.append(MetaLabelSample.model_validate(payload))
            except ValidationError:
                continue
        candidates: dict[str, PrimaryCandidate] = {}
        for payload in self.store.values(PRIMARY_CANDIDATE_COLLECTION):
            try:
                candidate = PrimaryCandidate.model_validate(payload)
            except ValidationError:
                continue
            candidates[candidate.candidate_id] = candidate
        artifacts: dict[str, PrimaryModelArtifact] = {}
        for payload in self.store.values(MODEL_ARTIFACT_COLLECTION):
            try:
                artifact = PrimaryModelArtifact.model_validate(payload)
            except ValidationError:
                continue
            artifacts[artifact.model_id] = artifact
        rows = self._deduplicate_point_in_time_rows(rows, candidates, artifacts)
        if timeframe is not None:
            rows = [row for row in rows if candidates[row.candidate_id].timeframe is timeframe]
        rows.sort(key=lambda row: (row.observed_at, row.sample_id))
        minimum_samples = 500 if timeframe is not None else self.minimum_training_samples
        if len(rows) < minimum_samples:
            scope = timeframe.value if timeframe is not None else "global"
            raise ValueError(
                f"{scope} pooled meta training requires {minimum_samples} unique primary OOS "
                f"labels; {len(rows)} are available"
            )
        if {row.target for row in rows} != set(MetaTarget):
            raise ValueError("pooled meta training requires both ACCEPT and REJECT outcomes")
        if timeframe is None:
            rows = rows[-self.maximum_training_samples :]
            walk_forward = WalkForwardConfig(
                train_size=1_200,
                validation_size=300,
                test_size=300,
                step_size=300,
                embargo_size=90,
            )
        else:
            walk_forward = WalkForwardConfig(
                train_size=200,
                validation_size=50,
                test_size=50,
                step_size=50,
                embargo_size=10,
            )
        config = MetaTrainingConfig(walk_forward=walk_forward, epochs=200)
        return self.trainer.train(tuple(rows), config)

    @staticmethod
    def _deduplicate_point_in_time_rows(
        rows: list[MetaLabelSample],
        candidates: dict[str, PrimaryCandidate],
        artifacts: dict[str, PrimaryModelArtifact],
    ) -> list[MetaLabelSample]:
        """Keep the earliest truly OOS forecast for each market decision.

        Repeated orchestration cycles retrain primary models over overlapping
        windows.  The same historical timestamp can therefore be persisted
        several times under different model IDs.  Treating those copies as
        independent observations overweights a small set of outcomes and lets
        later-trained models leak into earlier timestamps.  The earliest model
        artifact is the point-in-time-safe representative.
        """

        selected: dict[
            tuple[str, OfficialTimeframe, datetime, MLTarget],
            tuple[datetime, str, MetaLabelSample],
        ] = {}
        for row in rows:
            candidate = candidates.get(row.candidate_id)
            if candidate is None:
                continue
            artifact = artifacts.get(candidate.model_id)
            if artifact is None:
                continue
            key = (
                candidate.instrument,
                candidate.timeframe,
                row.observed_at,
                row.primary_direction,
            )
            rank = (artifact.created_at, row.sample_id)
            current = selected.get(key)
            if current is None or rank < current[:2]:
                selected[key] = (rank[0], rank[1], row)
        return [value[2] for value in selected.values()]

    def backtest_plans(
        self,
        snapshot: MarketSnapshot,
        dataset: HistoricalPrimaryDataset,
        artifact: PrimaryModelArtifact,
    ) -> tuple[BacktestTradePlan, ...]:
        """Create cost-model-ready plans from the same untouched primary test slice."""

        bars = snapshot.timeframes[dataset.timeframe]
        config = TripleBarrierConfig(
            max_holding_bars=self.dataset_builder.holding_bars,
            target_atr_multiple=1.0,
            stop_atr_multiple=1.0,
            use_structure=False,
        )
        label_maps = {
            direction: {
                row.signal_at: row for row in self.labeler.label(snapshot.instrument, bars, direction, config).labels
            }
            for direction in ResearchDirection
        }
        plans: list[BacktestTradePlan] = []
        for sample in dataset.request.test:
            probability_long = self.dataset_builder.model_probability_long(artifact, sample.features)
            direction = ResearchDirection.LONG if probability_long >= 0.5 else ResearchDirection.SHORT
            barrier = label_maps[direction].get(sample.observed_at)
            if barrier is None:
                continue
            plans.append(
                BacktestTradePlan(
                    trade_id=stable_key(
                        self.version,
                        "backtest-plan",
                        artifact.model_id,
                        sample.sample_id,
                    ),
                    instrument=snapshot.instrument,
                    direction=direction,
                    signal_at=sample.observed_at,
                    stop_loss=(barrier.lower_barrier if direction is ResearchDirection.LONG else barrier.upper_barrier),
                    take_profit=(
                        barrier.upper_barrier if direction is ResearchDirection.LONG else barrier.lower_barrier
                    ),
                    max_holding_bars=self.dataset_builder.holding_bars,
                )
            )
        return tuple(plans)

    def samples(self) -> tuple[MetaLabelSample, ...]:
        rows = tuple(
            MetaLabelSample.model_validate(row) for row in self.store.values(HISTORICAL_META_SAMPLE_COLLECTION)
        )
        return tuple(sorted(rows, key=lambda row: (row.observed_at, row.sample_id)))

    def current_features(
        self,
        candidate: PrimaryCandidate,
        snapshot: MarketSnapshot,
        feature_set: FeatureSet,
        context: ContextSet,
        ai_bundle: AIReviewBundle,
    ) -> MetaFeatureVector:
        """Construct one current meta vector from persisted, same-cycle evidence."""

        summary = feature_set.timeframes[candidate.timeframe]
        observations = (
            EvidenceObservation(
                evidence_id=stable_key(candidate.candidate_id, "current-trend"),
                name="current_trend_persistence",
                family=EvidenceFamily.TREND,
                value=summary.regime.trend_persistence,
                upstream_ids=(feature_set.feature_set_id,),
            ),
            EvidenceObservation(
                evidence_id=stable_key(candidate.candidate_id, "current-volatility"),
                name="current_volatility_percentile",
                family=EvidenceFamily.VOLATILITY,
                value=summary.volatility.volatility_percentile,
                upstream_ids=(feature_set.feature_set_id,),
            ),
            EvidenceObservation(
                evidence_id=stable_key(candidate.candidate_id, "current-liquidity"),
                name="current_broker_spread",
                family=EvidenceFamily.LIQUIDITY,
                value=snapshot.spread_bps,
                upstream_ids=(snapshot.snapshot_id,),
            ),
            EvidenceObservation(
                evidence_id=stable_key(candidate.candidate_id, "current-event"),
                name="current_event_veto",
                family=EvidenceFamily.EVENT,
                value=float(context.events.candidate_veto),
                upstream_ids=(context.context_set_id,),
            ),
        )
        dependency = self.evidence.analyze(observations)
        contradiction_count = sum(len(review.payload.contradictions) for review in ai_bundle.reviews)
        support_count = sum(len(review.payload.supports) for review in ai_bundle.reviews)
        review_count = contradiction_count + support_count
        contradiction_ratio = contradiction_count / review_count if review_count else 0.0
        macro_changes = tuple(factor.change_1 for factor in context.macro.factors.values())
        macro_state = 0.0
        if macro_changes:
            macro_state = max(-1.0, min(1.0, fmean(_sign(value) for value in macro_changes)))
        positioning = context.positioning
        liquidity_score = {
            LiquidityState.ADEQUATE: 1.0,
            LiquidityState.THIN: 0.5,
            LiquidityState.STRESSED: 0.0,
            LiquidityState.UNAVAILABLE: None,
        }[feature_set.liquidity.state]
        return MetaFeatureVector(
            raw_primary_probability=candidate.raw_direction_probability,
            model_disagreement=0.0,
            regime=candidate.regime,
            volatility_percentile=summary.volatility.volatility_percentile,
            spread_bps=snapshot.spread_bps,
            session=_session(candidate.observed_at.hour),
            event_risk=float(context.candidate_veto),
            macro_state=macro_state,
            mtf_conflict=float(feature_set.multi_timeframe.alignment in {"MIXED", "CONTRADICTORY", "UNCERTAIN"}),
            data_quality_score=snapshot.data_quality.score,
            ai_contradiction_ratio=contradiction_ratio,
            positioning_zscore=(positioning.z_score if positioning is not None else None),
            liquidity_score=liquidity_score,
            timeframe_code=_TIMEFRAME_CODE[candidate.timeframe],
            evidence_dependency_report_id=dependency.report_id,
            ai_review_bundle_id=ai_bundle.bundle_id,
        )

    def _historical_candidate(
        self,
        artifact: PrimaryModelArtifact,
        instrument: str,
        timeframe: OfficialTimeframe,
        regime: RegimeState,
        observed_at: datetime,
        raw_features: dict[str, float],
        probability_long: float,
        direction_probability: float,
        direction: MLTarget,
    ) -> PrimaryCandidate:
        candidate_id = stable_key(
            artifact.model_id,
            instrument.upper(),
            timeframe.value,
            regime.value,
            observed_at.isoformat(),
            raw_features,
        )
        candidate = PrimaryCandidate(
            candidate_id=candidate_id,
            model_id=artifact.model_id,
            instrument=instrument.upper(),
            timeframe=timeframe,
            regime=regime,
            observed_at=observed_at,
            direction=direction,
            raw_probability_long=probability_long,
            raw_direction_probability=direction_probability,
        )
        self.store.upsert(
            PRIMARY_CANDIDATE_COLLECTION,
            candidate_id,
            candidate.model_dump(mode="json"),
        )
        return candidate

    @staticmethod
    def _observations(
        snapshot: MarketSnapshot,
        artifact: PrimaryModelArtifact,
        sample_id: str,
        features: dict[str, float],
    ) -> tuple[EvidenceObservation, ...]:
        return (
            EvidenceObservation(
                evidence_id=stable_key(sample_id, "trend"),
                name="primary_trend_return",
                family=EvidenceFamily.TREND,
                value=features["return_10"],
                upstream_ids=(artifact.model_id, sample_id),
            ),
            EvidenceObservation(
                evidence_id=stable_key(sample_id, "volatility"),
                name="historical_realized_volatility",
                family=EvidenceFamily.VOLATILITY,
                value=features["realized_volatility_10"],
                upstream_ids=(sample_id,),
            ),
            EvidenceObservation(
                evidence_id=stable_key(sample_id, "liquidity"),
                name="broker_spread_cost_proxy",
                family=EvidenceFamily.LIQUIDITY,
                value=snapshot.spread_bps,
                upstream_ids=(snapshot.snapshot_id,),
            ),
        )

    @staticmethod
    def _barrier_return(
        barrier: TripleBarrierRecord,
        tp_before_sl: bool,
        timeout_return: float,
        spread_bps: float,
    ) -> float:
        cost = spread_bps / 10_000
        if barrier.label is TripleBarrierLabel.TIMEOUT:
            return timeout_return - cost
        if barrier.direction is ResearchDirection.LONG:
            gross = (
                (barrier.upper_barrier - barrier.entry) / barrier.entry
                if tp_before_sl
                else -(barrier.entry - barrier.lower_barrier) / barrier.entry
            )
        else:
            gross = (
                (barrier.entry - barrier.lower_barrier) / barrier.entry
                if tp_before_sl
                else -(barrier.upper_barrier - barrier.entry) / barrier.entry
            )
        return gross - cost


def _session(hour: int) -> MetaSession:
    if 12 <= hour < 16:
        return MetaSession.OVERLAP
    if 7 <= hour < 16:
        return MetaSession.LONDON
    if 13 <= hour < 22:
        return MetaSession.NEW_YORK
    if 0 <= hour < 9:
        return MetaSession.ASIA
    return MetaSession.OFF_HOURS


def _sign(value: float) -> float:
    if value > 0:
        return 1.0
    if value < 0:
        return -1.0
    return 0.0


__all__ = [
    "HISTORICAL_META_SAMPLE_COLLECTION",
    "HistoricalMetaBuildResult",
    "HistoricalMetaResearchService",
]
