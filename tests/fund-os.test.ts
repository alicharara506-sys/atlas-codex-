import assert from "node:assert/strict";
import test from "node:test";
import { chartPoints, DEFAULT_MANDATE_YAML, metricPercent } from "../app/lib/fund-os.ts";

test("Fund OS ships a research-only, versioned example mandate", () => {
  assert.match(DEFAULT_MANDATE_YAML, /version: 1\.0\.0/);
  assert.match(DEFAULT_MANDATE_YAML, /mode: research/);
  assert.match(DEFAULT_MANDATE_YAML, /symbols: \[XAUUSD, XAGUSD\]/);
});

test("Fund OS equity chart uses one common scale for Atlas and its benchmark", () => {
  const rows = [
    { timestamp: "2026-01-01T00:00:00Z", equity: 1, benchmark_equity: 1, period_return: 0 },
    { timestamp: "2026-01-02T00:00:00Z", equity: 1.1, benchmark_equity: 0.9, period_return: 0.1 },
  ];
  assert.equal(chartPoints(rows, "equity", 100, 100), "0.00,50.00 100.00,0.00");
  assert.equal(chartPoints(rows, "benchmark_equity", 100, 100), "0.00,50.00 100.00,100.00");
  assert.equal(metricPercent(0.01234), "+1.23%");
  assert.equal(metricPercent(-0.01), "-1.00%");
});
