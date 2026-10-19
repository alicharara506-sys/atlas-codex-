import type { MLCommitteeRead, OHLCBar } from "./types.ts";

interface TrainingRow {
  features: number[];
  label: -1 | 1;
}

interface DecisionStump {
  feature: number;
  threshold: number;
  polarity: 1 | -1;
}

function clip(value: number, low = -1, high = 1): number {
  return Math.max(low, Math.min(high, value));
}

function quantile(values: number[], probability: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(probability * (sorted.length - 1)))];
}

function featureVector(bars: OHLCBar[], index: number): number[] {
  const current = bars[index];
  const previous = bars[index - 1];
  const return1 = previous.close ? current.close / previous.close - 1 : 0;
  const momentum5 = bars[index - 5].close ? current.close / bars[index - 5].close - 1 : 0;
  let path = 0;
  for (let cursor = index - 13; cursor <= index; cursor++) path += Math.abs(bars[cursor].close - bars[cursor - 1].close);
  const efficiency = path > 0 ? Math.abs(current.close - bars[index - 14].close) / path : 0;
  const volumeWindow = bars.slice(index - 19, index + 1);
  const avgVolume = volumeWindow.reduce((sum, bar) => sum + bar.volume, 0) / volumeWindow.length;
  const volumeRatio = avgVolume > 0 ? current.volume / avgVolume : 1;
  const typicalVolume = volumeWindow.reduce((sum, bar) => sum + ((bar.high + bar.low + bar.close) / 3) * bar.volume, 0);
  const totalVolume = volumeWindow.reduce((sum, bar) => sum + bar.volume, 0);
  const vwap = totalVolume > 0 ? typicalVolume / totalVolume : current.close;
  const ranges = bars.slice(index - 19, index + 1).map((bar) => bar.high - bar.low);
  const avgRange = ranges.reduce((sum, value) => sum + value, 0) / ranges.length;
  const vwapDeviation = avgRange > 0 ? (current.close - vwap) / avgRange : 0;
  const rangeRatio = current.close > 0 ? avgRange / current.close : 0;
  return [return1 * 1_000, momentum5 * 200, efficiency, clip(volumeRatio / 2, 0, 2), clip(vwapDeviation, -3, 3), rangeRatio * 1_000];
}

export function tripleBarrierLabel(
  bars: OHLCBar[],
  index: number,
  atrDistance: number,
  horizon = 20
): -1 | 1 {
  const entry = bars[index].close;
  const upper = entry + 1.5 * atrDistance;
  const lower = entry - 1.5 * atrDistance;
  for (let cursor = index + 1; cursor <= Math.min(bars.length - 1, index + horizon); cursor++) {
    const bar = bars[cursor];
    const upperHit = bar.high >= upper;
    const lowerHit = bar.low <= lower;
    if (upperHit && lowerHit) return -1; // conservative ambiguous-bar rule
    if (upperHit) return 1;
    if (lowerHit) return -1;
  }
  return bars[Math.min(bars.length - 1, index + horizon)].close >= entry ? 1 : -1;
}

function trainingRows(bars: OHLCBar[]): TrainingRow[] {
  const start = Math.max(60, bars.length - 500);
  const rows: TrainingRow[] = [];
  for (let index = start; index < bars.length - 20; index++) {
    const ranges = bars.slice(index - 13, index + 1).map((bar) => bar.high - bar.low);
    const atrDistance = ranges.reduce((sum, value) => sum + value, 0) / ranges.length;
    if (atrDistance <= 0) continue;
    rows.push({ features: featureVector(bars, index), label: tripleBarrierLabel(bars, index, atrDistance) });
  }
  return rows;
}

function trainRuleForest(rows: TrainingRow[], count = 50): DecisionStump[] {
  if (!rows.length) return [];
  const featureCount = rows[0].features.length;
  const stumps: DecisionStump[] = [];
  for (let stumpIndex = 0; stumpIndex < count; stumpIndex++) {
    const feature = stumpIndex % featureCount;
    const probability = 0.15 + ((stumpIndex * 17) % 70) / 100;
    const threshold = quantile(rows.map((row) => row.features[feature]), probability);
    let direct = 0;
    let inverse = 0;
    for (const row of rows) {
      const vote = row.features[feature] >= threshold ? 1 : -1;
      if (vote === row.label) direct++;
      if (-vote === row.label) inverse++;
    }
    stumps.push({ feature, threshold, polarity: direct >= inverse ? 1 : -1 });
  }
  return stumps;
}

function forestVote(stumps: DecisionStump[], features: number[]): number {
  if (!stumps.length) return 0;
  return stumps.reduce((sum, stump) => {
    const vote = features[stump.feature] >= stump.threshold ? 1 : -1;
    return sum + vote * stump.polarity;
  }, 0) / stumps.length;
}

function normalizedReturns(bars: OHLCBar[], endExclusive: number): number[] {
  const returns: number[] = [];
  for (let index = endExclusive - 20; index < endExclusive; index++) {
    const previous = bars[index - 1].close;
    returns.push(previous ? bars[index].close / previous - 1 : 0);
  }
  const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
  const variance = returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / returns.length;
  const std = Math.sqrt(variance) || 1;
  return returns.map((value) => (value - mean) / std);
}

function patternVote(bars: OHLCBar[], k = 5): number {
  if (bars.length < 90) return 0;
  const recent = normalizedReturns(bars, bars.length);
  const matches: { distance: number; vote: number }[] = [];
  for (let end = 40; end < bars.length - 20; end += 2) {
    const candidate = normalizedReturns(bars, end);
    const distance = Math.sqrt(candidate.reduce((sum, value, index) => sum + (value - recent[index]) ** 2, 0));
    const future = bars[end + 19].close - bars[end - 1].close;
    matches.push({ distance, vote: future >= 0 ? 1 : -1 });
  }
  const nearest = matches.sort((a, b) => a.distance - b.distance).slice(0, k);
  return nearest.length ? nearest.reduce((sum, match) => sum + match.vote, 0) / nearest.length : 0;
}

export function runMLCommittee(bars: OHLCBar[]): MLCommitteeRead {
  if (bars.length < 100) {
    return { probability: 0, confidence: 0, ruleForestVote: 0, patternVote: 0, trainingSamples: 0, available: false };
  }
  const rows = trainingRows(bars);
  if (rows.length < 40) {
    return { probability: 0, confidence: 0, ruleForestVote: 0, patternVote: 0, trainingSamples: rows.length, available: false };
  }
  const stumps = trainRuleForest(rows, 50);
  const forest = forestVote(stumps, featureVector(bars, bars.length - 1));
  const pattern = patternVote(bars, 5);
  const probability = clip(forest * 0.6 + pattern * 0.4);
  return {
    probability,
    confidence: Math.min(1, Math.abs(forest) * 0.6 + Math.abs(pattern) * 0.4),
    ruleForestVote: forest,
    patternVote: pattern,
    trainingSamples: rows.length,
    available: true,
  };
}

export function blendMLAlignmentScore(
  existingScore: number,
  expectedSign: number,
  read: MLCommitteeRead,
  mlWeight = 0.15
): number {
  if (!read.available || expectedSign === 0) return existingScore;
  const mlAlignment = 50 + expectedSign * read.probability * 50;
  return Math.round((existingScore * (1 - mlWeight) + mlAlignment * mlWeight) * 10) / 10;
}
