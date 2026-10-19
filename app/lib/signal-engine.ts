// app/lib/signal-engine.ts
// Deterministic structure/regime classification and the weighted signal
// score. No AI, no randomness. Every threshold below is a named constant
// so a developer can retune the strategy without hunting through prose.
//
// Design note on "score" vs "direction":
//   1. Direction is decided ONLY from the multi-timeframe trend bias
//      (D1/4H/1H/15M). This keeps direction-flipping rare and explainable.
//   2. Score (0-100) then measures how much all eight weighted factors
//      *agree* with that direction. A perfectly aligned tape scores near
//      100; a contradictory tape scores low even if a naive trend filter
//      would have picked a direction.
//   3. Score is never converted into a probability anywhere in this file.
//      That conversion only happens in calibration.ts, gated on imported
//      evidence.

import { atr, computeIndicatorSnapshot, rsi, sma } from "./indicators.ts";
import { computeOrderFlowRead } from "./order-flow.ts";
import type {
  Direction,
  EngineeredFeatures,
  FactorScores,
  FactorWeights,
  OHLCBar,
  QualitativeConfidence,
  RegimeLabel,
  Scenario,
  StructureLabel,
  Tick,
  Timeframe,
  TradingSignal,
} from "./types.ts";
import { DEFAULT_FACTOR_WEIGHTS } from "./types.ts";

// ---------------------------------------------------------------------------
// Tunable constants (documented assumptions, not hidden magic numbers)
// ---------------------------------------------------------------------------

export const SWING_WINDOW = 10; // bars per half-window for structure detection
export const MTF_TIMEFRAME_WEIGHTS: Record<Timeframe, number> = {
  D1: 0.4,
  "4H": 0.3,
  "1H": 0.2,
  "15M": 0.1,
};
export const DIRECTION_THRESHOLD = 0.15; // min |weighted lean| to avoid WAIT
export const CONFLICT_WAIT_COUNT = 3; // timeframes disagreeing with majority -> WAIT
export const ATR_ENTRY_BUFFER_MULT = 0.25;
export const ATR_STOP_MULT = 1.5;
export const ATR_TARGET_MULTS: [number, number, number] = [1, 2, 3];
export type RegimeOverlayState = "TREND_UP" | "TREND_DOWN" | "MEAN_REVERT";

function percentileRank(values: number[], value: number): number {
  if (!values.length) return 0.5;
  return values.filter((candidate) => candidate <= value).length / values.length;
}

function normalizeWeights(weights: FactorWeights): FactorWeights {
  const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
  return Object.fromEntries(
    Object.entries(weights).map(([key, value]) => [key, value / total])
  ) as unknown as FactorWeights;
}

export function routeStrategyWeights(
  state: RegimeOverlayState,
  base: FactorWeights = DEFAULT_FACTOR_WEIGHTS
): FactorWeights {
  if (state === "MEAN_REVERT") {
    return normalizeWeights({
      ...base,
      structure: 0.16,
      trend: 0.1,
      momentum: 0.2,
      multiTimeframe: 0.12,
      volatility: 0.17,
      supportResistance: 0.15,
    });
  }
  return normalizeWeights({
    ...base,
    structure: 0.14,
    trend: 0.25,
    momentum: 0.25,
    multiTimeframe: 0.13,
    volatility: 0.08,
  });
}

// ---------------------------------------------------------------------------
// Structure
// ---------------------------------------------------------------------------

export function classifyStructure(
  bars: OHLCBar[],
  window = SWING_WINDOW
): StructureLabel {
  if (bars.length < window * 2) return "MIXED";
  const prior = bars.slice(-window * 2, -window);
  const recent = bars.slice(-window);

  const priorHigh = Math.max(...prior.map((b) => b.high));
  const priorLow = Math.min(...prior.map((b) => b.low));
  const recentHigh = Math.max(...recent.map((b) => b.high));
  const recentLow = Math.min(...recent.map((b) => b.low));

  if (recentHigh > priorHigh && recentLow > priorLow) return "HH_HL";
  if (recentHigh < priorHigh && recentLow < priorLow) return "LH_LL";
  return "MIXED";
}

// ---------------------------------------------------------------------------
// Regime
// ---------------------------------------------------------------------------

