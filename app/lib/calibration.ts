// app/lib/calibration.ts
// Converts imported, resolved outcomes into a calibrated historical win
// probability. This is the ONLY module in Atlas allowed to produce a
// probability number, and only once enough real evidence exists.
//
// Method: pooled-adjacent-violators (PAVA) isotonic regression over
// 10-point score bins, Beta(1,1)-smoothed win rate, 95% Wilson interval
// on the pooled (merged) bin. Nothing here is fit to future data — CSV
// rows with a future resolved_at are rejected outright.

import type {
  CalibratedProbability,
  CalibrationAvailability,
  OutcomeSource,
  ResolvedOutcome,
  Timeframe,
} from "./types";

export const MIN_TOTAL_OUTCOMES = 200;
export const MIN_WINS = 40;
export const MIN_LOSSES = 40;
export const MIN_SEGMENT_OUTCOMES = 30;
export const BIN_WIDTH = 10;
export const BIN_COUNT = 10; // 0-10 .. 90-100
const WILSON_Z = 1.96; // 95% CI

const VALID_TIMEFRAMES: Timeframe[] = ["15M", "1H", "4H", "D1"];
const VALID_SOURCES: OutcomeSource[] = ["OUT_OF_SAMPLE", "LIVE_RESOLVED"];

// ---------------------------------------------------------------------------
// CSV import + validation
// ---------------------------------------------------------------------------

export interface ImportResult {
  accepted: ResolvedOutcome[];
  rejected: { line: number; reason: string; raw: string }[];
}

function splitCsvLine(line: string): string[] {
  // Deliberately simple: this format has no embedded commas/quotes per the
  // documented schema (signal_id,score,outcome,resolved_at,source,symbol,timeframe).
  return line.split(",").map((s) => s.trim());
}

export function parseOutcomesCsv(
  csvText: string,
  now: Date = new Date()
): ImportResult {
  const lines = csvText.split(/\r?\n/).filter((l) => l.trim().length > 0);
  const accepted: ResolvedOutcome[] = [];
  const rejected: ImportResult["rejected"] = [];
  if (lines.length === 0) return { accepted, rejected };

  const header = splitCsvLine(lines[0]).map((h) => h.toLowerCase());
  const idx = (name: string) => header.indexOf(name);
  const required = ["signal_id", "score", "outcome", "resolved_at", "source"];
  for (const col of required) {
    if (idx(col) === -1) {
      rejected.push({
        line: 0,
        reason: `missing required column "${col}"`,
        raw: lines[0],
      });
      return { accepted, rejected };
    }
  }

  const seenIds = new Set<string>();

  for (let i = 1; i < lines.length; i++) {
    const raw = lines[i];
    const cols = splitCsvLine(raw);
    const signalId = cols[idx("signal_id")];
    const scoreStr = cols[idx("score")];
    const outcomeStr = cols[idx("outcome")];
    const resolvedAt = cols[idx("resolved_at")];
    const source = cols[idx("source")];
    const symbol = idx("symbol") !== -1 ? cols[idx("symbol")] : undefined;
    const timeframeRaw =
      idx("timeframe") !== -1 ? cols[idx("timeframe")] : undefined;

    if (!signalId) {
      rejected.push({ line: i, reason: "missing signal_id", raw });
      continue;
    }
    if (seenIds.has(signalId)) {
      rejected.push({ line: i, reason: `duplicate signal_id "${signalId}"`, raw });
      continue;
    }

    const score = Number(scoreStr);
    if (!Number.isFinite(score) || score < 0 || score > 100) {
      rejected.push({ line: i, reason: `invalid score "${scoreStr}"`, raw });
      continue;
    }

    if (outcomeStr !== "WIN" && outcomeStr !== "LOSS") {
      rejected.push({
        line: i,
        reason: `outcome must be WIN or LOSS, got "${outcomeStr}"`,
        raw,
      });
      continue;
    }

    if (!VALID_SOURCES.includes(source as OutcomeSource)) {
      rejected.push({
        line: i,
        reason: `source must be OUT_OF_SAMPLE or LIVE_RESOLVED, got "${source}"`,
        raw,
      });
      continue;
    }

    const resolvedDate = new Date(resolvedAt);
    if (Number.isNaN(resolvedDate.getTime())) {
      rejected.push({
        line: i,
        reason: `invalid resolved_at "${resolvedAt}"`,
        raw,
      });
      continue;
    }
    if (resolvedDate.getTime() > now.getTime()) {
      rejected.push({
        line: i,
        reason: `resolved_at "${resolvedAt}" is in the future`,
        raw,
      });
      continue;
    }

    let timeframe: Timeframe | undefined;
    if (timeframeRaw) {
      if (!VALID_TIMEFRAMES.includes(timeframeRaw as Timeframe)) {
        rejected.push({
          line: i,
          reason: `invalid timeframe "${timeframeRaw}"`,
          raw,
        });
        continue;
      }
      timeframe = timeframeRaw as Timeframe;
    }

    seenIds.add(signalId);
    accepted.push({
      signalId,
      score,
      outcome: outcomeStr,
      resolvedAt,
      source: source as OutcomeSource,
      symbol: symbol || undefined,
      timeframe,
    });
  }

  return { accepted, rejected };
}

