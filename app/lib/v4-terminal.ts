export type V4Section =
  | "COMMAND_CENTER"
  | "MARKETS"
  | "SIGNALS"
  | "ATLAS_AI"
  | "CALENDAR"
  | "PERFORMANCE"
  | "QUANT_LAB"
  | "MODELS"
  | "MT5"
  | "SETTINGS";

export type PublicationDecision =
  | "PUBLISHED"
  | "WAIT"
  | "NO_TRADE"
  | "QUANT_VETO"
  | "RISK_VETO"
  | "EVENT_BLOCK"
  | "DATA_DEGRADED"
  | "INSUFFICIENT_DATA"
  | "MODEL_UNCERTAIN";

export interface V4TimeframeSummary {
  timeframe: "15M" | "1H" | "4H" | "1D";
  last_close: number | null;
  last_bar_at: string | null;
  trend: string | null;
  structure: string | null;
  regime: string | null;
  volatility: string | null;
  raw_probability: number | null;
  meta_probability: number | null;
  calibrated_probability: number | null;
  event_risk: string | null;
  quant_status: string | null;
  selected: boolean;
  availability: "AVAILABLE" | "DATA_UNAVAILABLE";
}

export interface V4MarketSummary {
  instrument: string;
  asset_class: "FOREX" | "COMMODITY";
  bid: number | null;
  ask: number | null;
  price: number | null;
  spread: number | null;
  spread_bps: number | null;
  captured_at: string | null;
  session: string | null;
  broker: string | null;
  server: string | null;
  account_mode: string | null;
  last_tick_at: string | null;
  tick_age_seconds: number | null;
  tick_count: number | null;
  volume_provenance: string | null;
  mt5_status: string | null;
  data_quality: string | null;
  data_quality_score: number | null;
  data_issues: string[];
  liquidity: string | null;
  mtf_alignment: string | null;
  best_timeframe: V4TimeframeSummary["timeframe"] | null;
  calibrated_probability: number | null;
  grade: "B" | "A" | "A+" | "A+++++" | null;
  expected_value_net: number | null;
  regime: string | null;
  event_risk: string | null;
  quant_status: string | null;
  decision: PublicationDecision | null;
  decision_reasons: string[];
  signal_id: string | null;
  signal_direction: "LONG" | "SHORT" | null;
  tracking_state: string | null;
  timeframes: V4TimeframeSummary[];
  availability: "AVAILABLE" | "DEGRADED" | "DATA_UNAVAILABLE";
}

export interface CanonicalSignal {
  signal_id: string;
  schema_version: "4.0.0";
  instrument: string;
  direction: "LONG" | "SHORT";
  timeframe: V4TimeframeSummary["timeframe"];
  entry: { type: string; low: number; high: number };
  stop_loss: number;
  targets: Array<{ name: "TP1" | "TP2" | "TP3"; price: number; reward_risk: number }>;
  raw_probability: number;
  meta_probability: number;
  calibrated_probability: number;
  grade: "B" | "A" | "A+" | "A+++++";
  expected_r: number;
  expected_value_net: number;
  approved_risk_fraction: number;
  regime: string;
  macro_bias: string;
  macro_availability: string;
  event_risk: string;
  data_quality_score: number;
  quant_validation: { status: "PASS"; reasons: string[] };
  risk_validation: { status: "PASS"; reasons: string[] };
  drivers: string[];
  contradictions: string[];
  invalidations: string[];
  lineage: Record<string, string | null>;
  model_versions: Record<string, string>;
  created_at: string;
  expires_at: string;
  publication_decision: "PUBLISHED";
  signal_authority: true;
  research_only: true;
  execution_enabled: false;
  order_submission: false;
}

export interface V4TrackingState {
  signal_id: string;
  state: string;
  started_at: string;
  activated_at: string | null;
  terminal_at: string | null;
  entry_fill: number | null;
  highest_target_hit: number;
  live_price: number | null;
  live_pips: number | null;
  live_r: number | null;
  mfe_price: number;
  mae_price: number;
  distance_to_stop: number | null;
  distance_to_next_target: number | null;
  elapsed_seconds: number;
  spread: number | null;
  event_sequence: number;
  terminal: boolean;
  server_authoritative: true;
  browser_authority: false;
  execution_enabled: false;
}

export interface V4SignalSummary {
  signal: CanonicalSignal;
  tracking: V4TrackingState | null;
  event_count: number;
  revision_count: number;
}

export interface V4DecisionSummary {
  publication_attempt_id: string;
  created_at: string;
  instrument: string;
  candidate_id: string;
  decision: PublicationDecision;
  atlas_probability: number | null;
  grade: V4MarketSummary["grade"];
  reasons: string[];
  signal_id: string | null;
}