export function classifyRegime(bars: OHLCBar[]): RegimeLabel {
  if (bars.length < 30) return "UNCLEAR";
  const snap = computeIndicatorSnapshot(bars);
  const price = bars[bars.length - 1].close;
  if (
    snap.ema12 === null ||
    snap.ema26 === null ||
    snap.directionalEfficiency14 === null ||
    snap.volatilityStdev20 === null ||
    price === 0
  ) {
    return "UNCLEAR";
  }

  const normSpread = (snap.ema12 - snap.ema26) / price; // signed trend strength
  const normVol = snap.volatilityStdev20 / price; // volatility as fraction of price
  const dirEff = snap.directionalEfficiency14; // 0-100

  // Volatility regime is judged against the series' own recent volatility,
  // not an arbitrary constant, so it adapts per-instrument.
  const closesArr = bars.map((b) => b.close);
  const volSeries: number[] = [];
  for (let i = 20; i < bars.length; i++) {
    const window = closesArr.slice(i - 20, i);
    const mean = window.reduce((a, b) => a + b, 0) / window.length;
    const variance =
      window.reduce((a, b) => a + (b - mean) ** 2, 0) / window.length;
    volSeries.push(Math.sqrt(variance) / bars[i].close);
  }
  const avgVol =
    volSeries.length > 0
      ? volSeries.reduce((a, b) => a + b, 0) / volSeries.length
      : normVol;
  const volRatio = avgVol > 0 ? normVol / avgVol : 1;

  const structure = classifyStructure(bars);

  if (volRatio > 1.6) {
    return normSpread > 0 || normSpread < 0 ? "BREAKOUT" : "HIGH_VOLATILITY";
  }
  if (volRatio < 0.55) return "LOW_VOLATILITY";

  if (dirEff >= 45 && normSpread > 0.001) {
    return dirEff >= 65 ? "STRONG_BULL_TREND" : "WEAK_BULL_TREND";
  }
  if (dirEff >= 45 && normSpread < -0.001) {
    return dirEff >= 65 ? "STRONG_BEAR_TREND" : "WEAK_BEAR_TREND";
  }
  if (dirEff < 30 && structure === "MIXED") return "RANGE";

  // Structure flipped against the prevailing EMA spread -> possible reversal.
  if (
    (structure === "HH_HL" && normSpread < -0.0005) ||
    (structure === "LH_LL" && normSpread > 0.0005)
  ) {
    return "REVERSAL";
  }

  return "UNCLEAR";
}

export function detectRegimeOverlay(bars: OHLCBar[]): RegimeOverlayState {
  if (bars.length < 80) return "MEAN_REVERT";
  const atrValues = atr(bars, 14).filter((value): value is number => value !== null);
  const currentAtr = atrValues.at(-1) ?? 0;
  const atrPercentile = percentileRank(atrValues.slice(-120), currentAtr);
  const snap = computeIndicatorSnapshot(bars);
  const structures = [0, 10, 20].map((offset) =>
    classifyStructure(offset === 0 ? bars : bars.slice(0, -offset), 10)
  );
  if (atrPercentile > 0.7 && structures.every((value) => value === "HH_HL")) return "TREND_UP";
  if (atrPercentile > 0.7 && structures.every((value) => value === "LH_LL")) return "TREND_DOWN";

  const average = sma(bars.map((bar) => bar.close), 20);
  let crossings = 0;
  for (let index = Math.max(1, bars.length - 30); index < bars.length; index++) {
    const previousAverage = average[index - 1];
    const currentAverage = average[index];
    if (previousAverage === null || currentAverage === null) continue;
    const previousSide = Math.sign(bars[index - 1].close - previousAverage);
    const currentSide = Math.sign(bars[index].close - currentAverage);
    if (previousSide !== 0 && currentSide !== 0 && previousSide !== currentSide) crossings++;
  }
  if (atrPercentile < 0.5 && (snap.directionalEfficiency14 ?? 100) < 40 && crossings >= 3) return "MEAN_REVERT";
  const ema12 = snap.ema12 ?? bars.at(-1)!.close;
  const ema26 = snap.ema26 ?? ema12;
  return ema12 >= ema26 ? "TREND_UP" : "TREND_DOWN";
}

