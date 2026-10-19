export const FUND_OS_SYMBOLS = [
  "EURUSD",
  "GBPUSD",
  "USDJPY",
  "USDCHF",
  "AUDUSD",
  "USDCAD",
  "NZDUSD",
  "EURGBP",
  "XAUUSD",
  "XAGUSD",
  "USOIL",
  "UKOIL",
] as const;

export const DEFAULT_MANDATE_YAML = `mandate:
  name: Atlas Macro Tilt
  version: 1.0.0
  universe:
    asset_class: commodity
    symbols: [XAUUSD, XAGUSD]
  desks:
    enabled: [technical, macro, pattern]
    weights:
      technical: 0.55
      macro: 0.25
      pattern: 0.15
    llm_enabled: [technical, macro]
  risk:
    max_position_pct: 0.10
    max_leverage: 1.0
    daily_drawdown_limit: 0.03
    weekly_drawdown_limit: 0.06
    correlation_threshold: 0.80
  execution:
    mode: research
    rebalance_cadence: 1d
    exchange: none
  backtest:
    warmup_bars: 50
    eval_bars: 180
    benchmark: buy_and_hold
`;

export interface FundOsStatus {
  online: boolean;
  phase: number | null;
  agentEngine: boolean;
  mandateEngine: boolean;
  backtestEngine: boolean;
  database: boolean;
  redis: boolean;
  executionEnabled: false;
  issue: string | null;
}

export interface MandateValidation {
  mandate_key: string;
  phase: number;
  document: {
    mandate: {
      name: string;
      version: string;
      universe: { asset_class: string; symbols: string[] };
      desks: { enabled: string[]; weights: Record<string, number>; llm_enabled: string[] };
      risk: {
        max_position_pct: number;
        max_leverage: number;
        daily_drawdown_limit: number;
        weekly_drawdown_limit: number;
        correlation_threshold: number;
      };
      execution: { mode: string; rebalance_cadence: string; exchange: string };
      backtest: { warmup_bars: number; eval_bars: number; benchmark: string };
    };
  };
  lifecycle: {
    stage: string;
    backtest_complete: boolean;
    paper_eligible: false;
    live_eligible: false;
    execution_enabled: false;
    reasons: string[];
  };
  execution_enabled: false;
}

export interface BacktestMetrics {
  total_return: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  profit_factor: number | null;
  win_rate: number;
  benchmark_return: number;
  excess_return: number;
  trade_count: number;
}

export interface BacktestEquityPoint {
  timestamp: string;
  equity: number;
  benchmark_equity: number;
  period_return: number;
}

export interface BacktestTrade {
  symbol: string;
  side: "LONG" | "SHORT";
  signal_time: string;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  return_pct: number;
  weighted_return_pct: number;
}

export interface BacktestAuditEvent {
  sequence: number;
  timestamp: string;
  summary: string;
  signals: Record<string, "LONG" | "SHORT" | "HOLD">;
}

export interface FundOsBacktest {
  mandate_key: string;
  run_key: string;
  phase: number;
  report: {
    engine: string;
    mandate_name: string;
    mandate_version: string;
    symbols: string[];
    warmup_bars: number;
    evaluation_bars: number;
    metrics: BacktestMetrics;
    trades: BacktestTrade[];
    equity: BacktestEquityPoint[];
    audit: BacktestAuditEvent[];
    no_lookahead: true;
    research_only: true;
    execution_enabled: false;
  };
  lifecycle: MandateValidation["lifecycle"];
  market_data: {
    source: "MT5";
    timeframe: "1H";
    closed_bars_only: true;
    bars_per_symbol: number;
  };
  execution_enabled: false;
}

export interface BacktestRunSummary {
  run_key: string;
  mandate_key: string;
  mandate_name: string;
  mandate_version: string;
  symbols: string[];
  metrics: BacktestMetrics;
  lifecycle: MandateValidation["lifecycle"];
  evaluated_at: string | null;
  research_only: true;
  execution_enabled: false;
}

export interface AgentDeskSignal {
  ticker: string;
  desk: string;
  signal: "BULLISH" | "BEARISH" | "NEUTRAL";
  confidence: number;
  key_evidence: string[];
  risk_factors: string[];
  feedback: string;
}

export interface AgentDebate {
  ticker: string;
  bull_arguments: string[];
  bear_arguments: string[];
  consensus: "BULLISH" | "BEARISH" | "NEUTRAL";
  moderator_summary: string;
  disagreements: string[];
}

export interface AgentArbitration {
  ticker: string;
  final_score: number;
  threshold: number;
  intent: "LONG" | "SHORT" | "HOLD";
  contributions: Record<string, number>;
  rationale: string;
}

export interface AgentRiskAssessment {
  ticker: string;
  approved: boolean;
  vetoed: boolean;
  checks: Array<{ name: string; passed: boolean; detail: string }>;
  veto_reasons: string[];
  approved_weight: number;
  manager_override_recorded: boolean;
}

export interface AgentResearchState {
  universe: string[];
  market_scan: Record<string, Record<string, unknown>>;
  desk_signals: Record<string, AgentDeskSignal[]>;
  debate_transcript: AgentDebate[];
  arbitration: Record<string, AgentArbitration>;
  risk_assessment: Record<string, AgentRiskAssessment>;
  portfolio_proposal: {
    positions: Array<{ ticker: string; intent: string; target_weight: number; current_weight: number; rebalance_delta: number; reason: string }>;
    gross_target_weight: number;
    cash_weight: number;
    method: string;
    research_only: true;
    execution_enabled: false;
  };
  risk_veto: boolean;
  manager_override: string | null;
  execution_plan: [];
  audit_trail: Array<{ sequence: number; stage: string; summary: string; metadata: Record<string, unknown> }>;
}

export interface FundOsAgentTrace {
  run_key: string;
  phase: 2;
  research_only: true;
  execution_enabled: false;
  state: AgentResearchState;
}

export interface FundOsHistory {
  backtests: { count: number; runs: BacktestRunSummary[]; execution_enabled: false };
  research: {
    count: number;
    runs: Array<{ run_key: string; universe: string[]; risk_veto: boolean; state: AgentResearchState; research_only: true; execution_enabled: false }>;
    execution_enabled: false;
  };
  execution_enabled: false;
}

export function metricPercent(value: number): string {
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
}

export function chartPoints(
  rows: BacktestEquityPoint[],
  field: "equity" | "benchmark_equity",
  width = 820,
  height = 230,
): string {
  if (rows.length === 0) return "";
  const values = rows.flatMap((row) => [row.equity, row.benchmark_equity]);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = Math.max(high - low, 0.000001);
  const xStep = rows.length === 1 ? 0 : width / (rows.length - 1);
  return rows.map((row, index) => {
    const x = index * xStep;
    const y = height - ((row[field] - low) / span) * height;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}
