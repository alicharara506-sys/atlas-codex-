"""Deterministic, cost-aware and non-executable ATLAS V4 backtesting."""

from __future__ import annotations

import math
from itertools import pairwise

from app.core.store import RecordStore, stable_key
from app.v4.contracts import V4Bar
from app.v4.quant_contracts import (
    BacktestExitReason,
    BacktestMetrics,
    BacktestRun,
    BacktestTradePlan,
    BacktestTradeResult,
    BarrierOrderPolicy,
    CostModel,
    ResearchDirection,
)

BACKTEST_COLLECTION = "v4_quant_backtests"


class V4BacktestEngine:
    """Simulate immutable research plans with conservative deterministic fills."""

    version = "cost-aware-backtest-v1"

    def __init__(self, store: RecordStore | None = None) -> None:
        self.store = store

    def run(
        self,
        instrument: str,
        bars: tuple[V4Bar, ...],
        plans: tuple[BacktestTradePlan, ...],
        cost_model: CostModel | None = None,
        *,
        persist: bool = True,
    ) -> BacktestRun:
        _validate_inputs(instrument, bars, plans)
        costs = cost_model or CostModel()
        data_hash = stable_key([bar.model_dump(mode="json") for bar in bars])
        config_hash = stable_key([plan.model_dump(mode="json") for plan in plans], costs.model_dump(mode="json"))
        run_id = stable_key(self.version, instrument.upper(), data_hash, config_hash)
        if persist and self.store is not None:
            existing = self._by_id(run_id)
            if existing is not None:
                return existing
        results = tuple(self._simulate(plan, bars, costs) for plan in plans)
        run = BacktestRun(
            backtest_run_id=run_id,
            instrument=instrument.upper(),
            created_at=bars[-1].timestamp,
            data_hash=data_hash,
            config_hash=config_hash,
            cost_model=costs,
            trades=results,
            metrics=_metrics(results),
        )
        if persist and self.store is not None:
            self.store.upsert(BACKTEST_COLLECTION, run_id, run.model_dump(mode="json"))
        return run

    def _simulate(
        self,
        plan: BacktestTradePlan,
        bars: tuple[V4Bar, ...],
        costs: CostModel,
    ) -> BacktestTradeResult:
        candidates = [index for index, bar in enumerate(bars) if bar.timestamp > plan.signal_at]
        if not candidates:
            return _unfilled(plan, BacktestExitReason.DATA_UNAVAILABLE)
        entry_index = candidates[0] + costs.execution_delay_bars
        if entry_index >= len(bars):
            return _unfilled(plan, BacktestExitReason.DATA_UNAVAILABLE)
        entry_bar = bars[entry_index]
        if entry_bar.timestamp.hour not in costs.allowed_utc_hours:
            return _unfilled(plan, BacktestExitReason.SESSION_BLOCKED)
        if plan.direction is ResearchDirection.LONG:
            if not plan.stop_loss < entry_bar.open < plan.take_profit:
                return _unfilled(plan, BacktestExitReason.DATA_UNAVAILABLE)
        elif not plan.take_profit < entry_bar.open < plan.stop_loss:
            return _unfilled(plan, BacktestExitReason.DATA_UNAVAILABLE)

        entry_mid = entry_bar.open
        entry_fill = _adverse_fill(entry_mid, plan.direction, entering=True, costs=costs)
        exit_reason = BacktestExitReason.TIMEOUT
        exit_mid = bars[min(len(bars) - 1, entry_index + plan.max_holding_bars - 1)].close
        exit_at = bars[min(len(bars) - 1, entry_index + plan.max_holding_bars - 1)].timestamp
        holding_bars = min(plan.max_holding_bars, len(bars) - entry_index)
        collision = False
        gap_fill = False

        for offset, bar in enumerate(bars[entry_index : entry_index + plan.max_holding_bars], start=1):
            stop_hit, target_hit = _barrier_hits(plan, bar)
            if not stop_hit and not target_hit:
                continue
            collision = stop_hit and target_hit
            if collision:
                stop_wins = costs.barrier_order_policy is BarrierOrderPolicy.STOP_FIRST
            else:
                stop_wins = stop_hit
            exit_reason = BacktestExitReason.STOP if stop_wins else BacktestExitReason.TARGET
            exit_mid, gap_fill = _barrier_fill(plan, bar, exit_reason)
            exit_at = bar.timestamp
            holding_bars = offset
            break

        exit_fill = _adverse_fill(exit_mid, plan.direction, entering=False, costs=costs)
        direction_sign = 1.0 if plan.direction is ResearchDirection.LONG else -1.0
        gross_return = direction_sign * (exit_mid - entry_mid) / entry_mid
        if plan.direction is ResearchDirection.LONG:
            filled_return = exit_fill / entry_fill - 1
        else:
            filled_return = entry_fill / exit_fill - 1
        elapsed_days = max(0.0, (exit_at - entry_bar.timestamp).total_seconds() / 86_400)
        net_return = (
            filled_return
            - costs.commission_bps_round_turn / 10_000
            - costs.daily_swap_bps * elapsed_days / 10_000
        )
        risk_fraction = abs(entry_mid - plan.stop_loss) / entry_mid
        realized_r = net_return / risk_fraction if risk_fraction > 0 else None
        return BacktestTradeResult(
            trade_id=plan.trade_id,
            direction=plan.direction,
            signal_at=plan.signal_at,
            entry_at=entry_bar.timestamp,
            exit_at=exit_at,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
            gross_return=gross_return,
            net_return=net_return,
            realized_r=realized_r,
            holding_bars=holding_bars,
            exit_reason=exit_reason,
            same_bar_collision=collision,
            gap_fill=gap_fill,
        )

    def _by_id(self, run_id: str) -> BacktestRun | None:
        assert self.store is not None
        for row in self.store.values(BACKTEST_COLLECTION):
            if row.get("backtest_run_id") == run_id:
                return BacktestRun.model_validate(row)
        return None