export class FeatureEngineer {
  static compute(bars: OHLCBar[], bidAskPressure = 0.5): EngineeredFeatures {
    if (!bars.length) {
      return { rsi14ZScore50: 0, directionalEfficiency: 0, tickVolumeRatio: 0, bidAskPressure: 0.5, anchoredVwapDeviationZ: 0, atr14Percentile: 0 };
    }
    const closes = bars.map((bar) => bar.close);
    const rsiValues = rsi(closes, 14).filter((value): value is number => value !== null);
    const rsiWindow = rsiValues.slice(-50);
    const currentRsi = rsiWindow.at(-1) ?? 50;
    const rsiMean = rsiWindow.length ? rsiWindow.reduce((sum, value) => sum + value, 0) / rsiWindow.length : 50;
    const rsiVariance = rsiWindow.length ? rsiWindow.reduce((sum, value) => sum + (value - rsiMean) ** 2, 0) / rsiWindow.length : 0;
    const rsiStd = Math.sqrt(rsiVariance);
    const snap = computeIndicatorSnapshot(bars);
    const recent = bars.slice(-50);
    const volumeTotal = recent.reduce((sum, bar) => sum + Math.max(0, bar.volume), 0);
    const vwap = volumeTotal > 0
      ? recent.reduce((sum, bar) => sum + ((bar.high + bar.low + bar.close) / 3) * Math.max(0, bar.volume), 0) / volumeTotal
      : closes.at(-1)!;
    const atrValues = atr(bars, 14).filter((value): value is number => value !== null);
    const currentAtr = atrValues.at(-1) ?? 0;
    const lastPrice = closes.at(-1)!;
    return {
      rsi14ZScore50: rsiStd > 0 ? (currentRsi - rsiMean) / rsiStd : 0,
      directionalEfficiency: Math.max(0, Math.min(1, (snap.directionalEfficiency14 ?? 0) / 100)),
      tickVolumeRatio: Math.max(0, Math.min(1, (snap.volumeRatio20 ?? 0) / 2)),
      bidAskPressure: Math.max(0, Math.min(1, bidAskPressure)),
      anchoredVwapDeviationZ: currentAtr > 0 ? (lastPrice - vwap) / currentAtr : 0,
      atr14Percentile: percentileRank(atrValues.slice(-120), currentAtr),
    };
  }
}

function orderFlowPressureScore(pressure: string | undefined): number {
  if (pressure === "STRONG_BUY_PRESSURE") return 1;
  if (pressure === "BUY_PRESSURE") return 0.75;
  if (pressure === "SELL_PRESSURE") return 0.25;
  if (pressure === "STRONG_SELL_PRESSURE") return 0;
  return 0.5;
}

// ---------------------------------------------------------------------------
// Factor leans: signed [-1, 1], positive = bullish, negative = bearish
// ---------------------------------------------------------------------------

function clip(x: number, lo = -1, hi = 1): number {
  return Math.max(lo, Math.min(hi, x));
}

function structureLean(bars: OHLCBar[]): number {
  const structure = classifyStructure(bars);
  if (structure === "HH_HL") return 0.8;
  if (structure === "LH_LL") return -0.8;
  return 0;
}

function trendLean(bars: OHLCBar[]): number {
  const snap = computeIndicatorSnapshot(bars);
  const price = bars[bars.length - 1]?.close;
  if (snap.ema12 === null || snap.ema26 === null || !price) return 0;
  const normSpread = (snap.ema12 - snap.ema26) / price;
  return clip(normSpread * 40); // empirically-scaled: ~2.5% spread saturates
}

function momentumLean(bars: OHLCBar[]): number {
  const snap = computeIndicatorSnapshot(bars);
  const rsiComponent =
    snap.rsi14 === null ? 0 : clip((snap.rsi14 - 50) / 30);
  const rocComponent = snap.roc10 === null ? 0 : clip(snap.roc10 / 5);
  return clip((rsiComponent + rocComponent) / 2);
}

function timeframeTrendLean(bars: OHLCBar[]): number {
  return trendLean(bars);
}

