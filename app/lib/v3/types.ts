import type { OHLCBar, Timeframe } from "../types.ts";

export type V3TradeAction = "BUY" | "SELL" | "WAIT";
export type V3SignalStatus = "ACTIVE" | "WAIT" | "INVALIDATED" | "EXPIRED" | "CLOSED";
export type ProbabilityGrade = "B" | "A" | "A+" | "A+++++";
export type V3AssetClass = "FOREX" | "COMMODITY";
export type ModelGovernanceStatus = "EXPERIMENTAL" | "SHADOW" | "APPROVED" | "DEGRADED" | "RETIRED";

export interface DataQualityInput {
  bars: OHLCBar[];
  timeframe: Timeframe;
  dataStatus: "LIVE" | "DEMO" | "UNAVAILABLE";
  source: string;
  now: number;
  minimumBars?: number;
  referenceClose?: number;
  providerDisagreementToleranceBps?: number;
}

export interface DataQualityReport {
  status: "PASS" | "FAIL";
  critical: boolean;
  source: string;
  barCount: number;
  latestBarTime: number | null;
  staleBySeconds: number | null;
  duplicateTimestamps: number;
  gapCount: number;
  reasons: string[];
}

export interface CalibrationObservation {
  probability: number;
  outcome: 0 | 1;
}

export interface QuantValidationInput {
  calibratedProbability: number | null;
  calibrationSampleSize: number;
  brierScore: number | null;
  expectedCalibrationError: number | null;
  outOfSample: boolean;
  regimeStable: boolean;
  sensitivityStable: boolean;
  modelDriftDetected: boolean;
  expectedValue: number | null;
  costsIncluded: boolean;
}

export interface QuantValidationResult {
  status: "PASS" | "VETO";
  vetoReasons: string[];
  calibratedProbability: number | null;
  sampleSize: number;
  brierScore: number | null;
  expectedCalibrationError: number | null;
  expectedValue: number | null;
}

export interface PublicationCandidate {
  signalId: string;
  instrument: string;
  assetClass: V3AssetClass;
  timeframe: Timeframe;
  proposedAction: Exclude<V3TradeAction, "WAIT">;
  generatedAt: number;
  expiresAt: number;
  modelStatus: ModelGovernanceStatus;
  quant: QuantValidationResult;
  dataQuality: DataQualityReport;
  rewardRisk: number;
  regimeValid: boolean;
  liquidityAdequate: boolean;
  capacityAdequate: boolean;
  spreadAcceptable: boolean;
  eventRiskAcceptable: boolean;
  structuralStopValid: boolean;
  targetValid: boolean;
  unresolvedCriticalContradiction: boolean;
}

export interface PublicationDecision {
  signalId: string;
  instrument: string;
  timeframe: Timeframe;
  action: V3TradeAction;
  status: Extract<V3SignalStatus, "ACTIVE" | "WAIT">;
  calibratedWinProbability: number | null;
  probabilityGrade: ProbabilityGrade | null;
  expectedValue: number | null;
  quantValidationStatus: "PASS" | "VETO";
  rejectionReasons: string[];
  failClosed: true;
}

export interface TimeframeCandidate {
  timeframe: Timeframe;
  publication: PublicationDecision;
  regimeFit: number;
  signalToNoise: number;
  liquidity: number;
  structureClarity: number;
  macroAlignment: number;
  llmAgreement: number;
  volatilityFit: number;
  dataQualityScore: number;
  eventRiskScore: number;
}

export interface TimeframeSelection {
  timeframe: Timeframe | null;
  publication: PublicationDecision | null;
  selectionScore: number | null;
  evaluated: Array<{ timeframe: Timeframe; eligible: boolean; score: number | null }>;
  reasons: string[];
}
