// app/lib/indicators.ts
// Pure, deterministic indicator math. No I/O, no randomness, no AI.
// Every function operates on closed bars only — callers must exclude the
// forming/live bar before calling into this module.

import type { IndicatorSnapshot, OHLCBar } from "./types.ts";

function closes(bars: OHLCBar[]): number[] {
  return bars.map((b) => b.close);
}

export function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0 || values.length === 0) return out;
  const k = 2 / (period + 1);
  // Seed with an SMA of the first `period` values, matching common practice.
  if (values.length < period) return out;
  let seed = 0;
  for (let i = 0; i < period; i++) seed += values[i];
  seed /= period;
  out[period - 1] = seed;
  let prev = seed;
  for (let i = period; i < values.length; i++) {
    const next = values[i] * k + prev * (1 - k);
    out[i] = next;
    prev = next;
  }
  return out;
}

export function rsi(values: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length <= period) return out;

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const change = values[i] - values[i - 1];
    if (change >= 0) avgGain += change;
    else avgLoss += -change;
  }
  avgGain /= period;
  avgLoss /= period;

  const rsiFromAvgs = (gain: number, loss: number) => {
    if (loss === 0) return 100;
    const rs = gain / loss;
    return 100 - 100 / (1 + rs);
  };

  out[period] = rsiFromAvgs(avgGain, avgLoss);

  for (let i = period + 1; i < values.length; i++) {
    const change = values[i] - values[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = rsiFromAvgs(avgGain, avgLoss);
  }
  return out;
}

export function trueRange(bars: OHLCBar[]): number[] {
  const out: number[] = new Array(bars.length).fill(0);
  for (let i = 0; i < bars.length; i++) {
    if (i === 0) {
      out[i] = bars[i].high - bars[i].low;
      continue;
    }
    const prevClose = bars[i - 1].close;
    out[i] = Math.max(
      bars[i].high - bars[i].low,
      Math.abs(bars[i].high - prevClose),
      Math.abs(bars[i].low - prevClose)
    );
  }
  return out;
}

export function atr(bars: OHLCBar[], period = 14): (number | null)[] {
  const tr = trueRange(bars);
  const out: (number | null)[] = new Array(bars.length).fill(null);
  if (bars.length < period) return out;

  let seed = 0;
  for (let i = 0; i < period; i++) seed += tr[i];
  seed /= period;
  out[period - 1] = seed;
  let prev = seed;
  for (let i = period; i < bars.length; i++) {
    const next = (prev * (period - 1) + tr[i]) / period;
    out[i] = next;
    prev = next;
  }
  return out;
}

export function roc(values: number[], period = 10): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  for (let i = period; i < values.length; i++) {
    const prev = values[i - period];
    if (prev === 0) continue;
    out[i] = ((values[i] - prev) / prev) * 100;
  }
  return out;
}

export function stdevVolatility(
  values: number[],
  period = 20
): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    const window = values.slice(i - period + 1, i + 1);
    const mean = window.reduce((a, b) => a + b, 0) / period;
    const variance =
      window.reduce((a, b) => a + (b - mean) ** 2, 0) / period;
    out[i] = Math.sqrt(variance);
  }
  return out;
}

export function volumeRatio(
  bars: OHLCBar[],
  period = 20
): (number | null)[] {
  const out: (number | null)[] = new Array(bars.length).fill(null);
  const vols = bars.map((b) => b.volume);
  const avg = sma(vols, period);
  for (let i = 0; i < bars.length; i++) {
    const a = avg[i];
    if (a === null || a === 0) continue;
    out[i] = vols[i] / a;
  }
  return out;
}

/**
 * ADX-style directional-efficiency approximation, 0-100.
 * This is explicitly an approximation (documented in TRADING_ENGINE.md),
 * not a literal Wilder ADX implementation: it measures how much of a
 * period's total true-range travel was "spent" moving in the eventual
 * net direction, which is cheaper to compute and reason about while
 * still tracking trend efficiency vs. choppiness.
 */
export function directionalEfficiency(
  bars: OHLCBar[],
  period = 14
): (number | null)[] {
  const out: (number | null)[] = new Array(bars.length).fill(null);
  const tr = trueRange(bars);
  for (let i = period; i < bars.length; i++) {
    const window = bars.slice(i - period + 1, i + 1);
    const netMove = Math.abs(
      window[window.length - 1].close - bars[i - period].close
    );
    let totalTR = 0;
    for (let j = i - period + 1; j <= i; j++) totalTR += tr[j];
    if (totalTR === 0) {
      out[i] = 0;
      continue;
    }
    out[i] = Math.min(100, (netMove / totalTR) * 100);
  }
  return out;
}

/**
 * Computes the full indicator snapshot for the most recent closed bar.
 * Returns nulls for any indicator that doesn't yet have enough history —
 * callers must treat null as "not enough data", never as zero.
 */
export function computeIndicatorSnapshot(
  bars: OHLCBar[]
): IndicatorSnapshot {
  const c = closes(bars);
  const last = bars.length - 1;

  const sma20 = sma(c, 20);
  const sma50 = sma(c, 50);
  const ema12 = ema(c, 12);
  const ema26 = ema(c, 26);
  const rsi14 = rsi(c, 14);
  const atr14 = atr(bars, 14);
  const roc10 = roc(c, 10);
  const vol20 = stdevVolatility(c, 20);
  const volRatio20 = volumeRatio(bars, 20);
  const dirEff14 = directionalEfficiency(bars, 14);

  return {
    sma20: last >= 0 ? sma20[last] : null,
    sma50: last >= 0 ? sma50[last] : null,
    ema12: last >= 0 ? ema12[last] : null,
    ema26: last >= 0 ? ema26[last] : null,
    rsi14: last >= 0 ? rsi14[last] : null,
    atr14: last >= 0 ? atr14[last] : null,
    roc10: last >= 0 ? roc10[last] : null,
    volatilityStdev20: last >= 0 ? vol20[last] : null,
    volumeRatio20: last >= 0 ? volRatio20[last] : null,
    directionalEfficiency14: last >= 0 ? dirEff14[last] : null,
  };
}
