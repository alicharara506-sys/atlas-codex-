"""Versioned, immutable, leakage-checked V4 feature-store service."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

from app.core.store import RecordStore, stable_key
from app.v4.contracts import (
    DataQualityStatus,
    EvidenceFamily,
    FeatureQuality,
    FeatureRecord,
    FeatureSet,
    MarketSnapshot,
    OfficialTimeframe,
    TimeframeFeatureSummary,
)
from app.v4.features import (
    AuctionMarketEngine,
    LiquidityEngine,
    MarketRegimeEngine,
    MarketStructureEngine,
    MultiTimeframeEngine,
    VolatilityEngine,
)
from app.v4.symbols import normalize_instrument

FEATURE_COLLECTION = "v4_feature_sets"
FEATURE_VERSION = "feature-foundation-v3"


class FeatureComputationError(RuntimeError):
    """Raised when features cannot be computed without violating V4 rules."""


class V4FeatureService:
    """Compute and persist deterministic Phase 2 features from one snapshot."""

    def __init__(
        self,
        store: RecordStore,
        *,
        structure_engine: MarketStructureEngine | None = None,
        volatility_engine: VolatilityEngine | None = None,
        regime_engine: MarketRegimeEngine | None = None,
        multi_timeframe_engine: MultiTimeframeEngine | None = None,
        auction_engine: AuctionMarketEngine | None = None,
        liquidity_engine: LiquidityEngine | None = None,
    ) -> None:
        self.store = store
        self.structure_engine = structure_engine or MarketStructureEngine()
        self.volatility_engine = volatility_engine or VolatilityEngine()
        self.regime_engine = regime_engine or MarketRegimeEngine()
        self.multi_timeframe_engine = multi_timeframe_engine or MultiTimeframeEngine()
        self.auction_engine = auction_engine or AuctionMarketEngine()
        self.liquidity_engine = liquidity_engine or LiquidityEngine()

    def compute(
        self,
        snapshot: MarketSnapshot,
        *,
        computed_at: datetime | None = None,
        persist: bool = True,
    ) -> FeatureSet:
        feature_set_id = stable_key(snapshot.snapshot_id, FEATURE_VERSION)
        if persist:
            existing = self._by_id(feature_set_id)
            if existing is not None:
                return existing

        input_cutoff = max(
            (bar.timestamp for bars in snapshot.timeframes.values() for bar in bars),
            default=snapshot.captured_at,
        )
        if input_cutoff > snapshot.captured_at:
            raise FeatureComputationError("feature input contains observations after the snapshot timestamp")
        quality = (
            FeatureQuality.VALID
            if snapshot.data_quality.status is DataQualityStatus.HEALTHY
            else FeatureQuality.DEGRADED
        )
        summaries: dict[OfficialTimeframe, TimeframeFeatureSummary] = {}
        records: list[FeatureRecord] = []
        for timeframe in OfficialTimeframe:
            bars = snapshot.timeframes.get(timeframe, ())
            if len(bars) < 60:
                raise FeatureComputationError(f"{timeframe.value} requires at least 60 closed bars")
            if any(current.timestamp <= previous.timestamp for previous, current in pairwise(bars)):
                raise FeatureComputationError(f"{timeframe.value} bars are not strictly chronological")
            try:
                structure = self.structure_engine.calculate(bars)
                volatility = self.volatility_engine.calculate(bars, timeframe)
                auction = self.auction_engine.calculate(bars, timeframe, snapshot.volume_provenance)
                regime = self.regime_engine.calculate(bars, structure, volatility)
            except ValueError as exc:
                raise FeatureComputationError(f"{timeframe.value} feature computation failed: {exc}") from exc
            as_of = bars[-1].timestamp
            summary = TimeframeFeatureSummary(
                timeframe=timeframe,
                as_of=as_of,
                structure=structure,
                volatility=volatility,
                auction=auction,
                regime=regime,
            )
            summaries[timeframe] = summary
            records.extend(self._records_for(snapshot, summary, quality))

        multi_timeframe = self.multi_timeframe_engine.calculate(summaries)
        liquidity = self.liquidity_engine.calculate(snapshot)
        records.append(
            FeatureRecord(
                name="multi_timeframe.alignment",
                version=self.multi_timeframe_engine.version,
                timestamp=input_cutoff,
                instrument=snapshot.instrument,
                evidence_family=EvidenceFamily.MULTI_TIMEFRAME,
                value=multi_timeframe.alignment,
                quality=quality,
                source_snapshot_id=snapshot.snapshot_id,
                input_cutoff=input_cutoff,
            )
        )
        for name, value in (
            ("liquidity.state", liquidity.state.value),
            ("liquidity.evidence_level", liquidity.evidence_level),
            ("liquidity.spread_bps", liquidity.spread_bps),
            ("liquidity.tick_frequency_hz", liquidity.tick_frequency_hz),
            ("liquidity.price_activity_bps", liquidity.price_activity_bps),
            ("liquidity.order_book_available", liquidity.order_book_available),
            ("liquidity.futures_volume_available", liquidity.futures_volume_available),
        ):
            records.append(
                FeatureRecord(
                    name=name,
                    version=self.liquidity_engine.version,
                    timestamp=input_cutoff,
                    instrument=snapshot.instrument,
                    evidence_family=EvidenceFamily.LIQUIDITY,
                    value=value,
                    quality=quality,
                    source_snapshot_id=snapshot.snapshot_id,
                    input_cutoff=input_cutoff,
                    volume_provenance=snapshot.volume_provenance,
                )
            )
        feature_set = FeatureSet(
            feature_set_id=feature_set_id,
            feature_version=FEATURE_VERSION,
            instrument=snapshot.instrument,
            source_snapshot_id=snapshot.snapshot_id,
            computed_at=(computed_at or datetime.now(UTC)).astimezone(UTC),
            input_cutoff=input_cutoff,
            input_quality=snapshot.data_quality.status,
            timeframes=summaries,
            multi_timeframe=multi_timeframe,
            liquidity=liquidity,
            records=tuple(records),
        )
        if persist:
            self.store.upsert(FEATURE_COLLECTION, feature_set_id, feature_set.model_dump(mode="json"))
        return feature_set

    def latest(self, instrument: str) -> FeatureSet | None:
        canonical = normalize_instrument(instrument).canonical
        rows = [row for row in self.store.values(FEATURE_COLLECTION) if row.get("instrument") == canonical]
        if not rows:
            return None
        rows.sort(key=lambda row: str(row.get("computed_at", "")))
        return FeatureSet.model_validate(rows[-1])

    def _by_id(self, feature_set_id: str) -> FeatureSet | None:
        for row in self.store.values(FEATURE_COLLECTION):
            if row.get("feature_set_id") == feature_set_id:
                return FeatureSet.model_validate(row)
        return None

    def _records_for(
        self,
        snapshot: MarketSnapshot,
        summary: TimeframeFeatureSummary,
        quality: FeatureQuality,
    ) -> list[FeatureRecord]:
        timeframe = summary.timeframe
        as_of = summary.as_of
        base = {
            "timestamp": as_of,
            "instrument": snapshot.instrument,
            "timeframe": timeframe,
            "quality": quality,
            "source_snapshot_id": snapshot.snapshot_id,
            "input_cutoff": as_of,
        }
        values: list[tuple[str, str, float | int | str | bool | None]] = [
            ("structure.trend", self.structure_engine.version, summary.structure.trend.value),
            ("structure.bos", self.structure_engine.version, summary.structure.bos.value),
            ("structure.choch", self.structure_engine.version, summary.structure.choch.value),
            ("structure.breakout", self.structure_engine.version, summary.structure.breakout.value),
            ("structure.retest_confirmed", self.structure_engine.version, summary.structure.retest_confirmed),
            ("structure.support", self.structure_engine.version, summary.structure.support),
            ("structure.resistance", self.structure_engine.version, summary.structure.resistance),
            ("auction.state", self.auction_engine.version, summary.auction.state.value),
            ("auction.poc", self.auction_engine.version, summary.auction.poc),
            ("auction.value_area_high", self.auction_engine.version, summary.auction.value_area_high),
            ("auction.value_area_low", self.auction_engine.version, summary.auction.value_area_low),
            ("auction.value_migration", self.auction_engine.version, summary.auction.value_migration),
            ("auction.acceptance", self.auction_engine.version, summary.auction.acceptance),
            ("auction.rejection", self.auction_engine.version, summary.auction.rejection),
            ("auction.failed_auction", self.auction_engine.version, summary.auction.failed_auction),
            ("volatility.atr14", self.volatility_engine.version, summary.volatility.atr14),
            ("volatility.atr_percent", self.volatility_engine.version, summary.volatility.atr_percent),
            (
                "volatility.realized_20",
                self.volatility_engine.version,
                summary.volatility.realized_volatility_20,
            ),
            ("volatility.rolling_60", self.volatility_engine.version, summary.volatility.rolling_volatility_60),
            ("volatility.range_percentile", self.volatility_engine.version, summary.volatility.range_percentile),
            (
                "volatility.volatility_percentile",
                self.volatility_engine.version,
                summary.volatility.volatility_percentile,
            ),
            ("volatility.state", self.volatility_engine.version, summary.volatility.state),
            ("volume.provenance", FEATURE_VERSION, snapshot.volume_provenance.value),
            ("regime.primary", self.regime_engine.version, summary.regime.primary.value),
            ("regime.adx14", self.regime_engine.version, summary.regime.adx14),
            ("regime.trend_persistence", self.regime_engine.version, summary.regime.trend_persistence),
        ]
        values.extend(
            (f"regime.probability.{state.value}", self.regime_engine.version, probability)
            for state, probability in summary.regime.probabilities.items()
        )
        return [
            FeatureRecord(
                name=name,
                version=version,
                value=value,
                evidence_family=_evidence_family(name),
                volume_provenance=(
                    snapshot.volume_provenance if name.startswith(("volume.", "auction.")) else None
                ),
                **base,
            )
            for name, version, value in values
        ]


def _evidence_family(name: str) -> EvidenceFamily:
    prefix = name.split(".", maxsplit=1)[0].upper()
    return EvidenceFamily(prefix)


__all__ = ["FEATURE_COLLECTION", "FEATURE_VERSION", "FeatureComputationError", "V4FeatureService"]
