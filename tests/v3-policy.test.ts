import assert from "node:assert/strict";
import { test } from "node:test";

import {
  brierScore,
  calculateExpectancy,
  evaluatePublication,
  expectedCalibrationError,
  probabilityGrade,
  selectBestTimeframe,
  validateMarketData,
  validateQuantEvidence,
} from "../app/lib/v3/index.ts";
import type {
  PublicationCandidate,
  PublicationDecision,
  QuantValidationInput,
  TimeframeCandidate,
} from "../app/lib/v3/index.ts";
import type { OHLCBar, Timeframe } from "../app/lib/types.ts";

const NOW = 1_800_000_000;

function bars(timeframe: Timeframe, count = 80): OHLCBar[] {
  const step = timeframe === "15M" ? 900 : timeframe === "1H" ? 3_600 : timeframe === "4H" ? 14_400 : 86_400;
  const start = NOW - step * count;
  return Array.from({ length: count }, (_, index) => {
    const open = 100 + index * 0.01;
    const close = open + 0.005;
    return { time: start + step * (index + 1), open, high: close + 0.01, low: open - 0.01, close, volume: 100 + index };
  });
}

function quantInput(overrides: Partial<QuantValidationInput> = {}): QuantValidationInput {
  return {
    calibratedProbability: 0.82,
    calibrationSampleSize: 400,
    brierScore: 0.15,
    expectedCalibrationError: 0.04,
    outOfSample: true,
    regimeStable: true,
    sensitivityStable: true,
    modelDriftDetected: false,
    expectedValue: 0.35,
    costsIncluded: true,
    ...overrides,
  };
}

function candidate(overrides: Partial<PublicationCandidate> = {}): PublicationCandidate {
  const dataQuality = validateMarketData({ bars: bars("1H"), timeframe: "1H", dataStatus: "LIVE", source: "mt5", now: NOW });
  return {
    signalId: "atlas-v3-test",
    instrument: "EURUSD",
    assetClass: "FOREX",
    timeframe: "1H",
    proposedAction: "BUY",
    generatedAt: NOW - 60,
    expiresAt: NOW + 3_600,
    modelStatus: "APPROVED",
    quant: validateQuantEvidence(quantInput()),
    dataQuality,
    rewardRisk: 2,
    regimeValid: true,
    liquidityAdequate: true,
    capacityAdequate: true,
    spreadAcceptable: true,
    eventRiskAcceptable: true,
    structuralStopValid: true,
    targetValid: true,
    unresolvedCriticalContradiction: false,
    ...overrides,
  };
}

function activeDecision(timeframe: Timeframe, probability: number, expectedValue: number): PublicationDecision {
  return evaluatePublication(candidate({
    timeframe,
    quant: validateQuantEvidence(quantInput({ calibratedProbability: probability, expectedValue })),
  }), NOW);
}

function timeframeCandidate(timeframe: Timeframe, publication: PublicationDecision, quality: number): TimeframeCandidate {
  return {
    timeframe,
    publication,
    regimeFit: quality,
    signalToNoise: quality,
    liquidity: quality,
    structureClarity: quality,
    macroAlignment: quality,
    llmAgreement: quality,
    volatilityFit: quality,
    dataQualityScore: quality,
    eventRiskScore: quality,
  };
}

test("V3 probability grades enforce exact B/A/A+/A+++++ boundaries", () => {
  assert.equal(probabilityGrade(0.7999), null);
  assert.equal(probabilityGrade(0.80), "B");
  assert.equal(probabilityGrade(0.8499), "B");
  assert.equal(probabilityGrade(0.85), "A");
  assert.equal(probabilityGrade(0.90), "A+");
  assert.equal(probabilityGrade(0.95), "A+++++");
});

test("79.99 percent is WAIT while 80 percent can pass only with every gate", () => {
  const below = evaluatePublication(candidate({ quant: validateQuantEvidence(quantInput({ calibratedProbability: 0.7999 })) }), NOW);
  assert.equal(below.action, "WAIT");
  assert.ok(below.rejectionReasons.includes("CALIBRATED_PROBABILITY_BELOW_80_PERCENT"));
  const threshold = evaluatePublication(candidate({ quant: validateQuantEvidence(quantInput({ calibratedProbability: 0.80 })) }), NOW);
  assert.equal(threshold.action, "BUY");
  assert.equal(threshold.probabilityGrade, "B");
});

