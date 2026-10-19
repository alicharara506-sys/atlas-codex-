import type { ProbabilityGrade, PublicationCandidate, PublicationDecision } from "./types.ts";

export const MIN_ACTIVE_PROBABILITY = 0.80;
export const MIN_REWARD_RISK = 1.50;

export function probabilityGrade(probability: number | null): ProbabilityGrade | null {
  if (probability === null || !Number.isFinite(probability) || probability < MIN_ACTIVE_PROBABILITY || probability > 1) return null;
  if (probability < 0.85) return "B";
  if (probability < 0.90) return "A";
  if (probability < 0.95) return "A+";
  return "A+++++";
}

export function evaluatePublication(candidate: PublicationCandidate, now = Math.floor(Date.now() / 1_000)): PublicationDecision {
  const reasons = [...candidate.quant.vetoReasons];
  const calibrated = candidate.quant.calibratedProbability;
  if (candidate.quant.status !== "PASS") reasons.push("QUANT_VALIDATOR_VETO");
  if (calibrated === null) reasons.push("CALIBRATED_PROBABILITY_UNAVAILABLE");
  else if (calibrated < MIN_ACTIVE_PROBABILITY) reasons.push("CALIBRATED_PROBABILITY_BELOW_80_PERCENT");
  if (candidate.dataQuality.status !== "PASS" || candidate.dataQuality.critical) reasons.push("DATA_QUALITY_FAILED");
  if (candidate.modelStatus !== "APPROVED") reasons.push("MODEL_NOT_APPROVED");
  if (!candidate.regimeValid) reasons.push("REGIME_INVALID");
  if (!candidate.liquidityAdequate) reasons.push("LIQUIDITY_INADEQUATE");
  if (!candidate.capacityAdequate) reasons.push("CAPACITY_INADEQUATE");
  if (!candidate.spreadAcceptable) reasons.push("SPREAD_UNACCEPTABLE");
  if (!candidate.eventRiskAcceptable) reasons.push("EVENT_RISK_UNACCEPTABLE");
  if (!candidate.structuralStopValid) reasons.push("STRUCTURAL_STOP_INVALID");
  if (!candidate.targetValid) reasons.push("TARGET_INVALID");
  if (!Number.isFinite(candidate.rewardRisk) || candidate.rewardRisk < MIN_REWARD_RISK) reasons.push("REWARD_RISK_BELOW_MINIMUM");
  if (candidate.unresolvedCriticalContradiction) reasons.push("CRITICAL_CONTRADICTION");
  if (candidate.expiresAt <= now) reasons.push("CANDIDATE_EXPIRED");
  if (candidate.generatedAt > now) reasons.push("CANDIDATE_TIMESTAMP_IN_FUTURE");
  const uniqueReasons = [...new Set(reasons)];
  const active = uniqueReasons.length === 0;
  return {
    signalId: candidate.signalId,
    instrument: candidate.instrument,
    timeframe: candidate.timeframe,
    action: active ? candidate.proposedAction : "WAIT",
    status: active ? "ACTIVE" : "WAIT",
    calibratedWinProbability: calibrated,
    probabilityGrade: active ? probabilityGrade(calibrated) : null,
    expectedValue: candidate.quant.expectedValue,
    quantValidationStatus: candidate.quant.status,
    rejectionReasons: uniqueReasons,
    failClosed: true,
  };
}
