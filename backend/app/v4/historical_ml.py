"""Leakage-safe historical feature/label construction for primary ML research."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, pstdev

from app.core.store import stable_key
from app.v4.contracts import MarketSnapshot, OfficialTimeframe, RegimeState, V4Bar
from app.v4.ml_contracts import (
    MLSample,
    MLTarget,
    ModelTrainingRequest,
    PrimaryModelArtifact,
)


class HistoricalDatasetError(ValueError):
    """Raised when live history cannot produce a governed chronological split."""


@dataclass(frozen=True, slots=True)
class HistoricalPrimaryDataset:
    request: ModelTrainingRequest
    current_features: dict[str, float]
    current_regime: RegimeState
    timeframe: OfficialTimeframe


class HistoricalPrimaryDatasetBuilder:
    """Build one timeframe dataset using past-only features and future-only labels."""

    version = "historical-primary-dataset-v1"
    feature_names = (
        "candle_body_percent",
        "price_to_sma_10",
        "range_position_10",
        "realized_volatility_10",
        "return_1",
        "return_3",
        "return_10",
        "volume_ratio_10",
    )

    def __init__(self, *, lookback: int = 20, holding_bars: int = 6) -> None:
        if lookback < 10 or holding_bars < 1:
            raise ValueError("historical dataset lookback and holding period are invalid")
        self.lookback = lookback
        self.holding_bars = holding_bars

    def build(
        self,
        snapshot: MarketSnapshot,
        timeframe: OfficialTimeframe = OfficialTimeframe.H1,
    ) -> HistoricalPrimaryDataset:
        bars = snapshot.timeframes.get(timeframe, ())
        minimum = self.lookback + self.holding_bars + 62
        if len(bars) < minimum:
            raise HistoricalDatasetError(
                f"{timeframe.value} requires at least {minimum} closed bars for model training"
            )
        samples = tuple(
            self._sample(snapshot, timeframe, bars, index)
            for index in range(self.lookback, len(bars) - self.holding_bars)
        )
        train, validation, test = self._purged_split(samples)
        if {sample.target for sample in train} != {MLTarget.LONG, MLTarget.SHORT}:
            raise HistoricalDatasetError(
                f"{timeframe.value} training history does not contain both LONG and SHORT outcomes"
            )
        current_features = self._features(bars, len(bars) - 1)
        current_regime = self._regime(bars, len(bars) - 1)
        return HistoricalPrimaryDataset(
            request=ModelTrainingRequest(train=train, validation=validation, test=test),
            current_features=current_features,
            current_regime=current_regime,
            timeframe=timeframe,
        )

    def model_probability_long(
        self,
        artifact: PrimaryModelArtifact,
        features: dict[str, float],
    ) -> float:
        """Evaluate one feature row using only persisted model parameters."""

        if tuple(sorted(self.feature_names)) != artifact.feature_schema.names:
            raise HistoricalDatasetError("model schema is not owned by the historical dataset builder")
        row = tuple(
            (features[name] - mean) / scale
            for name, mean, scale in zip(
                artifact.feature_schema.names,
                artifact.standardization_means,
                artifact.standardization_scales,
                strict=True,
            )
        )
        if artifact.logistic_intercept is not None:
            score = artifact.logistic_intercept + sum(
                weight * value
                for weight, value in zip(artifact.logistic_coefficients, row, strict=True)
            )
            return _sigmoid(score)
        return fmean(
            stump.left_probability_long
            if row[stump.feature_index] <= stump.threshold
            else stump.right_probability_long
            for stump in artifact.extra_tree_stumps
        )

    def model_returns(
        self,
        artifact: PrimaryModelArtifact,
        samples: tuple[MLSample, ...],
    ) -> tuple[float, ...]:
        """Return chronological untouched-test PnL using only artifact parameters."""

        results: list[float] = []
        for sample in samples:
            probability_long = self.model_probability_long(artifact, sample.features)
            sign = 1 if probability_long >= 0.5 else -1
            results.append(sign * sample.forward_return_net)
        return tuple(results)

    def _sample(
        self,
        snapshot: MarketSnapshot,
        timeframe: OfficialTimeframe,
        bars: tuple[V4Bar, ...],
        index: int,
    ) -> MLSample:
        observed = bars[index]
        label_bar = bars[index + self.holding_bars]
        forward_return = label_bar.close / observed.close - 1
        target = MLTarget.LONG if forward_return >= 0 else MLTarget.SHORT
        return MLSample(
            sample_id=stable_key(
                self.version,
                snapshot.instrument,
                timeframe.value,
                observed.timestamp.isoformat(),
            ),
            instrument=snapshot.instrument,
            timeframe=timeframe,
            regime=self._regime(bars, index),
            observed_at=observed.timestamp,
            label_end_at=label_bar.timestamp,
            features=self._features(bars, index),
            target=target,
            forward_return_net=forward_return,
        )

    def _purged_split(
        self,
        samples: tuple[MLSample, ...],
    ) -> tuple[tuple[MLSample, ...], tuple[MLSample, ...], tuple[MLSample, ...]]:
        count = len(samples)
        train_end = max(30, int(count * 0.58))
        validation_start = train_end + self.holding_bars
        validation_end = validation_start + max(10, int(count * 0.18))
        test_start = validation_end + self.holding_bars
        train = samples[:train_end]
        validation = samples[validation_start:validation_end]
        test = samples[test_start:]
        if len(validation) < 10 or len(test) < 30:
            raise HistoricalDatasetError("history is insufficient after chronological purge gaps")
        return train, validation, test

    def _features(self, bars: tuple[V4Bar, ...], index: int) -> dict[str, float]:
        window = bars[index - self.lookback + 1 : index + 1]
        current = window[-1]
        closes = tuple(bar.close for bar in window)
        volumes = tuple(bar.volume for bar in window)
        returns = tuple(closes[row] / closes[row - 1] - 1 for row in range(1, len(closes)))
        ten = closes[-10:]
        high_10 = max(bar.high for bar in window[-10:])
        low_10 = min(bar.low for bar in window[-10:])
        price_span = high_10 - low_10
        volume_mean = fmean(volumes[-10:]) or 1.0
        candle_range = current.high - current.low
        values = {
            "candle_body_percent": (current.close - current.open) / candle_range if candle_range else 0.0,
            "price_to_sma_10": current.close / fmean(ten) - 1,
            "range_position_10": (current.close - low_10) / price_span if price_span else 0.5,
            "realized_volatility_10": pstdev(returns[-10:]) if len(returns) >= 2 else 0.0,
            "return_1": current.close / closes[-2] - 1,
            "return_3": current.close / closes[-4] - 1,
            "return_10": current.close / closes[-11] - 1,
            "volume_ratio_10": current.volume / volume_mean,
        }
        if any(not math.isfinite(value) for value in values.values()):
            raise HistoricalDatasetError("historical feature calculation produced a non-finite value")
        return values

    def _regime(self, bars: tuple[V4Bar, ...], index: int) -> RegimeState:
        window = bars[index - self.lookback + 1 : index + 1]
        closes = tuple(bar.close for bar in window)
        returns = tuple(closes[row] / closes[row - 1] - 1 for row in range(1, len(closes)))
        recent_volatility = pstdev(returns[-10:]) if len(returns) >= 2 else 0.0
        baseline_volatility = pstdev(returns) if len(returns) >= 2 else 0.0
        if baseline_volatility > 0 and recent_volatility > baseline_volatility * 1.8:
            return RegimeState.HIGH_VOL
        trend = closes[-1] / closes[-10] - 1
        threshold = max(0.0005, recent_volatility * math.sqrt(10) * 0.75)
        if trend > threshold:
            return RegimeState.TREND_UP
        if trend < -threshold:
            return RegimeState.TREND_DOWN
        return RegimeState.RANGE


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1 / (1 + exponential)
    exponential = math.exp(value)
    return exponential / (1 + exponential)


__all__ = [
    "HistoricalDatasetError",
    "HistoricalPrimaryDataset",
    "HistoricalPrimaryDatasetBuilder",
]
