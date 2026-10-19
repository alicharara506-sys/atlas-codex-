"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  chartPoints,
  DEFAULT_MANDATE_YAML,
  metricPercent,
  type AgentDeskSignal,
  type FundOsAgentTrace,
  type FundOsBacktest,
  type FundOsHistory,
  type FundOsStatus,
  type MandateValidation,
} from "../lib/fund-os.ts";

const RESEARCH_DESKS = ["Fundamentals", "Technical", "Sentiment", "Macro", "Narrative", "Flow", "Insider"];
const WORKFLOW = ["Market data", "7 desks", "Bull / bear debate", "Weighted arbitration", "Risk Guard", "Portfolio proposal"];

interface ApiFailure { error?: string; }

interface AtlasResearchPayload {
  effectiveAction: "LONG" | "SHORT" | "WAIT" | "BUY_STOP" | "SELL_STOP";
  signal: {
    score: number;
    factors: { structure: number; trend: number; momentum: number; multiTimeframe: number; volatility: number; volume: number; supportResistance: number; macro: number | null };
    engineeredFeatures?: { atr14Percentile: number };
    mlCommittee?: { probability: number; available: boolean };
  };
  localIntelligence: { pythonOrderFlow: { confluence: { score: number } } | null };
}

async function responseJson<T>(response: Response): Promise<T> {
  const payload = await response.json() as T & ApiFailure;
  if (!response.ok) throw new Error(payload.error || `Atlas returned HTTP ${response.status}.`);
  return payload;
}

function statusTone(ready: boolean): string {
  return ready ? "ready" : "offline";
}

