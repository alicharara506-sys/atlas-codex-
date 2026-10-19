import type { TimeframeCandidate, TimeframeSelection } from "./types.ts";

const QUALITY_FIELDS: Array<keyof Omit<TimeframeCandidate, "timeframe" | "publication">> = [
  "regimeFit", "signalToNoise", "liquidity", "structureClarity", "macroAlignment",
  "llmAgreement", "volatilityFit", "dataQualityScore", "eventRiskScore",
];

const WEIGHTS = {
  probability: 0.30,
  expectancy: 0.15,
  regimeFit: 0.08,
  signalToNoise: 0.09,
  liquidity: 0.08,
  structureClarity: 0.09,
  macroAlignment: 0.05,
  llmAgreement: 0.03,
  volatilityFit: 0.05,
  dataQualityScore: 0.05,
  eventRiskScore: 0.03,
} as const;

function bounded(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

function score(candidate: TimeframeCandidate): number {
  const probability = candidate.publication.calibratedWinProbability ?? 0;
  const expectancy = bounded((candidate.publication.expectedValue ?? 0) / 2);
  return probability * WEIGHTS.probability + expectancy * WEIGHTS.expectancy +
    candidate.regimeFit * WEIGHTS.regimeFit + candidate.signalToNoise * WEIGHTS.signalToNoise +
    candidate.liquidity * WEIGHTS.liquidity + candidate.structureClarity * WEIGHTS.structureClarity +
    candidate.macroAlignment * WEIGHTS.macroAlignment + candidate.llmAgreement * WEIGHTS.llmAgreement +
    candidate.volatilityFit * WEIGHTS.volatilityFit + candidate.dataQualityScore * WEIGHTS.dataQualityScore +
    candidate.eventRiskScore * WEIGHTS.eventRiskScore;
}

export function selectBestTimeframe(candidates: TimeframeCandidate[]): TimeframeSelection {
  if (candidates.length === 0) return { timeframe: null, publication: null, selectionScore: null, evaluated: [], reasons: ["NO_TIMEFRAME_CANDIDATES"] };
  for (const candidate of candidates) {
    for (const field of QUALITY_FIELDS) {
      const value = candidate[field];
      if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) throw new Error(`${String(field)} must be between 0 and 1`);
    }
  }
  const evaluated = candidates.map((candidate) => {
    const eligible = candidate.publication.status === "ACTIVE" && candidate.publication.action !== "WAIT";
    return { timeframe: candidate.timeframe, eligible, score: eligible ? score(candidate) : null, candidate };
  });
  const eligible = evaluated.filter((row) => row.eligible).sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const selected = eligible[0];
  return {
    timeframe: selected?.timeframe ?? null,
    publication: selected?.candidate.publication ?? null,
    selectionScore: selected?.score ?? null,
    evaluated: evaluated.map(({ timeframe, eligible: isEligible, score: rowScore }) => ({ timeframe, eligible: isEligible, score: rowScore })),
    reasons: selected ? [] : ["NO_VALIDATED_TIMEFRAME", ...candidates.flatMap((row) => row.publication.rejectionReasons)],
  };
}
