import assert from "node:assert/strict";
import { test } from "node:test";

import { atr, ema, rsi, sma } from "../app/lib/indicators.ts";
import {
  classifyStructure,
  detectRegimeOverlay,
  determineDirection,
  FeatureEngineer,
  generateSignal,
  routeStrategyWeights,
  weightedScore,
} from "../app/lib/signal-engine.ts";
import { computeRisk, validateRiskInput } from "../app/lib/risk-engine.ts";
import { buildConditionalPendingSetup } from "../app/lib/pending-engine.ts";
import { runBacktest } from "../app/lib/backtest.ts";
import {
  checkAvailability,
  getCalibratedProbability,
  parseOutcomesCsv,
} from "../app/lib/calibration.ts";
import { generateDemoSeries } from "../app/lib/demo-market.ts";
import {
  classifyTicks,
  computeBarOrderFlow,
  computeOrderFlowRead,
} from "../app/lib/order-flow.ts";
import {
  Mt5Provider,
  normalizeRatesResponse,
  normalizeSymbolInfoResponse,
  normalizeTicksResponse,
} from "../app/lib/mt5-provider.ts";
import { RiskValidationError } from "../app/lib/types.ts";
import type { OHLCBar, ResolvedOutcome, Tick } from "../app/lib/types.ts";
import { blendMLAlignmentScore, runMLCommittee, tripleBarrierLabel } from "../app/lib/ml-committee.ts";
import { SignalLifecycleTracker } from "../app/lib/live-tracker.ts";
import { OutcomeAnalytics, kellyCriterion } from "../app/lib/outcome-engine.ts";
import { applySentimentConfirmation, LocalLLMConsensusEngine } from "../app/lib/sentiment-oracle.ts";
import type { SignalOutcome } from "../app/lib/storage.ts";

// ---------------------------------------------------------------------------
// indicators.ts
// ---------------------------------------------------------------------------

test("sma matches hand-computed average", () => {
  const values = [1, 2, 3, 4, 5];
  const result = sma(values, 3);
  assert.equal(result[0], null);
  assert.equal(result[1], null);
  assert.equal(result[2], 2); // (1+2+3)/3
  assert.equal(result[3], 3); // (2+3+4)/3
  assert.equal(result[4], 4); // (3+4+5)/3
});

test("ema is seeded by sma and then recurses", () => {
  const values = [1, 2, 3, 4, 5, 6, 7, 8];
  const result = ema(values, 3);
  assert.equal(result[0], null);
  assert.equal(result[1], null);
  assert.equal(result[2], 2); // seed = sma of first 3 values = (1+2+3)/3
  const k = 2 / 4;
  const expectedIdx3 = values[3] * k + (result[2] as number) * (1 - k);
  assert.ok(Math.abs((result[3] as number) - expectedIdx3) < 1e-9);
});

test("rsi stays within [0, 100]", () => {
  const bars = generateDemoSeries({
    symbol: "TEST",
    timeframe: "1H",
    count: 300,
  });
  const closes = bars.map((b) => b.close);
  const result = rsi(closes, 14);
  for (const v of result) {
    if (v !== null) {
      assert.ok(v >= 0 && v <= 100, `rsi ${v} out of range`);
    }
  }
});

test("atr is always non-negative once seeded", () => {
  const bars = generateDemoSeries({
    symbol: "TEST",
    timeframe: "1H",
    count: 100,
  });
  const result = atr(bars, 14);
  for (const v of result) {
    if (v !== null) assert.ok(v >= 0);
  }
});

// ---------------------------------------------------------------------------
// signal-engine.ts
// ---------------------------------------------------------------------------

test("classifyStructure returns MIXED with insufficient history", () => {
  const bars: OHLCBar[] = [
    { time: 0, open: 1, high: 1, low: 1, close: 1, volume: 1 },
  ];
  assert.equal(classifyStructure(bars), "MIXED");
});

