// app/lib/backtest.ts
// Chronological, next-bar-open backtester for three rule-based strategies.
// Ambiguous bars (stop and target both inside the same bar's range) are
// always resolved stop-first, which is the conservative assumption.
//
// The engine does NOT model commissions, spreads, slippage, changing
// contract definitions, session gaps, or dataset revisions (see
// BACKTESTING.md). Every result is either real-provider or DEMO; callers
// must pass `demoOnly` explicitly rather than let this module guess.

import { atr, computeIndicatorSnapshot } from "./indicators.ts";
import { classifyRegime } from "./signal-engine.ts";
import type {
  BacktestResult,
  BacktestTrade,
  Direction,
  OHLCBar,
  PartitionStats,
  StrategyId,
  Timeframe,
} from "./types";

const WARMUP_BARS = 50;
const STOP_ATR_MULT = 1.5;
const TARGET_ATR_MULT = 2;

type EntrySignalFn = (bars: OHLCBar[], i: number) => Direction | null;

function trendFollowingEntry(bars: OHLCBar[], i: number): Direction | null {
  const window = bars.slice(0, i + 1);
  const snap = computeIndicatorSnapshot(window);
  if (
    snap.ema12 === null ||
    snap.ema26 === null ||
    snap.sma50 === null ||
    snap.directionalEfficiency14 === null
  )
    return null;
  const price = window[window.length - 1].close;
  const bullish =
    snap.ema12 > snap.ema26 &&
    price > snap.sma50 &&
    snap.directionalEfficiency14 >= 55;
  const bearish =
    snap.ema12 < snap.ema26 &&
    price < snap.sma50 &&
    snap.directionalEfficiency14 >= 55;
  if (bullish) return "LONG";
  if (bearish) return "SHORT";
  return null;
}

function pullbackEntry(bars: OHLCBar[], i: number): Direction | null {
  if (i < 2) return null;
  const window = bars.slice(0, i + 1);
  const snap = computeIndicatorSnapshot(window);
  const prevSnap = computeIndicatorSnapshot(window.slice(0, -1));
  if (
    snap.ema12 === null ||
    snap.ema26 === null ||
    snap.rsi14 === null ||
    snap.sma20 === null ||
    prevSnap.rsi14 === null
  )
    return null;
  const price = window[window.length - 1].close;
  const uptrend = snap.ema12 > snap.ema26;
  const downtrend = snap.ema12 < snap.ema26;
  // Pullback = RSI dipped under 45 (bull) or over 55 (bear) on the prior bar
  // and price has now reclaimed the 20-period average.
  if (uptrend && prevSnap.rsi14 < 45 && price > snap.sma20) return "LONG";
  if (downtrend && prevSnap.rsi14 > 55 && price < snap.sma20) return "SHORT";
  return null;
}

function meanReversionEntry(bars: OHLCBar[], i: number): Direction | null {
  const window = bars.slice(0, i + 1);
  if (window.length < 30) return null;
  const snap = computeIndicatorSnapshot(window);
  if (snap.rsi14 === null) return null;
  const regime = classifyRegime(window);
  if (regime !== "RANGE") return null;
  if (snap.rsi14 <= 30) return "LONG";
  if (snap.rsi14 >= 70) return "SHORT";
  return null;
}

const STRATEGY_ENTRY: Record<StrategyId, EntrySignalFn> = {
  TREND_FOLLOWING: trendFollowingEntry,
  PULLBACK: pullbackEntry,
  MEAN_REVERSION: meanReversionEntry,
};

function partitionFor(
  index: number,
  total: number
): "TRAIN" | "VALIDATION" | "TEST" {
  const frac = index / total;
  if (frac < 0.6) return "TRAIN";
  if (frac < 0.8) return "VALIDATION";
  return "TEST";
}

