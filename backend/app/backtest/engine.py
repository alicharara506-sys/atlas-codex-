"""OHLCV-only backtester with completed-bar signals and next-open fills."""

from __future__ import annotations

import math
from statistics import fmean
from typing import Literal

from app.backtest.contracts import (
    BacktestAuditEvent,
    BacktestMetrics,
    BacktestReport,
    BacktestSeries,
    EquityPoint,
    TradeRecord,
)
from app.mandates.contracts import MandateDocument


class BacktestError(ValueError):
    """Raised when a deterministic backtest cannot be run safely."""


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = fmean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _max_drawdown(equity: list[float]) -> float:
    peak = 1.0
    maximum = 0.0
    for value in equity:
        peak = max(peak, value)
        maximum = max(maximum, (peak - value) / peak)
    return maximum


def _metrics(
    period_returns: list[float],
    equity: list[float],
    benchmark_return: float,
    trades: list[TradeRecord],
) -> BacktestMetrics:
    total_return = equity[-1] - 1.0 if equity else 0.0
    deviation = _sample_std(period_returns)
    sharpe = fmean(period_returns) / deviation * math.sqrt(252.0) if deviation > 0.0 else 0.0
    downside = [min(0.0, value) for value in period_returns]
    downside_deviation = math.sqrt(fmean([value * value for value in downside])) if downside else 0.0
    sortino = fmean(period_returns) / downside_deviation * math.sqrt(252.0) if downside_deviation > 0.0 else 0.0
    gross_profit = sum(row.weighted_return_pct for row in trades if row.weighted_return_pct > 0.0)
    gross_loss = abs(sum(row.weighted_return_pct for row in trades if row.weighted_return_pct < 0.0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0.0 else None
    wins = sum(1 for row in trades if row.weighted_return_pct > 0.0)
    return BacktestMetrics(
        total_return=round(total_return, 10),
        sharpe=round(sharpe, 6),
        sortino=round(sortino, 6),
        max_drawdown=round(_max_drawdown(equity), 10),
        profit_factor=round(profit_factor, 6) if profit_factor is not None else None,
        win_rate=round(wins / len(trades), 6) if trades else 0.0,
        benchmark_return=round(benchmark_return, 10),
        excess_return=round(total_return - benchmark_return, 10),
        trade_count=len(trades),
    )


def run_backtest(document: MandateDocument, series: list[BacktestSeries]) -> BacktestReport:
    mandate = document.mandate
    expected = set(mandate.universe.symbols)
    supplied = {row.symbol for row in series}
    if expected != supplied or len(series) != len(supplied):
        raise BacktestError("backtest series must exactly match the unique mandate universe")
    warmup = mandate.backtest.warmup_bars
    evaluation = mandate.backtest.eval_bars
    required = warmup + evaluation
    if any(len(row.bars) < required for row in series):
        raise BacktestError(f"each series requires at least {required} bars")

    ordered = sorted(series, key=lambda row: row.symbol)
    allocation = min(mandate.risk.max_position_pct, mandate.risk.max_leverage / len(ordered))
    period_returns: list[float] = []
    trades: list[TradeRecord] = []
    equity_points: list[EquityPoint] = []
    audit: list[BacktestAuditEvent] = []
    equity_value = 1.0
    benchmark_value = 1.0
    lookback = min(20, warmup)
    benchmark_entries = {item.symbol: item.bars[warmup].open for item in ordered}

    for step in range(evaluation):
        signal_index = warmup - 1 + step
        entry_index = signal_index + 1
        step_returns: list[float] = []
        signals: dict[str, Literal["LONG", "SHORT", "HOLD"]] = {}
        for item in ordered:
            bars = item.bars
            history = [bar.close for bar in bars[signal_index - lookback + 1 : signal_index + 1]]
            moving_average = fmean(history)
            completed_close = bars[signal_index].close
            if completed_close > moving_average:
                direction = 1.0
                side: Literal["LONG", "SHORT", "HOLD"] = "LONG"
            elif completed_close < moving_average:
                direction = -1.0
                side = "SHORT"
            else:
                direction = 0.0
                side = "HOLD"
            signals[item.symbol] = side
            next_bar = bars[entry_index]
            raw_return = direction * (next_bar.close - next_bar.open) / next_bar.open
            weighted_return = max(-0.999999, raw_return * allocation)
            step_returns.append(weighted_return)
            if direction != 0.0:
                trades.append(
                    TradeRecord(
                        symbol=item.symbol,
                        side=side,
                        signal_time=bars[signal_index].timestamp,
                        entry_time=next_bar.timestamp,
                        exit_time=next_bar.timestamp,
                        signal_index=signal_index,
                        entry_index=entry_index,
                        entry_price=next_bar.open,
                        exit_price=next_bar.close,
                        return_pct=round(raw_return, 10),
                        weighted_return_pct=round(weighted_return, 10),
                    )
                )
        portfolio_return = sum(step_returns)
        equity_value *= 1.0 + portfolio_return
        benchmark_value = 1.0 + fmean(
            [item.bars[entry_index].close / benchmark_entries[item.symbol] - 1.0 for item in ordered]
        )
        period_returns.append(portfolio_return)
        timestamp = ordered[0].bars[entry_index].timestamp
        equity_points.append(
            EquityPoint(
                timestamp=timestamp,
                equity=round(equity_value, 10),
                benchmark_equity=round(benchmark_value, 10),
                period_return=round(portfolio_return, 10),
            )
        )
        audit.append(
            BacktestAuditEvent(
                sequence=step + 1,
                timestamp=timestamp,
                summary="Signals used only completed bars; any position was filled at the next bar open.",
                signals=signals,
            )
        )
    report_metrics = _metrics(period_returns, [row.equity for row in equity_points], benchmark_value - 1.0, trades)
    return BacktestReport(
        mandate_name=mandate.name,
        mandate_version=mandate.version,
        symbols=mandate.universe.symbols,
        warmup_bars=warmup,
        evaluation_bars=evaluation,
        metrics=report_metrics,
        trades=trades,
        equity=equity_points,
        audit=audit,
    )


__all__ = ["BacktestError", "run_backtest"]
