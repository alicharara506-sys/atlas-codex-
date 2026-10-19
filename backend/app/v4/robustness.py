"""Dependence-preserving robustness diagnostics for ATLAS V4 research."""

from __future__ import annotations

import math
import random

from app.core.store import RecordStore, stable_key
from app.v4.quant_contracts import RobustnessResult, RobustnessStatus

ROBUSTNESS_COLLECTION = "v4_quant_robustness"


class RobustnessValidator:
    """Estimate mean-return uncertainty with a deterministic moving-block bootstrap."""

    version = "moving-block-bootstrap-v1"

    def __init__(self, store: RecordStore | None = None) -> None:
        self.store = store

    def validate(
        self,
        returns: tuple[float, ...],
        *,
        variants_tested: int = 1,
        bootstrap_samples: int = 1_000,
        block_size: int | None = None,
        seed: int = 17,
        stress_cost_bps: float = 0.0,
        persist: bool = True,
    ) -> RobustnessResult:
        if variants_tested < 1:
            raise ValueError("variants_tested must be positive")
        if not 100 <= bootstrap_samples <= 20_000:
            raise ValueError("bootstrap_samples must be between 100 and 20000")
        if stress_cost_bps < 0 or stress_cost_bps > 1_000:
            raise ValueError("stress_cost_bps must be between 0 and 1000")
        if any(not math.isfinite(value) or value <= -1 for value in returns):
            raise ValueError("returns must be finite and greater than -1")
        sample_count = len(returns)
        resolved_block = block_size or max(2, int(math.sqrt(sample_count)))
        run_id = stable_key(
            self.version,
            returns,
            variants_tested,
            bootstrap_samples,
            resolved_block,
            seed,
            stress_cost_bps,
        )
        assumptions = (
            "returns are chronological and use one consistent cost convention",
            "moving contiguous blocks preserve local serial dependence",
            "the observed period is representative of the evaluated research regime",
        )
        if sample_count < 30:
            result = RobustnessResult(
                robustness_run_id=run_id,
                status=RobustnessStatus.INSUFFICIENT_DATA,
                sample_count=sample_count,
                bootstrap_samples=0,
                block_size=resolved_block,
                null_hypothesis="mean net return is less than or equal to zero",
                assumptions=assumptions,
                variants_tested=variants_tested,
                reason="At least 30 chronological net returns are required for the bootstrap diagnostic",
            )
            self._persist(result, persist)
            return result
        if not 2 <= resolved_block <= sample_count:
            raise ValueError("block_size must be between 2 and the return sample count")
        rng = random.Random(seed)
        means = [
            sum(_moving_block_sample(returns, resolved_block, rng)) / sample_count
            for _ in range(bootstrap_samples)
        ]
        means.sort()
        mean_return = sum(returns) / sample_count
        stressed = mean_return - stress_cost_bps / 10_000
        reason = (
            "DSR and PBO remain unavailable: they require governed cross-variant OOS estimates and a "
            "complete strategy-by-fold performance matrix, respectively"
        )
        result = RobustnessResult(
            robustness_run_id=run_id,
            status=RobustnessStatus.AVAILABLE,
            sample_count=sample_count,
            bootstrap_samples=bootstrap_samples,
            block_size=resolved_block,
            mean_return=mean_return,
            mean_ci_low=_quantile(means, 0.025),
            mean_ci_high=_quantile(means, 0.975),
            stressed_mean_return=stressed,
            null_hypothesis="mean net return is less than or equal to zero",
            assumptions=assumptions,
            variants_tested=variants_tested,
            deflated_sharpe_available=False,
            pbo_available=False,
            reason=reason,
        )
        self._persist(result, persist)
        return result

    def _persist(self, result: RobustnessResult, persist: bool) -> None:
        if persist and self.store is not None:
            self.store.upsert(
                ROBUSTNESS_COLLECTION,
                result.robustness_run_id,
                result.model_dump(mode="json"),
            )


def _moving_block_sample(
    values: tuple[float, ...],
    block_size: int,
    rng: random.Random,
) -> list[float]:
    result: list[float] = []
    maximum_start = len(values) - block_size
    while len(result) < len(values):
        start = rng.randint(0, maximum_start)
        result.extend(values[start : start + block_size])
    return result[: len(values)]


def _quantile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


__all__ = ["ROBUSTNESS_COLLECTION", "RobustnessValidator"]
