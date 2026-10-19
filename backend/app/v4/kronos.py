"""Kronos shadow forecasting, leakage checks, and scenario summarization."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise
from statistics import fmean, median, pstdev
from typing import Protocol

from app.core.store import RecordStore, stable_key
from app.v4.contracts import DataQualityStatus, MarketSnapshot, OfficialTimeframe, V4Bar
from app.v4.kronos_contracts import (
    KronosBarrierGeometry,
    KronosForecast,
    KronosForecastBundle,
    KronosForecastStatus,
    KronosLeakageAudit,
    KronosMode,
    KronosStatus,
)

KRONOS_FORECAST_COLLECTION = "v4_kronos_forecasts"
KRONOS_BUNDLE_COLLECTION = "v4_kronos_forecast_bundles"


class KronosPathProvider(Protocol):
    """Provider boundary; Kronos never receives signal-publication authority."""

    model_version: str
    provider_preprocessing_verified: bool
    pretraining_cutoff_verified: bool

    def availability(self) -> tuple[bool, str | None]: ...

    def generate_paths(
        self,
        bars: tuple[V4Bar, ...],
        timeframe: OfficialTimeframe,
        *,
        horizon_bars: int,
        sample_count: int,
        seed: int,
    ) -> tuple[tuple[float, ...], ...]: ...


class UnavailableKronosProvider:
    """Truthful default used until an official predictor runtime is configured."""

    model_version = "kronos-base"
    provider_preprocessing_verified = False
    pretraining_cutoff_verified = False

    def __init__(self, reason: str = "Official Kronos predictor runtime is not configured") -> None:
        self.reason = reason

    def availability(self) -> tuple[bool, str | None]:
        return False, self.reason

    def generate_paths(
        self,
        bars: tuple[V4Bar, ...],
        timeframe: OfficialTimeframe,
        *,
        horizon_bars: int,
        sample_count: int,
        seed: int,
    ) -> tuple[tuple[float, ...], ...]:
        del bars, timeframe, horizon_bars, sample_count, seed
        raise RuntimeError(self.reason)


class KronosShadowEngine:
    """Persist probabilistic price-path evidence without changing Atlas decisions."""

    version = "kronos-shadow-engine-v1"
    minimum_lookback = 64
    max_context = 512

    def __init__(
        self,
        store: RecordStore,
        provider: KronosPathProvider,
        *,
        enabled: bool,
        mode: KronosMode = KronosMode.SHADOW,
        sample_count: int = 200,
        horizon_bars: int = 12,
        max_weight: float = 0.25,
        effective_weight: float = 0,
        seed: int = 17,
    ) -> None:
        if sample_count not in {200, 500, 1_000}:
            raise ValueError("Kronos sample count must be 200, 500, or 1000")
        if not 1 <= horizon_bars <= 512:
            raise ValueError("Kronos horizon must be between 1 and 512 bars")
        if not 0 <= max_weight <= 0.25:
            raise ValueError("Kronos max weight must be between zero and 0.25")
        if not 0 <= effective_weight <= max_weight:
            raise ValueError("Kronos effective weight exceeds its configured cap")
        if mode is KronosMode.SHADOW and effective_weight != 0:
            raise ValueError("Kronos shadow mode requires zero effective weight")
        self.store = store
        self.provider = provider
        self.enabled = enabled
        self.mode = mode
        self.sample_count = sample_count
        self.horizon_bars = horizon_bars
        self.max_weight = max_weight
        self.effective_weight = effective_weight
        self.seed = seed

    def status(self) -> KronosStatus:
        available, reason = self.provider.availability()
        return KronosStatus(
            enabled=self.enabled,
            mode=self.mode,
            provider_available=available,
            provider_reason=reason,
            model_version=self.provider.model_version,
            configured_sample_count=self.sample_count,
            configured_horizon_bars=self.horizon_bars,
            effective_weight=self.effective_weight,
            max_weight=self.max_weight,
            pretraining_cutoff_verified=self.provider.pretraining_cutoff_verified,
        )

    def forecast_all(
        self,
        snapshot: MarketSnapshot,
        *,
        barriers: dict[OfficialTimeframe, KronosBarrierGeometry] | None = None,
        persist: bool = True,
        now: datetime | None = None,
    ) -> KronosForecastBundle:
        inferred_at = _utc(now or datetime.now(UTC))
        forecasts = tuple(
            self.forecast(
                snapshot,
                timeframe,
                barrier=(barriers or {}).get(timeframe),
                persist=persist,
                now=inferred_at,
            )
            for timeframe in OfficialTimeframe
        )
        bundle_id = stable_key(
            self.version,
            snapshot.snapshot_id,
            [forecast.forecast_id for forecast in forecasts],
            self.mode.value,
        )
        bundle = KronosForecastBundle(
            bundle_id=bundle_id,
            snapshot_id=snapshot.snapshot_id,
            instrument=snapshot.instrument,
            mode=self.mode,
            forecasts=forecasts,
            available_forecasts=sum(
                forecast.status is KronosForecastStatus.AVAILABLE for forecast in forecasts
            ),
            created_at=inferred_at,
            effective_weight=self.effective_weight,
        )
        if persist:
            self.store.upsert(KRONOS_BUNDLE_COLLECTION, bundle_id, bundle.model_dump(mode="json"))
        return bundle

    def forecast(
        self,
        snapshot: MarketSnapshot,
        timeframe: OfficialTimeframe,
        *,
        barrier: KronosBarrierGeometry | None = None,
        persist: bool = True,
        now: datetime | None = None,
    ) -> KronosForecast:
        inferred_at = _utc(now or datetime.now(UTC))
        bars = snapshot.timeframes.get(timeframe, ())
        audit = self.audit(snapshot, timeframe)
        status = KronosForecastStatus.AVAILABLE
        reason: str | None = None
        paths: tuple[tuple[float, ...], ...] = ()
        if not self.enabled:
            status = KronosForecastStatus.DISABLED
            reason = "Kronos is disabled by configuration"
        elif snapshot.data_quality.status is not DataQualityStatus.HEALTHY:
            status = KronosForecastStatus.DATA_BLOCKED
            reason = f"Snapshot data quality is {snapshot.data_quality.status.value}"
        elif not audit.passed:
            status = KronosForecastStatus.DATA_BLOCKED
            reason = "; ".join(audit.issues)
        else:
            available, provider_reason = self.provider.availability()
            if not available:
                status = KronosForecastStatus.PROVIDER_UNAVAILABLE
                reason = provider_reason or "Kronos provider is unavailable"
            else:
                try:
                    paths = self.provider.generate_paths(
                        bars[-self.max_context :],
                        timeframe,
                        horizon_bars=self.horizon_bars,
                        sample_count=self.sample_count,
                        seed=self.seed,
                    )
                    _validate_paths(paths, self.sample_count, self.horizon_bars)
                except Exception as exc:  # noqa: BLE001 - provider failures remain evidence, not crashes
                    status = KronosForecastStatus.PROVIDER_FAILED
                    reason = f"{type(exc).__name__}: {exc}"

        metrics = (
            _summarize_paths(paths, bars[-1].close, barrier)
            if status is KronosForecastStatus.AVAILABLE
            else {}
        )
        stability = self._historical_stability(snapshot.instrument, timeframe, metrics.get("expected_return"))
        forecast_id = stable_key(
            self.version,
            snapshot.snapshot_id,
            timeframe.value,
            self.provider.model_version,
            self.sample_count,
            self.horizon_bars,
            self.seed,
            barrier.model_dump(mode="json") if barrier else None,
        )
        forecast = KronosForecast(
            forecast_id=forecast_id,
            snapshot_id=snapshot.snapshot_id,
            instrument=snapshot.instrument,
            timeframe=timeframe,
            status=status,
            mode=self.mode,
            model_version=self.provider.model_version,
            sample_count=len(paths),
            horizon_bars=self.horizon_bars,
            forecast_stability=stability,
            effective_weight=self.effective_weight,
            max_weight=self.max_weight,
            leakage_audit=audit,
            data_timestamp=bars[-1].timestamp if bars else None,
            inference_timestamp=inferred_at,
            failure_reason=reason,
            pretraining_cutoff_verified=self.provider.pretraining_cutoff_verified,
            incremental_value_verified=False,
            **metrics,
        )
        if persist:
            self.store.upsert(KRONOS_FORECAST_COLLECTION, forecast_id, forecast.model_dump(mode="json"))
        return forecast

    def latest(self, instrument: str, timeframe: OfficialTimeframe) -> KronosForecast | None:
        rows: list[KronosForecast] = []
        for payload in self.store.values(KRONOS_FORECAST_COLLECTION):
            try:
                forecast = KronosForecast.model_validate(payload)
            except ValueError:
                continue
            if forecast.instrument == instrument and forecast.timeframe is timeframe:
                rows.append(forecast)
        return max(rows, key=lambda row: (row.inference_timestamp, row.forecast_id), default=None)

    def audit(self, snapshot: MarketSnapshot, timeframe: OfficialTimeframe) -> KronosLeakageAudit:
        bars = snapshot.timeframes.get(timeframe, ())
        issues: list[str] = []
        timestamps = tuple(bar.timestamp for bar in bars)
        duplicate = len(set(timestamps)) != len(timestamps)
        non_chronological = any(current <= previous for previous, current in pairwise(timestamps))
        future = any(timestamp > snapshot.captured_at for timestamp in timestamps)
        if len(bars) < self.minimum_lookback:
            issues.append(f"Kronos requires at least {self.minimum_lookback} closed bars")
        if duplicate:
            issues.append("Duplicate input timestamp detected")
        if non_chronological:
            issues.append("Kronos input bars are not strictly chronological")
        if future:
            issues.append("Kronos input contains a bar after the snapshot cutoff")
        provider_available, _ = self.provider.availability()
        if provider_available and not self.provider.provider_preprocessing_verified:
            issues.append("Provider normalization and preprocessing have not passed the Atlas leakage audit")
        passed = not issues
        return KronosLeakageAudit(
            passed=passed,
            input_cutoff=max(timestamps) if timestamps else None,
            forecast_origin=snapshot.captured_at,
            future_bar_detected=future,
            non_chronological_input=non_chronological,
            duplicate_timestamp_detected=duplicate,
            provider_preprocessing_verified=self.provider.provider_preprocessing_verified,
            issues=tuple(issues),
            quant_veto_required=not passed,
        )

    def _historical_stability(
        self,
        instrument: str,
        timeframe: OfficialTimeframe,
        expected_return: float | None,
    ) -> float | None:
        if expected_return is None:
            return None
        history: list[float] = []
        for payload in self.store.values(KRONOS_FORECAST_COLLECTION):
            try:
                row = KronosForecast.model_validate(payload)
            except ValueError:
                continue
            if (
                row.instrument == instrument
                and row.timeframe is timeframe
                and row.status is KronosForecastStatus.AVAILABLE
                and row.expected_return is not None
            ):
                history.append(row.expected_return)
        if len(history) < 3:
            return None
        recent = history[-9:] + [expected_return]
        direction = 1 if expected_return >= 0 else -1
        agreement = sum((1 if value >= 0 else -1) == direction for value in recent) / len(recent)
        scale = max(abs(fmean(recent)), 1e-9)
        dispersion_penalty = min(1.0, pstdev(recent) / scale)
        return max(0.0, min(1.0, agreement * (1 - 0.5 * dispersion_penalty)))


def _validate_paths(
    paths: tuple[tuple[float, ...], ...],
    sample_count: int,
    horizon_bars: int,
) -> None:
    if len(paths) != sample_count:
        raise ValueError(f"Kronos returned {len(paths)} paths; expected {sample_count}")
    for path in paths:
        if len(path) != horizon_bars:
            raise ValueError("Kronos returned a path with the wrong forecast horizon")
        if any(not math.isfinite(value) or value <= 0 for value in path):
            raise ValueError("Kronos returned a non-finite or non-positive price")


def _summarize_paths(
    paths: tuple[tuple[float, ...], ...],
    origin: float,
    barrier: KronosBarrierGeometry | None,
) -> dict[str, float | None]:
    terminal = tuple(path[-1] / origin - 1 for path in paths)
    path_returns = tuple(tuple(price / origin - 1 for price in path) for path in paths)
    expected_return = fmean(terminal)
    volatility = pstdev(terminal)
    positive = sum(value > 0 for value in terminal) / len(terminal)
    negative = 1.0 - positive
    entropy = _binary_entropy(positive)
    upside_threshold = max(0.0, expected_return) + volatility
    downside_threshold = min(0.0, expected_return) - volatility
    result: dict[str, float | None] = {
        "expected_return": expected_return,
        "p_positive_return": positive,
        "p_negative_return": negative,
        "expected_volatility": volatility,
        "return_q05": _quantile(terminal, 0.05),
        "return_q25": _quantile(terminal, 0.25),
        "return_q50": _quantile(terminal, 0.50),
        "return_q75": _quantile(terminal, 0.75),
        "return_q95": _quantile(terminal, 0.95),
        "forecast_dispersion": _quantile(terminal, 0.95) - _quantile(terminal, 0.05),
        "forecast_entropy": entropy,
        "upside_tail_probability": sum(value >= upside_threshold for value in terminal) / len(terminal),
        "downside_tail_probability": sum(value <= downside_threshold for value in terminal) / len(terminal),
        "expected_max_favorable_excursion": fmean(max(path) for path in path_returns),
        "expected_max_adverse_excursion": fmean(min(path) for path in path_returns),
    }
    if barrier is not None:
        tp_first = 0
        sl_first = 0
        neither = 0
        time_to_target: list[int] = []
        for path in path_returns:
            tp_index = next(
                (index for index, value in enumerate(path, start=1) if value >= barrier.take_profit_return),
                None,
            )
            sl_index = next(
                (index for index, value in enumerate(path, start=1) if value <= barrier.stop_loss_return),
                None,
            )
            if tp_index is not None and (sl_index is None or tp_index < sl_index):
                tp_first += 1
                time_to_target.append(tp_index)
            elif sl_index is not None:
                sl_first += 1
            else:
                neither += 1
        count = len(paths)
        result.update(
            {
                "tp_first_probability": tp_first / count,
                "sl_first_probability": sl_first / count,
                "neither_probability": neither / count,
                "median_time_to_target_bars": median(time_to_target) if time_to_target else None,
            }
        )
    return result


def _binary_entropy(probability: float) -> float:
    if probability in {0.0, 1.0}:
        return 0.0
    complement = 1 - probability
    return -(probability * math.log(probability) + complement * math.log(complement)) / math.log(2)


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Kronos timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "KRONOS_BUNDLE_COLLECTION",
    "KRONOS_FORECAST_COLLECTION",
    "KronosPathProvider",
    "KronosShadowEngine",
    "UnavailableKronosProvider",
]
