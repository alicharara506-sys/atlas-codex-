// app/lib/demo-market.ts
// Deterministic, seeded synthetic OHLC series for DEMO mode. Same seed
// always produces the same bars, so screenshots/tests/docs stay stable.
// This module must never be reachable when a real provider is configured
// and returning data — that switch lives in the provider layer, not here.

import type { OHLCBar, Tick, Timeframe } from "./types.ts";

const TIMEFRAME_SECONDS: Record<Timeframe, number> = {
  "15M": 15 * 60,
  "1H": 60 * 60,
  "4H": 4 * 60 * 60,
  D1: 24 * 60 * 60,
};

// Mulberry32 PRNG: tiny, deterministic, good enough for synthetic demo data.
function mulberry32(seed: number) {
  let a = seed;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashSeed(symbol: string, timeframe: string): number {
  let h = 2166136261;
  for (const ch of symbol + "|" + timeframe) {
    h ^= ch.charCodeAt(0);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export interface DemoSeriesOptions {
  symbol: string;
  timeframe: Timeframe;
  count: number;
  startPrice?: number;
  endTime?: number; // unix seconds; defaults to "now" floored to the timeframe
  driftPerBar?: number; // fraction, e.g. 0.0001
  volPerBar?: number; // fraction, e.g. 0.0015
}

export function generateDemoSeries(opts: DemoSeriesOptions): OHLCBar[] {
  const {
    symbol,
    timeframe,
    count,
    startPrice = 100,
    driftPerBar = 0.00005,
    volPerBar = 0.0015,
  } = opts;

  const stepSeconds = TIMEFRAME_SECONDS[timeframe];
  const endTime =
    opts.endTime ?? Math.floor(Date.now() / 1000 / stepSeconds) * stepSeconds;
  const startTime = endTime - stepSeconds * (count - 1);

  const rand = mulberry32(hashSeed(symbol, timeframe));
  const bars: OHLCBar[] = [];
  let price = startPrice;

  // Slowly-varying regime so structure/regime classifiers see realistic
  // trend/range/reversal segments rather than pure noise.
  let regimeBias = 0;
  let regimeBarsLeft = 0;

  for (let i = 0; i < count; i++) {
    if (regimeBarsLeft <= 0) {
      regimeBias = (rand() - 0.5) * 2 * driftPerBar * 6;
      regimeBarsLeft = 20 + Math.floor(rand() * 40);
    }
    regimeBarsLeft--;

    const open = price;
    const noise = (rand() - 0.5) * 2 * volPerBar;
    const close = Math.max(0.0001, open * (1 + regimeBias + noise));
    const high = Math.max(open, close) * (1 + rand() * volPerBar * 0.6);
    const low = Math.min(open, close) * (1 - rand() * volPerBar * 0.6);
    const volume = Math.round(1000 + rand() * 4000);

    bars.push({
      time: startTime + i * stepSeconds,
      open: round5(open),
      high: round5(high),
      low: round5(low),
      close: round5(close),
      volume,
    });

    price = close;
  }

  return bars;
}

function round5(x: number): number {
  return Math.round(x * 100000) / 100000;
}

/**
 * Deterministic synthetic ticks that stay within each bar's [low, high]
 * and interpolate from open toward close, for exercising order-flow.ts
 * without a live MT5 tick feed. Same inputs -> same ticks, every time.
 */
export function generateDemoTicks(bars: OHLCBar[], ticksPerBar = 20): Tick[] {
  if (bars.length === 0 || ticksPerBar <= 0) return [];
  const rand = mulberry32(hashSeed(`ticks-${bars[0].time}`, String(bars.length)));
  const ticks: Tick[] = [];

  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i];
    const nextTime =
      i < bars.length - 1
        ? bars[i + 1].time
        : bar.time + (bar.time - (bars[i - 1]?.time ?? bar.time - 60));
    const span = Math.max(1, nextTime - bar.time);
    const step = span / ticksPerBar;

    for (let j = 0; j < ticksPerBar; j++) {
      const progress = j / ticksPerBar;
      const target = bar.open + (bar.close - bar.open) * progress;
      const noise = (rand() - 0.5) * (bar.high - bar.low) * 0.3;
      const price = Math.min(bar.high, Math.max(bar.low, target + noise));
      const spread = Math.max(price * 0.00005, 0.00001);
      ticks.push({
        time: bar.time + j * step,
        bid: round5(price - spread / 2),
        ask: round5(price + spread / 2),
        volume: Math.round(1 + rand() * 5),
      });
    }
  }
  return ticks;
}