function volatilityScoreFor(bars: OHLCBar[]): number {
  // Volatility doesn't have a sign; it measures whether current conditions
  // are calm enough for the stop/target math to be meaningful. Extremely
  // high or extremely low volatility both reduce this factor's score.
  const regime = classifyRegime(bars);
  if (regime === "HIGH_VOLATILITY") return 30;
  if (regime === "LOW_VOLATILITY") return 45;
  if (regime === "BREAKOUT") return 60;
  return 75;
}

function volumeScoreFor(bars: OHLCBar[], direction: Direction): number {
  const snap = computeIndicatorSnapshot(bars);
  if (snap.volumeRatio20 === null) return 50;
  const lastTwo = bars.slice(-2);
  if (lastTwo.length < 2) return 50;
  const priceUp = lastTwo[1].close >= lastTwo[0].close;
  const volumeConfirms =
    (direction === "LONG" && priceUp) || (direction === "SHORT" && !priceUp);
  const ratio = snap.volumeRatio20;
  const magnitude = clip((ratio - 1) * 50, -50, 50); // >1x avg volume = support
  const base = 50 + (volumeConfirms ? magnitude : -magnitude);
  return Math.max(0, Math.min(100, base));
}

function supportResistanceScoreFor(
  bars: OHLCBar[],
  direction: Direction
): number {
  if (bars.length < SWING_WINDOW * 2) return 50;
  const window = bars.slice(-SWING_WINDOW * 2);
  const swingHigh = Math.max(...window.map((b) => b.high));
  const swingLow = Math.min(...window.map((b) => b.low));
  const price = bars[bars.length - 1].close;
  const range = swingHigh - swingLow;
  if (range <= 0) return 50;
  const positionInRange = (price - swingLow) / range; // 0 = at low, 1 = at high
  // Room to run in the trade's own direction scores higher.
  if (direction === "LONG") return Math.round((1 - positionInRange) * 100);
  if (direction === "SHORT") return Math.round(positionInRange * 100);
  return 50;
}

// ---------------------------------------------------------------------------
// Multi-timeframe direction
// ---------------------------------------------------------------------------

export interface MultiTimeframeInput {
  D1: OHLCBar[];
  "4H": OHLCBar[];
  "1H": OHLCBar[];
  "15M": OHLCBar[];
}

export interface DirectionResult {
  direction: Direction;
  weightedLean: number; // -1..1
  timeframeLeans: Record<Timeframe, number>;
  conflictingCount: number;
  multiTimeframeScore: number; // 0-100 alignment used as a factor score
}

export function determineDirection(mtf: MultiTimeframeInput): DirectionResult {
  const timeframes: Timeframe[] = ["D1", "4H", "1H", "15M"];
  const timeframeLeans = {} as Record<Timeframe, number>;
  for (const tf of timeframes) {
    const bars = mtf[tf];
    timeframeLeans[tf] = bars && bars.length >= 30 ? timeframeTrendLean(bars) : 0;
  }

  let weightedLean = 0;
  for (const tf of timeframes) {
    weightedLean += timeframeLeans[tf] * MTF_TIMEFRAME_WEIGHTS[tf];
  }

  const majoritySign = Math.sign(weightedLean);
  const conflictingCount = timeframes.filter(
    (tf) =>
      Math.sign(timeframeLeans[tf]) !== 0 &&
      majoritySign !== 0 &&
      Math.sign(timeframeLeans[tf]) !== majoritySign
  ).length;

  let direction: Direction = "WAIT";
  if (conflictingCount >= CONFLICT_WAIT_COUNT) {
    direction = "WAIT";
  } else if (weightedLean >= DIRECTION_THRESHOLD) {
    direction = "LONG";
  } else if (weightedLean <= -DIRECTION_THRESHOLD) {
    direction = "SHORT";
  }

  // Alignment score: how much of the total timeframe weight agrees with
  // the chosen direction (or, for WAIT, how balanced the tape is).
  let agreeingWeight = 0;
  for (const tf of timeframes) {
    const lean = timeframeLeans[tf];
    if (direction === "LONG" && lean > 0) agreeingWeight += MTF_TIMEFRAME_WEIGHTS[tf];
    if (direction === "SHORT" && lean < 0) agreeingWeight += MTF_TIMEFRAME_WEIGHTS[tf];
  }
  const multiTimeframeScore =
    direction === "WAIT" ? 50 - conflictingCount * 10 : agreeingWeight * 100;

  return {
    direction,
    weightedLean,
    timeframeLeans,
    conflictingCount,
    multiTimeframeScore: Math.max(0, Math.min(100, multiTimeframeScore)),
  };
}

