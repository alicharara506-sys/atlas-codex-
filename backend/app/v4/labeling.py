"""Leakage-resistant volatility/structure-aware triple-barrier labeling."""

from __future__ import annotations

from itertools import pairwise

from app.core.store import stable_key
from app.v4.contracts import V4Bar
from app.v4.quant_contracts import (
    BarrierOrderPolicy,
    ResearchDirection,
    TripleBarrierConfig,
    TripleBarrierLabel,
    TripleBarrierRecord,
    TripleBarrierResult,
)


class TripleBarrierLabeler:
    """Label trades using barriers constructed only from data at/before signal time."""

    version = "triple-barrier-v1"

    def label(
        self,
        instrument: str,
        bars: tuple[V4Bar, ...],
        direction: ResearchDirection,
        config: TripleBarrierConfig | None = None,
    ) -> TripleBarrierResult:
        resolved = config or TripleBarrierConfig()
        _validate_bars(bars)
        minimum_history = max(resolved.atr_lookback + 1, resolved.structure_lookback)
        required = minimum_history + resolved.max_holding_bars + 1
        if len(bars) < required:
            raise ValueError(f"triple-barrier labeling requires at least {required} chronological bars")
        input_payload = [bar.model_dump(mode="json") for bar in bars]
        input_hash = stable_key(instrument.upper(), input_payload)
        records: list[TripleBarrierRecord] = []
        for anchor_index in range(minimum_history - 1, len(bars) - resolved.max_holding_bars):
            records.append(self._label_one(instrument.upper(), bars, anchor_index, direction, resolved, input_hash))
        run_id = stable_key(self.version, instrument.upper(), direction.value, resolved.model_dump(mode="json"), input_hash)
        return TripleBarrierResult(
            labeling_run_id=run_id,
            instrument=instrument.upper(),
            direction=direction,
            config=resolved,
            input_hash=input_hash,
            labels=tuple(records),
        )

    def _label_one(
        self,
        instrument: str,
        bars: tuple[V4Bar, ...],
        anchor_index: int,
        direction: ResearchDirection,
        config: TripleBarrierConfig,
        input_hash: str,
    ) -> TripleBarrierRecord:
        signal = bars[anchor_index]
        entry_index = anchor_index + 1
        entry_bar = bars[entry_index]
        entry = entry_bar.open
        atr = _atr_at(bars, anchor_index, config.atr_lookback)
        structure = bars[anchor_index - config.structure_lookback + 1 : anchor_index + 1]
        support = min(bar.low for bar in structure)
        resistance = max(bar.high for bar in structure)
        stop_distance = atr * config.stop_atr_multiple
        target_distance = atr * config.target_atr_multiple
        if config.use_structure:
            if direction is ResearchDirection.LONG:
                stop_distance = max(stop_distance, max(0.0, entry - support))
                target_distance = max(target_distance, max(0.0, resistance - entry))
            else:
                stop_distance = max(stop_distance, max(0.0, resistance - entry))
                target_distance = max(target_distance, max(0.0, entry - support))
        if direction is ResearchDirection.LONG:
            lower = entry - stop_distance
            upper = entry + target_distance
        else:
            lower = entry - target_distance
            upper = entry + stop_distance
        if lower <= 0:
            raise ValueError("triple-barrier lower boundary must remain positive")

        label = TripleBarrierLabel.TIMEOUT
        resolved_at = bars[entry_index + config.max_holding_bars - 1].timestamp
        holding_bars = config.max_holding_bars
        collision = False
        for offset, bar in enumerate(
            bars[entry_index : entry_index + config.max_holding_bars],
            start=1,
        ):
            touched_upper = bar.high >= upper
            touched_lower = bar.low <= lower
            if not touched_upper and not touched_lower:
                continue
            collision = touched_upper and touched_lower
            touch_upper = touched_upper and (
                not collision or config.barrier_order_policy is BarrierOrderPolicy.TARGET_FIRST
            )
            if direction is ResearchDirection.LONG:
                label = TripleBarrierLabel.LONG_WIN if touch_upper else TripleBarrierLabel.LONG_LOSS
            else:
                label = TripleBarrierLabel.SHORT_LOSS if touch_upper else TripleBarrierLabel.SHORT_WIN
            resolved_at = bar.timestamp
            holding_bars = offset
            break
        label_id = stable_key(
            self.version,
            input_hash,
            instrument,
            direction.value,
            signal.timestamp.isoformat(),
            entry,
            lower,
            upper,
        )
        return TripleBarrierRecord(
            label_id=label_id,
            instrument=instrument,
            direction=direction,
            signal_at=signal.timestamp,
            input_cutoff=signal.timestamp,
            entry_at=entry_bar.timestamp,
            resolved_at=resolved_at,
            entry=entry,
            upper_barrier=upper,
            lower_barrier=lower,
            atr_at_signal=atr,
            label=label,
            holding_bars=holding_bars,
            same_bar_collision=collision,
        )


def _validate_bars(bars: tuple[V4Bar, ...]) -> None:
    if any(current.timestamp <= previous.timestamp for previous, current in pairwise(bars)):
        raise ValueError("triple-barrier bars must be strictly chronological")


def _atr_at(bars: tuple[V4Bar, ...], anchor_index: int, lookback: int) -> float:
    start = anchor_index - lookback + 1
    true_ranges: list[float] = []
    for index in range(start, anchor_index + 1):
        current = bars[index]
        previous_close = bars[index - 1].close
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            )
        )
    atr = sum(true_ranges) / len(true_ranges)
    if atr <= 0:
        raise ValueError("ATR must be positive for triple-barrier labeling")
    return atr


__all__ = ["TripleBarrierLabeler"]