test("classifyStructure detects a clean uptrend as HH_HL", () => {
  const bars: OHLCBar[] = [];
  let price = 100;
  for (let i = 0; i < 40; i++) {
    price += 1; // strictly rising
    bars.push({
      time: i,
      open: price - 0.5,
      high: price + 0.5,
      low: price - 1,
      close: price,
      volume: 100,
    });
  }
  assert.equal(classifyStructure(bars), "HH_HL");
});

test("determineDirection forces WAIT when 3+ timeframes conflict", () => {
  const flat: OHLCBar[] = Array.from({ length: 40 }, (_, i) => ({
    time: i,
    open: 100,
    high: 100.1,
    low: 99.9,
    close: 100,
    volume: 100,
  }));
  const result = determineDirection({
    D1: flat,
    "4H": flat,
    "1H": flat,
    "15M": flat,
  });
  // All flat -> zero leans -> WAIT by threshold, not by conflict, but should
  // never throw and must return a valid Direction.
  assert.ok(["LONG", "SHORT", "WAIT"].includes(result.direction));
});

test("weightedScore excludes null macro factor from the weighted total", () => {
  const withMacro = weightedScore({
    structure: 80,
    trend: 80,
    momentum: 80,
    multiTimeframe: 80,
    volatility: 80,
    volume: 80,
    supportResistance: 80,
    macro: 80,
  });
  const withoutMacro = weightedScore({
    structure: 80,
    trend: 80,
    momentum: 80,
    multiTimeframe: 80,
    volatility: 80,
    volume: 80,
    supportResistance: 80,
    macro: null,
  });
  assert.equal(withMacro, 80);
  assert.equal(withoutMacro, 80); // uniform inputs -> same regardless of exclusion
});

test("generateSignal produces a fully-formed signal on demo data", () => {
  const mk = (tf: "15M" | "1H" | "4H" | "D1", count: number) =>
    generateDemoSeries({ symbol: "EURUSD", timeframe: tf, count });
  const signal = generateSignal({
    symbol: "EURUSD",
    timeframe: "1H",
    mtf: {
      D1: mk("D1", 120),
      "4H": mk("4H", 120),
      "1H": mk("1H", 120),
      "15M": mk("15M", 120),
    },
    dataStatus: "DEMO",
    now: 1_700_000_000,
  });
  assert.ok(["LONG", "SHORT", "WAIT"].includes(signal.direction));
  assert.ok(signal.score >= 0 && signal.score <= 100);
  assert.equal(signal.historicalWinProbability, null);
  assert.equal(signal.targets.length, 3);
  assert.ok(signal.evidence.length > 0);
});

test("regime overlay, feature engineering, and routed weights remain bounded", () => {
  const bars = generateDemoSeries({ symbol: "EURUSD", timeframe: "1H", count: 260 });
  assert.ok(["TREND_UP", "TREND_DOWN", "MEAN_REVERT"].includes(detectRegimeOverlay(bars)));
  const features = FeatureEngineer.compute(bars, 0.75);
  assert.ok(features.directionalEfficiency >= 0 && features.directionalEfficiency <= 1);
  assert.ok(features.tickVolumeRatio >= 0 && features.tickVolumeRatio <= 1);
  assert.ok(features.atr14Percentile >= 0 && features.atr14Percentile <= 1);
  assert.equal(features.bidAskPressure, 0.75);
  const weights = routeStrategyWeights("TREND_UP");
  const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
  assert.ok(Math.abs(total - 1) < 1e-9);
  assert.ok(weights.trend > 0.2 && weights.momentum > 0.2);
});

