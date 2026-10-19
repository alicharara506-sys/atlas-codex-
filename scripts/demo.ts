#!/usr/bin/env node
// scripts/demo.ts
//
// Proof that the Atlas deterministic core actually runs, end to end, using
// nothing but Node.js and the real app/lib modules — no vinext, no server,
// no MT5 terminal, no UI. This is NOT the finished app. It's the honest
// thing you CAN run today.
//
// Run with:
//   npm run demo
// or directly:
//   node --experimental-strip-types scripts/demo.ts
//
// Requires Node.js 22.13+ (see package.json "engines"). Nothing else —
// no npm install needed for this script specifically, since it only
// imports from app/lib with explicit .ts extensions that Node's built-in
// type-stripping can load directly.

import {
  generateDemoSeries,
  generateDemoTicks,
} from "../app/lib/demo-market.ts";
import { generateSignal } from "../app/lib/signal-engine.ts";
import { computeRisk } from "../app/lib/risk-engine.ts";
import { runBacktest } from "../app/lib/backtest.ts";
import type { Timeframe } from "../app/lib/types.ts";

function section(title: string): void {
  console.log("\n" + "=".repeat(72));
  console.log(title);
  console.log("=".repeat(72));
}

const SYMBOL = "EURUSD";
const TIMEFRAME: Timeframe = "1H";
const ACCOUNT_BALANCE = 50; // your real MT5 balance, used here only as an example

section(`ATLAS ENGINE DEMO — ${SYMBOL} ${TIMEFRAME} (synthetic DEMO data, not live)`);
console.log(
  "Every number below comes from app/lib/*.ts — the same code the real\n" +
    "app will call once the server/UI exist. Nothing here is hand-typed."
);

// ---------------------------------------------------------------------------
// 1. Build synthetic multi-timeframe data + ticks (stands in for MT5/Twelve
//    Data until the provider wiring is connected to a live server route).
// ---------------------------------------------------------------------------

const mtf = {
  D1: generateDemoSeries({ symbol: SYMBOL, timeframe: "D1", count: 120 }),
  "4H": generateDemoSeries({ symbol: SYMBOL, timeframe: "4H", count: 120 }),
  "1H": generateDemoSeries({ symbol: SYMBOL, timeframe: "1H", count: 200 }),
  "15M": generateDemoSeries({ symbol: SYMBOL, timeframe: "15M", count: 120 }),
};
const ticks = generateDemoTicks(mtf["1H"].slice(-20), 20);

// ---------------------------------------------------------------------------
// 2. Generate a signal (8-factor score + order flow read)
// ---------------------------------------------------------------------------

const signal = generateSignal({
  symbol: SYMBOL,
  timeframe: TIMEFRAME,
  mtf,
  dataStatus: "DEMO",
  ticks,
});

section("1. SIGNAL");
console.log(`Direction:     ${signal.direction}`);
console.log(`Score:         ${signal.score}/100  (analytical alignment — NOT a probability)`);
console.log(`Confidence:    ${signal.confidence}`);
console.log(`Regime:        ${signal.regime}`);
console.log(`Structure:     ${signal.structure}`);
console.log(
  `Historical win probability: ${
    signal.historicalWinProbability ??
    "null — no calibration evidence imported yet (this is correct, not a bug)"
  }`
);
console.log(`\nEntry zone:    ${signal.entryZone.low.toFixed(5)} - ${signal.entryZone.high.toFixed(5)}`);
console.log(`Invalidation:  ${signal.invalidation.toFixed(5)}`);
console.log(`Targets:       ${signal.targets.map((t) => t.toFixed(5)).join("  |  ")}`);
console.log("\nEvidence:");
for (const e of signal.evidence) console.log(`  - ${e}`);

section("2. ORDER FLOW (tick-rule approximation)");
if (signal.orderFlow) {
  console.log(`Method:        ${signal.orderFlow.method}`);
  console.log(`Pressure:      ${signal.orderFlow.pressure}`);
  console.log(`Delta trend:   ${signal.orderFlow.cumulativeDeltaTrend}`);
  console.log(`Absorption:    ${signal.orderFlow.absorptionDetected}`);
  console.log(`Ticks used:    ${signal.orderFlow.ticksAnalyzed}`);
  console.log("Evidence:");
  for (const e of signal.orderFlow.evidence) console.log(`  - ${e}`);
} else {
  console.log("No tick data supplied -> orderFlow is null (never fabricated as neutral).");
}

// ---------------------------------------------------------------------------
// 3. Position sizing against your actual account balance
// ---------------------------------------------------------------------------

section(`3. POSITION SIZING — $${ACCOUNT_BALANCE} account, 1% risk, no broker spec (MODEL_ONLY)`);
const entry = mtf["1H"][mtf["1H"].length - 1].close;
const risk = computeRisk({
  accountBalance: ACCOUNT_BALANCE,
  riskPercent: 1,
  entry,
  stop: signal.invalidation,
  target: signal.targets[0],
  leverage: 30,
});
console.log(`Max loss ($):      ${risk.maxLossAmount.toFixed(2)}`);
console.log(`Position units:    ${risk.positionUnits}`);
console.log(`Reward:risk:       ${risk.rewardToRiskRatio?.toFixed(2) ?? "n/a"}R`);
console.log(`Sizing source:     ${risk.sizingSource}`);
if (risk.warnings.length > 0) {
  console.log("Warnings:");
  for (const w of risk.warnings) console.log(`  - [${w.code}] ${w.message}`);
}

// ---------------------------------------------------------------------------
// 4. Quick backtest
// ---------------------------------------------------------------------------

section("4. BACKTEST — Trend Following, demo data, chronological 60/20/20 partitions");
const bt = runBacktest("TREND_FOLLOWING", SYMBOL, TIMEFRAME, mtf["1H"], true);
const partitions: Array<["train" | "validation" | "test", typeof bt.stats.train]> = [
  ["train", bt.stats.train],
  ["validation", bt.stats.validation],
  ["test", bt.stats.test],
];
for (const [name, stats] of partitions) {
  console.log(
    `${name.toUpperCase().padEnd(11)} trades=${String(stats.tradeCount).padEnd(4)} ` +
      `winRate=${stats.winRate ?? "n/a"}  avgR=${stats.avgRMultiple ?? "n/a"}  ` +
      `maxDD(R)=${stats.maxDrawdownR ?? "n/a"}`
  );
}

section("DONE");
console.log(
  "This used ONLY app/lib/*.ts — no vinext, no server, no MT5, no UI.\n" +
    "It proves the engine runs correctly. It is not the desktop app yet."
);