export interface V4CalendarEvent {
  event_id: string;
  instrument: string;
  name: string;
  currency: string;
  scheduled_at: string;
  minutes_to_event: number | null;
  time_precision: "EXACT" | "DATE_ONLY";
  risk: string;
  candidate_veto: boolean;
  affected_signal_ids: string[];
}

export interface V4ModelSummary {
  model_id: string;
  model_version: string;
  family: string;
  status: string;
  created_at: string;
  supported_instruments: string[];
  supported_timeframes: V4TimeframeSummary["timeframe"][];
  test_sample_count: number;
  test_brier_score: number;
  test_mean_net_return: number;
  decision_influence: false;
}

export interface V4AIReviewSummary {
  bundle_id: string;
  candidate_id: string;
  instrument: string;
  status: string;
  specialist_statuses: Record<string, string>;
  contradictions: string[];
  invalidations: string[];
  total_latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  external_api_cost_usd: 0;
  non_voting: true;
}

export type EvidenceStatus = "AVAILABLE" | "PARTIAL" | "DATA_UNAVAILABLE";

export interface V4PerformanceSlice {
  dimension: "ALL" | "INSTRUMENT" | "TIMEFRAME" | "REGIME";
  key: string;
  published_count: number;
  resolved_count: number;
  win_count: number;
  loss_count: number;
  win_rate: number | null;
  mean_realized_r: number | null;
  cumulative_realized_r: number | null;
  signal_ids: string[];
  tracking_ids: string[];
  status: EvidenceStatus;
}

export interface V4ReliabilityBin {
  lower_bound: number;
  upper_bound: number;
  sample_count: number;
  mean_probability: number | null;
  observed_rate: number | null;
  confidence_low: number | null;
  confidence_high: number | null;
}

export interface V4CalibrationDashboardItem {
  calibration_id: string;
  source_validation_run_id: string;
  meta_model_id: string;
  created_at: string;
  status: string;
  method: string;
  sample_count: number;
  evaluation_sample_count: number;
  brier_before: number | null;
  brier_after: number | null;
  ece_after: number | null;
  uncertainty: string;
  reliability_bins: V4ReliabilityBin[];
}

export interface V4BacktestDashboardItem {
  backtest_run_id: string;
  instrument: string;
  created_at: string;
  sample_count: number;
  win_rate: number | null;
  mean_net_return: number | null;
  cumulative_net_return: number;
  profit_factor: number | null;
  maximum_drawdown: number;
  mean_gross_return: number | null;
  cost_drag: number | null;
  spread_bps: number;
  slippage_bps: number;
  commission_bps_round_turn: number;
  daily_swap_bps: number;
}

export interface V4RobustnessDashboardItem {
  robustness_run_id: string;
  status: string;
  sample_count: number;
  bootstrap_samples: number;
  variants_tested: number;
  mean_return: number | null;
  stressed_mean_return: number | null;
  deflated_sharpe_ratio: number | null;
  probability_backtest_overfitting: number | null;
  reason: string;
}

export interface V4ExperimentDashboardItem {
  experiment_id: string;
  registered_at: string;
  name: string;
  status: string;
  total_variants: number;
  feature_sets: string[];
  model_families: string[];
  automatic_promotion: false;
}

export interface V4ModelComparisonItem {
  model_id: string;
  model_version: string;
  family: string;
  created_at: string;
  sample_count: number;
  brier_score: number;
  log_loss: number;
  balanced_accuracy: number | null;
  mean_net_return: number;
  baseline_mean_net_return: number;
  status: string;
  run_ids: string[];
}

export interface V4VetoAnalytics {
  attempt_count: number;
  decision_counts: Record<string, number>;
  reason_counts: Record<string, number>;
  publication_attempt_ids: string[];
}

export interface V4PerformanceDashboard {
  version: "performance-dashboard-v1";
  generated_at: string;
  status: EvidenceStatus;
  performance_slices: V4PerformanceSlice[];
  calibrations: V4CalibrationDashboardItem[];
  backtests: V4BacktestDashboardItem[];
  robustness_runs: V4RobustnessDashboardItem[];
  experiments: V4ExperimentDashboardItem[];
  model_comparisons: V4ModelComparisonItem[];
  vetoes: V4VetoAnalytics;
  source_issues: string[];
  all_metrics_traceable: true;
  fabricated_metrics: false;
  browser_calculation_authority: false;
  research_only: true;
  execution_enabled: false;
}