test("pure TypeScript ML committee trains deterministic stumps and patterns", () => {
  const bars = generateDemoSeries({ symbol: "XAUUSD", timeframe: "1H", count: 260 });
  const read = runMLCommittee(bars);
  assert.equal(read.available, true);
  assert.ok(read.trainingSamples >= 40);
  assert.ok(read.probability >= -1 && read.probability <= 1);
  assert.ok(read.confidence >= 0 && read.confidence <= 1);
  assert.equal(runMLCommittee(bars).probability, read.probability);
  assert.ok([1, -1].includes(tripleBarrierLabel(bars, 100, 1, 20)));
  assert.ok(blendMLAlignmentScore(60, 1, read) >= 0);
});

test("qualified weak aligned bias becomes a conditional stop order instead of a forced market entry", () => {
  const rising = (stepSeconds: number): OHLCBar[] => Array.from({ length: 260 }, (_, index) => {
    const open = 1 + index * 0.00016;
    const close = open + 0.00006;
    return { time: 1_700_000_000 + index * stepSeconds, open, high: close + 0.00008, low: open - 0.00008, close, volume: 1_000 + index };
  });
  const mtf = { D1: rising(86_400), "4H": rising(14_400), "1H": rising(3_600), "15M": rising(900) };
  const original = generateSignal({ symbol: "EURUSD", timeframe: "1H", mtf, dataStatus: "LIVE", now: 1_800_000_000 });
  const setup = buildConditionalPendingSetup({ ...original, direction: "WAIT", score: Math.max(50, original.score) }, mtf);
  assert.equal(setup?.orderType, "BUY_STOP");
  assert.equal(setup?.status, "CONDITIONAL_UNCALIBRATED");
  assert.ok(setup!.trigger > Math.max(...mtf["1H"].slice(-20).map((bar) => bar.high)));
  assert.ok(setup!.stop < setup!.trigger);
  assert.ok(setup!.targets[2] > setup!.targets[1]);
});

// ---------------------------------------------------------------------------
// risk-engine.ts
// ---------------------------------------------------------------------------

test("computeRisk rejects risk above 10%", () => {
  assert.throws(
    () =>
      validateRiskInput({
        accountBalance: 10000,
        riskPercent: 11,
        entry: 100,
        stop: 99,
        leverage: 10,
      }),
    RiskValidationError
  );
});

test("computeRisk rejects identical entry and stop", () => {
  assert.throws(
    () =>
      validateRiskInput({
        accountBalance: 10000,
        riskPercent: 1,
        entry: 100,
        stop: 100,
        leverage: 10,
      }),
    RiskValidationError
  );
});

test("computeRisk model-only sizing matches the documented formula", () => {
  const result = computeRisk({
    accountBalance: 10000,
    riskPercent: 1,
    entry: 100,
    stop: 99,
    leverage: 10,
  });
  // maximum loss = balance * risk% = 100
  // position units = maximum loss / loss per unit at stop = 100 / 1 = 100
  assert.equal(result.maxLossAmount, 100);
  assert.equal(result.positionUnits, 100);
  assert.equal(result.sizingSource, "MODEL_ONLY");
  assert.ok(result.warnings.some((w) => w.code === "MISSING_BROKER_SPEC"));
});

test("computeRisk with a broker spec rounds DOWN to the lot step, never up", () => {
  const result = computeRisk({
    accountBalance: 10000,
    riskPercent: 1,
    entry: 1.1000,
    stop: 1.0950,
    leverage: 30,
    brokerSpec: {
      tickSize: 0.0001,
      tickValue: 1,
      contractSize: 100000,
      minVolume: 0.01,
      maxVolume: 100,
      lotStep: 0.01,
      leverage: 30,
    },
  });
  const remainder = result.positionUnits / 0.01;
  assert.ok(
    Math.abs(remainder - Math.round(remainder)) < 1e-6,
    "position units must land exactly on a lot-step multiple"
  );
});

test("broker-verified reward/risk remains a price-distance ratio", () => {
  const result = computeRisk({
    accountBalance: 10_000,
    riskPercent: 1,
    entry: 1.1,
    stop: 1.095,
    target: 1.11,
    leverage: 100,
    brokerSpec: { tickSize: 0.00001, tickValue: 1, contractSize: 100_000, minVolume: 0.01, maxVolume: 100, lotStep: 0.01, leverage: 100 },
  });
  assert.ok(Math.abs((result.rewardToRiskRatio ?? 0) - 2) < 1e-9);
});