// ---------------------------------------------------------------------------
// Factor scores + weighted total
// ---------------------------------------------------------------------------

function leanToAlignmentScore(lean: number, direction: Direction): number {
  if (direction === "WAIT") return 50 - Math.abs(lean) * 20;
  const sign = direction === "LONG" ? 1 : -1;
  // lean in [-1,1] aligned with direction sign maps to [0,100]
  return Math.max(0, Math.min(100, 50 + sign * lean * 50));
}

export function computeFactorScores(
  bars: OHLCBar[],
  direction: Direction,
  multiTimeframeScore: number,
  macroAvailable: boolean,
  macroLean: number | null
): FactorScores {
  return {
    structure: leanToAlignmentScore(structureLean(bars), direction),
    trend: leanToAlignmentScore(trendLean(bars), direction),
    momentum: leanToAlignmentScore(momentumLean(bars), direction),
    multiTimeframe: multiTimeframeScore,
    volatility: volatilityScoreFor(bars),
    volume: volumeScoreFor(bars, direction),
    supportResistance: supportResistanceScoreFor(bars, direction),
    macro:
      macroAvailable && macroLean !== null
        ? leanToAlignmentScore(macroLean, direction)
        : null,
  };
}

export function weightedScore(
  factors: FactorScores,
  weights: FactorWeights = DEFAULT_FACTOR_WEIGHTS
): number {
  const entries: [number, number | null][] = [
    [weights.structure, factors.structure],
    [weights.trend, factors.trend],
    [weights.momentum, factors.momentum],
    [weights.multiTimeframe, factors.multiTimeframe],
    [weights.volatility, factors.volatility],
    [weights.volume, factors.volume],
    [weights.supportResistance, factors.supportResistance],
    [weights.macro, factors.macro],
  ];
  let weightedSum = 0;
  let totalWeight = 0;
  for (const [w, score] of entries) {
    if (score === null) continue; // e.g. macro unavailable: excluded, not zeroed
    weightedSum += w * score;
    totalWeight += w;
  }
  if (totalWeight === 0) return 50;
  return Math.round((weightedSum / totalWeight) * 10) / 10;
}

export function confidenceFromScore(
  score: number,
  conflictingCount: number
): QualitativeConfidence {
  if (conflictingCount >= 2) return "LOW";
  if (score >= 70) return "HIGH";
  if (score >= 50) return "MODERATE";
  return "LOW";
}

// ---------------------------------------------------------------------------
// Full signal assembly
// ---------------------------------------------------------------------------

export interface GenerateSignalInput {
  symbol: string;
  timeframe: Timeframe; // the timeframe the signal is expressed on
  mtf: MultiTimeframeInput; // closed bars per timeframe, chronological
  weights?: FactorWeights;
  macroLean?: number | null; // supplied by macro module; null/undefined = unavailable
  dataStatus: "LIVE" | "DEMO" | "UNAVAILABLE";
  now?: number; // unix seconds, injectable for tests
  ticks?: Tick[]; // optional: chronological ticks for the order-flow read.
  // Omit entirely (not an empty array) when no tick feed is connected —
  // orderFlow will be null rather than a fabricated neutral reading.
}

const TIMEFRAME_EXPIRY_SECONDS: Record<Timeframe, number> = {
  "15M": 15 * 60 * 4,
  "1H": 60 * 60 * 4,
  "4H": 60 * 60 * 4 * 4,
  D1: 60 * 60 * 24 * 3,
};