export function runBacktest(
  strategy: StrategyId,
  symbol: string,
  timeframe: Timeframe,
  bars: OHLCBar[],
  demoOnly: boolean
): BacktestResult {
  const entryFn = STRATEGY_ENTRY[strategy];
  const trades: BacktestTrade[] = [];
  const equityCurve: { time: number; equity: number }[] = [];
  let cumulativeR = 0;

  let i = WARMUP_BARS;
  while (i < bars.length - 1) {
    const direction = entryFn(bars, i);
    if (!direction) {
      i++;
      continue;
    }

    const entryBar = bars[i + 1];
    const entryPrice = entryBar.open;
    const atrSeries = atr(bars.slice(0, i + 1), 14);
    const currentAtr = atrSeries[atrSeries.length - 1];
    if (!currentAtr || currentAtr <= 0) {
      i++;
      continue;
    }

    const stopDist = currentAtr * STOP_ATR_MULT;
    const targetDist = currentAtr * TARGET_ATR_MULT;
    const stop =
      direction === "LONG" ? entryPrice - stopDist : entryPrice + stopDist;
    const target =
      direction === "LONG" ? entryPrice + targetDist : entryPrice - targetDist;

    let exitPrice: number | null = null;
    let exitTime: number | null = null;
    let j = i + 1;
    for (; j < bars.length; j++) {
      const bar = bars[j];
      const hitStop =
        direction === "LONG" ? bar.low <= stop : bar.high >= stop;
      const hitTarget =
        direction === "LONG" ? bar.high >= target : bar.low <= target;

      if (hitStop && hitTarget) {
        // Ambiguous bar: conservative assumption is stop-first.
        exitPrice = stop;
        exitTime = bar.time;
        break;
      }
      if (hitStop) {
        exitPrice = stop;
        exitTime = bar.time;
        break;
      }
      if (hitTarget) {
        exitPrice = target;
        exitTime = bar.time;
        break;
      }
    }

    if (exitPrice === null) {
      // Never resolved within available history: close at the final bar.
      const lastBar = bars[bars.length - 1];
      exitPrice = lastBar.close;
      exitTime = lastBar.time;
      j = bars.length - 1;
    }

    const risk = Math.abs(entryPrice - stop);
    const reward =
      direction === "LONG" ? exitPrice - entryPrice : entryPrice - exitPrice;
    const rMultiple = risk > 0 ? reward / risk : 0;
    const outcome: "WIN" | "LOSS" = rMultiple >= 0 ? "WIN" : "LOSS";

    const trade: BacktestTrade = {
      strategy,
      direction,
      entryTime: entryBar.time,
      entryPrice,
      exitTime: exitTime as number,
      exitPrice,
      stop,
      target,
      outcome,
      rMultiple: Math.round(rMultiple * 100) / 100,
      partition: partitionFor(i, bars.length),
    };
    trades.push(trade);
    cumulativeR += trade.rMultiple;
    equityCurve.push({ time: trade.exitTime, equity: cumulativeR });

    // Resume scanning after this trade closes so trades don't overlap.
    i = Math.max(j, i + 1) + 1;
  }

  const byPartition = (p: "TRAIN" | "VALIDATION" | "TEST") =>
    trades.filter((t) => t.partition === p);

  return {
    strategy,
    symbol,
    timeframe,
    trades,
    equityCurve,
    stats: {
      train: computePartitionStats(byPartition("TRAIN")),
      validation: computePartitionStats(byPartition("VALIDATION")),
      test: computePartitionStats(byPartition("TEST")),
    },
    demoOnly,
  };
}

export function computePartitionStats(trades: BacktestTrade[]): PartitionStats {
  if (trades.length === 0) {
    return {
      tradeCount: 0,
      winRate: null,
      avgRMultiple: null,
      profitFactor: null,
      maxDrawdownR: null,
    };
  }
  const wins = trades.filter((t) => t.outcome === "WIN");
  const grossWin = trades
    .filter((t) => t.rMultiple > 0)
    .reduce((a, t) => a + t.rMultiple, 0);
  const grossLoss = Math.abs(
    trades.filter((t) => t.rMultiple < 0).reduce((a, t) => a + t.rMultiple, 0)
  );

  let cumulative = 0;
  let peak = 0;
  let maxDrawdown = 0;
  for (const t of trades) {
    cumulative += t.rMultiple;
    peak = Math.max(peak, cumulative);
    maxDrawdown = Math.max(maxDrawdown, peak - cumulative);
  }

  return {
    tradeCount: trades.length,
    winRate: Math.round((wins.length / trades.length) * 1000) / 1000,
    avgRMultiple:
      Math.round(
        (trades.reduce((a, t) => a + t.rMultiple, 0) / trades.length) * 100
      ) / 100,
    profitFactor: grossLoss > 0 ? Math.round((grossWin / grossLoss) * 100) / 100 : null,
    maxDrawdownR: Math.round(maxDrawdown * 100) / 100,
  };
}
