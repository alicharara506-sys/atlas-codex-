"""Strict, versioned YAML contracts for Atlas Phase 3 mandates."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

KNOWN_DESKS = {
    "fundamentals",
    "technical",
    "sentiment",
    "macro",
    "narrative",
    "flow",
    "insider",
    "pattern",
}


class MandateValidationError(ValueError):
    """Raised when mandate YAML or its typed schema is invalid."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MandateStage(StrEnum):
    VALIDATED = "VALIDATED"
    BACKTESTED = "BACKTESTED"


class UniverseConfig(StrictModel):
    asset_class: str = Field(min_length=1, max_length=32)
    symbols: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_symbols(self) -> UniverseConfig:
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("universe symbols must be unique")
        return self


class DeskConfig(StrictModel):
    enabled: list[str] = Field(min_length=1)
    weights: dict[str, float]
    llm_enabled: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_desks(self) -> DeskConfig:
        enabled = set(self.enabled)
        if len(enabled) != len(self.enabled):
            raise ValueError("enabled desks must be unique")
        unknown = enabled - KNOWN_DESKS
        if unknown:
            raise ValueError(f"unknown desks: {sorted(unknown)}")
        if set(self.weights) != enabled:
            raise ValueError("desk weights must exactly match enabled desks")
        if any(weight < 0.0 for weight in self.weights.values()) or sum(self.weights.values()) <= 0.0:
            raise ValueError("desk weights must be non-negative with a positive total")
        if not set(self.llm_enabled).issubset(enabled):
            raise ValueError("LLM-enabled desks must also be enabled")
        return self


class MandateRiskConfig(StrictModel):
    max_position_pct: float = Field(gt=0.0, le=1.0)
    max_leverage: float = Field(gt=0.0)
    daily_drawdown_limit: float = Field(gt=0.0, le=1.0)
    weekly_drawdown_limit: float = Field(gt=0.0, le=1.0)
    correlation_threshold: float = Field(ge=0.0, le=1.0)


class MandateExecutionConfig(StrictModel):
    mode: Literal["research", "paper", "live"] = "research"
    rebalance_cadence: str = Field(default="1d", min_length=1, max_length=16)
    exchange: str = Field(default="none", min_length=1, max_length=64)


class MandateBacktestConfig(StrictModel):
    warmup_bars: int = Field(default=50, ge=20, le=10_000)
    eval_bars: int = Field(default=180, ge=10, le=100_000)
    benchmark: Literal["buy_and_hold"] = "buy_and_hold"


class MandateDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=5, max_length=32)
    universe: UniverseConfig
    desks: DeskConfig
    risk: MandateRiskConfig
    execution: MandateExecutionConfig = Field(default_factory=MandateExecutionConfig)
    backtest: MandateBacktestConfig = Field(default_factory=MandateBacktestConfig)

    @model_validator(mode="after")
    def semantic_version(self) -> MandateDefinition:
        if re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", self.version) is None:
            raise ValueError("mandate version must use semantic versioning")
        return self


class MandateDocument(StrictModel):
    mandate: MandateDefinition


class PromotionStatus(StrictModel):
    stage: MandateStage
    backtest_complete: bool
    paper_eligible: Literal[False] = False
    live_eligible: Literal[False] = False
    execution_enabled: Literal[False] = False
    reasons: list[str]


def parse_mandate_yaml(text: str) -> MandateDocument:
    if not text.strip():
        raise MandateValidationError("mandate YAML is empty")
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MandateValidationError("mandate YAML is malformed") from exc
    if not isinstance(payload, dict):
        raise MandateValidationError("mandate YAML must contain an object")
    try:
        return MandateDocument.model_validate(payload)
    except ValidationError as exc:
        raise MandateValidationError(str(exc)) from exc


def promotion_status(*, backtest_complete: bool) -> PromotionStatus:
    stage = MandateStage.BACKTESTED if backtest_complete else MandateStage.VALIDATED
    reasons = [
        "Mandate validation passed.",
        "Paper and live promotion are disabled by the global fail-closed policy.",
    ]
    if backtest_complete:
        reasons.insert(1, "Deterministic backtest completed.")
    return PromotionStatus(stage=stage, backtest_complete=backtest_complete, reasons=reasons)


__all__ = [
    "DeskConfig",
    "MandateBacktestConfig",
    "MandateDefinition",
    "MandateDocument",
    "MandateExecutionConfig",
    "MandateRiskConfig",
    "MandateStage",
    "MandateValidationError",
    "PromotionStatus",
    "UniverseConfig",
    "parse_mandate_yaml",
    "promotion_status",
]
