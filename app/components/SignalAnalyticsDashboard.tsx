"use client";

import { useMemo, useState } from "react";
import { OutcomeAnalytics, kellyCriterion } from "../lib/outcome-engine.ts";
import type { SignalOutcome } from "../lib/storage.ts";

const BUCKETS = ["40-49", "50-59", "60-69", "70+"];

function pct(value: number | null): string { return value === null ? "—" : `${(value * 100).toFixed(1)}%`; }
function number(value: number | null, suffix = ""): string { return value === null ? "—" : `${value.toFixed(1)}${suffix}`; }

export function SignalAnalyticsDashboard({ outcomes }: { outcomes: SignalOutcome[] }) {
  const [regime, setRegime] = useState("ALL");
  const filtered = regime === "ALL" ? outcomes : outcomes.filter((row) => row.regimeAtGeneration === regime);
  const analytics = useMemo(() => new OutcomeAnalytics(filtered).compute(), [filtered]);
  const activeCount = filtered.filter((row) => !["TP3_HIT", "SL_HIT", "EXPIRED", "CLOSED_MANUAL"].includes(row.status)).length;
  const kelly = kellyCriterion(analytics.overall.winRate, analytics.overall.avgPlannedRR);
  const maxEquity = Math.max(1, ...analytics.equityCurve.map((row) => Math.abs(row.pips)));
  const equityPoints = analytics.equityCurve.map((row, index) => `${analytics.equityCurve.length <= 1 ? 5 : 5 + index / (analytics.equityCurve.length - 1) * 90},${25 - row.pips / maxEquity * 20}`).join(" ");
  const recent = [...filtered].sort((a, b) => (b.timestampResolved ?? b.timestampGenerated) - (a.timestampResolved ?? a.timestampGenerated)).slice(0, 20);
  return <section className="analytics-dashboard">
    <header className="analytics-head"><div><span className="eyebrow">LOCAL OUTCOME ANALYTICS</span><h2>Signal performance and calibration</h2></div><div className="regime-filters">{["ALL", "TREND_UP", "TREND_DOWN", "MEAN_REVERT"].map((item) => <button className={regime === item ? "active" : ""} type="button" key={item} onClick={() => setRegime(item)}>{item}</button>)}</div></header>
    <div className="performance-cards">
      <article><span>WIN RATE</span><b>{pct(analytics.overall.winRate)}</b></article>
      <article><span>TOTAL PIPS</span><b>{number(analytics.overall.totalPips)}</b></article>
      <article><span>AVG R/R</span><b>{number(analytics.overall.avgAchievedRR, "R")}</b></article>
      <article><span>EXPECTANCY</span><b>{number(analytics.overall.expectancyPips, " pips")}</b></article>
      <article><span>ACTIVE</span><b>{activeCount}</b></article>
      <article><span>KELLY INFO ONLY</span><b>{kelly === null ? "—" : pct(kelly)}</b></article>
    </div>
    <div className="analytics-grid-inner">
      <article className="analytics-chart"><h3>Win rate by score bucket</h3><svg viewBox="0 0 100 55" role="img" aria-label="Win rate by score bucket">{BUCKETS.map((bucket, index) => { const rate = analytics.byScoreBucket[bucket]?.winRate ?? 0; const height = rate * 38; return <g key={bucket}><rect x={8 + index * 23} y={43 - height} width="14" height={height} rx="2" fill="#49d3c7"/><text x={15 + index * 23} y="52" textAnchor="middle">{bucket}</text><text x={15 + index * 23} y={Math.max(7, 40 - height)} textAnchor="middle">{rate ? `${Math.round(rate * 100)}%` : "—"}</text></g>; })}</svg></article>
      <article className="analytics-chart"><h3>Equity curve · net pips</h3><svg viewBox="0 0 100 50" role="img" aria-label="Cumulative net pips"><line x1="5" y1="25" x2="95" y2="25" stroke="#314148" strokeWidth=".6"/>{equityPoints ? <polyline points={equityPoints} fill="none" stroke="#55d894" strokeWidth="2"/> : null}</svg></article>
      <article className="symbol-heatmap"><h3>Symbol performance</h3><div>{Object.entries(analytics.bySymbol).map(([symbol, row]) => <span key={symbol} className={row.totalPips >= 0 ? "positive-bg" : "negative-bg"}><b>{symbol}</b><small>{row.totalPips.toFixed(1)} pips</small></span>)}</div>{!Object.keys(analytics.bySymbol).length ? <p>No resolved signals yet.</p> : null}</article>
    </div>
    <div className={`calibration-gate ${analytics.calibrated ? "ready" : "collecting"}`}><b>{analytics.calibrated ? "CALIBRATED" : "COLLECTING EVIDENCE"}</b><span>{analytics.calibrationReason}</span><small>Friction: 1.5 pips spread + $7 round-turn (0.7-pip equivalent at $10/pip).</small></div>
    <div className="outcomes-table"><h3>Recent outcomes</h3><div className="outcomes-row outcomes-header"><span>SYMBOL</span><span>ACTION</span><span>SCORE</span><span>REGIME</span><span>RESULT</span><span>NET PIPS</span></div>{recent.map((row) => <div className="outcomes-row" key={row.id}><span>{row.symbol}</span><span>{row.action.replace("_", " ")}</span><span>{row.scoreAtGeneration.toFixed(1)}</span><span>{row.regimeAtGeneration}</span><span><b className={row.finalPips >= 0 ? "result-win" : "result-loss"}>{row.status.replace("_", " ")}</b></span><span>{(row.finalPips - 2.2).toFixed(1)}</span></div>)}{!recent.length ? <p>No local lifecycle outcomes have been recorded yet.</p> : null}</div>
  </section>;
}