// ---------------------------------------------------------------------------
// Availability gate
// ---------------------------------------------------------------------------

export function checkAvailability(
  outcomes: ResolvedOutcome[]
): CalibrationAvailability {
  const wins = outcomes.filter((o) => o.outcome === "WIN").length;
  const losses = outcomes.length - wins;
  if (
    outcomes.length < MIN_TOTAL_OUTCOMES ||
    wins < MIN_WINS ||
    losses < MIN_LOSSES
  ) {
    return {
      available: false,
      totalOutcomes: outcomes.length,
      wins,
      losses,
      reason: `Needs >=${MIN_TOTAL_OUTCOMES} total outcomes with >=${MIN_WINS} wins and >=${MIN_LOSSES} losses (have ${outcomes.length} total, ${wins}W/${losses}L).`,
    };
  }
  return { available: true, totalOutcomes: outcomes.length, wins, losses };
}

// ---------------------------------------------------------------------------
// Isotonic pooled bins
// ---------------------------------------------------------------------------

interface PooledBin {
  startBin: number;
  endBin: number;
  n: number;
  wins: number;
}

function binIndexForScore(score: number): number {
  return Math.min(BIN_COUNT - 1, Math.max(0, Math.floor(score / BIN_WIDTH)));
}

function betaSmoothedRate(wins: number, n: number): number {
  // Beta(1,1) prior == Laplace smoothing; keeps sparse bins from producing
  // 0% or 100% rates that would distort the monotonic pooling order.
  return (wins + 1) / (n + 2);
}

export function buildIsotonicBins(outcomes: ResolvedOutcome[]): PooledBin[] {
  const raw: { n: number; wins: number }[] = Array.from(
    { length: BIN_COUNT },
    () => ({ n: 0, wins: 0 })
  );
  for (const o of outcomes) {
    const b = binIndexForScore(o.score);
    raw[b].n += 1;
    if (o.outcome === "WIN") raw[b].wins += 1;
  }

  const stack: PooledBin[] = [];
  for (let i = 0; i < raw.length; i++) {
    const cur: PooledBin = { startBin: i, endBin: i, n: raw[i].n, wins: raw[i].wins };
    stack.push(cur);
    while (
      stack.length > 1 &&
      betaSmoothedRate(stack[stack.length - 2].wins, stack[stack.length - 2].n) >
        betaSmoothedRate(stack[stack.length - 1].wins, stack[stack.length - 1].n)
    ) {
      const b = stack.pop() as PooledBin;
      const a = stack.pop() as PooledBin;
      stack.push({
        startBin: a.startBin,
        endBin: b.endBin,
        n: a.n + b.n,
        wins: a.wins + b.wins,
      });
    }
  }
  return stack;
}

function wilsonInterval(
  wins: number,
  n: number,
  z = WILSON_Z
): { low: number; high: number } {
  if (n === 0) return { low: 0, high: 1 };
  const phat = wins / n;
  const denom = 1 + (z * z) / n;
  const center = phat + (z * z) / (2 * n);
  const margin =
    z * Math.sqrt((phat * (1 - phat)) / n + (z * z) / (4 * n * n));
  return {
    low: Math.max(0, (center - margin) / denom),
    high: Math.min(1, (center + margin) / denom),
  };
}

/**
 * Returns a calibrated probability for a given score, or null if the
 * evidence-gate (overall) or the segment-specific minimum sample size
 * is not met. Callers must check `.available` on checkAvailability()
 * first for the overall gate; this function re-checks the segment gate.
 */
export function getCalibratedProbability(
  score: number,
  outcomes: ResolvedOutcome[]
): CalibratedProbability | null {
  const overall = checkAvailability(outcomes);
  if (!overall.available) return null;

  const bins = buildIsotonicBins(outcomes);
  const targetBinIdx = binIndexForScore(score);
  const group = bins.find(
    (b) => targetBinIdx >= b.startBin && targetBinIdx <= b.endBin
  );
  if (!group || group.n < MIN_SEGMENT_OUTCOMES) return null;

  const probability = group.wins / group.n;
  const { low, high } = wilsonInterval(group.wins, group.n);

  return {
    probability: Math.round(probability * 1000) / 1000,
    wilsonLow: Math.round(low * 1000) / 1000,
    wilsonHigh: Math.round(high * 1000) / 1000,
    sampleSize: group.n,
    scoreSegment: [group.startBin * BIN_WIDTH, (group.endBin + 1) * BIN_WIDTH],
  };
}