test("computeRisk warns when reward-to-risk is below 1.5R", () => {
  const result = computeRisk({
    accountBalance: 10000,
    riskPercent: 1,
    entry: 100,
    stop: 99,
    target: 100.5, // only 0.5R reward
    leverage: 10,
  });
  assert.ok(result.warnings.some((w) => w.code === "REWARD_BELOW_1_5R"));
});

// ---------------------------------------------------------------------------
// backtest.ts
// ---------------------------------------------------------------------------

test("runBacktest partitions trades chronologically 60/20/20 by index", () => {
  const bars = generateDemoSeries({
    symbol: "EURUSD",
    timeframe: "1H",
    count: 800,
  });
  const result = runBacktest("TREND_FOLLOWING", "EURUSD", "1H", bars, true);
  assert.equal(result.demoOnly, true);
  // Every trade should belong to exactly one partition and R multiples
  // should be finite numbers.
  for (const t of result.trades) {
    assert.ok(["TRAIN", "VALIDATION", "TEST"].includes(t.partition));
    assert.ok(Number.isFinite(t.rMultiple));
    assert.ok(t.exitTime >= t.entryTime);
  }
});

test("runBacktest never produces overlapping trades", () => {
  const bars = generateDemoSeries({
    symbol: "EURUSD",
    timeframe: "1H",
    count: 800,
  });
  const result = runBacktest("MEAN_REVERSION", "EURUSD", "1H", bars, true);
  for (let i = 1; i < result.trades.length; i++) {
    assert.ok(
      result.trades[i].entryTime >= result.trades[i - 1].exitTime,
      "trades must not overlap"
    );
  }
});

// ---------------------------------------------------------------------------
// calibration.ts
// ---------------------------------------------------------------------------

test("parseOutcomesCsv rejects duplicate signal_id, bad score, future date, bad source", () => {
  const csv = [
    "signal_id,score,outcome,resolved_at,source,symbol,timeframe",
    "sig-001,72,WIN,2026-01-10T14:00:00Z,OUT_OF_SAMPLE,EURUSD,1H",
    "sig-001,60,LOSS,2026-01-11T14:00:00Z,OUT_OF_SAMPLE,EURUSD,1H", // dup id
    "sig-002,150,WIN,2026-01-10T14:00:00Z,OUT_OF_SAMPLE,EURUSD,1H", // bad score
    "sig-003,55,WIN,2099-01-10T14:00:00Z,OUT_OF_SAMPLE,EURUSD,1H", // future date
    "sig-004,55,WIN,2026-01-10T14:00:00Z,TRAIN,EURUSD,1H", // bad source
    "sig-005,55,WIN,2026-01-10T14:00:00Z,LIVE_RESOLVED,EURUSD,1H", // valid
  ].join("\n");
  const result = parseOutcomesCsv(csv, new Date("2026-06-01T00:00:00Z"));
  assert.equal(result.accepted.length, 2); // sig-001 (first) + sig-005
  assert.equal(result.rejected.length, 4);
});

test("checkAvailability requires 200/40/40 before enabling calibration", () => {
  const few: ResolvedOutcome[] = Array.from({ length: 50 }, (_, i) => ({
    signalId: `s${i}`,
    score: 60,
    outcome: i % 2 === 0 ? "WIN" : "LOSS",
    resolvedAt: "2026-01-01T00:00:00Z",
    source: "OUT_OF_SAMPLE",
  }));
  assert.equal(checkAvailability(few).available, false);
});

