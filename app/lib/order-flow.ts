// app/lib/order-flow.ts
// Deterministic tick-rule order-flow approximation. No AI, no randomness.
//
// WHY "APPROXIMATION": forex/CFD markets are decentralized OTC — there is
// no single consolidated tape, so no retail feed (MT5 included) exposes
// verified aggressor-side trade data the way a centralized exchange (e.g.
// futures order book with matched trades) does. This module uses the
// classic "tick rule": a tick is classified as buy-pressure if the trade
// price rose vs. the previous tick, sell-pressure if it fell, and inherits
// the prior classification if unchanged (the standard "zero-tick" rule).
// This is a legitimate, widely-used approximation — but it is still an
// approximation, and every OrderFlowRead carries `method:
// "TICK_RULE_APPROXIMATION"` so nothing downstream can present it as
// verified Level 2 data.
//
// Input ticks should be MT5 symbol_info_tick / copy_ticks_range output (or
// equivalent from another venue), sorted chronologically.

import type { BarOrderFlow, OHLCBar, OrderFlowPressure, OrderFlowRead, Tick } from "./types.ts";

const ABSORPTION_VOLUME_PERCENTILE = 0.75; // top quartile of bar volume
const ABSORPTION_MAX_RANGE_PERCENTILE = 0.35; // bottom-third of bar range
const PRESSURE_STRONG_THRESHOLD = 0.6; // |delta| / totalVolume for "strong"
const PRESSURE_THRESHOLD = 0.2; // |delta| / totalVolume for plain pressure

function tickPrice(t: Tick): number {
  // Mid price is the most stable choice when `last` isn't reported (common
  // for FX/CFD ticks, which are quote-driven rather than trade-driven).
  return t.last ?? (t.bid + t.ask) / 2;
}

/**
 * Classifies each tick as buy-pressure (+1), sell-pressure (-1), or
 * carries forward the prior tick's classification when price is unchanged.
 * Returns a same-length array of +1/-1 (never 0, except before the first
 * directional move, which is treated as neutral/+0).
 */
export function classifyTicks(ticks: Tick[]): number[] {
  const signs: number[] = new Array(ticks.length).fill(0);
  let lastSign = 0;
  let lastPrice: number | null = null;
  for (let i = 0; i < ticks.length; i++) {
    const price = tickPrice(ticks[i]);
    if (lastPrice === null) {
      signs[i] = 0;
    } else if (price > lastPrice) {
      lastSign = 1;
      signs[i] = 1;
    } else if (price < lastPrice) {
      lastSign = -1;
      signs[i] = -1;
    } else {
      signs[i] = lastSign; // zero-tick rule
    }
    lastPrice = price;
  }
  return signs;
}

/**
 * Buckets a tick series into the same closed-bar boundaries as `bars`
 * (each bar's window is [bar.time, nextBar.time)), producing per-bar
 * buy/sell volume, delta, and a running cumulative delta across the
 * whole supplied series.
 */
export function computeBarOrderFlow(
  bars: OHLCBar[],
  ticks: Tick[]
): BarOrderFlow[] {
  if (bars.length === 0) return [];
  const signs = classifyTicks(ticks);

  const result: BarOrderFlow[] = bars.map((b) => ({
    barTime: b.time,
    buyVolume: 0,
    sellVolume: 0,
    delta: 0,
    cumulativeDelta: 0,
    tickCount: 0,
    absorption: false,
  }));

  // Two-pointer bucketing assumes both bars and ticks are chronological.
  let barIdx = 0;
  for (let i = 0; i < ticks.length; i++) {
    const t = ticks[i].time;
    while (
      barIdx < bars.length - 1 &&
      t >= bars[barIdx + 1].time
    ) {
      barIdx++;
    }
    if (barIdx >= bars.length || t < bars[barIdx].time) continue;

    const sign = signs[i];
    const vol = ticks[i].volume;
    if (sign > 0) result[barIdx].buyVolume += vol;
    else if (sign < 0) result[barIdx].sellVolume += vol;
    result[barIdx].tickCount += 1;
  }

  let cumulative = 0;
  const volumes = result.map((r) => r.buyVolume + r.sellVolume);
  const sortedVolumes = [...volumes].sort((a, b) => a - b);
  const volumeThreshold =
    sortedVolumes[
      Math.min(
        sortedVolumes.length - 1,
        Math.floor(sortedVolumes.length * ABSORPTION_VOLUME_PERCENTILE)
      )
    ] ?? 0;

  const ranges = bars.map((b) => b.high - b.low);
  const sortedRanges = [...ranges].sort((a, b) => a - b);
  const rangeThreshold =
    sortedRanges[
      Math.min(
        sortedRanges.length - 1,
        Math.floor(sortedRanges.length * ABSORPTION_MAX_RANGE_PERCENTILE)
      )
    ] ?? 0;

  for (let i = 0; i < result.length; i++) {
    result[i].delta = result[i].buyVolume - result[i].sellVolume;
    cumulative += result[i].delta;
    result[i].cumulativeDelta = cumulative;
    // Absorption: heavy volume traded but the bar barely moved -> someone
    // is soaking up aggressive orders without price giving way.
    result[i].absorption =
      volumes[i] > 0 &&
      volumes[i] >= volumeThreshold &&
      ranges[i] <= rangeThreshold &&
      volumeThreshold > 0;
  }

  return result;
}

