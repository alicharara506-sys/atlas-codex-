"""Typed, non-authoritative contracts for the ATLAS Kronos shadow specialist."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from app.v4.contracts import FrozenModel, OfficialTimeframe


class KronosMode(str, Enum):
    SHADOW = "SHADOW"
    PRODUCTION = "PRODUCTION"


class KronosForecastStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    DISABLED = "DISABLED"
    DATA_BLOCKED = "DATA_BLOCKED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_FAILED = "PROVIDER_FAILED"


class KronosBarrierGeometry(FrozenModel):
    """Return-space barriers used only to evaluate already-proposed geometry."""

    take_profit_return: float = Field(gt=0)
    stop_loss_return: float = Field(lt=0)


class KronosLeakageAudit(FrozenModel):
    passed: bool
    input_cutoff: datetime | None = None
    forecast_origin: datetime | None = None
    future_bar_detected: bool = False
    non_chronological_input: bool = False
    duplicate_timestamp_detected: bool = False
    provider_preprocessing_verified: bool = False
    issues: tuple[str, ...] = ()
    quant_veto_required: bool


class KronosForecast(FrozenModel):
    forecast_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)
    instrument: str = Field(min_length=3, max_length=16)
    timeframe: OfficialTimeframe
    status: KronosForecastStatus
    mode: KronosMode
    model_version: str = Field(min_length=1, max_length=200)
    sample_count: int = Field(ge=0, le=1_000)
    horizon_bars: int = Field(ge=1, le=512)
    expected_return: float | None = None
    p_positive_return: float | None = Field(default=None, ge=0, le=1)
    p_negative_return: float | None = Field(default=None, ge=0, le=1)
    expected_volatility: float | None = Field(default=None, ge=0)
    return_q05: float | None = None
    return_q25: float | None = None
    return_q50: float | None = None
    return_q75: float | None = None
    return_q95: float | None = None
    forecast_dispersion: float | None = Field(default=None, ge=0)
    forecast_entropy: float | None = Field(default=None, ge=0, le=1)
    forecast_stability: float | None = Field(default=None, ge=0, le=1)
    upside_tail_probability: float | None = Field(default=None, ge=0, le=1)
    downside_tail_probability: float | None = Field(default=None, ge=0, le=1)
    tp_first_probability: float | None = Field(default=None, ge=0, le=1)
    sl_first_probability: float | None = Field(default=None, ge=0, le=1)
    neither_probability: float | None = Field(default=None, ge=0, le=1)
    expected_max_favorable_excursion: float | None = None
    expected_max_adverse_excursion: float | None = None
    median_time_to_target_bars: float | None = Field(default=None, ge=1)
    effective_weight: float = Field(ge=0, le=0.25)
    max_weight: float = Field(ge=0, le=0.25)
    leakage_audit: KronosLeakageAudit
    data_timestamp: datetime | None = None
    inference_timestamp: datetime
    failure_reason: str | None = Field(default=None, max_length=1_000)
    pretraining_cutoff_verified: bool = False
    incremental_value_verified: bool = False
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence(self) -> KronosForecast:
        if self.mode is KronosMode.SHADOW and self.effective_weight != 0:
            raise ValueError("Kronos shadow forecasts must have zero effective weight")
        if self.effective_weight > self.max_weight:
            raise ValueError("Kronos effective weight cannot exceed its configured cap")
        if self.status is KronosForecastStatus.AVAILABLE:
            required = (
                self.expected_return,
                self.p_positive_return,
                self.p_negative_return,
                self.expected_volatility,
                self.return_q05,
                self.return_q25,
                self.return_q50,
                self.return_q75,
                self.return_q95,
                self.forecast_dispersion,
                self.forecast_entropy,
                self.upside_tail_probability,
                self.downside_tail_probability,
            )
            if any(value is None for value in required):
                raise ValueError("available Kronos forecasts require a complete distribution vector")
            probability_sum = (self.p_positive_return or 0) + (self.p_negative_return or 0)
            if abs(probability_sum - 1.0) > 1e-9:
                raise ValueError("positive and negative return probabilities must sum to one")
            if self.leakage_audit.quant_veto_required:
                raise ValueError("an available Kronos forecast cannot fail its leakage audit")
        if self.tp_first_probability is not None:
            barrier_sum = (
                self.tp_first_probability
                + (self.sl_first_probability or 0)
                + (self.neither_probability or 0)
            )
            if abs(barrier_sum - 1.0) > 1e-9:
                raise ValueError("TP-first, SL-first, and neither probabilities must sum to one")
        return self


class KronosForecastBundle(FrozenModel):
    bundle_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)
    instrument: str = Field(min_length=3, max_length=16)
    mode: KronosMode
    forecasts: tuple[KronosForecast, ...] = Field(min_length=1, max_length=4)
    available_forecasts: int = Field(ge=0, le=4)
    created_at: datetime
    effective_weight: float = Field(ge=0, le=0.25)
    research_only: Literal[True] = True
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_bundle(self) -> KronosForecastBundle:
        if len({forecast.timeframe for forecast in self.forecasts}) != len(self.forecasts):
            raise ValueError("a Kronos bundle may contain only one forecast per timeframe")
        count = sum(forecast.status is KronosForecastStatus.AVAILABLE for forecast in self.forecasts)
        if count != self.available_forecasts:
            raise ValueError("available forecast count does not match the bundle")
        if self.mode is KronosMode.SHADOW and self.effective_weight != 0:
            raise ValueError("a shadow bundle cannot influence Atlas")
        return self


class KronosStatus(FrozenModel):
    enabled: bool
    mode: KronosMode
    provider_available: bool
    provider_reason: str | None = None
    model_version: str
    configured_sample_count: int = Field(ge=200, le=1_000)
    configured_horizon_bars: int = Field(ge=1, le=512)
    effective_weight: float = Field(ge=0, le=0.25)
    max_weight: float = Field(ge=0, le=0.25)
    pretraining_cutoff_verified: bool = False
    signal_authority: Literal[False] = False
    execution_enabled: Literal[False] = False


__all__ = [
    "KronosBarrierGeometry",
    "KronosForecast",
    "KronosForecastBundle",
    "KronosForecastStatus",
    "KronosLeakageAudit",
    "KronosMode",
    "KronosStatus",
]