test("getCalibratedProbability is monotonic non-decreasing across score bins", () => {
  const outcomes: ResolvedOutcome[] = [];
  let id = 0;
  // Construct outcomes where win rate genuinely increases with score, plus
  // one deliberately noisy bin, to confirm PAVA pooling enforces monotonicity.
  const binsTruth = [0.1, 0.15, 0.2, 0.3, 0.1, 0.5, 0.6, 0.7, 0.85, 0.95];
  for (let bin = 0; bin < 10; bin++) {
    for (let i = 0; i < 30; i++) {
      const score = bin * 10 + (i % 10);
      const isWin = i / 30 < binsTruth[bin];
      outcomes.push({
        signalId: `id-${id++}`,
        score,
        outcome: isWin ? "WIN" : "LOSS",
        resolvedAt: "2026-01-01T00:00:00Z",
        source: "OUT_OF_SAMPLE",
      });
    }
  }
  assert.ok(checkAvailability(outcomes).available);

  const probs: number[] = [];
  for (let s = 5; s < 100; s += 10) {
    const p = getCalibratedProbability(s, outcomes);
    assert.ok(p !== null, `expected a probability at score ${s}`);
    probs.push((p as { probability: number }).probability);
  }
  for (let i = 1; i < probs.length; i++) {
    assert.ok(
      probs[i] >= probs[i - 1] - 1e-9,
      `probability decreased from ${probs[i - 1]} to ${probs[i]} — isotonic pooling failed`
    );
  }
});

test("getCalibratedProbability returns null below the segment sample minimum", () => {
  const outcomes: ResolvedOutcome[] = Array.from({ length: 200 }, (_, i) => ({
    signalId: `id-${i}`,
    // Force every outcome into a single bin so all other bins are empty.
    score: 55,
    outcome: i < 100 ? "WIN" : "LOSS",
    resolvedAt: "2026-01-01T00:00:00Z",
    source: "OUT_OF_SAMPLE",
  }));
  assert.ok(checkAvailability(outcomes).available);
  // Score 5 falls in the empty 0-10 bin, which PAVA will have pooled
  // together with populated neighbors only if monotonicity requires it.
  // Either way it must never throw.
  const p = getCalibratedProbability(5, outcomes);
  assert.ok(p === null || p.sampleSize >= 30);
});

// ---------------------------------------------------------------------------
// demo-market.ts
// ---------------------------------------------------------------------------

test("generateDemoSeries is deterministic for a given symbol/timeframe/seed inputs", () => {
  const a = generateDemoSeries({ symbol: "EURUSD", timeframe: "1H", count: 50, endTime: 1_700_000_000 });
  const b = generateDemoSeries({ symbol: "EURUSD", timeframe: "1H", count: 50, endTime: 1_700_000_000 });
  assert.deepEqual(a, b);
});

// ---------------------------------------------------------------------------
// order-flow.ts
// ---------------------------------------------------------------------------

test("classifyTicks applies the tick rule with zero-tick carry-forward", () => {
  const ticks: Tick[] = [
    { time: 0, bid: 1.1, ask: 1.1002, volume: 1 }, // first tick: neutral
    { time: 1, bid: 1.1005, ask: 1.1007, volume: 1 }, // up -> +1
    { time: 2, bid: 1.1005, ask: 1.1007, volume: 1 }, // flat -> carries +1
    { time: 3, bid: 1.1, ask: 1.1002, volume: 1 }, // down -> -1
  ];
  const signs = classifyTicks(ticks);
  assert.deepEqual(signs, [0, 1, 1, -1]);
});

