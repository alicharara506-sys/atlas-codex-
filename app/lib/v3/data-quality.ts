import type { Timeframe } from "../types.ts";
import type { DataQualityInput, DataQualityReport } from "./types.ts";

const TIMEFRAME_SECONDS: Record<Timeframe, number> = {
  "15M": 15 * 60,
  "1H": 60 * 60,
  "4H": 4 * 60 * 60,
  D1: 24 * 60 * 60,
};

function finite(value: number): boolean {
  return Number.isFinite(value);
}

function isScheduledWeekendGap(previous: number, next: number): boolean {
  const start = new Date(previous * 1_000);
  const end = new Date(next * 1_000);
  return start.getUTCDay() === 5 && (end.getUTCDay() === 0 || end.getUTCDay() === 1);
}

export function validateMarketData(input: DataQualityInput): DataQualityReport {
  const minimumBars = input.minimumBars ?? 60;
  const expectedStep = TIMEFRAME_SECONDS[input.timeframe];
  const reasons: string[] = [];
  let duplicateTimestamps = 0;
  let gapCount = 0;

  if (input.dataStatus !== "LIVE") reasons.push(`DATA_STATUS_${input.dataStatus}`);
  if (!input.source.trim()) reasons.push("SOURCE_MISSING");
  if (input.bars.length < minimumBars) reasons.push("INSUFFICIENT_BARS");

  for (let index = 0; index < input.bars.length; index += 1) {
    const bar = input.bars[index];
    if (![bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume].every(finite)) {
      reasons.push("NON_FINITE_BAR");
      continue;
    }
    if (bar.time <= 0 || bar.open <= 0 || bar.high <= 0 || bar.low <= 0 || bar.close <= 0) {
      reasons.push("NON_POSITIVE_PRICE_OR_TIME");
    }
    if (bar.volume < 0) {
      reasons.push("NEGATIVE_VOLUME");
    }
    if (bar.high < Math.max(bar.open, bar.close) || bar.low > Math.min(bar.open, bar.close) || bar.high < bar.low) {
      reasons.push("IMPOSSIBLE_OHLC");
    }
    if (index === 0) continue;
    const previous = input.bars[index - 1].time;
    if (bar.time === previous) duplicateTimestamps += 1;
    if (bar.time < previous) reasons.push("NON_CHRONOLOGICAL_BARS");
    const gap = bar.time - previous;
    if (gap > expectedStep * 1.5 && !isScheduledWeekendGap(previous, bar.time)) gapCount += 1;
  }

  if (duplicateTimestamps > 0) reasons.push("DUPLICATE_TIMESTAMPS");
  if (gapCount > 0) reasons.push("TIMESTAMP_GAPS");

  const latest = input.bars.at(-1)?.time ?? null;
  const staleBySeconds = latest === null ? null : Math.max(0, input.now - latest);
  if (latest === null) reasons.push("NO_LATEST_BAR");
  else if (input.now - latest > expectedStep * 3) reasons.push("STALE_DATA");

  if (input.referenceClose !== undefined && latest !== null) {
    const close = input.bars.at(-1)?.close ?? 0;
    const denominator = Math.max(Math.abs(close), Math.abs(input.referenceClose));
    const differenceBps = denominator > 0 ? (Math.abs(close - input.referenceClose) / denominator) * 10_000 : Infinity;
    if (differenceBps > (input.providerDisagreementToleranceBps ?? 25)) reasons.push("PROVIDER_DISAGREEMENT");
  }

  const uniqueReasons = [...new Set(reasons)];
  return {
    status: uniqueReasons.length === 0 ? "PASS" : "FAIL",
    critical: uniqueReasons.length > 0,
    source: input.source,
    barCount: input.bars.length,
    latestBarTime: latest,
    staleBySeconds,
    duplicateTimestamps,
    gapCount,
    reasons: uniqueReasons,
  };
}
