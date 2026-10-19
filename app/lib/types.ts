// app/lib/types.ts
// Canonical shared types for Atlas's deterministic engines.
// UI and provider layers convert into these shapes; nothing downstream
// of this file should need to know where the data came from.

export type Timeframe = "15M" | "1H" | "4H" | "D1";

export interface OHLCBar {
  time: number; // unix seconds, UTC, closed-bar timestamp
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type DataStatus = "LIVE" | "DEMO" | "UNAVAILABLE";

export interface CanonicalSeries {
  symbol: string;
  timeframe: Timeframe;
  bars: OHLCBar[]; // chronological, oldest first, closed bars only
  status: DataStatus;
  source: string; // e.g. "mt5", "twelve-data", "demo"
}

// ---------------------------------------------------------------------------
// Indicators
// ---------------------------------------------------------------------------

export interface IndicatorSnapshot {
  sma20: number | null;
  sma50: number | null;
  ema12: number | null;
  ema26: number | null;
  rsi14: number | null;
  atr14: number | null;
  roc10: number | null;
  volatilityStdev20: number | null;
  volumeRatio20: number | null; // current volume / 20-bar avg volume
  directionalEfficiency14: number | null; // ADX-style approximation, 0-100
}

// ---------------------------------------------------------------------------
// Structure & regime
// ---------------------------------------------------------------------------

export type StructureLabel =
  | "HH_HL" // higher-high / higher-low (uptrend structure)
  | "LH_LL" // lower-high / lower-low (downtrend structure)
  | "MIXED";

export type RegimeLabel =
  | "STRONG_BULL_TREND"
  | "WEAK_BULL_TREND"
  | "STRONG_BEAR_TREND"
  | "WEAK_BEAR_TREND"
  | "RANGE"
  | "BREAKOUT"
  | "REVERSAL"
  | "HIGH_VOLATILITY"
  | "LOW_VOLATILITY"
  | "UNCLEAR";

// ---------------------------------------------------------------------------
// Signal engine
// ---------------------------------------------------------------------------

export interface FactorWeights {
  structure: number;
  trend: number;
  momentum: number;
  multiTimeframe: number;
  volatility: number;
  volume: number;
  supportResistance: number;
  macro: number;
}

export const DEFAULT_FACTOR_WEIGHTS: FactorWeights = {
  structure: 0.2,
  trend: 0.15,
  momentum: 0.15,
  multiTimeframe: 0.15,
  volatility: 0.1,
  volume: 0.1,
  supportResistance: 0.1,
  macro: 0.05,
};

export interface FactorScores {
  structure: number; // each factor pre-normalized to 0-100
  trend: number;
  momentum: number;
  multiTimeframe: number;
  volatility: number;
  volume: number;
  supportResistance: number;
  macro: number | null; // null when macro providers are unavailable (neutral)
}

export type Direction = "LONG" | "SHORT" | "WAIT";
export type QualitativeConfidence = "LOW" | "MODERATE" | "HIGH";

export interface Scenario {
  label: "BULL" | "BASE" | "BEAR";
  description: string;
  targetPrice: number;
}

export interface TradingSignal {
  symbol: string;
  timeframe: Timeframe;
  generatedAt: number; // unix seconds
  // Order flow is deliberately the first analytical field: it's meant to
  // render at the TOP of the signal view, ahead of the 8-factor score.
  // It is null when no tick series was supplied (e.g. demo mode) or ticks
  // are unavailable — never silently defaulted to neutral.
  orderFlow: OrderFlowRead | null;
  direction: Direction;
  score: number; // 0-100, analytical alignment only. Order flow does NOT
  // feed into this weighted score — it stays a separate, prominent read so
  // adding it never silently changes the documented 20/15/15/15/10/10/10/5
  // weighting or invalidates existing calibration evidence.
  confidence: QualitativeConfidence; // qualitative label, never a probability
  historicalWinProbability: CalibratedProbability | null; // null unless evidence-gated
  factors: FactorScores;
  weightsUsed: FactorWeights;
  regime: RegimeLabel;
  structure: StructureLabel;
  entryZone: { low: number; high: number };
  invalidation: number; // ATR-based stop level
  targets: [number, number, number];
  evidence: string[]; // short factual bullet points, no free-form LLM text
  scenarios: [Scenario, Scenario, Scenario];
  expiresAt: number; // unix seconds
  dataStatus: DataStatus;
  regimeOverlay?: "TREND_UP" | "TREND_DOWN" | "MEAN_REVERT";
  engineeredFeatures?: EngineeredFeatures;
  strategyMode?: "TREND" | "MEAN_REVERT";
  researchRiskPercent?: number;
  stopAtrMultiple?: number;
  mlCommittee?: MLCommitteeRead;
}

export interface EngineeredFeatures {
  rsi14ZScore50: number;
  directionalEfficiency: number;
  tickVolumeRatio: number;
  bidAskPressure: number;
  anchoredVwapDeviationZ: number;
  atr14Percentile: number;
}

export interface MLCommitteeRead {
  probability: number; // signed [-1, 1]
  confidence: number; // [0, 1]
  ruleForestVote: number;
  patternVote: number;
  trainingSamples: number;
  available: boolean;
}

// ---------------------------------------------------------------------------
// Order flow (tick-rule approximation)
// ---------------------------------------------------------------------------
//
// IMPORTANT: forex/CFD markets are decentralized OTC — there is no single
// tape, so no retail feed (including MT5) exposes verified aggressor-side
// data the way a centralized exchange does. Everything in this section is
// the standard "tick rule" approximation (an uptick is treated as buy
// pressure, a downtick as sell pressure). It is a widely-used, honest
// approximation — not verified order-flow data. Label it as such in any UI.

export interface Tick {
  time: number; // unix seconds (fractional allowed for sub-second ticks)
  bid: number;
  ask: number;
  last?: number;
  volume: number; // tick/lot volume as reported by the venue
}

export type TickDirection = "UP" | "DOWN" | "FLAT";

export type OrderFlowPressure =
  | "STRONG_BUY_PRESSURE"
  | "BUY_PRESSURE"
  | "NEUTRAL"
  | "SELL_PRESSURE"
  | "STRONG_SELL_PRESSURE";

export interface BarOrderFlow {
  barTime: number;
  buyVolume: number;
  sellVolume: number;
  delta: number; // buyVolume - sellVolume
  cumulativeDelta: number; // running total across the supplied series
  tickCount: number;
  absorption: boolean; // high volume, little net price movement
}

export interface OrderFlowRead {
  method: "TICK_RULE_APPROXIMATION"; // never claim more than this
  symbol: string;
  pressure: OrderFlowPressure;
  cumulativeDeltaTrend: "RISING" | "FALLING" | "FLAT";
  recentBars: BarOrderFlow[];
  absorptionDetected: boolean;
  evidence: string[];
  dataStatus: DataStatus;
  ticksAnalyzed: number;
}

// ---------------------------------------------------------------------------
// Risk engine
// ---------------------------------------------------------------------------

export type SizingSource = "MODEL_ONLY" | "BROKER_VERIFIED";

export interface BrokerSpec {
  tickSize: number;
  tickValue: number;
  contractSize: number;
  minVolume: number;
  maxVolume: number;
  lotStep: number;
  leverage: number;
  marginRate?: number; // OANDA-style margin rate override
  pipLocation?: number; // OANDA-style pip location override
}

export interface RiskInput {
  accountBalance: number;
  riskPercent: number; // e.g. 1 = 1%
  entry: number;
  stop: number;
  target?: number;
  leverage: number;
  brokerSpec?: BrokerSpec;
}

export interface RiskWarning {
  code:
    | "RISK_ABOVE_2_PERCENT"
    | "REWARD_BELOW_1_5R"
    | "MARGIN_ABOVE_HALF_EQUITY"
    | "MISSING_BROKER_SPEC"
    | "PROVIDER_UNIT_CONSTRAINT";
  message: string;
}

export interface RiskResult {
  maxLossAmount: number;
  positionUnits: number;
  lossPerUnit: number;
  rewardToRiskRatio: number | null;
  requiredMargin: number | null;
  sizingSource: SizingSource;
  warnings: RiskWarning[];
}

export class RiskValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RiskValidationError";
  }
}