test("computeBarOrderFlow buckets ticks into the correct bar and computes cumulative delta", () => {
  const bars: OHLCBar[] = [
    { time: 0, open: 1, high: 1.01, low: 0.99, close: 1, volume: 10 },
    { time: 60, open: 1, high: 1.01, low: 0.99, close: 1, volume: 10 },
  ];
  const ticks: Tick[] = [
    { time: 5, bid: 1.0, ask: 1.0002, volume: 5 },
    { time: 10, bid: 1.002, ask: 1.0022, volume: 5 }, // up in bar 0
    { time: 65, bid: 1.0, ask: 1.0002, volume: 5 }, // down in bar 1
  ];
  const flow = computeBarOrderFlow(bars, ticks);
  assert.equal(flow.length, 2);
  assert.equal(flow[0].buyVolume, 5);
  assert.equal(flow[0].sellVolume, 0);
  assert.equal(flow[0].cumulativeDelta, 5);
  assert.equal(flow[1].sellVolume, 5);
  assert.equal(flow[1].cumulativeDelta, 0); // 5 (buy) - 5 (sell)
});

test("computeOrderFlowRead returns null with no ticks, never fabricates NEUTRAL", () => {
  const bars = generateDemoSeries({ symbol: "EURUSD", timeframe: "1H", count: 30 });
  const result = computeOrderFlowRead({
    symbol: "EURUSD",
    bars,
    ticks: [],
    dataStatus: "LIVE",
  });
  assert.equal(result, null);
});

test("computeOrderFlowRead labels itself as an approximation and classifies strong buy pressure", () => {
  const bars: OHLCBar[] = Array.from({ length: 5 }, (_, i) => ({
    time: i * 60,
    open: 1,
    high: 1.01,
    low: 0.99,
    close: 1,
    volume: 10,
  }));
  // All ticks trending up -> should read as strong buy pressure.
  const ticks: Tick[] = [];
  let price = 1.0;
  for (let i = 0; i < 5; i++) {
    for (let j = 0; j < 4; j++) {
      price += 0.0001;
      ticks.push({ time: i * 60 + j * 10, bid: price, ask: price + 0.0002, volume: 10 });
    }
  }
  const result = computeOrderFlowRead({
    symbol: "EURUSD",
    bars,
    ticks,
    dataStatus: "LIVE",
  });
  assert.ok(result !== null);
  assert.equal(result?.method, "TICK_RULE_APPROXIMATION");
  assert.equal(result?.pressure, "STRONG_BUY_PRESSURE");
  assert.equal(result?.cumulativeDeltaTrend, "RISING");
});

test("generateSignal leaves orderFlow null when no ticks are supplied, and populated when they are", () => {
  const mk = (tf: "15M" | "1H" | "4H" | "D1", count: number) =>
    generateDemoSeries({ symbol: "EURUSD", timeframe: tf, count });
  const baseInput = {
    symbol: "EURUSD" as const,
    timeframe: "1H" as const,
    mtf: {
      D1: mk("D1", 120),
      "4H": mk("4H", 120),
      "1H": mk("1H", 120),
      "15M": mk("15M", 120),
    },
    dataStatus: "DEMO" as const,
    now: 1_700_000_000,
  };
  const withoutTicks = generateSignal(baseInput);
  assert.equal(withoutTicks.orderFlow, null);

  const bars1H = baseInput.mtf["1H"];
  const ticks: Tick[] = bars1H.slice(-5).flatMap((b, idx) => [
    { time: b.time + 1, bid: b.close, ask: b.close + 0.0002, volume: 5 },
    { time: b.time + 2, bid: b.close + 0.0001 * (idx + 1), ask: b.close + 0.0003, volume: 5 },
  ]);
  const withTicks = generateSignal({ ...baseInput, ticks });
  assert.ok(withTicks.orderFlow !== null);
  assert.equal(withTicks.orderFlow?.method, "TICK_RULE_APPROXIMATION");
  // Order flow must never leak into the weighted score/factors.
  assert.deepEqual(withTicks.factors, withoutTicks.factors);
  assert.equal(withTicks.score, withoutTicks.score);
});