test("99 percent plus Quant veto is always WAIT", () => {
  const quant = validateQuantEvidence(quantInput({ calibratedProbability: 0.99, modelDriftDetected: true }));
  const result = evaluatePublication(candidate({ quant }), NOW);
  assert.equal(quant.status, "VETO");
  assert.equal(result.action, "WAIT");
  assert.ok(result.rejectionReasons.includes("MODEL_DRIFT_DETECTED"));
});

test("95 percent plus negative expectancy is always WAIT", () => {
  const expectation = calculateExpectancy({ winProbability: 0.95, expectedWin: 0.1, expectedLoss: 3, spreadCost: 0.02, slippageCost: 0.01, commissionCost: 0.01 });
  assert.ok(expectation.netExpectedValue < 0);
  const quant = validateQuantEvidence(quantInput({ calibratedProbability: 0.95, expectedValue: expectation.netExpectedValue }));
  const result = evaluatePublication(candidate({ quant }), NOW);
  assert.equal(result.action, "WAIT");
  assert.ok(result.rejectionReasons.includes("NON_POSITIVE_EXPECTANCY"));
});

test("critical event risk, demo data, and stale data fail closed", () => {
  const event = evaluatePublication(candidate({ eventRiskAcceptable: false }), NOW);
  assert.equal(event.action, "WAIT");
  const demoQuality = validateMarketData({ bars: bars("1H"), timeframe: "1H", dataStatus: "DEMO", source: "deterministic-demo", now: NOW });
  assert.equal(evaluatePublication(candidate({ dataQuality: demoQuality }), NOW).action, "WAIT");
  const staleQuality = validateMarketData({ bars: bars("1H"), timeframe: "1H", dataStatus: "LIVE", source: "mt5", now: NOW + 20_000 });
  assert.ok(staleQuality.reasons.includes("STALE_DATA"));
  assert.equal(evaluatePublication(candidate({ dataQuality: staleQuality }), NOW).action, "WAIT");
});

test("data quality detects impossible prices, duplicates, gaps, and disagreement", () => {
  const rows = bars("1H");
  rows[10] = { ...rows[10], high: rows[10].low - 1 };
  rows[20] = { ...rows[20], time: rows[19].time };
  rows[30] = { ...rows[30], time: rows[29].time + 20_000 };
  const report = validateMarketData({ bars: rows, timeframe: "1H", dataStatus: "LIVE", source: "mt5", now: NOW, referenceClose: 120 });
  assert.equal(report.status, "FAIL");
  assert.ok(report.reasons.includes("IMPOSSIBLE_OHLC"));
  assert.ok(report.reasons.includes("DUPLICATE_TIMESTAMPS"));
  assert.ok(report.reasons.includes("TIMESTAMP_GAPS"));
  assert.ok(report.reasons.includes("PROVIDER_DISAGREEMENT"));
});

test("Brier score and ECE are deterministic calibration diagnostics", () => {
  const observations = [
    { probability: 0.8, outcome: 1 as const },
    { probability: 0.7, outcome: 1 as const },
    { probability: 0.2, outcome: 0 as const },
    { probability: 0.1, outcome: 0 as const },
  ];
  assert.ok((brierScore(observations) ?? 1) < 0.1);
  assert.ok((expectedCalibrationError(observations, 5) ?? 1) < 0.3);
});

test("timeframe selector chooses strongest validated evidence, not shortest timeframe", () => {
  const m15 = timeframeCandidate("15M", activeDecision("15M", 0.81, 0.2), 0.55);
  const h4 = timeframeCandidate("4H", activeDecision("4H", 0.88, 0.6), 0.90);
  const selection = selectBestTimeframe([m15, h4]);
  assert.equal(selection.timeframe, "4H");
  assert.equal(selection.publication?.status, "ACTIVE");
});

test("timeframe selector returns no publication when every candidate is WAIT", () => {
  const waiting = evaluatePublication(candidate({ quant: validateQuantEvidence(quantInput({ calibratedProbability: null })) }), NOW);
  const selection = selectBestTimeframe([timeframeCandidate("1H", waiting, 0.9)]);
  assert.equal(selection.timeframe, null);
  assert.equal(selection.publication, null);
  assert.ok(selection.reasons.includes("NO_VALIDATED_TIMEFRAME"));
});
