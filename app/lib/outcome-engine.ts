import type { SignalOutcome } from "./storage.ts";

export const DEFAULT_SPREAD_PIPS = 1.5;
export const DEFAULT_ROUND_TURN_USD = 7;
export const DEFAULT_USD_PER_PIP = 10;

export interface OutcomeAnalyticsOptions {
  spreadPips?: number;
  roundTurnUsd?: number;
  usdPerPip?: number;
}

export interface PerformanceSlice {
  count: number;
  wins: number;
  losses: number;
  winRate: number | null;
  avgWinPips: number | null;
  avgLossPips: number | null;
  expectancyPips: number | null;
  totalPips: number;
  avgPlannedRR: number | null;
  avgAchievedRR: number | null;
}

export interface OutcomeAnalyticsResult {
  overall: PerformanceSlice;
  byAction: Record<string, PerformanceSlice>;
  byScoreBucket: Record<string, PerformanceSlice>;
  bySymbol: Record<string, PerformanceSlice>;
  byRegime: Record<string, PerformanceSlice>;
  rolling30: PerformanceSlice;
  rolling100: PerformanceSlice;
  equityCurve: { time: number; pips: number }[];
  calibrated: boolean;
  calibrationReason: string;
}

const RESOLVED = new Set(["TP3_HIT", "SL_HIT", "CLOSED_MANUAL"]);
const SCORE_BUCKETS = ["40-49", "50-59", "60-69", "70+"] as const;

function scoreBucket(score: number): string {
  if (score >= 70) return "70+";
  if (score >= 60) return "60-69";
  if (score >= 50) return "50-59";
  return "40-49";
}

export class OutcomeAnalytics {
  private readonly outcomes: SignalOutcome[];
  private readonly frictionPips: number;

  constructor(outcomes: SignalOutcome[], options: OutcomeAnalyticsOptions = {}) {
    this.outcomes = outcomes.filter((outcome) => RESOLVED.has(outcome.status));
    const spread = options.spreadPips ?? DEFAULT_SPREAD_PIPS;
    const commission = (options.roundTurnUsd ?? DEFAULT_ROUND_TURN_USD) / (options.usdPerPip ?? DEFAULT_USD_PER_PIP);
    this.frictionPips = spread + commission;
  }

  netPips(outcome: SignalOutcome): number {
    return outcome.finalPips - this.frictionPips;
  }

  private slice(rows: SignalOutcome[]): PerformanceSlice {
    const nets = rows.map((row) => this.netPips(row));
    const wins = nets.filter((value) => value > 0);
    const losses = nets.filter((value) => value <= 0);
    const avg = (values: number[]): number | null => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    const winRate = rows.length ? wins.length / rows.length : null;
    const avgWin = avg(wins);
    const avgLoss = avg(losses.map(Math.abs));
    const expectancy = winRate === null || avgWin === null || avgLoss === null
      ? rows.length ? avg(nets) : null
      : winRate * avgWin - (1 - winRate) * avgLoss;
    return {
      count: rows.length,
      wins: wins.length,
      losses: losses.length,
      winRate,
      avgWinPips: avgWin,
      avgLossPips: avgLoss,
      expectancyPips: expectancy,
      totalPips: nets.reduce((sum, value) => sum + value, 0),
      avgPlannedRR: avg(rows.map((row) => row.plannedRR)),
      avgAchievedRR: avg(rows.map((row) => row.achievedRR)),
    };
  }

  private grouped(key: (row: SignalOutcome) => string): Record<string, PerformanceSlice> {
    const groups = new Map<string, SignalOutcome[]>();
    for (const row of this.outcomes) groups.set(key(row), [...(groups.get(key(row)) ?? []), row]);
    return Object.fromEntries([...groups].map(([name, rows]) => [name, this.slice(rows)]));
  }

  isCalibrated(): { calibrated: boolean; reason: string } {
    const overall = this.slice(this.outcomes);
    if (overall.count < 200) return { calibrated: false, reason: `${overall.count}/200 resolved outcomes.` };
    if (overall.wins < 40 || overall.losses < 40) return { calibrated: false, reason: `Requires 40 wins and 40 losses; has ${overall.wins}/${overall.losses}.` };
    const byBucket = this.grouped((row) => scoreBucket(row.scoreAtGeneration));
    const sparse = SCORE_BUCKETS.filter((bucket) => (byBucket[bucket]?.count ?? 0) < 30);
    if (sparse.length) return { calibrated: false, reason: `Score segments below 30 outcomes: ${sparse.join(", ")}.` };
    return { calibrated: true, reason: "Resolved-outcome calibration thresholds passed." };
  }

  compute(): OutcomeAnalyticsResult {
    const chronological = [...this.outcomes].sort((a, b) => (a.timestampResolved ?? 0) - (b.timestampResolved ?? 0));
    let cumulative = 0;
    const calibration = this.isCalibrated();
    return {
      overall: this.slice(this.outcomes),
      byAction: this.grouped((row) => row.action),
      byScoreBucket: this.grouped((row) => scoreBucket(row.scoreAtGeneration)),
      bySymbol: this.grouped((row) => row.symbol),
      byRegime: this.grouped((row) => row.regimeAtGeneration),
      rolling30: this.slice(chronological.slice(-30)),
      rolling100: this.slice(chronological.slice(-100)),
      equityCurve: chronological.map((row) => ({
        time: row.timestampResolved ?? row.timestampGenerated,
        pips: cumulative += this.netPips(row),
      })),
      calibrated: calibration.calibrated,
      calibrationReason: calibration.reason,
    };
  }
}

export function kellyCriterion(winRate: number | null, rewardRisk: number | null): number | null {
  if (winRate === null || rewardRisk === null || rewardRisk <= 0) return null;
  return Math.max(0, Math.min(1, winRate - (1 - winRate) / rewardRisk));
}