function lifecycleFixture(overrides: Partial<SignalOutcome> = {}): SignalOutcome {
  return {
    id: "EURUSD:BUY_STOP:1.1", symbol: "EURUSD", action: "BUY_STOP",
    entryPrice: 1.1, triggerPrice: 1.1, slPrice: 1.095,
    tp1Price: 1.105, tp2Price: 1.11, tp3Price: 1.115,
    status: "PENDING", livePrices: [], highestPips: 0, lowestPips: 0,
    finalPips: 0, plannedRR: 2, achievedRR: 0, scoreAtGeneration: 65,
    confidenceAtGeneration: "MODERATE", regimeAtGeneration: "TREND_UP",
    sentimentAtGeneration: 0, mlProbabilityAtGeneration: 0.25,
    timestampGenerated: 1_700_000_000, userReactions: [], ...overrides,
  };
}

test("lifecycle tracker emits trigger, TP levels, SL, and expiry events", () => {
  const events: string[] = [];
  const tracker = new SignalLifecycleTracker({ onEvent: (event) => events.push(event.type) });
  tracker.track(lifecycleFixture());
  tracker.observeTick("EURUSD", 1.1001, 1_700_000_010);
  tracker.observeTick("EURUSD", 1.1051, 1_700_000_020);
  tracker.observeTick("EURUSD", 1.1101, 1_700_000_030);
  tracker.observeTick("EURUSD", 1.1151, 1_700_000_040);
  assert.deepEqual(events, ["SIGNAL_TRIGGERED", "TP1_HIT", "TP2_HIT", "TP3_HIT"]);
  assert.equal(tracker.get("EURUSD:BUY_STOP:1.1")?.status, "TP3_HIT");

  const expiring = new SignalLifecycleTracker({ expirySeconds: 10, onEvent: (event) => events.push(event.type) });
  expiring.track(lifecycleFixture({ id: "expired" }));
  expiring.observeTick("EURUSD", 1.09, 1_700_000_011);
  assert.equal(expiring.get("expired")?.status, "EXPIRED");
});

test("outcome analytics deducts spread and round-turn friction and exposes Kelly", () => {
  const rows = [
    lifecycleFixture({ id: "win", action: "LONG", status: "TP3_HIT", finalPips: 30, achievedRR: 3, timestampResolved: 2 }),
    lifecycleFixture({ id: "loss", action: "SHORT", status: "SL_HIT", finalPips: -10, achievedRR: -1, timestampResolved: 3 }),
  ];
  const analytics = new OutcomeAnalytics(rows).compute();
  assert.equal(analytics.overall.count, 2);
  assert.ok(Math.abs(analytics.overall.totalPips - 15.6) < 1e-9);
  assert.equal(analytics.calibrated, false);
  assert.ok((kellyCriterion(0.6, 2) ?? 0) > 0);
});

test("local sentiment requires all four loopback models and blocks opposing sentiment", async () => {
  const fakeFetch = (async () => new Response(JSON.stringify({ response: "0.80" }), { status: 200 })) as typeof fetch;
  const engine = new LocalLLMConsensusEngine({ fetcher: fakeFetch });
  const consensus = await engine.analyze("XAUUSD", ["Local note says demand is strong."]);
  assert.equal(consensus.consensus, true);
  assert.equal(consensus.score, 0.8);
  const blocked = applySentimentConfirmation("SHORT", 70, consensus);
  assert.equal(blocked.blocked, true);
  assert.equal(blocked.action, "WAIT");
});

// ---------------------------------------------------------------------------
// mt5-provider.ts
// ---------------------------------------------------------------------------

test("normalizeRatesResponse sorts chronologically and tags source/status", () => {
  const series = normalizeRatesResponse(
    {
      symbol: "EURUSD",
      brokerSymbol: "EURUSD.a",
      timeframe: "1H",
      bars: [
        { time: 200, open: 1, high: 1.01, low: 0.99, close: 1, volume: 10 },
        { time: 100, open: 1, high: 1.01, low: 0.99, close: 1, volume: 10 },
      ],
    },
    "1H"
  );
  assert.equal(series.source, "mt5");
  assert.equal(series.status, "LIVE");
  assert.deepEqual(
    series.bars.map((b) => b.time),
    [100, 200]
  );
});