export function generateSignal(input: GenerateSignalInput): TradingSignal {
  const bars = input.mtf[input.timeframe];
  if (!bars || bars.length < SWING_WINDOW * 2) {
    throw new Error(
      `generateSignal: not enough closed bars for ${input.symbol} ${input.timeframe}`
    );
  }
  const overlay = detectRegimeOverlay(bars);
  const weights = input.weights ?? routeStrategyWeights(overlay);
  const now = input.now ?? Math.floor(Date.now() / 1000);

  const dirResult = determineDirection(input.mtf);
  const macroAvailable = input.macroLean !== undefined && input.macroLean !== null;
  const factors = computeFactorScores(
    bars,
    dirResult.direction,
    dirResult.multiTimeframeScore,
    macroAvailable,
    macroAvailable ? (input.macroLean as number) : null
  );
  const score = weightedScore(factors, weights);
  const rawConfidence = confidenceFromScore(score, dirResult.conflictingCount);
  const confidence: QualitativeConfidence = overlay === "MEAN_REVERT" && rawConfidence === "HIGH" ? "MODERATE" : rawConfidence;
  const regime = classifyRegime(bars);
  const structure = classifyStructure(bars);

  const last = bars[bars.length - 1];
  const atrSeries = atr(bars, 14);
  const currentAtr = atrSeries[atrSeries.length - 1] ?? last.close * 0.005;
  const price = last.close;

  const entryBuffer = currentAtr * ATR_ENTRY_BUFFER_MULT;
  const entryZone = { low: price - entryBuffer, high: price + entryBuffer };

  const dirSign = dirResult.direction === "SHORT" ? -1 : 1;
  const highVolatility = percentileRank(atrSeries.filter((value): value is number => value !== null).slice(-120), currentAtr) >= 0.8;
  const stopAtrMultiple = highVolatility ? 2 : ATR_STOP_MULT;
  const invalidation = price - dirSign * currentAtr * stopAtrMultiple;
  const targets: [number, number, number] = [
    price + dirSign * currentAtr * ATR_TARGET_MULTS[0],
    price + dirSign * currentAtr * ATR_TARGET_MULTS[1],
    price + dirSign * currentAtr * ATR_TARGET_MULTS[2],
  ];

  const scenarios: [Scenario, Scenario, Scenario] = [
    {
      label: "BULL",
      description: "Trend/momentum factors continue to align; price extends.",
      targetPrice: price + Math.abs(currentAtr) * ATR_TARGET_MULTS[2],
    },
    {
      label: "BASE",
      description: "Price consolidates near the entry zone without a clean break.",
      targetPrice: price,
    },
    {
      label: "BEAR",
      description: "Structure fails and price reverses toward the stop.",
      targetPrice: price - Math.abs(currentAtr) * ATR_TARGET_MULTS[2],
    },
  ];

  const evidence: string[] = [
    `Structure: ${structure} over the last ${SWING_WINDOW * 2} bars.`,
    `Regime: ${regime}.`,
    `Multi-timeframe conflict count: ${dirResult.conflictingCount}/4.`,
    `ATR(14): ${currentAtr.toFixed(5)}.`,
    factors.macro === null
      ? "Macro factor unavailable; excluded from score, weight redistributed."
      : `Macro factor score: ${factors.macro.toFixed(1)}.`,
  ];

  const orderFlow =
    input.ticks && input.ticks.length > 0
      ? computeOrderFlowRead({
          symbol: input.symbol,
          bars,
          ticks: input.ticks,
          dataStatus: input.dataStatus,
        })
      : null;
  const engineeredFeatures = FeatureEngineer.compute(
    bars,
    orderFlowPressureScore(orderFlow?.pressure)
  );
  evidence.push(
    `Regime overlay: ${overlay}; ${overlay === "MEAN_REVERT" ? "counter-trend confidence is capped" : "trend and momentum weights are boosted"}.`,
    `ATR percentile: ${(engineeredFeatures.atr14Percentile * 100).toFixed(0)}%; research risk ${highVolatility ? "0.25" : "0.50"}%.`
  );

  return {
    symbol: input.symbol,
    timeframe: input.timeframe,
    generatedAt: now,
    orderFlow,
    direction: dirResult.direction,
    score,
    confidence,
    historicalWinProbability: null, // only calibration.ts may populate this
    factors,
    weightsUsed: weights,
    regime,
    structure,
    entryZone,
    invalidation,
    targets,
    evidence,
    scenarios,
    expiresAt: now + TIMEFRAME_EXPIRY_SECONDS[input.timeframe],
    dataStatus: input.dataStatus,
    regimeOverlay: overlay,
    engineeredFeatures,
    strategyMode: overlay === "MEAN_REVERT" ? "MEAN_REVERT" : "TREND",
    researchRiskPercent: highVolatility ? 0.25 : 0.5,
    stopAtrMultiple,
  };
}