def _barrier_hits(plan: BacktestTradePlan, bar: V4Bar) -> tuple[bool, bool]:
    if plan.direction is ResearchDirection.LONG:
        return bar.low <= plan.stop_loss, bar.high >= plan.take_profit
    return bar.high >= plan.stop_loss, bar.low <= plan.take_profit


def _barrier_fill(
    plan: BacktestTradePlan,
    bar: V4Bar,
    reason: BacktestExitReason,
) -> tuple[float, bool]:
    if plan.direction is ResearchDirection.LONG:
        if reason is BacktestExitReason.STOP and bar.open <= plan.stop_loss:
            return bar.open, True
        if reason is BacktestExitReason.TARGET and bar.open >= plan.take_profit:
            return plan.take_profit, True
    else:
        if reason is BacktestExitReason.STOP and bar.open >= plan.stop_loss:
            return bar.open, True
        if reason is BacktestExitReason.TARGET and bar.open <= plan.take_profit:
            return plan.take_profit, True
    return (plan.stop_loss if reason is BacktestExitReason.STOP else plan.take_profit), False


def _adverse_fill(price: float, direction: ResearchDirection, *, entering: bool, costs: CostModel) -> float:
    half_spread = costs.spread_bps / 20_000
    slippage = costs.slippage_bps / 10_000
    adverse = half_spread + slippage
    buying = (direction is ResearchDirection.LONG and entering) or (
        direction is ResearchDirection.SHORT and not entering
    )
    return price * (1 + adverse if buying else 1 - adverse)


def _unfilled(plan: BacktestTradePlan, reason: BacktestExitReason) -> BacktestTradeResult:
    return BacktestTradeResult(
        trade_id=plan.trade_id,
        direction=plan.direction,
        signal_at=plan.signal_at,
        holding_bars=0,
        exit_reason=reason,
    )


def _metrics(results: tuple[BacktestTradeResult, ...]) -> BacktestMetrics:
    executed = [row for row in results if row.entry_at is not None]
    wins = [row.net_return for row in executed if row.net_return > 0]
    losses = [row.net_return for row in executed if row.net_return < 0]
    timeouts = sum(row.exit_reason is BacktestExitReason.TIMEOUT for row in executed)
    cumulative = math.prod(1 + row.net_return for row in executed) - 1 if executed else 0.0
    equity = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for row in executed:
        equity *= 1 + row.net_return
        peak = max(peak, equity)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return BacktestMetrics(
        total_plans=len(results),
        executed_trades=len(executed),
        wins=len(wins),
        losses=len(losses),
        timeouts=timeouts,
        blocked=len(results) - len(executed),
        win_rate=len(wins) / len(executed) if executed else None,
        mean_net_return=sum(row.net_return for row in executed) / len(executed) if executed else None,
        cumulative_net_return=cumulative,
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else None,
        maximum_drawdown=maximum_drawdown,
    )


def _validate_inputs(
    instrument: str,
    bars: tuple[V4Bar, ...],
    plans: tuple[BacktestTradePlan, ...],
) -> None:
    if not instrument.strip():
        raise ValueError("backtest instrument cannot be empty")
    if len(bars) < 2:
        raise ValueError("backtest requires at least two bars")
    if any(current.timestamp <= previous.timestamp for previous, current in pairwise(bars)):
        raise ValueError("backtest bars must be strictly chronological")
    if len({plan.trade_id for plan in plans}) != len(plans):
        raise ValueError("backtest trade IDs must be unique")
    if any(plan.instrument.upper() != instrument.upper() for plan in plans):
        raise ValueError("backtest plan instrument does not match the run instrument")


__all__ = ["BACKTEST_COLLECTION", "V4BacktestEngine"]
