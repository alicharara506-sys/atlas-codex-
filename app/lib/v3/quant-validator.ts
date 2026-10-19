import type { CalibrationObservation, QuantValidationInput, QuantValidationResult } from "./types.ts";

export const MIN_QUANT_SAMPLE_SIZE = 200;
export const MAX_BRIER_SCORE = 0.25;
export const MAX_EXPECTED_CALIBRATION_ERROR = 0.10;

function probability(value: number, name: string): void {
  if (!Number.isFinite(value) || value < 0 || value > 1) throw new Error(`${name} must be between 0 and 1`);
}

export function brierScore(observations: CalibrationObservation[]): number | null {
  if (observations.length === 0) return null;
  let total = 0;
  for (const row of observations) {
    probability(row.probability, "probability");
    if (row.outcome !== 0 && row.outcome !== 1) throw new Error("outcome must be 0 or 1");
    total += (row.probability - row.outcome) ** 2;
  }
  return total / observations.length;
}

export function expectedCalibrationError(observations: CalibrationObservation[], binCount = 10): number | null {
  if (observations.length === 0) return null;
  if (!Number.isInteger(binCount) || binCount < 2 || binCount > 100) throw new Error("binCount must be an integer from 2 to 100");
  const bins = Array.from({ length: binCount }, () => ({ count: 0, probabilities: 0, outcomes: 0 }));
  for (const row of observations) {
    probability(row.probability, "probability");
    const index = Math.min(binCount - 1, Math.floor(row.probability * binCount));
    bins[index].count += 1;
    bins[index].probabilities += row.probability;
    bins[index].outcomes += row.outcome;
  }
  return bins.reduce((sum, bin) => {
    if (bin.count === 0) return sum;
    const confidence = bin.probabilities / bin.count;
    const accuracy = bin.outcomes / bin.count;
    return sum + (bin.count / observations.length) * Math.abs(confidence - accuracy);
  }, 0);
}

export function validateQuantEvidence(input: QuantValidationInput): QuantValidationResult {
  const vetoReasons: string[] = [];
  if (input.calibratedProbability === null) vetoReasons.push("CALIBRATED_PROBABILITY_UNAVAILABLE");
  else if (!Number.isFinite(input.calibratedProbability) || input.calibratedProbability < 0 || input.calibratedProbability > 1) vetoReasons.push("CALIBRATED_PROBABILITY_INVALID");
  if (input.calibrationSampleSize < MIN_QUANT_SAMPLE_SIZE) vetoReasons.push("INSUFFICIENT_CALIBRATION_SAMPLE");
  if (input.brierScore === null || !Number.isFinite(input.brierScore)) vetoReasons.push("BRIER_SCORE_UNAVAILABLE");
  else if (input.brierScore > MAX_BRIER_SCORE) vetoReasons.push("BRIER_SCORE_TOO_HIGH");
  if (input.expectedCalibrationError === null || !Number.isFinite(input.expectedCalibrationError)) vetoReasons.push("CALIBRATION_ERROR_UNAVAILABLE");
  else if (input.expectedCalibrationError > MAX_EXPECTED_CALIBRATION_ERROR) vetoReasons.push("CALIBRATION_ERROR_TOO_HIGH");
  if (!input.outOfSample) vetoReasons.push("OUT_OF_SAMPLE_VALIDATION_REQUIRED");
  if (!input.regimeStable) vetoReasons.push("REGIME_INSTABILITY");
  if (!input.sensitivityStable) vetoReasons.push("SENSITIVITY_INSTABILITY");
  if (input.modelDriftDetected) vetoReasons.push("MODEL_DRIFT_DETECTED");
  if (!input.costsIncluded) vetoReasons.push("TRANSACTION_COSTS_NOT_INCLUDED");
  if (input.expectedValue === null || !Number.isFinite(input.expectedValue)) vetoReasons.push("EXPECTED_VALUE_UNAVAILABLE");
  else if (input.expectedValue <= 0) vetoReasons.push("NON_POSITIVE_EXPECTANCY");
  return {
    status: vetoReasons.length === 0 ? "PASS" : "VETO",
    vetoReasons,
    calibratedProbability: input.calibratedProbability,
    sampleSize: input.calibrationSampleSize,
    brierScore: input.brierScore,
    expectedCalibrationError: input.expectedCalibrationError,
    expectedValue: input.expectedValue,
  };
}