export interface V4TerminalWorkspace {
  engine: "ATLAS";
  version: "4.0.0";
  phase: 15;
  generated_at: string;
  selected_instrument: string;
  markets: V4MarketSummary[];
  signals: V4SignalSummary[];
  decisions: V4DecisionSummary[];
  calendar: V4CalendarEvent[];
  models: V4ModelSummary[];
  ai_reviews: V4AIReviewSummary[];
  performance: V4PerformanceDashboard;
  counts: {
    published_signals: number;
    active_signals: number;
    pending_signals: number;
    wait_markets: number;
    quant_vetoes: number;
    risk_vetoes: number;
    event_blocks: number;
    degraded_markets: number;
  };
  source_issues: string[];
  metrics_status: EvidenceStatus;
  browser_signal_authority: false;
  browser_tracking_authority: false;
  research_only: true;
  execution_enabled: false;
  order_submission: false;
}

export type V4OperationalState = "HEALTHY" | "DEGRADED" | "UNAVAILABLE" | "DISABLED";

export interface V4OperationalComponent {
  name: "DATABASE" | "REDIS" | "MT5" | "DATA" | "MODELS" | "LLM";
  state: V4OperationalState;
  configured: boolean;
  checked_at: string;
  latency_ms: number;
  evidence_count: number;
  message: string;
  issues: string[];
}

export interface V4OperationalHealth {
  engine: "ATLAS";
  version: "4.0.0";
  phase: 15;
  checked_at: string;
  status: "READY" | "DEGRADED" | "NOT_READY";
  database: V4OperationalComponent;
  redis: V4OperationalComponent;
  mt5: V4OperationalComponent;
  data: V4OperationalComponent;
  models: V4OperationalComponent;
  llm: V4OperationalComponent;
  analytical_signal_authority: true;
  browser_signal_authority: false;
  research_only: true;
  execution_enabled: false;
  order_submission: false;
}

export type V4OrchestrationRunState = "COMPLETED" | "COMPLETED_WITH_BLOCKERS" | "FAILED";

export interface V4OrchestrationInstrumentResult {
  instrument: string;
  state: V4OrchestrationRunState;
  blocker_codes: string[];
  snapshot_id: string | null;
  feature_set_id: string | null;
  context_set_id: string | null;
  model_id: string | null;
  robustness_run_id: string | null;
  candidate_id: string | null;
  publication_attempt_id: string | null;
  signal_id: string | null;
  execution_enabled: false;
  order_submission: false;
}

export interface V4OrchestrationStatus {
  engine: "ATLAS";
  version: "4.0.0";
  enabled: boolean;
  running: boolean;
  cycle_in_progress: boolean;
  interval_seconds: number;
  instruments: string[];
  current_instrument: string | null;
  current_stage: string | null;
  instruments_completed: number;
  instruments_total: number;
  cycles_started: number;
  cycles_completed: number;
  consecutive_cycle_failures: number;
  service_started_at: string | null;
  current_run_started_at: string | null;
  last_cycle_started_at: string | null;
  last_cycle_completed_at: string | null;
  next_cycle_at: string | null;
  last_run_id: string | null;
  last_state: V4OrchestrationRunState | null;
  last_error: string | null;
  latest_results: V4OrchestrationInstrumentResult[];
  analytical_signal_authority: true;
  research_only: true;
  execution_enabled: false;
  order_submission: false;
}

export type MarketFilter = "ALL" | "FOREX" | "COMMODITY" | "SIGNALS" | "A_PLUS" | "P90" | "EVENT_SAFE" | "LONG" | "SHORT" | "WAIT";

export function filterMarkets(markets: V4MarketSummary[], filter: MarketFilter): V4MarketSummary[] {
  if (filter === "ALL") return markets;
  if (filter === "FOREX" || filter === "COMMODITY") return markets.filter((row) => row.asset_class === filter);
  if (filter === "SIGNALS") return markets.filter((row) => row.decision === "PUBLISHED");
  if (filter === "A_PLUS") return markets.filter((row) => row.grade === "A+" || row.grade === "A+++++");
  if (filter === "P90") return markets.filter((row) => (row.calibrated_probability ?? 0) >= 0.9);
  if (filter === "EVENT_SAFE") return markets.filter((row) => row.event_risk === "LOW" || row.event_risk === "MODERATE");
  if (filter === "WAIT") return markets.filter((row) => row.decision === "WAIT");
  return markets.filter((row) => row.signal_direction === filter && row.decision === "PUBLISHED");
}

export function decisionTone(decision: PublicationDecision | null): "positive" | "negative" | "caution" | "neutral" {
  if (decision === "PUBLISHED") return "positive";
  if (decision === "QUANT_VETO" || decision === "RISK_VETO" || decision === "EVENT_BLOCK" || decision === "DATA_DEGRADED") return "negative";
  if (decision === "WAIT" || decision === "MODEL_UNCERTAIN" || decision === "INSUFFICIENT_DATA") return "caution";
  return "neutral";
}

export function displayLabel(value: string | null | undefined): string {
  return value ? value.replaceAll("_", " ") : "DATA UNAVAILABLE";
}

export function formatProbability(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}