// ---------------------------------------------------------------------------
// Backtesting
// ---------------------------------------------------------------------------

export type StrategyId = "TREND_FOLLOWING" | "PULLBACK" | "MEAN_REVERSION";

export interface BacktestTrade {
  strategy: StrategyId;
  direction: Direction;
  entryTime: number;
  entryPrice: number;
  exitTime: number;
  exitPrice: number;
  stop: number;
  target: number;
  outcome: "WIN" | "LOSS";
  rMultiple: number;
  partition: "TRAIN" | "VALIDATION" | "TEST";
}

export interface BacktestResult {
  strategy: StrategyId;
  symbol: string;
  timeframe: Timeframe;
  trades: BacktestTrade[];
  equityCurve: { time: number; equity: number }[];
  stats: {
    train: PartitionStats;
    validation: PartitionStats;
    test: PartitionStats;
  };
  demoOnly: boolean;
}

export interface PartitionStats {
  tradeCount: number;
  winRate: number | null;
  avgRMultiple: number | null;
  profitFactor: number | null;
  maxDrawdownR: number | null;
}

// ---------------------------------------------------------------------------
// Calibration
// ---------------------------------------------------------------------------

export type OutcomeSource = "OUT_OF_SAMPLE" | "LIVE_RESOLVED";

export interface ResolvedOutcome {
  signalId: string;
  score: number;
  outcome: "WIN" | "LOSS";
  resolvedAt: string; // ISO timestamp
  source: OutcomeSource;
  symbol?: string;
  timeframe?: Timeframe;
}

export interface CalibratedProbability {
  probability: number; // 0-1
  wilsonLow: number;
  wilsonHigh: number;
  sampleSize: number;
  scoreSegment: [number, number];
}

export interface CalibrationAvailability {
  available: boolean;
  totalOutcomes: number;
  wins: number;
  losses: number;
  reason?: string;
}