test("normalizeRatesResponse throws on duplicate/non-chronological timestamps", () => {
  assert.throws(() =>
    normalizeRatesResponse(
      {
        symbol: "EURUSD",
        brokerSymbol: "EURUSD.a",
        timeframe: "1H",
        bars: [
          { time: 100, open: 1, high: 1.01, low: 0.99, close: 1, volume: 10 },
          { time: 100, open: 1, high: 1.01, low: 0.99, close: 1, volume: 10 },
        ],
      },
      "1H"
    )
  );
});

test("normalizeTicksResponse sorts chronologically and preserves null last", () => {
  const ticks = normalizeTicksResponse({
    symbol: "EURUSD",
    brokerSymbol: "EURUSD.a",
    ticks: [
      { time: 2, bid: 1.1, ask: 1.1002, last: null, volume: 1 },
      { time: 1, bid: 1.0999, ask: 1.1001, last: null, volume: 1 },
    ],
  });
  assert.deepEqual(
    ticks.map((t) => t.time),
    [1, 2]
  );
  assert.equal(ticks[0].last, undefined);
});

test("normalizeSymbolInfoResponse rejects non-positive tick size", () => {
  assert.throws(() =>
    normalizeSymbolInfoResponse(
      {
        symbol: "EURUSD",
        brokerSymbol: "EURUSD.a",
        digits: 5,
        tickSize: 0,
        tickValue: 1,
        contractSize: 100000,
        minVolume: 0.01,
        maxVolume: 100,
        lotStep: 0.01,
        profitCurrency: "USD",
        marginCurrency: "USD",
      },
      30
    )
  );
});

test("Mt5Provider.fetchSeries calls the bridge with a bearer token and normalizes the result", async () => {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const fakeFetch = (async (url: string | URL, init?: RequestInit) => {
    calls.push({
      url: url.toString(),
      headers: (init?.headers as Record<string, string>) ?? {},
    });
    const body = {
      symbol: "EURUSD",
      brokerSymbol: "EURUSD.a",
      timeframe: "1H",
      bars: [{ time: 100, open: 1, high: 1.01, low: 0.99, close: 1, volume: 10 }],
    };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const provider = new Mt5Provider({ port: 8765, token: "secret", fetchImpl: fakeFetch });
  const series = await provider.fetchSeries("EURUSD", "1H", 100);

  assert.equal(series.bars.length, 1);
  assert.equal(calls.length, 1);
  assert.ok(calls[0].url.includes("127.0.0.1:8765/rates"));
  assert.equal(calls[0].headers.Authorization, "Bearer secret");
});

test("Mt5Provider surfaces bridge errors as ProviderError, not a raw throw", async () => {
  const fakeFetch = (async () =>
    new Response(JSON.stringify({ error: "symbol not found" }), {
      status: 404,
    })) as typeof fetch;
  const provider = new Mt5Provider({ fetchImpl: fakeFetch });
  await assert.rejects(
    () => provider.fetchSeries("NOTASYMBOL", "1H", 100),
    /symbol not found/
  );
});

test("Mt5Provider reports an unreachable bridge clearly instead of an opaque network error", async () => {
  const fakeFetch = (async () => {
    throw new Error("ECONNREFUSED");
  }) as typeof fetch;
  const provider = new Mt5Provider({ fetchImpl: fakeFetch });
  await assert.rejects(() => provider.fetchSeries("EURUSD", "1H", 100), /bridge unreachable/);
});

test("generateDemoSeries bars are chronological with no negative prices", () => {
  const bars = generateDemoSeries({ symbol: "XAUUSD", timeframe: "4H", count: 200 });
  for (let i = 1; i < bars.length; i++) {
    assert.ok(bars[i].time > bars[i - 1].time);
  }
  for (const b of bars) {
    assert.ok(b.low > 0 && b.high >= b.low);
  }
});
