import assert from "node:assert/strict";
import { test } from "node:test";

import {
  decisionTone,
  filterMarkets,
  formatProbability,
} from "../app/lib/v4-terminal.ts";
import type { V4MarketSummary } from "../app/lib/v4-terminal.ts";

function market(overrides: Partial<V4MarketSummary> = {}): V4MarketSummary {
  return {
    instrument: "EURUSD",
    asset_class: "FOREX",
    bid: 1.1,
    ask: 1.1001,
    price: 1.10005,
    spread: 0.0001,
    spread_bps: 0.91,
    captured_at: "2026-08-20T10:00:00Z",
    session: "LONDON",
    broker: "TEST",
    server: "TEST-DEMO",
    account_mode: "DEMO",
    last_tick_at: "2026-08-20T10:00:00Z",
    tick_age_seconds: 0.5,
    tick_count: 100,
    volume_provenance: "MT5_TICK_VOLUME",
    mt5_status: "CONNECTED",
    data_quality: "GOOD",
    data_quality_score: 97,
    data_issues: [],
    liquidity: "ADEQUATE",
    mtf_alignment: "ALIGNED",
    best_timeframe: "1H",
    calibrated_probability: 0.91,
    grade: "A+",
    expected_value_net: 0.42,
    regime: "TRENDING",
    event_risk: "LOW",
    quant_status: "PASS",
    decision: "PUBLISHED",
    decision_reasons: [],
    signal_id: "sig-1",
    signal_direction: "LONG",
    tracking_state: "ACTIVE",
    timeframes: [],
    availability: "AVAILABLE",
    ...overrides,
  };
}

test("V4 market filters preserve direction, authority, and exact quality gates", () => {
  const rows = [
    market(),
    market({ instrument: "GBPUSD", signal_id: "sig-2", signal_direction: "SHORT", calibrated_probability: 0.88 }),
    market({ instrument: "XAUUSD", asset_class: "COMMODITY", decision: "WAIT", signal_id: null, signal_direction: null, grade: "B", calibrated_probability: 0.78 }),
  ];

  assert.deepEqual(filterMarkets(rows, "LONG").map((row) => row.instrument), ["EURUSD"]);
  assert.deepEqual(filterMarkets(rows, "SHORT").map((row) => row.instrument), ["GBPUSD"]);
  assert.deepEqual(filterMarkets(rows, "WAIT").map((row) => row.instrument), ["XAUUSD"]);
  assert.deepEqual(filterMarkets(rows, "SIGNALS").map((row) => row.instrument), ["EURUSD", "GBPUSD"]);
  assert.deepEqual(filterMarkets(rows, "COMMODITY").map((row) => row.instrument), ["XAUUSD"]);
  assert.deepEqual(filterMarkets(rows, "P90").map((row) => row.instrument), ["EURUSD"]);
});

test("V4 probability display never manufactures a missing probability", () => {
  assert.equal(formatProbability(null), "—");
  assert.equal(formatProbability(undefined), "—");
  assert.equal(formatProbability(0), "0.0%");
  assert.equal(formatProbability(0.9123), "91.2%");
});

test("V4 decision tones keep publication, waiting, and veto states distinct", () => {
  assert.equal(decisionTone("PUBLISHED"), "positive");
  assert.equal(decisionTone("WAIT"), "caution");
  assert.equal(decisionTone("MODEL_UNCERTAIN"), "caution");
  assert.equal(decisionTone("QUANT_VETO"), "negative");
  assert.equal(decisionTone("RISK_VETO"), "negative");
  assert.equal(decisionTone(null), "neutral");
});