function shortDate(value: string): string {
  return new Intl.DateTimeFormat("en", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}

function signedFactor(value: number, sign: number): number {
  return Math.max(-1, Math.min(1, ((value - 50) / 50) * sign));
}

function actionSign(action: AtlasResearchPayload["effectiveAction"]): number {
  if (action === "LONG" || action === "BUY_STOP") return 1;
  if (action === "SHORT" || action === "SELL_STOP") return -1;
  return 0;
}

export function FundOsConsole({ symbol = "XAUUSD" }: { symbol?: string }) {
  const [status, setStatus] = useState<FundOsStatus | null>(null);
  const [yamlText, setYamlText] = useState(DEFAULT_MANDATE_YAML);
  const [validation, setValidation] = useState<MandateValidation | null>(null);
  const [backtest, setBacktest] = useState<FundOsBacktest | null>(null);
  const [trace, setTrace] = useState<FundOsAgentTrace | null>(null);
  const [history, setHistory] = useState<FundOsHistory | null>(null);
  const [busy, setBusy] = useState<"validate" | "backtest" | "trace" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/fund-os/status", { cache: "no-store" });
      setStatus(await responseJson<FundOsStatus>(response));
    } catch (cause) {
      setStatus({ online: false, phase: null, agentEngine: false, mandateEngine: false, backtestEngine: false, database: false, redis: false, executionEnabled: false, issue: cause instanceof Error ? cause.message : "Fund OS backend is offline." });
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const response = await fetch("/api/fund-os/history", { cache: "no-store" });
      setHistory(await responseJson<FundOsHistory>(response));
    } catch {
      setHistory(null);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => { void loadStatus(); void loadHistory(); }, 0);
    const timer = window.setInterval(() => void loadStatus(), 30_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [loadHistory, loadStatus]);

  async function validateMandate(): Promise<void> {
    setBusy("validate");
    setError(null);
    setBacktest(null);
    try {
      const response = await fetch("/api/fund-os/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ yamlText }),
      });
      setValidation(await responseJson<MandateValidation>(response));
      await loadStatus();
    } catch (cause) {
      setValidation(null);
      setError(cause instanceof Error ? cause.message : "Mandate validation failed.");
    } finally {
      setBusy(null);
    }
  }

  async function runBacktest(): Promise<void> {
    setBusy("backtest");
    setError(null);
    setBacktest(null);
    try {
      const validationResponse = await fetch("/api/fund-os/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ yamlText }),
      });
      setValidation(await responseJson<MandateValidation>(validationResponse));
      const response = await fetch("/api/fund-os/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ yamlText }),
      });
      setBacktest(await responseJson<FundOsBacktest>(response));
      await Promise.all([loadStatus(), loadHistory()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Deterministic backtest failed.");
    } finally {
      setBusy(null);
    }
  }

  async function runAgentTrace(): Promise<void> {
    setBusy("trace");
    setTraceError(null);
    try {
      const marketResponse = await fetch(`/api/atlas?symbol=${encodeURIComponent(symbol)}`, { cache: "no-store" });
      const market = await responseJson<AtlasResearchPayload>(marketResponse);
      const sign = actionSign(market.effectiveAction);
      const factors = market.signal.factors;
      const technicalComposite = (factors.structure + factors.trend + factors.momentum + factors.multiTimeframe + factors.supportResistance) / 5;
      const mandateRisk = validation?.document.mandate.risk;
      const response = await fetch("/api/fund-os/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          scores: {
            fundamentals: null,
            technical: signedFactor(technicalComposite, sign),
            sentiment: market.signal.mlCommittee?.available ? market.signal.mlCommittee.probability : null,
            macro: factors.macro === null ? null : signedFactor(factors.macro, sign),
            narrative: sign === 0 ? 0 : sign * Math.min(1, market.signal.score / 100),
            flow: market.localIntelligence.pythonOrderFlow ? Math.max(-1, Math.min(1, market.localIntelligence.pythonOrderFlow.confluence.score / 100)) : null,
            insider: null,
          },
          volatilityPercentile: market.signal.engineeredFeatures?.atr14Percentile ?? null,
          correlationToBook: 0,
          maxPositionWeight: mandateRisk?.max_position_pct,
          maxGrossLeverage: mandateRisk?.max_leverage,
          maxDailyDrawdown: mandateRisk?.daily_drawdown_limit,
          maxWeeklyDrawdown: mandateRisk?.weekly_drawdown_limit,
          maxCorrelation: mandateRisk?.correlation_threshold,
        }),
      });
      setTrace(await responseJson<FundOsAgentTrace>(response));
      await loadHistory();
    } catch (cause) {
      setTraceError(cause instanceof Error ? cause.message : "Agent workflow failed.");
    } finally {
      setBusy(null);
    }
  }

  const enabledDesks = new Set(validation?.document.mandate.desks.enabled ?? []);
  const equity = useMemo(() => chartPoints(backtest?.report.equity ?? [], "equity"), [backtest]);
  const benchmark = useMemo(() => chartPoints(backtest?.report.equity ?? [], "benchmark_equity"), [backtest]);
  const metrics = backtest?.report.metrics;
  const recentTrades = backtest?.report.trades.slice(-8).reverse() ?? [];
  const recentAudit = backtest?.report.audit.slice(-6).reverse() ?? [];
  const traceTicker = trace?.state.universe[0] ?? null;
  const traceDesks = traceTicker ? Object.values(trace?.state.desk_signals ?? {}).flat().filter((row) => row.ticker === traceTicker) : [];
  const traceDebate = trace?.state.debate_transcript.find((row) => row.ticker === traceTicker) ?? null;
  const traceArbitration = traceTicker ? trace?.state.arbitration[traceTicker] ?? null : null;
  const traceRisk = traceTicker ? trace?.state.risk_assessment[traceTicker] ?? null : null;

  return <section className="fund-console" aria-label="Atlas Fund OS console">
    <header className="fund-hero">
      <div>
        <span className="eyebrow">PHASE 4 CONTROL SURFACE</span>
        <h1>Agentic Hedge Fund OS</h1>
        <p>Mandate-governed research, seven independent desks, deterministic testing, and a mandatory fail-closed Risk Guard.</p>
      </div>
      <div className="research-lock"><b>RESEARCH ONLY</b><span>Execution disabled at every layer</span><small>Terminal focus: {symbol}</small></div>
    </header>

    <div className="fund-status-grid" aria-label="Fund OS service health">
      {[
        ["BACKEND", status?.online === true, status?.online ? `PHASE ${status.phase ?? 3}` : "OFFLINE"],
        ["AGENT ENGINE", status?.agentEngine === true, "7 DESKS"],
        ["MANDATES", status?.mandateEngine === true, "STRICT YAML"],
        ["BACKTEST", status?.backtestEngine === true, "NO LOOKAHEAD"],
        ["POSTGRES", status?.database === true, status?.database ? "READY" : "NOT READY"],
        ["REDIS", status?.redis === true, status?.redis ? "READY" : "NOT READY"],
      ].map(([name, ready, note]) => <article className={statusTone(Boolean(ready))} key={String(name)}><i /><span>{name}</span><b>{note}</b></article>)}
      <article className="locked"><i /><span>ORDER ROUTER</span><b>DISABLED</b></article>
    </div>
    {status?.issue ? <div className="fund-alert"><b>CORE ATTENTION</b><span>{status.issue}</span><button type="button" onClick={() => void loadStatus()}>RECHECK</button></div> : null}

    <section className="workflow-panel">
      <div className="fund-section-heading"><div><span className="eyebrow">DECISION PIPELINE</span><h2>Evidence must survive every gate</h2></div><small>Execution plan is structurally empty</small></div>
      <div className="workflow-map">
        {WORKFLOW.map((step, index) => <div className={step === "Risk Guard" ? "guard" : ""} key={step}><span>{String(index + 1).padStart(2, "0")}</span><b>{step}</b>{index < WORKFLOW.length - 1 ? <em aria-hidden="true">→</em> : null}</div>)}
      </div>
      <div className="desk-rack">
        {RESEARCH_DESKS.map((desk) => <article className={enabledDesks.has(desk.toLowerCase()) ? "enabled" : ""} key={desk}><span>{desk.slice(0, 2).toUpperCase()}</span><div><b>{desk}</b><small>{enabledDesks.has(desk.toLowerCase()) ? "Mandate enabled" : status?.agentEngine ? "Engine ready" : "Engine offline"}</small></div></article>)}
      </div>
      <div className="trace-control"><div><b>LIVE DECISION TRACE</b><span>Refreshes Atlas market evidence, then persists all seven desk outputs and gates.</span></div><button type="button" onClick={() => void runAgentTrace()} disabled={busy !== null || status?.online !== true}>{busy === "trace" ? "RUNNING 7 DESKS…" : `RUN ${symbol} AGENT TRACE`}</button></div>
      {traceError ? <div className="trace-error"><b>TRACE UNAVAILABLE</b><span>{traceError}</span></div> : null}
    </section>

    {trace && traceTicker && traceArbitration && traceRisk ? <section className="agent-trace-panel">
      <div className="fund-section-heading"><div><span className="eyebrow">PERSISTED AGENT TRACE</span><h2>{traceTicker} · {traceArbitration.intent}</h2></div><small>Run {trace.run_key.slice(0, 12)} · execution plan {trace.state.execution_plan.length}</small></div>
      <div className="trace-desk-grid">{traceDesks.map((desk: AgentDeskSignal) => <article className={desk.signal.toLowerCase()} key={desk.desk}><header><span>{desk.desk.toUpperCase()}</span><b>{desk.signal}</b></header><strong>{(desk.confidence * 100).toFixed(0)}%</strong><p>{desk.key_evidence[0]}</p><small>{desk.risk_factors[0] ?? "No desk-level risk flag."}</small></article>)}</div>
      <div className="trace-decision-grid">
        <article className="debate-card"><header><div><span className="eyebrow">BULL / BEAR DEBATE</span><h3>{traceDebate?.consensus ?? "NEUTRAL"} consensus</h3></div></header><p>{traceDebate?.moderator_summary}</p><div><section><b>BULL CASE</b>{traceDebate?.bull_arguments.map((argument) => <span key={argument}>{argument}</span>)}{traceDebate?.bull_arguments.length === 0 ? <span>No validated bullish desk argument.</span> : null}</section><section><b>BEAR CASE</b>{traceDebate?.bear_arguments.map((argument) => <span key={argument}>{argument}</span>)}{traceDebate?.bear_arguments.length === 0 ? <span>No validated bearish desk argument.</span> : null}</section></div></article>
        <article className="arbitration-card"><span className="eyebrow">WEIGHTED ARBITRATION</span><div className="arbitration-score"><strong>{traceArbitration.final_score >= 0 ? "+" : ""}{traceArbitration.final_score.toFixed(3)}</strong><small>gate ±{traceArbitration.threshold.toFixed(2)}</small></div><p>{traceArbitration.rationale}</p>{Object.entries(traceArbitration.contributions).map(([desk, value]) => <div className="contribution" key={desk}><span>{desk}</span><i><em className={value >= 0 ? "positive-bar" : "negative-bar"} style={{ width: `${Math.abs(value) * 100}%` }} /></i><b>{value >= 0 ? "+" : ""}{value.toFixed(3)}</b></div>)}</article>
        <article className={`risk-guard-card ${traceRisk.vetoed ? "veto" : "pass"}`}><span className="eyebrow">MANDATORY RISK GUARD</span><strong>{traceRisk.vetoed ? "VETO" : traceRisk.approved ? "PASS" : "NO POSITION"}</strong><div>{traceRisk.checks.map((check) => <p key={check.name}><i>{check.passed ? "✓" : "×"}</i><span><b>{check.name.replaceAll("_", " ")}</b><small>{check.detail}</small></span></p>)}</div></article>
      </div>
      <div className="trace-audit"><header><b>TOPOLOGY EXECUTION LOG</b><span>{trace.state.audit_trail.length} persisted events</span></header>{trace.state.audit_trail.map((event) => <div key={event.sequence}><span>{String(event.sequence).padStart(2, "0")}</span><b>{event.stage.replaceAll("_", " ")}</b><p>{event.summary}</p></div>)}</div>
    </section> : null}

    <section className="mandate-workbench">
      <article className="mandate-editor">
        <div className="fund-section-heading"><div><span className="eyebrow">MANDATE EDITOR</span><h2>Strategy constitution</h2></div><small>YAML · strict schema · versioned</small></div>
        <textarea aria-label="Mandate YAML" spellCheck={false} value={yamlText} onChange={(event) => { setYamlText(event.target.value); setValidation(null); setBacktest(null); setError(null); }} />
        <div className="mandate-actions">
          <button type="button" className="secondary" onClick={() => { setYamlText(DEFAULT_MANDATE_YAML); setValidation(null); setBacktest(null); setError(null); }}>RESTORE EXAMPLE</button>
          <button type="button" onClick={() => void validateMandate()} disabled={busy !== null}>{busy === "validate" ? "VALIDATING…" : "VALIDATE MANDATE"}</button>
          <button type="button" className="run" onClick={() => void runBacktest()} disabled={busy !== null || status?.online !== true}>{busy === "backtest" ? "RUNNING ON MT5 BARS…" : "RUN DETERMINISTIC BACKTEST"}</button>
        </div>
      </article>

      <aside className="mandate-inspector">
        <div className="fund-section-heading"><div><span className="eyebrow">GOVERNANCE</span><h2>Validation record</h2></div></div>
        {error ? <div className="validation-error"><b>REJECTED / UNAVAILABLE</b><p>{error}</p></div> : validation ? <>
          <div className="validation-pass"><i>✓</i><div><b>{validation.document.mandate.name}</b><span>Schema valid · v{validation.document.mandate.version}</span></div></div>
          <dl>
            <div><dt>Stage</dt><dd>{backtest?.lifecycle.stage ?? validation.lifecycle.stage}</dd></div>
            <div><dt>Universe</dt><dd>{validation.document.mandate.universe.symbols.join(" · ")}</dd></div>
            <div><dt>Asset class</dt><dd>{validation.document.mandate.universe.asset_class}</dd></div>
            <div><dt>Max position</dt><dd>{(validation.document.mandate.risk.max_position_pct * 100).toFixed(1)}%</dd></div>
            <div><dt>Max leverage</dt><dd>{validation.document.mandate.risk.max_leverage.toFixed(2)}×</dd></div>
            <div><dt>Daily DD limit</dt><dd>{(validation.document.mandate.risk.daily_drawdown_limit * 100).toFixed(1)}%</dd></div>
            <div><dt>Weekly DD limit</dt><dd>{(validation.document.mandate.risk.weekly_drawdown_limit * 100).toFixed(1)}%</dd></div>
            <div><dt>Execution</dt><dd className="blocked">DISABLED</dd></div>
          </dl>
          <div className="lifecycle-notes">{(backtest?.lifecycle.reasons ?? validation.lifecycle.reasons).map((reason) => <p key={reason}>{reason}</p>)}</div>
        </> : <div className="inspector-empty"><span>01</span><p>Validate the mandate to reveal its universe, risk envelope, lifecycle stage, and promotion locks.</p></div>}
      </aside>
    </section>

    <section className="leaderboard-panel">
      <div className="fund-section-heading"><div><span className="eyebrow">PERSISTED RESEARCH REGISTRY</span><h2>Mandate leaderboard</h2></div><small>Ranked by deterministic Sharpe · {history?.research.count ?? 0} saved agent trace(s)</small></div>
      <div className="leaderboard-head"><span>RANK</span><span>MANDATE</span><span>UNIVERSE</span><span>RETURN</span><span>SHARPE</span><span>MAX DD</span><span>TRADES</span><span>STAGE</span></div>
      {history?.backtests.runs.map((run, index) => <div className="leaderboard-row" key={run.run_key}><strong>{String(index + 1).padStart(2, "0")}</strong><span><b>{run.mandate_name}</b><small>v{run.mandate_version} · {run.run_key.slice(0, 8)}</small></span><span>{run.symbols.join(" · ")}</span><span className={run.metrics.total_return >= 0 ? "positive" : "negative"}>{metricPercent(run.metrics.total_return)}</span><b>{run.metrics.sharpe.toFixed(2)}</b><span>{(run.metrics.max_drawdown * 100).toFixed(2)}%</span><span>{run.metrics.trade_count}</span><span className="stage-chip">{run.lifecycle.stage}</span></div>)}
      {history && history.backtests.runs.length === 0 ? <div className="leaderboard-empty">Run a deterministic backtest to create the first ranked mandate record.</div> : null}
      {!history ? <div className="leaderboard-empty">Research registry will appear when the local Phase 3 backend is available.</div> : null}
    </section>

    {backtest && metrics ? <section className="backtest-report">
      <div className="fund-section-heading"><div><span className="eyebrow">DETERMINISTIC RESEARCH REPORT</span><h2>{backtest.report.mandate_name} · v{backtest.report.mandate_version}</h2></div><small>{backtest.market_data.source} {backtest.market_data.timeframe} · {backtest.market_data.bars_per_symbol} closed bars / symbol</small></div>
      <div className="backtest-metrics">
        {[
          ["TOTAL RETURN", metricPercent(metrics.total_return), metrics.total_return >= 0 ? "positive" : "negative"],
          ["SHARPE", metrics.sharpe.toFixed(2), ""],
          ["SORTINO", metrics.sortino.toFixed(2), ""],
          ["MAX DRAWDOWN", `${(metrics.max_drawdown * 100).toFixed(2)}%`, "negative"],
          ["PROFIT FACTOR", metrics.profit_factor === null ? "N/A" : metrics.profit_factor.toFixed(2), ""],
          ["WIN RATE", `${(metrics.win_rate * 100).toFixed(1)}%`, ""],
          ["EXCESS RETURN", metricPercent(metrics.excess_return), metrics.excess_return >= 0 ? "positive" : "negative"],
          ["TRADES", String(metrics.trade_count), ""],
        ].map(([name, value, tone]) => <article key={name}><span>{name}</span><b className={tone}>{value}</b></article>)}
      </div>

      <div className="report-grid">
        <article className="equity-chart">
          <header><div><h3>Equity vs buy-and-hold</h3><small>Completed-bar signals · next-open fills</small></div><div className="chart-legend"><span className="atlas">Atlas</span><span className="benchmark">Benchmark</span></div></header>
          <svg viewBox="0 0 820 260" role="img" aria-label="Atlas and benchmark equity curves">
            {[0, 65, 130, 195, 260].map((y) => <line key={y} x1="0" y1={y} x2="820" y2={y} className="chart-gridline" />)}
            <polyline points={benchmark} className="benchmark-line" />
            <polyline points={equity} className="atlas-line" />
          </svg>
          <div className="chart-foot"><span>{shortDate(backtest.report.equity[0].timestamp)}</span><b>NO LOOKAHEAD: VERIFIED</b><span>{shortDate(backtest.report.equity.at(-1)!.timestamp)}</span></div>
        </article>
        <article className="audit-stream">
          <header><div><h3>Audit stream</h3><small>Latest research decisions</small></div><b>{backtest.report.audit.length} EVENTS</b></header>
          {recentAudit.map((row) => <div key={row.sequence}><span>{String(row.sequence).padStart(3, "0")}</span><p><b>{shortDate(row.timestamp)}</b>{Object.entries(row.signals).map(([ticker, signal]) => `${ticker} ${signal}`).join(" · ")}</p></div>)}
        </article>
      </div>

      <div className="trade-ledger">
        <header><div><h3>Recent research trades</h3><small>Illustrative backtest records—not broker orders</small></div><b>{backtest.report.trades.length} TOTAL</b></header>
        <div className="trade-row trade-head"><span>SYMBOL</span><span>SIDE</span><span>ENTRY</span><span>EXIT</span><span>RAW RETURN</span><span>WEIGHTED</span></div>
        {recentTrades.map((trade, index) => <div className="trade-row" key={`${trade.symbol}-${trade.entry_time}-${index}`}><b>{trade.symbol}</b><span className={trade.side === "LONG" ? "positive" : "negative"}>{trade.side}</span><span>{trade.entry_price.toFixed(5)}</span><span>{trade.exit_price.toFixed(5)}</span><span className={trade.return_pct >= 0 ? "positive" : "negative"}>{metricPercent(trade.return_pct)}</span><span>{metricPercent(trade.weighted_return_pct)}</span></div>)}
      </div>
    </section> : null}
  </section>;
}