function classifyPressure(delta: number, totalVolume: number): OrderFlowPressure {
  if (totalVolume === 0) return "NEUTRAL";
  const ratio = delta / totalVolume;
  if (ratio >= PRESSURE_STRONG_THRESHOLD) return "STRONG_BUY_PRESSURE";
  if (ratio >= PRESSURE_THRESHOLD) return "BUY_PRESSURE";
  if (ratio <= -PRESSURE_STRONG_THRESHOLD) return "STRONG_SELL_PRESSURE";
  if (ratio <= -PRESSURE_THRESHOLD) return "SELL_PRESSURE";
  return "NEUTRAL";
}

export interface ComputeOrderFlowInput {
  symbol: string;
  bars: OHLCBar[]; // closed bars, same series the signal is generated on
  ticks: Tick[]; // chronological ticks covering (at least) the bar window
  lookbackBars?: number; // how many recent bars to summarize; default 10
  dataStatus: "LIVE" | "DEMO" | "UNAVAILABLE";
}

export function computeOrderFlowRead(
  input: ComputeOrderFlowInput
): OrderFlowRead | null {
  const { symbol, bars, ticks, dataStatus } = input;
  const lookback = input.lookbackBars ?? 10;

  if (bars.length === 0 || ticks.length === 0) return null;

  const allBarFlow = computeBarOrderFlow(bars, ticks);
  const recentBars = allBarFlow.slice(-lookback);
  if (recentBars.length === 0) return null;

  const totalBuy = recentBars.reduce((a, b) => a + b.buyVolume, 0);
  const totalSell = recentBars.reduce((a, b) => a + b.sellVolume, 0);
  const totalDelta = totalBuy - totalSell;
  const totalVolume = totalBuy + totalSell;
  const pressure = classifyPressure(totalDelta, totalVolume);

  const first = recentBars[0].cumulativeDelta;
  const last = recentBars[recentBars.length - 1].cumulativeDelta;
  const deltaChange = last - first;
  const trend: "RISING" | "FALLING" | "FLAT" =
    Math.abs(deltaChange) < totalVolume * 0.05
      ? "FLAT"
      : deltaChange > 0
      ? "RISING"
      : "FALLING";

  const absorptionDetected = recentBars.some((b) => b.absorption);

  const evidence: string[] = [
    `Tick-rule approximation (not verified Level 2 data) over the last ${recentBars.length} bars.`,
    `Buy volume ${totalBuy.toFixed(0)} vs sell volume ${totalSell.toFixed(0)} (net delta ${totalDelta.toFixed(0)}).`,
    `Cumulative delta ${trend.toLowerCase()} over the lookback window.`,
  ];
  if (absorptionDetected) {
    evidence.push(
      "Absorption flagged on at least one bar: high volume with an unusually small range."
    );
  }
  if (totalVolume === 0) {
    evidence.push("No tick volume in the lookback window; pressure read is NEUTRAL by default, not by evidence.");
  }

  return {
    method: "TICK_RULE_APPROXIMATION",
    symbol,
    pressure,
    cumulativeDeltaTrend: trend,
    recentBars,
    absorptionDetected,
    evidence,
    dataStatus,
    ticksAnalyzed: ticks.length,
  };
}
